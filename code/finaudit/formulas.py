"""16 条确定性公式（公式目录）。

目录与公式规格逐条一致：
- 比较类 FRM-CMP-01 至 05（输出 % / 同输入差额）；
- 指标类 FRM-IND-01 至 07（输出 % / 倍 / 次）；
- 勾稽类 FRM-TIE-01 至 04（输出差额，单位同输入）。

计算行为：
- 输入按 role 取 NumericValue；缺角色、输入缺失、单位不可换算 → 参数校验失败；
- 分母为零 → FormulaError(code="分母为零")，不得输出数值；
- 分母为负 → 照算，符号解释假设由调用方记入 Calculation.assumptions；
- 中间不舍入，output.value 为完整精度 Decimal；
- 口径/期间/实体一致性与约数、区间值禁用属前置校验，由调用方执行。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable

from .numeric import to_decimal, unit_scale

_HUNDRED = Decimal(100)
_TWO = Decimal(2)
_ZERO = Decimal(0)
_RATIO_PLACES = Decimal("0.0001")

SAME_AS_INPUT = "同输入"

_RATIO_UNITS = frozenset({"%", "倍", "次"})


class FormulaError(Exception):
    """公式执行失败：code 供 result_status/error 落账，message 为人类可读说明。"""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message or code
        super().__init__(f"{code}：{self.message}" if message else code)


@dataclass
class Formula:
    """单条目录公式。"""

    formula_id: str
    name: str
    family: str  # CMP | IND | TIE
    roles: list[str]  # 输入角色名（逐条对齐公式目录）
    output_unit: str  # "%"/"倍"/"次"/"同输入"
    # 兜底常数：CMP/IND 按 output 标度；"同输入"族为基本货币单位半个，
    # 且优先用较粗输入粒度（此处 0.5 仅在完全无粒度可依时退化使用）。
    fallback_tol: Decimal
    func: Callable[[dict[str, Decimal]], Decimal]


# --------------------------------------------------------------------------
# 计算助手
# --------------------------------------------------------------------------


def _div(numerator: Decimal, denominator: Decimal, role: str) -> Decimal:
    """做除法；分母为零按计算失败口径拒绝（分母为负照算）。"""

    if denominator == _ZERO:
        # FormulaError 会自行拼上 code 前缀，此处只传角色名，避免出现「分母为零：分母为零：x」
        raise FormulaError("分母为零", role)
    return numerator / denominator


def _growth(report_role: str, base_role: str) -> Callable[[dict[str, Decimal]], Decimal]:
    """（报告期 − 基期）÷ 基期 × 100%，输入已换算到同一标度。"""

    def func(values: dict[str, Decimal]) -> Decimal:
        base = values[base_role]
        return _div(values[report_role] - base, base, base_role) * _HUNDRED

    return func


def _f_cmp_05(values: dict[str, Decimal]) -> Decimal:
    return values["报告期值"] - values["基期值"]


def _f_ind_01(values: dict[str, Decimal]) -> Decimal:
    revenue = values["营业收入"]
    return _div(revenue - values["营业成本"], revenue, "营业收入") * _HUNDRED


def _f_ind_02(values: dict[str, Decimal]) -> Decimal:
    return _div(values["净利润"], values["营业收入"], "营业收入") * _HUNDRED


def _f_ind_03(values: dict[str, Decimal]) -> Decimal:
    return _div(values["负债合计"], values["资产合计"], "资产合计") * _HUNDRED


def _f_ind_04(values: dict[str, Decimal]) -> Decimal:
    return _div(values["流动资产"], values["流动负债"], "流动负债")


def _f_ind_05(values: dict[str, Decimal]) -> Decimal:
    average = (values["期初存货"] + values["期末存货"]) / _TWO
    return _div(values["营业成本"], average, "平均存货")


def _f_ind_06(values: dict[str, Decimal]) -> Decimal:
    average = (values["期初应收账款"] + values["期末应收账款"]) / _TWO
    return _div(values["营业收入"], average, "平均应收账款")


def _f_ind_07(values: dict[str, Decimal]) -> Decimal:
    return _div(values["净利润"], values["期末净资产"], "期末净资产") * _HUNDRED


def _f_tie_01(values: dict[str, Decimal]) -> Decimal:
    parts = [amount for role, amount in values.items() if role.startswith("分项")]
    return sum(parts, _ZERO) - values["合计行"]


def _f_tie_02(values: dict[str, Decimal]) -> Decimal:
    return values["资产合计"] - (values["负债合计"] + values["所有者权益合计"])


def _f_tie_03(values: dict[str, Decimal]) -> Decimal:
    return (
        values["期初值"]
        + values["本期增加"]
        - values["本期减少"]
        - values["期末值"]
    )


def _f_tie_04(values: dict[str, Decimal]) -> Decimal:
    return (
        values["期末未分配利润"]
        - values["期初未分配利润"]
        - values["净利润"]
        + values["提取盈余公积"]
        + values["向股东分配利润"]
    )


# --------------------------------------------------------------------------
# 目录
# --------------------------------------------------------------------------

FORMULAS: dict[str, Formula] = {}


def _register(formula: Formula) -> None:
    FORMULAS[formula.formula_id] = formula


_register(Formula("FRM-CMP-01", "同比增速", "CMP", ["报告期值", "基期值"], "%",
                  Decimal("0.5"), _growth("报告期值", "基期值")))
_register(Formula("FRM-CMP-02", "环比增速", "CMP", ["本期值", "上期值"], "%",
                  Decimal("0.5"), _growth("本期值", "上期值")))
_register(Formula("FRM-CMP-03", "较年初增速", "CMP", ["本期末值", "年初值"], "%",
                  Decimal("0.5"), _growth("本期末值", "年初值")))
_register(Formula("FRM-CMP-04", "较上期末变动率", "CMP", ["本期末值", "上期末值"], "%",
                  Decimal("0.5"), _growth("本期末值", "上期末值")))
_register(Formula("FRM-CMP-05", "比较增减额", "CMP", ["报告期值", "基期值"], SAME_AS_INPUT,
                  Decimal("0.5"), _f_cmp_05))

_register(Formula("FRM-IND-01", "毛利率", "IND", ["营业收入", "营业成本"], "%",
                  Decimal("0.5"), _f_ind_01))
_register(Formula("FRM-IND-02", "净利率", "IND", ["净利润", "营业收入"], "%",
                  Decimal("0.5"), _f_ind_02))
_register(Formula("FRM-IND-03", "资产负债率", "IND", ["负债合计", "资产合计"], "%",
                  Decimal("0.5"), _f_ind_03))
_register(Formula("FRM-IND-04", "流动比率", "IND", ["流动资产", "流动负债"], "倍",
                  Decimal("0.005"), _f_ind_04))
_register(Formula("FRM-IND-05", "存货周转率", "IND",
                  ["营业成本", "期初存货", "期末存货"], "次",
                  Decimal("0.005"), _f_ind_05))
_register(Formula("FRM-IND-06", "应收账款周转率", "IND",
                  ["营业收入", "期初应收账款", "期末应收账款"], "次",
                  Decimal("0.005"), _f_ind_06))
_register(Formula("FRM-IND-07", "期末净资产收益率", "IND", ["净利润", "期末净资产"], "%",
                  Decimal("0.5"), _f_ind_07))

_register(Formula("FRM-TIE-01", "分项合计勾稽", "TIE", ["分项1", "分项2", "合计行"],
                  SAME_AS_INPUT, Decimal("0.5"), _f_tie_01))
_register(Formula("FRM-TIE-02", "资产负债表基本等式", "TIE",
                  ["资产合计", "负债合计", "所有者权益合计"], SAME_AS_INPUT,
                  Decimal("0.5"), _f_tie_02))
_register(Formula("FRM-TIE-03", "期初期末勾稽", "TIE",
                  ["期初值", "本期增加", "本期减少", "期末值"], SAME_AS_INPUT,
                  Decimal("0.5"), _f_tie_03))
_register(Formula("FRM-TIE-04", "跨表权益勾稽", "TIE",
                  ["期末未分配利润", "期初未分配利润", "净利润", "提取盈余公积",
                   "向股东分配利润"], SAME_AS_INPUT, Decimal("0.5"), _f_tie_04))


# --------------------------------------------------------------------------
# 呈现格式（仅影响 value_text；value 保留完整精度）
# --------------------------------------------------------------------------


def _format_ratio(value: Decimal) -> str:
    """比率/倍数/次数：4 位小数呈现，至少保留 2 位（如 10.00、1.60、4.1301）。"""

    quantized = value.quantize(_RATIO_PLACES, rounding=ROUND_HALF_UP)
    text = format(quantized, "f")
    integer, _, fraction = text.partition(".")
    fraction = fraction.rstrip("0")
    if len(fraction) < 2:
        fraction = fraction.ljust(2, "0")
    return f"{integer}.{fraction}"


def _format_amount(value: Decimal) -> str:
    """金额/计数：完整精度、千分位分组、去掉无意义末尾零。"""

    if value == _ZERO:
        return "0"
    text = format(value, "f")
    sign = ""
    if text.startswith("-"):
        sign, text = "-", text[1:]
    integer, _, fraction = text.partition(".")
    fraction = fraction.rstrip("0")
    grouped = f"{int(integer):,}"
    return f"{sign}{grouped}.{fraction}" if fraction else f"{sign}{grouped}"


# --------------------------------------------------------------------------
# 执行入口
# --------------------------------------------------------------------------


def _unit_code(nv: dict) -> str:
    unit = nv.get("unit")
    if not isinstance(unit, dict):
        raise ValueError("输入单位缺失")
    code = unit.get("code")
    if not isinstance(code, str) or not code:
        raise ValueError("输入单位代码缺失")
    return code


def _value(nv: dict) -> Decimal:
    if nv.get("value") is None:
        raise ValueError("输入数值缺失（value 为空）")
    return to_decimal(nv["value"])


def compute(
    formula_id: str,
    inputs: list[dict],
    params: dict | None = None,
) -> dict:
    """执行目录公式，成功返回 OutputValue（六字段），失败抛 FormulaError。

    ``inputs`` 元素形如 ``{"role": "...", "value": NumericValue}``。
    ``params`` 可选覆盖输出元数据：entity / period / basis / unit_code。

    返回：``{value, value_text, entity, unit:{code}, period, basis}``；
    ``value`` 为 Decimal，调用方在 json.dumps 前自行转 str。勾稽类返回差额
    （左式 − 右式），复算类返回公式值。
    """

    formula = FORMULAS.get(formula_id)
    if formula is None:
        raise FormulaError("参数校验失败", f"目录外 formula_id：{formula_id}")
    if not isinstance(inputs, list) or not inputs:
        raise FormulaError("参数校验失败", "inputs 不得为空")
    if params is None:
        params = {}
    elif not isinstance(params, dict):
        raise FormulaError("参数校验失败", "params 须为对象")

    # 1) 按 role 收齐输入
    values: dict[str, dict] = {}
    for item in inputs:
        if not isinstance(item, dict):
            raise FormulaError("参数校验失败", "输入项须为 {role, value} 对象")
        role = item.get("role")
        numeric_value = item.get("value")
        if not isinstance(role, str) or not role:
            raise FormulaError("参数校验失败", "输入角色缺失")
        if not isinstance(numeric_value, dict):
            raise FormulaError("参数校验失败", f"输入角色 {role} 的 NumericValue 缺失")
        if role in values:
            raise FormulaError("参数校验失败", f"输入角色重复：{role}")
        values[role] = numeric_value

    # 2) 角色齐备校验（TIE-01 分项为动态角色，至少 2 项）
    if formula.formula_id == "FRM-TIE-01":
        part_roles = [role for role in values if role.startswith("分项")]
        if len(part_roles) < 2:
            raise FormulaError("参数校验失败", "分项输入少于 2 项")
        required_roles = ["合计行"]
    else:
        required_roles = formula.roles
    for role in required_roles:
        if role not in values:
            raise FormulaError("参数校验失败", f"缺少输入角色：{role}")

    first_role = next((role for role in formula.roles if role in values), None)
    if first_role is None:  # TIE-01 只给了 分项3/分项4
        first_role = next(iter(values))
    first_nv = values[first_role]

    # 3) 确定输出单位并换算输入标度
    try:
        if formula.output_unit == SAME_AS_INPUT:
            output_unit = params.get("unit_code") or _unit_code(first_nv)
        else:
            output_unit = params.get("unit_code") or formula.output_unit
        target_scale = unit_scale(output_unit)

        if formula.output_unit == SAME_AS_INPUT:
            # 差额类：所有输入换算到输出单位标度
            converted = {
                role: _value(nv) * unit_scale(_unit_code(nv)) / target_scale
                for role, nv in values.items()
            }
        else:
            # 比率类：先换算到基本货币单位（比值与标度无关，% 与计数档位为 1）
            converted = {
                role: _value(nv) * unit_scale(_unit_code(nv))
                for role, nv in values.items()
            }
        result = formula.func(converted)
    except FormulaError:
        raise
    except (ValueError, ArithmeticError) as exc:
        raise FormulaError("参数校验失败", str(exc)) from None

    # 4) 输出元数据（默认取首个输入，允许 params 覆盖）
    entity = params.get("entity", first_nv.get("entity"))
    if not isinstance(entity, str) or not entity:
        raise FormulaError("参数校验失败", "输出实体缺失")
    period = params.get("period", first_nv.get("period"))
    if not isinstance(period, dict) or not period:
        raise FormulaError("参数校验失败", "输出期间缺失")
    basis = params.get("basis", first_nv.get("basis"))
    if not isinstance(basis, dict) or not basis:
        raise FormulaError("参数校验失败", "输出口径缺失")

    display = _format_ratio(result) if output_unit in _RATIO_UNITS else _format_amount(result)

    return {
        "value": result,
        "value_text": f"{display}{output_unit}",
        "entity": entity,
        "unit": {"code": output_unit},
        "period": copy.deepcopy(period),
        "basis": copy.deepcopy(basis),
    }
