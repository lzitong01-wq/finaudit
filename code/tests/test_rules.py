"""rules.py 硬测试（unittest）：判定顺序、冲突前置检查、无法判定出口。

运行：cd code && python -m unittest tests.test_rules -v
"""

from __future__ import annotations

import unittest

from finaudit.rules import (
    RULES,
    STATUS_VALUES,
    check_calc_routing,
    check_judgment_record,
    check_status_exit,
    check_tie_tolerance,
    conflict_detected,
    evaluate,
    norm_text,
    severity_of,
)

PERIOD_2024 = {"kind": "期间", "start": "2024-01-01", "end": "2024-12-31", "point": None, "label": "2024年度"}
PERIOD_2023 = {"kind": "期间", "start": "2023-01-01", "end": "2023-12-31", "point": None, "label": "2023年度"}
PERIOD_2026 = {"kind": "期间", "start": "2026-01-01", "end": "2026-12-31", "point": None, "label": "2026年度"}
PERIOD_2024_Q3 = {"kind": "期间", "start": "2024-07-01", "end": "2024-09-30", "point": None, "label": "2024Q3"}
POINT_2024 = {"kind": "时点", "point": "2024-12-31", "start": None, "end": None, "label": "2024年末"}
POINT_2023 = {"kind": "时点", "point": "2023-12-31", "start": None, "end": None, "label": "2023年末"}

LOCATOR = {"type": "页码", "page": 3, "table_ref": None, "text_range": None, "value": "P3"}
SOURCE = {
    "kind": "material",
    "material_id": "MAT-0001",
    "block_id": "BLK-0001",
    "locator": LOCATOR,
    "quote": None,
    "calc_id": None,
}


def make_numeric(
    value=1285.0,
    unit="亿元",
    value_text="1,285.0亿元",
    kind="精确值",
    period=None,
    entity="甲企业",
    consolidation="合并报表",
    accounting="中国企业会计准则",
    comparison=None,
    range_low=None,
    range_high=None,
):
    return {
        "value": value,
        "value_text": value_text,
        "value_kind": kind,
        "range_low": range_low,
        "range_high": range_high,
        "entity": entity,
        "unit": {"code": unit, "other": None},
        "period": period or dict(PERIOD_2024),
        "basis": {"consolidation": consolidation, "accounting": accounting, "note": None},
        "source": dict(SOURCE),
        "comparison": comparison,
    }


def make_claim(**overrides):
    claim = {
        "claim_id": "CLM-0001",
        "task_id": "TSK-0001",
        "report_material_id": "MAT-0001",
        "claim_text": "甲企业2024年度营业收入1,285.0亿元",
        "claim_type": "数值型",
        "judgment_type": "事实",
        "numeric": make_numeric(),
        "reference": None,
        "unknown_fields": [],
        "report_locator": dict(LOCATOR),
        "status": "待核验",
    }
    claim.update(overrides)
    return claim


def make_candidate(
    candidate_id="CND-0001",
    value=1285.0,
    unit="亿元",
    metric="营业收入",
    entity="甲企业",
    period=None,
    material_id="MAT-0002",
    quote=None,
    ocr_band="不适用",
):
    numeric = make_numeric(
        value=value,
        unit=unit,
        value_text=f"{value}{unit}",
        period=period or dict(PERIOD_2024),
        entity=entity,
    )
    return {
        "candidate_id": candidate_id,
        "task_id": "TSK-0001",
        "material_id": material_id,
        "block_id": "BLK-0007",
        "locator": LOCATOR,
        "quote": quote or f"{metric} {value}{unit}",
        "truth_judgment": "未判定",
        "ocr_band": ocr_band,
        "normalized_fact_draft": {"metric": metric, "value": numeric},
    }


def make_calc(
    formula_id="FRM-IND-01",
    output=35.7187,
    expected=42.0,
    diff=6.2813,
    within=False,
    unit="%",
    side=None,
    calc_id="CALC-0001",
):
    params = {}
    if side:
        params["side"] = side
    return {
        "calc_id": calc_id,
        "task_id": "TSK-0001",
        "claim_id": "CLM-0001",
        "formula_id": formula_id,
        "formula_text": "按公式目录执行",
        "inputs": [],
        "output": {
            "value": output,
            "value_text": f"{output}{unit}",
            "entity": "甲企业",
            "unit": {"code": unit, "other": None},
            "period": dict(PERIOD_2024),
            "basis": {"consolidation": "合并报表", "accounting": "unknown", "note": None},
        },
        "tolerance": {"abs": 0.5, "rel": None},
        "result_status": "成功",
        "assumptions": [],
        "boundaries": "容差按展示粒度推导",
        "comparison": {
            "expected_value": expected,
            "diff": diff,
            "within_tolerance": within,
        },
        "params": params,
    }


def make_ctx(candidates=(), calculations=(), calc_error=None, config=None, facts=()):
    return {
        "candidates": list(candidates),
        "facts": list(facts),
        "calculations": list(calculations),
        "calc_error": calc_error,
        "config": config if isinstance(config, dict) else {"data_cutoff": "2024-12-31"},
    }


