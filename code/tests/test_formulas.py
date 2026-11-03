"""公式目录的 21 个独立计算样例 + numeric/formulas 单元测试（硬测试锚点）。"""

from __future__ import annotations

import unittest
from decimal import ROUND_HALF_UP, Decimal

from finaudit.formulas import FORMULAS, FormulaError, compute
from finaudit.numeric import (
    coarsest_input_ulp_half,
    half_ulp_in,
    parse_granularity,
    to_unit,
    tolerance_abs,
    unit_scale,
    within,
)

D = Decimal
ENTITY = "甲企业"
BASIS = {
    "consolidation": "合并报表",
    "accounting": "中国企业会计准则",
    "note": None,
}


def nv(value, text, unit="元", kind="期间", label="2024年度", point=None):
    """构造一条最小可用的 NumericValue。"""

    period = {"kind": kind, "label": label}
    if kind == "时点":
        period["point"] = point or "2024-12-31"
    else:
        period["start"] = "2024-01-01"
        period["end"] = "2024-12-31"
    return {
        "value": D(value) if isinstance(value, str) else value,
        "value_text": text,
        "value_kind": "精确值",
        "entity": ENTITY,
        "unit": {"code": unit},
        "period": period,
        "basis": dict(BASIS),
        "source": {
            "kind": "material",
            "material_id": "MAT-0001",
            "block_id": "BLK-0001",
            "locator": {
                "type": "页码",
                "page": 12,
                "table_ref": None,
                "text_range": None,
                "value": "P12",
            },
            "quote": text,
            "calc_id": None,
        },
    }


def inp(role, value, text, unit="元", **kwargs):
    return {"role": role, "value": nv(value, text, unit, **kwargs)}


def q(value, places="0.0001"):
    return D(value).quantize(D(places), rounding=ROUND_HALF_UP)


class TestParseGranularity(unittest.TestCase):
    def test_exact_decimals(self):
        self.assertEqual(parse_granularity("128,499,687,543.21元"), D("0.01"))
        self.assertEqual(parse_granularity("6.1%"), D("0.1"))
        self.assertEqual(parse_granularity("35.72%"), D("0.01"))
        self.assertEqual(parse_granularity("0.00"), D("0.01"))

    def test_exact_integers(self):
        self.assertEqual(parse_granularity("12%"), D("1"))
        self.assertEqual(parse_granularity("1,234"), D("1"))
        self.assertEqual(parse_granularity("100"), D("1"))

    def test_approximate(self):
        self.assertEqual(parse_granularity("约1,200亿元"), D("100"))
        self.assertEqual(parse_granularity("约1,285亿元"), D("1"))
        self.assertEqual(parse_granularity("约12.5%"), D("0.1"))
        self.assertEqual(parse_granularity("约0.05"), D("0.01"))

    def test_unparsable(self):
        self.assertIsNone(parse_granularity("N/A"))
        self.assertIsNone(parse_granularity("不适用"))
        self.assertIsNone(parse_granularity(""))
        self.assertIsNone(parse_granularity(None))  # type: ignore[arg-type]


class TestUnitScale(unittest.TestCase):
    def test_currency_scales(self):
        self.assertEqual(unit_scale("元"), D(1))
        self.assertEqual(unit_scale("千元"), D(1000))
        self.assertEqual(unit_scale("万元"), D(10000))
        self.assertEqual(unit_scale("百万元"), D(1000000))
        self.assertEqual(unit_scale("亿元"), D(100000000))
        self.assertEqual(unit_scale("十亿元"), D(1000000000))
        self.assertEqual(unit_scale("亿美元"), D(100000000))
        self.assertEqual(unit_scale("万港元"), D(10000))
        self.assertEqual(unit_scale("万日元"), D(10000))

    def test_non_currency_scales(self):
        for code in ("%", "百分点", "倍", "次", "年", "个月", "日", "天", "吨",
                     "平方米", "千瓦时"):
            self.assertEqual(unit_scale(code), D(1))

    def test_unavailable(self):
        for code in ("unknown", "不适用", "OTHER", "光年", ""):
            with self.assertRaises(ValueError):
                unit_scale(code)


