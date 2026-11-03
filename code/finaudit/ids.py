"""ID 分配器（编号规则）。

规则：任务内同类型唯一、按创建顺序单调递增、不依赖时钟与随机数；
格式 `<PREFIX>-NNNN`（4 位十进制零填充）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

PREFIXES: dict[str, str] = {
    "task": "TSK",
    "material": "MAT",
    "block": "BLK",
    "candidate": "CND",
    "fact": "FCT",
    "claim": "CLM",
    "crq": "CRQ",
    "calc": "CALC",
    "vrf": "VRF",
    "audit": "AUD",
    "mrv": "MRV",
    "event": "EVT",
}

@dataclass
class IdAllocator:
    """按类型发号；同一实例内单调递增，重复运行同输入得到同一序列。"""

    counters: dict[str, int] = field(default_factory=dict)

    def next(self, kind: str) -> str:
        if kind not in PREFIXES:
            raise KeyError(f"未知 ID 类型: {kind}")
        prefix = PREFIXES[kind]
        n = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = n
        return f"{prefix}-{n:04d}"
