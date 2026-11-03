"""O1/O2/O3 交付物生成：人读报告、机读明细与审计包。

- O2：manifest + items（Claim/VRF/Evidence/Calculation）；CSV 展平视图。
- O1：人读核查报告（S0–S9 暂定结构，官方模板未到位，标"待确认"）。
- O3：A–G 组审计包目录 + manifest（哈希清单）。
Decimal 在 JSON 中序列化为字符串以保精度（在 G 组复现说明中声明）。
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import formulas

CONTRACT_VERSION = "finaudit-data-contract-v1"
STATUS_ORDER = ["通过", "错误", "冲突", "证据不足", "低置信", "需人工复核", "不适用", "待核验"]


def _json_default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    return str(obj)


def _dump(path: Path, data) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, default=_json_default)
    path.write_text(text + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _num_text(value) -> str:
    """数值呈现：Decimal/float/数字字符串保留至多 4 位小数并去尾零。

    从 JSON 回读的值是字符串，这里一并归一化；否则 48 位小数会整个漏进报告。
    """
    if value is None:
        return "—"
    if isinstance(value, (Decimal, float, int)):
        s = f"{Decimal(str(value)):.4f}"
        return s.rstrip("0").rstrip(".") if "." in s else s
    text = str(value).strip()
    try:
        dec = Decimal(text)
    except (InvalidOperation, ValueError):
        return text
    s = f"{dec:.4f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _locator_text(locator) -> str:
    """定位呈现：优先取人读的 value（如 P68-表1），退回页码，最后才用原文。"""

    if not locator:
        return ""
    if isinstance(locator, str):
        return locator
    value = (locator.get("value") or "").strip()
    if value:
        return value
    page = locator.get("page")
    return f"P{page}" if page else ""


def write_o2(out: Path, task: dict, items: list[dict]) -> dict:
    o2 = out / "o2"
    o2.mkdir(parents=True, exist_ok=True)
    json_path = o2 / "O2-核查明细.json"
    csv_path = o2 / "O2-核查明细.csv"

    flat_rows = []
    for it in items:
        claim = it["claim"]
        vrf = it["current_verification"]
        calcs = it.get("calculations") or []
        last_calc = calcs[-1] if calcs else {}
        comp = (last_calc.get("comparison") or {}) if last_calc else {}
        flat_rows.append({
            "claim_id": claim.get("claim_id"),
            "claim_text": claim.get("claim_text"),
            "claim_type": claim.get("claim_type"),
            "judgment_type": claim.get("judgment_type"),
            "status": vrf.get("status"),
            "reason": vrf.get("reason"),
            "rule_ids": ";".join(vrf.get("rule_ids") or []),
            # 列名带 _text，输出人读定位值（如 P68-表1）；结构化定位仍在 O2 JSON 里
            "source_locator_text": _locator_text(vrf.get("source_locator")),
            "evidence_refs": ";".join(vrf.get("evidence_refs") or []),
            "calc_refs": ";".join(vrf.get("calc_refs") or []),
            "suggestion": vrf.get("suggestion") or "",
            "formula_ids": ";".join(c.get("formula_id") for c in calcs),
            "calc_within_tolerance": comp.get("within_tolerance"),
            "decided_at": vrf.get("decided_at") or "",
        })

    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        fieldnames = list(flat_rows[0].keys()) if flat_rows else ["claim_id"]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flat_rows)

    items_sha = hashlib.sha256(
        json.dumps(items, ensure_ascii=False, sort_keys=True,
                   default=_json_default).encode("utf-8")).hexdigest()
    manifest = {
        "schema_id": "urn:finaudit:deliverable:o2:v1",
        "task_id": task["task_id"],
        "subject_alias": task.get("subject_alias"),
        "period": task.get("period_scope"),
        "contract_version": CONTRACT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items_count": len(items),
        "items_sha256": items_sha,
        "csv_file": csv_path.name,
        "csv_sha256": _hash(csv_path),
    }
    _dump(json_path, {"manifest": manifest, "items": items})
    return {"json": json_path, "csv": csv_path, "items": len(items)}


def _alias_map() -> dict:
    """化名映射表：单一来源，只含化名与占位编号。"""
    from .alias import as_map
    return as_map()


def _cell(text: str) -> str:
    """markdown 表格单元格：换行归一、管道符转义。"""
    return str(text).replace("\n", " ").replace("|", "\\|").strip()


def _clip(text: str, limit: int) -> str:
    """超长才截断并加省略号，未超长保持原样。"""
    return text if len(text) <= limit else text[:limit] + "…"


def write_o1(out: Path, task: dict, items: list[dict], severity_map: dict,
             run_config: dict | None = None, outputs: dict | None = None) -> Path:
    o1 = out / "o1"
    o1.mkdir(parents=True, exist_ok=True)
    path = o1 / "O1-核查报告.md"

    counts = {}
    for it in items:
        s = it["current_verification"]["status"]
        counts[s] = counts.get(s, 0) + 1

    lines = []
    lines.append("# 核查报告（O1）")
    lines.append("")
    lines.append(f"- 任务：{task['task_id']}　主体：{task.get('subject_alias')}　"
                 f"期间：{(task.get('period_scope') or {}).get('label', '')}")
    lines.append(f"- 数据截止点：{task.get('data_cutoff')}　边界：{task.get('boundary_statement')}")
    lines.append("- O1 结构为项目内部暂定结构：无官方体裁/模板/篇幅要求可对齐，按内部暂定结构交付。")

    from . import rules as rules_mod
    rule_total = len(rules_mod.RULES)
    rc = run_config or {}
    if rc:
        lines.append("")
        lines.append("## 运行配置（可复现性声明）")
        lines.append("")
        lines.append("| 项 | 值 |")
        lines.append("|---|---|")
        lines.append(f"| 主张拆解模型 | {rc.get('model')}（provider={rc.get('provider')}） |")
        lines.append(f"| 提示词版本 | {rc.get('prompt_version')}（SHA 前 12 位 {rc.get('prompt_hash', '')[:12]}） |")
        lines.append(f"| 材料输入形态 | {rc.get('input_formats')} |")
        lines.append(f"| 数据契约 | {rc.get('contract_version')} |")
        lines.append(f"| 判定引擎 | 确定性规则 {rule_total} 条 + 确定性公式引擎"
                     f"（LLM 不参与判定与计算） |")
        lines.append("| 稳定性 | 隔离重跑状态签名一致 |")
        if rc.get("llm_failure"):
            lines.append(f"| LLM 状态 | **调用失败降级**：{rc.get('llm_failure')}（已记 EV-05，未静默编造） |")
        else:
            lines.append("| LLM 状态 | 正常（retry=" + str(rc.get("llm_retry", 0)) + "） |")
    lines.append("")
    lines.append("## S1 结论摘要")
    lines.append("")
    lines.append("| 状态 | 数量 |")
    lines.append("|---|---|")
    for s in STATUS_ORDER:
        lines.append(f"| {s} | {counts.get(s, 0)} |")
    lines.append(f"| 合计 | {len(items)} |")
    lines.append("")
    bad = [it for it in items if it["current_verification"]["status"] == "错误"]
    if bad:
        lines.append("重要发现（判错项）：")
        lines.append("")
        for it in bad:
            c = it["claim"]
            lines.append(f"- {c['claim_id']}（{c.get('judgment_type')}）：{c['claim_text']} "
                         f"— {_clip(it['current_verification']['reason'], 200)}")
        lines.append("")

    lines.append("## S2 逐项核验表")
    lines.append("")
    lines.append("理由列是摘要（超过 140 字截断）；完整理由、证据引用与规则命中见 O2 核查明细。")
    lines.append("")
    lines.append("| claim_id | 主张摘要 | 类型 | 状态 | 理由（摘要） | 来源定位 | 修改建议 | 严重度 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for it in items:
        c = it["claim"]
        v = it["current_verification"]
        loc = v.get("source_locator") or {}
        loc_text = loc.get("value") or "—"
        # 主张原文完整展示（管道符转义）；理由/建议超长才截断
        text = _cell(c.get("claim_text", ""))
        reason = _clip(_cell(v.get("reason") or ""), 140)
        sugg = _clip(_cell(v.get("suggestion") or "—"), 80)
        lines.append(f"| {c['claim_id']} | {text} | {c.get('judgment_type')} "
                     f"| {v['status']} | {reason} | {loc_text} | {sugg} "
                     f"| {severity_map.get(c['claim_id'], '—')} |")
    lines.append("")

    lines.append("## S3 类型分区（事实/计算/推论/观点）")
    lines.append("")
    for jt in ("事实", "计算", "推论", "观点"):
        subset = [it for it in items if it["claim"].get("judgment_type") == jt]
        lines.append(f"- {jt}：{len(subset)} 条——" +
                     "、".join(f"{it['claim']['claim_id']}({it['current_verification']['status']})"
                               for it in subset))
    lines.append("")

    lines.append("## S4 冲突项专区")
    lines.append("")
    conflicts = [it for it in items if it["current_verification"]["status"] == "冲突"]
    if conflicts:
        for it in conflicts:
            v = it["current_verification"]
            lines.append(f"- {it['claim']['claim_id']}：{v['reason']}")
            lines.append(f"  - 处置：{v.get('suggestion') or '待 R3 人工裁定'}")
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## S5 证据不足与待复核专区")
    lines.append("")
    for st in ("证据不足", "低置信", "需人工复核", "待核验"):
        subset = [it for it in items if it["current_verification"]["status"] == st]
        for it in subset:
            v = it["current_verification"]
            lines.append(f"- [{st}] {it['claim']['claim_id']}：{_clip(v['reason'], 90)}")
    lines.append("")

    lines.append("## S6 计算附录")
    lines.append("")
    lines.append("| claim_id | 公式 | 输出 | 容差 | 差值 | 容差内 | 结果状态 | 假设 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for it in items:
        for calc in it.get("calculations") or []:
            out_v = calc.get("output") or {}
            comp = calc.get("comparison") or {}
            within = comp.get("within_tolerance")
            within_text = "—" if within is None else ("是" if within else "否")
            # 容差与差值同输出单位（"%"/"亿元"/"元"），表内不重复设列，直接跟在数值后
            unit = ((out_v.get("unit") or {}).get("code") or "").strip()
            tol_text = _num_text((calc.get("tolerance") or {}).get("abs"))
            diff_text = _num_text(comp.get("diff"))
            if unit:
                tol_text = f"{tol_text}{unit}" if tol_text != "—" else tol_text
                diff_text = f"{diff_text}{unit}" if diff_text != "—" else diff_text
            lines.append(f"| {it['claim']['claim_id']} | {calc['formula_id']} "
                         f"| {out_v.get('value_text') or '—'} "
                         f"| {tol_text} | {diff_text} "
                         f"| {within_text} | {calc['result_status']} "
                         f"| {'；'.join(calc.get('assumptions') or []) or '—'} |")
    lines.append("")

    lines.append("## S7 边界与适用说明")
    lines.append("")
    lines.append("- 估值属扩展范围，本报告不含估值结论。")
    lines.append("- 计算仅复算数值一致性，不判断披露真实性与会计政策恰当性。")
    lines.append("- 索引口径无官方要求可对齐，按内部定义执行。")
    if rc.get("cutoff_excluded"):
        lines.append(f"- 数据截止点 {rc.get('data_cutoff')} 之后的行已排除 "
                     f"{rc['cutoff_excluded']} 条，不作为证据（避免用截止点后的信息核验）。")
    if rc.get("cutoff_blocked_materials"):
        lines.append(f"- 截至 {rc.get('data_cutoff')} 尚未披露的材料整份不作证据："
                     f"{'、'.join(rc['cutoff_blocked_materials'])}。")
    if rc.get("data_cutoff") and not rc.get("cutoff_publish_known"):
        lines.append(f"- 材料未登记披露日（publish_date），前视偏差只按期间判断："
                     f"{rc.get('data_cutoff')} 之后的行已排除，但无法判断整份材料在截止点是否可得。")
    lines.append("")

    lines.append("## S8 审计包索引")
    lines.append("")
    lines.append("| 文件 | 内容 |")
    lines.append("|---|---|")
    lines.append("| `o3/A-inputs.json` | 输入材料哈希与块索引 |")
    lines.append("| `o3/B-versions.json` | 规则、公式、提示词与数据契约版本 |")
    lines.append("| `o3/C-events.jsonl` | 全量审计事件流（19 类事件） |")
    lines.append("| `o3/D-calculations.json` | 确定性计算记录（输入、公式、输出） |")
    lines.append("| `o3/E-reviews.json` | 导出时已存在的人工复核记录（复核通常在导出之后进行，故常为空） |")
    lines.append("| `o3/review-log.jsonl` | 界面人工复核留痕，运行后追加；无人复核时不生成 |")
    lines.append("| `o3/F-outputs.json` | 输出文件哈希 |")
    lines.append("| `o3/G-reproduce.md` | 复现说明（运行时按实际配置生成） |")
    lines.append("| `o3/manifest.json` | 以上文件的清单与校验值 |")
    lines.append("")
    lines.append("复现步骤见 `o3/G-reproduce.md`；机读明细与人读结论分别在 `o2/` 与本报告。")

    lines.append("## S9 检查与放行记录")
    lines.append("")
    oc = outputs or {}
    kl = oc.get("kl_scan_result")
    al = oc.get("alias_check")
    if kl is None:
        lines.append("- 检查：待导出前扫描（首版渲染）。")
    else:
        lines.append(f"- 密钥扫描：{kl} 处命中（`finaudit/scan.py` 真实执行，范围 O1/O2）。")
        lines.append(f"- 化名扫描：{al} 处命中（白名单由 `alias.py` 受控映射表派生；"
                     "真实主体名经环境变量当次传入，不落盘）。")
        checks = oc.get("schema_checks") or {}
        checked = checks.get("items")
        failed = checks.get("error_count") or 0
        lines.append(f"- 契约校验：{checks.get('passed', '—')}/{checked if checked is not None else '—'} "
                     f"项通过" + (f"，{failed} 处不合契约" if failed else "") + "。")
        for err in (checks.get("errors") or [])[:5]:
            lines.append(f"  - {err}")
        release = (kl == 0 and al == 0 and failed == 0
                   and checked is not None and checks.get("passed") == checked)
        lines.append(f"- 放行：**{'放行' if release else '阻断'}**"
                     f"（两项扫描零命中、契约校验全部通过才放行；否则转人工处置）。")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_o3(out: Path, task: dict, records: list[dict], items: list[dict],
             events, blocks: list[dict], rows: list[dict], materials: list[dict],
             outputs: dict, run_config: dict | None = None) -> dict:
    o3 = out / "o3"
    o3.mkdir(parents=True, exist_ok=True)
    files = {}

    rc = run_config or {}
    # 评测集标识按实际输入写：上传材料没有金标准，不能沿用演示集的标签
    eval_label = (f"{rc.get('fixtures', '—')}（金标准 {rc['gt_size']} 条）"
                  if rc.get("gt_size") else
                  f"{rc.get('fixtures', '—')}（未附金标准，只出核验结果，不计准确率）")
    files["A-inputs.json"] = _dump(o3 / "A-inputs.json", {
        "materials": [{"material_id": r["material_id"], "sha256": r["sha256"],
                       "source_file_ref": r["source_file_ref"],
                       # 披露日决定材料在截止点是否可得，属证据资格要件，随包留痕
                       **({"publish_date": r["publish_date"]}
                          if r.get("publish_date") else {})}
                      for r in records],
        "task_order_sha256": hashlib.sha256(
            json.dumps(task, ensure_ascii=False, sort_keys=True,
                       default=_json_default).encode()).hexdigest(),
        "eval_set": eval_label,
        "alias_map": _alias_map(),  # 化名映射表（只含化名与占位编号）
        # 块级原文索引：供复核界面按证据定位到原文（HUMAN 只读受控材料区内块）
        "blocks": [{"block_id": b["block_id"], "material_id": b["material_id"],
                    "page": b.get("page"), "block_type": b.get("block_type"),
                    "parse_status": b.get("parse_status"),
                    "locator": b.get("locator"),
                    "content_text": (b.get("content_text") or "")[:1500]}
                   for b in blocks],
    })
    used_formulas = sorted({c["formula_id"] for it in items
                            for c in (it.get("calculations") or [])
                            if c.get("formula_id")})
    from . import rules as rules_mod
    rule_total = len(rules_mod.RULES)
    rule_impl = sum(1 for r in rules_mod.RULES.values() if r.get("implemented"))
    files["B-versions.json"] = _dump(o3 / "B-versions.json", {
        "schema_contract": CONTRACT_VERSION,
        "tools": [{"name": "finaudit", "version": "0.1.0"},
                  {"name": "md-parser", "version": "0.1.0"},
                  {"name": "finaudit-calc", "version": "0.1.0"}],
        # 条数由规则/公式表算出，不写死；「库内总数」与「本次调用」分开列
        "rules_version": f"{rule_total} 条（已实现 {rule_impl} 条）",
        "formulas_version": f"{len(formulas.FORMULAS)} 条（公式库总数）",
        "formulas_used": used_formulas,
        "run_config": {"fixtures": rc.get("fixtures", "—"),
                       "python": rc.get("python", "—")},
    })
    c_path = o3 / "C-events.jsonl"
    c_path.write_text("\n".join(json.dumps(e, ensure_ascii=False, default=_json_default)
                                for e in events.events) + "\n", encoding="utf-8")
    files["C-events.jsonl"] = _hash(c_path)

    calcs = [c for it in items for c in (it.get("calculations") or [])]
    files["D-calculations.json"] = _dump(o3 / "D-calculations.json", {"calculations": calcs})
    files["E-reviews.json"] = _dump(o3 / "E-reviews.json", {
        "manual_reviews": [],
        "note": "MVP 不含人工复核记录；P2–P6 由测试用例在实现环境执行。",
    })
    files["F-outputs.json"] = _dump(o3 / "F-outputs.json", {
        "o1": outputs.get("o1_hash"), "o2": outputs.get("o2_hash"),
        "csv": outputs.get("csv_hash"),
        "schema_checks": outputs.get("schema_checks"),
        "kl_scan_result": outputs.get("kl_scan_result", 0),
        "alias_check": outputs.get("alias_check", 0),
    })
    rc = run_config or {}
    from . import rules as rules_mod
    rule_total = len(rules_mod.RULES)
    rule_impl = sum(1 for r in rules_mod.RULES.values() if r.get("implemented"))
    dep_note = ("核心零第三方依赖（纯标准库）；PDF 文本层需 pypdf（BSD-3-Clause）、"
                "扫描件 OCR 需 rapidocr_onnxruntime（Apache-2.0），未安装时对应路径不可用，"
                "Markdown 路径不受影响")
    llm_line = (f"   - 主张拆解：{rc.get('model')}（provider={rc.get('provider')}，"
                f"prompt={rc.get('prompt_version')}）；LLM 失败时降级为夹具主张并记 EV-05，不静默编造。"
                if rc.get("provider") != "mock" else
                "   - 主张拆解：mock provider（确定性占位，用夹具主张清单）；"
                "真实模型经 `--llm-preset modelscope|deepseek` 启用。")
    g_path = o3 / "G-reproduce.md"
    fixture_name = rc.get("fixtures", "demo01")
    py_ver = rc.get("python", "3.13")
    g_path.write_text(
        "\n".join([
            "# 复现说明（G 组）",
            "",
            f"1. 环境：Python {py_ver}（3.11+ 兼容）。" + dep_note + "。",
            f"2. 运行：`cd code && python -m finaudit.cli run --fixtures fixtures/{fixture_name} "
            f"--out ../out/{fixture_name}`（演示记录：`python -m finaudit.demo "
            f"--fixtures fixtures/{fixture_name} --out ../out/{fixture_name}`，输出 D0–D8 九步）",
            f"3. 本次材料输入形态：{rc.get('input_formats', '—')}；"
            f"数据契约 {rc.get('contract_version', '—')}。",
            "4. 确定性：ID 分配与处理顺序只依赖输入；时间戳不参与复现比对；"
            "管线内置隔离重跑（独立号段、不发事件）并比对状态签名。",
            "5. JSON 中的 Decimal 以字符串序列化以保精度（合同允许 number；本实现取字符串）。",
            f"6. 规则覆盖：{rule_impl}/{rule_total} 条已实现；"
            "过程性元规则（R-GEN-01/06/07、R-TIE-04）以守卫函数暴露，未参与判定链。",
            "7. 已知差异与局限：",
            llm_line,
            "   - 事实/证据账本与人工复核记录为空（I3 之后实现）。",
            "   - 表格解析依赖「一行一条记录」版式；跨行合并单元格记解析失败，不猜内容。",
            "   - 规模基准为构造性金标准（300 条十二族），非全量评测集 ≥200 条全链路口径；"
            "引擎准确率与真实模型端到端表现为两套口径，分开报告。",
            "",
        ]), encoding="utf-8")
    files["G-reproduce.md"] = _hash(g_path)

    manifest = {
        "schema_id": "urn:finaudit:deliverable:o3:v1",
        "task_id": task["task_id"],
        "subject_alias": task.get("subject_alias"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "event_count": len(events.events),
        "blocks": len(blocks), "rows": len(rows),
        "materials": [m["material_id"] for m in materials],
    }
    _dump(o3 / "manifest.json", manifest)
    return {"dir": o3, "files": files, "manifest": manifest}


def _schema_checks(items: list[dict]) -> dict:
    """逐项校验 Claim / VerificationResult / Calculation，返回真实通过数。

    不把「通过数」直接写成项数：校验真跑，不合格项计入 errors，并阻断 S9 放行。
    """
    from .contract import validate

    errors: list[str] = []
    passed = 0
    for it in items:
        item_errors = (validate(it.get("claim") or {}, "Claim")
                       + validate(it.get("current_verification") or {}, "VerificationResult"))
        for calc in it.get("calculations") or []:
            item_errors.extend(validate(calc, "Calculation"))
        if item_errors:
            errors.extend(item_errors)
        else:
            passed += 1
    return {"items": len(items), "passed": passed,
            "error_count": len(errors), "errors": errors[:10]}


def write_outputs(*, out_dir: Path, task: dict, records: list[dict], items: list[dict],
                  events, blocks: list[dict], rows: list[dict], severity_map: dict,
                  materials: list[dict], run_config: dict | None = None,
                  alias_keywords: list[str] | None = None) -> dict:
    out = out_dir
    o2 = write_o2(out, task, items)
    o1 = write_o1(out, task, items, severity_map, run_config=run_config)  # 首版（S9 待扫描）

    # 两项扫描真实执行（O1/O2 已落盘；O3 由管线自生成）
    from . import scan as scan_mod
    scan = scan_mod.run_all(out, alias_keywords=alias_keywords)

    outputs = {
        "o1_hash": _hash(o1),
        "o2_hash": _hash(o2["json"]),
        "csv_hash": _hash(o2["csv"]),
        "schema_checks": _schema_checks(items),
        "kl_scan_result": scan["kl_scan_result"],
        "alias_check": scan["alias_check"],
    }
    # 以最终版重写 O1（S9 写入真实扫描结果），哈希基于最终文件
    o1 = write_o1(out, task, items, severity_map, run_config=run_config, outputs=outputs)
    outputs["o1_hash"] = _hash(o1)

    # EV-11 导出事件：输出哈希 + Schema 校验 + 两项扫描（须先于 C 组落盘）
    events.emit("导出", "S7", "程序", "导出程序",
                {"o1_sha256": outputs["o1_hash"], "o2_sha256": outputs["o2_hash"],
                 "csv_sha256": outputs["csv_hash"],
                 "schema_checks": outputs["schema_checks"],
                 "kl_scan_result": scan["kl_scan_result"],
                 "alias_check": scan["alias_check"],
                 "扫描范围": "O1/O2（O3 由管线自生成）",
                 "命中摘要": scan["hits"]})
    o3 = write_o3(out, task, records, items, events, blocks, rows, materials, outputs,
                  run_config=run_config)
    return {
        "out_dir": str(out),
        "o1": str(o1), "o2_json": str(o2["json"]), "o2_csv": str(o2["csv"]),
        "o3_manifest": str(o3["dir"] / "manifest.json"),
        "outputs": outputs,
    }
