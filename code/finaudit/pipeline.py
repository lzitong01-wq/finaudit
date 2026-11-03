"""S0–S7 编排：材料登记 → 解析 → 检索 → 核验 → 审计 → 交付物生成。

输入与边界（与 README「已知限制」一致）：
- 材料可以是 Markdown、PDF 文本层或扫描件 OCR（PDF 需 pypdf，扫描件需 rapidocr）。
- 主张由 LLM 拆解；未接模型时以夹具主张清单作确定性占位，模型失败时降级并记事件。
- 检索为确定性匹配：按（指标, 期间）取证据行，按材料可靠性定级区分「直接支持」与「冲突」。
  年报（经审计）rank=1 > 公告（未审计）rank=2 > 研报 rank=9（不作为证据）。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

from . import contract, formulas, rules
from .agents import (AuditorAgent, CalculatorAgent, Ctx, ExtractorAgent,
                     ParserAgent, ReporterAgent, RetrieverAgent, Supervisor,
                     VerifierAgent, assert_write, record_handoff)
from .events import EventLog
from .extract import extract_claims
from .ids import IdAllocator
from .llm import LLMClient, LLMConfig
from .numeric import half_ulp_in, to_decimal, tolerance_abs, unit_scale, within
from .periods import (parse_period_label, period_for_metric, period_key,
                      previous_period_end, previous_year_period, prior_year_end)

# 材料可靠性定级：经审计年报 > 未审计公告 > 其他证据材料 > 研报（被核验对象，不作证据）。
# 定级由材料登记的 role 派生，不按材料编号假设（编号顺序随上传顺序变化）。
ROLE_RANK = (("年报", 1), ("公告", 2), ("证据材料", 3), ("待核验", 9), ("研报", 9))
REPORT_RANK = 9

# 历史常量保留仅为兼容外部直接引用；运行期一律用 material_rank() 派生。
MATERIAL_RANK = {"MAT-0002": 1, "MAT-0003": 2, "MAT-0001": 9}


def report_material_id(materials: list[dict]) -> str:
    """待核验研报的材料编号：按登记 role 判定（I1 唯一性），不按编号顺序假设。"""
    hits = [m["material_id"] for m in materials
            if "待核验" in str(m.get("role", "")) or str(m.get("role", "")).strip() == "研报"]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ValueError(
            "材料登记中未标注待核验研报（I1）：请在 materials.json 中给研报设 role 含「待核验」")
    raise ValueError(f"材料登记中待核验研报多于一份：{hits}（I1 唯一性，需收敛为一份）")


def material_rank(materials: list[dict], report_id: str) -> dict[str, int]:
    """材料可靠性定级：由 role 派生；研报恒为最低级（不作证据）。"""
    ranks: dict[str, int] = {}
    for m in materials:
        role = str(m.get("role", ""))
        rank = 9
        for keyword, value in ROLE_RANK:
            if keyword in role:
                rank = min(rank, value)
        ranks[m["material_id"]] = rank
    if report_id:
        ranks[report_id] = REPORT_RANK
    return ranks

METRIC_ALIASES = {
    "营业收入": ["营业收入"],
    "营业成本": ["营业成本"],
    "净利润": ["净利润"],
    "资产合计": ["资产合计"],
    "负债合计": ["负债合计"],
    "所有者权益合计": ["所有者权益合计"],
    "流动资产": ["流动资产", "流动资产合计"],
    "流动负债": ["流动负债", "流动负债合计"],
    "存货": ["存货", "合计"],
    "研发投入": ["研发投入"],
    "毛利率": ["毛利率"],
    "资产负债率": ["资产负债率"],
}
POINT_METRICS = frozenset({"资产合计", "负债合计", "所有者权益合计", "存货",
                           "流动资产", "流动负债", "资产负债率"})

# 派生指标：材料里没有同名行项目，必须由公式复算；检索阶段取输入指标的行作候选证据。
# 不放进 METRIC_ALIASES —— 那是「材料里能直接读到的行项目」。
DERIVED_METRICS: dict[str, dict] = {
    "毛利率": {"formula": "FRM-IND-01", "inputs": ["营业收入", "营业成本"]},
    "资产负债率": {"formula": "FRM-IND-03", "inputs": ["负债合计", "资产合计"]},
    "净利率": {"formula": "FRM-IND-02", "inputs": ["净利润", "营业收入"]},
    "净资产收益率": {"formula": "FRM-IND-07", "inputs": ["净利润", "所有者权益合计"]},
    "流动比率": {"formula": "FRM-IND-04", "inputs": ["流动资产", "流动负债"]},
}

# 公式库有 16 条，主张路由能触发的不是全部。两张表显式登记，测试据此守住
# 「既没路由、也没说明为什么」的空档——新增公式不表态就会失败。
ROUTED_FORMULAS = frozenset({
    "FRM-CMP-01",   # 同比增速
    "FRM-CMP-03",   # 较年初增速
    "FRM-IND-01",   # 毛利率
    "FRM-IND-02",   # 净利率
    "FRM-IND-03",   # 资产负债率
    "FRM-IND-04",   # 流动比率
    "FRM-IND-07",   # 期末净资产收益率
    "FRM-TIE-02",   # 资产负债表基本等式
})

UNROUTED_FORMULAS = {
    "FRM-CMP-02": "环比需上一等长区间；材料未提供分季/分月期间，接上会取错基期，宁可转人工",
    "FRM-CMP-04": "较上期末需期间起点前一日的时点值，取哪个时点由材料表头决定，不自动猜",
    "FRM-CMP-05": "比较增减额需主张声明比较口径（金额差），当前主张结构未携带该字段",
    "FRM-IND-05": "存货周转率需期初与期末双时点存货，授权材料只列其一时无法算",
    "FRM-IND-06": "应收账款周转率需应收账款行项目，材料未列示（派生指标表里也没有可抽取值）",
    "FRM-TIE-01": "分项合计勾稽需分项行，授权材料未提供分项明细",
    "FRM-TIE-03": "期初期末勾稽需本期增加/减少行，授权材料未提供",
    "FRM-TIE-04": "跨表权益勾稽需未分配利润、提取盈余公积等行项目，授权材料未提供",
}

PAGE_RE = re.compile(r"<!--\s*page\s+(\d+)\s*-->")
UNIT_RE = re.compile(r"单位[：:]\s*(元|亿元|万元|千元|百万元|十亿元)")
NUM_RE = re.compile(r"-?[\d,]+(?:\.\d+)?")
PERIOD_COL_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_TEXT_ENCODINGS = ("utf-8-sig", "gb18030")


def _read_text_any(path: Path) -> tuple[str, str]:
    """按常见中文编码依次尝试读取正文，返回 (文本, 实际编码)。

    国内年报/公告导出的 .txt/.md 常见 GBK/GB18030，直接用 utf-8 读会抛
    UnicodeDecodeError 让整条任务失败。这里按顺序探测；都失败才用替换字符兜底，
    并把实际编码记进解析事件，避免乱码无声无息地进判定。
    """
    raw = path.read_bytes()
    for enc in _TEXT_ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8（存在无法解码字节，已替换）"


def _num(text: str) -> Decimal | None:
    m = NUM_RE.search(text.replace("（略）", ""))
    if not m:
        return None
    return Decimal(m.group(0).replace(",", ""))


def _period_key_from_cell(cell: str) -> tuple[str, dict] | None:
    parsed = parse_period_label(cell)
    if parsed is None:
        match = PERIOD_COL_RE.search(cell)
        parsed = parse_period_label(match.group(0)) if match else None
    key = period_key(parsed)
    return (key, parsed) if key is not None and parsed is not None else None


def _period_for_column(cell: str, metric: str,
                       task_scope: dict | None) -> dict | None:
    """处理显式表头和“本期/上期/期初/期末”等相对期间表头。"""
    explicit = _period_key_from_cell(cell)
    if explicit:
        return period_for_metric(explicit[1], metric, POINT_METRICS)

    label = re.sub(r"\s+", "", cell)
    is_point = metric in POINT_METRICS
    if is_point:
        if "上年同期" in label:
            return period_for_metric(previous_year_period(task_scope), metric,
                                     POINT_METRICS)
        if "上期末" in label:
            return previous_period_end(task_scope)
        if any(k in label for k in ("上年末", "年初", "期初")):
            return prior_year_end(task_scope)
        if any(k in label for k in ("本期", "期末", "报告期末")):
            return period_for_metric(task_scope, metric, POINT_METRICS)
        return None

    if any(k in label for k in ("上年同期", "上期", "上年度", "上年")):
        return previous_year_period(task_scope)
    if any(k in label for k in ("本期", "报告期", "本年度", "本年")):
        return period_for_metric(task_scope, metric, POINT_METRICS)
    return None


# 脚注引导标记：上标序号、*、†、‡ 及"注N/注："形式
FOOTNOTE_MARK_RE = re.compile(
    r"^\s*(?:(?:\[\s*注\s*(\d+)\s*\])|(?:注\s*(\d+)\s*[:：])|(?:注\s*[:：])"
    r"|(?:注\s*[†‡])|(?:[\*†‡]{1,3}))")


def _footnote_mark(body: str) -> str | None:
    """返回脚注标记串（如"脚注3""注†"），非脚注返回 None。"""
    m = FOOTNOTE_MARK_RE.match(body or "")
    if not m:
        return None
    num = m.group(1) or m.group(2)
    if num:
        return f"脚注{num}"
    tail = m.group(0).strip()
    if "†" in tail:
        return "注†"
    if "‡" in tail:
        return "注‡"
    if "*" in tail:
        return f"注{'*' * min(3, tail.count('*'))}"
    return "脚注"


def _text_range(text: str, body: str) -> str | None:
    """文本区间定位：仅当片段在原文中唯一出现时给出字符区间，
    重复出现无法区分指代 → None（不臆造定位）。"""
    if not body or not text:
        return None
    if text.count(body) != 1:
        return None
    start = text.find(body)
    return f"{start}-{start + len(body)}"


def parse_material(material_id: str, text: str, alloc: IdAllocator,
                   ocr_blocks: list[dict], *,
                   period_scope: dict | None = None) -> tuple[list[dict], list[dict]]:
    """返回 (blocks, rows)。rows = 结构化数值行，供检索与计算使用。"""
    def band_for(body: str) -> str:
        for b in ocr_blocks or []:
            token = b.get("match")
            if token and token in body:
                return b.get("band", "低")
        return "不适用"

    blocks: list[dict] = []
    rows: list[dict] = []
    page_no = 0
    unit_code = "元"
    default_period: dict | None = period_scope
    table_idx = 0

    for seg in PAGE_RE.split(text):
        if seg.isdigit():
            page_no = int(seg)
            table_idx = 0
            continue
        page_text = seg
        um = UNIT_RE.search(page_text)
        page_unit = um.group(1) if um else unit_code
        period_label = re.search(
            r"(?:期间|报告期)[：:]\s*([^\r\n|，,；;]{4,32})", page_text)
        if period_label:
            parsed_period = parse_period_label(period_label.group(1).strip())
            if parsed_period is not None:
                default_period = parsed_period

        lines = page_text.splitlines()
        i = 0
        paragraph_buf: list[str] = []

        def flush_paragraph() -> None:
            nonlocal paragraph_buf
            body = "\n".join(x.strip() for x in paragraph_buf if x.strip())
            paragraph_buf = []
            if not body:
                return
            # 脚注识别：引导标记命中即按脚注块处理，
            # 保留定位并参与检索；标记串写入 locator.value
            footnote_mark = _footnote_mark(body)
            text_range = _text_range(text, body)
            block_id = alloc.next("block")
            # 段落中的「研发投入 45.6 亿元」类数值陈述；块号先分配，
            # 数值行必须指向该段落块（否则候选证据会指向不存在的块）
            new_rows: list[dict] = []
            for metric, aliases in METRIC_ALIASES.items():
                if metric not in ("研发投入",):
                    continue
                for a in aliases:
                    mm = re.search(a + r"\s*([\d,]+(?:\.\d+)?)\s*(亿元|万元|元)", body)
                    if mm:
                        metric_period = period_for_metric(default_period, metric,
                                                          frozenset(POINT_METRICS))
                        new_rows.append({
                            "metric": metric, "metric_label": a,
                            "period_key": period_key(metric_period),
                            "period": metric_period,
                            "value": Decimal(mm.group(1).replace(",", "")),
                            "value_text": f"{mm.group(1)}{mm.group(2)}",
                            "unit_code": mm.group(2), "material_id": material_id,
                            "page": page_no, "block_id": block_id, "table_ref": None,
                            "quote": body[:200],
                            "ocr_band": band_for(body),
                        })
            rows.extend(new_rows)
            blocks.append({
                "block_id": block_id, "material_id": material_id,
                "block_type": "脚注" if footnote_mark else "段落",
                "page": page_no, "content_text": body[:800],
                "parse_status": "已解析",
                "locator": {"type": "页码", "page": page_no, "table_ref": None,
                            "text_range": text_range,
                            "value": (f"P{page_no}/{footnote_mark}" if footnote_mark
                                      else f"P{page_no}")},
            })

        while i < len(lines):
            line = lines[i]
            if line.strip().startswith("|"):
                flush_paragraph()
                table_lines = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i].strip())
                    i += 1
                table_idx += 1
                if len(table_lines) < 3:
                    continue
                cells = [[c.strip() for c in ln.strip("|").split("|")] for ln in table_lines]
                header, body_rows = cells[0], cells[2:]
                table_ref = f"P{page_no}-表{table_idx}"
                block = {
                    "block_id": alloc.next("block"), "material_id": material_id,
                    "block_type": "表格", "page": page_no,
                    "content_text": "\n".join(table_lines)[:800],
                    "parse_status": "已解析",
                    "locator": {"type": "表格坐标", "page": page_no, "table_ref": table_ref,
                                "text_range": None, "value": f"P{page_no}/{table_ref}"},
                }
                blocks.append(block)
                # 列期间
                col_periods = header[1:]
                for row_cells in body_rows:
                    if not row_cells or not row_cells[0]:
                        continue
                    label = row_cells[0]
                    metric = None
                    for m, aliases in METRIC_ALIASES.items():
                        if label in aliases:
                            metric = m
                            break
                    if metric is None and label == "合计" and "存货" in page_text:
                        metric = "存货"
                    if metric is None:
                        continue
                    for ci, cell in enumerate(row_cells[1:], start=1):
                        val = _num(cell)
                        if val is None:
                            continue
                        raw_period = (_period_for_column(col_periods[ci - 1], metric, default_period)
                                      if ci - 1 < len(col_periods) else None)
                        # 显式/相对列期间优先；未识别的单列可回退到任务范围。
                        if raw_period is None and len(row_cells) <= 2:
                            raw_period = period_for_metric(default_period, metric,
                                                           POINT_METRICS)
                        per = (raw_period if raw_period is not None else None)
                        rows.append({
                            "metric": metric, "metric_label": label,
                            "period_key": period_key(per), "period": per,
                            "value": val, "value_text": f"{cell}{page_unit}",
                            "unit_code": page_unit, "material_id": material_id,
                            "page": page_no, "block_id": block["block_id"],
                            "table_ref": table_ref,
                            "quote": f"{label} {cell}（{table_ref}）",
                            "ocr_band": band_for(f"{label} {cell}"),
                        })
                for r in rows:
                    if r["block_id"] is None and r["material_id"] == material_id and r["page"] == page_no:
                        r["block_id"] = block["block_id"]
            else:
                if line.strip():
                    paragraph_buf.append(line)
                else:
                    flush_paragraph()
                i += 1
        flush_paragraph()

    return blocks, rows


# 复合指标后缀：出现这些词时，主张不是单纯的"营业收入/营业成本"，
# 不得模糊匹配到基础指标（避免把"海外收入占比"当营业收入判错，应出口证据不足）
COMPOSITE_METRIC_HINTS = ("占比", "比重", "数量", "人数", "天数", "净额",
                          "账面价值", "周转率", "周转天数")


def _claim_metric(claim: dict) -> str | None:
    text = claim.get("claim_text", "")
    # 派生指标先匹配：名字里可能含「周转率」「率」等复合词，会被下面的排除规则吃掉
    for metric in DERIVED_METRICS:
        if metric in text:
            return metric
    if any(h in text for h in COMPOSITE_METRIC_HINTS):
        return None  # 复合指标：不把比率/占比等误作基础指标
    for metric in ("研发投入", "净利润", "资产合计",
                   "负债合计", "所有者权益合计", "存货", "营业收入", "营业成本"):
        if metric in text:
            return metric
    if "收入" in text:  # 「某新业务板块收入」等简写
        return "营业收入"
    return None


_GROWTH_UNITS = frozenset({"%", "％"})
# 变动量单位：比的是差，不是水平值。公式库只有「比较增减额」（金额口径），
# 百分点口径没有对应公式，只能转人工，不得套用水平公式。
_CHANGE_UNITS = frozenset({"百分点", "个百分点", "pct", "pp"})


def _calc_unroutable(claim: dict, alloc: IdAllocator, text: str, unit: str) -> tuple:
    """变动量主张无对应公式：留一条失败计算记录，交人工复核。

    不能 return (None, None) 一走了之——审计包里会查不到「为什么没算」。
    """
    calc = {
        "calc_id": alloc.next("calc"), "task_id": claim["task_id"],
        "claim_id": claim["claim_id"], "crq_id": alloc.next("crq"),
        "formula_id": "无对应公式", "formula_text": "变动量（百分点）",
        "inputs": [], "output": None,
        "tool": {"name": "finaudit-calc", "version": "0.1.0"},
        "executed_by": "确定性程序", "params": {"side": "report", "unit": unit},
        "tolerance": {"abs": None, "rel": None},
        "result_status": "参数校验失败",
        "error": f"主张为变动量（{unit}），公式库无百分点口径公式",
        "assumptions": [],
        "boundaries": "比率类指标的变化量需两期水平值相减，当前公式库只覆盖水平值与金额增减额，"
                      "不据此判定真伪，转人工复核。",
        "version": 1,
    }
    return calc, calc["error"]


def _is_growth_claim(text: str, unit: str) -> bool:
    """比较类主张：文本说了同比/较年初，且值以百分数表述。

    单位为「百分点」时主张比的是**差**而非增速，不能套增速公式——
    否则「毛利率同比下降 2 个百分点」会算出毛利率的相对变化率，判成错误。
    """
    if unit not in _GROWTH_UNITS:
        return False
    return "同比" in text or "较年初" in text


def _claim_period_key(claim: dict) -> str | None:
    numeric = claim.get("numeric") or {}
    period = numeric.get("period") or {}
    return period_key(period)


def _to_claim_unit(value: Decimal, from_unit: str, to_unit: str) -> Decimal:
    return value * unit_scale(from_unit) / unit_scale(to_unit)


def _period_beyond_cutoff(period, cutoff) -> bool:
    """证据期间起点晚于数据截止点：属截止点之后的信息，不得用于核验。

    主张侧的前视偏差由规则层（_period_future）挡；证据侧必须在这里挡，
    否则一份截止点之后才发布的材料会被当成"证据"，判出看似正确的结论。
    """
    if not cutoff or not isinstance(period, dict):
        return False
    ref = period.get("start") or period.get("point")
    return bool(ref) and str(ref)[:10] > str(cutoff)[:10]


def _published_after(publish_date, cutoff) -> bool:
    """材料披露日晚于数据截止点：整份材料在截止点尚不可得，不得作证据。

    期间过滤只能挡住「行」（2025 年的数），挡不住「整份材料」——
    一份 2025-04 才披露的年报，其 2024 年叙述与 2023 年比较数同样不在
    截止点的信息集里。材料未登记披露日时返回 False，退化为期间过滤。
    """
    if not cutoff or not publish_date:
        return False
    return str(publish_date)[:10] > str(cutoff)[:10]


def _find_block_for_row(row: dict, blocks: list[dict]) -> str | None:
    """数值行缺块号时的兜底定位：同材料同页、正文含该数值的块。

    仅在解析阶段漏填块号时使用；找不到就返回 None，由调用方显式报错，
    绝不新分配块号（那会造出索引中不存在的块）。
    """

    mid, page = row.get("material_id"), row.get("page")
    label = row.get("metric_label") or ""
    value = row.get("value")
    forms = []
    if value is not None:
        forms = [str(value), f"{value:,}"]
        if "." in str(value):
            forms.append(str(value).rstrip("0").rstrip("."))
    for b in blocks:
        if b.get("material_id") != mid or b.get("page") != page:
            continue
        text = b.get("content_text") or ""
        if not label or label not in text:
            continue
        if not forms or any(f in text for f in forms):
            return b["block_id"]
    return None


def select_candidates(claim: dict, rows: list[dict], blocks: list[dict],
                      alloc: IdAllocator, *, report_id: str = "",
                      ranks: dict[str, int] | None = None,
                      blocked_materials: set[str] | None = None) -> list[dict]:
    """候选证据检索（RA3）。研报材料由 report_id 指定（不按编号假设），
    材料可靠性定级由 ranks 派生（不读硬编码常量）。

    blocked_materials：数据截止点之后才有的材料（其数据行已全部排除），
    整份材料不参与检索——否则定性主张仍会拿越界的块判"通过"。
    """
    ranks = ranks or MATERIAL_RANK
    blocked = blocked_materials or set()
    metric = claim.get("metric")
    numeric = claim.get("numeric") or {}
    claim_unit = (numeric.get("unit") or {}).get("code", "元")
    claim_value = (to_decimal(numeric.get("value"))
                   if numeric.get("value") is not None else None)
    claim_pk = _claim_period_key(claim)
    task_id = claim["task_id"]

    def make_candidate(row: dict) -> dict:
        # 候选证据必须指向真实存在的块：不得用新分配的块号顶替，
        # 否则会凭空造出「索引里没有的块」，复核界面无法定位原文。
        block_id = row.get("block_id")
        if not block_id:
            block_id = _find_block_for_row(row, blocks)
        if not block_id:
            raise ValueError(
                f"证据候选无法定位到材料块：{row.get('material_id')} "
                f"P{row.get('page')} {row.get('metric_label')}")
        table_ref = row.get("table_ref")
        if table_ref:
            loc_value = table_ref if str(table_ref).startswith("P") else f"P{row['page']}/{table_ref}"
        else:
            loc_value = f"P{row['page']}"
        locator = {"type": "表格坐标" if table_ref else "页码",
                   "page": row["page"], "table_ref": table_ref,
                   "text_range": None, "value": loc_value}
        return {
            "candidate_id": alloc.next("candidate"),
            "task_id": task_id,
            "material_id": row["material_id"],
            "block_id": block_id,
            "locator": locator,
            "quote": row.get("quote", ""),
            "truth_judgment": "未判定",
            "ocr_band": row.get("ocr_band", "不适用"),
            "normalized_fact_draft": {
                "metric": row["metric"],
                "value": {
                    "value": row["value"],
                    "value_text": row.get("value_text"),
                    "value_kind": "精确值",
                    "range_low": None, "range_high": None,
                    "entity": "甲企业",
                    "unit": {"code": row["unit_code"], "other": None},
                    "period": row["period"],
                    "basis": {"consolidation": "合并报表", "accounting": "unknown", "note": None},
                    "source": {"kind": "material", "material_id": row["material_id"],
                               "block_id": block_id, "locator": locator,
                               "quote": row.get("quote"), "calc_id": None},
                    "comparison": None,
                },
            },
        }

    # 定性表述：从段落块找包含主张谓语的文本（R-GEN-09 字面匹配）
    if claim.get("claim_type") == "定性表述型":
        import re as _re
        def _norm(s: str) -> str:
            return _re.sub(r"[\s，。；：、（）()\-—_]", "", s)
        probe = claim.get("claim_text", "")
        for cut in ("主营业务", "管理层", "发展前景"):
            if cut in probe:
                probe = probe[probe.index(cut):]
                break
        else:
            probe = probe[6:]
        key = _norm(probe)[:8]
        for b in blocks:
            if b["material_id"] == report_id or b["material_id"] in blocked:
                continue
            if key and key in _norm(b.get("content_text", "")):
                return [{
                    "candidate_id": alloc.next("candidate"), "task_id": task_id,
                    "material_id": b["material_id"], "block_id": b["block_id"],
                    "locator": b["locator"], "quote": b["content_text"][:120],
                    "truth_judgment": "未判定", "ocr_band": "不适用",
                    "normalized_fact_draft": {"metric": None, "value": None},
                }]
        return []

    if not metric or (claim_value is None and claim.get("claim_type") != "引用型"):
        return []

    # 引用型：按引用主体取指标行作为对照证据（无同主体证据时由实体门禁出口 R-ENT-03）
    if claim.get("claim_type") == "引用型" and claim_value is None:
        ref = claim.get("reference") or {}
        if ref.get("cited_subject"):
            claim["entity"] = ref["cited_subject"]
        metric_rows = [r for r in rows if r["metric"] == metric
                       and r["material_id"] != report_id]
        if not metric_rows:
            return []
        best_rank = min(ranks.get(r["material_id"], 9) for r in metric_rows)
        return [make_candidate(r) for r in metric_rows
                if ranks.get(r["material_id"], 9) == best_rank]

    # 数值主张缺少明确期间时不跨期匹配，避免把任意年度的同名指标当作证据。
    if claim_pk is None:
        return []

    # 派生指标：证据取自其输入指标的行（毛利率←收入/成本；净利率←净利润/收入 …）
    metrics = (DERIVED_METRICS.get(metric) or {}).get("inputs") or [metric]

    same = [r for r in rows if r["metric"] in metrics and r["material_id"] != report_id]
    if metric == "资产合计" and "表1" in claim.get("claim_text", ""):
        same = [r for r in rows if r["metric"] in ("资产合计", "负债合计", "所有者权益合计")
                and r["material_id"] == report_id]
        if same:
            return [make_candidate(r) for r in same]

    period_rows = [r for r in same if r["period_key"] == claim_pk]
    if not period_rows:
        return []

    try:
        tol = half_ulp_in(numeric.get("value_text", ""), claim_unit, claim_unit)
    except ValueError:
        tol = None
    if tol is None:
        best_rank = min(ranks.get(r["material_id"], 9) for r in period_rows)
        return [make_candidate(r) for r in period_rows
                if ranks.get(r["material_id"], 9) == best_rank]
    matching = []
    for r in period_rows:
        try:
            converted = _to_claim_unit(r["value"], r["unit_code"], claim_unit)
        except Exception:
            continue
        if abs(converted - claim_value) <= tol:
            matching.append(r)

    if matching:
        hit_ranks = {ranks.get(r["material_id"], 9) for r in matching}
        materials_hit = {r["material_id"] for r in matching}
        best_rank = min(hit_ranks)
        if len(materials_hit) > 1:
            # 主张与多来源均相容（如约数）：不构成"证据相互矛盾"，取主源对照
            return [make_candidate(r) for r in matching
                    if ranks.get(r["material_id"], 9) == best_rank]
        # 仅匹配单一来源：带上更高可靠性材料的同事实值，供冲突前置检查
        picked = matching + [r for r in period_rows
                             if ranks.get(r["material_id"], 9) < best_rank]
        return [make_candidate(r) for r in picked]

    # 不匹配：取最高可靠性材料的值作为对照（判错用）
    best_rank = min(ranks.get(r["material_id"], 9) for r in period_rows)
    return [make_candidate(r) for r in period_rows
            if ranks.get(r["material_id"], 9) == best_rank]


def _parse_failure_block(alloc: IdAllocator, material_id: str, filename: str,
                         reason: str) -> dict:
    """显式解析失败块：解析失败必须留痕，不静默丢弃。"""
    return {
        "block_id": alloc.next("block"), "material_id": material_id,
        "block_type": "段落", "page": None,
        "content_text": f"（{filename} 解析失败：{reason}）",
        "parse_status": "解析失败",
        "locator": {"type": "页码", "page": None, "table_ref": None,
                    "text_range": None, "value": "解析失败"},
    }


def _best_evidence_material(rows: list[dict], metric: str, period_key: str | None,
                            ranks: dict[str, int], report_id: str) -> str | None:
    """该指标在该期间上的最优证据材料：可靠性定级最高（数值最小）且非研报。"""
    cand = [r for r in rows if r["metric"] == metric and r["material_id"] != report_id
            and (period_key is None or r["period_key"] == period_key)]
    if not cand:
        return None
    return min(cand, key=lambda r: (ranks.get(r["material_id"], 9),
                                    r["material_id"]))["material_id"]


def _inputs_for(rows: list[dict], metric: str, period_key: str | None, role: str,
                material: str = "MAT-0002") -> dict | None:
    for r in rows:
        if (r["metric"] == metric and r["material_id"] == material
                and (period_key is None or r["period_key"] == period_key)):
            return {"role": role, "value": {
                "value": r["value"], "value_text": r["value_text"], "value_kind": "精确值",
                "range_low": None, "range_high": None, "entity": "甲企业",
                "unit": {"code": r["unit_code"], "other": None},
                "period": r["period"],
                "basis": {"consolidation": "合并报表", "accounting": "中国企业会计准则", "note": None},
                "source": {"kind": "material", "material_id": r["material_id"],
                           "block_id": r["block_id"],
                           "locator": {"type": "表格坐标", "page": r["page"],
                                       "table_ref": r["table_ref"], "text_range": None,
                                       "value": (r["table_ref"]
                                                 if str(r["table_ref"]).startswith("P")
                                                 else f"P{r['page']}/{r['table_ref']}")},
                           "quote": r.get("quote"), "calc_id": None},
                "comparison": None,
            }}
    return None


def build_calculation(claim: dict, rows: list[dict], alloc: IdAllocator, *,
                      report_id: str = "",
                      ranks: dict[str, int] | None = None) -> tuple[dict | None, str | None]:
    """按主张内容路由公式；返回 (calculation, calc_error)。

    证据材料由可靠性定级选优（不写死编号），研报表内恒等式（FRM-TIE-02）用 report_id。
    """
    ranks = ranks or MATERIAL_RANK
    metric = claim.get("metric")
    text = claim.get("claim_text", "")
    numeric = claim.get("numeric") or {}
    if numeric.get("value") is None:
        return None, None
    claim_value = to_decimal(numeric.get("value"))
    claim_unit = (numeric.get("unit") or {}).get("code", "元")
    claim_period = numeric.get("period") or {}
    flow_period = period_for_metric(claim_period, str(metric or ""), POINT_METRICS)
    flow_period_key = period_key(flow_period)
    prior_flow = previous_year_period(flow_period)
    prior_flow_key = period_key(prior_flow)
    point_period = period_for_metric(claim_period, "资产合计", POINT_METRICS)
    point_period_key = period_key(point_period)
    prior_point = prior_year_end(point_period)
    prior_point_key = period_key(prior_point)

    plan = None
    comparison = numeric.get("comparison") or {}
    zero_base = (comparison.get("baseline_value") in (0, 0.0, "0")
                 or "基期收入为0" in text)
    if zero_base and metric == "营业收入":
        # 主张自述基期为 0：公式分母为零，按计算失败口径记录失败计算，不产出数值
        baseline_period = (comparison.get("baseline_period")
                           or prior_flow
                           or {"kind": "期间", "point": None, "start": None,
                               "end": None, "label": "期间未确定"})
        calc = {
            "calc_id": alloc.next("calc"), "task_id": claim["task_id"],
            "claim_id": claim["claim_id"], "crq_id": alloc.next("crq"),
            "formula_id": "FRM-CMP-01", "formula_text": "同比增速",
            "inputs": [{"role": "基期值", "value": {
                "value": Decimal(0), "value_text": "0元", "value_kind": "精确值",
                "range_low": None, "range_high": None, "entity": "甲企业",
                "unit": {"code": "元", "other": None},
                "period": baseline_period,
                "basis": {"consolidation": "合并报表", "accounting": "unknown", "note": None},
                "source": {"kind": "material", "material_id": claim["report_material_id"],
                           "block_id": None, "locator": claim.get("report_locator"),
                           "quote": claim.get("claim_text"), "calc_id": None},
                "comparison": None}}],
            "output": None,
            "tool": {"name": "finaudit-calc", "version": "0.1.0"},
            "executed_by": "确定性程序", "params": {"side": "report"},
            "tolerance": {"abs": None, "rel": None},
            "result_status": "参数校验失败",
            "error": "分母为零：基期值（主张自述基期为 0）",
            "assumptions": ["基期值取自主张自述，非材料披露"],
            "boundaries": "基期为零时增速无定义，不产出数值。",
            "version": 1,
        }
        return calc, calc["error"]

    def inp(metric_name: str, period_key: str | None, role: str) -> dict | None:
        """取最优证据材料的输入值。"""
        if period_key is None:
            return None
        material = _best_evidence_material(rows, metric_name, period_key, ranks, report_id)
        if material is None:
            return None
        return _inputs_for(rows, metric_name, period_key, role, material=material)

    # 三点顺序有讲究：
    #   ① 变动量（百分点）先拦——它不是「水平值」，套水平公式会拿 35.7% 去比 2 个百分点，
    #      算出个看似确定的"错误"；
    #   ② 比较类（同比/较年初）再判，且排在水平公式之前——「毛利率同比增长10%」说的是
    #      增速，不是 10% 的毛利率；
    #   ③ 剩下的才按水平值取公式。
    if claim_unit in _CHANGE_UNITS or "个百分点" in text:
        return _calc_unroutable(claim, alloc, text, claim_unit)
    if metric and _is_growth_claim(text, claim_unit):
        # 比较类主张不挑指标：此前只硬编码了「营业收入同比」「存货较年初」，
        # 换成「净利润同比」「资产合计较年初」就落不到公式上，只能判证据不足。
        # 期间键按指标性质取：时点指标比期末，流量指标比同期。
        is_point = metric in POINT_METRICS
        if "同比" in text:
            plan = ("FRM-CMP-01",
                    [inp(metric, point_period_key if is_point else flow_period_key, "报告期值"),
                     inp(metric, prior_point_key if is_point else prior_flow_key, "基期值")])
        else:  # 较年初
            plan = ("FRM-CMP-03", [inp(metric, point_period_key, "本期末值"),
                                   inp(metric, prior_point_key, "年初值")])
    elif metric == "毛利率":
        plan = ("FRM-IND-01", [inp("营业收入", flow_period_key, "营业收入"),
                               inp("营业成本", flow_period_key, "营业成本")])
    elif metric == "净利率":
        plan = ("FRM-IND-02", [inp("净利润", flow_period_key, "净利润"),
                               inp("营业收入", flow_period_key, "营业收入")])
    elif metric == "净资产收益率":
        # 净利润是流量、净资产是期末时点，两者期间键不同，分别取
        plan = ("FRM-IND-07", [inp("净利润", flow_period_key, "净利润"),
                               inp("所有者权益合计", point_period_key, "期末净资产")])
    elif metric == "流动比率":
        plan = ("FRM-IND-04", [inp("流动资产", point_period_key, "流动资产"),
                               inp("流动负债", point_period_key, "流动负债")])
    elif metric == "资产负债率":
        plan = ("FRM-IND-03", [inp("负债合计", point_period_key, "负债合计"),
                               inp("资产合计", point_period_key, "资产合计")])
    elif "基期收入为0" in text:
        baseline_period = (comparison.get("baseline_period")
                           or prior_flow
                           or {"kind": "期间", "point": None, "start": None,
                               "end": None, "label": "期间未确定"})
        base = {"role": "基期值", "value": {
            "value": Decimal(0), "value_text": "0元", "value_kind": "精确值",
            "range_low": None, "range_high": None, "entity": "甲企业",
            "unit": {"code": "元", "other": None},
            "period": baseline_period,
            "basis": {"consolidation": "合并报表", "accounting": "unknown", "note": None},
            "source": {"kind": "material", "material_id": report_id or claim.get(
                "report_material_id"), "block_id": None,
                       "locator": claim.get("report_locator")
                                  or {"type": "页码", "page": None, "table_ref": None,
                                      "text_range": None, "value": None},
                       "quote": "基期收入为0", "calc_id": None},
            "comparison": None,
        }}
        plan = ("FRM-CMP-01", [base, base])
    elif metric == "资产合计" and "等于" in text:
        # 表内恒等式：三项均取自研报自身（report_id），不混用其他材料
        plan = ("FRM-TIE-02",
                 [_inputs_for(rows, "资产合计", point_period_key, "资产合计", material=report_id),
                 _inputs_for(rows, "负债合计", point_period_key, "负债合计", material=report_id),
                 _inputs_for(rows, "所有者权益合计", point_period_key, "所有者权益合计",
                             material=report_id)])

    if plan is None:
        return None, None
    formula_id, inputs = plan
    formula = formulas.FORMULAS[formula_id]
    if any(i is None for i in inputs):
        # 输入缺失也要留计算记录：与「分母为零」等失败同级，否则审计包里查不到
        # 为什么没算，只能看到一句判定理由。
        missing = [role for role, item in zip(formula.roles, inputs) if item is None]
        calc = {
            "calc_id": alloc.next("calc"),
            "task_id": claim["task_id"],
            "claim_id": claim["claim_id"],
            "crq_id": alloc.next("crq"),
            "formula_id": formula_id,
            "formula_text": f"{formula.name}",
            "inputs": [i for i in inputs if i is not None],
            "output": None,
            "tool": {"name": "finaudit-calc", "version": "0.1.0"},
            "executed_by": "确定性程序",
            "params": {"side": "report"},
            "tolerance": {"abs": None, "rel": None},
            "result_status": "参数校验失败",
            "error": f"输入缺失：{'、'.join(missing)}",
            "assumptions": [],
            "boundaries": "缺输入时不计算，不猜测数值；补齐材料后重算。",
            "version": 1,
        }
        return calc, calc["error"]

    calc = {
        "calc_id": alloc.next("calc"),
        "task_id": claim["task_id"],
        "claim_id": claim["claim_id"],
        "crq_id": alloc.next("crq"),
        "formula_id": formula_id,
        "formula_text": f"{formula.name}",
        "inputs": inputs,
        "output": None,
        "tool": {"name": "finaudit-calc", "version": "0.1.0"},
        "executed_by": "确定性程序",
        "params": {"side": "report"},
        "tolerance": {"abs": None, "rel": None},
        "result_status": "成功",
        "error": None,
        "assumptions": [],
        "boundaries": "仅复算数值一致性，不判断披露本身真实性与会计政策恰当性。",
        "version": 1,
    }
    try:
        output = formulas.compute(formula_id, inputs)
    except formulas.FormulaError as exc:
        calc["result_status"] = "参数校验失败"
        calc["error"] = exc.message
        return calc, exc.message

    family = formula.family
    coarsest = None
    for i in inputs:
        h = half_ulp_in(i["value"].get("value_text", ""), i["value"]["unit"]["code"],
                       output["unit"]["code"])
        if h is not None and (coarsest is None or h > coarsest):
            coarsest = h
    tol = tolerance_abs({**numeric, "unit": {"code": claim_unit}}, family,
                        coarsest_input_ulp_half=coarsest, fallback=formula.fallback_tol)
    diff = output["value"] - claim_value
    ok = within(diff, tol)
    calc["output"] = output
    calc["tolerance"] = {"abs": tol, "rel": None}
    calc["comparison"] = {
        "expected_value": claim_value,
        "expected_source": {"kind": "material", "material_id": claim["report_material_id"],
                            "block_id": None,
                            "locator": claim.get("report_locator"),
                            "quote": claim.get("claim_text"), "calc_id": None},
        "diff": diff,
        "within_tolerance": ok,
    }
    return calc, None


def _verify_all(claims_entries: list[dict], rows_all: list[dict], blocks_all: list[dict],
                task: dict, alloc: IdAllocator, events: EventLog | None,
                records: list[dict] | None = None, *, report_id: str = "",
                ranks: dict[str, int] | None = None, ctx: Ctx | None = None,
                blocked_materials: set[str] | None = None) -> tuple:
    """对全部主张执行 检索 → 计算 → 规则判定，返回 (items, vrfs, calcs, severity_map)。

    RA3（检索）/ RA6（计算）/ RA5（判定）逐条真实执行：执行体 + 输出契约校验，
    逐条工作不占规划步数（12 步上限指 Supervisor 规划步）。

    blocked_materials：整份材料的数据都晚于数据截止点，其块也不得作证据。
    """
    ctx = ctx or Ctx()
    vrfs: list[dict] = []
    calcs: list[dict] = []
    items: list[dict] = []
    severity_map: dict[str, str] = {}
    task_id = task["task_id"]

    for entry in claims_entries:
        claim = json.loads(json.dumps(entry["claim"], default=str))
        claim.setdefault("numeric", {})
        # 外部 JSON 常把数值序列化为字符串：统一回 Decimal（不改契约语义）
        if isinstance(claim.get("numeric"), dict):
            raw_value = claim["numeric"].get("value")
            if isinstance(raw_value, str):
                parsed_value = to_decimal(raw_value)
                if parsed_value is not None:
                    claim["numeric"]["value"] = parsed_value
        metric = _claim_metric(claim)
        claim["metric"] = metric
        if metric and isinstance(claim.get("numeric"), dict):
            claim["numeric"]["metric"] = metric

        candidates = RetrieverAgent(ctx, events, execute_fn=lambda: select_candidates(
            claim, rows_all, blocks_all, alloc, report_id=report_id,
            ranks=ranks, blocked_materials=blocked_materials)).run(count_step=False)
        if events is not None:
            events.emit("检索调用", "S3", "Agent", "RA3",
                        {"query": claim.get("claim_text", "")[:60],
                         "candidate_count": len(candidates), "tool_version": "0.1.0"},
                        related_ids={"claim_id": claim["claim_id"]})

        calc, calc_error = CalculatorAgent(ctx, events, execute_fn=lambda: build_calculation(
            claim, rows_all, alloc, report_id=report_id, ranks=ranks)).run(count_step=False)
        if calc is not None:
            calcs.append(calc)
            if events is not None:
                events.emit("计算", "S5", "程序", "finaudit-calc",
                            {"formula_id": calc["formula_id"], "tool_version": "0.1.0",
                             "output": str(calc["output"]["value"]) if calc.get("output") else None,
                             "result_status": calc["result_status"]},
                            related_ids={"claim_id": claim["claim_id"],
                                         "calc_id": calc["calc_id"]})

        def _judge() -> dict:
            return rules.evaluate(claim, {
                "candidates": candidates,
                "facts": [],
                "calculations": [calc] if calc else [],
                "calc_error": calc_error,
                "materials": records or [],
                "config": {"data_cutoff": task.get("data_cutoff"),
                           "unknown_threshold": 2, "conflict_rel_tol": 1e-4},
            })

        outcome = VerifierAgent(ctx, events, execute_fn=_judge).run(count_step=False)
        # RA5 为状态唯一写点：写入前后均做权限校验（TB-5）
        assert_write("RA5", "status", events=events, detail=f"判定 {claim['claim_id']}")
        if events is not None:
            events.emit("规则判定", "S5", "Agent", "RA5",
                        {"规则编号": outcome.get("rule_ids"), "判定": outcome["status"]},
                        related_ids={"claim_id": claim["claim_id"]})

        vrf = {
            "vrf_id": alloc.next("vrf"), "task_id": task_id,
            "contract_version": "finaudit-data-contract-v1",
            "claim_id": claim["claim_id"], "status": outcome["status"],
            "reason": outcome.get("reason", ""), "rule_ids": outcome.get("rule_ids", []),
            "evidence_refs": [c["candidate_id"] for c in candidates],
            "source_locator": candidates[0]["locator"] if candidates else None,
            "calc_refs": [calc["calc_id"]] if calc else [],
            "suggestion": outcome.get("suggestion"),
            "decided_by": "RA5",
            "supersedes_vrf_id": None, "superseded_by_vrf_id": None,
        }
        errs = contract.validate(vrf, "VerificationResult")
        if errs:
            raise ValueError(f"VerificationResult 校验失败 {claim['claim_id']}: {errs}")
        vrfs.append(vrf)
        severity_map[claim["claim_id"]] = outcome.get("severity", "—")

        claim["status"] = outcome["status"]
        items.append({
            "claim": claim,
            "current_verification": vrf,
            "evidence": candidates,
            "facts": [],
            "calculations": [calc] if calc else [],
        })
    return items, vrfs, calcs, severity_map


def _alias_keywords() -> list[str]:
    """真实主体名关键词：从环境变量读取，不落盘（化名规则）。"""
    import os
    raw = os.environ.get("FINAUDIT_ALIAS_KEYWORDS", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def _parse_one(m: dict, records: list[dict], fixtures: Path, alloc: IdAllocator,
               ocr_specs: dict[str, list[dict]], events: EventLog,
               period_scope: dict | None = None) -> tuple[list, list]:
    """单份材料的解析（RA2 执行体内部），返回 (blocks, rows)。"""
    path = fixtures / "materials" / next(
        r["source_file_ref"] for r in records if r["material_id"] == m["material_id"])
    if path.suffix.lower() == ".pdf":
        from .ocr import available as ocr_available
        from .pdfparse import PDFEngineMissing, parse_pdf_material
        want_ocr = m.get("format") == "scanned_pdf"
        if want_ocr and not ocr_available():
            events.emit("工具失败", "S2", "程序", "OCR",
                        {"tool": "rapidocr", "failure_kind": "引擎不可用",
                         "retry_count": 0},
                        related_ids={"material_id": m["material_id"]})
            return [], []
        try:
            return parse_pdf_material(m["material_id"], path, alloc,
                                      ocr_specs.get(m["material_id"], []),
                                      use_ocr=want_ocr,
                                      period_scope=period_scope)
        except PDFEngineMissing as exc:
            # 引擎缺失：记工具失败 + 解析失败块，不冒充"文件损坏"、不静默通过
            events.emit("工具失败", "S2", "程序", "pdf-parser",
                        {"tool": "pypdf", "failure_kind": "引擎不可用",
                         "detail": str(exc)[:160], "retry_count": 0},
                        related_ids={"material_id": m["material_id"]})
            return [_parse_failure_block(alloc, m["material_id"], path.name,
                                         str(exc)[:120])], []
    text, encoding = _read_text_any(path)
    blocks, rows = parse_material(m["material_id"], text, alloc,
                                  ocr_specs.get(m["material_id"], []),
                                  period_scope=period_scope)
    events.emit("解析调用", "S2", "Agent", "RA2",
                {"工具名": "md-parser" if path.suffix.lower() != ".pdf" else "pdf-parser",
                 "版本": "0.1.0",
                 "文件": path.name, "字节数": path.stat().st_size,
                 "编码": encoding,
                 "页数": m.get("page_count"),
                 "OCR引擎": ("rapidocr" if m.get("format") == "scanned_pdf"
                             else "不适用"),
                 "OCR置信度分布": {b: sum(1 for r in rows if r.get("ocr_band") == b)
                                  for b in ("高", "中", "低", "不适用")},
                 "脚注块数": sum(1 for b in blocks if b.get("block_type") == "脚注"),
                 # 区域降级清单（第 5 条）。Markdown/文本层无版面区域信息，
                 # 脚注按标记识别，未产生区域级降级，故为空清单（不谎报）
                 "footnote_downgrade_list": [],
                 "失败页清单": []},
                related_ids={"material_id": m["material_id"]})
    return blocks, rows


def _clear_previous_outputs(out: Path) -> list[str]:
    """清掉上一次运行留在 o1/o2/o3 里的产物，返回被清掉的文件名。

    不清会出现「包里有上次的旧报告，而 verify 只核对本次清单仍报全部一致」——
    复核者无从分辨包里哪个文件是本次运行的。o3/review-log.jsonl 是跨运行累积的
    人工复核留痕，不属于单次产物，保留。

    只在目录看起来确实是本程序的输出目录时才动手（有 manifest 或 O1 报告），
    避免用户把 --out 指向一个碰巧也有 o1 子目录的地方而被误清。
    """
    import shutil

    o3, o1 = out / "o3", out / "o1"
    if not ((o3 / "manifest.json").exists() or (o1 / "O1-核查报告.md").exists()):
        return []
    cleared: list[str] = []
    for name in ("o1", "o2", "o3"):
        d = out / name
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.name == "review-log.jsonl":
                continue
            cleared.append(f"{name}/{p.name}")
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
    return cleared


def _run_stages(ctx: Ctx, events=None) -> dict:
    """S0–S7 阶段实现；由 Supervisor（RA1）编排调用。"""
    io = ctx.data["io"]
    fixtures, out = io["fixtures"], io["out"]
    llm_config = io.get("llm_config")
    out.mkdir(parents=True, exist_ok=True)
    cleared = _clear_previous_outputs(out)

    task = json.loads((fixtures / "task_order.json").read_text(encoding="utf-8"))
    materials_doc = json.loads((fixtures / "materials.json").read_text(encoding="utf-8"))
    materials = materials_doc["materials"] if isinstance(materials_doc, dict) else materials_doc
    # 研报由材料登记的 role 判定（不按编号假设）；可靠性定级由 role 派生
    report_id = report_material_id(materials)
    ranks = material_rank(materials, report_id)
    ctx.data["report_material_id"] = report_id
    ctx.data["material_rank"] = ranks
    claims_gt = json.loads((fixtures / "claims_gt.json").read_text(encoding="utf-8"))

    alloc = IdAllocator()
    task_id = task["task_id"]
    ctx.task_id = task_id
    events = EventLog(task_id, alloc, sink=io.get("on_event"))

    # S0 任务受理
    events.emit("任务单创建", "S0", "Agent", "RA1",
                {"受理参数摘要": task.get("subject_alias"), "材料清单": len(materials),
                 # 清掉上次产物也要留痕：否则复核者不知道旧文件去哪了
                 "清理上次产物": len(cleared)},
                related_ids={"material_id": None})
    task_order = {
        "task_id": task_id,
        "contract_version": "finaudit-data-contract-v1",
        "subject_alias": task.get("subject_alias"),
        "period_scope": task.get("period_scope"),
        "data_cutoff": task.get("data_cutoff"),
        "materials": task.get("materials"),
        "boundary_statement": task.get("boundary_statement"),
        "missing_items": task.get("missing_items", []),
    }
    # H1 任务受理参数 → 登记程序：缺项即中止，不静默启动
    missing_h1 = record_handoff("RA1", "H1", task_order, events=events, stage="S0")
    if missing_h1:
        raise ValueError(f"受理参数缺项（H1）：{missing_h1}")

    # S1 材料登记（RA1 写受控材料区）
    assert_write("RA1", "materials", events=events, detail="材料登记")
    records = []
    seen_refs: dict[str, str] = {}
    seen_digests: dict[str, str] = {}
    for m in materials:
        path = fixtures / "materials" / m["source_file_ref"]
        mid = m["material_id"]
        # 同一份物理文件重复登记会让块索引整份翻倍、候选证据重复，在入口就拦掉
        if path.name in seen_refs:
            raise ValueError(
                f"材料重复登记：{path.name} 已登记为 {seen_refs[path.name]}，又登记为 {mid}"
                "（同一文件只能登记一次）")
        if not path.is_file():
            if mid == report_id:
                raise ValueError(f"研报文件缺失：materials/{path.name}（材料 {mid}）")
            # 证据材料缺失不中断整条任务：登记为无效，解析阶段跳过，其余材料照常
            rec = {
                "material_id": mid, "task_id": task_id,
                "contract_version": "finaudit-data-contract-v1",
                "source_file_ref": path.name, "sha256": "0" * 64,
                "format": m.get("format", "md"), "page_count": m.get("page_count"),
                "status": "无效", "failure_reason": "文件缺失",
            }
            errs = contract.validate(rec, "MaterialRecord")
            if errs:
                raise ValueError(f"MaterialRecord 校验失败: {errs}")
            records.append(rec)
            events.emit("材料登记", "S1", "程序", "登记程序",
                        {"material_id": mid, "状态": "无效", "原因": "文件缺失"},
                        related_ids={"material_id": mid})
            continue
        digest = _sha256(path)
        if digest in seen_digests:
            raise ValueError(
                f"材料重复登记：{path.name} 与 {seen_digests[digest]} 的 sha256 相同"
                "（同一份材料只能登记一次）")
        seen_refs[path.name] = mid
        seen_digests[digest] = mid
        rec = {
            "material_id": mid, "task_id": task_id,
            "contract_version": "finaudit-data-contract-v1",
            "source_file_ref": path.name, "sha256": digest,
            "format": m.get("format", "md"), "page_count": m.get("page_count"),
            "status": "已登记",
        }
        # 披露日可选：登记了就按它判材料可得性，没登记则退化为按期间过滤
        if m.get("publish_date"):
            rec["publish_date"] = m["publish_date"]
        errs = contract.validate(rec, "MaterialRecord")
        if errs:
            raise ValueError(f"MaterialRecord 校验失败: {errs}")
        records.append(rec)
        events.emit("材料登记", "S1", "程序", "登记程序",
                    {"material_id": mid, "sha256": digest,
                     "格式": rec["format"], "页数": rec["page_count"]},
                    related_ids={"material_id": mid})
    # H2 材料登记表 → RA2
    record_handoff("登记程序", "H2", records[0] if records else {}, events=events,
                   stage="S1")

    # S2 解析（RA2 写文档结构树）
    assert_write("RA2", "blocks", events=events, detail="解析结果")
    ocr_specs: dict[str, list[dict]] = {m["material_id"]: m.get("ocr_blocks", [])
                                        for m in materials}

    def _parse_all() -> tuple[list[dict], list[dict]]:
        blocks_all: list[dict] = []
        rows_all: list[dict] = []
        # 登记为「无效」的材料（文件缺失等）不进入解析，避免 FileNotFoundError
        usable = {r["material_id"] for r in records if r["status"] == "已登记"}
        for m in materials:
            if m["material_id"] not in usable:
                continue
            blocks, rows = _parse_one(m, records, fixtures, alloc, ocr_specs, events,
                                      period_scope=task.get("period_scope"))
            blocks_all.extend(blocks)
            rows_all.extend(rows)
        return blocks_all, rows_all

    parsed = ParserAgent(ctx, events, execute_fn=_parse_all).run()
    blocks_all, rows_all = parsed
    # 截止点之后的信息不作为证据（前视偏差），两层：
    #   ① 材料级：披露日晚于截止点的材料整份不可得 → 其行与块都不得作证据；
    #   ② 行级：期间起点晚于截止点的行 → 只是该行不可用。
    cutoff = task.get("data_cutoff")
    # 研报是核验对象本身，恒为可得（表内恒等式 FRM-TIE-02 还要用它的行），
    # 披露日只管证据材料。
    late_materials = {r["material_id"] for r in records
                      if r["material_id"] != report_id
                      and _published_after(r.get("publish_date"), cutoff)}
    kept_rows = [r for r in rows_all
                 if r["material_id"] not in late_materials
                 and not _period_beyond_cutoff(r.get("period"), cutoff)]
    cutoff_excluded = len(rows_all) - len(kept_rows)
    # 整份材料越界：原本有数据行、过滤后一行不剩 → 连块也不能作证据，
    # 否则定性主张会拿截止点之后的原文判"通过"
    total_by_mat = Counter(r["material_id"] for r in rows_all)
    kept_by_mat = Counter(r["material_id"] for r in kept_rows)
    cutoff_blocked = late_materials | {m for m, n in total_by_mat.items()
                                       if n and kept_by_mat.get(m, 0) == 0}
    rows_all = kept_rows
    # H3 文档结构树与定位索引 → RA3/RA4
    record_handoff("RA2", "H3", blocks_all[0] if blocks_all else {}, events=events,
                   stage="S2", dst="RA3/RA4")




    # S4 主张拆解（RA4 写主张清单）
    assert_write("RA4", "claims", events=events, detail="主张拆解")
    # LLM 引擎；mock 为确定性占位，openai 为真实模型
    report_path = next(fixtures / "materials" / r["source_file_ref"]
                       for r in records if r["material_id"] == report_id)
    if report_path.suffix.lower() == ".pdf":
        from .ocr import available as ocr_available
        from .pdfparse import PDFEngineMissing, extract_pages
        report_fmt = next((m.get("format") for m in materials
                           if m["material_id"] == report_id), "pdf")
        try:
            pages = extract_pages(report_path,
                                  use_ocr=report_fmt == "scanned_pdf" and ocr_available())
        except PDFEngineMissing as exc:
            events.emit("工具失败", "S4", "程序", "pdf-parser",
                        {"tool": "pypdf", "failure_kind": "引擎不可用",
                         "detail": str(exc)[:160], "retry_count": 0},
                        related_ids={"material_id": report_id})
            pages = []
        report_text = "\n".join(entry[1] for entry in pages)
    else:
        report_text, report_encoding = _read_text_any(report_path)
        if not report_encoding.startswith("utf-8"):
            # 研报正文编码不是 UTF-8：留痕，避免乱码无声进入主张拆解
            events.emit("解析调用", "S4", "Agent", "RA4",
                        {"工具名": "md-parser", "版本": "0.1.0",
                         "文件": report_path.name, "编码": report_encoding},
                        related_ids={"material_id": report_id})

    if llm_config is None:
        import tempfile
        mock_dir = tempfile.mkdtemp(prefix="finaudit_llm_")
        mock_path = Path(mock_dir) / "mock_claims.json"
        mock_path.write_text(json.dumps(
            [{"claim_text": e["claim"]["claim_text"],
              "page": ((e["claim"].get("report_locator") or {}).get("page")
                       if e["claim"].get("report_locator") else None),
              "claim_type": e["claim"]["claim_type"],
              "judgment_type": e["claim"]["judgment_type"],
              "numeric": e["claim"].get("numeric"),
              "reference": e["claim"].get("reference")} for e in claims_gt],
            ensure_ascii=False, default=str), encoding="utf-8")
        llm_config = LLMConfig(mock_claims_path=str(mock_path))

    client = LLMClient(llm_config, events)

    def _extract_all() -> list[dict]:
        extracted = extract_claims(report_text, client, alloc, task_id,
                                   report_material_id=report_id,
                                   default_period=task.get("period_scope"))
        return extracted

    claims = ExtractorAgent(ctx, events, execute_fn=_extract_all).run()
    claims_entries = [{"claim": c} for c in claims]
    # H5 原子主张清单 → RA5
    record_handoff("RA4", "H5", claims[0] if claims else {}, events=events, stage="S4")

    # S3 检索（RA3 写候选证据集）/ S5 判定（RA5 为状态唯一写点）
    assert_write("RA3", "candidates", events=events, detail="候选证据")
    assert_write("RA5", "status", events=events, detail="核验状态唯一写点")
    assert_write("RA6", "calcs", events=events, detail="计算记录")
    # S3–S5 检索 → 计算 → 规则判定
    items, vrfs, calcs, severity_map = _verify_all(
        claims_entries, rows_all, blocks_all, task, alloc, events, records,
        report_id=report_id, ranks=ranks, ctx=ctx, blocked_materials=cutoff_blocked)
    signature = [(it["claim"]["claim_id"], it["current_verification"]["status"])
                 for it in items]
    # H4 候选证据集 → RA5；H7 计算记录 → RA5（成功交输出值，失败交错误说明）
    first_cand = (items[0]["evidence"][0] if items and items[0].get("evidence") else {})
    missing_h4 = record_handoff("RA3", "H4", first_cand, events=events, stage="S3")
    if first_cand and missing_h4:
        raise ValueError(f"候选证据集缺项（H4）：{missing_h4}")
    if calcs:
        c0 = calcs[0]
        succeeded = c0.get("result_status") == "成功"
        missing_h7 = record_handoff("RA6", "H7", {
            "calc_id": c0["calc_id"],
            "公式": c0.get("formula_text") or c0["formula_id"],
            "输出值或错误说明": ((c0.get("output") or {}).get("value")
                            if succeeded else c0.get("error"))},
            events=events, stage="S5")
        if missing_h7:
            raise ValueError(f"计算记录缺项（H7）：{missing_h7}")

    # 稳定性：隔离重跑（不发事件、独立号段），比对状态签名
    dry_items, _, _, _ = _verify_all(claims_entries, rows_all, blocks_all, task,
                                     IdAllocator(), None, records,
                                     report_id=report_id, ranks=ranks,
                                     blocked_materials=cutoff_blocked)
    dry_signature = [(it["claim"]["claim_id"], it["current_verification"]["status"])
                     for it in dry_items]
    stability_ok = signature == dry_signature

    # S6 独立审计（RA7）：判错项定位/建议完整性 + R-GEN-06 计算路由 + R-GEN-07 判定记录
    assert_write("RA7", "audit", events=events, detail="审计复核")
    err_items = [v for v in vrfs if v["status"] == "错误"]

    def _audit() -> dict:
        violations: list[tuple[str, dict]] = []
        for it in items:
            for chk in (rules.check_calc_routing(it["claim"], it.get("calculations") or []),
                        rules.check_judgment_record(it["current_verification"])):
                if chk:
                    violations.append((it["claim"]["claim_id"], chk))
        ok = all(v.get("source_locator") and v.get("suggestion")
                 for v in err_items) and not violations
        return {"audit_id": alloc.next("audit"), "audit_ok": ok,
                "violations": violations, "err_items": len(err_items)}

    audit = AuditorAgent(ctx, events, execute_fn=_audit).run()
    audit_ok = audit["audit_ok"]
    violations = audit["violations"]
    events.emit("审计复核", "S6", "Agent", "RA7",
                {"复核范围": "判错项全量 + 计算路由(R-GEN-06) + 判定记录(R-GEN-07)",
                 "判错项数": audit["err_items"],
                 "违规项数": len(violations),
                 "发现": len(violations),
                 "处置": "确认" if audit_ok else "标记需人工复核"})
    # H8 核验结果 → RA7；H9 升级项清单 → HUMAN；H11 审计发现 → RA8
    if vrfs:
        missing_h8 = record_handoff("RA5", "H8", vrfs[0], events=events, stage="S6")
        if missing_h8:
            raise ValueError(f"核验结果缺项（H8）：{missing_h8}")
    scope = "判错项全量 + 计算路由(R-GEN-06) + 判定记录(R-GEN-07)"
    missing_h9 = record_handoff("RA7", "H9", {"audit_id": audit["audit_id"],
                                              "复核范围": scope, "发现": len(violations)},
                                events=events, stage="S6")
    if missing_h9:
        raise ValueError(f"升级项清单缺项（H9）：{missing_h9}")
    missing_h11 = record_handoff("RA7", "H11", {
        "audit_id": audit["audit_id"],
        "处置": "确认" if audit_ok else "标记需人工复核"}, events=events, stage="S6")
    if missing_h11:
        raise ValueError(f"审计发现缺项（H11）：{missing_h11}")

    # S7 交付（RA8 写交付区）
    assert_write("RA8", "delivery", events=events, detail="O1/O2/O3")
    from . import report
    # 运行配置（O1 可复现性声明）：模型/prompt/材料形态/契约版本/LLM 健康
    llm_ev = next((e["payload"] for e in reversed(events.events)
                   if e["event_type"] == "LLM调用"), {})
    fmt_counts: dict[str, int] = {}
    for m in materials:
        fmt_counts[m.get("format", "md")] = fmt_counts.get(m.get("format", "md"), 0) + 1
    run_config = {
        "provider": client.config.provider,
        "model": client.config.model,
        "prompt_version": llm_ev.get("prompt_version", "—"),
        "prompt_hash": llm_ev.get("prompt_hash", ""),
        "input_formats": "、".join(f"{k}×{v}" for k, v in sorted(fmt_counts.items())),
        "contract_version": report.CONTRACT_VERSION,
        "llm_failure": llm_ev.get("failure_kind") or "",
        "llm_retry": llm_ev.get("retry_count", 0),
        "data_cutoff": cutoff,
        "cutoff_excluded": cutoff_excluded,
        # 截止点时尚未披露的材料：整份不作证据，在 O1 边界说明里点名
        "cutoff_blocked_materials": sorted(
            f"{r['material_id']}（披露日 {r['publish_date']}）"
            for r in records if r["material_id"] in late_materials),
        # 一份都没登记披露日时，材料级可得性无从判断，O1 要如实说明只剩期间过滤
        "cutoff_publish_known": any(r.get("publish_date") for r in records),
        # 实际运行环境：G/B 组复现说明按此写，不写死 demo01 与 Python 版本
        "fixtures": fixtures.name,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "gt_size": len(claims_gt),
        "formulas_used": sorted({c["formula_id"] for c in calcs}),
    }
    def _deliver() -> dict:
        return report.write_outputs(
            out_dir=out, task=task_order, records=records, items=items,
            events=events, blocks=blocks_all, rows=rows_all,
            severity_map=severity_map, materials=materials, run_config=run_config,
            alias_keywords=_alias_keywords(),
        )

    summary = ReporterAgent(ctx, events, execute_fn=_deliver).run()
    events.emit("导出", "S7", "Agent", "RA8",
                {"产物": "O1 人读报告", "路径": str(summary.get("o1", "")),
                 "主张数": len(items), "审计结论": "确认" if audit_ok else "标记需人工复核"})

    summary.update({
        "task_id": task_id,
        "claims": len(items),
        "status_counts": _counts(v["status"] for v in vrfs),
        "audit_ok": audit_ok,
        "events": len(events.events),
        "calcs": len(calcs),
        "blocks": len(blocks_all),
        "rows": len(rows_all),
        "llm_provider": client.config.provider,
        "llm_model": client.config.model,
        "claims_extracted": len(claims_entries),
        "stability": stability_ok,
        "cleared_previous": len(cleared),
    })

    from . import eval as eval_mod
    gt_map = {e["claim"]["claim_id"]: e for e in claims_gt}
    eval_path = eval_mod.write_eval_report(
        out, o2_items=items, gt=gt_map,
        summary={**summary, "event_types": [e["event_type"] for e in events.events]},
        baseline_signature=signature, signature=dry_signature,
        dataset=fixtures.name.upper())
    summary["eval_report"] = str(eval_path)
    return summary


def _counts(values) -> dict:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def run(fixtures_dir: str | Path, out_dir: str | Path,
        llm_config: LLMConfig | None = None, *, on_event=None) -> dict:
    """对外入口：构造上下文，交由 RA1 Supervisor 编排 S0–S7。

    on_event：可选的事件订阅回调（界面进度用），不改变事件内容与落盘结果。
    """
    ctx = Ctx(data={"io": {"fixtures": Path(fixtures_dir), "out": Path(out_dir),
                           "llm_config": llm_config, "on_event": on_event}})
    return Supervisor(ctx).run()