class TestConversionAndTolerance(unittest.TestCase):
    def test_to_unit(self):
        self.assertEqual(to_unit(nv("1.2", "1.2亿元", "亿元"), "元"), D("120000000.0"))
        self.assertEqual(to_unit(nv("120000000", "120,000,000元"), "亿元"), D("1.2"))

    def test_half_ulp(self):
        # 约数取最低非零位（100 亿元），精确整数取个位（1 亿元）
        self.assertEqual(half_ulp_in("约1,200亿元", "亿元", "元"), D("5000000000"))
        self.assertEqual(half_ulp_in("1,200亿元", "亿元", "元"), D("50000000"))
        self.assertEqual(half_ulp_in("6.1%", "%", "%"), D("0.05"))
        self.assertEqual(half_ulp_in("137.97亿元", "亿元", "亿元"), D("0.005"))
        self.assertIsNone(half_ulp_in("N/A", "元", "元"))

    def test_coarsest_input(self):
        inputs = [
            inp("分项1", "100.0", "100.0亿元", "亿元"),
            inp("分项2", "28.4997", "28.4997亿元", "亿元"),
            inp("合计行", "128.4997", "128.4997亿元", "亿元"),
        ]
        self.assertEqual(coarsest_input_ulp_half(inputs, "亿元"), D("0.05"))
        self.assertIsNone(coarsest_input_ulp_half([inp("x", "1", "N/A")], "元"))

    def test_tolerance_main_rule(self):
        self.assertEqual(tolerance_abs(nv("12", "12%", "%"), "CMP"), D("0.5"))
        self.assertEqual(tolerance_abs(nv("6.1", "6.1%", "%"), "CMP"), D("0.05"))
        self.assertEqual(tolerance_abs(nv("35.72", "35.72%", "%"), "IND"), D("0.005"))
        self.assertEqual(tolerance_abs(nv("4.13", "4.13次", "次"), "IND"), D("0.005"))

    def test_tolerance_tie_and_fallback(self):
        claim = nv("0", "0", "元")
        self.assertEqual(
            tolerance_abs(claim, "TIE", coarsest_input_ulp_half=D("0.05")), D("0.05")
        )
        self.assertEqual(tolerance_abs(claim, "TIE", fallback=D("0.5")), D("0.5"))
        self.assertEqual(tolerance_abs(nv("0", "N/A", "%"), "CMP", fallback=D("0.5")), D("0.5"))
        with self.assertRaises(ValueError):
            tolerance_abs(nv("0", "N/A", "%"), "CMP")
        with self.assertRaises(ValueError):
            tolerance_abs(claim, "XXX", fallback=D("0.5"))

    def test_within(self):
        self.assertTrue(within(D("0.0288"), D("0.5")))
        self.assertTrue(within(D("-0.5"), D("0.5")))
        self.assertFalse(within(D("0.5001"), D("0.5")))


