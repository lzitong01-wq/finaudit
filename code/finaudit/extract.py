"""主张拆解（RA4）：从研报抽取原子主张（LLM 为引擎，确定性解析为落账层）。

流程：
1. 取研报正文（页标记保留）；
2. 调 LLM（claim_extract.v1）→ JSON 数组；
3. 逐条规范化为 Claim：claim_id/定位/claim_type/judgment_type/numeric（数值可解析时）；
4. 数值元数据（单位/期间）由本地解析器补齐，LLM 不产出计算值。

mock provider 输出为夹具的「主张清单」结构化 JSON；openai provider 输出为模型生成的
JSON 数组。两条路径共用同一规范化流程，产出结构一致；结构校验在导出前统一执行
（见 `report._schema_checks`），不合格项阻断放行，不在抽取阶段静默丢弃。
"""

from __future__ import annotations

import json
import re
import calendar

from .ids import IdAllocator
from .llm import LLMClient, LLMError
from .numeric import to_decimal
from .periods import parse_period_label

NUM_TOKEN = re.compile(r"(-?[\d,]+(?:\.\d+)?)\s*(亿元|万元|元|%|倍|次|美元|亿美元)?")


POINT_METRICS = ("资产合计", "负债合计", "所有者权益合计", "存货", "资产负债率")


def _period_for_claim(text: str, default_period: dict | None = None) -> dict:
    """从主张和任务范围推断期间；证据不足时保留未知，不假定 2024 年。"""
    clean = text.replace(" ", "")
    point_metric = any(k in clean for k in POINT_METRICS if k != "存货") or (
        "存货" in clean and not any(k in clean for k in ("周转率", "周转天数")))
    point_phrase = any(k in clean for k in ("年末", "年初", "期末", "期初", "截至"))

    date_match = re.search(
        r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?", clean)
    explicit_date = None
    if date_match:
        year, month, day = map(int, date_match.groups())
        try:
            explicit_date = f"{year:04d}-{month:02d}-{day:02d}"
            # 校验日期，避免把不可能的日期写入账本。
            from datetime import date
            date(year, month, day)
        except ValueError:
            explicit_date = None

    quarter_match = re.search(
        r"(20\d{2})\s*(?:年\s*)?(?:Q([1-4])|第([1-4一二三四])季度)", clean,
        re.IGNORECASE)
    quarter_period = None
    if quarter_match:
        year = int(quarter_match.group(1))
        q_raw = quarter_match.group(2) or quarter_match.group(3)
        quarter = int(q_raw) if q_raw.isdigit() else "一二三四".index(q_raw) + 1
        start_month = (quarter - 1) * 3 + 1
        end_month = start_month + 2
        quarter_period = {
            "kind": "期间", "point": None,
            "start": f"{year:04d}-{start_month:02d}-01",
            "end": f"{year:04d}-{end_month:02d}-{calendar.monthrange(year, end_month)[1]:02d}",
            "label": f"{year}Q{quarter}",
        }

    half_match = re.search(r"(20\d{2})\s*(?:年\s*)?(上半年|下半年|H1|H2|1H|2H)",
                           clean, re.IGNORECASE)
    half_period = None
    if half_match:
        year = int(half_match.group(1))
        is_first = half_match.group(2).upper() in ("上半年", "H1", "1H")
        half_period = {
            "kind": "期间", "point": None,
            "start": f"{year:04d}-01-01" if is_first else f"{year:04d}-07-01",
            "end": f"{year:04d}-06-30" if is_first else f"{year:04d}-12-31",
            "label": f"{year}{'H1' if is_first else 'H2'}",
        }

    ytd_match = re.search(
        r"(20\d{2}年(?:前三季度|\d{1,2}月?[-至到]\d{1,2}月))", clean)
    ytd_period = parse_period_label(ytd_match.group(1)) if ytd_match else None

    if explicit_date and (point_metric or point_phrase):
        return {"kind": "时点", "point": explicit_date, "start": None,
                "end": None, "label": explicit_date}

    period = quarter_period or half_period or ytd_period
    year_match = re.search(r"(20\d{2})(?=年度|年全年|年|Q[1-4]|上半年|下半年|H[12])",
                           clean, re.IGNORECASE)
    year = int(year_match.group(1)) if year_match else None
    if period is None and year is not None:
        period = {"kind": "期间", "point": None, "start": f"{year:04d}-01-01",
                  "end": f"{year:04d}-12-31", "label": f"{year}年度"}

    if point_phrase and point_metric:
        point = None
        start_is_comparison_base = bool(re.search(r"(?:较|比|相比|相较)(?:年初|期初)", clean))
        if "年末" in clean or "期末" in clean or "截至" in clean:
            point = ((period or {}).get("end") or (default_period or {}).get("point")
                     or (default_period or {}).get("end"))
            if point is None and year:
                point = f"{year:04d}-12-31"
        elif start_is_comparison_base:
            # “较年初/期初”描述比较基准，不是本期数值所属时点。
            point = ((period or {}).get("end") or (default_period or {}).get("point")
                     or (default_period or {}).get("end"))
        elif "年初" in clean:
            base_year = year or (int(default_period["start"][:4])
                                 if default_period and default_period.get("start") else None)
            if base_year:
                point = f"{base_year:04d}-01-01"
        elif "期初" in clean:
            point = ((period or {}).get("start") or (default_period or {}).get("start"))
        elif period and period.get("end"):
            point = period["end"]
        elif default_period:
            point = default_period.get("point") or default_period.get("end")
        elif year:
            point = f"{year:04d}-12-31"
        if point:
            return {"kind": "时点", "point": point, "start": None, "end": None,
                    "label": point}

    if point_metric:
        point = ((period or {}).get("end") or (default_period or {}).get("point")
                 or (default_period or {}).get("end"))
        if not point and year:
            point = f"{year:04d}-12-31"
        if point:
            return {"kind": "时点", "point": point, "start": None, "end": None,
                    "label": point}

    if period:
        return dict(period)
    if default_period:
        return dict(default_period)
    return {"kind": "期间", "point": None, "start": None, "end": None,
            "label": "期间未确定"}


def _parse_numeric(text: str, default_period: dict | None = None,
                   material_id: str = "MAT-0001",
                   preferred_value_text: str | None = None) -> dict | None:
    matches = list(NUM_TOKEN.finditer(text))
    if not matches:
        return None
    # 模型可提供 value_text 作为候选位置，但只能在其逐字存在于原文时采用；
    # 数值本身、单位、期间等字段均由本地解析，不能信任模型生成的元数据。
    with_unit = [m for m in matches if m.group(2)]
    preferred = None
    if preferred_value_text and preferred_value_text in text:
        start = text.find(preferred_value_text)
        candidate = NUM_TOKEN.match(text, start)
        if candidate and candidate.group(0).strip() == preferred_value_text.strip():
            preferred = candidate
    m = preferred or (with_unit[-1] if with_unit else matches[-1])
    raw, unit = m.group(1), m.group(2) or ""
    value = to_decimal(raw)
    if value is None:
        return None
    value_kind = "精确值"
    if "约" in text[: m.start() + 1]:
        value_kind = "约数"
    period = _period_for_claim(text, default_period)
    # 实体推断：出现"子公司X"时归到该主体（避免以集团数替代，R-ENT-03）
    entity = "甲企业"
    sub = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]{2,20}子公司[\u4e00-\u9fa5A-Za-z0-9]*)", text)
    if sub:
        entity = sub.group(1)
    return {
        "value": value, "value_text": f"{raw}{unit}", "value_kind": value_kind,
        "range_low": None, "range_high": None, "entity": entity,
        "unit": {"code": unit or "unknown", "other": None},
        "period": period,
        "basis": {"consolidation": "合并报表", "accounting": "unknown", "note": None},
        "source": {"kind": "material", "material_id": material_id,
                   "block_id": None, "locator": None, "quote": None, "calc_id": None},
        "comparison": None,
    }


