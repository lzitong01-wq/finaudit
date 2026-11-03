"""PDF 材料端到端测试（可选依赖 pypdf；未安装时跳过）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "fixtures" / "demo01_pdf"

try:
    import pypdf  # noqa: F401
    HAS_PDF = True
except ImportError:  # pragma: no cover
    HAS_PDF = False


@unittest.skipUnless(HAS_PDF, "pypdf 未安装（PDF 接入为可选依赖）")
class TestPDFPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from finaudit.pipeline import run
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "pdf"
        cls.summary = run(FIX, cls.out)
        cls.o2 = json.loads((cls.out / "o2" / "O2-核查明细.json").read_text(encoding="utf-8"))
        cls.gt = {e["claim"]["claim_id"]: e for e in
                  json.loads((FIX / "claims_gt.json").read_text(encoding="utf-8"))}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_pdf_extract_rows(self) -> None:
        self.assertGreaterEqual(self.summary["rows"], 14)
        self.assertGreaterEqual(self.summary["blocks"], 3)

    def test_pdf_status_matches_ground_truth(self) -> None:
        bad = [
            (it["claim"]["claim_id"],
             self.gt[it["claim"]["claim_id"]]["gt_status"],
             it["current_verification"]["status"])
            for it in self.o2["items"]
            if it["current_verification"]["status"]
            != self.gt[it["claim"]["claim_id"]]["gt_status"]
        ]
        self.assertEqual(bad, [], msg=f"不一致 {len(bad)} 条")

    def test_pdf_matches_markdown_result(self) -> None:
        md_out = Path(self._tmp.name) / "md"
        from finaudit.pipeline import run
        run(BASE / "fixtures" / "demo01", md_out)
        md = json.loads((md_out / "o2" / "O2-核查明细.json").read_text(encoding="utf-8"))
        md_sig = [(it["claim"]["claim_id"], it["current_verification"]["status"])
                  for it in md["items"]]
        pdf_sig = [(it["claim"]["claim_id"], it["current_verification"]["status"])
                   for it in self.o2["items"]]
        self.assertEqual(md_sig, pdf_sig)


if __name__ == "__main__":
    unittest.main()