class TestFormulaCatalog(unittest.TestCase):
    EXPECTED = {
        "FRM-CMP-01": ("同比增速", "CMP", ["报告期值", "基期值"], "%"),
        "FRM-CMP-02": ("环比增速", "CMP", ["本期值", "上期值"], "%"),
        "FRM-CMP-03": ("较年初增速", "CMP", ["本期末值", "年初值"], "%"),
        "FRM-CMP-04": ("较上期末变动率", "CMP", ["本期末值", "上期末值"], "%"),
        "FRM-CMP-05": ("比较增减额", "CMP", ["报告期值", "基期值"], "同输入"),
        "FRM-IND-01": ("毛利率", "IND", ["营业收入", "营业成本"], "%"),
        "FRM-IND-02": ("净利率", "IND", ["净利润", "营业收入"], "%"),
        "FRM-IND-03": ("资产负债率", "IND", ["负债合计", "资产合计"], "%"),
        "FRM-IND-04": ("流动比率", "IND", ["流动资产", "流动负债"], "倍"),
        "FRM-IND-05": ("存货周转率", "IND", ["营业成本", "期初存货", "期末存货"], "次"),
        "FRM-IND-06": (
            "应收账款周转率",
            "IND",
            ["营业收入", "期初应收账款", "期末应收账款"],
            "次",
        ),
        "FRM-IND-07": ("期末净资产收益率", "IND", ["净利润", "期末净资产"], "%"),
        "FRM-TIE-01": ("分项合计勾稽", "TIE", ["分项1", "分项2", "合计行"], "同输入"),
        "FRM-TIE-02": (
            "资产负债表基本等式",
            "TIE",
            ["资产合计", "负债合计", "所有者权益合计"],
            "同输入",
        ),
        "FRM-TIE-03": ("期初期末勾稽", "TIE", ["期初值", "本期增加", "本期减少", "期末值"], "同输入"),
        "FRM-TIE-04": (
            "跨表权益勾稽",
            "TIE",
            ["期末未分配利润", "期初未分配利润", "净利润", "提取盈余公积", "向股东分配利润"],
            "同输入",
        ),
    }

    def test_catalog_complete(self):
        self.assertEqual(set(FORMULAS), set(self.EXPECTED))
        self.assertEqual(len(FORMULAS), 16)
        families = [f.family for f in FORMULAS.values()]
        self.assertEqual(families.count("CMP"), 5)
        self.assertEqual(families.count("IND"), 7)
        self.assertEqual(families.count("TIE"), 4)

    def test_catalog_fields(self):
        for formula_id, (name, family, roles, unit) in self.EXPECTED.items():
            formula = FORMULAS[formula_id]
            self.assertEqual(formula.name, name)
            self.assertEqual(formula.family, family)
            self.assertEqual(list(formula.roles), roles)
            self.assertEqual(formula.output_unit, unit)
            self.assertIsInstance(formula.fallback_tol, Decimal)
            self.assertTrue(callable(formula.func))


