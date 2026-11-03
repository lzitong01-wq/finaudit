"""导出前两项扫描（真实执行，非硬编码）：密钥泄漏 / 化名合规。

- 密钥扫描（kl_scan_result）：扫输出物是否含密钥特征（sk-、Bearer、api_key= 等）。
- 化名扫描（alias_check）：扫输出物是否含非白名单的主体名模式；可选外部关键词
  （关键词不落盘，仅当次调用传入）。

命中即非 0；导出事件如实登记命中数，不静默置零。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

# 密钥特征（避免误伤正常文本：要求足够的熵/长度）
KEY_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{16,}"),
    re.compile(r"\bms-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"\b(api[_-]?key|apikey)\s*[=:]\s*[\"']?[A-Za-z0-9._\-]{16,}", re.I),
    re.compile(r"\b(password|passwd|secret)\s*[=:]\s*[\"']?[A-Za-z0-9._\-]{8,}", re.I),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

# 化名白名单：由 alias.py 单一来源派生
from .alias import whitelist as _whitelist  # noqa: E402

ALIAS_WHITELIST = _whitelist()

# 合法主体后缀（先剥离再判虚词，避免"有限公司"的"有"被误当虚词）
ENTITY_SUFFIXES = ("有限责任公司", "有限公司", "股份", "集团", "控股", "科技",
                   "Inc.", "Ltd.", "Corp.")

# 虚词字符："不得以集团"词干含虚词即属行文，而非主体名
STOP_FIRST_CHARS = set("仅有得该充并及或为是的了和与对从在把被给让使由向")

# 脱敏自述上下文：命中附近含"脱敏"时不计命中（夹具自述）
DESENSITIZE_HINT = "脱敏"

# 疑似真实主体名模式：中文/英文主体 + 公司类后缀，且不在白名单内
ENTITY_PATTERN = re.compile(
    r"[一-龥A-Za-z0-9]{2,12}(?:股份|集团|控股|科技|有限公司|有限责任公司|"
    r"有限公司|Inc\.|Ltd\.|Corp\.)")

# 参与扫描的文本产物后缀
TEXT_SUFFIXES = {".md", ".json", ".csv", ".txt"}


def iter_text_files(out_dir: Path) -> Iterator[tuple[Path, str]]:
    """遍历输出目录下的文本产物，逐个产出 (路径, 文本)。

    读失败的文件跳过但不中断整轮扫描；两个扫描函数共用此遍历，避免重复实现。
    """
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            yield path, path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue


def _scan_text(text: str, patterns: list[re.Pattern]) -> list[str]:
    hits: list[str] = []
    for pat in patterns:
        found = pat.findall(text)
        for f in found:
            item = f if isinstance(f, str) else "".join(f)
            hits.append(item[:40])
    return hits


def scan_keys(out_dir: Path) -> tuple[int, list[str]]:
    """扫描输出目录下所有文本产物的密钥特征。"""
    hits: list[str] = []
    for path, text in iter_text_files(out_dir):
        for h in _scan_text(text, KEY_PATTERNS):
            hits.append(f"{path.name}:{h}")
    return len(hits), hits


def scan_alias(out_dir: Path, extra_keywords: list[str] | None = None) -> tuple[int, list[str]]:
    """扫描非白名单主体名；extra_keywords 为外部持有的真实主体名（不落盘）。"""
    hits: list[str] = []
    for path, text in iter_text_files(out_dir):
        for kw in extra_keywords or []:
            if kw and kw in text:
                hits.append(f"{path.name}:{kw}")
        for name in ENTITY_PATTERN.findall(text):
            if name in ALIAS_WHITELIST or any(w in name for w in ALIAS_WHITELIST):
                continue
            # 先剥离合法后缀，再对词干判虚词："乙样本控股有限公司"保留，
            # "不得以集团"词干为"不得以"→ 属行文，不计命中
            stem = name
            for suffix in ENTITY_SUFFIXES:
                if name.endswith(suffix) and len(name) > len(suffix):
                    stem = name[: -len(suffix)]
                    break
            if stem and any(ch in stem for ch in STOP_FIRST_CHARS):
                continue
            pos = text.find(name)
            context = text[max(0, pos - 40):pos]
            if DESENSITIZE_HINT in context:
                continue  # 夹具/自述中的脱敏说明
            hits.append(f"{path.name}:{name}")
    return len(hits), hits


def run_all(out_dir: Path, alias_keywords: list[str] | None = None) -> dict:
    """执行两项扫描，返回计数与命中摘要（供导出事件与 O1 S9 使用）。"""
    kl, kl_hits = scan_keys(out_dir)
    al, al_hits = scan_alias(out_dir, alias_keywords)
    return {
        "kl_scan_result": kl,
        "alias_check": al,
        "hits": {"keys": kl_hits[:5], "alias": al_hits[:5]},
    }
