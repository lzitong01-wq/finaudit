"""核验规则引擎（53 条规则）：规则注册表与纯函数判定。

边界（README「模块与接口」）：
- evaluate(claim, ctx) 只读入参：不改写 claim、不写事件、不分配 ID、不 import formulas。
- 数值一律由管线预先算好放入 ctx["calculations"]；本模块只做单位标度换算、
  数量级比值检验与区间判定（不做任何公式计算）。
- 未实现规则不得产出「通过」：注册表 implemented 全为 True；`_mk` 另按 R-GEN-01
  拒绝 R10 八值以外的状态值。

判定顺序（冲突前置检查；路由表）：
1. R-GEN-02       report_locator=null                    → 待核验
2. R-GEN-05/08    冲突证据前置检查（必须先于判错）        → 冲突
3. R-FRM-04       计算失败                                → 需人工复核
4. R-ACC-03       会计解释禁判                            → 需人工复核
5. R-ASM-01/03    估值假设类（扩展范围，统一人工）        → 需人工复核
6. R-GEN-04       无候选证据（非可核验项除外）            → 证据不足
7. R-ASM-02       观点 / 未来期间推论                     → 不适用
8. R-GEN-09       定性事实字面匹配 → 通过 / 需人工复核
9. R-CIT-01..05   引用型
10. 数值型：R-UNI（维度/币种）→ R-PER（类型/财年/一致性）→ R-ENT-01
    → R-CON-01/03 → R-ACC-01/02 → R-CMP-01/03/04 → R-NUM-04 区间值
    → R-UNI-04 → R-TIE-02/03/04（勾稽）→ R-FRM-03（输入齐备）→ R-CMP-02 / R-FRM-01
    → R-NUM-01/03（原值/约数）
11. R-TIE-01/05    勾稽责任分流（研报侧不平→错误；证据侧不平→冲突）

rule_ids 只登记决定最终状态的规则（记录范围）：
- 过程性元规则 R-GEN-01/06/07 与约束性规则 R-TIE-04 不占 rule_ids，本模块以
  独立纯函数 `check_status_exit` / `check_calc_routing` / `check_judgment_record`
  / `check_tie_tolerance` 暴露其检查，仅在违反时以 rule_id 回链；
- R-CON-02/R-ACC-04 为条件通过注记，不占 rule_ids；口径声明一致（R-CON-01/
  R-ACC-01 的通过分支）亦不占 rule_ids（禁用通过条件第 4 条）。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal

# ---------------------------------------------------------------------------
# 规则注册表（53 条，按域码分组）
# ---------------------------------------------------------------------------

_RULE_SPECS = [
    # 编号, 名称, 域, 严重度, 是否在本模块实现
    ("R-GEN-01", "状态出口约束", "GEN", "高", True),
    ("R-GEN-02", "阻断矩阵前置机检", "GEN", "高", True),
    ("R-GEN-03", "OCR 低置信阻断", "GEN", "高", True),
    ("R-GEN-04", "证据不足出口", "GEN", "中", True),
    ("R-GEN-05", "冲突出口", "GEN", "高", True),
    ("R-GEN-06", "计算路由与 LLM 禁算", "GEN", "高", True),
    ("R-GEN-07", "判定记录", "GEN", "中", True),
    ("R-GEN-08", "多规则命中优先级", "GEN", "中", True),
    ("R-GEN-09", "定性事实型核验", "GEN", "高", True),

    ("R-NUM-01", "原值一致性核对", "NUM", "高", True),
    ("R-NUM-02", "数值可解析性", "NUM", "中", True),
    ("R-NUM-03", "约数判定", "NUM", "中", True),
    ("R-NUM-04", "区间值判定", "NUM", "高", True),

    ("R-UNI-01", "单位维度一致性", "UNI", "高", True),
    ("R-UNI-02", "单位缺失处置", "UNI", "中", True),
    ("R-UNI-03", "跨币种换算来源", "UNI", "中", True),
    ("R-UNI-04", "数量级错配", "UNI", "高", True),

    ("R-PER-01", "期间一致性", "PER", "高", True),
    ("R-PER-02", "时点/期间类型一致性", "PER", "中", True),
    ("R-PER-03", "财年与日历年度", "PER", "中", True),
    ("R-PER-04", "期间缺失处置", "PER", "中", True),

    ("R-ENT-01", "实体一致性", "ENT", "高", True),
    ("R-ENT-02", "实体缺失或表外", "ENT", "中", True),
    ("R-ENT-03", "子公司与集团表述交叉", "ENT", "高", True),

    ("R-CON-01", "合并口径一致性", "CON", "高", True),
    ("R-CON-02", "口径缺失的条件通过（合并口径）", "CON", "中", True),
    ("R-CON-03", "口径不可比出口", "CON", "中", True),

    ("R-ACC-01", "会计准则一致性", "ACC", "高", True),
    ("R-ACC-02", "管理口径（经调整）处置", "ACC", "中", True),
    ("R-ACC-03", "会计解释禁判", "ACC", "中", True),
    ("R-ACC-04", "口径缺失的条件通过（会计口径）", "ACC", "中", True),

    ("R-CMP-01", "比较类型与基期关系", "CMP", "高", True),
    ("R-CMP-02", "比较值复算路由", "CMP", "高", True),
    ("R-CMP-03", "基期缺失与不可推导处置", "CMP", "中", True),
    ("R-CMP-04", "百分点与百分比混用", "CMP", "高", True),

    ("R-FRM-01", "派生值复算路由", "FRM", "高", True),
    ("R-FRM-02", "指标口径公式核对", "FRM", "高", True),
    ("R-FRM-03", "公式输入齐备性", "FRM", "中", True),
    ("R-FRM-04", "计算失败处置", "FRM", "中", True),
    ("R-FRM-05", "容差执行", "FRM", "高", True),

    ("R-CIT-01", "引用可定位性", "CIT", "高", True),
    ("R-CIT-02", "引用内容一致性", "CIT", "高", True),
    ("R-CIT-03", "引用主体一致性", "CIT", "高", True),
    ("R-CIT-04", "被引材料状态", "CIT", "中", True),
    ("R-CIT-05", "引用型主张证据要求", "CIT", "高", True),

    ("R-TIE-01", "表内勾稽", "TIE", "高", True),
    ("R-TIE-02", "跨表勾稽", "TIE", "高", True),
    ("R-TIE-03", "跨期勾稽与重述", "TIE", "高", True),
    ("R-TIE-04", "勾稽容差来源", "TIE", "高", True),
    ("R-TIE-05", "勾稽不平责任分流", "TIE", "高", True),

    ("R-ASM-01", "假设揭示与事实分离", "ASM", "中", True),
    ("R-ASM-02", "可核验性边界", "ASM", "中", True),
    ("R-ASM-03", "扩展范围边界", "ASM", "中", True),
]

RULES: dict[str, dict] = {
    rule_id: {
        "name": name,
        "domain": domain,
        "severity": severity,
        "implemented": implemented,
    }
    for rule_id, name, domain, severity, implemented in _RULE_SPECS
}

_SEVERITY_RANK = {"高": 3, "中": 2, "低": 1}

# R10 八值（状态枚举；路由表）——R-GEN-01 的枚举域，本模块唯一合法状态集。
STATUS_VALUES = frozenset(
    {"待核验", "通过", "错误", "冲突", "证据不足", "低置信", "需人工复核", "不适用"}
)

# 合并口径的标准取值（R-CON-01 判错的适用范围；其余口径按 R-CON-03 出口不适用）。
_STANDARD_CONSOLIDATION = frozenset({"合并报表", "母公司报表"})

# 管理口径（经调整）触发词（R-ACC-02）；R-ACC-03 的会计解释触发词另见表。
_MANAGED_ACCOUNTING = ("管理口径", "经调整", "adjusted", "non-gaap", "non-ifrs")

# 估值/预测类主张触发词（R-ASM-01/03 扩展范围登记）。
_VALUATION_WORDS = (
    "估值", "目标价", "目标市值", "合理价值", "市值", "市盈率", "市净率",
    "绝对估值", "相对估值", "现金流折现", "pe", "pb", "dcf",
)

# 比较类型关键词（R-CMP-01 ①，关键词/正则模式匹配，确定性）。
_CMP_TYPE_TOKENS = (
    ("同比", "同比"),
    ("环比", "环比"),
    ("较年初", "较年初"),
    ("较期初", "较年初"),
    ("较上期末", "较上期末"),
    ("较上年末", "较上期末"),
    ("较去年末", "较上期末"),
)
_CMP_DERIVABLE = ("同比", "环比", "较年初", "较上期末")

# 指代型引用主体（R-CIT-03「主体语义不清」出口）。
_AMBIGUOUS_SUBJECT = ("该公司", "本公司", "上述公司", "其自身")

# ---------------------------------------------------------------------------
# 单位维度与货币标度（Unit 字段契约；R-UNI-01 档位表）
# ---------------------------------------------------------------------------

_CURRENCY_SCALE = {
    "CNY": {"元": 0, "千元": 3, "万元": 4, "百万元": 6, "亿元": 8, "十亿元": 9},
    "USD": {"美元": 0, "千美元": 3, "万美元": 4, "百万美元": 6, "亿美元": 8},
    "HKD": {"港元": 0, "万港元": 4, "亿港元": 8},
    "EUR": {"欧元": 0, "万欧元": 4, "亿欧元": 8},
    "JPY": {"日元": 0, "万日元": 4},
}

_UNIT_INFO: dict[str, tuple[str, str | None, int | None]] = {}
for _family, _table in _CURRENCY_SCALE.items():
    for _code, _scale in _table.items():
        _UNIT_INFO[_code] = ("货币", _family, _scale)
for _code in ("%", "倍"):
    _UNIT_INFO[_code] = ("比率", None, None)
_UNIT_INFO["百分点"] = ("百分点", None, None)
for _code in ("人", "个", "件", "次"):
    _UNIT_INFO[_code] = ("计数", None, None)
for _code in ("年", "个月", "日", "天"):
    _UNIT_INFO[_code] = ("时间", None, None)
for _code in ("吨", "平方米", "千瓦时"):
    _UNIT_INFO[_code] = ("物理量", None, None)

_MULTIPLIERS = (("十亿", 9), ("亿", 8), ("百万", 6), ("万", 4), ("千", 3))
_FAMILY_SUFFIX = (("美元", "USD"), ("港元", "HKD"), ("欧元", "EUR"), ("日元", "JPY"), ("元", "CNY"))

# 会计解释类触发词（R-ACC-03 禁判总规则）
_ACCOUNTING_INTERPRETATION = (
    "会计政策", "收入确认", "重述", "合并范围", "会计估计", "经调整", "资本化", "计提",
    "折旧年限", "摊销", "坏账准备",
)

_NUM_TOKEN = re.compile(r"\d[\d,.]*")


# ---------------------------------------------------------------------------
# 基础工具（纯函数）
# ---------------------------------------------------------------------------

def norm_text(text) -> str:
    """规范化文本：全半角统一（NFKC）、去空白、去标点、小写。

    用于 R-GEN-09 / R-CIT-02 的字面层匹配（第①步「去空白、标点归一」）。
    去标点使「……设备制造。」与「……设备制造，覆盖……」可按包含关系匹配。
    """
    if text is None:
        return ""
    value = unicodedata.normalize("NFKC", str(text))
    value = re.sub(r"\s+", "", value)
    value = "".join(ch for ch in value if not unicodedata.category(ch).startswith("P"))
    return value.lower()


def severity_of(rule_ids) -> str:
    """取命中规则的最高严重度；未登记编号忽略，无命中时按「中」。"""
    best = 0
    for rule_id in rule_ids or ():
        rule = RULES.get(rule_id)
        if not rule:
            continue
        best = max(best, _SEVERITY_RANK.get(rule["severity"], 0))
    for level, rank in _SEVERITY_RANK.items():
        if rank == best:
            return level
    return "中"


def _dicts(value) -> list[dict]:
    return [item for item in (value or []) if isinstance(item, dict)]


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        s = f"{value:.4f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s or "0"
    if isinstance(value, float):
        s = f"{value:.4f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s or "0"
    return str(value)


def _dedupe(ids) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for rule_id in ids:
        if rule_id in seen:
            continue
        seen.add(rule_id)
        out.append(rule_id)
    return out


def _mk(status: str, rule_ids, reason: str, suggestion: str | None = None) -> dict:
    # R-GEN-01：状态出口约束——非 R10 八值一律拒绝写入（不静默改写为其他状态）。
    if status not in STATUS_VALUES:
        raise ValueError(f"R-GEN-01 状态出口约束：{status!r} 不属 R10 八值，拒绝写入")
    ids = _dedupe(rule_ids)
    return {
        "status": status,
        "rule_ids": ids,
        "reason": reason,
        "suggestion": suggestion,
        "severity": severity_of(ids),
    }


# ---------------------------------------------------------------------------
# 过程性元规则（R-GEN-01/06/07、R-TIE-04）：不产状态、不占 rule_ids，
# 仅在违反/不可确定时以 rule_id 回链并转人工（rule_ids 范围②）。
# ---------------------------------------------------------------------------

def check_status_exit(status) -> dict | None:
    """R-GEN-01 状态出口约束：合法返回 None，否则拒绝写入并以 R-GEN-01 回链。"""
    if status in STATUS_VALUES:
        return None
    return _mk(
        "需人工复核",
        ["R-GEN-01"],
        f"规则产出状态「{status}」不在 R10 八值（待核验/通过/错误/冲突/证据不足/低置信/"
        "需人工复核/不适用）内，按 R-GEN-01 拒绝写入原状态，记审计事件并转人工复核。",
        "由人工核对该规则产出并修正为八值之一后重核。",
    )


def check_calc_routing(claim, calculations) -> dict | None:
    """R-GEN-06 计算路由与 LLM 禁算：需复算的判定必须有确定性程序的 Calculation。

    违规两类：① 计算记录 executed_by 非确定性程序（LLM 写计算记录/口算）；
    ② 需要复算（judgment_type=计算 或带 comparison）但既无 Calculation 也无计算失败记录。
    """
    claim = claim if isinstance(claim, dict) else {}
    calculations = _dicts(calculations)
    allowed = ("确定性程序", "程序", "程序性", "finaudit-calc")
    for calculation in calculations:
        executed_by = calculation.get("executed_by")
        if executed_by and str(executed_by) not in allowed:
            return _mk(
                "需人工复核",
                ["R-GEN-06"],
                f"计算记录 {calculation.get('calc_id') or '未编号'} 的 executed_by=「{executed_by}」"
                "非确定性程序；按 R-GEN-06，LLM 不得写计算记录、不得作关键数字计算器，"
                "该计算不计入判定，转人工复核。",
                "改由确定性程序重算并写入 CalculationRequest/Calculation。",
            )
        if isinstance(calculation.get("comparison"), dict) and not calculation.get("formula_id"):
            return _mk(
                "需人工复核",
                ["R-GEN-06"],
                f"计算记录 {calculation.get('calc_id') or '未编号'} 有 comparison 但缺 formula_id，"
                "无法回链公式定义与容差缺省值，按 R-GEN-06 不计入判定，转人工复核。",
                "补齐 CalculationRequest.formula_id 后重算。",
            )
    numeric = claim.get("numeric") if isinstance(claim.get("numeric"), dict) else {}
    needs_calc = claim.get("judgment_type") == "计算" or isinstance(numeric.get("comparison"), dict)
    if needs_calc and not calculations:
        return _mk(
            "需人工复核",
            ["R-GEN-06"],
            "主张需换算/复算/勾稽，但未取得任何 Calculation 记录（无 CalculationRequest），"
            "按 R-GEN-06 不得由 LLM 口算替代，转人工复核。",
            "补齐确定性计算记录后重核。",
        )
    return None


def check_judgment_record(verification) -> dict | None:
    """R-GEN-07 判定记录：状态与 reason 同步追加，记录缺失即阻断导出。"""
    record = verification if isinstance(verification, dict) else {}
    missing: list[str] = []
    if record.get("status") not in STATUS_VALUES:
        missing.append("status（非八值或缺失）")
    if not str(record.get("reason") or "").strip():
        missing.append("reason")
    if not isinstance(record.get("rule_ids"), list):
        missing.append("rule_ids")
    if not missing:
        return None
    return _mk(
        "需人工复核",
        ["R-GEN-07"],
        f"判定记录缺失 {'、'.join(missing)}，按 R-GEN-07 记录不完整即阻断导出，"
        "不得以缺记录的结果交付。",
        "补全规则判定事件 payload（规则编号、输入摘要、判定）后重核。",
    )


def check_tie_tolerance(calculation) -> dict | None:
    """R-TIE-04 勾稽容差来源：容差须可溯源（公式级缺省值或最粗输入展示粒度）。"""
    calculation = calculation if isinstance(calculation, dict) else {}
    tolerance = calculation.get("tolerance") if isinstance(calculation.get("tolerance"), dict) else {}
    abs_tol = tolerance.get("abs")
    if abs_tol is not None:
        return None
    return _mk(
        "需人工复核",
        ["R-TIE-04"],
        f"勾稽计算 {calculation.get('calc_id') or '未编号'} 的 tolerance.abs 缺失，容差来源不可确定"
        "（公式级缺省值未回填且展示粒度不可推导），按 R-TIE-04 禁止统一容差，"
        "承载规则不出状态，转人工复核。",
        "补齐公式级 tolerance 缺省值或输入展示粒度后重算。",
    )


# ---------------------------------------------------------------------------
# 单位
# ---------------------------------------------------------------------------

def _unit_code(unit) -> str:
    if isinstance(unit, dict):
        code = unit.get("code")
        return str(code) if code else "unknown"
    if isinstance(unit, str) and unit:
        return unit
    return "unknown"


def _other_unit_info(text) -> tuple[str, str | None, int | None]:
    token = norm_text(text)
    if not token:
        return ("未知", None, None)
    for suffix, family in _FAMILY_SUFFIX:
        if not token.endswith(suffix):
            continue
        head = token[: -len(suffix)]
        scale = 0
        for mark, exponent in _MULTIPLIERS:
            if head.endswith(mark):
                scale = exponent
                head = head[: -len(mark)]
                break
        if head:
            return ("未知", None, None)
        return ("货币", family, scale)
    return ("未知", None, None)


def _unit_info(unit) -> tuple[str, str | None, int | None]:
    code = _unit_code(unit)
    if code == "OTHER":
        other = unit.get("other") if isinstance(unit, dict) else None
        return _other_unit_info(other)
    return _UNIT_INFO.get(code, ("未知", None, None))


def _num_value(numeric) -> float | None:
    if not isinstance(numeric, dict):
        return None
    value = numeric.get("value")
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return float(value)
    if not isinstance(value, (int, float)):
        return None
    return float(value)


def _base_value(numeric) -> float | None:
    """把货币标度折算到基本单位，仅用于同一事实的冲突比对与数量级检验。"""
    value = _num_value(numeric)
    if value is None:
        return None
    dimension, _family, scale = _unit_info(numeric.get("unit"))
    if dimension == "货币" and scale is not None:
        return value * (10.0 ** scale)
    return value


def _magnitude_mismatch(claim_numeric, evidence_numeric):
    """R-UNI-04：换算后两值比值 ≥100 或 ≤0.01。返回 (比值, 档位差) 或 None。"""
    claim_value = _num_value(claim_numeric)
    ev_value = _num_value(evidence_numeric)
    if claim_value is None or ev_value is None or ev_value == 0:
        return None
    claim_dim, claim_family, claim_scale = _unit_info(claim_numeric.get("unit"))
    ev_dim, ev_family, ev_scale = _unit_info(evidence_numeric.get("unit"))
    if claim_dim != "货币" or ev_dim != "货币" or claim_family != ev_family:
        return None
    if claim_scale is None or ev_scale is None or claim_scale == ev_scale:
        return None
    converted = claim_value * (10.0 ** (claim_scale - ev_scale))
    ratio = converted / ev_value
    if ratio >= 100 or ratio <= 0.01:
        return (ratio, claim_scale - ev_scale)
    return None


def _has_fx(config: dict, claim_family: str | None, ev_family: str | None) -> bool:
    rates = config.get("fx_rates") if isinstance(config, dict) else None
    if not isinstance(rates, dict) or not claim_family or not ev_family:
        return False
    keys = (f"{claim_family}/{ev_family}", f"{ev_family}/{claim_family}")
    return any(key in rates for key in keys)


# ---------------------------------------------------------------------------
# 证据行（候选与事实统一视图）
# ---------------------------------------------------------------------------

def _row_period_key(numeric) -> tuple | None:
    period = numeric.get("period") if isinstance(numeric, dict) else None
    if not isinstance(period, dict):
        return None
    kind = period.get("kind")
    if not kind or kind == "unknown":
        return None
    return (kind, period.get("start"), period.get("end"), period.get("point"), period.get("label"))


def _row_from_numeric(numeric, *, metric=None, **meta) -> dict:
    if isinstance(numeric, dict):
        dimension, family, _scale = _unit_info(numeric.get("unit"))
    else:
        dimension, family = "未知", None
    period_key = _row_period_key(numeric)
    basis = numeric.get("basis") if isinstance(numeric, dict) else None
    consolidation = basis.get("consolidation") if isinstance(basis, dict) else None
    row = {
        "numeric": numeric,
        "metric": metric,
        "entity": numeric.get("entity") if isinstance(numeric, dict) else None,
        "period_key": period_key,
        "consolidation": consolidation,
        "dimension": dimension,
        "family": family,
    }
    row.update(meta)
    return row


def _candidate_rows(candidates) -> list[dict]:
    rows: list[dict] = []
    for candidate in candidates or ():
        if not isinstance(candidate, dict):
            continue
        metric = candidate.get("metric") or candidate.get("indicator")
        numeric = None
        draft = candidate.get("normalized_fact_draft")
        if isinstance(draft, dict):
            metric = metric or draft.get("metric") or draft.get("indicator")
            inner = draft.get("value")
            if isinstance(inner, dict) and ("unit" in inner or "value_kind" in inner):
                numeric = inner
            elif "unit" in draft or "value_kind" in draft:
                numeric = draft
        if numeric is None and isinstance(candidate.get("numeric"), dict):
            numeric = candidate["numeric"]
        if numeric is None and isinstance(candidate.get("value"), dict):
            numeric = candidate["value"]
        rows.append(
            _row_from_numeric(
                numeric,
                metric=metric,
                candidate_id=candidate.get("candidate_id"),
                material_id=candidate.get("material_id"),
                block_id=candidate.get("block_id"),
                quote=candidate.get("quote") or "",
                ocr_band=candidate.get("ocr_band"),
            )
        )
    return rows


def _fact_rows(facts) -> list[dict]:
    rows: list[dict] = []
    for fact in facts or ():
        if not isinstance(fact, dict):
            continue
        content = fact.get("content") if isinstance(fact.get("content"), dict) else {}
        numeric = None
        inner = content.get("value")
        if isinstance(inner, dict):
            numeric = inner
        elif "unit" in content or "value_kind" in content:
            numeric = content
        metric = fact.get("metric") or content.get("metric") or content.get("indicator")
        rows.append(
            _row_from_numeric(
                numeric,
                metric=metric,
                fact_id=fact.get("fact_id"),
                material_id=fact.get("material_id") or content.get("material_id"),
                quote=content.get("quote") or "",
                ocr_band=None,
            )
        )
    return rows


def _pick_evidence(rows, claim, numeric):
    metric = claim.get("metric") or numeric.get("metric") or numeric.get("indicator")
    if metric:
        target = norm_text(metric)
        for row in rows:
            if row.get("numeric") and norm_text(row.get("metric") or "") == target:
                return row
    for row in rows:
        if row.get("numeric"):
            return row
    return None


# ---------------------------------------------------------------------------
# 冲突证据前置检查（R-GEN-05 / R-GEN-08 第①步）
# ---------------------------------------------------------------------------

def conflict_detected(candidates, *, rel_tol: float = 1e-4) -> bool:
    """两条及以上候选对同一（实体、指标、期间、口径）给出超容差的不同值时返回 True。

    只比较带指标名与期间的数值候选（避免把同一主张的多个输入值——如营业收入与
    营业成本——误判为冲突）；OCR band=低 的候选不参与冲突认定。
    """
    buckets: dict[tuple, list[tuple[str | None, float]]] = {}
    for row in _candidate_rows(candidates):
        numeric = row.get("numeric")
        if not isinstance(numeric, dict):
            continue
        if row.get("ocr_band") == "低":
            continue
        metric = norm_text(row.get("metric") or "")
        period_key = row.get("period_key")
        if not metric or not period_key:
            continue
        value = _base_value(numeric)
        if value is None:
            continue
        key = (
            metric,
            norm_text(row.get("entity") or ""),
            period_key,
            row.get("consolidation"),
            row.get("dimension"),
            row.get("family"),
        )
        buckets.setdefault(key, []).append((row.get("material_id"), value))

    for entries in buckets.values():
        if len(entries) < 2:
            continue
        materials = {material for material, _ in entries if material}
        if len(materials) < 2:
            continue
        values = [value for _, value in entries]
        low, high = min(values), max(values)
        scale = max(abs(low), abs(high), 1e-12)
        if (high - low) / scale > rel_tol:
            return True
    return False


# ---------------------------------------------------------------------------
# 判定辅助
# ---------------------------------------------------------------------------

def _period_future(claim, config) -> bool:
    cutoff = None
    if isinstance(config, dict):
        cutoff = config.get("data_cutoff")
    cutoff = cutoff or claim.get("data_cutoff")
    numeric = claim.get("numeric") or {}
    period = numeric.get("period") or {}
    start = period.get("start") or period.get("point")
    if not cutoff or not start:
        return False
    return str(start)[:10] > str(cutoff)[:10]


def _needs_evidence(claim, config) -> bool:
    judgment = claim.get("judgment_type")
    claim_type = claim.get("claim_type")
    if claim_type == "无法归类":
        return False
    if judgment == "观点":
        return False
    if claim_type == "定性表述型" and judgment == "推论":
        return False
    if judgment == "推论" and _period_future(claim, config):
        return False
    return True


def _accounting_interpretation(text) -> str | None:
    token = norm_text(text)
    for word in _ACCOUNTING_INTERPRETATION:
        if norm_text(word) in token:
            return word
    return None


def _numbers(text) -> set[str]:
    out: set[str] = set()
    for token in _NUM_TOKEN.findall(norm_text(text)):
        cleaned = token.rstrip(".").replace(",", "")
        if cleaned:
            out.add(cleaned)
    return out


def _pass_gate(claim, candidates, config):
    """通过前的阻断机检（禁用通过条件第 5、7 条；R-GEN-03）。"""
    low_only = bool(candidates) and all(
        isinstance(c, dict) and c.get("ocr_band") == "低" for c in candidates
    )
    if low_only:
        return _mk(
            "需人工复核",
            ["R-GEN-03"],
            "拟判通过所依据的证据全部来自 OCR band=低 块，按禁用通过条件第 7 条与 "
            "R-GEN-03，该块不得单独支撑通过，转 R2 人工复核。",
            "补充独立证据或完成 R2 复核后重核。",
        )
    threshold = 2
    if isinstance(config, dict) and isinstance(config.get("unknown_threshold"), int):
        threshold = config["unknown_threshold"]
    if len(claim.get("unknown_fields") or []) >= threshold:
        return _mk(
            "低置信",
            ["R-GEN-02"],
            f"unknown_fields 合计 {len(claim.get('unknown_fields') or [])} 项，达到禁用通过条件"
            f"第 5 条阈值（{threshold}），不判通过，转低置信抽核。",
            "补齐缺失元数据后重核。",
        )
    return None


def _ok(claim, candidates, config, rule_ids, reason, suggestion=None) -> dict:
    gate = _pass_gate(claim, candidates, config)
    if gate:
        return gate
    return _mk("通过", rule_ids, reason, suggestion)


def _pick_calc(calculations, claim):
    """优先取属于该主张的计算记录；管线只传本条主张的计算时退化为首条。"""
    claim_id = claim.get("claim_id")
    if claim_id:
        for calculation in calculations:
            if calculation.get("claim_id") == claim_id:
                return calculation
    return calculations[0]


def _calc_side(calculation) -> str:
    """勾稽计算的责任侧：证据侧写在 Calculation.params.side（R-TIE-05）。"""
    params = calculation.get("params") if isinstance(calculation.get("params"), dict) else {}
    side = calculation.get("side") or params.get("side") or params.get("勾稽侧") or ""
    token = str(side).lower()
    if token in ("evidence", "证据侧", "证据"):
        return "evidence"
    return "claim"


def _calc_desc(calculation, comparison) -> str:
    calc_id = calculation.get("calc_id") or "未编号计算"
    # 人读理由用公式中文名（编号在 S6 计算附录里可追溯），避免整句只出现编号
    formula = (calculation.get("formula_text")
               or calculation.get("formula_id") or "未声明公式")
    output = calculation.get("output") if isinstance(calculation.get("output"), dict) else {}
    unit = _unit_code(output.get("unit")) if isinstance(output.get("unit"), dict) else ""
    tolerance = calculation.get("tolerance") if isinstance(calculation.get("tolerance"), dict) else {}
    return (
        f"确定性复算（{calc_id}，{formula}）得 {_fmt(output.get('value'))}{unit}，"
        f"研报声称 {_fmt(comparison.get('expected_value'))}{unit}，"
        f"差 {_fmt(comparison.get('diff'))}{unit}，"
        f"容差 {_fmt(tolerance.get('abs'))}{unit}（按公式级缺省值或展示粒度推导）"
    )


def _basis_note(unchecked: list[str], status: str) -> str:
    if not unchecked:
        return ""
    names = "、".join(unchecked)
    if status == "通过":
        return (
            f"口径未核对：{names}为 unknown，按禁用通过条件第 4 条，允许通过但须注明口径未核对，"
            "本次未应用口径类规则（R-CON-02 / R-ACC-04 不占 rule_ids）。"
        )
    if status == "冲突":
        return f"口径未核对：{names}为 unknown；口径缺失不改变冲突出口（禁用通过条件只约束「通过」）。"
    return f"口径未核对：{names}为 unknown；口径缺失不阻碍判错（禁用通过条件只约束「通过」）。"


# ---------------------------------------------------------------------------
# 各路由判定
# ---------------------------------------------------------------------------

def _entity_gate(claim, rows, config):
    numeric = claim.get("numeric") if isinstance(claim.get("numeric"), dict) else {}
    entity = numeric.get("entity") or claim.get("entity")
    if not entity:
        return None
    if entity == "unknown":
        return _mk(
            "低置信",
            ["R-ENT-02"],
            "实体未声明（entity=unknown），按 R-ENT-02 禁用通过，不猜测映射。",
            "补充受控化名实体后重核。",
        )
    vocab = config.get("entity_vocab") if isinstance(config, dict) else None
    if isinstance(vocab, (list, tuple, set)) and vocab and entity not in vocab:
        return _mk(
            "需人工复核",
            ["R-ENT-02"],
            f"实体「{entity}」不在受控化名词汇表内（R-ENT-02 表外实体），不自动判定。",
            "人工确认实体映射后重核。",
        )
    if "子公司" in str(entity):
        known = {row.get("entity") for row in rows if row.get("entity")}
        if entity not in known:
            return _mk(
                "证据不足",
                ["R-ENT-03"],
                f"主张实体为「{entity}」，授权材料内无该主体单体证据（仅有集团/合并口径），"
                "按 R-ENT-03 不得以集团数替代，出口证据不足。",
                "补充该子公司单体报表或改引集团合并数。",
            )
    return None


# ---------------------------------------------------------------------------
# 专项检查（按域分组）
#
# 统一约定：每个检查返回 (终局判定 | None, 已核对规则编号)。
# - result 非 None → 立即出口（该规则命中并决定状态）；
# - 否则 ids 记入最终 rule_ids，仅记录需要留痕的通过分支；口径类（R-CON-01/
#   R-ACC-01）的通过分支不占 rule_ids（禁用通过条件第 4 条的对称口径）。
# ---------------------------------------------------------------------------

def _to_number(value) -> Decimal | None:
    """把 int/float/Decimal/str 转为 Decimal；不可解析返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        token = value.strip().replace(",", "").replace("，", "")
        if not token:
            return None
        try:
            return Decimal(token)
        except Exception:
            return None
    return None


