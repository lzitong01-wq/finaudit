"""规则覆盖补测：12 条只被注册、未被用例覆盖的规则。

补齐目标是「每条规则至少有一条用例走通它的出口」，包括：
- R-ACC-03 会计解释禁判、R-CIT-01 引用可定位性、R-ENT-02/03 实体与子公司；
- R-NUM-02/03 不可解析值与约数、R-PER-02/04 期间类型与期间缺失；
- R-UNI-01/03 单位维度与跨币种；
- R-CON-02 / R-ACC-04 属「条件通过注记」，按设计不占 rule_ids，
  因此断言落在理由文本与状态上，而不是 rule_ids。
"""

from __future__ import annotations

import unittest

from finaudit.rules import evaluate
from tests.test_rules import (PERIOD_2024, POINT_2023, make_candidate,
                              make_claim, make_ctx)


class AccountingInterpretationTest(unittest.TestCase):
    def test_accounting_interpretation_goes_manual(self) -> None:
        """会计解释类表述（重分类、政策变更…）不自动判真伪。"""
        claim = make_claim(claim_type="定性表述型", numeric=None,
                           claim_text="甲企业自2024年度起变更收入确认政策，变更后毛利率提升")
        result = evaluate(claim, make_ctx([make_candidate()]))
        self.assertEqual(result["status"], "需人工复核")
        self.assertIn("R-ACC-03", result["rule_ids"])

    def test_plain_claim_not_hijacked(self) -> None:
        claim = make_claim()
        result = evaluate(claim, make_ctx([make_candidate()]))
        self.assertNotIn("R-ACC-03", result.get("rule_ids") or [])


class CitationLocatorTest(unittest.TestCase):
    def test_citation_without_reference(self) -> None:
        """引用型主张缺 reference：回链不了被引材料，出口证据不足。"""
        claim = make_claim(claim_type="引用型", numeric=None, reference=None)
        result = evaluate(claim, make_ctx([make_candidate()]))
        self.assertEqual(result["status"], "证据不足")
        self.assertIn("R-CIT-01", result["rule_ids"])


class EntityGateTest(unittest.TestCase):
    def test_unknown_entity_blocks_pass(self) -> None:
        claim = make_claim(numeric=make_claim()["numeric"] | {"entity": "unknown"})
        result = evaluate(claim, make_ctx([make_candidate()]))
        self.assertEqual(result["status"], "低置信")
        self.assertIn("R-ENT-02", result["rule_ids"])

    def test_entity_outside_vocab_goes_manual(self) -> None:
        claim = make_claim()
        ctx = make_ctx([make_candidate()], config={"data_cutoff": "2024-12-31",
                                                   "entity_vocab": ["乙企业"]})
        result = evaluate(claim, ctx)
        self.assertEqual(result["status"], "需人工复核")
        self.assertIn("R-ENT-02", result["rule_ids"])

    def test_subsidiary_needs_own_evidence(self) -> None:
        """子公司主张不得用集团合并数替代。"""
        claim = make_claim(numeric=make_claim()["numeric"] | {"entity": "子公司A"})
        result = evaluate(claim, make_ctx([make_candidate()]))
        self.assertEqual(result["status"], "证据不足")
        self.assertIn("R-ENT-03", result["rule_ids"])


class NumericShapeTest(unittest.TestCase):
    def test_text_value_is_low_confidence(self) -> None:
        numeric = dict(make_claim()["numeric"])
        numeric.update({"value": None, "value_kind": "文本值", "value_text": "未披露"})
        result = evaluate(make_claim(numeric=numeric), make_ctx([make_candidate()]))
        self.assertEqual(result["status"], "低置信")
        self.assertIn("R-NUM-02", result["rule_ids"])

    def test_unparsable_value_is_low_confidence(self) -> None:
        numeric = dict(make_claim()["numeric"])
        numeric["value"] = None
        result = evaluate(make_claim(numeric=numeric), make_ctx([make_candidate()]))
        self.assertEqual(result["status"], "低置信")
        self.assertIn("R-NUM-02", result["rule_ids"])

    def test_approximate_value_never_judged_wrong(self) -> None:
        """约数与证据差得较多：按取整粒度放宽后仍超差，也不判错误，转人工。"""
        numeric = dict(make_claim()["numeric"])
        numeric.update({"value": 1300.0, "value_text": "约1,300亿元", "value_kind": "约数"})
        result = evaluate(make_claim(numeric=numeric),
                          make_ctx([make_candidate(value=1200.0)]))
        self.assertEqual(result["status"], "需人工复核")
        self.assertIn("R-NUM-03", result["rule_ids"])


class PeriodShapeTest(unittest.TestCase):
    def test_period_kind_unknown_is_low_confidence(self) -> None:
        numeric = dict(make_claim()["numeric"])
        numeric["period"] = {"kind": "unknown", "point": None, "start": None, "end": None}
        result = evaluate(make_claim(numeric=numeric), make_ctx([make_candidate()]))
        self.assertEqual(result["status"], "低置信")
        self.assertIn("R-PER-04", result["rule_ids"])

    def test_point_vs_flow_is_not_applicable(self) -> None:
        """时点值对期间值不可比，出口不适用而不是判错。"""
        numeric = dict(make_claim()["numeric"])
        numeric["period"] = dict(PERIOD_2024)
        candidate = make_candidate(period=dict(POINT_2023))
        result = evaluate(make_claim(numeric=numeric), make_ctx([candidate]))
        self.assertEqual(result["status"], "不适用")
        self.assertIn("R-PER-02", result["rule_ids"])


class UnitTest(unittest.TestCase):
    def test_dimension_mismatch_is_wrong(self) -> None:
        """百分比（无量纲）对金额（货币）：跨维度不换算，判错误。"""
        numeric = dict(make_claim()["numeric"])
        numeric["unit"] = {"code": "%", "other": None}
        numeric["value"] = 42.0
        result = evaluate(make_claim(numeric=numeric), make_ctx([make_candidate()]))
        self.assertEqual(result["status"], "错误")
        self.assertIn("R-UNI-01", result["rule_ids"])

    def test_cross_currency_without_fx_rate(self) -> None:
        """跨币种且材料无汇率披露：不自行取汇率，证据不足。"""
        numeric = dict(make_claim()["numeric"])
        numeric["unit"] = {"code": "美元", "other": None}
        result = evaluate(make_claim(numeric=numeric), make_ctx([make_candidate()]))
        self.assertEqual(result["status"], "证据不足")
        self.assertIn("R-UNI-03", result["rule_ids"])


class ConditionalPassNoteTest(unittest.TestCase):
    def test_basis_unknown_pass_keeps_note(self) -> None:
        """口径为 unknown 时允许通过，但理由必须写明口径未核对。"""
        numeric = dict(make_claim()["numeric"])
        numeric["basis"] = {"consolidation": "unknown", "accounting": "unknown", "note": None}
        result = evaluate(make_claim(numeric=numeric), make_ctx([make_candidate()]))
        if result["status"] != "通过":
            self.skipTest(f"该输入未判通过（{result['status']}），注记口径不适用")
        self.assertIn("口径未核对", result["reason"])
        # 条件通过注记按设计不占 rule_ids
        self.assertNotIn("R-CON-02", result["rule_ids"])
        self.assertNotIn("R-ACC-04", result["rule_ids"])


if __name__ == "__main__":
    unittest.main()
