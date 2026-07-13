"""OCR fallback for scanned PDFs.

If ``pypdf`` extraction on a page yields no text, we render that page to
an image using pdf2image and run tesseract on it. This turns scanned /
image-only PDFs into indexable text.

Falls back to a no-op if either dependency isn't available at runtime.
"""

from __future__ import annotations

import io
import logging

log = logging.getLogger("runner.ocr")


def _try_import():
    try:
        from pdf2image import convert_from_bytes  # noqa: F401
        import pytesseract  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def ocr_pages(data: bytes, page_indexes: list[int]) -> dict[int, str]:
    """Return {page_index_1_based: extracted_text} for the requested pages."""
    if not page_indexes or not _try_import():
        return {}
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
    except Exception:  # noqa: BLE001
        return {}

    out: dict[int, str] = {}
    for page_no in page_indexes:
        try:
            images = convert_from_bytes(
                data, first_page=page_no, last_page=page_no, dpi=200
            )
            if not images:
                continue
            text = pytesseract.image_to_string(images[0]) or ""
            text = text.strip()
            if text:
                out[page_no] = text
        except Exception as exc:  # noqa: BLE001
            log.warning("OCR failed for page %s: %s", page_no, exc)
    return out