def _strip_fences(raw: str) -> str:
    """剥离 ```json ... ``` 代码块与前后说明文字，取第一个 JSON 数组。"""
    text = raw.strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("["):
                return candidate
        text = parts[1] if len(parts) > 1 else text
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text


def _page_of(text: str, claim_text: str) -> int | None:
    """在带 <!-- page N --> 标记的原文中回查主张所在页（首次出现时调用）。"""
    pattern = re.compile(r"<!--\s*page\s+(\d+)\s*-->")
    parts = pattern.split(text)
    probe = claim_text.strip().rstrip("。.")
    if not probe:
        return None
    for i in range(1, len(parts), 2):
        page = int(parts[i])
        body = parts[i + 1] if i + 1 < len(parts) else ""
        if probe in body:
            return page
    return None


def _split_report_text(text: str, max_chars: int = 6000,
                      overlap_chars: int = 300) -> list[tuple[int, int, str]]:
    """分块覆盖全文；保留重叠区，防止边界处的主张被切断。"""
    if not text:
        return []
    chunks: list[tuple[int, int, str]] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            floor = start + max_chars * 2 // 3
            boundary = max(text.rfind("\n", floor, end),
                           text.rfind("。", floor, end),
                           text.rfind("；", floor, end))
            if boundary >= floor:
                end = boundary + 1
        chunk = text[start:end]
        if chunk.strip():
            chunks.append((start, end, chunk))
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return chunks


