"""生成扫描件 PDF 夹具（PIL 渲染 + reportlab 封装），用于验证 OCR 路径。

用法：python tools/md_to_scanned_pdf.py <src_dir> <dst_dir>
字体：默认 Windows 微软雅黑；可用环境变量 FINAUDIT_SCAN_FONT 指定。
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

FONT_PATH = os.environ.get("FINAUDIT_SCAN_FONT", r"C:\Windows\Fonts\msyh.ttc")
FONT_SIZE = 30
PAGE_W, PAGE_H = 1240, 1754  # A4 @150DPI
MARGIN_X, MARGIN_Y = 60, 60
LINE_H = 50


def _font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(FONT_PATH, FONT_SIZE)
    except OSError:
        print(f"[warn] 字体缺失：{FONT_PATH}，中文将无法渲染", file=sys.stderr)
        return ImageFont.load_default()


def render_pages(lines: list[str], font) -> list[Image.Image]:
    """渲染并按 A4 自动续页；超宽行按空格/单元格边界换行，绝不截断数字。"""
    pages: list[Image.Image] = []
    img = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    d = ImageDraw.Draw(img)
    y = MARGIN_Y
    max_width = PAGE_W - 2 * MARGIN_X

    def new_page() -> None:
        nonlocal img, d, y
        pages.append(img)
        img = Image.new("RGB", (PAGE_W, PAGE_H), "white")
        d = ImageDraw.Draw(img)
        y = MARGIN_Y

    def wrap(line: str) -> list[str]:
        if d.textlength(line, font=font) <= max_width:
            return [line]
        parts = line.split("    ")
        chunks, cur = [], ""
        for part in parts:
            candidate = f"{cur}    {part}" if cur else part
            if d.textlength(candidate, font=font) <= max_width:
                cur = candidate
                continue
            if cur:
                chunks.append(cur)
            if d.textlength(part, font=font) <= max_width:
                cur = part
            else:
                words, buf = part.split(" "), ""
                for w in words:
                    cand = f"{buf} {w}" if buf else w
                    if d.textlength(cand, font=font) <= max_width:
                        buf = cand
                    else:
                        if buf:
                            chunks.append(buf)
                        buf = w
                cur = buf
        if cur:
            chunks.append(cur)
        return chunks or [line]

    for line in lines:
        for chunk in wrap(line):
            if y > PAGE_H - MARGIN_Y:
                new_page()
            d.text((MARGIN_X, y), chunk, fill="black", font=font)
            y += LINE_H
    pages.append(img)
    return pages


def convert(md_path: Path, pdf_path: Path) -> None:
    font = _font()
    text = md_path.read_text(encoding="utf-8")
    pages: list[list[str]] = [[]]
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("<!-- page"):
            pages.append([])
            continue
        if not line:
            pages[-1].append("")
            continue
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        if line.startswith("|"):
            cells = [x.strip() for x in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells if c):
                continue
            line = "    ".join(cells)
        pages[-1].append(line)

    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    rendered = 0
    for lines in pages:
        body = [ln for ln in lines if ln.strip()]
        if not body or all("略页" in ln for ln in body):
            continue
        for img in render_pages(lines, font):
            buf = io.BytesIO()
            img.save(buf, format="PNG", dpi=(150, 150))
            buf.seek(0)
            c.drawImage(ImageReader(buf), 0, 0, width=A4[0], height=A4[1])
            c.showPage()
            rendered += 1
    if rendered == 0:
        c.drawString(72, A4[1] - 72, "(no content)")
        c.showPage()
    c.save()


def main() -> None:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.glob("*.md")):
        out = dst / (f.stem + ".pdf")
        convert(f, out)
        print(f"{f.name} -> {out.name}")


if __name__ == "__main__":
    main()
