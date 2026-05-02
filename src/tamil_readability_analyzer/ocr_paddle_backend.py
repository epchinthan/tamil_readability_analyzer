"""Optional PaddleOCR Tamil backend.

PaddleOCR is heavier than Tesseract and may need separate installation, so this
module is imported only when the user selects the Paddle backend. It keeps the
main app usable even when PaddleOCR is not installed.
"""

from __future__ import annotations

import gc
import os
import re
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

TAMIL_RE = re.compile(r"[\u0B80-\u0BFF]+")

_OCR = None


def _collect_text(value, out: List[str]) -> None:
    """Collect recognized text from both old and new PaddleOCR result shapes."""
    if value is None:
        return
    if isinstance(value, str):
        if TAMIL_RE.search(value):
            out.append(value)
        return
    if isinstance(value, dict):
        for key in ("rec_texts", "texts", "text", "label", "transcription"):
            if key in value:
                _collect_text(value[key], out)
        return
    if hasattr(value, "json"):
        try:
            _collect_text(value.json, out)
            return
        except Exception:
            pass
    if isinstance(value, (list, tuple)):
        # Old PaddleOCR shape: [box, (text, confidence)].
        if len(value) == 2 and isinstance(value[1], (list, tuple)) and value[1] and isinstance(value[1][0], str):
            _collect_text(value[1][0], out)
            return
        for item in value:
            _collect_text(item, out)


def _get_ocr():
    global _OCR
    if _OCR is not None:
        return _OCR

    from paddleocr import PaddleOCR

    init_attempts = [
        {"lang": "ta", "use_textline_orientation": True},
        {"lang": "ta", "use_angle_cls": True, "show_log": False},
        {"lang": "ta"},
    ]
    last_error = None
    for kwargs in init_attempts:
        try:
            _OCR = PaddleOCR(**kwargs)
            return _OCR
        except TypeError as exc:
            last_error = exc
            continue
    raise last_error or RuntimeError("Could not initialize PaddleOCR Tamil backend")


def is_available() -> Tuple[bool, str]:
    try:
        import paddleocr  # noqa: F401
        import paddle  # noqa: F401
        import pdf2image  # noqa: F401
    except Exception as exc:
        return False, f"PaddleOCR backend not installed: {exc}"
    return True, "PaddleOCR Tamil backend ready"


def _ocr_image(path: str) -> str:
    ocr = _get_ocr()
    if hasattr(ocr, "predict"):
        result = ocr.predict(path)
    else:
        result = ocr.ocr(path, cls=True)
    parts: List[str] = []
    _collect_text(result, parts)
    return "\n".join(parts)


def ocr_pdf_tamil(
    pdf_path: str | os.PathLike,
    *,
    dpi: Optional[int] = None,
    max_pages: Optional[int] = None,
    progress: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, object]:
    from pdf2image import convert_from_path, pdfinfo_from_path

    ok, message = is_available()
    if not ok:
        return {"ok": False, "text": "", "words": [], "pages": 0, "error": message, "backend": "paddleocr_tamil"}

    pdf_path = str(pdf_path)
    dpi = dpi or int(os.environ.get("TAMIL_ANALYZER_OCR_DPI", "300"))
    env_max = int(os.environ.get("TAMIL_ANALYZER_OCR_MAX_PAGES", "0"))
    max_pages = max_pages if max_pages is not None else env_max

    info = pdfinfo_from_path(pdf_path)
    total_pages = int(info.get("Pages", 0) or 0)
    if max_pages and max_pages > 0:
        total_pages = min(total_pages, max_pages)

    if progress:
        progress("ocr", f"PaddleOCR Tamil starting - {total_pages} page(s)")

    parts: List[str] = []
    page_word_counts: List[int] = []
    with tempfile.TemporaryDirectory(prefix="paddle_tamil_ocr_") as tmpdir:
        for page_no in range(1, total_pages + 1):
            if progress:
                progress("ocr", f"PaddleOCR Tamil page {page_no} of {total_pages}")
            try:
                paths = convert_from_path(
                    pdf_path,
                    dpi=dpi,
                    first_page=page_no,
                    last_page=page_no,
                    fmt="png",
                    thread_count=1,
                    output_folder=tmpdir,
                    paths_only=True,
                )
                if not paths:
                    page_word_counts.append(0)
                    continue
                text = _ocr_image(paths[0])
                parts.append(text)
                page_word_counts.append(len(TAMIL_RE.findall(text)))
                try:
                    Path(paths[0]).unlink(missing_ok=True)
                except Exception:
                    pass
            except Exception as exc:
                parts.append("")
                page_word_counts.append(0)
                if progress:
                    progress("ocr", f"PaddleOCR Tamil page {page_no} failed: {exc}")
            finally:
                gc.collect()

    full_text = "\n".join(parts).strip()
    words = TAMIL_RE.findall(full_text)
    return {
        "ok": bool(words),
        "text": full_text,
        "words": words,
        "pages": total_pages,
        "page_word_counts": page_word_counts,
        "backend": "paddleocr_tamil",
        "error": "" if words else "PaddleOCR completed but no Tamil words were detected",
    }