def _period_of(numeric) -> dict:
    period = numeric.get("period") if isinstance(numeric, dict) else None
    return period if isinstance(period, dict) else {}


def _day(value) -> str:
    return str(value)[:10] if value else ""


def _as_date(value) -> date | None:
    text = _day(value)
    if len(text) != 10:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _entity_consistency(numeric, evidence_numeric, config):
    """R-ENT-01 实体一致性：两值须来自受控化名词汇表且相等。"""
    if not isinstance(evidence_numeric, dict):
        return None, []
    claim_entity = numeric.get("entity")
    evidence_entity = evidence_numeric.get("entity")
    if not claim_entity or claim_entity == "unknown":
        return None, []  # R-ENT-02 已前置处置
    if not evidence_entity:
        return None, []
    if evidence_entity == "unknown":
        return _mk(
            "需人工复核",
            ["R-ENT-01"],
            "证据侧实体未声明（entity=unknown），研报与证据的归属标签无法核对（词汇表缺失/"
            "实体缺失），按 R-ENT-01 出口需人工复核，不猜测映射。",
            "补充证据侧受控化名实体后重核。",
        ), []
    vocab = config.get("entity_vocab") if isinstance(config, dict) else None
    if isinstance(vocab, (list, tuple, set)) and vocab:
        for name in (claim_entity, evidence_entity):
            if name not in vocab:
                return _mk(
                    "需人工复核",
                    ["R-ENT-01"],
                    f"实体「{name}」不在受控化名词汇表内，标签一致性不可判定（R-ENT-01 词汇表缺失出口）。",
                    "人工确认实体映射后重核。",
                ), []
    if str(claim_entity) != str(evidence_entity):
        return _mk(
            "错误",
            ["R-ENT-01"],
            f"实体不一致：研报将该数值归属「{claim_entity}」，证据归属「{evidence_entity}」，"
            "按 R-ENT-01 判实体标签错配（不得以其他主体数据替代）。",
            f"改为「{evidence_entity}」口径的数据或更正归属表述。",
        ), []
    return None, ["R-ENT-01"]


