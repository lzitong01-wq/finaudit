"""轻量契约校验（数据契约第 6 节子集）。

本模块只校验引擎实际产出的对象：Claim / Calculation / VerificationResult / AuditEvent /
EvidenceCandidate / MaterialRecord / DocumentBlock / Fact。校验项：必填、状态枚举、
事件类型枚举、编号 pattern、payload 禁敏键名、白名单外字段。
"""

from __future__ import annotations

import re

STATUS = ["待核验", "通过", "错误", "冲突", "证据不足", "低置信", "需人工复核", "不适用"]

EVENT_TYPES = [
    "任务单创建", "材料登记", "解析调用", "检索调用", "LLM调用", "规则判定",
    "计算", "交接", "审计复核", "人工复核", "导出", "评测运行", "工具失败",
    "重试", "越权", "外发请求", "升级", "状态回退", "复现失败",
]

ID_RE = re.compile(r"^[A-Z]{3,4}-[0-9]{4}$")
SHA_RE = re.compile(r"^[a-fA-F0-9]{64}$")

FORBIDDEN_PAYLOAD_KEYS = (
    "secret", "token", "password", "credential", "api_key", "private_key",
)

REQUIRED: dict[str, list[str]] = {
    "MaterialRecord": ["material_id", "task_id", "sha256", "format", "status"],
    "DocumentBlock": ["block_id", "task_id", "material_id", "block_type", "page",
                      "content_text", "parse_status"],
    "EvidenceCandidate": ["candidate_id", "task_id", "material_id", "block_id",
                          "locator", "quote", "truth_judgment"],
    "Fact": ["fact_id", "task_id", "statement", "content", "source", "status"],
    "Claim": ["claim_id", "task_id", "report_material_id", "claim_text",
              "claim_type", "judgment_type", "unknown_fields", "status"],
    "Calculation": ["calc_id", "task_id", "claim_id", "formula_id", "inputs",
                    "tolerance", "result_status", "assumptions", "boundaries"],
    "VerificationResult": ["vrf_id", "task_id", "claim_id", "status", "reason",
                           "rule_ids", "decided_by"],
    "AuditEvent": ["event_id", "task_id", "seq", "event_type", "stage", "actor"],
}

ALLOWED_EXTRA: dict[str, set[str]] = {
    "MaterialRecord": {"source_file_ref", "format_detail", "page_count", "registered_at",
                       "registered_by", "failure_reason", "contract_version",
                       "publish_date"},
    "DocumentBlock": {"contract_version", "locator", "ocr", "quality_flags",
                       "parent_block_id", "child_order", "version"},
    "EvidenceCandidate": {"contract_version", "locator", "ocr_band", "retrieval",
                           "normalized_fact_draft", "relevance_note"},
    "Fact": {"contract_version", "source_candidate_id", "entered_by", "entered_at",
             "version", "supersedes_fact_id"},
    "Claim": {"contract_version", "report_locator", "context_quote", "numeric",
              "reference", "decomposed_by", "decomposed_at", "llm_event_id", "version",
              "metric", "entity"},
    "Calculation": {"contract_version", "crq_id", "formula_text", "tool", "output",
                    "executed_by", "executed_at", "params", "tolerance", "comparison",
                    "error", "version"},
    "VerificationResult": {"contract_version", "evidence_refs", "source_locator",
                            "calc_refs", "suggestion", "decided_at",
                            "supersedes_vrf_id", "superseded_by_vrf_id"},
    "AuditEvent": {"contract_version", "payload", "related_ids", "handoff",
                    "input_hash", "output_hash"},
}


def _check_id(value: str) -> bool:
    return isinstance(value, str) and bool(ID_RE.match(value))


def validate(obj: dict, object_name: str) -> list[str]:
    """返回错误清单；空列表 = 通过。"""
    errors: list[str] = []
    if object_name not in REQUIRED:
        return [f"未注册的校验对象: {object_name}"]

    for f in REQUIRED[object_name]:
        if f not in obj or obj[f] in (None, ""):
            errors.append(f"{object_name}.{f}: 必填缺失")

    allowed = set(REQUIRED[object_name]) | ALLOWED_EXTRA.get(object_name, set())
    for k in obj:
        if k not in allowed:
            errors.append(f"{object_name}.{k}: 未声明字段（契约 additionalProperties=false）")

    if object_name in ("Claim", "VerificationResult") and "status" in obj:
        if obj.get("status") not in STATUS:
            errors.append(f"{object_name}.status: 非法状态值 {obj.get('status')!r}")

    if object_name == "MaterialRecord" and obj.get("status") not in ("已登记", "无效", "解析失败"):
        errors.append(f"MaterialRecord.status: 非法值 {obj.get('status')!r}")

    if object_name == "DocumentBlock" and obj.get("parse_status") not in ("已解析", "定位缺失", "解析失败"):
        errors.append(f"DocumentBlock.parse_status: 非法值 {obj.get('parse_status')!r}")

    for id_field in ("material_id", "block_id", "candidate_id", "fact_id", "claim_id",
                     "calc_id", "vrf_id", "event_id"):
        if id_field in obj and obj[id_field] is not None and not _check_id(obj[id_field]):
            errors.append(f"{object_name}.{id_field}: 编号格式错误")

    if object_name == "MaterialRecord" and "sha256" in obj:
        if not SHA_RE.match(str(obj["sha256"])):
            errors.append("MaterialRecord.sha256: 需 64 位十六进制")

    if object_name == "AuditEvent":
        if obj.get("event_type") not in EVENT_TYPES:
            errors.append(f"AuditEvent.event_type: 枚举外值 {obj.get('event_type')!r}")
        if not isinstance(obj.get("seq"), int) or obj.get("seq", 0) < 1:
            errors.append("AuditEvent.seq: 需为正整数")
        if not obj.get("stage", "").startswith("S"):
            errors.append("AuditEvent.stage: 需为 S0–S8")
        payload = obj.get("payload") or {}
        for k in payload:
            low = k.lower()
            if any(bad in low for bad in FORBIDDEN_PAYLOAD_KEYS):
                errors.append(f"AuditEvent.payload.{k}: 键名触发禁敏规则")

    if object_name == "Calculation":
        if obj.get("result_status") not in ("成功", "参数校验失败", "超时", "工具内部失败"):
            errors.append(f"Calculation.result_status: 非法值 {obj.get('result_status')!r}")
        if obj.get("result_status") != "成功" and not obj.get("error"):
            errors.append("Calculation.error: 失败时必须填写")
        # output 只在成功时必填：失败的计算（如分母为零）不得产出数值，
        # 强行要求 output 会逼实现方编一个占位值，与「不以 0 或无穷代替结果」冲突。
        if obj.get("result_status") == "成功":
            out = obj.get("output") or {}
            if out.get("value") in (None, ""):
                errors.append("Calculation.output.value: 成功时必须存在")
        if not (obj.get("assumptions") or isinstance(obj.get("assumptions"), list)):
            errors.append("Calculation.assumptions: 需为列表（可为空）")
        if not obj.get("boundaries"):
            errors.append("Calculation.boundaries: 必填（不得为空串）")

    if object_name == "VerificationResult":
        if obj.get("status") == "错误":
            if not obj.get("source_locator"):
                errors.append("VerificationResult.source_locator: 判错项必填")
            if not obj.get("suggestion"):
                errors.append("VerificationResult.suggestion: 判错项必填")
        if obj.get("status") == "通过" and not obj.get("evidence_refs"):
            errors.append("VerificationResult.evidence_refs: 通过项至少一条依据")

    return errors
