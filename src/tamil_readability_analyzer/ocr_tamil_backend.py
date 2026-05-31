"""Tamil OCR backend inspired by khaleeljageer/OCR-Tamil.

The OCR-Tamil repository is an Android/Kotlin app built around Tesseract 4
for Tamil OCR. This project is a Python/Flask web app, so the Android code
cannot be imported directly. This adapter implements the same practical OCR
idea on the server side:

    PDF page -> image -> Tesseract 4 Tamil model (tam.traineddata) -> Tamil text

Repository reference: https://github.com/khaleeljageer/OCR-Tamil
License note: OCR-Tamil is GPL-3.0. This adapter does not copy its Kotlin code;
it uses the same open Tesseract approach via pytesseract/pdf2image.
"""

from __future__ import annotations

import gc
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

TAMIL_RE = re.compile(r"[\u0B80-\u0BFF]+")
TAMIL_WORD_RE = re.compile(r"[\u0B80-\u0BFF][\u0B80-\u0BFF\u0BCD\u0BBE-\u0BCC\u0BD7]*")

DEFAULT_CONFIGS = [
    "--oem 3 --psm 3 -c preserve_interword_spaces=1",
    "--oem 3 --psm 6 -c preserve_interword_spaces=1",
    "--oem 3 --psm 4 -c preserve_interword_spaces=1",
]

_NOISY_FRAGMENT_RE = re.compile(
    r"(?:டட|ணண|ம்மம்|றுறுற|ஆஆ|ஊஊ|[0-9௦-௯]|[A-Za-z])"
)
_PURE_CONSONANT_FRAGMENT_RE = re.compile(r"^[க-ஹ]்?$")


def _ocr_quality_score(text: str) -> int:
    """Prefer OCR output with real Tamil words and fewer scan fragments."""
    words = extract_tamil_words(text)
    if not words:
        return -10_000
    tamil_chars = sum(len(TAMIL_RE.findall(w)[0]) if TAMIL_RE.search(w) else 0 for w in words)
    short_fragments = sum(
        1
        for w in words
        if len(w) <= 2 or _PURE_CONSONANT_FRAGMENT_RE.fullmatch(w) or _NOISY_FRAGMENT_RE.search(w)
    )
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    line_bonus = sum(1 for line in lines if len(extract_tamil_words(line)) >= 2)
    return tamil_chars + (len(words) * 2) + (line_bonus * 4) - (short_fragments * 8)


def has_tamil(text: str) -> bool:
    return bool(text and TAMIL_RE.search(text))


def extract_tamil_words(text: str) -> List[str]:
    """Extract Tamil-looking words only; useful after OCR to avoid English noise."""
    if not text:
        return []
    words = [w.strip(".,;:!?()[]{}'\"“”‘’") for w in TAMIL_WORD_RE.findall(text)]
    return [w for w in words if len(w) > 0]


def available_languages() -> List[str]:
    try:
        import pytesseract
        return sorted(pytesseract.get_languages(config=""))
    except Exception:
        return []


def is_available() -> Tuple[bool, str]:
    if not shutil.which("tesseract"):
        return False, "Tesseract executable not found"
    try:
        import pytesseract  # noqa: F401
        import pdf2image  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception as exc:
        return False, f"Python OCR libraries missing: {exc}"
    langs = available_languages()
    if "tam" not in langs:
        return False, "Tamil language data not found. Install tesseract-ocr-tam or tam.traineddata"
    return True, "Tamil Tesseract OCR ready"


def preprocess_image(img):
    """Conservative preprocessing for Tamil glyphs; avoids destroying fine strokes."""
    try:
        from PIL import ImageOps
        gray = ImageOps.grayscale(img)
        w, h = gray.size
        if max(w, h) < 1500:
            gray = gray.resize((w * 2, h * 2))
        gray = ImageOps.autocontrast(gray, cutoff=1)
        # Mild threshold: Tamil has fine curls/marks; aggressive thresholds hurt OCR.
        return gray.point(lambda px: 255 if px > 128 else 0)
    except Exception:
        return img