class TestComparisonSamples(unittest.TestCase):
    """比较类 CMP-01 至 CMP-05。"""

    def test_cmp_01_year_on_year(self):
        inputs = [
            inp("报告期值", "128499687543.21", "128,499,687,543.21元"),
            inp("基期值", "114702345678.90", "114,702,345,678.90元"),
        ]
        output = compute("FRM-CMP-01", inputs)
        self.assertEqual(output["unit"], {"code": "%"})
        self.assertEqual(q(output["value"]), D("12.0288"))

        claim = nv("12", "12%", "%")
        tolerance = tolerance_abs(claim, "CMP")
        self.assertEqual(tolerance, D("0.5"))
        diff = output["value"] - D("12")
        self.assertEqual(q(diff), D("0.0288"))
        self.assertTrue(within(diff, tolerance))

        claim_high = nv("15", "15%", "%")
        diff_high = output["value"] - D("15")
        self.assertEqual(q(diff_high.copy_abs()), D("2.9712"))
        self.assertFalse(within(diff_high, tolerance_abs(claim_high, "CMP")))

    def test_cmp_02_quarter_on_quarter(self):
        inputs = [
            inp("本期值", "350.0", "350.0亿元", "亿元"),
            inp("上期值", "330.0", "330.0亿元", "亿元"),
        ]
        output = compute("FRM-CMP-02", inputs)
        self.assertEqual(q(output["value"]), D("6.0606"))
        claim = nv("6.1", "6.1%", "%")
        tolerance = tolerance_abs(claim, "CMP")
        self.assertEqual(tolerance, D("0.05"))
        diff = output["value"] - D("6.1")
        self.assertEqual(q(diff.copy_abs()), D("0.0394"))
        self.assertTrue(within(diff, tolerance))

    def test_cmp_03_versus_year_start(self):
        inputs = [
            inp("本期末值", "220.0", "220.0亿元", "亿元", kind="时点"),
            inp("年初值", "200.0", "200.0亿元", "亿元", kind="时点",
                label="2024-01-01", point="2024-01-01"),
        ]
        output = compute("FRM-CMP-03", inputs)
        self.assertEqual(q(output["value"]), D("10.0000"))
        claim = nv("10", "10%", "%")
        tolerance = tolerance_abs(claim, "CMP")
        self.assertEqual(tolerance, D("0.5"))
        self.assertTrue(within(output["value"] - D("10"), tolerance))

    def test_cmp_04_versus_prior_period_end(self):
        inputs = [
            inp("本期末值", "220.0", "220.0亿元", "亿元", kind="时点"),
            inp("上期末值", "215.0", "215.0亿元", "亿元", kind="时点",
                label="2024-09-30", point="2024-09-30"),
        ]
        output = compute("FRM-CMP-04", inputs)
        self.assertEqual(q(output["value"]), D("2.3256"))
        claim = nv("2.3", "2.3%", "%")
        tolerance = tolerance_abs(claim, "CMP")
        self.assertEqual(tolerance, D("0.05"))
        diff = output["value"] - D("2.3")
        self.assertEqual(q(diff), D("0.0256"))
        self.assertTrue(within(diff, tolerance))

    def test_cmp_05_delta_amount(self):
        inputs = [
            inp("报告期值", "128499687543.21", "128,499,687,543.21元"),
            inp("基期值", "114702345678.90", "114,702,345,678.90元"),
        ]
        output = compute("FRM-CMP-05", inputs)
        self.assertEqual(output["unit"]["code"], "元")
        self.assertEqual(output["value"], D("13797341864.31"))

        delta_yi = to_unit(output, "亿元")
        self.assertEqual(q(delta_yi, "0.0000000001"), D("137.9734186431"))
        claim = nv("137.97", "137.97亿元", "亿元")
        tolerance = tolerance_abs(claim, "CMP")
        self.assertEqual(tolerance, D("0.005"))
        diff = delta_yi - D("137.97")
        self.assertEqual(q(diff, "0.0000000001"), D("0.0034186431"))
        self.assertTrue(within(diff, tolerance))


