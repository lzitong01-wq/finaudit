"""金融期间解析与规范化。

期间必须来自明确任务范围或材料表头；未知期间保持未知，不以固定年份补齐。
"""

from __future__ import annotations

import calendar
import re
from datetime import date


def _iso(year: int, month: int, day: int) -> str:
    return date(year, month, day).isoformat()


def _period(kind: str, start: str | None, end: str | None,
            label: str, point: str | None = None) -> dict:
    return {"kind": kind, "point": point, "start": start, "end": end,
            "label": label}


def parse_period_label(value: str) -> dict | None:
    """解析年度、季度、半年、日期时点或明确区间；不识别时返回 None。"""
    raw = re.sub(r"\s+", "", str(value or ""))
    if not raw:
        return None

    date_re = r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?"
    range_match = re.fullmatch(date_re + r"(?:至|到|~|～|—|–|-|—)" + date_re, raw)
    if range_match:
        y1, m1, d1, y2, m2, d2 = map(int, range_match.groups())
        try:
            start, end = _iso(y1, m1, d1), _iso(y2, m2, d2)
        except ValueError:
            return None
        if start > end:
            return None
        return _period("期间", start, end, raw)

    point_match = re.fullmatch(date_re, raw)
    if point_match:
        year, month, day = map(int, point_match.groups())
        try:
            point = _iso(year, month, day)
        except ValueError:
            return None
        return _period("时点", None, None, point, point)

    year_match = re.fullmatch(r"(20\d{2})(.*)", raw)
    if not year_match:
        return None
    year = int(year_match.group(1))
    suffix = year_match.group(2).lower()

    quarter_match = re.fullmatch(r"年?-?q([1-4])|年?第([一二三四1-4])季度", suffix,
                                 re.IGNORECASE)
    if quarter_match:
        q_raw = quarter_match.group(1) or quarter_match.group(2)
        quarter = int(q_raw) if q_raw.isdigit() else "一二三四".index(q_raw) + 1
        first_month = (quarter - 1) * 3 + 1
        last_month = first_month + 2
        start = _iso(year, first_month, 1)
        end = _iso(year, last_month, calendar.monthrange(year, last_month)[1])
        return _period("期间", start, end, f"{year}Q{quarter}")

    if re.fullmatch(r"年前三季度", suffix):
        return _period("期间", f"{year:04d}-01-01", f"{year:04d}-09-30",
                       f"{year}前三季度")

    half_match = re.fullmatch(r"年?(上半年|下半年|h1|h2|1h|2h)", suffix,
                              re.IGNORECASE)
    if half_match:
        first_half = half_match.group(1).upper() in ("上半年", "H1", "1H")
        start, end = ((f"{year:04d}-01-01", f"{year:04d}-06-30") if first_half
                      else (f"{year:04d}-07-01", f"{year:04d}-12-31"))
        return _period("期间", start, end, f"{year}{'H1' if first_half else 'H2'}")

    month_match = re.fullmatch(r"年?(\d{1,2})月?[-至到](\d{1,2})月", suffix)
    if month_match:
        first_month, last_month = map(int, month_match.groups())
        if not (1 <= first_month <= last_month <= 12):
            return None
        start = _iso(year, first_month, 1)
        end = _iso(year, last_month, calendar.monthrange(year, last_month)[1])
        return _period("期间", start, end, raw)

    if suffix in ("", "年", "年度", "年全年", "全年"):
        return _period("期间", f"{year:04d}-01-01", f"{year:04d}-12-31",
                       f"{year}年度")

    point_match = re.fullmatch(r"年?(年末|年初|期末|期初)", suffix)
    if point_match:
        point = (f"{year:04d}-01-01" if point_match.group(1) in ("年初", "期初")
                 else f"{year:04d}-12-31")
        return _period("时点", None, None, point, point)
    return None


