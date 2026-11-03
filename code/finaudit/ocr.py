"""OCR 适配层（RapidOCR / PP-OCR，可选依赖）。

仅在材料为扫描件（无文本层）时加载；未安装 rapidocr_onnxruntime 时
`available()` 返回 False，扫描件按「解析失败/需人工复核」出口，不静默通过。

输出结构：按行合并的文本 + 置信度（行内各 token 的中位数），供解析层映射
OCR 分档（>=0.90 高 / >=0.75 中 / <0.75 低）。
"""

from __future__ import annotations

OCR_HIGH = 0.90
OCR_MID = 0.75
_LINE_Y_TOL = 16  # 像素：同一文本行的 y 容差（200 DPI 渲染下的经验值）


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def band_of(score: float) -> str:
    if score >= OCR_HIGH:
        return "高"
    if score >= OCR_MID:
        return "中"
    return "低"


class OCREngine:
    """RapidOCR 包装：单例加载模型（首次调用约 1-2 秒）。"""

    _engine = None

    def __init__(self) -> None:
        if not available():
            raise RuntimeError("rapidocr_onnxruntime 未安装")
        if OCREngine._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            OCREngine._engine = RapidOCR()

    def run(self, image) -> list[dict]:
        """返回 [{text, score, y, x, area}]；image 可为路径、PIL.Image 或 ndarray。"""
        if hasattr(image, "convert"):  # PIL.Image → ndarray（RapidOCR 不接受 PIL 对象）
            import numpy as np
            image = np.array(image.convert("RGB"))
        result, _elapse = OCREngine._engine(image)
        items: list[dict] = []
        for box, text, score in result or []:
            try:
                conf = float(score)
            except (TypeError, ValueError):
                conf = 0.0
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            # 识别区域面积（面积加权平均用）
            area = max(max(xs) - min(xs), 1.0) * max(max(ys) - min(ys), 1.0)
            items.append({"text": str(text), "score": conf,
                          "y": sum(ys) / len(ys), "x": min(xs), "area": area})
        return items


def group_lines(items: list[dict], y_tol: int = _LINE_Y_TOL) -> list[dict]:
    """把 OCR token 按 y 坐标聚成行，行内按 x 排序拼接。

    行级 confidence 按识别区域面积加权平均并保留 3 位小数（面积加权，第 1 条），
    与块级聚合方式一致，保证任务内口径唯一。
    """
    if not items:
        return []
    ordered = sorted(items, key=lambda it: it["y"])
    groups: list[list[dict]] = []
    for it in ordered:
        if groups and abs(it["y"] - groups[-1][0]["y"]) <= y_tol:
            groups[-1].append(it)
        else:
            groups.append([it])
    lines: list[dict] = []
    for g in groups:
        g_sorted = sorted(g, key=lambda it: it["x"])
        text = "    ".join(part["text"] for part in g_sorted if part["text"].strip())
        if not text.strip():
            continue
        weights = [float(part.get("area") or 1.0) for part in g_sorted]
        total = sum(weights) or 1.0
        conf = sum(float(part["score"]) * w for part, w in zip(g_sorted, weights)) / total
        lines.append({"text": text, "score": round(conf, 3)})
    return lines