class TestIndicatorSamples(unittest.TestCase):
    """指标类 IND-01 至 IND-07。"""

    def test_ind_01_gross_margin(self):
        inputs = [
            inp("营业收入", "128499687543.21", "128,499,687,543.21元"),
            inp("营业成本", "82601234567.89", "82,601,234,567.89元"),
        ]
        output = compute("FRM-IND-01", inputs)
        self.assertEqual(q(output["value"]), D("35.7187"))

        wrong_claim = nv("42", "42%", "%")
        diff = output["value"] - D("42")
        self.assertEqual(q(diff.copy_abs()), D("6.2813"))
        self.assertFalse(within(diff, tolerance_abs(wrong_claim, "IND")))

        right_claim = nv("35.72", "35.72%", "%")
        tolerance = tolerance_abs(right_claim, "IND")
        self.assertEqual(tolerance, D("0.005"))
        diff_ok = output["value"] - D("35.72")
        self.assertEqual(q(diff_ok.copy_abs()), D("0.0013"))
        self.assertTrue(within(diff_ok, tolerance))

    def test_ind_02_net_margin(self):
        inputs = [
            inp("净利润", "12849968754.32", "12,849,968,754.32元"),
            inp("营业收入", "128499687543.21", "128,499,687,543.21元"),
        ]
        output = compute("FRM-IND-02", inputs)
        self.assertEqual(q(output["value"]), D("10.0000"))
        self.assertTrue(within(output["value"] - D("10"), tolerance_abs(nv("10", "10%", "%"), "IND")))

    def test_ind_03_debt_ratio(self):
        inputs = [
            inp("负债合计", "1200.0", "1,200.0亿元", "亿元", kind="时点"),
            inp("资产合计", "2000.0", "2,000.0亿元", "亿元", kind="时点"),
        ]
        output = compute("FRM-IND-03", inputs)
        self.assertEqual(q(output["value"]), D("60.0000"))
        self.assertTrue(within(output["value"] - D("60"), tolerance_abs(nv("60", "60%", "%"), "IND")))

    def test_ind_04_current_ratio(self):
        inputs = [
            inp("流动资产", "800.0", "800.0亿元", "亿元", kind="时点"),
            inp("流动负债", "500.0", "500.0亿元", "亿元", kind="时点"),
        ]
        output = compute("FRM-IND-04", inputs)
        self.assertEqual(output["unit"]["code"], "倍")
        self.assertEqual(output["value"], D("1.6"))
        claim = nv("1.6", "1.6倍", "倍")
        self.assertEqual(tolerance_abs(claim, "IND"), D("0.05"))
        self.assertTrue(within(output["value"] - D("1.6"), tolerance_abs(claim, "IND")))

    def test_ind_05_inventory_turnover(self):
        inputs = [
            inp("营业成本", "82601234567.89", "82,601,234,567.89元"),
            inp("期初存货", "180.0", "180.0亿元", "亿元", kind="时点"),
            inp("期末存货", "220.0", "220.0亿元", "亿元", kind="时点"),
        ]
        output = compute("FRM-IND-05", inputs)
        self.assertEqual(output["unit"]["code"], "次")
        self.assertEqual(q(output["value"]), D("4.1301"))
        claim = nv("4.13", "4.13次", "次")
        tolerance = tolerance_abs(claim, "IND")
        self.assertEqual(tolerance, D("0.005"))
        self.assertTrue(within(output["value"] - D("4.13"), tolerance))

    def test_ind_06_receivable_turnover(self):
        inputs = [
            inp("营业收入", "128499687543.21", "128,499,687,543.21元"),
            inp("期初应收账款", "90.0", "90.0亿元", "亿元", kind="时点"),
            inp("期末应收账款", "110.0", "110.0亿元", "亿元", kind="时点"),
        ]
        output = compute("FRM-IND-06", inputs)
        self.assertEqual(q(output["value"]), D("12.8500"))
        claim = nv("12.85", "12.85次", "次")
        self.assertEqual(tolerance_abs(claim, "IND"), D("0.005"))
        self.assertTrue(within(output["value"] - D("12.85"), tolerance_abs(claim, "IND")))

    def test_ind_07_period_end_roe(self):
        inputs = [
            inp("净利润", "12849968754.32", "12,849,968,754.32元"),
            inp("期末净资产", "800.0", "800.0亿元", "亿元", kind="时点"),
        ]
        output = compute("FRM-IND-07", inputs)
        self.assertEqual(q(output["value"]), D("16.0625"))
        claim = nv("16.06", "16.06%", "%")
        tolerance = tolerance_abs(claim, "IND")
        self.assertEqual(tolerance, D("0.005"))
        self.assertTrue(within(output["value"] - D("16.06"), tolerance))


