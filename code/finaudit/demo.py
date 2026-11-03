"""现场演示脚本（D0–D8）：一条命令跑完并输出可讲解的九步记录。

用法：
    python -m finaudit.demo --fixtures fixtures/demo01 --out ../out/demo01
输出：终端逐步打印 + `<out>/demo-transcript.md`（可投影或留档）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

from . import formulas


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(v) -> str:
    if v is None:
        return "—"
    try:
        d = Decimal(str(v))
        s = f"{d:.4f}"
        return s.rstrip("0").rstrip(".") if "." in s else s
    except Exception:  # noqa: BLE001
        return str(v)


def _clip(text: str, limit: int) -> str:
    """按字数截断并只在真截断时加省略号（短文本不该出现「…」）。"""

    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"


class _Doc:
    """同时输出到终端与 transcript。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def say(self, text: str = "") -> None:
        print(text)
        self.lines.append(text)


def run_demo(fixtures: str | Path, out: str | Path, llm_config=None) -> dict:
    from .pipeline import run

    fixtures, out = Path(fixtures), Path(out)
    doc = _Doc()
    doc.say("# 研鉴（FinAudit）现场演示记录\n")
    doc.say(f"- 输入夹具：`{fixtures.name}`　输出目录：`{out}`")

    summary = run(fixtures, out, llm_config=llm_config)
    o2 = _load(out / "o2" / "O2-核查明细.json")
    items = o2["items"]
    f_outputs = _load(out / "o3" / "F-outputs.json")
    manifest = _load(out / "o3" / "manifest.json")

    # ---------------- D0 版本锚定 ----------------
    doc.say("\n## D0 版本锚定（演示文件 = 提交版本）")
    o1 = out / "o1" / "O1-核查报告.md"
    o2_json = out / "o2" / "O2-核查明细.json"
    checks = {
        "O1": (_hash(o1), f_outputs.get("o1")),
        "O2": (_hash(o2_json), f_outputs.get("o2")),
    }
    for name, (actual, recorded) in checks.items():
        doc.say(f"- {name} 现场哈希 {actual[:16]}… ｜ 审计包登记 "
                f"{str(recorded)[:16]}… ｜ {'一致' if actual == recorded else '不一致'}")
    checks_summary = f_outputs.get("schema_checks", {})
    doc.say(f"- 契约校验：{checks_summary.get('passed')}/{checks_summary.get('items')} 项通过"
            + (f"，{checks_summary.get('error_count')} 处不合契约"
               if checks_summary.get("error_count") else ""))

    # ---------------- D1 任务受理与材料登记 ----------------
    doc.say("\n## D1 任务受理与材料登记")
    task = _load(fixtures / "task_order.json")
    doc.say(f"- 任务 {task['task_id']}｜主体 {task['subject_alias']}｜"
            f"期间 {task['period_scope'].get('label')}｜数据截止点 {task['data_cutoff']}")
    doc.say("- 材料清单（SHA-256 前 16 位）：")
    for m in _load(out / "o3" / "A-inputs.json")["materials"]:
        doc.say(f"  - {m['material_id']} {m['source_file_ref']}  {m['sha256'][:16]}…")

    # ---------------- D2 结论摘要 ----------------
    doc.say("\n## D2 结论摘要")
    counts = Counter(it["current_verification"]["status"] for it in items)
    order = ["通过", "错误", "冲突", "证据不足", "低置信", "需人工复核", "不适用", "待核验"]
    doc.say("| 状态 | 数量 |")
    doc.say("|---|---|")
    for s in order:
        if counts.get(s):
            doc.say(f"| {s} | {counts[s]} |")
    doc.say(f"| 合计 | {len(items)} |")

    # ---------------- D3 逐项表走查 ----------------
    doc.say("\n## D3 逐项表走查（每状态抽 1 条）")
    seen: set[str] = set()
    for it in items:
        st = it["current_verification"]["status"]
        if st in seen:
            continue
        seen.add(st)
        c, v = it["claim"], it["current_verification"]
        loc = (v.get("source_locator") or {}).get("value", "—")
        doc.say(f"- [{st}] {c['claim_id']}：{_clip(c['claim_text'], 34)}")
        doc.say(f"  理由：{_clip(v['reason'], 70)}")
        doc.say(f"  规则：{','.join(v['rule_ids']) or '—'}｜定位：{loc}"
                f"｜建议：{_clip(v.get('suggestion') or '—', 24)}")

    # ---------------- D4 计算复现 ----------------
    doc.say("\n## D4 计算复现（按审计包输入重算）")
    calcs = _load(out / "o3" / "D-calculations.json")["calculations"]
    sample = next((c for c in calcs if c["formula_id"] == "FRM-IND-01"), None)
    if sample:
        inputs = sample["inputs"]
        recomputed = formulas.compute(sample["formula_id"], inputs)
        doc.say(f"- {sample['calc_id']} {sample['formula_id']}：")
        doc.say(f"  登记输出 {_fmt(sample['output']['value'])}"
                f"{sample['output']['unit']['code']}｜现场重算 "
                f"{_fmt(recomputed['value'])}{recomputed['unit']['code']}｜"
                f"{'一致' if str(recomputed['value']) == str(sample['output']['value'])
                   or abs(Decimal(str(recomputed['value'])) - Decimal(str(sample['output']['value'])))
                   < Decimal('1e-9') else '不一致'}")
        doc.say(f"  容差 {_fmt(sample['tolerance']['abs'])}｜diff "
                f"{_fmt(sample['comparison']['diff'])}｜within_tolerance="
                f"{sample['comparison']['within_tolerance']}")
        doc.say(f"  执行者 {sample['executed_by']}（LLM 不参与计算）")
    else:
        doc.say("- 本任务无指标类计算记录")

    # ---------------- D5 审计包走查 ----------------
    doc.say("\n## D5 审计包走查")
    events = [json.loads(line) for line in
              (out / "o3" / "C-events.jsonl").read_text(encoding="utf-8").splitlines() if line]
    doc.say(f"- 事件总数 {len(events)}｜类型 {len(set(e['event_type'] for e in events))} 类")
    doc.say("- 类型计数：" + "，".join(f"{k}×{v}" for k, v in
                                       Counter(e["event_type"] for e in events).items()))
    llm_events = [e for e in events if e["event_type"] == "LLM调用"]
    if llm_events:
        payload = llm_events[0]["payload"]
        doc.say(f"- LLM 调用留痕：model={payload.get('model')}｜"
                f"prompt_version={payload.get('prompt_version')}｜"
                f"retry={payload.get('retry_count')}")

    # ---------------- D6 失败与边界 ----------------
    doc.say("\n## D6 失败与边界（不隐藏）")
    for st in ("冲突", "证据不足", "低置信", "需人工复核", "待核验"):
        subset = [it for it in items if it["current_verification"]["status"] == st]
        for it in subset[:2]:
            doc.say(f"- [{st}] {it['claim']['claim_id']}："
                    f"{_clip(it['current_verification']['reason'], 64)}")
            doc.say(f"  → 处置：{(it['current_verification'].get('suggestion') or '按状态保留，不判对错')}")

    # ---------------- D7 指标口径 ----------------
    doc.say("\n## D7 指标口径与稳定性")
    doc.say(f"- 稳定性（隔离重跑状态签名）：{'一致' if summary.get('stability') else '不一致'}")
    doc.say(f"- 评测报告：`{Path(summary['eval_report']).name}`（样本量与局限同报，"
            "门槛为团队内部目标）")
    doc.say(f"- LLM：provider={summary.get('llm_provider')}，"
            f"model={summary.get('llm_model')}（mock 为离线占位，真实模型经配置启用）")

    # ---------------- D8 交付前检查 ----------------
    doc.say("\n## D8 交付前检查")
    for key, label in (("kl_scan_result", "密钥扫描"), ("alias_check", "化名扫描")):
        doc.say(f"- {label}：{f_outputs.get(key, '见导出事件')}（0 = 零命中）")
    doc.say(f"- 审计结论：{'通过' if summary.get('audit_ok') else '存在违规项，阻断导出'}")
    doc.say(f"- 交付物：O1 报告 / O2 明细（JSON+CSV）/ O3 审计包（{len(manifest['files'])} 个文件）")

    transcript = out / "demo-transcript.md"
    transcript.write_text("\n".join(doc.lines) + "\n", encoding="utf-8")
    print(f"\n[demo] transcript 已写入 {transcript}")
    return {"transcript": str(transcript), "summary": summary,
            "steps": ["D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finaudit.demo")
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    run_demo(args.fixtures, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
