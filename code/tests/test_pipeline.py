"""端到端测试：DEMO-01 金标准、交付物完整性、契约校验、确定性、化名。

运行：cd code && python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from finaudit import contract
from finaudit.pipeline import run

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "fixtures" / "demo01"

EXPECTED_DISTRIBUTION = {
    "通过": 5, "错误": 4, "冲突": 1, "证据不足": 2,
    "低置信": 1, "需人工复核": 4, "不适用": 2, "待核验": 1,
}

REQUIRED_OUTPUTS = [
    "o1/O1-核查报告.md",
    "o2/O2-核查明细.json",
    "o2/O2-核查明细.csv",
    "o3/manifest.json",
    "o3/A-inputs.json",
    "o3/B-versions.json",
    "o3/C-events.jsonl",
    "o3/D-calculations.json",
    "o3/E-reviews.json",
    "o3/F-outputs.json",
    "o3/G-reproduce.md",
]


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "run1"
        cls.summary = run(FIX, cls.out)
        cls.o2 = json.loads((cls.out / "o2" / "O2-核查明细.json").read_text(encoding="utf-8"))
        cls.gt = {e["claim"]["claim_id"]: e for e in
                  json.loads((FIX / "claims_gt.json").read_text(encoding="utf-8"))}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_20_claims(self) -> None:
        self.assertEqual(len(self.o2["items"]), 20)

    def test_status_matches_ground_truth(self) -> None:
        for it in self.o2["items"]:
            cid = it["claim"]["claim_id"]
            self.assertEqual(it["current_verification"]["status"],
                             self.gt[cid]["gt_status"], msg=cid)

    def test_status_distribution(self) -> None:
        self.assertEqual(self.summary["status_counts"], EXPECTED_DISTRIBUTION)

    def test_gt_rule_coverage(self) -> None:
        for it in self.o2["items"]:
            cid = it["claim"]["claim_id"]
            got = set(it["current_verification"]["rule_ids"])
            gt = set(self.gt[cid]["gt_rule_ids"])
            if gt:
                self.assertTrue(gt.issubset(got), msg=f"{cid}: {gt} ⊄ {got}")

    def test_outputs_exist(self) -> None:
        for rel in REQUIRED_OUTPUTS:
            self.assertTrue((self.out / rel).is_file(), msg=rel)

    def test_o3_manifest_lists_all_groups(self) -> None:
        manifest = json.loads((self.out / "o3" / "manifest.json").read_text(encoding="utf-8"))
        for group in ("A-inputs.json", "B-versions.json", "C-events.jsonl",
                      "D-calculations.json", "E-reviews.json", "F-outputs.json",
                      "G-reproduce.md"):
            self.assertIn(group, manifest["files"])

    def test_contract_validation(self) -> None:
        for it in self.o2["items"]:
            self.assertEqual(contract.validate(it["claim"], "Claim"), [],
                             msg=it["claim"]["claim_id"])
            self.assertEqual(contract.validate(it["current_verification"],
                                               "VerificationResult"), [],
                             msg=it["current_verification"]["vrf_id"])
        events = [json.loads(line) for line in
                  (self.out / "o3" / "C-events.jsonl").read_text(encoding="utf-8").splitlines()
                  if line.strip()]
        self.assertGreater(len(events), 40)
        for e in events:
            self.assertEqual(contract.validate(e, "AuditEvent"), [], msg=e["event_id"])

    def test_error_items_have_locator_and_suggestion(self) -> None:
        for it in self.o2["items"]:
            v = it["current_verification"]
            if v["status"] == "错误":
                self.assertTrue(v.get("source_locator"), msg=v["claim_id"])
                self.assertTrue(v.get("suggestion"), msg=v["claim_id"])

    def test_pass_items_have_evidence(self) -> None:
        for it in self.o2["items"]:
            v = it["current_verification"]
            if v["status"] == "通过":
                self.assertTrue(v.get("evidence_refs"), msg=v["claim_id"])

    # ---- 证据块引用完整性（复核界面「定位原文」依赖它） ----
    def _blocks(self) -> list[dict]:
        A = json.loads((self.out / "o3" / "A-inputs.json").read_text(encoding="utf-8"))
        return A["blocks"]

    def test_block_ids_unique(self) -> None:
        ids = [b["block_id"] for b in self._blocks()]
        self.assertEqual(len(ids), len(set(ids)), "块号重复会让定位指向错误的块")

    def test_evidence_blocks_exist(self) -> None:
        """每条证据都必须指向索引里真实存在的块，不得出现悬空引用。"""

        known = {b["block_id"] for b in self._blocks()}
        dangling = [(it["claim"]["claim_id"], e["block_id"])
                    for it in self.o2["items"]
                    for e in (it.get("evidence") or [])
                    if e.get("block_id") and e["block_id"] not in known]
        self.assertEqual(dangling, [], f"证据指向不存在的块：{dangling}")

    def test_paragraph_metric_row_points_to_own_block(self) -> None:
        """段落里的数值行（如研发投入）应指向包含该段落的块，而不是另起块号。"""

        blocks = {b["block_id"]: b for b in self._blocks()}
        found = 0
        for it in self.o2["items"]:
            for e in it.get("evidence") or []:
                if e.get("block_id") in blocks and e.get("ocr_band") in ("高", "中", "低"):
                    b = blocks[e["block_id"]]
                    self.assertIn(e["quote"][:20], b["content_text"],
                                  msg=f"引文不在所指向的块内：{e['candidate_id']}")
                    found += 1
        self.assertGreater(found, 0, "DEMO-01 应至少有一条 OCR 段落证据")

    def test_determinism(self) -> None:
        out2 = Path(self._tmp.name) / "run2"
        summary2 = run(FIX, out2)
        self.assertEqual(summary2["status_counts"], self.summary["status_counts"])
        o2b = json.loads((out2 / "o2" / "O2-核查明细.json").read_text(encoding="utf-8"))
        sig_a = [(it["claim"]["claim_id"], it["current_verification"]["vrf_id"],
                  it["current_verification"]["status"]) for it in self.o2["items"]]
        sig_b = [(it["claim"]["claim_id"], it["current_verification"]["vrf_id"],
                  it["current_verification"]["status"]) for it in o2b["items"]]
        self.assertEqual(sig_a, sig_b)

    def test_no_six_digit_stock_codes(self) -> None:
        text = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                         for p in self.out.rglob("*") if p.is_file())
        # 排除十六进制哈希、小数、时间戳片段：6 位数字两侧不得紧邻数字/点/冒号/逗号/正负号/十六进制字符
        self.assertIsNone(re.search(
            r"(?<![\d.,:\-+a-fA-F])\d{6}(?![\d.,:\-+a-fA-F])", text))
        self.assertIn("甲企业", text)

    def test_event_enum_closed(self) -> None:
        events = [json.loads(line) for line in
                  (self.out / "o3" / "C-events.jsonl").read_text(encoding="utf-8").splitlines()
                  if line.strip()]
        for e in events:
            self.assertIn(e["event_type"], contract.EVENT_TYPES)


if __name__ == "__main__":
    unittest.main()