def _period_consistency(numeric, evidence_numeric, derived):
    """R-PER-01 期间一致性：kind 相同比 point / start-end；label 差异不作为判错依据。"""
    if derived or not isinstance(evidence_numeric, dict):
        return None, []
    claim_period = _period_of(numeric)
    evidence_period = _period_of(evidence_numeric)
    claim_kind = claim_period.get("kind") or "unknown"
    evidence_kind = evidence_period.get("kind") or "unknown"
    if claim_kind == "unknown" or evidence_kind == "unknown":
        return _mk(
            "低置信",
            ["R-PER-04"],
            f"期间未声明（研报 {claim_kind} / 证据 {evidence_kind}），任一侧 kind=unknown 即按 "
            "R-PER-04 禁用通过。",
            "补充报告期后重核。",
        ), []
    if claim_kind != evidence_kind:
        return None, []  # R-PER-02 已前置出口「不适用」
    if claim_kind == "时点":
        claim_point = _day(claim_period.get("point"))
        evidence_point = _day(evidence_period.get("point"))
        if claim_point and evidence_point and claim_point != evidence_point:
            return _mk(
                "错误",
                ["R-PER-01"],
                f"期间不一致：研报时点为 {claim_point}，证据时点为 {evidence_point}，"
                "时点不同不可比，按 R-PER-01 判错误。",
                "按证据时点更正研报期间标注或改用同一时点数据。",
            ), []
        return None, ["R-PER-01"]
    claim_start, claim_end = _day(claim_period.get("start")), _day(claim_period.get("end"))
    evidence_start = _day(evidence_period.get("start"))
    evidence_end = _day(evidence_period.get("end"))
    if claim_start and evidence_start and claim_start != evidence_start:
        return _mk(
            "错误",
            ["R-PER-01"],
            f"期间不一致：研报为 {claim_start}～{claim_end or '—'}，证据为 "
            f"{evidence_start}～{evidence_end or '—'}；start/end 不同即不可比（label 差异但 "
            "start/end 相同不判错），按 R-PER-01 判错误。",
            "按证据报告期更正研报期间标注或改用同期数据。",
        ), []
    if claim_end and evidence_end and claim_end != evidence_end:
        return _mk(
            "错误",
            ["R-PER-01"],
            f"期间不一致：研报期末为 {claim_end}，证据期末为 {evidence_end}，"
            "按 R-PER-01 判错误。",
            "按证据报告期更正研报期间标注或改用同期数据。",
        ), []
    return None, ["R-PER-01"]


