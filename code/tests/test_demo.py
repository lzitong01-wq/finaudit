"""演示脚本测试：D0–D8 九步全部产出，版本锚定一致、审计通过。"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from finaudit.demo import run_demo

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "fixtures" / "demo01"


class TestDemo(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "demo"
        with contextlib.redirect_stdout(io.StringIO()):
            cls.result = run_demo(FIX, cls.out)
        cls.text = Path(cls.result["transcript"]).read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_all_steps_present(self) -> None:
        for step in ("D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"):
            self.assertIn(f"## {step}", self.text, msg=step)

    def test_version_anchor_consistent(self) -> None:
        d0 = self.text.split("## D0")[1].split("## D1")[0]
        self.assertIn("一致", d0)
        self.assertNotIn("不一致", d0)

    def test_audit_passed(self) -> None:
        self.assertTrue(self.result["summary"]["audit_ok"])
        self.assertIn("审计结论：通过", self.text)

    def test_d6_shows_boundaries(self) -> None:
        d6 = self.text.split("## D6")[1].split("## D7")[0]
        for st in ("冲突", "证据不足", "需人工复核"):
            self.assertIn(st, d6)

    def test_d5_llm_trace(self) -> None:
        d5 = self.text.split("## D5")[1].split("## D6")[0]
        self.assertIn("LLM 调用留痕", d5)
        self.assertIn("prompt_version", d5)


if __name__ == "__main__":
    unittest.main()
