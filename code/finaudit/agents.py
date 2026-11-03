"""Agent 框架层：角色、权限边界、交接契约、Supervisor 编排。

对齐规划文档：
- 运行时 Agent 定义（RA1–RA8、人工复核员、确定性程序层）
- 失败/重试/升级；区域读写权限
- 越权边界与越权用例
- 规划步数上限 12、工具重试上限 2 次

设计：Agent 只知道"我读什么、写什么、禁止什么"；越权在写操作时被拦截并留痕，
不靠调用方自觉。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------- 区域与角色

REGIONS = {
    "materials": "受控材料区（材料文件与登记表）",
    "blocks": "文档结构树与定位索引",
    "candidates": "证据候选集",
    "claims": "原子主张清单",
    "status": "核验结果与状态字段",
    "calcs": "计算记录",
    "audit": "审计复核记录",
    "review": "人工复核记录",
    "logs": "全流程日志",
    "delivery": "交付区（O1/O2/O3）",
}


@dataclass(frozen=True)
class Role:
    code: str
    name: str
    stage: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    forbidden: tuple[str, ...]


ROLES: dict[str, Role] = {
    "RA1": Role("RA1", "Supervisor", "S0",
                ("I1 待核验研报", "I2 原始证据材料", "I3 受理参数"),
                ("任务单", "编排决策", "升级记录"),
                REGIONS.keys(), ("materials", "logs"),
                ("核验判定", "计算", "报告撰写", "写任何业务数据内容")),
    "RA2": Role("RA2", "文档解析 Agent", "S2",
                ("已登记材料",), ("文档结构树 + 定位索引",),
                ("materials",), ("blocks",),
                ("判断内容真伪", "跨材料比对", "写账本或主张清单")),
    "RA3": Role("RA3", "事实检索 Agent", "S3",
                ("文档结构树与定位索引", "检索请求"),
                ("候选证据集", "规范化事实草案"),
                ("blocks", "claims"), ("candidates",),
                ("跨材料集检索", "真伪判定", "直接写事实/证据账本")),
    "RA4": Role("RA4", "主张拆解 Agent（LLM）", "S4",
                ("研报解析结果",), ("原子主张清单",),
                ("blocks",), ("claims",),
                ("判断真伪", "检索证据", "写状态字段")),
    "RA5": Role("RA5", "核验 Agent", "S5",
                ("原子主张清单", "候选证据集", "计算记录"),
                ("逐项核验结果", "计算请求"),
                ("claims", "candidates", "calcs"), ("status",),
                ("自行计算关键数字", "改写证据", "修改他人已写结果")),
    "RA6": Role("RA6", "财务计算 Agent", "S5",
                ("计算请求",), ("计算记录",),
                ("candidates",), ("calcs",),
                ("估值模型与假设外推", "核验结论解释", "写核验状态")),
    "RA7": Role("RA7", "独立审计 Agent", "S6",
                ("核验结果", "全部中间产物只读"),
                ("审计复核记录", "升级项清单"),
                REGIONS.keys(), ("audit",),
                ("改写状态", "新增事实", "替代人工确认判错项", "写 O1/O2/O3")),
    "RA8": Role("RA8", "报告 Agent", "S7",
                ("审定结果", "全流程日志"), ("O1 人读报告",),
                ("status", "audit", "review", "logs"), ("delivery",),
                ("新增事实", "改状态", "生成 O2/O3", "自行判定内容真伪")),
    "HUMAN": Role("HUMAN", "人工复核员", "S6",
                  ("受控界面呈现的证据与结果",), ("人工复核记录",),
                  ("materials", "candidates", "status", "audit"), ("review",),
                  ("访问材料集外文件", "导出原始材料", "无留痕修改")),
    "EXPORT": Role("EXPORT", "导出程序", "S7",
                   ("审定结果", "日志", "计算", "审计"), ("O2", "O3"),
                   ("status", "calcs", "audit", "logs", "review"), ("delivery",),
                   ("改状态", "新增事实")),
}

# 失败重试与步数上限
MAX_PLANNING_STEPS = 12
TOOL_MAX_RETRIES = 2
LLM_MAX_RETRIES = 1


class PermissionDenied(Exception):
    """Agent 尝试写入未授权区域。"""


# ---------------------------------------------------------------- 权限校验

def assert_write(role_code: str, region: str, *, events=None,
                 detail: str = "") -> None:
    """写操作前校验；越权抛 PermissionDenied 并留痕（TB-5 / OA-01）。"""
    role = ROLES.get(role_code)
    if role is None:
        raise PermissionDenied(f"未登记的角色：{role_code}")
    if region not in role.writes:
        if events is not None:
            try:
                events.emit("越权", role.stage, "Agent", role_code,
                            {"目标区域": region, "授权区域": list(role.writes),
                             "detail": detail[:120]})
            except Exception:  # noqa: BLE001 - 留痕失败不掩盖越权本身
                pass
        raise PermissionDenied(
            f"{role_code}（{role.name}）无权写入 {region}；授权写入：{list(role.writes)}")


def assert_read(role_code: str, region: str) -> None:
    role = ROLES.get(role_code)
    if role is None:
        raise PermissionDenied(f"未登记的角色：{role_code}")
    if region not in role.reads:
        raise PermissionDenied(f"{role_code} 无权读取 {region}")


# ---------------------------------------------------------------- 交接契约

@dataclass(frozen=True)
class Handoff:
    """交接物：接收方校验必需字段齐全才接收，否则退回上游。"""
    code: str            # H1–H11
    name: str
    src: str
    dst: str
    required: tuple[str, ...]


HANDOFFS: dict[str, Handoff] = {
    "H1": Handoff("H1", "任务受理参数", "RA1", "登记程序", ("subject_alias", "period_scope", "materials")),
    "H2": Handoff("H2", "材料登记表", "登记程序", "RA2", ("material_id", "sha256", "format")),
    "H3": Handoff("H3", "文档结构树与定位索引", "RA2", "RA3/RA4", ("block_id", "locator")),
    "H4": Handoff("H4", "候选证据集", "RA3", "RA5", ("candidate_id", "locator")),
    "H5": Handoff("H5", "原子主张清单", "RA4", "RA5", ("claim_id", "claim_text")),
    "H6": Handoff("H6", "计算请求", "RA5", "RA6", ("formula_id", "输入值及来源定位")),
    # 失败的计算没有输出值，但必须有错误说明——两者居其一即算交接到位
    "H7": Handoff("H7", "计算记录", "RA6", "RA5", ("calc_id", "公式", "输出值或错误说明")),
    "H8": Handoff("H8", "核验结果", "RA5", "RA7", ("vrf_id", "claim_id", "status")),
    "H9": Handoff("H9", "升级项清单", "RA7", "HUMAN", ("audit_id", "复核范围", "发现")),
    "H10": Handoff("H10", "人工复核记录", "HUMAN", "RA8", ("review_id", "处置", "操作人")),
    "H11": Handoff("H11", "审计发现", "RA7", "RA8", ("audit_id", "处置")),
}


def record_handoff(actor_id: str, code: str, payload: dict, *, events=None,
                   stage: str = "S1", dst: str = "") -> list[str]:
    """交接物接收判定并留痕：actor_id 可为 Agent 编号或确定性程序名。"""
    h = HANDOFFS.get(code)
    if h is None:
        raise ValueError(f"未登记的交接物：{code}")
    # 判空而非判真：计数值 0 是有效值（如"发现 0 项"），空列表/空串才算缺项
    missing = [f for f in h.required
               if payload.get(f) in (None, "", [], {})]
    if events is not None:
        events.emit(
            "交接", stage, "Agent" if actor_id in ROLES else "程序", actor_id,
            {"交接物": code, "名称": h.name, "接收方": dst or h.dst, "缺失字段": missing},
            handoff={"code": code, "missing": missing})
    return missing


def verify_handoff(code: str, payload: dict) -> list[str]:
    """交接物接收判定：返回缺失字段列表（空=接收通过）。

    判空而非判真：计数值 0 是有效值（如"发现 0 项"），空列表/空串才算缺项。
    """
    h = HANDOFFS.get(code)
    if h is None:
        return [f"未登记的交接物：{code}"]
    return [f for f in h.required if payload.get(f) in (None, "", [], {})]


# ---------------------------------------------------------------- Agent 基类

@dataclass
class Ctx:
    """Agent 共享上下文：只按角色权限读写。"""
    task_id: str = ""
    data: dict = field(default_factory=dict)
    steps: int = 0
    escalations: list[str] = field(default_factory=list)

    def put(self, role: str, region: str, key: str, value, *, events=None) -> None:
        assert_write(role, region, events=events, detail=f"写入 {key}")
        self.data.setdefault(region, {})[key] = value

    def get(self, role: str, region: str, key: str | None = None):
        assert_read(role, region)
        region_data = self.data.get(region, {})
        return region_data if key is None else region_data.get(key)


class Agent:
    """Agent 基类：校验输入 → 执行 → 校验输出 → 留痕。

    execute_fn 由编排层注入真实执行体（确定性实现），Agent 层只负责边界与留痕；
    逐条执行的角色（RA3/RA5/RA6）用 count_step=False，不占规划步数（上限 12）。
    """

    def __init__(self, ctx: Ctx, events=None, execute_fn=None):
        self.ctx = ctx
        self.events = events
        self.execute_fn = execute_fn

    @property
    def code(self) -> str:
        raise NotImplementedError

    @property
    def role(self) -> Role:
        return ROLES[self.code]

    def check_inputs(self) -> None:
        """子类可覆盖：输入契约校验（缺项则抛 ValueError）。"""

    def execute(self):
        if self.execute_fn is None:
            raise NotImplementedError(f"{self.code} 未注入执行体")
        return self.execute_fn()

    def check_outputs(self, result) -> None:
        """子类可覆盖：输出契约校验。"""

    def run(self, *, count_step: bool = True):
        if count_step:
            self.ctx.steps += 1
            if self.ctx.steps > MAX_PLANNING_STEPS:
                raise RuntimeError(f"规划步数超过上限 {MAX_PLANNING_STEPS}")
        self.check_inputs()
        result = self.execute()
        self.check_outputs(result)
        return result

    def handoff(self, code: str, payload: dict, *, dst: str = "") -> list[str]:
        """交接物接收判定并留痕（交接契约）：返回缺失字段列表（空=接收通过）。"""
        missing = verify_handoff(code, payload)
        if self.events is not None:
            self.events.emit(
                "交接", self.role.stage, "Agent", self.code,
                {"交接物": code, "名称": HANDOFFS[code].name,
                 "接收方": dst or HANDOFFS[code].dst, "缺失字段": missing,
                 "数量": len(payload) if isinstance(payload, list) else None},
                handoff={"code": code, "missing": missing})
        return missing


# ---------------------------------------------------------------- Supervisor

class Supervisor(Agent):
    """RA1：按 S0→S7 编排，步数/重试/升级受控。"""

    @property
    def code(self) -> str:
        return "RA1"

    def execute(self):
        from . import pipeline  # 延迟导入：执行体复用现有确定性实现
        return pipeline._run_stages(self.ctx, self.events)


# ---------------------------------------------------------------- 各 Agent
# 执行体委托 pipeline 的确定性实现（保持行为等价），Agent 层负责边界与留痕。

class ParserAgent(Agent):
    @property
    def code(self) -> str:
        return "RA2"

    def check_outputs(self, result) -> None:
        # 执行体返回 (blocks, rows) 元组或 {"blocks": [...]} 字典
        blocks = result[0] if isinstance(result, tuple) else (result or {}).get("blocks", [])
        for b in blocks:
            if not b.get("block_id"):
                raise ValueError("解析输出缺 block_id（H3 接收判定失败）")


class RetrieverAgent(Agent):
    @property
    def code(self) -> str:
        return "RA3"

    def check_outputs(self, result) -> None:
        # 执行体返回候选列表或 {"candidates": [...]} 字典
        if isinstance(result, dict):
            cands = (result or {}).get("candidates", [])
        else:
            cands = result or []
        for c in cands:
            if not c.get("candidate_id"):
                raise ValueError("候选证据缺 candidate_id（H4 接收判定失败）")


class ExtractorAgent(Agent):
    @property
    def code(self) -> str:
        return "RA4"

    def check_outputs(self, result) -> None:
        for c in result or []:
            if not c.get("claim_id") or not c.get("claim_text"):
                raise ValueError("主张缺 claim_id/claim_text（H5 接收判定失败）")


class VerifierAgent(Agent):
    @property
    def code(self) -> str:
        return "RA5"

    def check_outputs(self, result) -> None:
        # 执行体返回单条判定（dict）、单条 item 或 items 列表
        if isinstance(result, dict) and "current_verification" not in result:
            rows = [{"current_verification": result}]
        elif isinstance(result, dict):
            rows = [result]
        else:
            rows = result or []
        for it in rows:
            v = it.get("current_verification") or {}
            if not v.get("status"):
                raise ValueError("核验结果缺 status（H8 接收判定失败）")


class CalculatorAgent(Agent):
    @property
    def code(self) -> str:
        return "RA6"

    def check_outputs(self, result) -> None:
        # 执行体返回 (calc, error) 元组或计算记录列表
        if isinstance(result, tuple):
            calcs = [result[0]] if result and result[0] else []
        else:
            calcs = result or []
        for c in calcs:
            if not c.get("calc_id"):
                raise ValueError("计算记录缺 calc_id（H7 接收判定失败）")


class AuditorAgent(Agent):
    @property
    def code(self) -> str:
        return "RA7"

    def check_outputs(self, result) -> None:
        if (result or {}).get("audit_ok") is None:
            raise ValueError("审计结果缺 audit_ok（H9/H11 接收判定失败）")


class ReporterAgent(Agent):
    @property
    def code(self) -> str:
        return "RA8"


def roster() -> list[dict]:
    """Agent 名册（供 CLI 打印与交付文档引用）。"""
    return [{"code": r.code, "name": r.name, "stage": r.stage,
             "inputs": list(r.inputs), "outputs": list(r.outputs),
             "reads": list(r.reads), "writes": list(r.writes),
             "forbidden": list(r.forbidden)} for r in ROLES.values()]
