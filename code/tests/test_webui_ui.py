"""界面接口契约测试：公式显示、审计摘要、任务排序、作业淘汰、页面结构。

这些用例只校验展示层契约，不重复 test_security 的越权与注入覆盖。
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path


class TestReviewPayloadShape(unittest.TestCase):
    """D 区两张表的展示字段。"""

    def test_formula_text_preferred_over_id(self) -> None:
        from web_page import PAGE
        # 界面按中文名渲染，编号放 title；两者都缺时才回退到编号
        self.assertIn("c.formula_text||c.formula_id", PAGE)
        self.assertIn("title=c.formula_id", PAGE)

    def test_failed_calc_shows_reason(self) -> None:
        from web_page import PAGE
        self.assertIn("c.result_status!=='成功'", PAGE)
        self.assertIn("c.error", PAGE)

    def test_item_view_uses_rule_names(self) -> None:
        """规则列给中文名，编号另存字段供追溯；未登记的编号原样保留。"""

        import webui

        item = {
            "claim": {"claim_id": "CLM-0001", "claim_text": "示例",
                      "claim_type": "数值型", "judgment_type": "事实"},
            "current_verification": {"status": "通过", "reason": "",
                                     "source_locator": {"value": "P1"},
                                     "rule_ids": ["R-NUM-01", "R-GEN-01", "R-XXX-99"]},
            "evidence": [],
        }
        view = webui._item_view(item)
        self.assertIn("原值一致性核对", view["rules"])
        self.assertIn("R-NUM-01", view["rule_ids"])
        # 未登记编号不编造名称，原样出现
        self.assertIn("R-XXX-99", view["rules"])


class TestReadablePayload(unittest.TestCase):
    """审计事件摘要从 JSON 串转成可读文本。"""

    def test_flat_payload_joined_by_separator(self) -> None:
        from webui import _readable_payload
        self.assertEqual(_readable_payload({"判错项数": 4, "违规项数": 0}), "判错项数=4；违规项数=0")

    def test_nested_payload_serialized(self) -> None:
        from webui import _readable_payload
        text = _readable_payload({"范围": ["a", "b"]})
        self.assertIn('["a", "b"]', text)

    def test_technical_keys_translated(self) -> None:
        from webui import _readable_payload
        text = _readable_payload({"tool": "rapidocr", "failure_kind": "引擎不可用"})
        self.assertIn("工具=rapidocr", text)
        self.assertIn("失败类型=引擎不可用", text)

    def test_unknown_key_kept_as_is(self) -> None:
        from webui import _readable_payload
        self.assertIn("foo=bar", _readable_payload({"foo": "bar"}))

    def test_long_payload_truncated(self) -> None:
        from webui import _readable_payload
        text = _readable_payload({"k": "x" * 400}, limit=50)
        self.assertEqual(len(text), 51)
        self.assertTrue(text.endswith("…"))

    def test_empty_payload(self) -> None:
        from webui import _readable_payload
        self.assertEqual(_readable_payload({}), "")


class TestTaskListing(unittest.TestCase):
    """任务下拉：只列有核验明细的目录，最近的排在前面。"""

    def test_sorted_by_mtime_desc_and_filtered(self) -> None:
        import webui
        old = webui.OUT
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, age in (("old", 300), ("new", 0), ("no_o2", 0)):
                d = root / name
                (d / "o2").mkdir(parents=True)
                if name != "no_o2":
                    (d / "o2" / "O2-核查明细.json").write_text("{}", encoding="utf-8")
                ts = time.time() - age
                import os
                os.utime(d, (ts, ts))
            webui.OUT = root
            try:
                self.assertEqual(webui._list_tasks(), ["new", "old"])
            finally:
                webui.OUT = old


class TestJobEviction(unittest.TestCase):
    """作业字典有上限，且不淘汰仍在运行的作业。"""

    def test_only_finished_are_evicted(self) -> None:
        import webui
        saved = webui._JOBS.copy()
        try:
            webui._JOBS.clear()
            for i in range(webui.MAX_JOBS + 3):
                webui._JOBS[f"done{i}"] = {"status": "完成", "started": i}
            webui._JOBS["running"] = {"status": "运行中", "started": 0}
            with webui._JOBS_LOCK:
                webui._evict_finished_jobs()
            self.assertIn("running", webui._JOBS)
            self.assertLessEqual(len(webui._JOBS), webui.MAX_JOBS)
            # 最早完成的先被淘汰
            self.assertNotIn("done0", webui._JOBS)
            self.assertIn(f"done{webui.MAX_JOBS + 2}", webui._JOBS)
        finally:
            webui._JOBS.clear()
            webui._JOBS.update(saved)


class TestFixtureScope(unittest.TestCase):
    """夹具定位只允许内置目录与上传目录下的一级子目录。"""

    def test_rejects_traversal_and_empty(self) -> None:
        from webui import _safe_fixture
        for bad in ("", "../", "..", "../fixtures", "U:", "U:../demo01",
                    "demo01/../bench", "a\\b", "/etc", "C:\\Windows"):
            self.assertIsNone(_safe_fixture(bad), bad)

    def test_accepts_builtin_and_upload(self) -> None:
        from webui import _safe_fixture
        self.assertIsNotNone(_safe_fixture("demo01"))
        self.assertIsNone(_safe_fixture("不存在的夹具"))


class TestPageStructure(unittest.TestCase):
    """页面必须保留的受控元素与提示位。"""

    def setUp(self) -> None:
        from web_page import PAGE
        self.page = PAGE

    def test_run_target_hint_present(self) -> None:
        self.assertIn('id="runhint"', self.page)
        self.assertIn("function runTarget()", self.page)

    def test_upload_requires_real_model_hint(self) -> None:
        self.assertIn('id="mockWarn"', self.page)
        self.assertIn("上传材料没有金标准", self.page)

    def test_tables_are_scroll_wrapped(self) -> None:
        for table_id in ("claims", "calcs", "audit", "review"):
            self.assertIn(f'<div class="tablewrap"><table id="{table_id}">', self.page)
        self.assertIn(".tablewrap{overflow-x:auto", self.page)

    def test_review_hint_not_overwritten_by_log_table(self) -> None:
        # 留痕表单独占用 reviewlog，提示写在 rvstat，避免提交反馈被覆盖
        self.assertIn('id="reviewlog"', self.page)
        self.assertIn("document.getElementById('reviewlog')", self.page)

    def test_rules_show_names_with_ids_in_tooltip(self) -> None:
        # 规则列显示中文名（顿号分隔），规则编号放 title 供追溯
        self.assertIn("it.rule_ids", self.page)
        self.assertIn("tr.children[3].title", self.page)


if __name__ == "__main__":
    unittest.main()
