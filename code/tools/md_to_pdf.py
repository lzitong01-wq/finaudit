"""把 Markdown 材料渲染成 PDF（reportlab），用于生成 PDF 端到端测试夹具。

仅用于测试夹具生成，不参与运行时。用法：
    python tools/md_to_pdf.py <src_dir> <dst_dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

FONT = "STSong-Light"
PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
LINE_H = 12
FONT_SIZE = 9


def _register() -> None:
    global FONT
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    except Exception:  # noqa: BLE001 - 无中文字体时退回内置
        FONT = "Helvetica"


def render(md_path: Path, pdf_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    y = PAGE_H - MARGIN
    c.setFont(FONT, FONT_SIZE)

    def new_page() -> None:
        nonlocal y
        c.showPage()
        c.setFont(FONT, FONT_SIZE)
        y = PAGE_H - MARGIN

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("<!-- page"):
            new_page()
            continue
        if not line:
            y -= LINE_H * 0.5
            continue
        if line.startswith("### "):
            c.setFont(FONT, FONT_SIZE + 2)
            line = line[4:]
        elif line.startswith("## "):
            c.setFont(FONT, FONT_SIZE + 3)
            line = line[3:]
        elif line.startswith("# "):
            c.setFont(FONT, FONT_SIZE + 4)
            line = line[2:]
        if line.startswith("|"):
            # 表格行：单元格以 4 空格连接，去掉分隔行
            cells = [x.strip() for x in line.strip("|").split("|")]
            if all(set(cell) <= set("-: ") for cell in cells if cell):
                continue
            line = "    ".join(cells)
        # 简化换行：按可用宽度截断
        max_chars = int((PAGE_W - 2 * MARGIN) / (FONT_SIZE * 0.55))
        while line:
            chunk, line = line[:max_chars], line[max_chars:]
            if y < MARGIN:
                new_page()
            c.drawString(MARGIN, y, chunk)
            y -= LINE_H
        c.setFont(FONT, FONT_SIZE)
    c.save()


def main() -> None:
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    _register()
    for f in sorted(src.glob("*.md")):
        out = dst / (f.stem + ".pdf")
        render(f, out)
        print(f"{f.name} -> {out.name}")


if __name__ == "__main__":
    main()
