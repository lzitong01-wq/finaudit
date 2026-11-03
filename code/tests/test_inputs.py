"""夹具输入契约：字段校验、错误提示、以及「不许静默忽略」。

这一组守的是最容易被忽视的一类缺陷：**输错字段名不会报错，而是悄悄失效**。
`publishdate` 少一个下划线，披露日就没登记，前视偏差过滤随之退化——报告还会
把用户自己的拼写错误写成"材料未登记披露日"。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from finaudit import inputs
from finaudit.pipeline import run

BASE = Path(__file__).resolve().parents[1]
FIXTURES = BASE / "fixtures"


def _mutate(src: Path, dst: Path, fn) -> Path:
    shutil.copytree(src, dst)
    fn(dst)
    return dst


class TestTaskOrder(unittest.TestCase):
    def test_required_fields_present(self) -> None:
        self.assertEqual(inputs.check_task({
            "task_id": "TSK-1", "subject_alias": "甲企业",
            "period_scope": {"kind": "期间"}, "data_cutoff": "2025-04-30",
        }), [])

    def test_missing_cutoff_is_error(self) -> None:
        """数据截止点缺失必须报错：没有它，「这条主张当时能不能核」无从判断。"""
        errs = inputs.check_task({
            "task_id": "TSK-1", "subject_alias": "甲企业", "period_scope": {},
        })
        self.assertTrue(any("data_cutoff" in e for e in errs), errs)
        self.assertTrue(any("缺必填" in e for e in errs), errs)

    def test_unknown_field_gets_typo_hint(self) -> None:
        errs = inputs.check_task({
            "task_id": "TSK-1", "subject_alias": "甲企业", "period_scope": {},
            "data_cutoff": "2025-04-30", "data_cutof": "2025-04-30",
        })
        self.assertTrue(any("未声明字段 `data_cutof`" in e for e in errs), errs)
        self.assertTrue(any("是不是想写 `data_cutoff`" in e for e in errs), errs)

    def test_not_a_dict(self) -> None:
        self.assertTrue(inputs.check_task(["不是对象"]))


class TestMaterials(unittest.TestCase):
    def _one(self, **over):
        item = {"material_id": "MAT-0001", "source_file_ref": "a.md", "role": "待核验研报"}
        item.update(over)
        return {"materials": [item]}

    def test_minimal_ok(self) -> None:
        self.assertEqual(inputs.check_materials(self._one()), [])

    def test_top_level_key_typo(self) -> None:
        errs = inputs.check_materials({"material": []})
        self.assertTrue(any("缺 `materials` 键" in e for e in errs), errs)

    def test_empty_list(self) -> None:
        self.assertTrue(any("为空" in e for e in inputs.check_materials({"materials": []})))

    def test_publishdate_typo_caught(self) -> None:
        errs = inputs.check_materials(self._one(publishdate="2025-04-30"))
        self.assertTrue(any("未声明字段 `publishdate`" in e for e in errs), errs)
        self.assertTrue(any("是不是想写 `publish_date`" in e for e in errs), errs)

    def test_bad_format(self) -> None:
        errs = inputs.check_materials(self._one(format="docx"))
        self.assertTrue(any("只支持 md / pdf / scanned_pdf" in e for e in errs), errs)

    def test_duplicate_material_id(self) -> None:
        doc = {"materials": [
            {"material_id": "MAT-0001", "source_file_ref": "a.md", "role": "待核验研报"},
            {"material_id": "MAT-0001", "source_file_ref": "b.md", "role": "证据材料"},
        ]}
        self.assertTrue(any("重复" in e for e in inputs.check_materials(doc)))

    def test_missing_required_field(self) -> None:
        errs = inputs.check_materials(self._one(source_file_ref=None))
        self.assertTrue(any("source_file_ref" in e for e in errs), errs)


class TestBundledFixturesPass(unittest.TestCase):
    """白名单过严会把自家夹具判错——四组夹具必须全部通过入口校验。"""

    def test_all_fixtures_valid(self) -> None:
        for d in sorted(FIXTURES.iterdir()):
            if not (d / "materials.json").is_file():
                continue
            with self.subTest(fixture=d.name):
                task = json.loads((d / "task_order.json").read_text(encoding="utf-8"))
                mats = json.loads((d / "materials.json").read_text(encoding="utf-8"))
                self.assertEqual(inputs.check_all(task, mats), [])


class TestPipelineRejectsBadInput(unittest.TestCase):
    def _run(self, src_mutate) -> Exception:
        tmp = Path(tempfile.mkdtemp())
        fixture = _mutate(BASE / "fixtures" / "demo01", tmp / "fx", src_mutate)
        with self.assertRaises(ValueError) as ctx:
            run(fixture, tmp / "out")
        return ctx.exception

    def test_publishdate_typo_blocks_run(self) -> None:
        """拼错的披露日不得被静默忽略。"""
        def bad(d: Path) -> None:
            p = d / "materials.json"
            doc = json.loads(p.read_text(encoding="utf-8"))
            doc["materials"][0]["publishdate"] = "2025-04-30"
            p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        err = self._run(bad)
        self.assertIn("publishdate", str(err))

    def test_missing_cutoff_blocks_run(self) -> None:
        def bad(d: Path) -> None:
            p = d / "task_order.json"
            task = json.loads(p.read_text(encoding="utf-8"))
            task.pop("data_cutoff")
            p.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
        self.assertIn("data_cutoff", str(self._run(bad)))

    def test_broken_json_message_is_readable(self) -> None:
        def bad(d: Path) -> None:
            (d / "materials.json").write_text("{坏", encoding="utf-8")
        msg = str(self._run(bad))
        self.assertIn("不是合法 JSON", msg)
        self.assertIn("行", msg)

    def test_no_partial_output_on_rejected_input(self) -> None:
        """输入不合契约时不该产出半份交付物，也不该清掉上一次的产物。"""
        tmp = Path(tempfile.mkdtemp())
        fixture = Path(tempfile.mkdtemp()) / "fx"
        shutil.copytree(BASE / "fixtures" / "demo01", fixture)
        out = tmp / "out"
        run(fixture, out)                       # 先跑一次，产出完整包
        before = (out / "o3" / "manifest.json").read_text(encoding="utf-8")

        p = fixture / "materials.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["materials"][0]["publishdate"] = "2025-04-30"
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            run(fixture, out)
        self.assertEqual((out / "o3" / "manifest.json").read_text(encoding="utf-8"),
                         before, "输入被拒时不该动已有产物")


class TestMissingEvidenceMaterialIsDisclosed(unittest.TestCase):
    """证据材料文件缺失：不中断，但必须在报告里点名，不能只在事件流里留痕。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        fixture = Path(cls._tmp.name) / "fx"
        shutil.copytree(BASE / "fixtures" / "demo01", fixture)
        p = fixture / "materials.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc["materials"][1]["source_file_ref"] = "MAT-9999-不存在.md"
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        cls.out = Path(cls._tmp.name) / "out"
        cls.summary = run(fixture, cls.out)
        cls.o1 = (cls.out / "o1" / "O1-核查报告.md").read_text(encoding="utf-8")
        cls.g = (cls.out / "o3" / "G-reproduce.md").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_run_still_completes(self) -> None:
        self.assertEqual(self.summary["claims_extracted"], 20)

    def test_input_formats_excludes_missing(self) -> None:
        """三份登记材料只剩两份能解析，报告不能仍写 md×3。"""
        self.assertIn("md×2", self.o1)
        self.assertNotIn("md×3", self.o1)

    def test_o1_names_the_missing_material(self) -> None:
        self.assertIn("材料可用性", self.o1)
        self.assertIn("1 份未解析", self.o1)
        self.assertIn("MAT-0002", self.o1)
        self.assertIn("文件缺失", self.o1)

    def test_release_line_carries_caveat(self) -> None:
        line = next(l for l in self.o1.splitlines() if l.startswith("- 放行："))
        self.assertIn("放行", line)
        self.assertIn("未解析", line)

    def test_g_reproduce_mentions_it(self) -> None:
        self.assertIn("未解析", self.g)


class TestCliExitCode(unittest.TestCase):
    def test_bad_input_exits_2_without_traceback(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        fixture = tmp / "fx"
        shutil.copytree(BASE / "fixtures" / "demo01", fixture)
        p = fixture / "task_order.json"
        task = json.loads(p.read_text(encoding="utf-8"))
        task.pop("data_cutoff")
        p.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, "-m", "finaudit.cli", "run",
             "--fixtures", str(fixture), "--out", str(tmp / "out")],
            cwd=BASE, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("错误：", proc.stderr)
        self.assertIn("data_cutoff", proc.stderr)

    def test_good_input_exits_0(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        proc = subprocess.run(
            [sys.executable, "-m", "finaudit.cli", "run",
             "--fixtures", str(BASE / "fixtures" / "demo01"), "--out", str(tmp / "out")],
            cwd=BASE, capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])


if __name__ == "__main__":
    unittest.main()