def _ocr_image_with_fallbacks(img, timeout: int, min_tamil_words: int = 2) -> str:
    import pytesseract

    best_text = ""
    best_score = -1
    clean = preprocess_image(img)
    for cfg in DEFAULT_CONFIGS:
        try:
            text = pytesseract.image_to_string(clean, lang="tam", config=cfg, timeout=timeout) or ""
        except RuntimeError:
            continue
        except Exception:
            continue
        word_count = len(extract_tamil_words(text))
        score = _ocr_quality_score(text)
        if score > best_score:
            best_text, best_score = text, score
        if word_count < min_tamil_words:
            continue
    return best_text


def ocr_pdf_tamil(
    pdf_path: str | os.PathLike,
    *,
    dpi: Optional[int] = None,
    max_pages: Optional[int] = None,
    timeout: Optional[int] = None,
    progress: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, object]:
    """Run Tamil-only OCR on a PDF and return text + Tamil word list.

    This is meant for scanned PDFs and non-Unicode Tamil-font PDFs.
    """
    from pdf2image import convert_from_path, pdfinfo_from_path
    from PIL import Image

    pdf_path = str(pdf_path)
    # These TN textbook PDFs are print-layout scans. At 300 DPI Tesseract often
    # reads trim marks and printer metadata as Tamil fragments; 150 DPI keeps
    # Grade 1 textbook text readable while reducing that noise. Users can still
    # override this for low-resolution scans.
    dpi = dpi or int(os.environ.get("TAMIL_ANALYZER_OCR_DPI", "150"))
    timeout = timeout or int(os.environ.get("TAMIL_ANALYZER_OCR_TIMEOUT", "90"))
    env_max = int(os.environ.get("TAMIL_ANALYZER_OCR_MAX_PAGES", "0"))
    max_pages = max_pages if max_pages is not None else env_max

    ok, message = is_available()
    if not ok:
        return {"ok": False, "text": "", "words": [], "error": message, "pages": 0}

    info = pdfinfo_from_path(pdf_path)
    total_pages = int(info.get("Pages", 0) or 0)
    if max_pages and max_pages > 0:
        total_pages = min(total_pages, max_pages)

    if progress:
        progress("ocr", f"OCR-Tamil backend starting — {total_pages} page(s)")

    parts: List[str] = []
    page_word_counts: List[int] = []

    with tempfile.TemporaryDirectory(prefix="ocr_tamil_backend_") as tmpdir:
        for page_no in range(1, total_pages + 1):
            if progress:
                progress("ocr", f"OCR-Tamil backend page {page_no} of {total_pages}")
            try:
                paths = convert_from_path(
                    pdf_path,
                    dpi=dpi,
                    first_page=page_no,
                    last_page=page_no,
                    fmt="png",
                    grayscale=True,
                    thread_count=1,
                    output_folder=tmpdir,
                    paths_only=True,
                )
                if not paths:
                    page_word_counts.append(0)
                    continue
                img_path = paths[0]
                with Image.open(img_path) as img:
                    text = _ocr_image_with_fallbacks(img, timeout=timeout)
                parts.append(text)
                page_word_counts.append(len(extract_tamil_words(text)))
                try:
                    Path(img_path).unlink(missing_ok=True)
                except Exception:
                    pass
            except Exception as exc:
                parts.append("")
                page_word_counts.append(0)
                if progress:
                    progress("ocr", f"OCR-Tamil backend page {page_no} failed: {exc}")
            finally:
                gc.collect()

    full_text = "\n".join(parts).strip()
    words = extract_tamil_words(full_text)
    return {
        "ok": bool(words),
        "text": full_text,
        "words": words,
        "pages": total_pages,
        "page_word_counts": page_word_counts,
        "backend": "ocr_tamil_tesseract4_python",
        "error": "" if words else "OCR completed but no Tamil words were detected",
    }
