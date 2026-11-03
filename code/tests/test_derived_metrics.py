"""派生指标：材料里没有同名行项目，必须由公式复算，而不是判成「证据不足」。

覆盖净利率（FRM-IND-02）、净资产收益率（FRM-IND-07）与流动比率（FRM-IND-04，
材料缺输入时应转人工而不得猜测）。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from finaudit.pipeline import DERIVED_METRICS, _claim_metric, run

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "fixtures" / "demo01"


class TestMetricRouting(unittest.TestCase):
    def test_derived_metrics_recognized(self) -> None:
        cases = {
            "甲企业2024年度销售净利率为10%": "净利率",
            "甲企业2024年度净利率为10%": "净利率",
            "甲企业2024年度净资产收益率为16.1%": "净资产收益率",
            "甲企业2024年度流动比率为1.5倍": "流动比率",
            "甲企业2024年度毛利率为42%": "毛利率",
            "甲企业2024年末资产负债率为60%": "资产负债率",
        }
        for text, expect in cases.items():
            self.assertEqual(_claim_metric({"claim_text": text}), expect, text)

    def test_derived_inputs_are_real_line_items(self) -> None:
        """每个派生指标的输入都必须是材料里能直接读到的行项目。"""
        from finaudit.pipeline import METRIC_ALIASES
        for metric, spec in DERIVED_METRICS.items():
            self.assertTrue(spec["formula"].startswith("FRM-"), metric)
            for name in spec["inputs"]:
                self.assertIn(name, METRIC_ALIASES, f"{metric} 的输入 {name} 不可抽取")

    def test_composite_ratio_still_not_misread(self) -> None:
        # 未接公式的复合指标不得退化成拿基础指标直接比对
        self.assertIsNone(_claim_metric({"claim_text": "甲企业2024年度存货周转率为8次"}))
        self.assertIsNone(_claim_metric({"claim_text": "甲企业2024年度研发人员占比为30%"}))


class TestDerivedEndToEnd(unittest.TestCase):
    """真实夹具跑一遍：可算的判通过/错误，缺输入的转人工，不猜。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        fx = tmp / "fx"
        shutil.copytree(FIX, fx)
        gt = json.loads((fx / "claims_gt.json").read_text(encoding="utf-8"))
        template = gt[1]["claim"]  # 以「毛利率为42%」为模板
        for cid, text, value, vtext, unit in (
            ("CLM-0021", "甲企业2024年度销售净利率为10%", 10, "10%", "%"),
            ("CLM-0022", "甲企业2024年度净资产收益率为16.1%", 16.1, "16.1%", "%"),
            ("CLM-0023", "甲企业2024年度流动比率为1.5倍", 1.5, "1.5倍", "倍"),
        ):
            claim = json.loads(json.dumps(template, ensure_ascii=False))
            claim.update({"claim_id": cid, "claim_text": text,
                          "context_quote": text + "。", "judgment_type": "计算"})
            claim["numeric"].update({"value": value, "value_text": vtext,
                                     "unit": {"code": unit}})
            gt.append({"claim": claim, "gt_status": "待定", "gt_rule_ids": []})
        (fx / "claims_gt.json").write_text(
            json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")

        out = tmp / "out"
        run(fx, out)
        data = json.loads((out / "o2" / "O2-核查明细.json").read_text(encoding="utf-8"))
        cls.items = {it["claim"]["claim_id"]: it for it in data["items"]}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _calc(self, cid: str) -> dict:
        calcs = self.items[cid].get("calculations") or []
        self.assertTrue(calcs, f"{cid} 没有计算记录")
        return calcs[0]

    def test_net_margin_computed_by_formula(self) -> None:
        item = self.items["CLM-0021"]
        self.assertEqual(item["current_verification"]["status"], "通过")
        calc = self._calc("CLM-0021")
        self.assertEqual(calc["formula_id"], "FRM-IND-02")
        self.assertEqual(calc["result_status"], "成功")
        self.assertEqual(calc["output"]["value_text"], "10.00%")

    def test_roe_uses_net_profit_and_ending_equity(self) -> None:
        item = self.items["CLM-0022"]
        self.assertEqual(item["current_verification"]["status"], "通过")
        calc = self._calc("CLM-0022")
        self.assertEqual(calc["formula_id"], "FRM-IND-07")
        self.assertEqual([i["role"] for i in calc["inputs"]], ["净利润", "期末净资产"])
        self.assertEqual(calc["output"]["value_text"], "16.0625%")

    def test_missing_input_goes_to_manual_not_guess(self) -> None:
        """材料里没有流动资产/流动负债：不得凭空造数，转人工复核。"""
        item = self.items["CLM-0023"]
        self.assertEqual(item["current_verification"]["status"], "需人工复核")
        self.assertIn("R-FRM-04", item["current_verification"]["rule_ids"])
        self.assertEqual(self._calc("CLM-0023")["result_status"], "参数校验失败")


if __name__ == "__main__":
    unittest.main()