def _fiscal_year_consistency(numeric, evidence_numeric, rows, derived):
    """R-PER-03 财年与日历年度：label 含"财年"以 start/end 为准；表述歧义升级人工。"""
    claim_period = _period_of(numeric)
    label = str(claim_period.get("label") or "")
    if not label:
        return None, []
    token = norm_text(label)
    if not any(marker in token for marker in ("财年", "财政年度", "fy")):
        # 歧义分支：主张仅写「2024年」，证据侧日历年与财年两套口径并存
        if re.fullmatch(r"\d{4}年", label.strip()):
            evidence_labels = {
                str(_period_of(row.get("numeric")).get("label") or "")
                for row in rows
                if row.get("numeric")
            }
            has_fiscal = any("财年" in norm_text(item) for item in evidence_labels)
            has_calendar = any(
                re.fullmatch(r"\d{4}年(度)?", item.strip()) for item in evidence_labels
            )
            if has_fiscal and has_calendar:
                return _mk(
                    "需人工复核",
                    ["R-PER-03"],
                    f"主张期间标注「{label}」未区分日历年与财年，而证据侧日历年与财年两套口径并存，"
                    "按 R-PER-03 属期间表述歧义，转人工复核。",
                    "明确采用日历年或财年后重核。",
                ), []
        return None, []
    claim_start = _day(claim_period.get("start"))
    claim_end = _day(claim_period.get("end"))
    if not claim_start or not claim_end:
        return _mk(
            "需人工复核",
            ["R-PER-03"],
            f"期间 label「{label}」含财年表述但 start/end 缺失，财年区间不可确定，"
            "按 R-PER-03 出口需人工复核。",
            "补充财年起止日期后重核。",
        ), []
    years = re.findall(r"\d{4}", label)
    if years and int(years[0]) not in (int(claim_start[:4]), int(claim_end[:4])):
        return _mk(
            "需人工复核",
            ["R-PER-03"],
            f"期间 label「{label}」与 start/end（{claim_start}～{claim_end}）自相矛盾，"
            "按 R-PER-03 出口需人工复核。",
            "核对财年标注与起止日期后重核。",
        ), []
    if not derived and isinstance(evidence_numeric, dict):
        evidence_period = _period_of(evidence_numeric)
        evidence_start = _day(evidence_period.get("start"))
        evidence_end = _day(evidence_period.get("end"))
        if (evidence_start and evidence_start != claim_start) or (
            evidence_end and evidence_end != claim_end
        ):
            return _mk(
                "错误",
                ["R-PER-03"],
                f"财年区间不一致：研报「{label}」按 start/end 为 {claim_start}～{claim_end}，"
                f"证据为 {evidence_start or '—'}～{evidence_end or '—'}；"
                "按 R-PER-03 以 start/end 为准判错误。",
                "按证据财年区间更正研报期间标注或改用同区间数据。",
            ), []
    return None, ["R-PER-03"]


def _consolidation_consistency(numeric, evidence_numeric):
    """R-CON-01 合并口径一致性 / R-CON-03 口径不可比出口。"""
    if not isinstance(evidence_numeric, dict):
        return None, []
    claim_basis = numeric.get("basis") if isinstance(numeric.get("basis"), dict) else {}
    evidence_basis = (
        evidence_numeric.get("basis") if isinstance(evidence_numeric.get("basis"), dict) else {}
    )
    claim_value = claim_basis.get("consolidation")
    evidence_value = evidence_basis.get("consolidation")
    if not claim_value or claim_value == "unknown" or not evidence_value or evidence_value == "unknown":
        return None, []  # 口径缺失由 R-CON-02 条件通过注记处置，不阻断也不占 rule_ids
    if str(claim_value) == str(evidence_value):
        return None, []
    note = claim_basis.get("note")
    if isinstance(note, str) and note.strip():
        return _mk(
            "需人工复核",
            ["R-CON-01"],
            f"研报声明的合并口径「{claim_value}」与证据「{evidence_value}」不同，且附有调整/桥接"
            f"说明（「{note.strip()[:40]}」）；桥接是否成立属会计解释（R-ACC-03），"
            "按 R-CON-01 转人工复核，不自动判错。",
            "人工核对口径转换桥接后结论。",
        ), []
    if str(claim_value) in _STANDARD_CONSOLIDATION and str(evidence_value) in _STANDARD_CONSOLIDATION:
        return _mk(
            "错误",
            ["R-CON-01"],
            f"合并口径不一致：研报为「{claim_value}」，证据为「{evidence_value}」，"
            "研报未声明口径转换，按 R-CON-01 判错误（口径恰当性不在此判，属 R-ACC-03）。",
            f"改用「{evidence_value}」口径数据或补正口径声明。",
        ), []
    return _mk(
        "不适用",
        ["R-CON-03"],
        f"口径不可比：研报口径「{claim_value}」与证据口径「{evidence_value}」不同，"
        "既无同口径证据亦无桥接说明，按 R-CON-03 出口不适用（记录理由，不判真伪）。",
        "补充同口径证据或改用可比口径。",
    ), []


def _accounting_consistency(numeric, evidence_numeric):
    """R-ACC-01 会计准则一致性 / R-ACC-02 管理口径（经调整）处置。"""
    claim_basis = numeric.get("basis") if isinstance(numeric.get("basis"), dict) else {}
    claim_value = claim_basis.get("accounting")
    if not claim_value or claim_value == "unknown":
        return None, []  # R-ACC-04 条件通过注记处置
    managed = any(norm_text(marker) in norm_text(claim_value) for marker in _MANAGED_ACCOUNTING)
    if managed:
        note = claim_basis.get("note")
        if not (isinstance(note, str) and note.strip()):
            return _mk(
                "低置信",
                ["R-ACC-02"],
                f"研报采用管理口径（经调整，accounting=「{claim_value}」），但 basis.note 为空，"
                "缺调整桥接说明，按 R-ACC-02 出口低置信。",
                "补充经调整口径的调整桥接说明后重核。",
            ), []
        evidence_basis: dict = {}
        if isinstance(evidence_numeric, dict) and isinstance(evidence_numeric.get("basis"), dict):
            evidence_basis = evidence_numeric["basis"]
        evidence_value = evidence_basis.get("accounting")
        if not evidence_value or evidence_value == "unknown":
            return _mk(
                "需人工复核",
                ["R-ACC-02"],
                "研报采用管理口径（经调整），检索不到同口径（经调整）证据，按 R-ACC-02 出口"
                "需人工复核，不直接判错或判通过。",
                "补充经调整口径证据或改按法定口径核对。",
            ), []
        evidence_managed = any(
            norm_text(marker) in norm_text(evidence_value) for marker in _MANAGED_ACCOUNTING
        )
        if not evidence_managed and str(evidence_value) != str(claim_value):
            return _mk(
                "需人工复核",
                ["R-ACC-02"],
                f"研报为管理口径（经调整，「{claim_value}」），证据为「{evidence_value}」的法定披露口径，"
                "两者不可直接比对，按 R-ACC-02 转人工复核（不得直接判错或判过）。",
                "由人工核对调整桥接后结论。",
            ), []
        return None, []
    if not isinstance(evidence_numeric, dict):
        return None, []
    evidence_basis = (
        evidence_numeric.get("basis") if isinstance(evidence_numeric.get("basis"), dict) else {}
    )
    evidence_value = evidence_basis.get("accounting")
    if not evidence_value or evidence_value == "unknown":
        return _mk(
            "证据不足",
            ["R-ACC-01"],
            f"研报声明会计准则「{claim_value}」，证据侧 accounting=unknown，检索不到同准则证据，"
            "按 R-ACC-01 出口证据不足。",
            "补充同准则口径的证据材料后重核。",
        ), []
    if str(evidence_value) != str(claim_value):
        return _mk(
            "错误",
            ["R-ACC-01"],
            f"会计准则不一致：研报「{claim_value}」对证据「{evidence_value}」，"
            "数值因准则口径不可比，按 R-ACC-01 判错误。",
            "改用同准则数据或补正准则声明。",
        ), []
    return None, []


def _cmp_text_types(text) -> set[str]:
    token = norm_text(text)
    return {kind for word, kind in _CMP_TYPE_TOKENS if norm_text(word) in token}


def _baseline_of(numeric) -> dict:
    comparison = numeric.get("comparison") if isinstance(numeric, dict) else None
    if not isinstance(comparison, dict):
        return {}
    baseline = comparison.get("baseline_period")
    return baseline if isinstance(baseline, dict) else {}


