"""审计事件（事件契约 / 安全边界）。

- event_type 仅 19 值；payload 键名不得含禁敏子串；
- seq 与 event_id 一对一、从 1 起单调递增；
- 事件是唯一的过程留痕，O3 的 C 组来源。
"""

from __future__ import annotations

from .ids import IdAllocator
from .contract import EVENT_TYPES, FORBIDDEN_PAYLOAD_KEYS

RELATED_KEYS = [
    "material_id", "block_id", "claim_id", "candidate_id", "fact_id",
    "crq_id", "calc_id", "vrf_id", "audit_id", "mrv_id",
]

ACTOR_KINDS = ("Agent", "程序", "人工复核员")
STAGES = [f"S{i}" for i in range(9)]


class EventLog:
    def __init__(self, task_id: str, alloc: IdAllocator, sink=None):
        self.task_id = task_id
        self.alloc = alloc
        self.seq = 0
        self.events: list[dict] = []
        # sink：可选的实时订阅回调（界面进度用）。写文件仍是 report 层职责。
        self.sink = sink

    def emit(
        self,
        event_type: str,
        stage: str,
        actor_kind: str,
        actor_id: str,
        payload: dict | None = None,
        related_ids: dict | None = None,
        handoff: dict | None = None,
    ) -> dict:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"事件类型不在 19 值枚举内: {event_type}")
        if stage not in STAGES:
            raise ValueError(f"阶段非法: {stage}")
        if actor_kind not in ACTOR_KINDS:
            raise ValueError(f"主体类别非法: {actor_kind}")

        payload = payload or {}
        for k in payload:
            low = k.lower()
            if any(bad in low for bad in FORBIDDEN_PAYLOAD_KEYS):
                raise ValueError(f"payload 键名触发禁敏规则: {k}")

        self.seq += 1
        rel = {k: None for k in RELATED_KEYS}
        if related_ids:
            unknown = set(related_ids) - set(RELATED_KEYS)
            if unknown:
                raise ValueError(f"related_ids 含未声明键: {unknown}")
            rel.update(related_ids)

        event = {
            "event_id": self.alloc.next("event"),
            "task_id": self.task_id,
            "contract_version": "finaudit-data-contract-v1",
            "seq": self.seq,
            "event_type": event_type,
            "stage": stage,
            "actor": {"kind": actor_kind, "id": actor_id},
            "payload": payload,
            "related_ids": rel,
            "handoff": handoff,
            "input_hash": None,
            "output_hash": None,
        }
        self.events.append(event)
        if self.sink is not None:
            try:
                self.sink(event)
            except Exception:  # noqa: BLE001 - 订阅失败不影响主流程留痕
                pass
        return event

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.events:
            out[e["event_type"]] = out.get(e["event_type"], 0) + 1
        return out
