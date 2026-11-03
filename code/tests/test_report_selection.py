"""研报选择：必须按材料登记的 role 定位，不按材料编号顺序假设。"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from finaudit.pipeline import report_material_id, material_rank, run
from finaudit.llm import LLMConfig

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "fixtures" / "demo01"


def _load(d: Path, name: str):
    return json.loads((d / name).read_text(encoding="utf-8"))


def _save(d: Path, name: str, obj) -> None:
    (d / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


class TestReportSelection(unittest.TestCase):
    def test_role_derives_report_and_rank(self) -> None:
        mats = _load(FIX, "materials.json")["materials"]
        self.assertEqual(report_material_id(mats), "MAT-0001")
        ranks = material_rank(mats, report_material_id(mats))
        # 年报(1) > 公告(2) > 研报(9，不作为证据)
        self.assertEqual(ranks["MAT-0002"], 1)
        self.assertEqual(ranks["MAT-0003"], 2)
        self.assertEqual(ranks["MAT-0001"], 9)

    def test_no_report_role_raises(self) -> None:
        with self.assertRaises(ValueError):
            report_material_id([{"material_id": "MAT-0001", "role": "年报（证据材料）"}])

    def test_two_reports_raise(self) -> None:
        with self.assertRaises(ValueError):
            report_material_id([{"material_id": "MAT-0001", "role": "待核验研报"},
                                {"material_id": "MAT-0002", "role": "待核验研报"}])

    def test_real_model_failure_never_falls_back_to_fixture_gold(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "failed-model"
            summary = run(FIX, out, llm_config=LLMConfig(
                provider="openai", model="unavailable", base_url=""))
            self.assertEqual(summary["claims_extracted"], 0)
            self.assertEqual(summary["claims"], 0)
            data = _load(out / "o2", "O2-核查明细.json")
            self.assertEqual(data["items"], [])

    def test_report_not_first_material(self) -> None:
        """研报登记在最后一份（编号 MAT-0003）：核验结果与基线一致，不核错文件。"""
        with tempfile.TemporaryDirectory() as td:
            base = run(FIX, Path(td) / "base")
            fx = Path(td) / "fx"
            shutil.copytree(FIX, fx)
            mats = _load(fx, "materials.json")["materials"]
            report = next(m for m in mats if m["material_id"] == "MAT-0001")
            others = [m for m in mats if m["material_id"] != "MAT-0001"]
            # 重新编号：研报排到最后一份
            ordered = others + [report]
            for i, m in enumerate(ordered, start=1):
                m["material_id"] = f"MAT-{i:04d}"
            report_id = f"MAT-{len(ordered):04d}"
            _save(fx, "materials.json", {"materials": ordered})

            task = _load(fx, "task_order.json")
            by_name = {m["source_file_ref"]: m["role"] for m in ordered}
            for t in task["materials"]:
                t["role"] = by_name.get(t["source_file_ref"], t.get("role"))
            _save(fx, "task_order.json", task)

            gt = _load(fx, "claims_gt.json")
            for e in gt:
                e["claim"]["report_material_id"] = report_id
                for ev in e.get("evidence") or []:
                    ev.pop("material_id", None)
            _save(fx, "claims_gt.json", gt)

            moved = run(fx, Path(td) / "moved")
            self.assertEqual(report_material_id(ordered), report_id)
            self.assertEqual(moved["claims"], base["claims"])
            self.assertEqual(moved["status_counts"], base["status_counts"])


if __name__ == "__main__":
    unittest.main()