def _baseline_problem(ctype, numeric) -> str | None:
    """R-CMP-01 ②：基期关系程序化校验（同比/环比/较年初/较上期末）。"""
    baseline = _baseline_of(numeric)
    if not baseline:
        return None
    claim_period = _period_of(numeric)
    claim_start = _as_date(claim_period.get("start") or claim_period.get("point"))
    if claim_start is None:
        return None
    base_start = _as_date(baseline.get("start") or baseline.get("point"))
    base_end = _as_date(baseline.get("end") or baseline.get("point"))
    if ctype == "同比":
        if base_start is None:
            return None
        try:
            expected = claim_start.replace(year=claim_start.year - 1)
        except ValueError:
            return "报告期日期无法平移一年（2 月 29 日）"
        if base_start != expected:
            return (
                f"同比要求基期为上年同期 {expected.isoformat()}，实为 {base_start.isoformat()}"
            )
        return None
    if ctype == "环比":
        if base_end is None:
            return None
        if base_end >= claim_start:
            return (
                f"环比要求基期在报告期之前，基期末 {base_end.isoformat()} 不早于报告期初 "
                f"{claim_start.isoformat()}"
            )
        if claim_period.get("kind") == "期间" and (claim_start - base_end).days != 1:
            return (
                f"环比要求基期紧邻报告期（基期末 {base_end.isoformat()} 后一日不等于报告期初 "
                f"{claim_start.isoformat()}）"
            )
        if claim_period.get("kind") == "时点" and (claim_start - base_end).days > 100:
            return (
                f"环比基期 {base_end.isoformat()} 与报告时点 {claim_start.isoformat()} 相隔超过一个季度"
            )
        return None
    if ctype == "较年初":
        base_point = _as_date(baseline.get("point") or baseline.get("start") or baseline.get("end"))
        if base_point is None:
            return None
        allowed = {date(claim_start.year, 1, 1), date(claim_start.year - 1, 12, 31)}
        if base_point not in allowed:
            return (
                f"较年初要求基期为本年年初（{claim_start.year}-01-01 或 "
                f"{claim_start.year - 1}-12-31），实为 {base_point.isoformat()}"
            )
        return None
    if ctype == "较上期末":
        if base_end is None:
            return None
        if base_end >= claim_start:
            return f"较上期末要求基期为上一期末（早于报告期），实为 {base_end.isoformat()}"
        return None
    return None


def _cmp_type_consistency(claim, numeric):
    """R-CMP-01 ①：comparison.type 与文本语义一致；② 基期关系成立。"""
    comparison = numeric.get("comparison") if isinstance(numeric, dict) else None
    if not isinstance(comparison, dict):
        return None, []
    ctype = comparison.get("type")
    text_types = _cmp_text_types(claim.get("claim_text") or "")
    if not ctype or ctype == "unknown":
        if text_types:
            return _mk(
                "需人工复核",
                ["R-CMP-01"],
                f"主张文本出现「{'/'.join(sorted(text_types))}」表述，但 comparison.type 未声明，"
                "按 R-CMP-01 不做语义猜测，转人工复核。",
                "补全 comparison.type 与基期元数据后重核。",
            ), []
        return None, []
    if ctype == "其他":
        return None, []  # 由 R-CMP-03 ③ 处置
    if text_types and ctype not in text_types:
        return _mk(
            "需人工复核",
            ["R-CMP-01"],
            f"comparison.type=「{ctype}」与主张文本的比较表述「{'/'.join(sorted(text_types))}」"
            "不一致，语义无法由模式匹配确定，按 R-CMP-01 转人工复核，不猜语义。",
            "人工确认比较类型与基期后重核。",
        ), []
    problem = _baseline_problem(ctype, numeric)
    if problem:
        return _mk(
            "错误",
            ["R-CMP-01"],
            f"基期关系不成立：{problem}；按 R-CMP-01 判错误（比较基期取错）。",
            "按 comparison.type 更正基期期间/基期值。",
        ), []
    return None, ["R-CMP-01"]


def _derived_baseline_targets(ctype, claim_start) -> set[date]:
    if ctype == "同比":
        try:
            return {claim_start.replace(year=claim_start.year - 1)}
        except ValueError:
            return set()
    if ctype == "较年初":
        return {date(claim_start.year, 1, 1), date(claim_start.year - 1, 12, 31)}
    return set()


def _has_baseline_row(rows, ctype, numeric, claim_start) -> bool:
    baseline = _baseline_of(numeric)
    targets = {_as_date(baseline.get(key)) for key in ("start", "end", "point")}
    targets = {item for item in targets if item}
    targets |= _derived_baseline_targets(ctype, claim_start)
    for row in rows:
        row_period = _period_of(row.get("numeric"))
        row_dates = {
            item
            for item in (
                _as_date(row_period.get("start")),
                _as_date(row_period.get("end")),
                _as_date(row_period.get("point")),
            )
            if item
        }
        if not row_dates:
            continue
        if targets:
            if row_dates & targets:
                return True
        elif min(row_dates) < claim_start:
            return True
    return False


def _cmp_baseline_consistency(numeric, rows, calculations):
    """R-CMP-03 基期缺失与不可推导：① 缺失不阻断 ② 证据侧无基期值→证据不足 ③ 不可推导→人工。"""
    comparison = numeric.get("comparison") if isinstance(numeric, dict) else None
    if not isinstance(comparison, dict):
        return None, []
    ctype = comparison.get("type")
    claim_period = _period_of(numeric)
    claim_start = _as_date(claim_period.get("start") or claim_period.get("point"))
    if ctype in _CMP_DERIVABLE:
        if claim_start is None:
            return _mk(
                "需人工复核",
                ["R-CMP-03"],
                f"比较类型为「{ctype}」，但报告期缺失，基期无法由报告期推导，"
                "按 R-CMP-03 ③ 不猜基期，转人工复核。",
                "补全报告期后重核。",
            ), []
        if calculations:
            return None, []  # 已有确定性复算记录，基期取值由 Calculation.inputs 承载
        if not _has_baseline_row(rows, ctype, numeric, claim_start):
            return _mk(
                "证据不足",
                ["R-CMP-03"],
                f"基期可推导（{ctype}，据报告期 {claim_start.isoformat()} 推定），但证据侧检索不到"
                "基期值（无对应期间证据行），按 R-CMP-03 ② 出口证据不足。",
                "补充基期期间的证据数据后重核。",
            ), []
        return None, []
    if not _period_known(_baseline_of(numeric)):
        return _mk(
            "需人工复核",
            ["R-CMP-03"],
            f"比较类型为「{ctype or '未声明'}」，基期期间缺失且无法由报告期推导，"
            "按 R-CMP-03 ③ 不猜基期，转人工复核。",
            "补充 comparison.baseline_period 后重核。",
        ), []
    return None, []


def _period_known(period) -> bool:
    kind = period.get("kind") if isinstance(period, dict) else None
    return bool(kind) and kind != "unknown"


def _percent_point_consistency(claim, numeric):
    """R-CMP-04 百分点与百分比混用。"""
    # 保留 % 等符号：norm_text 会去标点，此处只做全半角归一。
    text = unicodedata.normalize("NFKC", str(claim.get("claim_text") or ""))
    code = _unit_code(numeric.get("unit"))
    mentions_point = "百分点" in text
    if mentions_point:
        if code == "%":
            return _mk(
                "错误",
                ["R-CMP-04"],
                "主张文本以「百分点」表述变动幅度，而数值单位记为 %（百分比），"
                "百分点与百分比混用，按 R-CMP-04 判错误。",
                "按文本语义改用「百分点」为数值单位，或把变动幅度改写为 %。",
            ), []
        if code == "百分点":
            return None, ["R-CMP-04"]
        return _mk(
            "需人工复核",
            ["R-CMP-04"],
            f"主张文本以「百分点」表述变动幅度，而数值单位为「{code}」，度量口径不明，"
            "按 R-CMP-04 出口需人工复核。",
            "确认变动幅度的度量单位后重核。",
        ), []
    if code == "百分点":
        if "%" in text or "百分比" in text:
            return _mk(
                "错误",
                ["R-CMP-04"],
                "数值单位记为「百分点」，而主张文本以 %（百分比）表述，百分点与百分比混用，"
                "按 R-CMP-04 判错误。",
                "按文本语义更正数值单位。",
            ), []
        return _mk(
            "需人工复核",
            ["R-CMP-04"],
            "数值单位记为「百分点」但文本未以百分点表述，变动幅度的度量口径不明，"
            "按 R-CMP-04 出口需人工复核。",
            "确认变动幅度的度量单位后重核。",
        ), []
    return None, []


def _formula_input_gate(calculation):
    """R-FRM-03 公式输入齐备性：输入须带 material 定位；元数据 unknown 不得复算。"""
    inputs = [item for item in (calculation.get("inputs") or []) if isinstance(item, dict)]
    if not inputs:
        return None
    for item in inputs:
        value = item.get("value") if isinstance(item.get("value"), dict) else item
        if not isinstance(value, dict):
            return _mk(
                "证据不足",
                ["R-FRM-03"],
                "计算输入不是 NumericValue 对象，输入链断裂，按 R-FRM-03 不得发起复算，出口证据不足。",
                "补齐带定位的输入值后重算。",
            )
        source = value.get("source")
        role = item.get("role") or "未命名输入"
        if (
            not isinstance(source, dict)
            or source.get("kind") != "material"
            or not source.get("material_id")
            or not source.get("locator")
        ):
            return _mk(
                "证据不足",
                ["R-FRM-03"],
                f"计算输入「{role}」的 source 不是 material 定位（缺 material_id 或 locator，"
                "输入链断裂），按 R-FRM-03 不得发起复算，出口证据不足。",
                "补齐输入值的材料定位后重算。",
            )
        unit_code = _unit_code(value.get("unit"))
        period = _period_of(value)
        if unit_code in ("unknown", "") or not _period_known(period):
            return _mk(
                "低置信",
                ["R-FRM-03"],
                f"计算输入「{role}」的元数据不完整（unit={unit_code}，期间 "
                f"{period.get('kind') or 'unknown'}），按 R-FRM-03 不得发起复算，出口低置信。",
                "补齐输入的单位与期间后重算。",
            )
    return None


def _range_bounds(numeric) -> tuple[Decimal | None, Decimal | None]:
    """区间上下界：优先 range_low/range_high，缺失时从 value_text 的数值段解析。"""
    low = _to_number(numeric.get("range_low"))
    high = _to_number(numeric.get("range_high"))
    if low is not None and high is not None:
        return low, high
    tokens = re.findall(r"-?\d[\d,]*(?:\.\d+)?", str(numeric.get("value_text") or ""))
    numbers = [_to_number(token.replace(",", "")) for token in tokens]
    numbers = [item for item in numbers if item is not None]
    if len(numbers) == 2:
        return (low if low is not None else numbers[0], high if high is not None else numbers[1])
    return low, high