class TestTieSamples(unittest.TestCase):
    """勾稽类 TIE-01 至 TIE-04（差额 = 左式 − 右式，与 0 比较）。"""

    def test_tie_01_parts_vs_total(self):
        inputs = [
            inp("分项1", "100.0", "100.0亿元", "亿元"),
            inp("分项2", "28.4997", "28.4997亿元", "亿元"),
            inp("合计行", "128.4997", "128.4997亿元", "亿元"),
        ]
        output = compute("FRM-TIE-01", inputs)
        self.assertEqual(output["unit"]["code"], "亿元")
        self.assertEqual(output["value"], D("0.0000"))
        tolerance = tolerance_abs(None, "TIE", coarsest_input_ulp_half(inputs, "亿元"))
        self.assertEqual(tolerance, D("0.05"))
        self.assertTrue(within(output["value"], tolerance))

        bad_inputs = [
            inp("分项1", "100.0", "100.0亿元", "亿元"),
            inp("分项2", "28.0", "28.0亿元", "亿元"),
            inp("合计行", "128.4997", "128.4997亿元", "亿元"),
        ]
        bad = compute("FRM-TIE-01", bad_inputs)
        self.assertEqual(bad["value"], D("-0.4997"))
        bad_tol = tolerance_abs(None, "TIE", coarsest_input_ulp_half(bad_inputs, "亿元"))
        self.assertEqual(bad_tol, D("0.05"))
        self.assertFalse(within(bad["value"], bad_tol))

    def test_tie_01_three_parts(self):
        inputs = [
            inp("分项1", "100.0", "100.0亿元", "亿元"),
            inp("分项2", "20.0", "20.0亿元", "亿元"),
            inp("分项3", "8.4997", "8.4997亿元", "亿元"),
            inp("合计行", "128.4997", "128.4997亿元", "亿元"),
        ]
        self.assertEqual(compute("FRM-TIE-01", inputs)["value"], D("0"))

    def test_tie_02_balance_sheet_identity(self):
        good = [
            inp("资产合计", "1000.0", "1,000.0亿元", "亿元", kind="时点"),
            inp("负债合计", "600.0", "600.0亿元", "亿元", kind="时点"),
            inp("所有者权益合计", "400.0", "400.0亿元", "亿元", kind="时点"),
        ]
        output = compute("FRM-TIE-02", good)
        self.assertEqual(output["value"], D("0"))

        bad = [
            inp("资产合计", "100.0", "100.0亿元", "亿元", kind="时点"),
            inp("负债合计", "60.0", "60.0亿元", "亿元", kind="时点"),
            inp("所有者权益合计", "35.0", "35.0亿元", "亿元", kind="时点"),
        ]
        bad_output = compute("FRM-TIE-02", bad)
        self.assertEqual(bad_output["value"], D("5.0"))
        bad_tol = tolerance_abs(None, "TIE", coarsest_input_ulp_half(bad, "亿元"))
        self.assertEqual(bad_tol, D("0.05"))
        self.assertFalse(within(bad_output["value"], bad_tol))

    def test_tie_03_opening_closing(self):
        good = [
            inp("期初值", "200.0", "200.0亿元", "亿元", kind="时点"),
            inp("本期增加", "500.0", "500.0亿元", "亿元"),
            inp("本期减少", "480.0", "480.0亿元", "亿元"),
            inp("期末值", "220.0", "220.0亿元", "亿元", kind="时点"),
        ]
        output = compute("FRM-TIE-03", good)
        self.assertEqual(output["value"], D("0"))
        self.assertTrue(within(output["value"], tolerance_abs(
            None, "TIE", coarsest_input_ulp_half(good, "亿元"))))

        bad = list(good)
        bad[3] = inp("期末值", "230.0", "230.0亿元", "亿元", kind="时点")
        bad_output = compute("FRM-TIE-03", bad)
        self.assertEqual(bad_output["value"], D("-10.0"))
        bad_tol = tolerance_abs(None, "TIE", coarsest_input_ulp_half(bad, "亿元"))
        self.assertEqual(bad_tol, D("0.05"))
        self.assertFalse(within(bad_output["value"], bad_tol))

    def test_tie_04_cross_statement_equity(self):
        inputs = [
            inp("期末未分配利润", "385.6", "385.6亿元", "亿元", kind="时点"),
            inp("期初未分配利润", "300.0", "300.0亿元", "亿元", kind="时点",
                label="2024-01-01", point="2024-01-01"),
            inp("净利润", "128.5", "128.5亿元", "亿元"),
            inp("提取盈余公积", "12.9", "12.9亿元", "亿元"),
            inp("向股东分配利润", "30.0", "30.0亿元", "亿元"),
        ]
        output = compute("FRM-TIE-04", inputs)
        self.assertEqual(output["value"], D("0.0"))
        tolerance = tolerance_abs(None, "TIE", coarsest_input_ulp_half(inputs, "亿元"))
        self.assertEqual(tolerance, D("0.05"))
        self.assertTrue(within(output["value"], tolerance))


