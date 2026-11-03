"""生成 GS-C 规模评测集（240 条构造性金标准）。

原则：金标准由**构造规则独立推导**（不调用规则引擎），避免自我循环。
六类家族（各 40 条）：
  A 正确值：同指标不同展示粒度/口径内取值 → 通过
  B 数值偏差：ev×(1+δ) → 错误（R-NUM-01）
  C 标度错配：同数字换成 万元 档 → 错误（R-UNI-04）
  D 单位缺失 → 低置信（R-UNI-02）
  E 约数：按取整粒度推定，超出 → 需人工复核（R-NUM-03）
  F 指标复算：毛利率 Y → 通过/错误（R-FRM-01/02/05）

运行：python tools/gen_benchmark.py
"""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SRC = BASE / "fixtures" / "demo01"
DST = BASE / "fixtures" / "bench"

# 证据值（与 DEMO-01 年报一致，单位：亿元）
METRICS = {
    "营业收入": Decimal("1284.9968754321"),
    "营业成本": Decimal("826.0123456789"),
    "净利润": Decimal("128.4996875432"),
    "资产合计": Decimal("2000.0"),
    "负债合计": Decimal("1200.0"),
    "存货": Decimal("220.0"),
}

PERIOD = {"kind": "期间", "point": None, "start": "2024-01-01",
          "end": "2024-12-31", "label": "2024年度"}
PERIOD_POINT = {"kind": "时点", "point": "2024-12-31", "start": None,
                "end": None, "label": "2024-12-31"}
POINT_METRICS = {"资产合计", "负债合计", "存货"}


def _claim(cid: str, text: str, claim_type: str, judgment: str,
           value: Decimal | None = None, unit: str = "", value_text: str = "",
           metric: str | None = None, reference: dict | None = None,
           value_kind: str = "精确值") -> dict:
    numeric = None
    if value is not None:
        period = PERIOD_POINT if metric in POINT_METRICS else PERIOD
        numeric = {
            "value": float(value), "value_text": value_text or f"{value}{unit}",
            "value_kind": value_kind, "range_low": None, "range_high": None,
            "entity": "甲企业", "unit": {"code": unit or "unknown", "other": None},
            "period": dict(period),
            "basis": {"consolidation": "合并报表", "accounting": "unknown", "note": None},
            "source": {"kind": "material", "material_id": "MAT-0001",
                       "block_id": None, "locator": None, "quote": None, "calc_id": None},
            "comparison": None,
        }
        if metric:
            numeric["metric"] = metric
    return {
        "claim": {
            "claim_id": cid, "task_id": "TSK-BENCH",
            "contract_version": "finaudit-data-contract-v1",
            "report_material_id": "MAT-0001",
            "report_locator": {"type": "页码", "page": 3, "table_ref": None,
                               "text_range": None, "value": "P3"},
            "claim_text": text, "context_quote": text,
            "claim_type": claim_type, "judgment_type": judgment,
            "numeric": numeric, "reference": reference, "unknown_fields": [],
            "status": "待核验",
        },
        "gt_status": None, "gt_rule_ids": [],
    }


