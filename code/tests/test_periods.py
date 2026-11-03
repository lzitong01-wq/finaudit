"""期间规范化、证据解析和动态计算路由回归。"""

from __future__ import annotations

import unittest
from decimal import Decimal

from finaudit.ids import IdAllocator
from finaudit.extract import _parse_numeric
from finaudit.periods import (parse_period_label, period_for_metric, period_key,
                              previous_year_period, prior_year_end)
from finaudit.pipeline import (POINT_METRICS, _claim_metric, _period_key_from_cell,
                               build_calculation, parse_material)
from finaudit.pdfparse import _period_of


class TestPeriodNormalization(unittest.TestCase):
    def test_period_labels_have_canonical_keys(self) -> None:
        cases = {
            "2025年度": "2025",
            "2025Q3": "2025Q3",
            "2025年第三季度": "2025Q3",
            "2025年上半年": "2025H1",
            "2025年前三季度": "2025-01-01/2025-09-30",
            "2025年1-9月": "2025-01-01/2025-09-30",
            "2025年1月至9月": "2025-01-01/2025-09-30",
            "2025-09-30": "2025-09-30",
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(period_key(parse_period_label(label)), expected)

    def test_comparison_periods_are_period_aware(self) -> None:
        q3 = parse_period_label("2025Q3")
        prior_flow = previous_year_period(q3)
        self.assertEqual(period_key(prior_flow), "2024Q3")

        q3_point = period_for_metric(q3, "资产合计", POINT_METRICS)
        self.assertEqual(period_key(q3_point), "2025-09-30")
        self.assertEqual(period_key(prior_year_end(q3_point)), "2024-12-31")

    def test_year_start_comparison_keeps_current_period_end(self) -> None:
        numeric = _parse_numeric("甲企业2025Q3存货较年初增长20%",
                                 default_period=parse_period_label("2025Q3"))
        self.assertEqual(numeric["period"]["point"], "2025-09-30")

    def test_year_end_flow_metric_remains_a_duration(self) -> None:
        numeric = _parse_numeric("甲企业2025年末毛利率为35%",
                                 default_period=parse_period_label("2025年度"))
        self.assertEqual(numeric["period"]["kind"], "期间")
        self.assertEqual(period_key(numeric["period"]), "2025")

    def test_ytd_claim_period_is_not_collapsed_to_single_quarter(self) -> None:
        scope = parse_period_label("2025Q3")
        numeric = _parse_numeric("甲企业2025年前三季度营业收入为100亿元",
                                 default_period=scope)
        self.assertEqual(period_key(numeric["period"]),
                         "2025-01-01/2025-09-30")

    def test_composite_claim_is_not_matched_to_base_metric(self) -> None:
        claim = {"claim_text": "甲企业海外营业收入占比为30%"}
        self.assertIsNone(_claim_metric(claim))

    def test_material_parser_uses_task_period_and_metric_semantics(self) -> None:
        scope = parse_period_label("2025Q3")
        text = """<!-- page 1 -->
单位：亿元
| 指标 | 本期 |
| --- | --- |
| 营业收入 | 100.0 |
| 资产合计 | 200.0 |
"""
        _, rows = parse_material("MAT-0002", text, IdAllocator(), [],
                                 period_scope=scope)
        by_metric = {row["metric"]: row for row in rows}
        self.assertEqual(by_metric["营业收入"]["period_key"], "2025Q3")
        self.assertEqual(by_metric["资产合计"]["period_key"], "2025-09-30")

    def test_table_period_headers_override_task_scope(self) -> None:
        text = """<!-- page 1 -->
单位：亿元
| 指标 | 2025Q3 | 2024Q3 |
| --- | --- | --- |
| 营业收入 | 100.0 | 90.0 |
| 资产合计 | 200.0 | 180.0 |
"""
        _, rows = parse_material("MAT-0002", text, IdAllocator(), [],
                                 period_scope=parse_period_label("2025年度"))
        revenues = [row for row in rows if row["metric"] == "营业收入"]
        assets = [row for row in rows if row["metric"] == "资产合计"]
        self.assertEqual([row["period_key"] for row in revenues], ["2025Q3", "2024Q3"])
        self.assertEqual([row["period_key"] for row in assets],
                         ["2025-09-30", "2024-09-30"])

    def test_relative_table_headers_map_current_and_comparison_periods(self) -> None:
        text = """<!-- page 1 -->
单位：亿元
| 指标 | 本期金额/期末余额 | 上期金额/期初余额 |
| --- | --- | --- |
| 营业收入 | 100.0 | 80.0 |
| 资产合计 | 200.0 | 150.0 |
"""
        _, rows = parse_material("MAT-0002", text, IdAllocator(), [],
                                 period_scope=parse_period_label("2025Q3"))
        revenues = [row for row in rows if row["metric"] == "营业收入"]
        assets = [row for row in rows if row["metric"] == "资产合计"]
        self.assertEqual([row["period_key"] for row in revenues], ["2025Q3", "2024Q3"])
        self.assertEqual([row["period_key"] for row in assets],
                         ["2025-09-30", "2024-12-31"])

    def test_missing_period_is_not_replaced_by_a_fixed_year(self) -> None:
        text = """<!-- page 1 -->
单位：亿元
| 指标 | 本期 |
| --- | --- |
| 营业收入 | 100.0 |
"""
        _, rows = parse_material("MAT-0002", text, IdAllocator(), [])
        self.assertIsNone(rows[0]["period_key"])
        self.assertIsNone(_period_key_from_cell("本期"))

    def test_pdf_columns_follow_task_scope_without_default_year(self) -> None:
        scope = parse_period_label("2025Q3")
        self.assertEqual(_period_of("营业收入", scope, 0)[0], "2025Q3")
        self.assertEqual(_period_of("营业收入", scope, 1)[0], "2024Q3")
        self.assertEqual(_period_of("存货", scope, 0)[0], "2025-09-30")
        self.assertEqual(_period_of("存货", scope, 1)[0], "2024-12-31")
        self.assertIsNone(_period_of("营业收入", None, 0)[0])

    def test_calculation_uses_claim_quarter_and_comparable_quarter(self) -> None:
        scope = parse_period_label("2025Q3")
        rows = []
        for metric, value in (("营业收入", "100.0"), ("营业成本", "64.28")):
            rows.append({
                "metric": metric, "metric_label": metric,
                "period_key": "2025Q3", "period": scope,
                "value": Decimal(value), "value_text": f"{value}亿元",
                "unit_code": "亿元", "material_id": "MAT-0002",
                "page": 1, "block_id": "BLK-0001", "table_ref": "P1-表1",
                "quote": f"{metric} {value}亿元", "ocr_band": "不适用",
            })
        claim = {
            "task_id": "TSK-0001", "claim_id": "CLM-0001",
            "report_material_id": "MAT-0001",
            "claim_text": "甲企业2025Q3毛利率35.72%",
            "metric": "毛利率",
            "numeric": {"value": Decimal("35.72"), "value_text": "35.72%",
                        "unit": {"code": "%"}, "period": scope},
        }
        calculation, error = build_calculation(
            claim, rows, IdAllocator(), report_id="MAT-0001",
            ranks={"MAT-0001": 9, "MAT-0002": 1})
        self.assertIsNone(error)
        self.assertEqual(calculation["formula_id"], "FRM-IND-01")
        self.assertEqual([item["value"]["period"]["label"]
                          for item in calculation["inputs"]], ["2025Q3", "2025Q3"])

    def test_yoy_calculation_uses_prior_comparable_quarter(self) -> None:
        scope = parse_period_label("2025Q3")
        rows = []
        for year_key, value in (("2025Q3", "100.0"), ("2024Q3", "80.0")):
            period = parse_period_label(year_key)
            rows.append({
                "metric": "营业收入", "metric_label": "营业收入",
                "period_key": year_key, "period": period,
                "value": Decimal(value), "value_text": f"{value}亿元",
                "unit_code": "亿元", "material_id": "MAT-0002",
                "page": 1, "block_id": "BLK-0001", "table_ref": "P1-表1",
                "quote": f"营业收入 {value}亿元", "ocr_band": "不适用",
            })
        claim = {
            "task_id": "TSK-0001", "claim_id": "CLM-0002",
            "report_material_id": "MAT-0001",
            "claim_text": "甲企业2025Q3营业收入同比增长25%",
            "metric": "营业收入",
            "numeric": {"value": Decimal("25"), "value_text": "25%",
                        "unit": {"code": "%"}, "period": scope},
        }
        calculation, error = build_calculation(
            claim, rows, IdAllocator(), report_id="MAT-0001",
            ranks={"MAT-0001": 9, "MAT-0002": 1})
        self.assertIsNone(error)
        self.assertEqual(calculation["formula_id"], "FRM-CMP-01")
        self.assertEqual([item["value"]["period"]["label"]
                          for item in calculation["inputs"]], ["2025Q3", "2024Q3"])

    def test_inventory_yoy_uses_current_quarter_end_and_prior_year_end(self) -> None:
        rows = []
        for point_key, value in (("2025-09-30", "120.0"), ("2024-12-31", "100.0")):
            point_period = parse_period_label(point_key)
            rows.append({
                "metric": "存货", "metric_label": "存货",
                "period_key": point_key, "period": point_period,
                "value": Decimal(value), "value_text": f"{value}亿元",
                "unit_code": "亿元", "material_id": "MAT-0002",
                "page": 1, "block_id": "BLK-0001", "table_ref": "P1-表1",
                "quote": f"存货 {value}亿元", "ocr_band": "不适用",
            })
        numeric = _parse_numeric("甲企业2025Q3存货较年初增长20%",
                                 default_period=parse_period_label("2025Q3"))
        claim = {
            "task_id": "TSK-0001", "claim_id": "CLM-0003",
            "report_material_id": "MAT-0001",
            "claim_text": "甲企业2025Q3存货较年初增长20%",
            "metric": "存货", "numeric": numeric,
        }
        calculation, error = build_calculation(
            claim, rows, IdAllocator(), report_id="MAT-0001",
            ranks={"MAT-0001": 9, "MAT-0002": 1})
        self.assertIsNone(error)
        self.assertEqual(calculation["formula_id"], "FRM-CMP-03")
        self.assertEqual([item["value"]["period"]["point"]
                          for item in calculation["inputs"]],
                         ["2025-09-30", "2024-12-31"])


if __name__ == "__main__":
    unittest.main()