class RuleRegistryTest(unittest.TestCase):
    def test_registry_has_53_rules(self):
        self.assertEqual(len(RULES), 53)
        expected_domains = {
            "GEN": 9, "NUM": 4, "UNI": 4, "PER": 4, "ENT": 3, "CON": 3,
            "ACC": 4, "CMP": 4, "FRM": 5, "CIT": 5, "TIE": 5, "ASM": 3,
        }
        actual: dict[str, int] = {}
        for rule_id, rule in RULES.items():
            self.assertRegex(rule_id, r"^R-(GEN|NUM|UNI|PER|ENT|CON|ACC|CMP|FRM|CIT|TIE|ASM)-[0-9]{2}$")
            self.assertEqual(set(rule), {"name", "domain", "severity", "implemented"})
            self.assertIn(rule["severity"], ("高", "中", "低"))
            self.assertIsInstance(rule["implemented"], bool)
            actual[rule["domain"]] = actual.get(rule["domain"], 0) + 1
        self.assertEqual(actual, expected_domains)

    def test_norm_text_and_severity(self):
        self.assertEqual(norm_text(" １，２８５．０ 亿元 "), "12850亿元")
        self.assertEqual(norm_text("设备制造。"), norm_text("设备制造"))
        self.assertEqual(severity_of(["R-NUM-01", "R-FRM-04"]), "高")
        self.assertEqual(severity_of([]), "中")