def _evaluate_range(claim, numeric, rows, candidates, config) -> dict:
    """R-NUM-04 区间值判定：点值落在区间内→通过；区间外→错误；不重叠→错误。"""
    low, high = _range_bounds(numeric)
    text = numeric.get("value_text") or ""
    if low is None or high is None:
        return _mk(
            "需人工复核",
            ["R-NUM-04"],
            f"区间值「{text}」的上下界不可解析（range_low/range_high 缺失且 value_text 无两个数值），"
            "按 R-NUM-04 出口需人工复核（区间口径不清）。",
            "补充 range_low/range_high 后重核。",
        )
    if low >= high:
        return _mk(
            "需人工复核",
            ["R-NUM-04"],
            f"区间边界异常：下限 {_fmt(low)} ≥ 上限 {_fmt(high)}（「{text}」），"
            "按 R-NUM-04 出口需人工复核。",
            "更正区间上下界后重核。",
        )
    evidence_row = _pick_evidence(rows, claim, numeric)
    evidence_numeric = evidence_row.get("numeric") if evidence_row else None
    if not isinstance(evidence_numeric, dict):
        return _mk(
            "证据不足",
            ["R-NUM-04"],
            "区间值判定缺证据侧数值（无候选证据值），按 R-NUM-04 出口证据不足。",
            "补充证据值后重核。",
        )
    claim_dim, claim_family, claim_scale = _unit_info(numeric.get("unit"))
    evidence_dim, evidence_family, evidence_scale = _unit_info(evidence_numeric.get("unit"))
    if claim_dim != evidence_dim or claim_family != evidence_family:
        return _mk(
            "需人工复核",
            ["R-NUM-04"],
            f"区间值判定两侧单位不可换算（{_unit_code(numeric.get('unit'))} 对 "
            f"{_unit_code(evidence_numeric.get('unit'))}），区间口径不清，按 R-NUM-04 转人工复核。",
            "统一单位口径后重核。",
        )
    gap = (evidence_scale or 0) - (claim_scale or 0)
    factor = Decimal(10) ** gap

    def _to_claim_unit(value):
        return value * factor

    evidence_text = evidence_numeric.get("value_text") or ""
    if evidence_numeric.get("value_kind") == "区间值":
        evidence_low, evidence_high = _range_bounds(evidence_numeric)
        if evidence_low is None or evidence_high is None:
            return _mk(
                "需人工复核",
                ["R-NUM-04"],
                f"证据侧区间「{evidence_text}」的上下界不可解析，无法做重叠判定，"
                "按 R-NUM-04 转人工复核。",
                "补充证据侧区间上下界后重核。",
            )
        evidence_low = _to_claim_unit(evidence_low)
        evidence_high = _to_claim_unit(evidence_high)
        within_overlap = max(low, evidence_low) <= min(high, evidence_high)
        if within_overlap:
            return _ok(
                claim,
                candidates,
                config,
                ["R-NUM-04"],
                f"区间重叠判定：研报区间 {_fmt(low)}～{_fmt(high)} 与证据区间 "
                f"{_fmt(evidence_low)}～{_fmt(evidence_high)} 存在重叠，按 R-NUM-04 判通过。",
            )
        return _mk(
            "错误",
            ["R-NUM-04"],
            f"区间不重叠：研报区间 {_fmt(low)}～{_fmt(high)} 与证据区间 "
            f"{_fmt(evidence_low)}～{_fmt(evidence_high)} 无交集，按 R-NUM-04 判错误。",
            "按证据区间更正研报区间表述。",
        )
    evidence_value = _num_value(evidence_numeric)
    if evidence_value is None:
        return _mk(
            "证据不足",
            ["R-NUM-04"],
            "证据侧数值不可解析，区间包含判定无法执行，按 R-NUM-04 出口证据不足。",
            "补充可解析的证据值后重核。",
        )
    converted = _to_claim_unit(Decimal(str(evidence_value)))
    if low <= converted <= high:
        return _ok(
            claim,
            candidates,
            config,
            ["R-NUM-04"],
            f"区间判定：证据值 {_fmt(converted)}（{evidence_text or _fmt(evidence_value)} 换算）"
            f"落在研报区间 {_fmt(low)}～{_fmt(high)} 内，按 R-NUM-04 判通过。",
        )
    return _mk(
        "错误",
        ["R-NUM-04"],
        f"区间判定：证据值 {_fmt(converted)}（{evidence_text or _fmt(evidence_value)} 换算）"
        f"落在研报区间 {_fmt(low)}～{_fmt(high)} 之外，按 R-NUM-04 判错误。",
        "按证据值更正研报区间表述。",
    )



def _is_valuation_claim(claim) -> bool:
    text = norm_text(claim.get("claim_text")) + norm_text(claim.get("context_quote"))
    return any(norm_text(word) in text for word in _VALUATION_WORDS)


def _asm_valuation_gate(claim, calculations) -> dict | None:
    """R-ASM-01 假设揭示与事实分离 / R-ASM-03 扩展范围边界（估值类统一人工）。"""
    if not _is_valuation_claim(claim):
        return None
    assumptions: list[str] = []
    boundaries: list[str] = []
    for calculation in calculations:
        items = calculation.get("assumptions")
        if isinstance(items, list):
            assumptions.extend(str(item) for item in items if str(item).strip())
        boundary = calculation.get("boundaries")
        if isinstance(boundary, str) and boundary.strip():
            boundaries.append(boundary)
    if assumptions and boundaries:
        return _mk(
            "需人工复核",
            ["R-ASM-03"],
            "估值假设类主张属扩展范围（自动估值仅主闭环达标后纳入），本阶段统一"
            "出口需人工复核；假设清单与适用边界已载明，按 R-ASM-01 记录后交人工判断，"
            "不按事实核对假设值（无事实基准，不得判错误）。",
            "人工评估假设合理性与适用边界。",
        )
    return _mk(
        "需人工复核",
        ["R-ASM-01", "R-ASM-03"],
        "估值/预测假设类主张未见完整的假设清单与适用边界（Calculation.assumptions 或 boundaries "
        "为空），按 R-ASM-01 假设揭示与事实分离要求不得按事实核对；估值属 R-ASM-03 扩展范围，"
        "统一出口需人工复核。",
        "补充假设清单与适用边界后交人工复核。",
    )


def _evaluate_qualitative(claim, candidates, config) -> dict:
    text = claim.get("claim_text") or ""
    trigger = _accounting_interpretation(text)
    if trigger:
        return _mk(
            "需人工复核",
            ["R-ACC-03"],
            f"主张涉及会计解释事项（触发词「{trigger}」：会计政策、重述、合并范围、收入确认等），"
            "按会计解释与数学事实分离原则禁止自动判定，转人工复核。",
            "由人工复核会计处理恰当性，不自动判错。",
        )

    quotes = [c.get("quote") for c in candidates if isinstance(c, dict) and c.get("quote")]
    target = norm_text(text)
    for quote in quotes:
        candidate_text = norm_text(quote)
        if target and (target in candidate_text or candidate_text in target):
            return _ok(
                claim,
                candidates,
                config,
                ["R-GEN-09"],
                f"定性事实字面层命中：主张文本与证据原文规范化（去空白、全半角统一）后匹配"
                f"（证据摘录：「{str(quote)[:40]}」），按 R-GEN-09 判通过。",
            )
    return _mk(
        "需人工复核",
        ["R-GEN-09"],
        "定性事实无字面匹配：证据原文可能存在同义/概括转述，语义等价性按 R-GEN-09 第②层"
        "不自动判定，转人工复核。",
        "人工核对语义等价性后结论。",
    )


def _amount_numbers(nums) -> set[str]:
    """过滤出"金额型"数字：排除年份与页码等小整数。"""
    out: set[str] = set()
    for token in nums:
        try:
            v = abs(float(token))
        except (TypeError, ValueError):
            continue
        if 1900 <= v <= 2100 and "." not in token:
            continue
        if v >= 1000 or "." in token:
            out.add(token)
    return out


def _magnitude_match(cited: set[str], quote_numbers: set[str]) -> bool:
    """引用金额与原文金额按单位档位（10^k）归一后比对。"""
    quotes = []
    for token in quote_numbers:
        try:
            quotes.append(float(token))
        except (TypeError, ValueError):
            continue
    for token in cited:
        try:
            cv = float(token)
        except (TypeError, ValueError):
            continue
        if cv == 0:
            continue
        matched = False
        for qv in quotes:
            if qv == 0:
                continue
            ratio = qv / cv
            if ratio <= 0:
                continue
            for k in range(-8, 9):
                ref = 10.0 ** k
                if abs(ratio - ref) / ref <= 1e-6:
                    matched = True
                    break
            if matched:
                break
        if not matched:
            return False
    return True


def _evaluate_citation(claim, candidates, config, materials=()) -> dict:
    reference = claim.get("reference")
    if not isinstance(reference, dict):
        return _mk(
            "证据不足",
            ["R-CIT-01"],
            "引用型主张缺少 reference 对象，无法回链被引材料（R-CIT-01）。",
            "补齐引用对象与被引定位后重核。",
        )
    rows = _candidate_rows(candidates)
    gate = _entity_gate(claim, rows, config)
    if gate:
        return gate

    # R-CIT-04 被引材料状态：status 为无效/解析失败 → 该材料内容不可用
    records = _dicts(materials)
    cited_material = reference.get("cited_material_id") or reference.get("material_id")
    if cited_material and records:
        record = next(
            (item for item in records if item.get("material_id") == cited_material), None
        )
        status = record.get("status") if record else None
        if status in ("无效", "解析失败", "不可用"):
            return _mk(
                "证据不足",
                ["R-CIT-04"],
                f"被引材料 {cited_material} 的 status=「{status}」，该材料内容不可用，"
                "按 R-CIT-04 出口证据不足（不以不可用材料支撑引用）。",
                "补充可用版本或改引已登记且解析成功的材料。",
            )

    # R-CIT-03 引用主体一致性：cited_subject 与 numeric.entity 必须一致
    numeric = claim.get("numeric") if isinstance(claim.get("numeric"), dict) else {}
    cited_subject = reference.get("cited_subject")
    claim_entity = numeric.get("entity")
    if cited_subject and any(marker in str(cited_subject) for marker in _AMBIGUOUS_SUBJECT):
        return _mk(
            "需人工复核",
            ["R-CIT-03"],
            f"引用主体「{cited_subject}」为指代表述，主体语义不清，按 R-CIT-03 转人工复核，"
            "不猜测被引主体。",
            "明确被引主体化名后重核。",
        )
    if cited_subject and claim_entity and str(cited_subject) != str(claim_entity):
        return _mk(
            "错误",
            ["R-CIT-03"],
            f"引用主体不一致：cited_subject=「{cited_subject}」，numeric.entity=「{claim_entity}」，"
            "按 R-CIT-03 判引用主体错配（以甲主体数据描述乙主体）。",
            "按被引材料主体更正引用表述或改用对应主体数据。",
        )

    # R-CIT-05 引用型主张证据要求：候选证据须指向被引材料，不得只引研报自身
    report_material = claim.get("report_material_id")
    cited_candidates = [
        item for item in _dicts(candidates) if item.get("material_id") != report_material
    ]
    if candidates and report_material and not cited_candidates:
        return _mk(
            "证据不足",
            ["R-CIT-05"],
            "引用型主张的候选证据全部来自研报自身材料（未回链被引材料），按 R-CIT-05 "
            "通过必须引用指向被引材料的候选证据，出口证据不足。",
            "补充被引材料的可回链证据后重核。",
        )

    if not reference.get("cited_locator"):
        return _mk(
            "证据不足",
            ["R-CIT-01"],
            "cited_locator=null，引用定位缺失，按 R-CIT-01 出口证据不足。",
            "补充被引材料定位后重核。",
        )

    cited = reference.get("cited_text") or ""
    quotes = [c.get("quote") for c in candidates if isinstance(c, dict) and c.get("quote")]
    if not quotes:
        return _mk(
            "证据不足",
            ["R-CIT-01"],
            "被引块无可比对原文（回链断裂），按 R-CIT-01 出口证据不足。",
            "补充被引块原文后重核。",
        )

    target = norm_text(cited)
    for quote in quotes:
        candidate_text = norm_text(quote)
        if target and (target in candidate_text or candidate_text in target):
            return _ok(
                claim,
                candidates,
                config,
                ["R-CIT-02"],
                f"引用内容与被引块原文规范化后字面一致（「{str(quote)[:40]}」），按 R-CIT-02 判通过。",
            )

    cited_numbers = _numbers(cited)
    quote_numbers: set[str] = set()
    for quote in quotes:
        quote_numbers |= _numbers(quote)

    amounts = _amount_numbers(cited_numbers)
    if amounts:
        if _magnitude_match(amounts, quote_numbers):
            return _ok(
                claim,
                candidates,
                config,
                ["R-CIT-02"],
                f"引用金额与原文金额按单位档位（10^k）归一后一致"
                f"（引用 {sorted(amounts)}），按 R-CIT-02 判通过。",
            )
        return _mk(
            "错误",
            ["R-CIT-02"],
            f"引用金额 {sorted(amounts)} 在被引块原文中不存在"
            f"（原文数值 {sorted(quote_numbers)[:6]}），按 R-CIT-02 判引用内容错配。",
            "按被引材料原文更正引用数值与表述。",
        )

    if cited_numbers and (cited_numbers - quote_numbers):
        return _mk(
            "错误",
            ["R-CIT-02"],
            f"引用文本数值 {sorted(cited_numbers)} 在被引块原文中不存在"
            f"（原文数值 {sorted(quote_numbers)}），按 R-CIT-02 判引用内容错配。",
            "按被引材料原文更正引用数值与表述。",
        )

    return _mk(
        "需人工复核",
        ["R-CIT-02"],
        "引用文本与被引块原文非字面一致，可能为同义转述，语义等价性按 R-CIT-02 第②层转人工。",
        "人工核对转述是否忠实后结论。",
    )


