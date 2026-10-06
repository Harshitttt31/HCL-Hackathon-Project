"""OCR for scanned pages and image uploads (Tesseract via pytesseract).

OCR failure never crashes ingestion: the caller gets an OCRUnavailable error it can turn into a warning,
and the document is reported as partially parsed instead of silently empty.
"""
from __future__ import annotations

import io
import shutil
from dataclasses import dataclass
from typing import Optional

from app.core.config import get_settings
from app.core.errors import OCRUnavailable
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class OCRLine:
    text: str
    height: float
    top: float
    confidence: float  # 0..1


@dataclass
class OCRPage:
    lines: list[OCRLine]
    mean_confidence: float


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_image_bytes(png_or_jpeg: bytes) -> OCRPage:
    settings = get_settings()
    if not settings.ocr_enabled:
        raise OCRUnavailable("OCR is disabled (OCR_ENABLED=false)")
    if not tesseract_available():
        raise OCRUnavailable("the tesseract binary is not installed")
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise OCRUnavailable(f"OCR python packages are missing: {exc}") from exc
    try:
        image = Image.open(io.BytesIO(png_or_jpeg)).convert("L")
        data = pytesseract.image_to_data(image, lang=settings.ocr_language, output_type=pytesseract.Output.DICT, config="--psm 6")
    except Exception as exc:
        raise OCRUnavailable(f"OCR failed: {exc}") from exc

    grouped: dict[tuple[int, int, int], list[int]] = {}
    for i, word in enumerate(data["text"]):
        if not word or not word.strip():
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0:
            continue
        grouped.setdefault((data["block_num"][i], data["par_num"][i], data["line_num"][i]), []).append(i)
    lines: list[OCRLine] = []
    for _, idxs in sorted(grouped.items(), key=lambda kv: min(data["top"][i] for i in kv[1])):
        words = [data["text"][i] for i in idxs]
        confs = [float(data["conf"][i]) for i in idxs]
        lines.append(OCRLine(
            text=" ".join(words), height=max(float(data["height"][i]) for i in idxs),
            top=min(float(data["top"][i]) for i in idxs), confidence=sum(confs) / len(confs) / 100.0))
    mean = sum(l.confidence for l in lines) / len(lines) if lines else 0.0
    return OCRPage(lines=lines, mean_confidence=mean)