class RuleEngineTest(unittest.TestCase):
    def test_01_missing_locator_is_pending(self):
        claim = make_claim(report_locator=None)
        result = evaluate(claim, make_ctx([make_candidate()], [make_calc()]))
        self.assertEqual(result["status"], "待核验")
        self.assertIn("R-GEN-02", result["rule_ids"])

    def test_02_conflict_precedes_error(self):
        candidates = [
            make_candidate("CND-0001", 1285.0, material_id="MAT-0002"),
            make_candidate("CND-0002", 1302.0, material_id="MAT-0003"),
        ]
        claim = make_claim(numeric=make_numeric(value=1302.0, value_text="1,302.0亿元"))
        result = evaluate(
            claim,
            make_ctx(candidates, [make_calc(expected=1302.0, diff=17.0, within=False)]),
        )
        self.assertEqual(result["status"], "冲突")
        self.assertIn("R-GEN-05", result["rule_ids"])
        self.assertIn("R-GEN-08", result["rule_ids"])
        self.assertTrue(conflict_detected(candidates))

    def test_03_empty_candidates_is_insufficient(self):
        result = evaluate(make_claim(), make_ctx([], [make_calc()]))
        self.assertEqual(result["status"], "证据不足")
        self.assertIn("R-GEN-04", result["rule_ids"])

    def test_04_future_prediction_not_applicable(self):
        claim = make_claim(
            judgment_type="推论",
            numeric=make_numeric(value=1500.0, value_text="1,500.0亿元", period=dict(PERIOD_2026)),
        )
        result = evaluate(claim, make_ctx([], [], config={"data_cutoff": "2024-12-31"}))
        self.assertEqual(result["status"], "不适用")
        self.assertIn("R-ASM-02", result["rule_ids"])

    def test_05_opinion_not_applicable(self):
        claim = make_claim(
            claim_type="定性表述型",
            judgment_type="观点",
            numeric=None,
            claim_text="甲企业管理层优秀，发展前景广阔",
        )
        result = evaluate(claim, make_ctx([make_candidate(quote="甲企业主营业务为XX设备制造")]))
        self.assertEqual(result["status"], "不适用")
        self.assertIn("R-ASM-02", result["rule_ids"])

    def test_06a_qualitative_literal_match_passes(self):
        claim = make_claim(
            claim_type="定性表述型",
            judgment_type="事实",
            numeric=None,
            claim_text="甲企业主营业务为XX设备制造。",
        )
        candidate = make_candidate(
            quote="甲企业主营业务为XX设备制造，覆盖研发、生产与销售。", metric=None
        )
        candidate["normalized_fact_draft"] = None
        result = evaluate(claim, make_ctx([candidate]))
        self.assertEqual(result["status"], "通过")
        self.assertIn("R-GEN-09", result["rule_ids"])

    def test_06b_qualitative_semantic_uncertain_needs_review(self):
        claim = make_claim(
            claim_type="定性表述型",
            judgment_type="事实",
            numeric=None,
            claim_text="甲企业为国内最大XX生产商",
        )
        candidate = make_candidate(quote="甲企业主营业务为XX设备制造", metric=None)
        candidate["normalized_fact_draft"] = None
        result = evaluate(claim, make_ctx([candidate]))
        self.assertEqual(result["status"], "需人工复核")
        self.assertIn("R-GEN-09", result["rule_ids"])

    def test_07_unit_unknown_is_low_confidence(self):
        claim = make_claim(
            numeric=make_numeric(unit="unknown", value_text="1,285.0"),
            unknown_fields=["单位"],
        )
        result = evaluate(claim, make_ctx([make_candidate()], [make_calc(within=True)]))
        self.assertEqual(result["status"], "低置信")
        self.assertIn("R-UNI-02", result["rule_ids"])

    def test_08_magnitude_mismatch_is_error(self):
        claim = make_claim(
            numeric=make_numeric(value=1285.0, unit="万元", value_text="1,285.0万元"),
            claim_text="甲企业2024年度营业收入1,285.0万元",
        )
        result = evaluate(claim, make_ctx([make_candidate(value=1285.0, unit="亿元")], [make_calc()]))
        self.assertEqual(result["status"], "错误")
        self.assertIn("R-UNI-04", result["rule_ids"])

    def test_09_recompute_fail_is_error(self):
        claim = make_claim(
            judgment_type="计算",
            numeric=make_numeric(value=42.0, unit="%", value_text="42%"),
            claim_text="甲企业2024年度毛利率42%",
        )
        candidates = [
            make_candidate("CND-0001", 128499687543.21, unit="元", metric="营业收入"),
            make_candidate("CND-0002", 82601234567.89, unit="元", metric="营业成本"),
        ]
        result = evaluate(
            claim,
            make_ctx(candidates, [make_calc(output=35.7187, expected=42.0, diff=6.2813, within=False)]),
        )
        self.assertEqual(result["status"], "错误")
        for rule_id in ("R-FRM-01", "R-FRM-02", "R-FRM-05"):
            self.assertIn(rule_id, result["rule_ids"])

    def test_10_recompute_pass(self):
        claim = make_claim(
            judgment_type="计算",
            numeric=make_numeric(
                value=12.0,
                unit="%",
                value_text="12%",
                comparison={
                    "type": "同比",
                    "baseline_period": dict(PERIOD_2023),
                    "baseline_value": None,
                    "baseline_source": None,
                },
            ),
            claim_text="甲企业2024年度营业收入同比增长12%",
        )
        candidates = [
            make_candidate("CND-0001", 128499687543.21, unit="元", metric="营业收入", period=dict(PERIOD_2024)),
            make_candidate("CND-0002", 114702345678.90, unit="元", metric="营业收入", period=dict(PERIOD_2023)),
        ]
        calculation = make_calc(
            formula_id="FRM-CMP-01", output=12.0288, expected=12.0, diff=0.0288, within=True
        )
        result = evaluate(claim, make_ctx(candidates, [calculation]))
        self.assertEqual(result["status"], "通过")
        self.assertIn("R-CMP-02", result["rule_ids"])

    def test_11_calc_error_needs_review(self):
        claim = make_claim(
            judgment_type="计算",
            numeric=make_numeric(value=20.0, unit="%", value_text="20%"),
            claim_text="某新业务板块收入较基期增长20%",
        )
        result = evaluate(
            claim,
            make_ctx([make_candidate("CND-0001", 0.0, unit="亿元")], [], calc_error="分母为零：基期值"),
        )
        self.assertEqual(result["status"], "需人工复核")
        self.assertIn("R-FRM-04", result["rule_ids"])
        self.assertIn("分母为零", result["reason"])

    def test_12a_citation_match_passes(self):
        claim = make_claim(
            claim_type="引用型",
            judgment_type="事实",
            numeric=None,
            claim_text="引年报第68页合并利润表营业收入",
            reference={
                "cited_subject": "甲企业",
                "cited_text": "营业收入 128,499,687,543.21 元",
                "cited_locator": {"type": "页码", "page": 68, "table_ref": "合并利润表", "text_range": None, "value": "P68"},
            },
        )
        candidate = make_candidate(quote="营业收入 128,499,687,543.21 元", metric=None)
        candidate["normalized_fact_draft"] = None
        result = evaluate(claim, make_ctx([candidate]))
        self.assertEqual(result["status"], "通过")
        self.assertIn("R-CIT-02", result["rule_ids"])

    def test_12b_citation_mismatch_is_error(self):
        claim = make_claim(
            claim_type="引用型",
            judgment_type="事实",
            numeric=None,
            claim_text="引年报第68页合并利润表营业收入1,342.3亿元",
            reference={
                "cited_subject": "甲企业",
                "cited_text": "营业收入 1,342.3 亿元",
                "cited_locator": {"type": "页码", "page": 68, "table_ref": "合并利润表", "text_range": None, "value": "P68"},
            },
        )
        candidate = make_candidate(quote="营业收入 128,499,687,543.21 元", metric=None)
        candidate["normalized_fact_draft"] = None
        result = evaluate(claim, make_ctx([candidate]))
        self.assertEqual(result["status"], "错误")
        self.assertIn("R-CIT-02", result["rule_ids"])

    def test_13a_tie_over_tolerance_is_error(self):
        claim = make_claim(
            judgment_type="计算",
            numeric=make_numeric(value=100.0, value_text="100.0亿元"),
            claim_text="研报表格资产合计100.0=负债60.0+权益35.0",
        )
        calculation = make_calc(
            formula_id="FRM-TIE-02", output=95.0, expected=100.0, diff=5.0, within=False, side="claim"
        )
        result = evaluate(claim, make_ctx([make_candidate()], [calculation]))
        self.assertEqual(result["status"], "错误")
        self.assertIn("R-TIE-01", result["rule_ids"])
        self.assertIn("R-TIE-05", result["rule_ids"])

    def test_13b_tie_evidence_side_unbalanced_is_conflict(self):
        claim = make_claim(
            judgment_type="计算",
            numeric=make_numeric(value=100.0, value_text="100.0亿元"),
            claim_text="研报表格资产合计100.0=负债60.0+权益35.0",
        )
        calculation = make_calc(
            formula_id="FRM-TIE-02", output=95.0, expected=100.0, diff=5.0, within=False, side="evidence"
        )
        result = evaluate(claim, make_ctx([make_candidate()], [calculation]))
        self.assertEqual(result["status"], "冲突")
        self.assertIn("R-TIE-05", result["rule_ids"])

    def test_14_interval_out_of_range_is_error(self):
        # R-NUM-04 已实现（区间值规则）：区间值不再走「未实现→需人工复核」兜底，
        # 证据点值落在区间外判错误。原「未实现不得通过」断言随规则实现退役。
        claim = make_claim(
            numeric=make_numeric(
                value=None,
                unit="亿元",
                value_text="1,200–1,250亿元",
                kind="区间值",
                range_low=1200.0,
                range_high=1250.0,
            ),
            claim_text="甲企业2024年度营业收入1,200–1,250亿元",
        )
        result = evaluate(
            claim,
            make_ctx([make_candidate(value=1285.0)], [make_calc(within=True)]),
        )
        self.assertEqual(result["status"], "错误")
        self.assertIn("R-NUM-04", result["rule_ids"])

    def test_15_basis_unknown_passes_with_note(self):
        claim = make_claim(
            numeric=make_numeric(accounting="unknown"),
            unknown_fields=["口径"],
        )
        calculation = make_calc(
            formula_id="FRM-NUM-01", output=1284.9968754321, expected=1285.0, diff=0.0031, within=True, unit="亿元"
        )
        result = evaluate(claim, make_ctx([make_candidate()], [calculation]))
        self.assertEqual(result["status"], "通过")
        self.assertIn("口径未核对", result["reason"])
        self.assertEqual([r for r in result["rule_ids"] if r.startswith(("R-CON", "R-ACC"))], [])

    def test_16_ocr_low_block_blocks_pass(self):
        claim = make_claim(
            numeric=make_numeric(value=45.6, value_text="45.6亿元"),
            claim_text="扫描件公告载明2024年度研发投入45.6亿元",
        )
        candidate = make_candidate("CND-0001", 45.6, ocr_band="低", material_id="MAT-0003")
        calculation = make_calc(
            formula_id="FRM-NUM-01", output=45.6, expected=45.6, diff=0.0, within=True, unit="亿元"
        )
        result = evaluate(claim, make_ctx([candidate], [calculation]))
        self.assertEqual(result["status"], "需人工复核")
        self.assertIn("R-GEN-03", result["rule_ids"])

    def test_17_evaluate_is_pure(self):
        claim = make_claim()
        ctx = make_ctx([make_candidate()], [make_calc()])
        snapshot = (dict(claim), dict(ctx), list(ctx["candidates"]), list(ctx["calculations"]))
        evaluate(claim, ctx)
        self.assertEqual(claim, snapshot[0])
        self.assertEqual(ctx, snapshot[1])
        self.assertEqual(ctx["candidates"], snapshot[2])
        self.assertEqual(ctx["calculations"], snapshot[3])