_TIE_SCOPE_RULES = {"表内": "R-TIE-01", "跨表": "R-TIE-02", "跨期": "R-TIE-03"}


def _tie_scope(calculation) -> str:
    """勾稽范围：params.tie_scope 优先，其次按输入的（材料、表格）组合推断。"""
    params = calculation.get("params") if isinstance(calculation.get("params"), dict) else {}
    scope = params.get("tie_scope") or params.get("勾稽范围") or calculation.get("tie_scope")
    if scope in _TIE_SCOPE_RULES:
        return scope
    sources: set[tuple] = set()
    for item in calculation.get("inputs") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("value") if isinstance(item.get("value"), dict) else item
        if not isinstance(value, dict):
            continue
        source = value.get("source") if isinstance(value.get("source"), dict) else {}
        locator = source.get("locator") if isinstance(source.get("locator"), dict) else {}
        sources.add((source.get("material_id"), locator.get("table_ref")))
    return "跨表" if len(sources) > 1 else "表内"


def _evaluate_tie(tie_calculations, unchecked, claim, candidates, config, extra_ids=()) -> dict:
    evidence_bad = False
    claim_bad = False
    missing = False
    scopes: list[str] = []
    restated = False
    for calculation in tie_calculations:
        scopes.append(_tie_scope(calculation))
        comparison = calculation.get("comparison")
        within = comparison.get("within_tolerance") if isinstance(comparison, dict) else None
        if not isinstance(within, bool):
            missing = True
            continue
        if _calc_side(calculation) == "evidence":
            evidence_bad = evidence_bad or (not within)
        else:
            claim_bad = claim_bad or (not within)
        params = calculation.get("params") if isinstance(calculation.get("params"), dict) else {}
        basis = (claim.get("numeric") or {}).get("basis") or {}
        note = " ".join(
            str(item) for item in (params.get("note"), basis.get("note")) if item
        )
        if "重述" in note:
            restated = True

    scope = next(
        (item for item in ("跨期", "跨表", "表内") if item in scopes), "表内"
    )
    rule_id = _TIE_SCOPE_RULES.get(scope, "R-TIE-01")

    # R-TIE-03 重述形态：重述后可比性属会计解释（R-ACC-03），一律转人工
    if restated:
        return _mk(
            "需人工复核",
            ["R-TIE-03", "R-TIE-05"],
            "勾稽涉及「重述」：口径说明或计算参数含重述标注，重述后期间可比性属会计解释"
            "（R-ACC-03），按 R-TIE-03 不自动判定，转人工复核。",
            "人工核对重述前后口径与可比性后结论。",
        )
    # R-TIE-04 容差来源：容差不可溯源时承载规则不出状态
    for calculation in tie_calculations:
        tolerance_gate = check_tie_tolerance(calculation)
        if tolerance_gate:
            return tolerance_gate

    if evidence_bad:
        return _mk(
            "冲突",
            [rule_id, "R-TIE-05", *extra_ids],
            f"勾稽差出现在证据侧：授权材料原表自身不平，按 R-TIE-05 责任分流取冲突（R3 人工裁定），"
            f"不判研报错误（勾稽范围：{scope}，承载规则 {rule_id}）。" + _basis_note(unchecked, "冲突"),
            "由 R3 裁定证据材料口径或版本问题。",
        )
    if claim_bad:
        return _mk(
            "错误",
            [rule_id, "R-TIE-05", *extra_ids],
            f"勾稽差出现在研报表述侧：研报表格勾稽不平且超出容差，按 {rule_id}/R-TIE-05 判错误"
            f"（勾稽范围：{scope}）。" + _basis_note(unchecked, "错误"),
            "按证据原表更正研报表格各项数值或合计项。",
        )
    if missing:
        return _mk(
            "需人工复核",
            ["R-FRM-04"],
            "勾稽计算记录缺少 comparison.within_tolerance，无法判定容差结果，按 R-FRM-04 转人工。",
            "补全勾稽计算记录后重核。",
        )
    return _ok(
        claim,
        candidates,
        config,
        [rule_id, "R-TIE-05", *extra_ids],
        f"研报侧与证据侧勾稽均在容差内一致（勾稽范围：{scope}），按 {rule_id} 判通过。"
        + _basis_note(unchecked, "通过"),
    )


def _direct_compare(claim, numeric, evidence_numeric, candidates, config, extra_ids=()) -> dict:
    """R-NUM-01/03 直接数值比对（无需 Calculation：同口径同期间的值对值比较）。"""
    from .numeric import half_ulp_in

    claim_unit = _unit_code(numeric.get("unit"))
    ev_unit = _unit_code(evidence_numeric.get("unit"))
    value_kind = numeric.get("value_kind")
    claim_value = _num_value(numeric)
    ev_value = _num_value(evidence_numeric)
    text = numeric.get("value_text") or f"{_fmt(claim_value)}{claim_unit}"

    if claim_value is None or ev_value is None:
        return _mk("需人工复核", ["R-NUM-01"], "数值不可解析，无法直接比对。", "人工核对数值。")

    _dim_c, fam_c, sc_c = _unit_info(numeric.get("unit"))
    _dim_e, fam_e, sc_e = _unit_info(evidence_numeric.get("unit"))
    if fam_c != fam_e or sc_c is None or sc_e is None:
        return _mk(
            "需人工复核",
            ["R-NUM-01"],
            f"单位不可换算（{claim_unit} 对 {ev_unit}），无法直接比对，转人工复核。",
            "统一单位口径后重核。",
        )

    try:
        tol = half_ulp_in(text, claim_unit, ev_unit)
    except Exception:
        tol = None
    if tol is None:
        # 兜底：展示粒度不可解析时取证据值的 1e-6 相对量级（仅防零容差，非统一容差）。
        tol = Decimal(str(abs(ev_value))) * Decimal("1e-6")

    converted = Decimal(str(claim_value)) * (Decimal(10) ** (sc_c - sc_e))
    diff = converted - Decimal(str(ev_value))
    abs_tol = Decimal(str(tol))
    scale_gap = Decimal(10) ** (sc_c - sc_e)
    diff_disp = abs(diff) / scale_gap
    tol_disp = abs_tol / scale_gap
    unit_disp = claim_unit if claim_unit not in ("unknown", "不适用") else ev_unit
    ev_text = evidence_numeric.get("value_text") or f"{_fmt(ev_value)}{ev_unit}"
    rule = "R-NUM-03" if value_kind == "约数" else "R-NUM-01"

    if abs(diff) <= abs_tol:
        # 直比路径同样要带口径注记：否则「口径未核对」的通过会比走计算路径的
        # 通过更干净，同一份报告里口径状态被隐去（R-CON-02 / R-ACC-04）。
        basis = numeric.get("basis") or {}
        unchecked = [name for name, key in
                     (("合并口径", "consolidation"), ("会计口径", "accounting"))
                     if basis.get(key) == "unknown"]
        reason = (f"数值一致：研报「{text}」换算后与证据「{ev_text}」相差 "
                  f"{_fmt(diff_disp)} {unit_disp}，在容差 {_fmt(tol_disp)} {unit_disp}以内，判通过。"
                  + _basis_note(unchecked, "通过"))
        return _ok(claim, candidates, config, [rule, *extra_ids], reason)

    if value_kind == "约数":
        return _mk(
            "需人工复核",
            ["R-NUM-03", *extra_ids],
            f"约数比对：研报「{text}」按取整粒度推定容差 {_fmt(tol_disp)} {unit_disp}，"
            f"与证据「{ev_text}」相差 {_fmt(diff_disp)} {unit_disp} 超出；"
            "约数不得按精确值判错，转人工复核。",
            "人工确认取整粒度或按精确值重述后重核。",
        )

    return _mk(
        "错误",
        ["R-NUM-01"],
        f"数值不符：研报「{text}」换算后与证据「{ev_text}」相差 {_fmt(diff_disp)} {unit_disp}，"
        f"超出容差 {_fmt(tol_disp)} {unit_disp}，判错误。",
        f"按证据值更正研报数值（{ev_text}）。",
    )


