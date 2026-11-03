"""本地 Web 界面：上传/选夹具 → 跑核验 → 看四区结果。仅标准库，零前端依赖。

四区（F08b 受控呈现）：
  A 任务概览：受理参数、材料登记、状态分布、运行配置
  B Agent 流程：RA1–RA8 的状态与事件，全部来自运行事件流，不做前端预设
  C 主张与证据：逐条主张 + 候选证据，点证据定位原文
  D 复核与交付：计算过程、审计事件、人工复核留痕、O1/O2/O3 下载

安全约束：
  - 上传：扩展名白名单 + 单文件/总量/份数上限 + 文件名清洗 + 路径收敛校验；
  - 渲染：一律用 DOM 文本节点，不用 innerHTML 拼接用户可控内容；
  - 下载：仅限本任务输出目录内 o1/o2/o3 与评测报告，白名单文件名；
  - 外发：外部模型端点需显式勾选授权，未授权拒绝发送研报正文。

启动：python webui.py [--port 8765]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import uuid
import webbrowser
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
OUT = ROOT.parent / "out"
UPLOADS = OUT / "uploads"           # 用户上传材料生成的任务目录（不进代码目录）
sys.path.insert(0, str(ROOT))

from web_page import PAGE  # noqa: E402  （页面模板依赖 ROOT 入 sys.path）

# ---------------------------------------------------------------- 上传限制
ALLOWED_SUFFIX = (".pdf", ".md", ".markdown", ".txt")
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 60 * 1024 * 1024
MAX_FILES = 10
MAX_MULTIPART_OVERHEAD_BYTES = 1024 * 1024
MAX_JSON_BODY_BYTES = 1024 * 1024

# ---------------------------------------------------------------- 任务作业
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_REVIEW_LOCK = threading.Lock()
MAX_JOBS = 20           # 内存中保留的作业数上限，超出时淘汰最早的已结束作业
MAX_JOB_EVENTS = 2000   # 每个作业保留的事件条数；名额按 300 条基准（约 670 事件）留足余量


# ---------------------------------------------------------------- 上传校验

def _safe_name(name: str) -> str:
    """文件名清洗：仅保留中英文、数字、下划线、连字符与扩展名点。"""
    base = Path(name).name
    base = re.sub(r"[^\w\u4e00-\u9fa5.\-]", "_", base)
    return base.lstrip(".")[:120]


def _validate_upload(files: dict[str, bytes]) -> str | None:
    if not files:
        return "未收到文件"
    if len(files) > MAX_FILES:
        return f"文件数量超过上限（{MAX_FILES} 份）"
    total = 0
    for name, blob in files.items():
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_SUFFIX:
            return f"不支持的文件类型：{name}（仅 {('、').join(ALLOWED_SUFFIX)}）"
        if len(blob) > MAX_FILE_BYTES:
            return f"单文件超过 {MAX_FILE_BYTES // 1024 // 1024}MB：{name}"
        total += len(blob)
    if total > MAX_TOTAL_BYTES:
        return f"上传总量超过 {MAX_TOTAL_BYTES // 1024 // 1024}MB"
    return None


def _parse_period_scope(label: str) -> dict | None:
    """统一用期间解析器校验任务范围，避免界面与材料解析口径分叉。"""
    from finaudit.periods import parse_period_label
    return parse_period_label(label)


def _make_fixture(files: dict[str, bytes], params: dict) -> tuple[str | None, str]:
    """把上传材料落成夹具目录（上传受理的 I1/I2/I3）。缺受理参数返回错误，不静默启动。"""
    for key in ("subject", "period", "cutoff"):
        if not params.get(key):
            return None, f"缺受理参数：{key}（I3，缺项先提示，不静默启动）"
    if not params.get("report"):
        return None, "请指定哪一份是待核验研报（I1）"
    err = _validate_upload(files)
    if err:
        return None, err
    if len(files) < 2:
        return None, "至少需要 1 份研报 + 1 份证据材料（I2）"

    period_scope = _parse_period_scope(params["period"])
    if period_scope is None:
        return None, "期间格式无法无歧义识别；请用年度、季度、半年、日期时点或明确起止日期"
    safe_names = [_safe_name(name) for name in files]
    if any(not name for name in safe_names):
        return None, "存在不可用文件名"
    if len(set(safe_names)) != len(safe_names):
        return None, "上传文件名清洗后重名；请先为文件改成不同名称"
    report_name = _safe_name(params["report"])
    if report_name not in safe_names:
        return None, "指定的研报不在上传文件内（I1）"

    tid = "U" + uuid.uuid4().hex[:6]
    dst = UPLOADS / tid
    mats = dst / "materials"
    mats.mkdir(parents=True, exist_ok=True)

    registry, saved = [], []
    for idx, ((name, blob), safe) in enumerate(zip(files.items(), safe_names), start=1):
        target = (mats / safe).resolve()
        try:
            target.relative_to(mats.resolve())
        except ValueError:
            return None, f"文件名不合法：{name}"
        target.write_bytes(blob)
        is_report = safe == report_name
        fmt = "pdf" if safe.lower().endswith(".pdf") else "md"
        registry.append({
            "material_id": f"MAT-{idx:04d}",
            "role": "待核验研报" if is_report else "证据材料",
            "source_file_ref": safe,
            "format": fmt,
            "page_count": None,
        })
        saved.append(safe)
    if len(registry) < 2:
        return None, "至少需要 1 份研报 + 1 份证据材料（I2）"
    (dst / "materials.json").write_text(
        json.dumps({"materials": registry}, ensure_ascii=False, indent=2), encoding="utf-8")

    task = {
        "task_id": f"TSK-{tid}",
        "contract_version": "finaudit-data-contract-v1",
        "subject_alias": params["subject"],
        "period_scope": period_scope,
        "data_cutoff": params["cutoff"],
        "boundary_statement": "仅核查本任务授权材料范围内的可核验主张",
        "materials": [{"source_file_ref": m["source_file_ref"], "role": m["role"]}
                      for m in registry],
        "missing_items": [],
    }
    (dst / "task_order.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    # 上传场景无金标准：置空，评测按"无金标准"口径输出，不伪造对照
    (dst / "claims_gt.json").write_text("[]", encoding="utf-8")
    return str(dst), tid


# ---------------------------------------------------------------- 受控范围

def _safe_fixture(name: str) -> Path | None:
    """夹具定位校验：只允许 FIXTURES / UPLOADS 下的一级子目录，拒绝空名与路径穿越。

    上传夹具用 `U:<目录>` 形式，其余为内置夹具名。
    """
    if not name:
        return None
    base, sub = (UPLOADS, name[2:]) if name.startswith("U:") else (FIXTURES, name)
    if not sub or "/" in sub or "\\" in sub or sub in (".", "..") or ".." in sub:
        return None
    try:
        target = (base / sub).resolve()
        target.relative_to(base.resolve())
    except (ValueError, OSError):
        return None
    return target if target.is_dir() else None


def _safe_out(name: str) -> Path | None:
    """受控呈现的范围校验：只允许 OUT 之下的目录（OA-03 界面越权）。"""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    try:
        target = (OUT / name).resolve()
    except Exception:  # noqa: BLE001
        return None
    base = OUT.resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target if target.is_dir() else None


DOWNLOAD_WHITELIST = {
    "O1-核查报告.md": "o1",
    "O2-核查明细.json": "o2",
    "O2-核查明细.csv": "o2",
    "A-inputs.json": "o3", "B-versions.json": "o3", "C-events.jsonl": "o3",
    "D-calculations.json": "o3", "E-reviews.json": "o3", "F-outputs.json": "o3",
    "G-reproduce.md": "o3", "manifest.json": "o3", "review-log.jsonl": "o3",
    "eval-report.md": "",
}


def _download_path(name: str, filename: str) -> Path | None:
    """交付下载：仅限白名单文件，且必须落在本任务输出目录内。"""
    d = _safe_out(name)
    if d is None:
        return None
    subdir = DOWNLOAD_WHITELIST.get(Path(filename).name)
    if subdir is None:
        return None
    target = (d / subdir / Path(filename).name).resolve() if subdir else (d / filename).resolve()
    try:
        target.relative_to(d.resolve())
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target


def _list_tasks() -> list[str]:
    """可复核的任务目录：只列有核验明细的，按产出时间倒序（最近的排前面）。"""
    if not OUT.exists():
        return []
    items = [p for p in OUT.iterdir()
             if p.is_dir() and (p / "o2" / "O2-核查明细.json").exists()]
    items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in items]


# ---------------------------------------------------------------- 运行作业

def _actor_role(event: dict) -> str:
    """把事件主体归到角色：程序执行体的产出归属其发起 Agent。

    finaudit-calc 归 RA6、登记程序归 RA2、导出程序归 RA8；其余按主体 id 直取。
    只做面板归属，不改事件流本身的记录。
    """

    actor = event.get("actor") or {}
    aid = actor.get("id") or ""
    # 按主体 id 归属，不依赖 kind（调用方构造的事件不一定带 kind）
    if aid == "finaudit-calc" and event.get("event_type") == "计算":
        return "RA6"
    if aid == "登记程序":
        return "RA2"
    if aid == "导出程序":
        return "RA8"
    return aid


def _agent_progress(events: list[dict], job_status: str = "运行中",
                    current_stage: str = "") -> list[dict]:
    """按阶段、最近事件和作业终态计算状态，不把启动事件误报成完成。"""
    from finaudit.agents import ROLES
    stage_order = {f"S{i}": i for i in range(9)}
    current_order = stage_order.get(current_stage, -1)
    last_event = events[-1] if events else {}
    last_actor = _actor_role(last_event) if last_event else None
    out = []
    for code, role in ROLES.items():
        if code in ("HUMAN", "EXPORT"):
            continue
        mine = [e for e in events if _actor_role(e) == code]
        failed = any(e.get("event_type") in ("工具失败", "越权") for e in mine)
        status = "未运行"
        role_order = stage_order.get(role.stage, 99)
        if code == "RA1":
            status = {"完成": "已完成", "失败": "失败"}.get(
                job_status, "未运行" if job_status in ("未开始", "尚未启动") else "运行中")
        elif job_status == "完成":
            status = "已完成" if mine else "未运行"
        elif job_status == "失败":
            if code == last_actor or failed:
                status = "失败"
            elif mine and role_order < current_order:
                status = "已完成"
            elif mine and role_order == current_order:
                status = "已中断"
        elif role_order == current_order and code == last_actor:
            status = "运行中"
        elif mine:
            if role_order < current_order:
                status = "已完成"
            elif role_order == current_order:
                status = "运行中" if code == last_actor else "已完成"
        out.append({"code": code, "name": role.name, "stage": role.stage,
                    "count": len(mine), "writes": "、".join(role.writes),
                    "status": status,
                    "last": mine[-1]["event_type"] if mine else ""})
    return out


def _public_event_summary(event: dict) -> dict:
    """向前端提供最小事件摘要；交接只暴露契约元数据，不暴露原始 payload。"""
    from finaudit.agents import HANDOFFS
    actor_id = (event.get("actor") or {}).get("id", "")
    summary = {
        "seq": event.get("seq"),
        "event_type": event.get("event_type", ""),
        "stage": event.get("stage", ""),
        "actor": actor_id,
    }
    handoff = event.get("handoff")
    if isinstance(handoff, dict) and handoff.get("code"):
        code = handoff["code"]
        contract = HANDOFFS.get(code)
        missing = handoff.get("missing")
        summary["handoff"] = {
            "code": code,
            "name": contract.name if contract else "未登记交接物",
            "src": contract.src if contract else actor_id,
            "dst": contract.dst if contract else "接收方未知",
            "missing_count": len(missing) if isinstance(missing, list) else 0,
        }
    return summary


def _stage_event_counts(events: list[dict]) -> dict[str, int]:
    """汇总全量事件阶段分布，避免长任务只返回最近事件时误标早期阶段。"""
    counts = {f"S{i}": 0 for i in range(8)}
    for event in events:
        stage = event.get("stage")
        if stage in counts:
            counts[stage] += 1
    return counts


def _evict_finished_jobs() -> None:
    """作业字典只保留最近 MAX_JOBS 条；仅在持有 _JOBS_LOCK 时调用。

    只淘汰已结束的作业，运行中的不会因数量超限被删除（否则前端轮询会拿到"作业不存在"）。
    """
    if len(_JOBS) < MAX_JOBS:
        return
    finished = sorted(
        (k for k, v in _JOBS.items() if v.get("status") in ("完成", "失败")),
        key=lambda k: _JOBS[k].get("started", 0),
    )
    for key in finished[: len(_JOBS) - MAX_JOBS + 1]:
        _JOBS.pop(key, None)


def _job_worker(job_id: str, fixture: str, preset: str, api_key: str,
                allow_external: bool) -> None:
    import os
    from finaudit.llm import LLMConfig
    from finaudit.pipeline import run
    from finaudit.presets import resolve

    job = _JOBS[job_id]
    try:
        src = _safe_fixture(fixture)
        if src is None:
            raise ValueError(f"夹具不存在或名称不合法：{fixture or '（空）'}")
        # 每次运行使用独立输出目录；重跑同一夹具或并发作业不覆盖彼此产物。
        out = OUT / f"web_{job_id}"
        llm_config = None
        if preset and preset != "mock":
            effective_key = api_key or os.environ.get("FINAUDIT_LLM_API_KEY", "")
            if not effective_key and preset != "local":
                raise ValueError("使用真实模型需要 API Key（仅当次使用，不写文件、不进日志）")
            p = resolve(preset)
            llm_config = LLMConfig(provider=p["provider"], model=p["model"],
                                   model_version=p.get("model_version", "unknown"),
                                   base_url=p["base_url"],
                                   extra_body=p.get("extra_body", {}),
                                   api_key=effective_key,
                                   allow_external=allow_external)
        if preset in ("", "mock") and not (src / "claims_gt.json").read_text(
                encoding="utf-8").strip().strip("[]").strip():
            raise ValueError("上传材料没有金标准主张清单，mock 模式无可抽取来源。"
                             "请改用本机模型（local）或外部模型预设，或改用内置示例夹具。")

        def sink(event: dict) -> None:
            with _JOBS_LOCK:
                evs = job["events"]
                evs.append(event)
                # 事件流只保留最近 MAX_JOB_EVENTS 条；阶段计数单独累加，不受截断影响
                if len(evs) > MAX_JOB_EVENTS:
                    del evs[: len(evs) - MAX_JOB_EVENTS]
                job["stage"] = event.get("stage", "")
                stage = event.get("stage")
                if stage:
                    counts = job.setdefault("stage_counts", {})
                    counts[stage] = counts.get(stage, 0) + 1

        summary = run(src, out, llm_config=llm_config, on_event=sink)
        with _JOBS_LOCK:
            job["status"] = "完成"
            job["result"] = _result_payload(out, job["events"], summary)
            job["task"] = out.name
    except Exception as exc:  # noqa: BLE001 - 界面层统一报错
        with _JOBS_LOCK:
            job["status"] = "失败"
            # 界面直接展示这段文字，不带异常类名前缀；类型信息在服务端日志可见
            job["error"] = str(exc) or type(exc).__name__


def _item_view(it: dict) -> dict:
    """O2 单条主张的界面视图。

    运行结果与历史任务共用这一份字段口径，避免同一主张在两处显示不同。
    """
    c, v = it["claim"], it["current_verification"]
    rule_ids = v.get("rule_ids") or []
    return {
        "id": c["claim_id"],
        "text": c.get("claim_text", ""),
        "type": f'{c.get("claim_type", "")}/{c.get("judgment_type", "")}',
        "status": v["status"],
        "reason": v.get("reason") or "",
        "loc": (v.get("source_locator") or {}).get("value") or "—",
        # 界面显示规则中文名（编号留在 title 里供追溯），避免整列内部编号
        "rules": "、".join(_rule_name(r) for r in rule_ids) or "—",
        "rule_names": [_rule_name(r) for r in rule_ids],
        "rule_ids": ",".join(rule_ids) or "—",
        "evidence": [{"candidate_id": e.get("candidate_id"),
                      "block_id": e.get("block_id"),
                      "material_id": e.get("material_id"),
                      "locator": (e.get("locator") or {}).get("value"),
                      "quote": (e.get("quote") or "")[:200],
                      "ocr_band": e.get("ocr_band")} for e in (it.get("evidence") or [])],
    }


def _rule_name(rule_id: str) -> str:
    """规则编号转中文名；未登记的编号原样返回，不编造名称。"""

    from finaudit.rules import RULES

    meta = RULES.get(rule_id)
    return meta["name"] if meta else rule_id


def _result_payload(out: Path, events: list[dict] | None = None,
                    summary: dict | None = None) -> dict:
    o2 = json.loads((out / "o2" / "O2-核查明细.json").read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    items = []
    for it in o2["items"]:
        view = _item_view(it)
        counts[view["status"]] = counts.get(view["status"], 0) + 1
        items.append(view)
    ev_path = out / "o3" / "C-events.jsonl"
    n_events = sum(1 for line in ev_path.read_text(encoding="utf-8").splitlines() if line)
    # 失败必须显式出口：LLM 调用失败 / 0 条主张，界面要显著提示，不呈现为"正常运行完成"
    warning = ""
    for e in events or []:
        if e.get("event_type") == "LLM调用" and (e.get("payload") or {}).get("failure_kind"):
            warning = (f"主张拆解 LLM 调用失败（已记 EV-05）："
                       f"{(e['payload'].get('error_summary') or '')[:120]}"
                       "；本次未拆解出主张，未产生任何判定（不静默编造）")
            break
    if not warning and not items:
        warning = "本次未拆解出任何主张（0 条）：请检查模型端点与研报内容，或改用内置夹具对照。"
    stability = (summary or {}).get("stability")
    return {
        "warning": warning, "counts": counts, "items": items,
        "metrics": {
            "主张数": len(items),
            "事件数": n_events,
            "定位覆盖": f"{sum(1 for it in o2['items'] if it['claim'].get('report_locator'))}/{len(items)}",
            "稳定性": "一致" if stability is True else (
                "不一致" if stability is False else "未验证"),
        },
        "note": f"输出目录：{out}　｜　O1 报告：{out / 'o1' / 'O1-核查报告.md'}",
    }


# ---------------------------------------------------------------- 受控呈现

_PAYLOAD_KEY_CN = {
    "tool": "工具", "failure_kind": "失败类型", "retry_count": "重试次数",
    "detail": "详情", "tool_version": "工具版本", "reason": "原因",
    "host": "外部端点", "endpoint": "外部端点", "payload_kind": "外发内容",
    "from": "原角色", "to": "目标角色", "block_id": "块", "material_id": "材料",
    "detail_ref": "详情来源", "actor": "主体", "note": "说明",
}


def _readable_payload(payload: dict, limit: int = 160) -> str:
    """把审计事件 payload 展平成「键=值；键=值」，供界面直接阅读。

    原始 payload 仍按 JSON 保留在 O3 事件流中；这里只做展示层转写，不改变审计内容。
    常见技术键转中文（未知键原样保留，不猜含义）。
    """
    parts = []
    for key, value in (payload or {}).items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        parts.append(f"{_PAYLOAD_KEY_CN.get(key, key)}={value}")
    text = "；".join(parts)
    return text if len(text) <= limit else text[:limit] + "…"


def _review_payload(name: str) -> dict:
    """F08b 复核界面呈现：只读四类区域，不提供材料集外访问。"""
    d = _safe_out(name)
    if d is None:
        return {"ok": False, "error": "越权：只允许呈现本任务输出目录内的内容（OA-03）"}
    o2_path = d / "o2" / "O2-核查明细.json"
    if not o2_path.exists():
        return {"ok": False, "error": "该任务无核验明细，请先运行核验"}
    try:
        o2 = json.loads(o2_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"ok": False,
                "error": "该任务的核验明细已损坏，无法读取；请重新运行该任务生成新的明细"}
    events_path = d / "o3" / "C-events.jsonl"
    evs = [json.loads(line) for line in events_path.read_text(
        encoding="utf-8").splitlines() if line] if events_path.exists() else []
    audit = [{"seq": e["seq"], "event_type": e["event_type"],
              "actor": e["actor"]["id"],
              "summary": _readable_payload(e["payload"])}
             for e in evs
             if e["event_type"] in ("审计复核", "越权", "工具失败", "外发请求", "升级")]
    calcs = []
    dc = d / "o3" / "D-calculations.json"
    if dc.exists():
        for c in json.loads(dc.read_text(encoding="utf-8")).get("calculations", []):
            o = c.get("output") or {}
            # 记录里 D 组已给出带单位的 value_text（如 "35.7187%" / "128,499,687,543.21元"），
            # 直接用它；只有缺失时才退回原始 value 做兜底格式化。原始值不改动。
            out_text = o.get("value_text") or ""
            if not out_text:
                raw = o.get("value")
                if raw is None:
                    out_text = "—"
                else:
                    out_text = str(raw)
                    try:
                        fv = float(out_text)
                        if "e" not in out_text.lower() and abs(fv) < 1e6:
                            out_text = f"{fv:.6f}".rstrip("0").rstrip(".") or "0"
                    except ValueError:
                        pass
            calcs.append({"calc_id": c.get("calc_id"),
                          "formula_id": c.get("formula_id"),
                          "formula_text": c.get("formula_text") or "",
                          "output": out_text,
                          "result_status": c.get("result_status"),
                          "error": c.get("error") or ""})
    review_log = []
    rl = d / "o3" / "review-log.jsonl"
    if rl.exists():
        review_log = [json.loads(line) for line in rl.read_text(
            encoding="utf-8").splitlines() if line]
    files = [{"name": n} for n in DOWNLOAD_WHITELIST
             if (_download_path(name, n) is not None)]
    return {
        "ok": True,
        "task": name,
        "materials": _registered_materials(d),
        # 历史任务同样还原 Agent 面板：事件流就在 C-events.jsonl，不必显示"尚未启动"
        "agents": _agent_progress(evs, "完成") if evs else [],
        "events": [_public_event_summary(e) for e in evs[-60:]],
        "stage": (evs[-1].get("stage") if evs else ""),
        "run_status": "完成" if evs else "",
        "stage_event_counts": _stage_event_counts(evs),
        "status": [{"claim_id": it["claim"]["claim_id"],
                    "status": it["current_verification"]["status"],
                    "text": it["claim"].get("claim_text", "")} for it in o2["items"]],
        "items": [_item_view(it) for it in o2["items"]],
        "calcs": calcs,
        "audit": audit[-20:],
        "review_log": review_log,
        "files": files,
    }


def _registered_materials(d: Path) -> list[dict]:
    a_path = d / "o3" / "A-inputs.json"
    if not a_path.exists():
        return []
    return [{"material_id": r.get("material_id"),
             "sha256": (r.get("sha256") or "")[:16],
             "source_file_ref": r.get("source_file_ref")}
            for r in json.loads(a_path.read_text(encoding="utf-8")).get("materials", [])]


def _block_payload(name: str, block_id: str) -> dict:
    """证据定位：返回受控材料区内该块的原文（HUMAN 只读，不给原始文件下载）。"""
    d = _safe_out(name)
    if d is None:
        return {"ok": False, "error": "越权：只允许呈现本任务输出目录内的内容"}
    a_path = d / "o3" / "A-inputs.json"
    if not a_path.exists():
        return {"ok": False, "error": "该任务无输入索引"}
    try:
        blocks = json.loads(a_path.read_text(encoding="utf-8")).get("blocks", [])
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"ok": False, "error": "该任务的输入索引已损坏，无法定位原文"}
    for b in blocks:
        if b.get("block_id") == block_id:
            return {"ok": True, "block_id": block_id,
                    "material_id": b.get("material_id"),
                    "page": b.get("page"),
                    "locator": (b.get("locator") or {}).get("value"),
                    "parse_status": b.get("parse_status"),
                    "content_text": b.get("content_text") or ""}
    # 旧版运行记录（1.6.x）的 A-inputs 还没有块索引：退回该任务核验明细里
    # 证据自带的引文，并明确标注来源，不把引文冒充成原文块。
    quote = _quote_from_o2(d, block_id)
    if quote:
        return {"ok": True, "block_id": block_id, "legacy": True,
                "material_id": quote.get("material_id"),
                "page": (quote.get("locator") or {}).get("page"),
                "locator": (quote.get("locator") or {}).get("value"),
                "parse_status": "旧版记录（无块索引）",
                "content_text": quote.get("quote") or ""}
    return {"ok": False, "error": f"该任务材料集内没有块 {block_id}"}


def _quote_from_o2(d: Path, block_id: str) -> dict | None:
    """在核验明细的证据里找该块的引文（仅旧版记录缺少块索引时使用）。"""

    o2_path = d / "o2" / "O2-核查明细.json"
    if not o2_path.exists():
        return None
    try:
        items = json.loads(o2_path.read_text(encoding="utf-8")).get("items", [])
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    for it in items:
        for e in it.get("evidence") or []:
            if e.get("block_id") == block_id and (e.get("quote") or "").strip():
                return e
    return None


def _review_action(name: str, payload: dict) -> dict:
    """人工复核操作：只写复核记录并留痕，不改状态（状态唯一写点仍是 RA5）。"""
    d = _safe_out(name)
    if d is None:
        return {"ok": False, "error": "越权：操作范围超出本任务输出目录"}
    claim_id = payload.get("claim_id")
    action = payload.get("action")
    operator = payload.get("operator") or "人工复核员"
    actions = ("确认已查看（仅留痕）", "标记需人工复核（仅留痕）")
    if not claim_id or action not in actions:
        return {"ok": False, "error": "缺 claim_id 或处置不在允许集合"}
    o2_path = d / "o2" / "O2-核查明细.json"
    if not o2_path.is_file():
        return {"ok": False, "error": "任务核验明细不存在，无法确认主张编号"}
    claims = json.loads(o2_path.read_text(encoding="utf-8")).get("items", [])
    if not any((item.get("claim") or {}).get("claim_id") == claim_id for item in claims):
        return {"ok": False, "error": "主张编号不属于当前任务"}
    import datetime
    rec = {"review_id": f"RV-{uuid.uuid4().hex}", "claim_id": claim_id,
           "action": action, "operator": operator,
           "at": datetime.datetime.now().isoformat(timespec="seconds"),
           "note": str(payload.get("note") or "")[:1000]}
    log = d / "o3" / "review-log.jsonl"
    with _REVIEW_LOCK:
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True, "record": rec,
            "note": "已留痕；状态变更需由 RA5 重核写入（HUMAN 不得改状态）"}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/api/review/action"):
            body = self._read_json()
            if body.get("_request_error"):
                self._json({"ok": False, "error": body["_request_error"]}, status=400)
                return
            self._json(_review_action(body.get("task", ""), body))
            return
        if path.startswith("/api/run"):
            body = self._read_json()
            if body.get("_request_error"):
                self._json({"ok": False, "error": body["_request_error"]}, status=400)
                return
            self._json(self._start_job(body))
            return
        if not path.startswith("/api/upload"):
            self.send_error(404)
            return
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._json({"ok": False, "error": "需要 multipart/form-data"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json({"ok": False, "error": "Content-Length 格式无效"}, status=400)
            return
        if content_length <= 0 or content_length > MAX_TOTAL_BYTES + MAX_MULTIPART_OVERHEAD_BYTES:
            self._json({"ok": False, "error": "上传请求体超过允许大小或为空"}, status=413)
            return
        raw = self.rfile.read(content_length)
        if len(raw) != content_length:
            self._json({"ok": False, "error": "上传请求体不完整"}, status=400)
            return
        msg = BytesParser(policy=default).parsebytes(
            b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw)
        files: dict[str, bytes] = {}
        params: dict[str, str] = {}
        for part in msg.get_payload():
            name = part.get_param("name", header="content-disposition")
            fname = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if fname:
                if fname in files:
                    self._json({"ok": False, "error": f"上传文件名重复：{fname}"}, status=400)
                    return
                files[fname] = payload
            elif name:
                params[name] = payload.decode("utf-8", "ignore")
        path, tid = _make_fixture(files, params)
        if path is None:
            self._json({"ok": False, "error": tid})
            return
        self._json({"ok": True, "task_dir": path, "fixture": "U:" + tid,
                    "files": list(files), "params": params})

    def _start_job(self, body: dict) -> dict:
        fixture = body.get("fixture", "demo01")
        preset = body.get("preset", "mock")
        allow_external = bool(body.get("allow_external"))
        api_key = body.get("key", "")
        if _safe_fixture(str(fixture)) is None:
            return {"ok": False,
                    "error": f"夹具不存在或名称不合法：{fixture or '（空）'}"}
        from finaudit.presets import resolve
        try:
            cfg = resolve(preset)
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}
        if cfg["egress"] == "external" and not allow_external:
            return {"ok": False,
                    "error": f"外部端点 {cfg['base_url']} 未授权：请勾选「确认外发」，"
                             "或改用 local/mock 预设。未授权时不发送研报正文。"}
        job_id = "J" + uuid.uuid4().hex[:8]
        with _JOBS_LOCK:
            _evict_finished_jobs()
            _JOBS[job_id] = {"status": "运行中", "stage": "", "events": [],
                             "result": None, "error": None, "task": None,
                             "started": time.time()}
        threading.Thread(target=_job_worker,
                         args=(job_id, fixture, preset, api_key, allow_external),
                         daemon=True).start()
        return {"ok": True, "job_id": job_id}

    def _read_json(self) -> dict:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {"_request_error": "Content-Length 格式无效"}
        if content_length < 0 or content_length > MAX_JSON_BODY_BYTES:
            return {"_request_error": "JSON 请求体超过允许大小"}
        raw = self.rfile.read(content_length) or b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        q = parse_qs(url.query)
        path = url.path
        if path == "/api/agents":
            self._json({"ok": True, "agents": _agent_progress([], "未开始", "")})
            return
        if path.startswith("/api/job"):
            job_id = q.get("id", [""])[0]
            with _JOBS_LOCK:
                job = dict(_JOBS.get(job_id, {}))
                if job:
                    job["events"] = list(job.get("events") or [])
            if not job:
                self._json({"ok": False, "error": "作业不存在"})
                return
            events = job.get("events") or []
            stage_counts = {f"S{i}": 0 for i in range(8)}
            stage_counts.update(job.get("stage_counts") or {})
            self._json({"ok": True, "status": job.get("status"), "stage": job.get("stage"),
                        "events": [_public_event_summary(e) for e in events[-200:]],
                        "stage_event_counts": stage_counts,
                        "agents": _agent_progress(events, job.get("status", "运行中"),
                                                  job.get("stage", "")),
                        "task": job.get("task"),
                        "error": job.get("error"),
                        **(job.get("result") or {})})
            return
        if path.startswith("/api/tasks"):
            self._json({"ok": True, "tasks": _list_tasks()})
            return
        if path.startswith("/api/review"):
            self._json(_review_payload(q.get("task", [""])[0]))
            return
        if path.startswith("/api/block"):
            self._json(_block_payload(q.get("task", [""])[0], q.get("block_id", [""])[0]))
            return
        if path.startswith("/api/download"):
            name = q.get("task", [""])[0]
            target = _download_path(name, q.get("file", [""])[0])
            if target is None:
                self._json({"ok": False, "error": "越权或文件不在交付白名单内"})
                return
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream; charset=utf-8")
            # 中文文件名走 RFC 5987：latin-1 装不下，必须百分号编码
            ascii_name = target.name.encode("ascii", "ignore").decode() or "download"
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(target.name)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def _guard_api(method):
    """接口兜底：单个任务目录损坏（坏 JSON、权限异常等）只影响该次请求。

    没有这层保护时，未捕获异常会直接断开连接，前端只能看到 fetch 失败。
    """

    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            print(f"[webui] {getattr(self, 'command', '?')} {getattr(self, 'path', '?')} "
                  f"处理失败：{exc}", file=sys.stderr)
            try:
                self._json({"ok": False, "error": f"服务端处理失败：{exc}"}, status=500)
            except Exception:  # noqa: BLE001
                pass

    return wrapper


Handler.do_GET = _guard_api(Handler.do_GET)
Handler.do_POST = _guard_api(Handler.do_POST)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"研鉴 Web 界面：http://127.0.0.1:{args.port}")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(
            f"http://127.0.0.1:{args.port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
