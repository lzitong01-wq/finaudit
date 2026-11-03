"""异常输入容错测试：管线对损坏/空/畸形输入必须优雅降级，不静默编造。"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from finaudit.pipeline import run

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "fixtures" / "demo01"


def _make_fixture(tmp: Path, mutate) -> Path:
    dst = tmp / "fx"
    shutil.copytree(FIX, dst)
    mutate(dst)
    return dst


class TestRobustness(unittest.TestCase):
    def test_empty_material_falls_back(self) -> None:
        """研报材料为空：主张回退夹具，管线完成不崩，证据缺失显式出口（证据不足/需人工复核）。"""
        with tempfile.TemporaryDirectory() as td:
            fx = _make_fixture(Path(td), lambda d: (d / "materials" / "MAT-0001-研报.md")
                               .write_text("", encoding="utf-8"))
            summary = run(fx, Path(td) / "out")
            self.assertEqual(summary["claims"], 20)
            # 空材料必然触发证据不足/需人工复核（含 R-GEN-06 守卫），不判"通过"
            self.assertGreaterEqual(
                summary["status_counts"].get("证据不足", 0)
                + summary["status_counts"].get("需人工复核", 0)
                + summary["status_counts"].get("待核验", 0), 1)

    def test_corrupt_pdf_marked_parse_failure(self) -> None:
        """损坏的 PDF：解析失败显式记录，管线完成，主张不静默判"通过"。"""
        with tempfile.TemporaryDirectory() as td:
            fx = _make_fixture(Path(td), lambda d: (
                (d / "materials" / "MAT-0002-年报.md").unlink(),
                (d / "materials" / "MAT-0002-年报.pdf").write_bytes(b"%PDF-9.9 garbage"),
                self._switch_ref(d)))
            summary = run(fx, Path(td) / "out")
            self.assertIn("claims", summary)
            # 年报证据缺失 → 证据不足/需人工复核/待核验 占主，不允许全"通过"
            self.assertLess(summary["status_counts"].get("通过", 0), 5)

    def test_empty_ground_truth_completes(self) -> None:
        """金标准为空的夹具：管线完成，评测报告不崩。"""
        with tempfile.TemporaryDirectory() as td:
            fx = _make_fixture(Path(td), lambda d: (d / "claims_gt.json")
                               .write_text("[]", encoding="utf-8"))
            summary = run(fx, Path(td) / "out")
            self.assertEqual(summary["claims"], 0)
            self.assertTrue((Path(td) / "out" / "eval-report.md").exists())

    def test_gbk_material_decoded_not_crashed(self) -> None:
        """GBK/GB18030 材料（国内年报导出常见）：按编码探测解码，不得抛 UnicodeDecodeError。"""

        def to_gbk(d: Path) -> None:
            for name in ("MAT-0001-研报.md", "MAT-0002-年报.md"):
                p = d / "materials" / name
                p.write_bytes(p.read_text(encoding="utf-8").encode("gbk"))

        with tempfile.TemporaryDirectory() as td:
            fx = _make_fixture(Path(td), to_gbk)
            summary = run(fx, Path(td) / "out")
            # 解码正确时结果与 UTF-8 版本一致：20 条、五种以上状态、不是靠乱码蒙出来
            self.assertEqual(summary["claims"], 20)
            self.assertEqual(summary["status_counts"].get("通过"), 5)
            events = [json.loads(line) for line in
                      (Path(td) / "out" / "o3" / "C-events.jsonl")
                      .read_text(encoding="utf-8").splitlines() if line.strip()]
            encodings = {e["payload"].get("编码") for e in events
                         if e.get("payload", {}).get("编码")}
            self.assertIn("gb18030", encodings)

    def test_utf8_bom_material_decoded(self) -> None:
        """带 BOM 的 UTF-8：BOM 不得混进首个块正文，否则首条主张定位会偏移。"""

        def add_bom(d: Path) -> None:
            p = d / "materials" / "MAT-0001-研报.md"
            p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())

        with tempfile.TemporaryDirectory() as td:
            fx = _make_fixture(Path(td), add_bom)
            summary = run(fx, Path(td) / "out")
            self.assertEqual(summary["claims"], 20)
            self.assertNotIn("\ufeff", (Path(td) / "out" / "o1" / "O1-核查报告.md")
                             .read_text(encoding="utf-8"))

    def test_missing_evidence_registered_invalid(self) -> None:
        """证据材料文件缺失：登记为无效并跳过解析，其余材料照常核验，不抛 FileNotFoundError。"""

        def drop_yearbook(d: Path) -> None:
            (d / "materials" / "MAT-0002-年报.md").unlink()

        with tempfile.TemporaryDirectory() as td:
            fx = _make_fixture(Path(td), drop_yearbook)
            summary = run(fx, Path(td) / "out")
            self.assertEqual(summary["claims"], 20)
            a = json.loads((Path(td) / "out" / "o3" / "A-inputs.json")
                           .read_text(encoding="utf-8"))
            status = {m["material_id"]: m["sha256"] for m in a["materials"]}
            self.assertEqual(status["MAT-0002"], "0" * 64)  # 无效占位
            events = [json.loads(line) for line in
                      (Path(td) / "out" / "o3" / "C-events.jsonl")
                      .read_text(encoding="utf-8").splitlines() if line.strip()]
            invalid = [e for e in events if e["event_type"] == "材料登记"
                       and e["payload"].get("状态") == "无效"]
            self.assertEqual(len(invalid), 1)
            self.assertEqual(invalid[0]["payload"]["material_id"], "MAT-0002")

    def test_missing_report_is_explicit_error(self) -> None:
        """研报文件缺失无法核验：显式报错并指出文件，不能静默跑成空报告。"""

        def drop_report(d: Path) -> None:
            (d / "materials" / "MAT-0001-研报.md").unlink()

        with tempfile.TemporaryDirectory() as td:
            fx = _make_fixture(Path(td), drop_report)
            with self.assertRaises(ValueError) as ctx:
                run(fx, Path(td) / "out")
            self.assertIn("研报文件缺失", str(ctx.exception))
            self.assertIn("MAT-0001-研报.md", str(ctx.exception))

    def test_duplicate_material_rejected(self) -> None:
        """同一份材料登记两次会让块索引整份翻倍，必须在登记阶段拒绝。"""
        for label, copy_file in (("同文件", False), ("同内容", True)):
            with self.subTest(label), tempfile.TemporaryDirectory() as td:
                def add_dup(d: Path, copy_file: bool = copy_file) -> None:
                    if copy_file:
                        shutil.copy2(d / "materials" / "MAT-0002-年报.md",
                                     d / "materials" / "副本-年报.md")
                        ref = "副本-年报.md"
                    else:
                        ref = "MAT-0002-年报.md"
                    p = d / "materials.json"
                    data = json.loads(p.read_text(encoding="utf-8"))
                    dup = dict(data["materials"][1])
                    dup.update({"material_id": "MAT-0009", "source_file_ref": ref})
                    data["materials"].append(dup)
                    p.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                 encoding="utf-8")

                fx = _make_fixture(Path(td), add_dup)
                with self.assertRaises(ValueError) as ctx:
                    run(fx, Path(td) / "out")
                self.assertIn("材料重复登记", str(ctx.exception))

    def test_material_published_after_cutoff_not_evidence(self) -> None:
        """截止点尚未披露的材料整份不作证据，否则等于用未来信息核验。"""

        def shift_cutoff(d: Path) -> None:
            p = d / "task_order.json"
            t = json.loads(p.read_text(encoding="utf-8"))
            t["data_cutoff"] = "2025-04-20"  # 年报披露日 2025-04-25，公告 2025-04-18
            p.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")

        with tempfile.TemporaryDirectory() as td:
            fx = _make_fixture(Path(td), shift_cutoff)
            summary = run(fx, Path(td) / "out")
            self.assertEqual(summary["claims"], 20)
            data = json.loads((Path(td) / "out" / "o2" / "O2-核查明细.json")
                              .read_text(encoding="utf-8"))
            used = {e["material_id"] for it in data["items"] for e in it.get("evidence") or []}
            self.assertNotIn("MAT-0002", used)
            self.assertIn("MAT-0003", used)  # 已披露的公告仍可用
            md = (Path(td) / "out" / "o1" / "O1-核查报告.md").read_text(encoding="utf-8")
            self.assertIn("尚未披露的材料整份不作证据", md)
            self.assertIn("MAT-0002（披露日 2025-04-25）", md)

    def test_report_material_always_available(self) -> None:
        """研报是核验对象本身：披露日晚于截止点也不得被拦，否则表内恒等式无输入。"""

        def late_cutoff(d: Path) -> None:
            p = d / "task_order.json"
            t = json.loads(p.read_text(encoding="utf-8"))
            t["data_cutoff"] = "2025-04-26"  # 早于研报披露日 2025-04-30
            p.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")

        with tempfile.TemporaryDirectory() as td:
            fx = _make_fixture(Path(td), late_cutoff)
            summary = run(fx, Path(td) / "out")
            md = (Path(td) / "out" / "o1" / "O1-核查报告.md").read_text(encoding="utf-8")
            self.assertNotIn("MAT-0001（披露日", md)
            self.assertEqual(summary["status_counts"].get("通过"), 5)

    def test_no_publish_date_degrades_openly(self) -> None:
        """上传材料通常没有披露日：退化为只有期间过滤，但必须在报告里说明。"""

        def drop_dates(d: Path) -> None:
            p = d / "materials.json"
            data = json.loads(p.read_text(encoding="utf-8"))
            for m in data["materials"]:
                m.pop("publish_date", None)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        with tempfile.TemporaryDirectory() as td:
            fx = _make_fixture(Path(td), drop_dates)
            summary = run(fx, Path(td) / "out")
            self.assertEqual(summary["claims"], 20)
            md = (Path(td) / "out" / "o1" / "O1-核查报告.md").read_text(encoding="utf-8")
            self.assertIn("材料未登记披露日", md)
            self.assertIn("只按期间判断", md)

    def test_cutoff_helpers_two_layers(self) -> None:
        """行级按期间起点、材料级按披露日，两层各管一段，都不越权。"""
        from finaudit.pipeline import _period_beyond_cutoff, _published_after

        self.assertTrue(_period_beyond_cutoff({"start": "2025-01-01"}, "2024-12-31"))
        self.assertFalse(_period_beyond_cutoff({"point": "2024-12-31"}, "2024-12-31"))
        self.assertFalse(_period_beyond_cutoff(None, "2024-12-31"))
        self.assertFalse(_period_beyond_cutoff({"start": "2025-01-01"}, None))
        self.assertTrue(_published_after("2025-04-25", "2025-04-20"))
        self.assertFalse(_published_after("2025-04-18", "2025-04-20"))
        # 未登记披露日 → 不拦，退化为按期间过滤
        self.assertFalse(_published_after(None, "2025-04-20"))

    @staticmethod
    def _switch_ref(d: Path) -> None:
        for name in ("task_order.json", "materials.json"):
            p = d / name
            data = json.loads(p.read_text(encoding="utf-8"))
            items = data if isinstance(data, list) else data.get("materials", [])
            for m in items:
                ref = m.get("source_file_ref", "")
                if ref.endswith("年报.md"):
                    m["source_file_ref"] = ref.replace(".md", ".pdf")
                    m["format"] = "pdf"
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