def extract_claims(report_text: str, llm: LLMClient, alloc: IdAllocator,
                   task_id: str, report_material_id: str = "",
                   default_period: dict | None = None) -> list[dict]:
    """调用 LLM 抽取主张；失败返回空列表（管线降级，不静默编造）。

    report_material_id 必须由调用方按材料登记给出（不按编号假设），缺省即报错。
    """
    if not report_material_id:
        raise ValueError("extract_claims 需显式传入 report_material_id（研报材料编号）")
    provider = getattr(getattr(llm, "config", None), "provider", "openai")
    # Mock 输出是整份夹具的金标准，不按文本重复调用；真实模型则分块覆盖全文。
    chunks = ([(0, len(report_text), report_text)] if provider == "mock"
              else _split_report_text(report_text))
    if not chunks:
        return []

    parsed_items: list[tuple[dict, int, int]] = []
    seen_overlap: set[tuple[str, int | None]] = set()
    for chunk_index, (start, end, chunk_text) in enumerate(chunks):
        try:
            result = llm.call("claim_extract.v2", {"report_text": chunk_text})
        except LLMError:
            # 任一块失败即不返回不完整的主张集；界面通过事件显式提示失败。
            return []
        raw = _strip_fences(result["text"])
        try:
            chunk_items = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(chunk_items, dict) and "claims" in chunk_items:
            chunk_items = chunk_items["claims"]
        if not isinstance(chunk_items, list):
            return []

        overlap_len = max(0, chunks[chunk_index - 1][1] - start) if chunk_index else 0
        for item in chunk_items:
            if not isinstance(item, dict):
                continue
            claim_text = str(item.get("claim_text") or "").strip()
            if not claim_text:
                continue
            page_value = item.get("page")
            page_no = (int(page_value) if isinstance(page_value, int)
                       or (isinstance(page_value, str) and page_value.isdigit()) else None)
            if page_no is None:
                page_no = _page_of(report_text, claim_text)
            normalized = re.sub(r"\s+", "", claim_text).rstrip("。.")
            key = (normalized, page_no)
            local_pos = chunk_text.find(claim_text)
            if chunk_index and 0 <= local_pos < overlap_len and key in seen_overlap:
                continue
            seen_overlap.add(key)
            parsed_items.append((item, chunk_index, start))

    claims: list[dict] = []
    seen_texts: set[str] = set()
    for item, _chunk_index, _chunk_start in parsed_items:
        text = str(item.get("claim_text") or "").strip()
        if not text:
            continue
        key = text.rstrip("。.")
        first_seen = key not in seen_texts
        seen_texts.add(key)
        page = item.get("page")
        page_no = (int(page) if isinstance(page, int)
                   or (isinstance(page, str) and page.isdigit()) else None)
        if page_no is None and first_seen:
            # 模型漏页码时按原文回查；重复文本不回查（无法区分指代，保持定位缺失）
            page_no = _page_of(report_text, text)
        claim_type = item.get("claim_type") or "数值型"
        judgment = item.get("judgment_type") or "事实"
        locator = ({"type": "页码", "page": page_no, "table_ref": None,
                    "text_range": None, "value": f"P{page_no}"} if page_no else None)
        claim = {
            "claim_id": alloc.next("claim"),
            "task_id": task_id,
            "contract_version": "finaudit-data-contract-v1",
            "report_material_id": report_material_id,
            "report_locator": locator,
            "claim_text": text,
            "context_quote": text,
            "claim_type": claim_type,
            "judgment_type": judgment,
            "numeric": None,
            "reference": None,
            "unknown_fields": [],
            "status": "待核验",
        }
        if claim_type == "数值型" or claim_type == "引用型":
            # 夹具 mock 保留预先标注的金标准；真实模型只允许提供原文位置提示，
            # 金额、单位、实体和期间全部由本地解析器从主张文本及任务范围生成。
            if provider == "mock" and isinstance(item.get("numeric"), dict):
                numeric = dict(item["numeric"])
                numeric["source"] = dict(numeric.get("source") or {})
                numeric["source"]["material_id"] = report_material_id
                numeric["source"]["locator"] = claim["report_locator"]
                numeric["source"]["quote"] = text
                claim["numeric"] = numeric
            else:
                model_numeric = item.get("numeric") if isinstance(item.get("numeric"), dict) else {}
                parsed = _parse_numeric(
                    text, default_period=default_period, material_id=report_material_id,
                    preferred_value_text=model_numeric.get("value_text"))
                if parsed is not None:
                    parsed["source"]["locator"] = claim["report_locator"]
                    parsed["source"]["quote"] = text
                    claim["numeric"] = parsed
                else:
                    claim["unknown_fields"] = ["单位"]
        if isinstance(item.get("reference"), dict):
            claim["reference"] = item["reference"]
        elif claim_type == "引用型":
            # 模型未给 reference 时，从原文推断（页码/被引主体/被引文字）
            page_hit = re.search(r"第\s*(\d+)\s*页", text)
            claim["reference"] = {
                "cited_subject": "甲企业" if "甲企业" in text else None,
                "cited_text": text,
                "cited_locator": ({"type": "页码", "page": int(page_hit.group(1)),
                                   "table_ref": None, "text_range": None,
                                   "value": f"P{page_hit.group(1)}"}
                                  if page_hit else None),
            }
        if isinstance(claim.get("reference"), dict):
            cited_subject = claim["reference"].get("cited_subject")
            if cited_subject:
                claim["entity"] = cited_subject
                if isinstance(claim.get("numeric"), dict):
                    claim["numeric"]["entity"] = cited_subject
        claims.append(claim)
    return claims
