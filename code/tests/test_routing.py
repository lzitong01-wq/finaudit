"""主张 → 公式的路由：该算的必须算，不该算的不能硬套。

两类曾经出错的情形：
- 比较类主张（同比/较年初）原本只硬编码了「营业收入同比」「存货较年初」，
  换成净利润、资产合计就落不到公式上；
- 变动量主张（百分点）被套用水平值公式，「毛利率同比下降 2 个百分点」会拿
  35.7% 的毛利率去比 2，判出一个看起来确定的"错误"。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from finaudit import formulas
from finaudit.pipeline import (ROUTED_FORMULAS, UNROUTED_FORMULAS,
                               _is_growth_claim, run)

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "fixtures" / "demo01"

# (主张文本, 数值, 数值文本, 单位) → 期望的 (状态, 公式, result_status, 错误说明片段)
CASES = [
    ("甲企业2024年度净利润同比增长15%", 15, "15%", "%",
     ("需人工复核", "FRM-CMP-01", "参数校验失败", "基期值")),
    ("甲企业2024年末资产合计较年初增长8%", 8, "8%", "%",
     ("需人工复核", "FRM-CMP-03", "参数校验失败", "年初值")),
    ("甲企业2024年度毛利率同比下降2个百分点", 2, "2个百分点", "百分点",
     ("需人工复核", "无对应公式", "参数校验失败", "百分点口径")),
    ("甲企业2024年度营业收入同比增长10%", 10, "10%", "%",
     ("错误", "FRM-CMP-01", "成功", None)),
]


class TestFormulaInventory(unittest.TestCase):
    """16 条公式必须逐条表态：要么路由能触发，要么写明为什么不能。"""

    def test_every_formula_declared(self) -> None:
        registered = set(formulas.FORMULAS)
        self.assertEqual(registered, set(ROUTED_FORMULAS) | set(UNROUTED_FORMULAS))

    def test_declarations_disjoint(self) -> None:
        self.assertFalse(ROUTED_FORMULAS & set(UNROUTED_FORMULAS))

    def test_routed_really_appears_in_router(self) -> None:
        """声明为「已路由」的，build_calculation 里必须真有这条公式号，否则声明是空的。"""
        source = self._router_source()
        for fid in sorted(ROUTED_FORMULAS):
            self.assertIn(f'"{fid}"', source, f"{fid} 声明已路由，但 build_calculation 里没有")

    def test_unrouted_really_absent_from_router(self) -> None:
        source = self._router_source()
        for fid in sorted(UNROUTED_FORMULAS):
            self.assertNotIn(f'"{fid}"', source,
                             f"{fid} 已声明未路由，却在判定链里出现了——补一个 e2e 用例并挪表")

    @staticmethod
    def _router_source() -> str:
        import inspect
        from finaudit.pipeline import build_calculation
        return inspect.getsource(build_calculation)

    def test_every_unrouted_has_reason(self) -> None:
        for fid, reason in UNROUTED_FORMULAS.items():
            self.assertTrue(reason.strip(), fid)


class TestGrowthDetection(unittest.TestCase):
    def test_growth_needs_percent_unit(self) -> None:
        self.assertTrue(_is_growth_claim("营业收入同比增长12%", "%"))
        self.assertTrue(_is_growth_claim("存货较年初增长10%", "%"))
        # 百分点比的是差，不是增速
        self.assertFalse(_is_growth_claim("毛利率同比下降2个百分点", "百分点"))
        # 金额单位不是增速
        self.assertFalse(_is_growth_claim("营业收入同比增长30亿元", "亿元"))
        # 没有比较字样就是水平值
        self.assertFalse(_is_growth_claim("毛利率为42%", "%"))


class TestRoutingEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        fx = tmp / "fx"
        shutil.copytree(FIX, fx)
        gt = json.loads((fx / "claims_gt.json").read_text(encoding="utf-8"))
        template = gt[1]["claim"]
        for i, (text, value, vtext, unit, _) in enumerate(CASES):
            claim = json.loads(json.dumps(template, ensure_ascii=False))
            claim.update({"claim_id": f"CLM-00{91 + i}", "claim_text": text,
                          "context_quote": text + "。", "judgment_type": "计算"})
            claim["numeric"].update({"value": value, "value_text": vtext,
                                     "unit": {"code": unit}})
            gt.append({"claim": claim, "gt_status": "待定", "gt_rule_ids": []})
        (fx / "claims_gt.json").write_text(
            json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")

        out = tmp / "out"
        run(fx, out)
        data = json.loads((out / "o2" / "O2-核查明细.json").read_text(encoding="utf-8"))
        items = {it["claim"]["claim_id"]: it for it in data["items"]}
        new_ids = sorted(k for k in items if int(k.split("-")[1]) > 20)
        cls.items = [items[k] for k in new_ids]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_cases(self) -> None:
        self.assertEqual(len(self.items), len(CASES))
        for item, (text, *_, expect) in zip(self.items, CASES):
            with self.subTest(claim=text):
                status, formula, result_status, err = expect
                self.assertEqual(item["current_verification"]["status"], status)
                calcs = item.get("calculations") or []
                self.assertTrue(calcs, f"{text} 没有计算记录")
                c = calcs[0]
                self.assertEqual(c["formula_id"], formula)
                self.assertEqual(c["result_status"], result_status)
                if err:
                    self.assertIn(err, c.get("error") or "")

    def test_point_metric_growth_uses_year_end_base(self) -> None:
        """时点指标比期末：资产合计较年初要取 2023-12-31，而不是 2023 年度。"""
        calc = (self.items[1].get("calculations") or [{}])[0]
        roles = [i["role"] for i in calc["inputs"]]
        self.assertEqual(roles[0], "本期末值")


if __name__ == "__main__":
    unittest.main()
