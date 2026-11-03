"""数值与容差（数值精度与档位）。

职责：
- 展示粒度解析（最小有效位）：``parse_granularity``；
- 单位档位换算：``unit_scale`` / ``to_unit`` / ``half_ulp_in``；
- 容差缺省值（主规则 A/B + 公式族兜底）：``tolerance_abs``；
- 容差判定：``within``。

约束：全流程使用 ``decimal.Decimal``，禁止 float 参与判定；
金额/比率一律经 ``to_decimal`` 从 int/str/Decimal（float 走 repr 字符串）转换。
中间计算精度由模块级 decimal context 保证（至少 40 位，本模块取 50 位），
中间步骤不显式舍入。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, getcontext

# 中间计算等价精度十进制、中间不舍入。50 位有效数字远高于 40 位下限。
if getcontext().prec < 50:
    getcontext().prec = 50

_TWO = Decimal(2)

# 全角数字/符号归一，千分位逗号在取数时统一剔除。
_FULLWIDTH = str.maketrans("０１２３４５６７８９．，－＋", "0123456789.,-+")
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")

# 十进制档位表（R-UNI-01）：货币按基本货币单位计，比率/计数类为 1。
_UNIT_SCALES: dict[str, Decimal] = {
    # 人民币
    "元": Decimal(1),
    "千元": Decimal(10) ** 3,
    "万元": Decimal(10) ** 4,
    "百万元": Decimal(10) ** 6,
    "亿元": Decimal(10) ** 8,
    "十亿元": Decimal(10) ** 9,
    # 美元
    "美元": Decimal(1),
    "千美元": Decimal(10) ** 3,
    "万美元": Decimal(10) ** 4,
    "百万美元": Decimal(10) ** 6,
    "亿美元": Decimal(10) ** 8,
    # 港元
    "港元": Decimal(1),
    "万港元": Decimal(10) ** 4,
    "亿港元": Decimal(10) ** 8,
    # 欧元
    "欧元": Decimal(1),
    "万欧元": Decimal(10) ** 4,
    "亿欧元": Decimal(10) ** 8,
    # 日元
    "日元": Decimal(1),
    "万日元": Decimal(10) ** 4,
    # 非货币单位（契约枚举内可用项）
    "%": Decimal(1),
    "百分点": Decimal(1),
    "倍": Decimal(1),
    "次": Decimal(1),
    "年": Decimal(1),
    "个月": Decimal(1),
    "日": Decimal(1),
    "天": Decimal(1),
    "吨": Decimal(1),
    "平方米": Decimal(1),
    "千瓦时": Decimal(1),
    # 计数单位：与"次"同维度（无量纲计数），档位为 1。
    "人": Decimal(1),
    "个": Decimal(1),
    "件": Decimal(1),
}

_UNAVAILABLE_UNITS = frozenset({"unknown", "不适用", "OTHER"})

_FAMILIES = frozenset({"CMP", "IND", "TIE"})


# --------------------------------------------------------------------------
# 基础转换
# --------------------------------------------------------------------------


def to_decimal(value: object) -> Decimal:
    """把 int/str/Decimal（float 经 repr）转为 Decimal，禁止 float 直接参与运算。"""

    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool 是 int 子类，显式拒绝避免误用
        raise ValueError("布尔值不是合法数值")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("，", "")
        if not text:
            raise ValueError("空数值")
        try:
            return Decimal(text)
        except InvalidOperation:
            raise ValueError(f"无法解析数值：{value}") from None
    raise ValueError(f"不支持的数值类型：{type(value).__name__}")


def _nv_value(nv: dict) -> Decimal:
    if not isinstance(nv, dict):
        raise ValueError("NumericValue 须为对象")
    if nv.get("value") is None:
        raise ValueError("数值缺失（value 为空）")
    return to_decimal(nv["value"])


def _nv_unit(nv: dict) -> str:
    unit = nv.get("unit") if isinstance(nv, dict) else None
    if not isinstance(unit, dict):
        raise ValueError("单位缺失")
    code = unit.get("code")
    if not isinstance(code, str) or not code:
        raise ValueError("单位代码缺失")
    return code


# --------------------------------------------------------------------------
# 展示粒度
# --------------------------------------------------------------------------


def _exact_granularity(token: str) -> Decimal:
    """精确值：小数点后 n 位 → 10^-n；无小数点 → 1。"""

    if "." in token:
        frac = token.split(".", 1)[1]
        return Decimal(1).scaleb(-len(frac))
    return Decimal(1)


def _approximate_granularity(token: str) -> Decimal:
    """约数：取最低非零数字所在位（如约1,200→100、约1,285→1）。"""

    body = token.lstrip("+-")
    int_part, _, frac_part = body.partition(".")
    digits = int_part + frac_part
    last_nonzero: int | None = None
    for index, char in enumerate(digits):
        if char != "0":
            last_nonzero = index
    if last_nonzero is None:  # 约 0 之类：无有效末位，退化为个位
        return Decimal(1)
    exponent = len(int_part) - 1 - last_nonzero
    return Decimal(1).scaleb(exponent)


def parse_granularity(value_text: str) -> Decimal | None:
    """返回 ``value_text`` 的展示粒度（最小有效位）；无法解析返回 None。

    - 精确值：取数字文本小数点后的位数 n → 10^-n；无小数点 → 1；
      千分位逗号忽略，全角数字/符号先归一；
    - 约数（文本含"约"）：取最低非零数字所在位（"约1,200亿元"→100，
      "约1,285亿元"→1，"约12.5%"→0.1）；
    - 无数字（如 "N/A"、"不适用"、"—"）→ None。
    """

    if not isinstance(value_text, str) or not value_text.strip():
        return None
    text = value_text.translate(_FULLWIDTH)
    match = _NUMBER_RE.search(text)
    if match is None:
        return None
    token = match.group(0).replace(",", "")
    if "约" in value_text:
        return _approximate_granularity(token)
    return _exact_granularity(token)


# --------------------------------------------------------------------------
# 单位换算
# --------------------------------------------------------------------------


def unit_scale(code: str) -> Decimal:
    """单位 → 基本货币单位（元/美元/港元/欧元/日元）的十进制档位系数。

    unknown / 不适用 / OTHER 及枚举外代码一律 ``ValueError``（不可换算）。
    """

    if not isinstance(code, str) or not code:
        raise ValueError("单位代码为空")
    if code in _UNAVAILABLE_UNITS:
        raise ValueError(f"单位不可换算：{code}")
    try:
        return _UNIT_SCALES[code]
    except KeyError:
        raise ValueError(f"未知单位代码：{code}") from None


def to_unit(nv: dict, target_code: str) -> Decimal:
    """``nv["value"]`` 换算到目标单位：value × scale(src) ÷ scale(target)。"""

    source = _nv_unit(nv)
    return _nv_value(nv) * unit_scale(source) / unit_scale(target_code)


def half_ulp_in(value_text: str, src_unit: str, target_unit: str) -> Decimal | None:
    """展示粒度 × src/target 换算系数 ÷ 2（主规则 A/B 的半个最小有效位）。

    粒度以 ``src_unit`` 标度解析，换算到 ``target_unit`` 标度后取半；
    文本无明确末位（如 "N/A"）时返回 None。
    """

    granularity = parse_granularity(value_text)
    if granularity is None:
        return None
    factor = unit_scale(src_unit) / unit_scale(target_unit)
    return granularity * factor / _TWO


def coarsest_input_ulp_half(inputs: list[dict], output_unit: str) -> Decimal | None:
    """TIE 类主规则 B：最粗输入展示粒度的半个末位（换算至 output_unit 标度）。

    ``inputs`` 每项可为 InputValue（``{"role", "value"}``）或直接 NumericValue；
    逐个换算后取最大值；全部不可得时返回 None（调用方改用兜底值）。
    """

    coarsest: Decimal | None = None
    for item in inputs or []:
        nv = item.get("value") if isinstance(item, dict) and "role" in item else item
        if not isinstance(nv, dict):
            continue
        text = nv.get("value_text")
        if not isinstance(text, str):
            continue
        try:
            half = half_ulp_in(text, _nv_unit(nv), output_unit)
        except ValueError:
            continue
        if half is None:
            continue
        if coarsest is None or half > coarsest:
            coarsest = half
    return coarsest


# --------------------------------------------------------------------------
# 容差
# --------------------------------------------------------------------------


def tolerance_abs(
    claim_nv: dict,
    family: str,
    coarsest_input_ulp_half: Decimal | None = None,
    fallback: Decimal | None = None,
    target_unit: str | None = None,
) -> Decimal:
    """容差缺省值（绝对值，rel 恒为 null）。

    - family="CMP"/"IND"：主规则 A —— Claim.numeric.value_text 展示粒度半个末位；
      给出 ``target_unit`` 时按 Claim 单位→目标单位标度换算；
    - family="TIE"：主规则 B —— ``coarsest_input_ulp_half``（最粗输入半个末位）；
    - 主规则来源不可得时用 ``fallback``；仍不可得抛 ``ValueError``。

    返回值的标度与调用方所用输出单位一致（``target_unit`` 未给出时即 Claim 单位）。
    """

    if family not in _FAMILIES:
        raise ValueError(f"未知公式族：{family}")

    derived: Decimal | None = None
    if family in ("CMP", "IND"):
        text = claim_nv.get("value_text") if isinstance(claim_nv, dict) else None
        granularity = parse_granularity(text) if isinstance(text, str) else None
        if granularity is not None:
            derived = granularity / _TWO
            if target_unit is not None:
                try:
                    derived = derived * unit_scale(_nv_unit(claim_nv)) / unit_scale(target_unit)
                except ValueError:
                    # 单位不可得时按 Claim 自身标度取值，交由调用方核对口径。
                    pass
    elif coarsest_input_ulp_half is not None:
        derived = to_decimal(coarsest_input_ulp_half)

    if derived is not None:
        return derived
    if fallback is not None:
        return to_decimal(fallback)
    raise ValueError("容差来源不可得：Claim 粒度、输入粒度与兜底值均缺失")


def within(diff: Decimal, abs_tol: Decimal) -> bool:
    """|diff| ≤ abs_tol。"""

    return to_decimal(diff).copy_abs() <= to_decimal(abs_tol)
