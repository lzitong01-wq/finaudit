"""PDF 材料接入（pypdf 文本层）。仅在遇到 .pdf 材料时导入，核心流程不依赖第三方库。

解析策略（MVP：文本型 PDF，扫描件走 OCR 分档后按同样结构录入）：
1. 逐页抽取文本（pypdf.PdfReader.extract_text）；
2. 页内按行解析：`指标标签 + 数字[ + 上期数字]` 的行视为表格数据行；
3. 单位取页内"单位：元/亿元/..."；期间由受理任务期间推导，未知期间保持未知；
4. 输出与 Markdown 适配器同构的 (blocks, rows)，供 S2 之后的检索/计算复用。

局限（如实标注，不冒充通用 PDF 解析）：
- 仅支持文本型 PDF；扫描件需 OCR 前置（当前不内置）；
- 表格依赖"一行一条记录"的版式；跨行/合并单元格表格会解析失败，输出解析失败块。
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from .ids import IdAllocator
from .ocr import band_of
from .periods import period_for_metric, period_key, previous_year_period, prior_year_end
from .pipeline import _footnote_mark

UNIT_RE = re.compile(r"单位[：:]\s*(元|亿元|万元|千元|百万元|十亿元)")
NUM_RE = re.compile(r"-?[\d,]+(?:\.\d+)?")
POINT_METRICS = {"资产合计", "负债合计", "所有者权益合计", "存货"}
LABELS = {
    "营业收入": "营业收入", "营业成本": "营业成本", "净利润": "净利润",
    "资产合计": "资产合计", "负债合计": "负债合计",
    "所有者权益合计": "所有者权益合计", "存货": "存货", "研发投入": "研发投入",
}


class PDFEngineMissing(RuntimeError):
    """PDF 文本层引擎（pypdf）不可用：显式出口，不静默降级为"解析成功"。"""


def extract_pages(path: Path, use_ocr: bool = False) -> list[tuple[int, str, list[dict]]]:
    """逐页取文本。无文本层且 use_ocr=True 时走 OCR，返回 (页码, 文本, 行级OCR结果)。

    损坏文件（无法构造 PdfReader）返回空列表，由调用方记解析失败；
    引擎未安装抛 PDFEngineMissing，由调用方记工具失败（不冒充"文件损坏"）。
    """
    try:
        from pypdf import PdfReader  # 延迟导入：仅有 PDF 材料时需要
    except ImportError as exc:  # pragma: no cover - 依赖缺失分支
        raise PDFEngineMissing(
            "PDF 文本层需要 pypdf（pip install pypdf）；未安装时不解析 PDF 材料") from exc

    try:
        reader = PdfReader(str(path))
    except Exception:  # noqa: BLE001 - 损坏 PDF（PdfReadError/PdfStreamError/其他）
        # 失败原因由调用方写进审计事件（工具失败）；pypdf 自身可能往 stderr
        # 打一行「EOF marker not found」，属定位用的原始线索，不拦截。
        return []
    pages: list[tuple[int, str, list[dict]]] = []
    ocr_engine = None
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001
            text = ""
        lines: list[dict] = []
        if use_ocr and len(text) < 20:
            from .ocr import OCREngine, group_lines
            if ocr_engine is None:
                ocr_engine = OCREngine()
            try:
                images = list(page.images)
            except Exception:  # noqa: BLE001
                images = []
            if images:
                img = images[0].image
                # 空白页跳过 OCR：原图统计暗像素（文本页通常数万个暗像素）
                try:
                    import numpy as np
                    arr = np.asarray(img.convert("L"))
                    if int((arr < 160).sum()) < 500:
                        pages.append((i, "", []))
                        continue
                except Exception:  # noqa: BLE001
                    pass
                # 小图 2 倍上采样：提升短行/小字号的中文识别置信度
                scale = 2 if img.width < 1600 else 1
                if scale > 1:
                    from PIL import Image as _Image
                    img = img.resize((img.width * scale, img.height * scale),
                                     _Image.LANCZOS)
                items = ocr_engine.run(img)
                lines = group_lines(items, y_tol=16 * scale)
                text = "\n".join(ln["text"] for ln in lines)
        pages.append((i, text, lines))
    return pages


def _period_of(metric: str, period_scope: dict | None, index: int = 0) -> tuple[str | None, dict | None]:
    """PDF 当前列/比较列按任务期间映射；不以默认年份填空。"""
    scope = period_scope
    if index:
        scope = (prior_year_end(scope) if metric in POINT_METRICS
                 else previous_year_period(scope))
    period = period_for_metric(scope, metric, POINT_METRICS)
    return period_key(period), period


def _band_for(ocr_blocks: list[dict], text: str) -> str:
    for b in ocr_blocks or []:
        token = b.get("match")
        if token and token in text:
            return b.get("band", "低")
    return "不适用"


def parse_pdf_material(material_id: str, path: Path, alloc: IdAllocator,
                       ocr_blocks: list[dict], use_ocr: bool = False,
                       period_scope: dict | None = None) -> tuple[list[dict], list[dict]]:
    blocks: list[dict] = []
    rows: list[dict] = []
    pages = extract_pages(path, use_ocr=use_ocr)
    if not pages:
        # 解析失败（损坏/无文本层且 OCR 不可用）：显式记解析失败块，不静默
        blocks.append({
            "block_id": alloc.next("block"), "material_id": material_id,
            "block_type": "段落", "page": None,
            "content_text": f"（{path.name} 解析失败：文件损坏或无法读取文本层）",
            "parse_status": "解析失败",
            "locator": {"type": "页码", "page": None, "table_ref": None,
                        "text_range": None, "value": "解析失败"},
        })
        return blocks, rows

    for page_no, text, ocr_lines in pages:
        if not text.strip():
            continue
        um = UNIT_RE.search(text)
        unit_code = um.group(1) if um else "元"
        ocr_band_by_text = {ln["text"]: band_of(ln["score"]) for ln in ocr_lines} if ocr_lines else {}

        block = {
            "block_id": alloc.next("block"), "material_id": material_id,
            "block_type": "段落", "page": page_no, "content_text": text[:2000],
            "parse_status": "已解析",
            "locator": {"type": "页码", "page": page_no, "table_ref": None,
                        "text_range": None, "value": f"P{page_no}"},
        }
        blocks.append(block)

        # 脚注块：页内命中引导标记的行单独成块，保留定位并参与检索
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            mark = _footnote_mark(line)
            if mark:
                blocks.append({
                    "block_id": alloc.next("block"), "material_id": material_id,
                    "block_type": "脚注", "page": page_no, "content_text": line[:800],
                    "parse_status": "已解析",
                    "locator": {"type": "页码", "page": page_no, "table_ref": None,
                                "text_range": None, "value": f"P{page_no}/{mark}"},
                })

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # 存货附表：行标签为"合计"，以页内"存货"上下文判定指标
            matched_label, metric = None, None
            for label, m in LABELS.items():
                if line.startswith(label):
                    matched_label, metric = label, m
                    break
            if metric is None and line.startswith("合计") and "存货" in text:
                matched_label, metric = "合计", "存货"
            if metric is None:
                continue
            rest = line[len(matched_label):]
            numbers = NUM_RE.findall(rest)
            if not numbers:
                continue
            # 元/亿元 混排时，行内单位优先（如"45.6 亿元"）
            line_unit = unit_code
            for u in ("亿元", "万元", "千元", "元"):
                if u in rest:
                    line_unit = u
                    break
            band = _band_for(ocr_blocks, line)
            if band == "不适用" and line in ocr_band_by_text:
                band = ocr_band_by_text[line]
            # 两列（本期/上期）→ 两条行；"（略）"列自动缺席
            for idx, number in enumerate(numbers[:2]):
                pk, period = _period_of(metric, period_scope, idx)
                rows.append({
                    "metric": metric, "metric_label": matched_label,
                    "period_key": pk, "period": period,
                    "value": Decimal(number.replace(",", "")),
                    "value_text": f"{number}{line_unit}",
                    "unit_code": line_unit, "material_id": material_id,
                    "page": page_no, "block_id": block["block_id"],
                    "table_ref": f"P{page_no}-行",
                    "quote": line[:200], "ocr_band": band,
                })
    return blocks, rows