def make_input(role="营业收入", value=1285.0, unit="亿元", with_source=True):
    numeric = make_numeric(value=value, unit=unit, value_text=f"{value}{unit}", period=dict(PERIOD_2024))
    if not with_source:
        numeric["source"] = None
    return {"role": role, "value": numeric}


def with_basis(candidate, consolidation=None, accounting=None, note=None):
    """覆盖候选证据的 basis（口径专项检查用）。"""
    basis = candidate["normalized_fact_draft"]["value"]["basis"]
    if consolidation is not None:
        basis["consolidation"] = consolidation
    if accounting is not None:
        basis["accounting"] = accounting
    if note is not None:
        basis["note"] = note
    return candidate


class NewRulesTest(unittest.TestCase):
    """新增 23 条规则：正例、反例与无法判定出口（按域规则明细）。"""

    def _assert(self, result, status, rule_id):
        self.assertIn(result["status"], STATUS_VALUES)
        self.assertEqual(result["status"], status, msg=result["reason"])
        self.assertIn(rule_id, result["rule_ids"], msg=result["reason"])

    # -- R-GEN-01 / R-GEN-06 / R-GEN-07（过程性元规则） ---------------------
    def test_gen01_status_exit_constraint(self):
        self.assertIsNone(check_status_exit("错误"))
        self.assertIsNone(check_status_exit("待核验"))
        violation = check_status_exit("基本属实")
        self._assert(violation, "需人工复核", "R-GEN-01")
        self.assertIn("八值", violation["reason"])
        result = evaluate(make_claim(), make_ctx([make_candidate()]))
        self.assertIn(result["status"], STATUS_VALUES)

    def test_gen06_calc_routing_and_llm_ban(self):
        claim = make_claim(
            judgment_type="计算", numeric=make_numeric(value=42.0, unit="%", value_text="42%")
        )
        self.assertIsNone(check_calc_routing(claim, [make_calc()]))
        llm_calc = make_calc()
        llm_calc["executed_by"] = "LLM"
        self._assert(check_calc_routing(claim, [llm_calc]), "需人工复核", "R-GEN-06")
        # 需复算却无任何 Calculation 记录（LLM 口算）→ 不得替代
        self._assert(check_calc_routing(claim, []), "需人工复核", "R-GEN-06")
        # 纯事实型且无需换算 → 不触发
        self.assertIsNone(check_calc_routing(make_claim(), []))

    def test_gen07_judgment_record(self):
        result = evaluate(make_claim(), make_ctx([make_candidate()]))
        self.assertIsNone(check_judgment_record(result))
        broken = dict(result)
        broken["reason"] = ""
        self._assert(check_judgment_record(broken), "需人工复核", "R-GEN-07")
        broken["reason"] = "有理由"
        broken["status"] = "基本属实"
        self._assert(check_judgment_record(broken), "需人工复核", "R-GEN-07")

    # -- R-NUM-04 区间值 ---------------------------------------------------
    def test_num04_interval_value(self):
        def claim_range(low, high, text="甲企业2024年度营业收入1,200–1,250亿元",
                        value_text="1,200–1,250亿元"):
            return make_claim(
                numeric=make_numeric(
                    value=None, unit="亿元", value_text=value_text, kind="区间值",
                    range_low=low, range_high=high,
                ),
                claim_text=text,
            )

        inside = evaluate(
            claim_range(1200.0, 1250.0), make_ctx([make_candidate(value=1240.0)])
        )
        self._assert(inside, "通过", "R-NUM-04")
        outside = evaluate(
            claim_range(1200.0, 1250.0), make_ctx([make_candidate(value=1285.0)])
        )
        self._assert(outside, "错误", "R-NUM-04")
        # 证据侧为区间：重叠即通过（不重叠判错）
        overlap = make_candidate(value=None)
        overlap["normalized_fact_draft"]["value"].update({
            "value": None, "value_kind": "区间值", "range_low": 1240.0,
            "range_high": 1300.0, "value_text": "1,240–1,300亿元",
        })
        self._assert(
            evaluate(claim_range(1200.0, 1250.0), make_ctx([overlap])), "通过", "R-NUM-04"
        )
        detached = make_candidate(value=None)
        detached["normalized_fact_draft"]["value"].update({
            "value": None, "value_kind": "区间值", "range_low": 1300.0,
            "range_high": 1350.0, "value_text": "1,300–1,350亿元",
        })
        self._assert(
            evaluate(claim_range(1200.0, 1250.0), make_ctx([detached])), "错误", "R-NUM-04"
        )
        # 边界异常 range_low≥range_high → 需人工复核
        self._assert(
            evaluate(claim_range(1250.0, 1200.0), make_ctx([make_candidate()])),
            "需人工复核", "R-NUM-04",
        )
        # 区间口径不清（上下界不可解析）→ 需人工复核
        unclear = make_claim(
            numeric=make_numeric(
                value=None, unit="亿元", value_text="约1,200亿元左右", kind="区间值"
            ),
            claim_text="甲企业2024年度营业收入1,200亿元左右",
        )
        self._assert(evaluate(unclear, make_ctx([make_candidate()])), "需人工复核", "R-NUM-04")

    # -- R-PER-01 期间一致性 ----------------------------------------------
    def test_per01_period_consistency(self):
        claim = make_claim(numeric=make_numeric(period=dict(PERIOD_2024)))
        self._assert(
            evaluate(claim, make_ctx([make_candidate(value=1285.0, period=dict(PERIOD_2023))])),
            "错误", "R-PER-01",
        )
        # label 差异但 start/end 相同 → 以 start/end 为准不判错
        relabeled = make_candidate(value=1285.0)
        relabeled["normalized_fact_draft"]["value"]["period"] = {
            "kind": "期间", "start": "2024-01-01", "end": "2024-12-31",
            "point": None, "label": "FY2024",
        }
        self._assert(evaluate(claim, make_ctx([relabeled])), "通过", "R-PER-01")
        # 时点不一致 → 错误
        point_claim = make_claim(numeric=make_numeric(period=dict(POINT_2024)))
        self._assert(
            evaluate(point_claim, make_ctx([make_candidate(value=1285.0, period=dict(POINT_2023))])),
            "错误", "R-PER-01",
        )

    # -- R-PER-03 财年与日历年度 ------------------------------------------
    def test_per03_fiscal_year(self):
        fiscal = {
            "kind": "期间", "start": "2024-01-01", "end": "2024-12-31",
            "point": None, "label": "2024财年",
        }
        claim = make_claim(
            numeric=make_numeric(period=fiscal), claim_text="甲企业2024财年营业收入1,285.0亿元"
        )
        self._assert(evaluate(claim, make_ctx([make_candidate(value=1285.0)])), "通过", "R-PER-03")
        self._assert(
            evaluate(claim, make_ctx([make_candidate(value=1285.0, period=dict(PERIOD_2023))])),
            "错误", "R-PER-03",
        )
        # label 与 start/end 自相矛盾 → 需人工复核
        contradictory = {
            "kind": "期间", "start": "2023-01-01", "end": "2023-12-31",
            "point": None, "label": "2024财年",
        }
        mismatched = make_claim(
            numeric=make_numeric(period=contradictory),
            claim_text="甲企业2024财年营业收入1,285.0亿元",
        )
        self._assert(
            evaluate(mismatched, make_ctx([make_candidate(value=1285.0)])),
            "需人工复核", "R-PER-03",
        )
        # 表述歧义：主张仅写「2024年」，证据侧日历年与财年并存 → 需人工复核
        ambiguous = make_claim(
            numeric=make_numeric(period={
                "kind": "期间", "start": "2024-01-01", "end": "2024-12-31",
                "point": None, "label": "2024年",
            }),
            claim_text="甲企业2024年营业收入1,285.0亿元",
        )
        calendar = make_candidate("CND-0001", 1285.0)
        fiscal_row = make_candidate("CND-0002", 1285.0)
        fiscal_row["normalized_fact_draft"]["value"]["period"] = {
            "kind": "期间", "start": "2023-04-01", "end": "2024-03-31",
            "point": None, "label": "2024财年",
        }
        self._assert(
            evaluate(ambiguous, make_ctx([calendar, fiscal_row])), "需人工复核", "R-PER-03"
        )

    # -- R-ENT-01 实体一致性 ----------------------------------------------
    def test_ent01_entity_consistency(self):
        claim = make_claim()
        self._assert(evaluate(claim, make_ctx([make_candidate(value=1285.0)])), "通过", "R-ENT-01")
        self._assert(
            evaluate(claim, make_ctx([make_candidate(value=1285.0, entity="乙企业")])),
            "错误", "R-ENT-01",
        )
        # 证据侧实体未声明 → 词汇表/实体缺失，需人工复核
        self._assert(
            evaluate(claim, make_ctx([make_candidate(value=1285.0, entity="unknown")])),
            "需人工复核", "R-ENT-01",
        )

    # -- R-CON-01 / R-CON-03 合并口径 -------------------------------------
    def test_con01_con03_consolidation(self):
        # 研报母公司单体 对 证据合并报表，未声明转换 → 错误
        merged_candidate = make_candidate(value=1285.0)
        claim = make_claim(
            numeric=make_numeric(consolidation="母公司报表"),
            claim_text="甲企业2024年度母公司营业收入1,285.0亿元",
        )
        self._assert(evaluate(claim, make_ctx([merged_candidate])), "错误", "R-CON-01")
        # 声明桥接 → 需人工复核（桥接验证属解释）
        bridged_claim = make_claim(
            numeric=make_numeric(consolidation="母公司报表"),
            claim_text="甲企业2024年度母公司营业收入1,285.0亿元",
        )
        bridged_claim["numeric"]["basis"]["note"] = "母公司加总抵消内部交易后与合并数一致"
        self._assert(evaluate(bridged_claim, make_ctx([merged_candidate])),
                     "需人工复核", "R-CON-01")
        # 分部口径 对 合并合计，无同口径证据、无桥接 → 不适用（口径不可比）
        segment = make_claim(
            numeric=make_numeric(consolidation="分部口径"),
            claim_text="甲企业2024年度分板块营业收入1,285.0亿元",
        )
        self._assert(
            evaluate(segment, make_ctx([make_candidate(value=1285.0)])), "不适用", "R-CON-03"
        )

    # -- R-ACC-01 / R-ACC-02 会计口径 -------------------------------------
    def test_acc01_accounting_standard(self):
        claim = make_claim(
            numeric=make_numeric(accounting="IFRS"),
            claim_text="甲企业2024年度IFRS口径营业收入1,285.0亿元",
        )
        standards = with_basis(make_candidate(value=1285.0), accounting="中国企业会计准则")
        self._assert(evaluate(claim, make_ctx([standards])), "错误", "R-ACC-01")
        # 检索不到同准则证据 → 证据不足
        self._assert(
            evaluate(claim, make_ctx([with_basis(make_candidate(value=1285.0), accounting="unknown")])),
            "证据不足", "R-ACC-01",
        )

    def test_acc02_managed_basis(self):
        claim = make_claim(
            numeric=make_numeric(accounting="管理口径"),
            claim_text="甲企业2024年度营业收入1,285.0亿元",
        )
        # note 为空 → 低置信
        self._assert(evaluate(claim, make_ctx([make_candidate(value=1285.0)])), "低置信", "R-ACC-02")
        # note 齐备但仅有法定披露证据 → 需人工复核（不得直接判错或判过）
        bridged = make_claim(
            numeric=make_numeric(accounting="管理口径"),
            claim_text="甲企业2024年度营业收入1,285.0亿元",
        )
        bridged["numeric"]["basis"]["note"] = "加回股权激励费用与一次性重组费用"
        self._assert(evaluate(bridged, make_ctx([make_candidate(value=1285.0)])),
                     "需人工复核", "R-ACC-02")

    # -- R-CMP-01 比较类型与基期关系 --------------------------------------
    def test_cmp01_type_and_baseline_relation(self):
        comparison = {
            "type": "同比", "baseline_period": dict(PERIOD_2023),
            "baseline_value": None, "baseline_source": None,
        }
        claim = make_claim(
            judgment_type="计算",
            numeric=make_numeric(value=12.0, unit="%", value_text="12%", comparison=dict(comparison)),
            claim_text="甲企业2024年度营业收入同比增长12%",
        )
        candidates = [
            make_candidate("CND-0001", 128499687543.21, unit="元",
                           metric="营业收入", period=dict(PERIOD_2024)),
            make_candidate("CND-0002", 114702345678.90, unit="元",
                           metric="营业收入", period=dict(PERIOD_2023)),
        ]
        result = evaluate(
            claim,
            make_ctx(candidates, [make_calc(formula_id="FRM-CMP-01", output=12.0288,
                                            expected=12.0, diff=0.0288, within=True)]),
        )
        self._assert(result, "通过", "R-CMP-01")
        # 同比却取上一季度基期 → 错误
        wrong = dict(comparison)
        wrong["baseline_period"] = dict(PERIOD_2024_Q3)
        wrong_claim = make_claim(
            judgment_type="计算",
            numeric=make_numeric(value=12.0, unit="%", value_text="12%", comparison=wrong),
            claim_text="甲企业2024年度营业收入同比增长12%",
        )
        self._assert(
            evaluate(wrong_claim, make_ctx([make_candidate()], [make_calc(formula_id="FRM-CMP-01")])),
            "错误", "R-CMP-01",
        )
        # type 与文本表述冲突 → 语义无法匹配，需人工复核
        mixed = make_claim(
            judgment_type="计算",
            numeric=make_numeric(value=12.0, unit="%", value_text="12%", comparison=dict(comparison)),
            claim_text="甲企业2024年度营业收入环比增长12%",
        )
        self._assert(
            evaluate(mixed, make_ctx([make_candidate()], [make_calc(formula_id="FRM-CMP-01")])),
            "需人工复核", "R-CMP-01",
        )

    # -- R-CMP-03 基期缺失与不可推导 --------------------------------------
    def test_cmp03_baseline_missing(self):
        comparison = {
            "type": "同比", "baseline_period": dict(PERIOD_2023),
            "baseline_value": None, "baseline_source": None,
        }
        claim = make_claim(
            judgment_type="计算",
            numeric=make_numeric(value=12.0, unit="%", value_text="12%", comparison=comparison),
            claim_text="甲企业2024年度营业收入同比增长12%",
        )
        # ② 基期可推导但证据侧无基期值 → 证据不足
        self._assert(
            evaluate(claim, make_ctx([make_candidate(value=1285.0)])), "证据不足", "R-CMP-03"
        )
        # ③ 基期不可推导 → 需人工复核
        other = {
            "type": "其他", "baseline_period": {"kind": "unknown"},
            "baseline_value": None, "baseline_source": None,
        }
        unknown_claim = make_claim(
            judgment_type="计算",
            numeric=make_numeric(value=12.0, unit="%", value_text="12%", comparison=other),
            claim_text="甲企业2024年度营业收入增长12%",
        )
        self._assert(
            evaluate(unknown_claim, make_ctx([make_candidate()])), "需人工复核", "R-CMP-03"
        )
        # ① claim 侧 baseline_value/baseline_source 为 null 不阻断复算（字段契约）
        self.assertIsNone(comparison["baseline_value"])
        annual = [
            make_candidate("CND-0001", 128499687543.21, unit="元",
                           metric="营业收入", period=dict(PERIOD_2024)),
            make_candidate("CND-0002", 114702345678.90, unit="元",
                           metric="营业收入", period=dict(PERIOD_2023)),
        ]
        cmp_calc = make_calc(formula_id="FRM-CMP-01", output=12.0288,
                             expected=12.0, diff=0.0288, within=True)
        recovered = evaluate(claim, make_ctx(annual, [cmp_calc]))
        self._assert(recovered, "通过", "R-CMP-02")
        self.assertNotIn("R-CMP-03", recovered["rule_ids"])

    # -- R-CMP-04 百分点与百分比混用 --------------------------------------
    def test_cmp04_percentage_point(self):
        claim = make_claim(
            judgment_type="计算",
            numeric=make_numeric(value=12.0, unit="%", value_text="12%"),
            claim_text="甲企业2024年度毛利率提高12个百分点",
        )
        self._assert(
            evaluate(claim, make_ctx([make_candidate()], [make_calc(within=True)])),
            "错误", "R-CMP-04",
        )
        reverse = make_claim(
            judgment_type="计算",
            numeric=make_numeric(value=12.0, unit="百分点", value_text="12个百分点"),
            claim_text="甲企业2024年度毛利率提高12%",
        )
        self._assert(
            evaluate(reverse, make_ctx([make_candidate()], [make_calc(within=True)])),
            "错误", "R-CMP-04",
        )
        unclear = make_claim(
            judgment_type="计算",
            numeric=make_numeric(value=12.0, unit="亿元", value_text="12亿元"),
            claim_text="甲企业2024年度毛利率提高12个百分点",
        )
        self._assert(
            evaluate(unclear, make_ctx([make_candidate()], [make_calc(within=True)])),
            "需人工复核", "R-CMP-04",
        )
        # 反例：普通百分比表述不触发
        plain = evaluate(
            make_claim(),
            make_ctx([make_candidate()], [make_calc(formula_id="FRM-NUM-01", output=1285.0,
                                                   expected=1285.0, diff=0.0, within=True,
                                                   unit="亿元")]),
        )
        self.assertEqual(plain["status"], "通过")
        self.assertNotIn("R-CMP-04", plain["rule_ids"])

    # -- R-FRM-03 公式输入齐备性 ------------------------------------------
    def test_frm03_formula_inputs(self):
        claim = make_claim(
            judgment_type="计算",
            numeric=make_numeric(value=42.0, unit="%", value_text="42%"),
            claim_text="甲企业2024年度毛利率42%",
        )
        complete = make_calc(within=True)
        complete["inputs"] = [make_input("营业收入"), make_input("营业成本")]
        self.assertEqual(evaluate(claim, make_ctx([make_candidate()], [complete]))["status"], "通过")
        broken = make_calc(within=True)
        broken["inputs"] = [make_input("基期值", with_source=False)]
        self._assert(
            evaluate(claim, make_ctx([make_candidate()], [broken])), "证据不足", "R-FRM-03"
        )
        unknown_unit = make_calc(within=True)
        unknown_unit["inputs"] = [make_input("基期值", unit="unknown")]
        self._assert(
            evaluate(claim, make_ctx([make_candidate()], [unknown_unit])), "低置信", "R-FRM-03"
        )

    # -- R-CIT-03 / R-CIT-04 / R-CIT-05 引用类 ----------------------------
    def _citation_claim(self, subject="甲企业", entity="甲企业", text="营业收入 128,499,687,543.21 元"):
        return make_claim(
            claim_type="引用型",
            judgment_type="事实",
            numeric=make_numeric(entity=entity),
            claim_text="引年报第68页合并利润表营业收入",
            reference={
                "cited_subject": subject,
                "cited_text": text,
                "cited_locator": {
                    "type": "页码", "page": 68, "table_ref": "合并利润表",
                    "text_range": None, "value": "P68",
                },
            },
        )

    def test_cit03_cited_subject_consistency(self):
        candidate = make_candidate(quote="营业收入 128,499,687,543.21 元", metric=None)
        candidate["normalized_fact_draft"] = None
        self._assert(
            evaluate(self._citation_claim(), make_ctx([candidate])), "通过", "R-CIT-02"
        )
        self._assert(
            evaluate(self._citation_claim(subject="乙企业"), make_ctx([candidate])),
            "错误", "R-CIT-03",
        )
        # 指代型主体 → 语义不清，需人工复核
        self._assert(
            evaluate(self._citation_claim(subject="该公司"), make_ctx([candidate])),
            "需人工复核", "R-CIT-03",
        )

    def test_cit04_cited_material_status(self):
        claim = make_claim(
            claim_type="引用型", judgment_type="事实", numeric=None,
            claim_text="引公告第2页研发投入",
            reference={
                "cited_subject": "甲企业",
                "cited_text": "研发投入 45.6 亿元",
                "cited_material_id": "MAT-0003",
                "cited_locator": {"type": "页码", "page": 2, "table_ref": None,
                                  "text_range": None, "value": "P2"},
            },
        )
        candidate = make_candidate(quote="研发投入 45.6 亿元", metric=None, material_id="MAT-0003")
        candidate["normalized_fact_draft"] = None
        ctx = make_ctx([candidate])
        ctx["materials"] = [{"material_id": "MAT-0003", "status": "解析失败"}]
        self._assert(evaluate(claim, ctx), "证据不足", "R-CIT-04")
        ctx["materials"] = [{"material_id": "MAT-0003", "status": "已登记"}]
        result = evaluate(claim, ctx)
        self.assertEqual(result["status"], "通过")
        self.assertNotIn("R-CIT-04", result["rule_ids"])

    def test_cit05_citation_evidence_requirement(self):
        claim = make_claim(
            claim_type="引用型", judgment_type="事实", numeric=None,
            claim_text="引本简报第3页营业收入",
            reference={
                "cited_subject": "甲企业",
                "cited_text": "营业收入 1,285.0 亿元",
                "cited_locator": {"type": "页码", "page": 3, "table_ref": None,
                                  "text_range": None, "value": "P3"},
            },
        )
        own_material = make_candidate(
            quote="营业收入 1,285.0 亿元", metric=None, material_id="MAT-0001"
        )
        own_material["normalized_fact_draft"] = None
        self._assert(evaluate(claim, make_ctx([own_material])), "证据不足", "R-CIT-05")
        cited_material = make_candidate(
            quote="营业收入 1,285.0 亿元", metric=None, material_id="MAT-0002"
        )
        cited_material["normalized_fact_draft"] = None
        self._assert(evaluate(claim, make_ctx([cited_material])), "通过", "R-CIT-02")

    # -- R-TIE-02 / R-TIE-03 / R-TIE-04 勾稽 ------------------------------
    def _tie_claim(self, text="研报表格勾稽核对"):
        return make_claim(
            judgment_type="计算",
            numeric=make_numeric(value=100.0, value_text="100.0亿元"),
            claim_text=text,
        )

    def test_tie02_cross_table(self):
        calc = make_calc(formula_id="FRM-TIE-02", output=95.0, expected=100.0,
                         diff=5.0, within=False, side="claim")
        calc["params"]["tie_scope"] = "跨表"
        result = evaluate(self._tie_claim(), make_ctx([make_candidate()], [calc]))
        self._assert(result, "错误", "R-TIE-02")
        self.assertNotIn("R-TIE-01", result["rule_ids"])
        evidence_side = make_calc(formula_id="FRM-TIE-02", output=95.0, expected=100.0,
                                 diff=5.0, within=False, side="evidence")
        evidence_side["params"]["tie_scope"] = "跨表"
        self._assert(
            evaluate(self._tie_claim(), make_ctx([make_candidate()], [evidence_side])),
            "冲突", "R-TIE-02",
        )
        # 未声明范围时按输入的（材料、表格）组合推断为跨表
        inferred = make_calc(formula_id="FRM-TIE-02", output=95.0, expected=100.0,
                            diff=5.0, within=False, side="claim")
        first = make_input("本期净利润", value=100.0)
        second = make_input("未分配利润变动", value=95.0)
        second["value"]["source"]["material_id"] = "MAT-0003"
        inferred["inputs"] = [first, second]
        self._assert(
            evaluate(self._tie_claim(), make_ctx([make_candidate()], [inferred])),
            "错误", "R-TIE-02",
        )

    def test_tie03_cross_period_and_restatement(self):
        calc = make_calc(formula_id="FRM-TIE-02", output=210.0, expected=220.0,
                         diff=10.0, within=False, side="claim")
        calc["params"]["tie_scope"] = "跨期"
        claim = self._tie_claim("研报存货期初加变动等于期末")
        self._assert(evaluate(claim, make_ctx([make_candidate()], [calc])), "错误", "R-TIE-03")
        restated = self._tie_claim("研报存货期初加变动等于期末")
        restated["numeric"]["basis"]["note"] = "上期数据经重述"
        self._assert(
            evaluate(restated, make_ctx([make_candidate()], [calc])),
            "需人工复核", "R-TIE-03",
        )

    def test_tie04_tolerance_source(self):
        calc = make_calc(formula_id="FRM-TIE-02", within=False, side="claim")
        self.assertIsNone(check_tie_tolerance(calc))
        calc["tolerance"] = {"abs": None, "rel": None}
        self._assert(check_tie_tolerance(calc), "需人工复核", "R-TIE-04")
        self._assert(
            evaluate(self._tie_claim(), make_ctx([make_candidate()], [calc])),
            "需人工复核", "R-TIE-04",
        )

    # -- R-ASM-01 / R-ASM-03 估值假设 -------------------------------------
    def test_asm01_asm03_valuation_assumptions(self):
        claim = make_claim(
            judgment_type="推论",
            numeric=make_numeric(value=20.0, unit="倍", value_text="20倍"),
            claim_text="给予甲企业目标价对应市盈率20倍，合理估值120亿元",
        )
        missing = evaluate(claim, make_ctx([make_candidate()]))
        self._assert(missing, "需人工复核", "R-ASM-01")
        self.assertIn("R-ASM-03", missing["rule_ids"])
        documented_calc = make_calc(within=True)
        documented_calc["assumptions"] = ["永续增长率 3%"]
        documented_calc["boundaries"] = "适用于主业内生增长情形"
        documented = evaluate(claim, make_ctx([make_candidate()], [documented_calc]))
        self._assert(documented, "需人工复核", "R-ASM-03")
        self.assertNotIn("R-ASM-01", documented["rule_ids"])
        # 非估值主张不触发扩展范围出口
        plain = evaluate(make_claim(), make_ctx([make_candidate()]))
        self.assertEqual(plain["status"], "通过")
        self.assertNotIn("R-ASM-03", plain["rule_ids"])


if __name__ == "__main__":
    unittest.main()
