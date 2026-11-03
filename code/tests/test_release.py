"""发行一致性：版本号单一来源、README 与产物对齐、复核留痕并入审计包。

守两类回归：
1. 版本号写死在多个模块里，改一处漏一处，README 说 1.7.2、产物说 0.1.0；
2. 包里 review-log.jsonl 记着复核，E-reviews.json 却写「无复核记录」。
"""

from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from finaudit import __version__, cli
from finaudit.pipeline import run
from finaudit.report import _collect_reviews

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "fixtures" / "demo01"
README = BASE.parent / "README.md"


class TestVersionSingleSource(unittest.TestCase):
    def test_readme_states_same_version(self) -> None:
        text = README.read_text(encoding="utf-8")
        m = re.search(r"当前版本\s*([0-9]+\.[0-9]+\.[0-9]+)", text)
        self.assertIsNotNone(m, "README 里找不到「当前版本 X.Y.Z」")
        self.assertEqual(m.group(1), __version__,
                         "README 的当前版本与 finaudit/__init__.py 的 __version__ 不一致")

    def test_cli_prints_version(self) -> None:
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
            cli.main(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn(__version__, buf.getvalue())

    def test_webui_substitutes_version_placeholder(self) -> None:
        """页面模板留占位符，服务端按 __version__ 替换；两处不得各写一份。"""
        from web_page import PAGE
        from webui import APP_VERSION
        self.assertEqual(APP_VERSION, __version__)
        self.assertIn("%VERSION%", PAGE)
        self.assertIn(f"v{__version__}", PAGE.replace("%VERSION%", APP_VERSION))

    def test_no_second_version_definition(self) -> None:
        """__init__.py 之外不得再有 __version__ = "..."，防止出现第二个真源。"""
        offenders = []
        for path in sorted((BASE / "finaudit").glob("*.py")):
            if path.name == "__init__.py":
                continue
            if re.search(r"^__version__\s*=", path.read_text(encoding="utf-8"), re.M):
                offenders.append(path.name)
        self.assertEqual(offenders, [], f"以下模块自行定义了版本号：{offenders}")

    def test_no_hardcoded_tool_version_literal(self) -> None:
        """工具版本必须走 __version__，不能再写死成 "version": "1.2.3"。"""
        offenders = []
        for path in sorted((BASE / "finaudit").glob("*.py")):
            for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r'"(?:version|tool_version|版本)"\s*:\s*"[0-9]+\.[0-9]+\.[0-9]+"', line):
                    offenders.append(f"{path.name}:{no}")
        self.assertEqual(offenders, [], f"以下位置写死了版本号：{offenders}")


class TestVersionInPackage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "run"
        run(FIX, cls.out)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_b_versions_tools_report_release_version(self) -> None:
        b = json.loads((self.out / "o3" / "B-versions.json").read_text(encoding="utf-8"))
        names = [t["name"] for t in b["tools"]]
        self.assertEqual(names, ["finaudit", "md-parser", "finaudit-calc"])
        for tool in b["tools"]:
            self.assertEqual(tool["version"], __version__, f"{tool['name']} 版本与发行版本不一致")

    def test_calculations_record_release_version(self) -> None:
        d = json.loads((self.out / "o3" / "D-calculations.json").read_text(encoding="utf-8"))
        seen = [c["tool"] for c in d["calculations"] if c.get("tool")]
        self.assertTrue(seen, "D 组应至少有一条计算记录")
        for tool in seen:
            self.assertEqual(tool["version"], __version__)


class TestReviewLogFoldedIntoPackage(unittest.TestCase):
    def test_collect_reviews_empty_when_no_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            got = _collect_reviews(Path(tmp) / "review-log.jsonl")
            self.assertEqual(got["manual_reviews"], [])
            self.assertEqual(got["count"], 0)
            self.assertEqual(got["unparsed_lines"], 0)
            self.assertIn("常为空", got["note"])

    def test_collect_reviews_counts_bad_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "review-log.jsonl"
            log.write_text(
                json.dumps({"review_id": "RV-1", "claim_id": "CLM-0001"}, ensure_ascii=False) + "\n"
                + "{坏行\n"
                + "\n"
                + json.dumps({"review_id": "RV-2", "claim_id": "CLM-0002"}, ensure_ascii=False) + "\n",
                encoding="utf-8")
            got = _collect_reviews(log)
            self.assertEqual(got["count"], 2)
            self.assertEqual(got["unparsed_lines"], 1)
            self.assertIn("无法解析", got["note"])

    def test_rerun_carries_existing_reviews_into_e_group(self) -> None:
        """先跑一次 → 界面留痕 → 再跑一次：E 组必须带上这条记录。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            run(FIX, out)
            first = json.loads((out / "o3" / "E-reviews.json").read_text(encoding="utf-8"))
            self.assertEqual(first["count"], 0)

            rec = {"review_id": "RV-abc", "claim_id": "CLM-0001", "action": "确认",
                   "operator": "复核员", "at": "2026-01-01T09:00:00", "note": "核对原文一致"}
            with (out / "o3" / "review-log.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

            run(FIX, out)
            second = json.loads((out / "o3" / "E-reviews.json").read_text(encoding="utf-8"))
            self.assertEqual(second["count"], 1)
            self.assertEqual(second["manual_reviews"][0]["review_id"], "RV-abc")
            self.assertTrue((out / "o3" / "review-log.jsonl").exists(),
                            "重跑应保留复核留痕，不能清掉")


if __name__ == "__main__":
    unittest.main()