def build() -> list[dict]:
    out: list[dict] = []
    idx = 0

    def add(text, ctype, judgment, gt, rules, **kw):
        nonlocal idx
        idx += 1
        cid = f"CLM-{idx:04d}"
        item = _claim(cid, text, ctype, judgment, **kw)
        item["gt_status"] = gt
        item["gt_rule_ids"] = rules
        out.append(item)

    # A 正确值（40）：6 指标 × 粒度变体
    places_cycle = [0, 1, 2, 3, 4, 1, 2, 0, 3, 4]
    for i in range(40):
        metric = list(METRICS)[i % 6]
        ev = METRICS[metric]
        places = places_cycle[i % len(places_cycle)]
        value = ev.quantize(Decimal(1).scaleb(-places))
        prefix = "2024年末" if metric in POINT_METRICS else "2024年度"
        text = f"甲企业{prefix}{metric}为{format(value, ',')}亿元"
        add(text, "数值型", "事实", "通过", ["R-NUM-01"],
            value=value, unit="亿元", metric=metric,
            value_text=f"{format(value, ',')}亿元")

    # B 数值偏差（40）：δ ∈ {0.5%,1%,2%,5%,10%,20%,-1%,-5%}
    deltas = ["0.005", "0.01", "0.02", "0.05", "0.1", "0.2", "-0.01", "-0.05"]
    for i in range(40):
        metric = list(METRICS)[i % 6]
        ev = METRICS[metric]
        delta = Decimal(deltas[i % len(deltas)])
        value = (ev * (1 + delta)).quantize(Decimal("0.01"))
        prefix = "2024年末" if metric in POINT_METRICS else "2024年度"
        text = f"甲企业{prefix}{metric}为{format(value, ',')}亿元"
        add(text, "数值型", "事实", "错误", ["R-NUM-01"],
            value=value, unit="亿元", metric=metric,
            value_text=f"{format(value, ',')}亿元")

    # C 标度错配（40）：同数字用 万元 表示
    for i in range(40):
        metric = list(METRICS)[i % 6]
        ev = METRICS[metric]
        digits = ev.quantize(Decimal("0.1"))
        prefix = "2024年末" if metric in POINT_METRICS else "2024年度"
        text = f"甲企业{prefix}{metric}为{format(digits, ',')}万元"
        add(text, "数值型", "事实", "错误", ["R-UNI-04"],
            value=digits, unit="万元", metric=metric,
            value_text=f"{format(digits, ',')}万元")

    # D 单位缺失（40）
    for i in range(40):
        metric = list(METRICS)[i % 6]
        ev = METRICS[metric]
        digits = ev.quantize(Decimal("0.1"))
        prefix = "2024年末" if metric in POINT_METRICS else "2024年度"
        text = f"甲企业{prefix}{metric}为{format(digits, ',')}（原文未标注单位）"
        add(text, "数值型", "事实", "低置信", ["R-UNI-02"],
            value=digits, unit="unknown", metric=metric,
            value_text=f"{format(digits, ',')}")

    # E 约数（40）：粒度 g ∈ {1,10,100,1000}，粒度内 → 通过；超出 → 需人工复核
    for i in range(40):
        metric = list(METRICS)[i % 6]
        ev = METRICS[metric]
        g = [1, 10, 100, 1000][i % 4]
        step = Decimal(g)
        rounded = (ev / step).quantize(Decimal("1")) * step
        if rounded == 0:  # 退化：取整到 0 会丢失粒度语义，降一档
            step = Decimal(10)
            rounded = (ev / step).quantize(Decimal("1")) * step
        diff = abs(rounded - ev)
        gt = "通过" if diff <= step / 2 else "需人工复核"
        prefix = "2024年末" if metric in POINT_METRICS else "2024年度"
        text = f"甲企业{prefix}{metric}约{format(rounded, ',')}亿元"
        add(text, "数值型", "事实", gt, ["R-NUM-03"],
            value=rounded, unit="亿元", metric=metric,
            value_text=f"约{format(rounded, ',')}亿元", value_kind="约数")

    # F 毛利率复算（40）：Y 与 35.718727 的差 vs 容差（Y 的展示粒度的半个末位）
    targets = ["35.718727", "35.72", "35.7", "35.7187", "36", "35", "40", "42",
               "35.5", "34.5"]
    EV_GM = Decimal("35.7187273")
    for i in range(40):
        raw = targets[i % len(targets)]
        y = Decimal(raw)
        places = len(raw.split(".")[1]) if "." in raw else 0
        tol = Decimal(1).scaleb(-places) / 2
        diff = abs(y - EV_GM)
        gt = "通过" if diff <= tol else "错误"
        text = f"甲企业2024年度毛利率为{raw}%"
        add(text, "数值型", "计算", gt, ["R-FRM-01", "R-FRM-02", "R-FRM-05"],
            value=y, unit="%", metric="毛利率", value_text=f"{raw}%")

    # G 冲突（10）：断言公告口径值 1,302.0 亿元（与审计年报并存 → 冲突）
    phrasings = ["营业收入为{值}亿元", "营业收入达{值}亿元", "2024年度营业收入{值}亿元",
                 "营业收入录得{值}亿元", "全年营业收入为{值}亿元"]
    for i in range(10):
        text = "甲企业2024年度" + phrasings[i % len(phrasings)].format(值="1,302.0")
        add(text, "数值型", "事实", "冲突", ["R-GEN-05", "R-GEN-08"],
            value=Decimal("1302.0"), unit="亿元", metric="营业收入",
            value_text="1,302.0亿元")

    # H 引用（10）：5 条引用正确（按档位归一一致）+ 5 条引用错误
    for i in range(10):
        if i % 2 == 0:
            text = ("据甲企业2024年年度报告第68页合并利润表，甲企业2024年度营业收入为"
                    "1,284.9968754321亿元")
            gt, rules = "通过", ["R-CIT-02"]
            cited = "2024年度营业收入1,284.9968754321亿元"
        else:
            text = ("据甲企业2024年年度报告第68页合并利润表，甲企业2024年度营业收入为"
                    f"1,3{42 + i}.3亿元")
            gt, rules = "错误", ["R-CIT-02"]
            cited = f"2024年度营业收入1,3{42 + i}.3亿元"
        add(text, "引用型", "事实", gt, rules,
            reference={"cited_subject": "甲企业", "cited_text": cited,
                       "cited_locator": {"type": "表格坐标", "page": 68,
                                         "table_ref": "P68-表1", "text_range": None,
                                         "value": "P68-表1"}})

    # I 勾稽（10）：研报表1 资产=负债+权益，各行取不同错值 → 错误
    for i in range(10):
        asset = 100 + i
        text = (f"甲企业表1列示资产合计{asset}.0亿元、负债合计60.0亿元、"
                "所有者权益合计35.0亿元，资产合计等于负债合计加所有者权益合计")
        add(text, "数值型", "计算", "错误", ["R-TIE-01", "R-TIE-05"],
            value=Decimal(f"{asset}.0"), unit="亿元", metric="资产合计",
            value_text=f"{asset}.0亿元")

    # J 会计解释（10）：涉会计政策/估计变更 → 需人工复核（R-ACC-03）
    acc_texts = [
        "甲企业自2024年度起变更收入确认政策，变更后毛利率提升",
        "甲企业调整会计估计后净利润增加",
        "甲企业重述2023年度营业收入",
        "甲企业变更合并范围，营业收入口径调整",
        "甲企业将研发支出资本化，利润改善",
        "甲企业计提大额存货跌价准备",
        "甲企业按经调整口径披露毛利率",
        "甲企业会计政策变更影响期初未分配利润",
        "甲企业收入确认时点由交付改为验收",
        "甲企业改变折旧年限，成本下降",
    ]
    for text in acc_texts:
        add(text, "定性表述型", "事实", "需人工复核", ["R-ACC-03"])

    # K 臆造检测（20）：材料内无对应证据的主张，不得判"通过"（拒绝臆造）
    # K1 材料内完全无此指标（10）→ 证据不足
    absent = [
        ("研发人员数量", "1,234人"), ("员工总数", "8,765人"), ("海外收入占比", "35.0%"),
        ("应收账款周转天数", "45天"), ("经营活动现金流净额", "150.0亿元"),
        ("商誉账面价值", "88.0亿元"), ("短期借款", "120.0亿元"),
        ("无形资产", "66.0亿元"), ("所得税费用", "33.0亿元"), ("少数股东权益", "15.0亿元"),
    ]
    for metric, value in absent:
        text = f"甲企业2024年度{metric}为{value}"
        add(text, "数值型", "事实", "证据不足", ["R-GEN-04"],
            metric=metric, value_text=value)
    # K2 指标存在但数值与材料严重不符（10）→ 错误
    for i in range(10):
        wrong = 2000.0 + i * 100
        text = f"甲企业2024年度营业收入为{wrong:.1f}亿元"
        add(text, "数值型", "事实", "错误", ["R-NUM-01"],
            value=Decimal(f"{wrong:.1f}"), unit="亿元", metric="营业收入",
            value_text=f"{wrong:.1f}亿元")
    return out


def main() -> None:
    claims = build()
    DST.mkdir(parents=True, exist_ok=True)
    (DST / "materials").mkdir(exist_ok=True)
    for f in (SRC / "materials").glob("*.md"):
        shutil.copyfile(f, DST / "materials" / f.name)
    shutil.copyfile(SRC / "materials.json", DST / "materials.json")
    task = json.loads((SRC / "task_order.json").read_text(encoding="utf-8"))
    task["task_id"] = "TSK-BENCH"
    (DST / "task_order.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    (DST / "claims_gt.json").write_text(
        json.dumps(claims, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    dist: dict[str, int] = {}
    for c in claims:
        dist[c["gt_status"]] = dist.get(c["gt_status"], 0) + 1
    print(json.dumps({"total": len(claims), "distribution": dist}, ensure_ascii=False))


if __name__ == "__main__":
    main()
