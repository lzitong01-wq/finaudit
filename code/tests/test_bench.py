"""GS-C 规模基准（240 条构造性金标准）回归测试：状态准确率必须 100%。"""

from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from finaudit.pipeline import run

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "fixtures" / "bench"


class TestBenchmark(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "bench"
        cls.summary = run(FIX, cls.out)
        cls.o2 = json.loads((cls.out / "o2" / "O2-核查明细.json").read_text(encoding="utf-8"))
        cls.gt = {e["claim"]["claim_id"]: e for e in
                  json.loads((FIX / "claims_gt.json").read_text(encoding="utf-8"))}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_300_claims(self) -> None:
        self.assertEqual(len(self.o2["items"]), 300)

    def test_accuracy_100pct(self) -> None:
        bad = [
            (it["claim"]["claim_id"],
             self.gt[it["claim"]["claim_id"]]["gt_status"],
             it["current_verification"]["status"])
            for it in self.o2["items"]
            if it["current_verification"]["status"]
            != self.gt[it["claim"]["claim_id"]]["gt_status"]
        ]
        self.assertEqual(bad, [], msg=f"不一致 {len(bad)} 条，前 5：{bad[:5]}")

    def test_family_distribution(self) -> None:
        dist = Counter(v["gt_status"] for v in self.gt.values())
        self.assertEqual(dict(dist), {"通过": 105, "错误": 125, "低置信": 40,
                                      "冲突": 10, "需人工复核": 10, "证据不足": 10})

    def test_stability_flag(self) -> None:
        self.assertTrue(self.summary["stability"])


if __name__ == "__main__":
    unittest.main()
