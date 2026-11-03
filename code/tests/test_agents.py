"""Agent 框架测试：角色权限边界、交接契约、越权拦截（Agent 运行时与安全边界）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finaudit.agents import (Ctx, HANDOFFS, ROLES, PermissionDenied,
                             assert_read, assert_write, roster, verify_handoff)
from finaudit.pipeline import run

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "fixtures" / "demo01"


class TestAgentFramework(unittest.TestCase):
    def test_roster_complete(self) -> None:
        codes = set(ROLES)
        self.assertTrue({"RA1", "RA2", "RA3", "RA4", "RA5", "RA6", "RA7", "RA8",
                         "HUMAN", "EXPORT"} <= codes)
        self.assertEqual(len(roster()), len(ROLES))

    def test_status_single_writer(self) -> None:
        """状态字段唯一写点是 RA5。"""
        writers = [c for c, r in ROLES.items() if "status" in r.writes]
        self.assertEqual(writers, ["RA5"])

    def test_auditor_cannot_write_status(self) -> None:
        """RA7 只读：不得写状态（越权拦截）。"""
        with self.assertRaises(PermissionDenied):
            assert_write("RA7", "status")

    def test_parser_cannot_write_claims(self) -> None:
        with self.assertRaises(PermissionDenied):
            assert_write("RA2", "claims")

    def test_human_cannot_write_delivery(self) -> None:
        """人工复核员只写复核记录，不写交付区。"""
        with self.assertRaises(PermissionDenied):
            assert_write("HUMAN", "delivery")

    def test_ctx_enforces_permission(self) -> None:
        ctx = Ctx(task_id="TSK-X")
        ctx.put("RA4", "claims", "claims", [{"claim_id": "CLM-0001"}])
        self.assertEqual(len(ctx.get("RA5", "claims")), 1)   # RA5 可读主张
        with self.assertRaises(PermissionDenied):
            ctx.get("RA2", "status")                          # RA2 不可读状态

    def test_handoff_verification(self) -> None:
        self.assertEqual(verify_handoff("H5", {"claim_id": "C1", "claim_text": "x"}), [])
        missing = verify_handoff("H5", {"claim_id": "C1"})
        self.assertEqual(missing, ["claim_text"])
        self.assertTrue(verify_handoff("H99", {}))

    def test_handoffs_defined(self) -> None:
        self.assertTrue({"H1", "H5", "H8", "H10"} <= set(HANDOFFS))

    def test_unknown_role(self) -> None:
        with self.assertRaises(PermissionDenied):
            assert_write("RA9", "blocks")
        with self.assertRaises(PermissionDenied):
            assert_read("RA9", "blocks")

    def test_zero_is_present_not_missing(self) -> None:
        """计数值 0 是有效值，不得判为缺项。"""
        self.assertEqual(verify_handoff("H9", {"audit_id": "AUD-0001",
                                              "复核范围": "全量", "发现": 0}), [])
        self.assertEqual(verify_handoff("H9", {"audit_id": "AUD-0001",
                                              "复核范围": "全量", "发现": None}), ["发现"])


class TestAgentsReallyRun(unittest.TestCase):
    """各 Agent 必须真实出现在运行事件流里，且交接无缺项。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "run"
        run(FIX, cls.out)
        cls.evs = [json.loads(line) for line in
                   (cls.out / "o3" / "C-events.jsonl").read_text(
                       encoding="utf-8").splitlines() if line]

    def test_every_agent_has_events(self) -> None:
        actors = {e["actor"]["id"] for e in self.evs}
        for code in ("RA1", "RA2", "RA3", "RA4", "RA5", "RA6", "RA7"):
            self.assertIn(code, actors, f"{code} 未出现在运行事件流中")

    def test_handoffs_all_accepted(self) -> None:
        handoffs = [e for e in self.evs if e["event_type"] == "交接"]
        self.assertEqual({h["payload"]["交接物"] for h in handoffs},
                         {"H1", "H2", "H3", "H4", "H5", "H7", "H8", "H9", "H11"})
        for h in handoffs:
            self.assertEqual(h["payload"]["缺失字段"], [], h["payload"])
            self.assertEqual(h["handoff"]["missing"], [])

    def test_agent_output_contracts_checked(self) -> None:
        """逐条 Agent 执行后产物满足契约：主张有 claim_id、候选有 candidate_id。"""
        o2 = json.loads((self.out / "o2" / "O2-核查明细.json").read_text(encoding="utf-8"))
        for it in o2["items"]:
            self.assertTrue(it["claim"]["claim_id"])
            for c in it.get("evidence") or []:
                self.assertTrue(c["candidate_id"])
            for c in it.get("calculations") or []:
                self.assertTrue(c["calc_id"])


if __name__ == "__main__":
    unittest.main()
