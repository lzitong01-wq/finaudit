"""夹具输入契约：`task_order.json` 与 `materials.json` 的字段校验。

这两份文件由人手写（或由界面生成），字段写错时的默认行为很糟：
- 缺必填字段 → `KeyError` 回溯，看不出该写什么；
- 多写/拼错字段 → **静默忽略**。`publishdate` 少一个下划线，披露日就不生效，
  前视偏差过滤跟着退化，而报告只会说「未登记披露日」——把用户自己的拼写错误
  说成"材料没提供"。

所以入口就把话说清楚：哪一份、哪一项、缺什么、多什么、是不是拼错了。
"""

from __future__ import annotations

import difflib

# 任务单：受理参数（I3）。缺前三项任务无法成立，缺后几项则退化运行。
TASK_REQUIRED: dict[str, str] = {
    "task_id": "任务编号，输出文件与事件流都以它为前缀",
    "subject_alias": "主体化名，报告与界面展示用；真实名不进本文件",
    "period_scope": "核查期间，形如 {kind, start, end, label}",
    "data_cutoff": "数据截止点（YYYY-MM-DD）：研报只能用这个日期之前可得的材料核验",
}
TASK_OPTIONAL: dict[str, str] = {
    "contract_version": "数据契约版本，留空按当前版本",
    "materials": "材料清单摘要（正式登记以 materials.json 为准）",
    "boundary_statement": "边界声明，原样写进 O1",
    "missing_items": "未取得项，写明后进报告边界说明",
}

# 材料登记项：materials.json 的 materials[] 元素。
MATERIAL_REQUIRED: dict[str, str] = {
    "material_id": "材料编号，形如 MAT-0001",
    "source_file_ref": "materials/ 下的文件名（不是路径）",
    "role": "角色；含「待核验」的那份是研报，且只能有一份",
}
MATERIAL_OPTIONAL: dict[str, str] = {
    "format": "md / pdf / scanned_pdf，缺省按 md",
    "page_count": "页数，进审计包，可空",
    "publish_date": "披露日（YYYY-MM-DD）：晚于数据截止点的材料整份不作证据",
    "ocr_blocks": "扫描件的 OCR 置信度分档声明，仅 scanned_pdf 用",
}

MATERIAL_FORMATS = ("md", "pdf", "scanned_pdf")


def _typo_hint(key: str, known: dict[str, str]) -> str:
    near = difflib.get_close_matches(key, list(known), n=1, cutoff=0.6)
    if not near:
        return ""
    return f"（是不是想写 `{near[0]}`？{known[near[0]]}）"


def _check_keys(obj: dict, required: dict[str, str], optional: dict[str, str],
                where: str) -> list[str]:
    errs: list[str] = []
    known = {**required, **optional}
    for key, why in required.items():
        if key not in obj or obj[key] in (None, ""):
            errs.append(f"{where}: 缺必填字段 `{key}`（{why}）")
    for key in obj:
        if key not in known:
            errs.append(f"{where}: 未声明字段 `{key}`{_typo_hint(key, known)}"
                        f"（可写字段：{', '.join(known)}）")
    return errs


def check_task(task: dict) -> list[str]:
    """任务单字段校验；空列表 = 通过。"""
    if not isinstance(task, dict):
        return [f"task_order.json 顶层应为对象，实际是 {type(task).__name__}"]
    return _check_keys(task, TASK_REQUIRED, TASK_OPTIONAL, "task_order.json")


def check_materials(doc) -> list[str]:
    """材料登记校验：顶层结构 + 每项字段 + 格式取值 + 编号唯一。"""
    if isinstance(doc, list):
        items = doc
    elif isinstance(doc, dict):
        if "materials" not in doc:
            return [f"materials.json 顶层缺 `materials` 键"
                    f"（实际键：{', '.join(doc) or '空'}；应为 {{\"materials\": [...]}}）"]
        items = doc["materials"]
    else:
        return [f"materials.json 顶层应为对象或数组，实际是 {type(doc).__name__}"]

    if not isinstance(items, list):
        return ["materials.json 的 `materials` 应为数组"]
    if not items:
        return ["materials.json 的 `materials` 为空：至少要有 1 份研报"]

    errs: list[str] = []
    seen_ids: dict[str, int] = {}
    for idx, m in enumerate(items, start=1):
        where = f"materials.json 第 {idx} 项"
        if not isinstance(m, dict):
            errs.append(f"{where}: 应为对象，实际是 {type(m).__name__}")
            continue
        errs.extend(_check_keys(m, MATERIAL_REQUIRED, MATERIAL_OPTIONAL, where))
        fmt = m.get("format")
        if fmt is not None and fmt not in MATERIAL_FORMATS:
            errs.append(f"{where}: `format` 取值为 `{fmt}`，只支持 {' / '.join(MATERIAL_FORMATS)}")
        mid = m.get("material_id")
        if isinstance(mid, str):
            if mid in seen_ids:
                errs.append(f"{where}: 编号 `{mid}` 与第 {seen_ids[mid]} 项重复")
            else:
                seen_ids[mid] = idx
    return errs


def check_all(task: dict, materials_doc) -> list[str]:
    """一次性收集两份输入的错误，避免改一处跑一次。"""
    return check_task(task) + check_materials(materials_doc)
