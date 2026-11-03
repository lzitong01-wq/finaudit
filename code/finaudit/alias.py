"""化名映射表：关联实体采用稳定化名，映射受控、单一来源。

约定：
- 本表**只存化名与占位编号**，不存任何真实主体名/证券代码/人员名；
- 真实主体名如需参与扫描，通过环境变量 `FINAUDIT_ALIAS_KEYWORDS` 当次传入，不落盘；
- 化名白名单由本表派生（`whitelist()`），扫描器不得另行硬编码。
"""

from __future__ import annotations

# 占位编号 → 化名集合（同一实体的不同表述指向同一编号）
ALIAS_GROUPS: dict[str, list[str]] = {
    "SUBJ-001": ["甲企业", "甲公司"],              # 核查主体（合并口径）
    "SUBJ-002": ["甲企业子公司A", "子公司A"],      # 关联主体（单体口径）
    "ORG-001": ["某研究机构"],                     # 研报编制机构
    "SEG-001": ["某新业务板块"],                   # 分部/板块
}

# 生效口径（与任务单 subject_alias 对齐）
SUBJECT_ALIAS = "甲企业"


def whitelist() -> tuple[str, ...]:
    """扫描器白名单（由本表派生，单一来源）。"""
    return tuple(sorted({n for names in ALIAS_GROUPS.values() for n in names}))


def as_map() -> dict:
    """导出用映射（登记进审计包，不含真实名）。"""
    return {
        "subject_alias": SUBJECT_ALIAS,
        "groups": {aid: list(names) for aid, names in ALIAS_GROUPS.items()},
        "note": "只含化名与占位编号；真实主体名不落盘",
    }
