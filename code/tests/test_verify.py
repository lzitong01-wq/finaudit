"""输出目录完整性核对的测试：run 出来的包应全部一致，改一个字节应被检出。"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from finaudit.pipeline import run
from finaudit.verify import verify_output

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "fixtures" / "demo01"


class TestVerify(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "run1"
        run(FIX, cls.out)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_fresh_run_passes(self) -> None:
        result = verify_output(self.out)
        self.assertTrue(result["ok"], msg=result["note"])
        self.assertGreaterEqual(len(result["checks"]), 8)
        self.assertTrue(all(c["status"] == "一致" for c in result["checks"]))

    def test_tampered_output_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "copy"
            shutil.copytree(self.out, copied)
            target = copied / "o1" / "O1-核查报告.md"
            target.write_text(target.read_text(encoding="utf-8") + "\n<!-- 改动 -->\n",
                              encoding="utf-8")
            result = verify_output(copied)
            self.assertFalse(result["ok"])
            bad = [c for c in result["checks"] if c["status"] == "不一致"]
            self.assertEqual(len(bad), 1)
            self.assertEqual(bad[0]["file"], "o1/O1-核查报告.md")

    def test_missing_manifest_is_not_silently_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty"
            empty.mkdir()
            result = verify_output(empty)
            self.assertFalse(result["ok"])
            self.assertIn("o3/manifest.json", result["missing"])
            self.assertTrue(result["note"])

    def test_extra_review_log_does_not_break_verify(self) -> None:
        """界面留痕写在 manifest 之外，追加后不应被判成篡改。"""

        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "copy"
            shutil.copytree(self.out, copied)
            log = copied / "o3" / "review-log.jsonl"
            log.write_text(json.dumps({"claim_id": "CLM-0001", "action": "确认已查看（仅留痕）"},
                                      ensure_ascii=False) + "\n", encoding="utf-8")
            self.assertTrue(verify_output(copied)["ok"])

    def test_unregistered_file_in_package_detected(self) -> None:
        """包里混进未登记文件（上一次运行的残留）：哈希全对也不能算「包一致」。"""
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "copy"
            shutil.copytree(self.out, copied)
            (copied / "o3" / "STALE-old-run.txt").write_text("上次的", encoding="utf-8")
            result = verify_output(copied)
            self.assertFalse(result["ok"])
            self.assertEqual(result["unexpected"], ["o3/STALE-old-run.txt"])
            self.assertIn("未登记", result["note"])

    def test_rerun_clears_stale_but_keeps_review_log(self) -> None:
        """重跑到同一目录：旧产物清掉，跨运行累积的复核留痕保留。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "rerun"
            run(FIX, out)
            stale = out / "o3" / "STALE-old-run.txt"
            stale.write_text("上次的", encoding="utf-8")
            log = out / "o3" / "review-log.jsonl"
            log.write_text('{"claim_id": "CLM-0001"}\n', encoding="utf-8")

            summary = run(FIX, out)
            self.assertGreater(summary["cleared_previous"], 0)
            self.assertFalse(stale.exists())
            self.assertTrue(log.exists())
            self.assertTrue(verify_output(out)["ok"])


if __name__ == "__main__":
    unittest.main()
