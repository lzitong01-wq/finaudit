"""输出目录完整性核对：按 manifest 与 F-outputs 里记录的哈希复算，判断交付包是否被改动。

只读：不修改任何文件、不重跑核验。用于复核者独立确认交付包与运行当时一致。

用法：python -m finaudit.cli verify --out ../out/demo01
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# F-outputs 键 → 实际文件（相对输出目录）
_F_OUTPUT_FILES = {
    "o1": Path("o1") / "O1-核查报告.md",
    "o2": Path("o2") / "O2-核查明细.json",
    "csv": Path("o2") / "O2-核查明细.csv",
}

# 合法例外：界面人工复核留痕跨运行累积，不属于任何一次运行的产物
_ACCUMULATED = {"review-log.jsonl"}
_PACKAGE_DIRS = ("o1", "o2", "o3")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_output(out: Path) -> dict:
    """返回 {ok, checks:[{file, expect, actual, status}], missing, note}。

    status 取值：一致 / 不一致 / 缺失。
    """

    out = Path(out)
    o3 = out / "o3"
    checks: list[dict] = []
    missing: list[str] = []

    manifest_path = o3 / "manifest.json"
    manifest = None
    if not manifest_path.exists():
        missing.append("o3/manifest.json")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"ok": False, "checks": [], "missing": missing,
                    "note": "清单文件已损坏，无法核对"}

    if isinstance(manifest, dict):
        for name, expect in (manifest.get("files") or {}).items():
            path = o3 / name
            if not path.exists():
                checks.append({"file": f"o3/{name}", "expect": expect,
                               "actual": "", "status": "缺失"})
                continue
            actual = _sha256(path)
            checks.append({"file": f"o3/{name}", "expect": expect, "actual": actual,
                           "status": "一致" if actual == expect else "不一致"})

    f_path = o3 / "F-outputs.json"
    f_outputs = None
    if not f_path.exists():
        missing.append("o3/F-outputs.json")
    else:
        try:
            f_outputs = json.loads(f_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            f_outputs = None
            checks.append({"file": "o3/F-outputs.json", "expect": "", "actual": "",
                           "status": "不一致"})

    if isinstance(f_outputs, dict):
        for key, rel in _F_OUTPUT_FILES.items():
            expect = f_outputs.get(key)
            if not expect:
                continue
            path = out / rel
            if not path.exists():
                checks.append({"file": str(rel).replace("\\", "/"), "expect": expect,
                               "actual": "", "status": "缺失"})
                continue
            actual = _sha256(path)
            checks.append({"file": str(rel).replace("\\", "/"), "expect": expect,
                           "actual": actual,
                           "status": "一致" if actual == expect else "不一致"})

    bad = [c for c in checks if c["status"] != "一致"]
    # 包里多出来的文件：清单没登记、又不是累积型留痕，说明混进了上一次运行的产物
    registered = set()
    if isinstance(manifest, dict):
        registered = {f"o3/{name}" for name in (manifest.get("files") or {})}
    registered |= {str(rel).replace("\\", "/") for rel in _F_OUTPUT_FILES.values()}
    registered |= {f"o3/{name}" for name in _ACCUMULATED}
    # 清单不登记自身：自引用哈希不成立
    registered.add("o3/manifest.json")
    unexpected: list[str] = []
    for sub in _PACKAGE_DIRS:
        d = out / sub
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            rel = str(p.relative_to(out)).replace("\\", "/")
            if rel not in registered:
                unexpected.append(rel)

    note = ""
    if manifest is None and f_outputs is None:
        note = "缺少可核对的清单文件：该目录不是完整输出目录"
    elif not checks:
        note = "清单里没有可核对的哈希"
    elif bad:
        note = f"{len(bad)} 项与记录不一致：{', '.join(c['file'] for c in bad)}"
    elif unexpected:
        note = (f"{len(checks)} 项一致，但包内有 {len(unexpected)} 个文件未登记在清单里："
                f"{', '.join(unexpected[:5])}（不是本次运行的产物）")
    else:
        note = f"{len(checks)} 项全部一致"
    return {"ok": not bad and not unexpected and bool(checks) and manifest is not None,
            "checks": checks, "missing": missing, "unexpected": unexpected,
            "note": note}
