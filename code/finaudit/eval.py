"""评测报告（评测指标子集）。

指标（当前可计算部分）：
- IX 提取完整性：抽取主张数 / 金标准主张数
- IX 状态准确率（按 ID）：判定状态与金标准一致的主张数 / 总数（金标准仅用于离线评测）
- IX 提取覆盖/状态准确率（按原文匹配）：真实模型可能调序/多抽/漏抽，按 ID 会失真；
  以归一化原文贪心配对后计算，反映抽取的真实覆盖与判定质量
- IX 定位覆盖率：带 report_locator 的主张 / 总数
- IX 规则覆盖：rule_ids 非空的主张 / 总数
- IX 事件完整性：必需事件类型覆盖数 / 必需类型数
- IX 稳定性：重复运行状态签名一致（与基线一致记 1）
并同时报告样本数与局限（评测口径）。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


def _norm_text(s: str) -> str:
    return re.sub(r"[\s。，,.;；:：%（）()]", "", s or "")


def write_eval_report(out_dir: Path, *, o2_items: list[dict], gt: dict[str, dict],
                      summary: dict, baseline_signature: list | None = None,
                      signature: list | None = None,
                      dataset: str = "DEMO-01") -> Path:
    total = len(o2_items)
    matched = 0
    for it in o2_items:
        cid = it["claim"]["claim_id"]
        if cid in gt and it["current_verification"]["status"] == gt[cid]["gt_status"]:
            matched += 1
    _pct = lambda a, b: f"{a / b:.1%}" if b else "—"  # noqa: E731

    # 按原文贪心配对（同一原文多条时逐条消耗）：对真实模型的诚实口径
    pool = [(_norm_text(e["claim"].get("claim_text", "")), e["gt_status"])
            for e in gt.values()]
    text_matched = text_ok = 0
    for it in o2_items:
        key = _norm_text(it["claim"].get("claim_text", ""))
        for i, (t, g) in enumerate(pool):
            if t == key:
                text_matched += 1
                text_ok += it["current_verification"]["status"] == g
                pool.pop(i)
                break

    locator_ok = sum(1 for it in o2_items if it["claim"].get("report_locator"))
    rule_ok = sum(1 for it in o2_items if it["current_verification"].get("rule_ids"))
    required_events = {"任务单创建", "材料登记", "解析调用", "检索调用", "LLM调用",
                       "规则判定", "计算", "审计复核", "导出"}
    got_events = set(summary.get("event_types", []))
    event_cover = len(required_events & got_events)
    stable = "—"
    if baseline_signature is not None and signature is not None:
        stable = "一致" if baseline_signature == signature else "不一致"

    provider = summary.get("llm_provider")
    model = summary.get("llm_model")
    extract_note = ("mock provider 的主张抽取为确定性占位（用夹具主张清单），"
                    "真实抽取能力需以配置的国产模型复测"
                    if provider == "mock" else
                    f"主张抽取由真实模型 {model} 完成（prompt=claim_extract.v2）；"
                    "「按 ID」指标受抽取顺序影响，「按原文匹配」为抽取质量的诚实口径")
    tm_pct = _pct(text_ok, text_matched)
    lines = [
        f"# 评测报告（{dataset}）",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).isoformat()}",
        f"- 样本量：n={total}（金标准 {dataset}；非全量评测集口径，指标不代表整体性能）",
        f"- 运行模式：LLM provider={summary.get('llm_provider')}，"
        f"model={summary.get('llm_model')}（mock 为确定性占位；真实模型经配置启用）",
        "",
        "| 指标 | 结果 | 判定口径 |",
        "|---|---|---|",
        f"| 提取完整性 | {summary.get('claims_extracted', total)}/{len(gt)} "
        f"| 抽取主张数 ÷ 金标准主张数 |",
        f"| 状态准确率（按 ID） | {matched}/{total}（{_pct(matched, total)}） "
        f"| 判定状态与金标准一致（受抽取顺序影响） |",
        f"| 提取覆盖（按原文匹配） | {text_matched}/{len(gt)}"
        f"（{_pct(text_matched, len(gt))}） | 归一化原文贪心配对 |",
        f"| 状态准确率（按原文匹配） | {text_ok}/{text_matched}（{tm_pct}） "
        f"| 匹配项的判定状态与金标准一致 |",
        f"| 定位覆盖率 | {locator_ok}/{total}（{_pct(locator_ok, total)}） "
        f"| 带 report_locator 的主张比例 |",
        f"| 规则可回链率 | {rule_ok}/{total}（{_pct(rule_ok, total)}） "
        f"| rule_ids 非空的比例 |",
        f"| 事件完整性 | {event_cover}/{len(required_events)} "
        f"| 必需事件类型覆盖 |",
        f"| 稳定性（重复运行） | {stable} | 两次运行 VRF 编号与状态签名一致 |",
        "",
        "## 局限",
        "",
        "1. 样本量为演示集（含 DEMO-01 单集与规模基准），未达全量评测集 ≥200 条"
        "全链路口径；百分比仅用于回归对照，不作为对外性能声明。",
        f"2. {extract_note}；状态判定与计算为真实引擎结果。",
        "3. 指标门槛（如 ≥95%）为团队内部建议目标，非官方评分权重。",
        "",
    ]
    path = out_dir / "eval-report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
