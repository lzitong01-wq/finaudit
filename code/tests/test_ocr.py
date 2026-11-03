"""扫描件 OCR 端到端测试（可选依赖 rapidocr_onnxruntime；默认跳过，约 3-4 分钟）。

启用：FINAUDIT_OCR_TEST=1 python -m unittest tests.test_ocr -v
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "fixtures" / "demo01_scan"

try:
    import rapidocr_onnxruntime  # noqa: F401
    import pypdf  # noqa: F401
    HAS_OCR = True
except ImportError:  # pragma: no cover
    HAS_OCR = False

RUN = os.environ.get("FINAUDIT_OCR_TEST") == "1"


@unittest.skipUnless(HAS_OCR and RUN, "需 rapidocr_onnxruntime 且 FINAUDIT_OCR_TEST=1（OCR 约 3-4 分钟）")
class TestScanPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from finaudit.pipeline import run
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "scan"
        cls.summary = run(FIX, cls.out)
        cls.o2 = json.loads((cls.out / "o2" / "O2-核查明细.json").read_text(encoding="utf-8"))
        cls.gt = {e["claim"]["claim_id"]: e for e in
                  json.loads((FIX / "claims_gt.json").read_text(encoding="utf-8"))}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_ocr_rows_extracted(self) -> None:
        self.assertGreaterEqual(self.summary["rows"], 14)

    def test_scan_status_matches_ground_truth(self) -> None:
        bad = [
            (it["claim"]["claim_id"],
             self.gt[it["claim"]["claim_id"]]["gt_status"],
             it["current_verification"]["status"])
            for it in self.o2["items"]
            if it["current_verification"]["status"]
            != self.gt[it["claim"]["claim_id"]]["gt_status"]
        ]
        self.assertEqual(bad, [], msg=f"OCR 不一致 {len(bad)} 条")

    def test_ocr_bands_present(self) -> None:
        bands = {e.get("ocr_band") for it in self.o2["items"] for e in it["evidence"]}
        self.assertTrue(bands & {"高", "中", "低"}, msg=f"未见 OCR 分档：{bands}")


if __name__ == "__main__":
    unittest.main()
