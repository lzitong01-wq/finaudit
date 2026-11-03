"""契约校验：S9 的通过数必须来自真实校验，不合格项要让放行变成阻断。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finaudit.report import _schema_checks, write_o1

TASK = {
    "task_id": "TASK-0001",
    "subject_alias": "甲企业",
    "period_scope": {"label": "2024年度"},
    "data_cutoff": "2025-04-30",
    "boundary_statement": "仅比对授权材料",
}


def _item(**vrf_over) -> dict:
    """一条最小可用的核验明细：Claim + VerificationResult 齐全时应当通过校验。"""
    claim = {
        "claim_id": "CLM-0001", "task_id": "TASK-0001",
        "report_material_id": "MAT-0001", "claim_text": "营业收入 100 亿元",
        "claim_type": "数值", "judgment_type": "事实", "unknown_fields": [],
        "status": "通过",
    }
    vrf = {
        "vrf_id": "VRF-0001", "task_id": "TASK-0001", "claim_id": "CLM-0001",
        "status": "通过", "reason": "数值一致", "rule_ids": ["R-NUM-01"],
        "decided_by": "RA5", "evidence_refs": ["CND-0001"],
    }
    vrf.update(vrf_over)
    return {"claim": claim, "current_verification": vrf,
            "calculations": [], "evidence": [], "facts": []}


class TestSchemaChecks(unittest.TestCase):
    def test_good_item_counts_as_passed(self) -> None:
        checks = _schema_checks([_item()])
        self.assertEqual((checks["items"], checks["passed"], checks["error_count"]),
                         (1, 1, 0))
        self.assertEqual(checks["errors"], [])

    def test_illegal_status_is_a_failure(self) -> None:
        # 「已通过」不在八值内：必须被算作不合格项，不能混进通过数
        checks = _schema_checks([_item(status="已通过")])
        self.assertEqual((checks["items"], checks["passed"]), (1, 0))
        self.assertGreater(checks["error_count"], 0)

    def test_missing_required_field_is_a_failure(self) -> None:
        item = _item()
        del item["claim"]["claim_text"]
        checks = _schema_checks([item])
        self.assertEqual(checks["passed"], 0)
        self.assertTrue(any("claim_text" in e for e in checks["errors"]))

    def test_bad_calculation_is_a_failure(self) -> None:
        item = _item()
        item["calculations"] = [{"calc_id": "CALC-0001"}]  # 缺 formula_id 等必填
        checks = _schema_checks([item])
        self.assertEqual(checks["passed"], 0)
        self.assertTrue(any(e.startswith("Calculation.") for e in checks["errors"]))

    def test_failed_calculation_needs_no_output(self) -> None:
        """分母为零等失败记录不得产出数值：契约不能要求它填 output。"""
        item = _item()
        item["calculations"] = [{
            "calc_id": "CALC-0006", "task_id": "TASK-0001", "claim_id": "CLM-0001",
            "formula_id": "FRM-CMP-01", "inputs": [], "tolerance": {},
            "result_status": "参数校验失败", "error": "分母为零：基期值",
            "assumptions": [], "boundaries": "基期值为 0 时不计算增速",
        }]
        checks = _schema_checks([item])
        self.assertEqual(checks["error_count"], 0, checks["errors"])

    def test_successful_calculation_requires_output(self) -> None:
        item = _item()
        item["calculations"] = [{
            "calc_id": "CALC-0001", "task_id": "TASK-0001", "claim_id": "CLM-0001",
            "formula_id": "FRM-CMP-01", "inputs": [], "tolerance": {},
            "result_status": "成功", "assumptions": [], "boundaries": "—",
        }]
        checks = _schema_checks([item])
        self.assertEqual(checks["passed"], 0)
        self.assertTrue(any("output" in e for e in checks["errors"]))


class TestReleaseGate(unittest.TestCase):
    """放行口径：扫描零命中 + 契约校验全通过，缺一即阻断。"""

    def _s9(self, outputs: dict) -> str:
        with tempfile.TemporaryDirectory() as td:
            path = write_o1(Path(td), TASK, [_item()], {}, outputs=outputs)
            text = path.read_text(encoding="utf-8")
            return text[text.index("## S9"):]

    def test_release_when_all_pass(self) -> None:
        s9 = self._s9({"kl_scan_result": 0, "alias_check": 0,
                       "schema_checks": {"items": 1, "passed": 1, "error_count": 0}})
        self.assertIn("**放行**", s9)
        self.assertNotIn("**阻断**", s9)

    def test_block_when_schema_fails(self) -> None:
        s9 = self._s9({"kl_scan_result": 0, "alias_check": 0,
                       "schema_checks": {"items": 1, "passed": 0, "error_count": 1,
                                         "errors": ["Claim.claim_text: 必填缺失"]}})
        self.assertIn("**阻断**", s9)
        self.assertIn("1 处不合契约", s9)
        self.assertIn("Claim.claim_text: 必填缺失", s9)

    def test_block_when_scan_hits(self) -> None:
        s9 = self._s9({"kl_scan_result": 2, "alias_check": 0,
                       "schema_checks": {"items": 1, "passed": 1, "error_count": 0}})
        self.assertIn("**阻断**", s9)


if __name__ == "__main__":
    unittest.main()