def period_key(period: dict | None) -> str | None:
    """将期间转换成精确匹配键：年度、季度、半年、日期或起止日。"""
    if not isinstance(period, dict):
        return None
    if period.get("kind") == "时点":
        point = period.get("point")
        return str(point) if point else None
    start, end = period.get("start"), period.get("end")
    if not start or not end:
        return None
    try:
        start_date, end_date = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    except ValueError:
        return None
    if start_date > end_date:
        return None
    year = start_date.year
    if (start_date == date(year, 1, 1)
            and end_date == date(year, 12, 31)):
        return str(year)
    for quarter in range(1, 5):
        first_month = (quarter - 1) * 3 + 1
        last_month = first_month + 2
        if (start_date == date(year, first_month, 1)
                and end_date == date(year, last_month,
                                     calendar.monthrange(year, last_month)[1])):
            return f"{year}Q{quarter}"
    if start_date == date(year, 1, 1) and end_date == date(year, 6, 30):
        return f"{year}H1"
    if start_date == date(year, 7, 1) and end_date == date(year, 12, 31):
        return f"{year}H2"
    return f"{start_date.isoformat()}/{end_date.isoformat()}"


def period_for_metric(scope: dict | None, metric: str,
                      point_metrics: set[str] | frozenset[str]) -> dict | None:
    """按指标类型把任务/表头范围转换为流量期间或期末时点。"""
    if not isinstance(scope, dict):
        return None
    if metric in point_metrics:
        point = scope.get("point") if scope.get("kind") == "时点" else scope.get("end")
        if not point:
            return None
        try:
            point = date.fromisoformat(str(point)).isoformat()
        except ValueError:
            return None
        return _period("时点", None, None, point, point)
    if scope.get("kind") == "时点":
        return None
    result = dict(scope)
    return result if period_key(result) is not None else None


def previous_year_period(scope: dict | None, *, point_at_year_end: bool = False) -> dict | None:
    """取同比期间；时点默认平移一年，`point_at_year_end` 用于年初比较基准。"""
    if not isinstance(scope, dict):
        return None
    if scope.get("kind") == "时点":
        raw_point = scope.get("point")
        if not raw_point:
            return None
        try:
            current = date.fromisoformat(str(raw_point))
            year = current.year - 1
            point = (date(year, 12, 31) if point_at_year_end else
                     current.replace(year=year))
        except ValueError:
            return None
        point_text = point.isoformat()
        return _period("时点", None, None, point_text, point_text)

    start, end = scope.get("start"), scope.get("end")
    if not start or not end:
        return None
    try:
        first, last = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    except ValueError:
        return None

    def shift_one_year(value: date) -> date:
        try:
            return value.replace(year=value.year - 1)
        except ValueError:
            # 2 月 29 日的同比边界按 2 月 28 日对齐。
            return value.replace(year=value.year - 1, day=28)

    shifted_first = shift_one_year(first)
    shifted_last = shift_one_year(last)
    label = period_key({"kind": "期间", "start": shifted_first.isoformat(),
                        "end": shifted_last.isoformat()}) or "同比基期"
    return _period("期间", shifted_first.isoformat(), shifted_last.isoformat(), label)


def prior_year_end(scope: dict | None) -> dict | None:
    """由期间期末生成较年初比较所需的上一财年末时点。"""
    if not isinstance(scope, dict):
        return None
    anchor = scope.get("point") if scope.get("kind") == "时点" else scope.get("end")
    if not anchor:
        return None
    try:
        year = date.fromisoformat(str(anchor)).year - 1
    except ValueError:
        return None
    point = f"{year:04d}-12-31"
    return _period("时点", None, None, point, point)


def previous_period_end(scope: dict | None) -> dict | None:
    """上一报告区间的期末时点，用于表头明确标注“上期末”的资产负债项目。"""
    if not isinstance(scope, dict) or scope.get("kind") == "时点" or not scope.get("start"):
        return None
    try:
        point = date.fromisoformat(str(scope["start"])).fromordinal(
            date.fromisoformat(str(scope["start"])).toordinal() - 1)
    except (ValueError, OverflowError):
        return None
    point_text = point.isoformat()
    return _period("时点", None, None, point_text, point_text)