class TestFormulaErrors(unittest.TestCase):
    def test_unknown_formula_id(self):
        with self.assertRaises(FormulaError) as ctx:
            compute("FRM-CMP-99", [inp("报告期值", "1", "1元")])
        self.assertEqual(ctx.exception.code, "参数校验失败")
        self.assertIn("FRM-CMP-99", ctx.exception.message)

    def test_missing_role(self):
        with self.assertRaises(FormulaError) as ctx:
            compute("FRM-CMP-01", [inp("报告期值", "100", "100元")])
        self.assertEqual(ctx.exception.code, "参数校验失败")
        self.assertIn("基期值", ctx.exception.message)

    def test_missing_tie_item_not_zero_filled(self):
        inputs = [
            inp("期末未分配利润", "385.6", "385.6亿元", "亿元"),
            inp("期初未分配利润", "300.0", "300.0亿元", "亿元"),
            inp("净利润", "128.5", "128.5亿元", "亿元"),
            inp("向股东分配利润", "30.0", "30.0亿元", "亿元"),
        ]
        with self.assertRaises(FormulaError) as ctx:
            compute("FRM-TIE-04", inputs)
        self.assertEqual(ctx.exception.code, "参数校验失败")
        self.assertIn("提取盈余公积", ctx.exception.message)

    def test_zero_denominator(self):
        with self.assertRaises(FormulaError) as ctx:
            compute("FRM-CMP-01", [
                inp("报告期值", "100.0", "100.0亿元", "亿元"),
                inp("基期值", "0.0", "0.0亿元", "亿元"),
            ])
        self.assertEqual(ctx.exception.code, "分母为零")
        self.assertIn("基期值", ctx.exception.message)

    def test_zero_average_inventory(self):
        with self.assertRaises(FormulaError) as ctx:
            compute("FRM-IND-05", [
                inp("营业成本", "82601234567.89", "82,601,234,567.89元"),
                inp("期初存货", "0.0", "0.0亿元", "亿元"),
                inp("期末存货", "0.0", "0.0亿元", "亿元"),
            ])
        self.assertEqual(ctx.exception.code, "分母为零")
        self.assertIn("平均存货", ctx.exception.message)

    def test_negative_denominator_computes(self):
        """分母为负照算（符号解释由调用方转人工）。"""

        output = compute("FRM-CMP-01", [
            inp("报告期值", "50.0", "50.0亿元", "亿元"),
            inp("基期值", "-100.0", "-100.0亿元", "亿元"),
        ])
        self.assertEqual(q(output["value"]), D("-150.0000"))

    def test_zero_numerator_is_valid(self):
        output = compute("FRM-IND-02", [
            inp("净利润", "0.0", "0.0亿元", "亿元"),
            inp("营业收入", "100.0", "100.0亿元", "亿元"),
        ])
        self.assertEqual(output["value"], D("0"))

    def test_unknown_unit_rejected(self):
        with self.assertRaises(FormulaError) as ctx:
            compute("FRM-IND-01", [
                inp("营业收入", "100.0", "100.0", "unknown"),
                inp("营业成本", "60.0", "60.0", "unknown"),
            ])
        self.assertEqual(ctx.exception.code, "参数校验失败")

    def test_params_override_metadata(self):
        output = compute(
            "FRM-IND-03",
            [
                inp("负债合计", "1200.0", "1,200.0亿元", "亿元"),
                inp("资产合计", "2000.0", "2,000.0亿元", "亿元"),
            ],
            params={"entity": "甲企业（合并）", "value_text": "ignored"},
        )
        self.assertEqual(output["entity"], "甲企业（合并）")
        self.assertEqual(set(output), {"value", "value_text", "entity", "unit", "period", "basis"})
        self.assertIn("%", output["value_text"])


if __name__ == "__main__":
    unittest.main()