def _evaluate_numeric(claim, candidates, rows, calculations, calc_error, config) -> dict:
    numeric = claim.get("numeric")
    if not isinstance(numeric, dict):
        return _mk(
            "需人工复核",
            [],
            "数值型主张缺少 numeric（NumericValue），无法判定；按路由表转人工。",
            "补齐数值元数据后重核。",
        )

    unit = numeric.get("unit")
    code = _unit_code(unit)
    if code == "unknown":
        return _mk(
            "低置信",
            ["R-UNI-02"],
            "单位未声明（unit.code=unknown），按 R-UNI-02 禁用通过，"
            "不猜单位（拒绝臆造、不猜单位）。",
            "补充单位与币种（如 亿元）后重核。",
        )

    kind = numeric.get("value_kind")
    if kind == "文本值":
        return _mk(
            "低置信",
            ["R-NUM-02"],
            "数值为文本值（value_kind=文本值，value=null），不可比对，按 R-NUM-02 出口低置信。",
            "人工确认该表述的实际数值与单位。",
        )
    if kind == "区间值":
        return _evaluate_range(claim, numeric, rows, candidates, config)
    if _num_value(numeric) is None:
        return _mk(
            "低置信",
            ["R-NUM-02"],
            "数值不可解析（value 缺失或非数值），按 R-NUM-02 出口低置信。",
            "补齐可解析数值后重核。",
        )

    derived = claim.get("judgment_type") == "计算" or isinstance(numeric.get("comparison"), dict)
    evidence_row = _pick_evidence(rows, claim, numeric)
    evidence_numeric = evidence_row.get("numeric") if evidence_row else None

    if isinstance(evidence_numeric, dict) and not derived:
        claim_dim, claim_family, _ = _unit_info(unit)
        ev_dim, ev_family, _ = _unit_info(evidence_numeric.get("unit"))
        if claim_dim != "未知" and ev_dim != "未知" and claim_dim != ev_dim:
            return _mk(
                "错误",
                ["R-UNI-01"],
                f"单位维度不一致：研报为{claim_dim}（{code}），证据为{ev_dim}"
                f"（{_unit_code(evidence_numeric.get('unit'))}）；跨维度不得换算，按 R-UNI-01 判错误。",
                "按证据单位维度更正研报表述。",
            )
        if claim_dim == "货币" and ev_dim == "货币" and claim_family != ev_family:
            if not _has_fx(config, claim_family, ev_family):
                return _mk(
                    "证据不足",
                    ["R-UNI-03"],
                    f"跨币种比对（{claim_family} 对 {ev_family}）且材料内无汇率披露，"
                    "按 R-UNI-03 不得自行取汇率，出口证据不足。",
                    "补充材料内汇率披露或改用同币种口径。",
                )

    period = numeric.get("period") or {}
    period_kind = period.get("kind") or "unknown"
    if period_kind == "unknown":
        return _mk(
            "低置信",
            ["R-PER-04"],
            "期间未声明（period.kind=unknown），按 R-PER-04 禁用通过。",
            "补充报告期后重核。",
        )
    if isinstance(evidence_numeric, dict):
        ev_period_kind = ((evidence_numeric.get("period") or {}).get("kind")) or "unknown"
        if (
            period_kind in ("时点", "期间")
            and ev_period_kind in ("时点", "期间")
            and period_kind != ev_period_kind
        ):
            return _mk(
                "不适用",
                ["R-PER-02"],
                f"期间类型不可比：研报为{period_kind}，证据为{ev_period_kind}"
                "（时点对期间），按 R-PER-02 出口不适用。",
                "改用同类型期间的数据比对。",
            )

    gate = _entity_gate(claim, rows, config)
    if gate:
        return gate

    # 前置一致性与专项检查（冲突前置检查：前置不一致时数值规则不产结论，
    # 由 R-ENT/R-PER/R-CON/R-ACC/R-CMP 判定；命中即出口，未命中则记录已核对项）。
    checked: list[str] = []
    for result, ids in (
        _entity_consistency(numeric, evidence_numeric, config),
        _fiscal_year_consistency(numeric, evidence_numeric, rows, derived),
        _period_consistency(numeric, evidence_numeric, derived),
        _accounting_consistency(numeric, evidence_numeric),
        _cmp_type_consistency(claim, numeric),
        _cmp_baseline_consistency(numeric, rows, calculations),
        _percent_point_consistency(claim, numeric),
        _consolidation_consistency(numeric, evidence_numeric),
    ):
        if result is not None:
            return result
        checked.extend(ids)

    if isinstance(evidence_numeric, dict) and not derived:
        mismatch = _magnitude_mismatch(numeric, evidence_numeric)
        if mismatch:
            ratio, gap = mismatch
            claim_text_value = numeric.get("value_text") or f"{_fmt(numeric.get('value'))}{code}"
            ev_code = _unit_code(evidence_numeric.get("unit"))
            ev_text_value = evidence_numeric.get("value_text") or f"{_fmt(evidence_numeric.get('value'))}{ev_code}"
            return _mk(
                "错误",
                ["R-UNI-04"],
                f"数量级错配：研报「{claim_text_value}」与证据「{ev_text_value}」"
                f"换算后相差 {abs(gap)} 个标度档（比值 {ratio:.4g}），超过 R-UNI-04 的 100 倍阈值。",
                "核对单位标度（万元/亿元）后更正研报数值或单位。",
            )

    basis = numeric.get("basis") or {}
    unchecked: list[str] = []
    if basis.get("consolidation") == "unknown":
        unchecked.append("合并口径")
    if basis.get("accounting") == "unknown":
        unchecked.append("会计口径")

    tie_calculations = [
        c for c in calculations if str(c.get("formula_id") or "").upper().startswith("FRM-TIE")
    ]
    if tie_calculations:
        return _evaluate_tie(
            tie_calculations, unchecked, claim, candidates, config, tuple(checked)
        )

    if calc_error:
        return _mk(
            "需人工复核",
            ["R-FRM-04"],
            f"确定性计算失败（{calc_error}），按 R-FRM-04，计算未成功不得判通过。",
            "核对计算输入（如基期值是否为零）后重算。",
        )

    if not calculations:
        if isinstance(evidence_numeric, dict) and not derived:
            return _direct_compare(
                claim, numeric, evidence_numeric, candidates, config, tuple(checked)
            )
        return _mk(
            "需人工复核",
            [],
            "缺少确定性复算记录（Calculation）：本引擎不自行计算（见 README「模块与接口」），"
            "无法判定数值一致性，转人工复核。",
            "由管线补齐复算记录后重核。",
        )

    calculation = _pick_calc(calculations, claim)
    result_status = calculation.get("result_status")
    if result_status and result_status != "成功":
        return _mk(
            "需人工复核",
            ["R-FRM-04"],
            f"计算记录 result_status={result_status}"
            f"（{calculation.get('error') or '未给出错误信息'}），按 R-FRM-04，"
            "计算未成功不得判通过，转人工复核。",
            "核对计算输入（如基期值是否为零）后重算。",
        )
    input_gate = _formula_input_gate(calculation)
    if input_gate:
        return input_gate
    comparison = calculation.get("comparison") if isinstance(calculation.get("comparison"), dict) else {}
    within = comparison.get("within_tolerance")
    if not isinstance(within, bool):
        return _mk(
            "需人工复核",
            ["R-FRM-04"],
            "计算记录缺少 comparison.within_tolerance，容差结果不可判定，按 R-FRM-04 转人工复核。",
            "补全计算记录后重核。",
        )

    desc = _calc_desc(calculation, comparison)

    if kind == "约数":
        if within:
            return _ok(
                claim,
                candidates,
                config,
                ["R-NUM-03", *checked],
                f"约数判定：{desc}，在按 value_text 最低非零位推定的取整粒度容差内，按 R-NUM-03 判通过。",
            )
        return _mk(
            "需人工复核",
            ["R-NUM-03", *checked],
            f"约数判定：{desc}，超出推定取整粒度容差；「约」的边界属解释，按 R-NUM-03 不自动判错，转人工。",
            "人工确认约数语义与取整粒度后结论。",
        )

    if isinstance(numeric.get("comparison"), dict):
        rule_ids = ["R-CMP-02", *checked]
    elif claim.get("judgment_type") == "计算":
        rule_ids = ["R-FRM-01", "R-FRM-02", "R-FRM-05", *checked]
    else:
        rule_ids = ["R-NUM-01", *checked]

    if within:
        reason = f"数值一致：{desc}，差值在容差内，判通过。" + _basis_note(unchecked, "通过")
        return _ok(claim, candidates, config, rule_ids, reason)

    output = calculation.get("output") if isinstance(calculation.get("output"), dict) else {}
    out_unit = _unit_code(output.get("unit")) if isinstance(output.get("unit"), dict) else ""
    reason = f"数值不符：{desc}，超出容差，判错误。" + _basis_note(unchecked, "错误")
    suggestion = (
        f"将研报数值更正为复算值 {_fmt(output.get('value'))}{out_unit}，并核对计算输入的口径与期间。"
    )
    return _mk("错误", rule_ids, reason, suggestion)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def evaluate(claim: dict, ctx: dict) -> dict:
    """判定一条原子主张，返回 {status, rule_ids, reason, suggestion, severity}。

    claim：Claim 字段（claim_text/claim_type/judgment_type/numeric/reference/
           unknown_fields/report_locator/status）。
    ctx：  {"candidates": [EvidenceCandidate], "facts": [Fact],
            "calculations": [Calculation | None], "calc_error": str | None,
            "materials": [MaterialRecord]（R-CIT-04 用，可选）, "config": dict}
    纯函数：不修改 claim 与 ctx，不写事件，不分配 ID。
    """
    claim = claim if isinstance(claim, dict) else {}
    ctx = ctx if isinstance(ctx, dict) else {}
    candidates = _dicts(ctx.get("candidates"))
    facts = _dicts(ctx.get("facts"))
    calculations = _dicts(ctx.get("calculations"))
    config = ctx.get("config") if isinstance(ctx.get("config"), dict) else {}
    calc_error = ctx.get("calc_error")

    # 1) R-GEN-02 定位缺失（主张字段契约）
    if claim.get("report_locator") is None:
        return _mk(
            "待核验",
            ["R-GEN-02"],
            "主张缺少原文定位（report_locator=null），按 R-GEN-02 强制为待核验，"
            "不进入规则正判定。",
            "补充研报原文定位后重核。",
        )

    # 2) R-GEN-05 / R-GEN-08 冲突证据前置检查（必须先于判错）
    rel_tol = config.get("conflict_rel_tol") if isinstance(config, dict) else None
    if not isinstance(rel_tol, (int, float)):
        rel_tol = 1e-4
    if conflict_detected(candidates, rel_tol=float(rel_tol)):
        return _mk(
            "冲突",
            ["R-GEN-05", "R-GEN-08"],
            "冲突证据前置检查命中：两条及以上候选对同一（实体、指标、期间、口径）给出超容差的"
            "不同值，按 R-GEN-05 出口冲突并交 R3 人工裁定；按 R-GEN-08 第①步，不以冲突中的"
            "任一材料值直接判定对错。",
            "由 R3 人工裁定采信口径或补正材料。",
        )

    # 2.5) 计算失败（分母为零等）→ R-FRM-04 需人工复核
    if calc_error:
        return _mk(
            "需人工复核",
            ["R-FRM-04"],
            f"确定性计算失败（{calc_error}），按 R-FRM-04 出口需人工复核；"
            "不以 0 或无穷代替结果，不据此判对错。",
            "人工确认口径或补正输入后重算。",
        )

    # 2.6) 会计解释类（R-ACC-03）：禁自动判定（先于证据齐备性检查）
    if _accounting_interpretation(claim.get("claim_text")):
        return _mk(
            "需人工复核",
            ["R-ACC-03"],
            "主张涉会计解释（收入确认、会计政策/估计变更、重述等），按 "
            "R-ACC-03 禁自动判定，转人工复核。",
            "由人工结合会计政策说明判断。",
        )

    # 2.7) 估值假设类（R-ASM-01/03）：扩展范围统一人工（先于证据齐备性检查，
    #      按优先级「需人工复核 ＞ 证据不足」）。
    valuation_gate = _asm_valuation_gate(claim, calculations)
    if valuation_gate:
        return valuation_gate

    # 3) R-GEN-04 无候选证据
    if not candidates and not facts and _needs_evidence(claim, config):
        return _mk(
            "证据不足",
            ["R-GEN-04"],
            "检索未取得候选证据（EvidenceSet 为空），按 R-GEN-04 出口证据不足；不猜内容、"
            "不生成新事实（拒绝臆造）。",
            "补充授权材料或调整检索口径后重核。",
        )

    claim_type = claim.get("claim_type")
    judgment = claim.get("judgment_type")

    # 4) R-ASM-02 不可核验项（路由表 / 估值假设类规则）
    if judgment == "观点":
        return _mk(
            "不适用",
            ["R-ASM-02"],
            "主张为观点（judgment_type=观点），不属可核验项，按路由表出口不适用，"
            "不判真伪、不生成新事实。",
            None,
        )
    if judgment == "推论" and _period_future(claim, config):
        numeric = claim.get("numeric") or {}
        period = numeric.get("period") or {}
        cutoff = config.get("data_cutoff") or claim.get("data_cutoff")
        return _mk(
            "不适用",
            ["R-ASM-02"],
            f"预测期间（起于 {period.get('start') or period.get('point')}）晚于数据截止点"
            f"（{cutoff}），按 R-ASM-02 属可核验性边界之外，出口不适用。",
            None,
        )
    if claim_type == "定性表述型" and judgment == "推论":
        return _mk(
            "不适用",
            ["R-ASM-02"],
            "定性表述型推论不可核验（路由表），出口不适用。",
            None,
        )
    if claim_type == "无法归类":
        return _mk(
            "需人工复核",
            [],
            "主张类型=无法归类（路由表），不自动判定；回退重核由 RA7 或人工复核员"
            "按状态流转约定触发，RA5 不自行发起。",
            "人工归类后重核。",
        )

    # 5) R-GEN-09 定性事实
    if claim_type == "定性表述型":
        return _evaluate_qualitative(claim, candidates, config)

    # 6) 引用型
    if claim_type == "引用型":
        return _evaluate_citation(claim, candidates, config, _dicts(ctx.get("materials")))

    # 7) 数值型
    if claim_type == "数值型":
        rows = _candidate_rows(candidates) + _fact_rows(facts)
        return _evaluate_numeric(claim, candidates, rows, calculations, calc_error, config)

    return _mk(
        "需人工复核",
        [],
        f"未登记的主张类型 {claim_type!r}，按路由表转人工复核。",
        "人工确认主张类型后重核。",
    )
