# SPDX-License-Identifier: Apache-2.0
"""Pages of words with bounding boxes, from a PDF's text layer or from OCR of a scanned page or an image.

Every word carries its box in page points (origin top-left) and a confidence:
1.0 from a text layer, the engine's own figure from OCR. A page with no text
layer is OCR'd when the engine is installed; when it is not, the page says so
(``ocr: unavailable``) rather than pretending it read nothing.
"""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()

MAX_PAGES = 50
MAX_BYTES = 25 * 1024 * 1024
OCR_DPI = 200
PDF_MIMES = ("application/pdf",)
IMAGE_MIMES = ("image/png", "image/jpeg", "image/tiff", "image/bmp", "image/webp")


class DocumentError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class Word:
    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 in page points
    confidence: float = 1.0
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "bbox": [round(v, 1) for v in self.bbox],
            "confidence": round(self.confidence, 2),
            "line": self.line,
        }


@dataclass
class Line:
    text: str
    bbox: tuple[float, float, float, float]
    words: list[Word] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        return sum(w.confidence for w in self.words) / len(self.words) if self.words else 0.0


@dataclass
class Page:
    number: int  # 1-indexed
    width: float
    height: float
    words: list[Word] = field(default_factory=list)
    source: str = "text"  # text | ocr | empty
    ocr: str = "not_needed"  # not_needed | done | unavailable | failed
    script: str | None = None

    @property
    def lines(self) -> list[Line]:
        return group_lines(self.words)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def confidence(self) -> float:
        return sum(w.confidence for w in self.words) / len(self.words) if self.words else 0.0

    def to_dict(self, *, with_words: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "number": self.number,
            "width": round(self.width, 1),
            "height": round(self.height, 1),
            "source": self.source,
            "ocr": self.ocr,
            "script": self.script,
            "words": len(self.words),
            "confidence": round(self.confidence, 2),
            "lines": [{"text": line.text, "bbox": [round(v, 1) for v in line.bbox]} for line in self.lines],
        }
        if with_words:
            out["word_boxes"] = [w.to_dict() for w in self.words]
        return out


def union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def group_lines(words: list[Word], *, tolerance: float = 4.0) -> list[Line]:
    """Words into lines by their vertical position, each line's words left to right."""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: ((w.bbox[1] + w.bbox[3]) / 2, w.bbox[0]))
    lines: list[list[Word]] = []
    for word in ordered:
        middle = (word.bbox[1] + word.bbox[3]) / 2
        if lines:
            last = lines[-1]
            last_middle = sum((w.bbox[1] + w.bbox[3]) / 2 for w in last) / len(last)
            height = max(1.0, max(w.bbox[3] - w.bbox[1] for w in last))
            if abs(middle - last_middle) <= max(tolerance, height * 0.5):
                last.append(word)
                continue
        lines.append([word])
    out = []
    for index, group in enumerate(lines):
        group.sort(key=lambda w: w.bbox[0])
        for w in group:
            w.line = index
        out.append(Line(text=" ".join(w.text for w in group), bbox=union([w.bbox for w in group]), words=group))
    return out


def ocr_available() -> bool:
    try:
        import pytesseract  # type: ignore[import-untyped]  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return bool(shutil.which("tesseract"))


def ocr_words(image: Any, *, scale: float = 1.0) -> tuple[list[Word], str | None]:
    """Words and boxes from the OCR engine for a PIL image; boxes scaled back by ``scale`` to page points."""
    import pytesseract  # type: ignore[import-untyped]
    from PIL import ImageOps
    from pytesseract import Output  # type: ignore[import-untyped]

    prepared = ImageOps.autocontrast(ImageOps.grayscale(image))
    script = None
    try:
        osd = pytesseract.image_to_osd(prepared, timeout=30)
        script = next((line.split(":", 1)[1].strip() for line in osd.splitlines() if line.startswith("Script:")), None)
    # enterprise-gate: broad-except-ok reason=ocr-engine-boundary-page-marked-failed-and-routed-to-review
    except Exception:  # noqa: BLE001 - orientation detection is optional
        script = None
    data = pytesseract.image_to_data(prepared, output_type=Output.DICT, timeout=120)
    words: list[Word] = []
    for text, left, top, width, height, conf in zip(
        data["text"], data["left"], data["top"], data["width"], data["height"], data["conf"], strict=False
    ):
        token = str(text).strip()
        try:
            confidence = float(conf)
        except (TypeError, ValueError):
            confidence = -1.0
        if not token or confidence < 0:
            continue
        box = (left / scale, top / scale, (left + width) / scale, (top + height) / scale)
        words.append(Word(text=token, bbox=box, confidence=max(0.0, min(1.0, confidence / 100.0))))
    return words, script


def _pdf_module() -> Any:
    try:
        import pymupdf  # type: ignore[import-untyped]

        return pymupdf
    except ImportError:  # pragma: no cover - older installs
        import fitz  # type: ignore[import-untyped]

        return fitz


def pdf_pages(stream: bytes, *, ocr: bool = True, max_pages: int = MAX_PAGES) -> list[Page]:
    """The pages of a PDF: the text layer's words where there is one, OCR where there is not."""
    pdf = _pdf_module()
    try:
        document = pdf.open(stream=stream, filetype="pdf")
    # enterprise-gate: broad-except-ok reason=ocr-engine-boundary-page-marked-failed-and-routed-to-review
    except Exception as exc:  # noqa: BLE001 - the parser's own error types vary
        raise DocumentError(422, "pdf_unreadable", f"The PDF could not be opened: {type(exc).__name__}") from None
    pages: list[Page] = []
    engine = ocr_available()
    for index, raw in enumerate(document):
        if index >= max_pages:
            break
        page = Page(number=index + 1, width=float(raw.rect.width), height=float(raw.rect.height))
        words = raw.get_text("words") or []
        if words:
            page.words = [
                Word(text=str(w[4]), bbox=(float(w[0]), float(w[1]), float(w[2]), float(w[3])))
                for w in words
                if str(w[4]).strip()
            ]
            page.source = "text"
        elif ocr and engine:
            try:
                from PIL import Image

                pix = raw.get_pixmap(dpi=OCR_DPI)
                image = Image.open(io.BytesIO(pix.tobytes("png")))
                page.words, page.script = ocr_words(image, scale=OCR_DPI / 72.0)
                page.source = "ocr" if page.words else "empty"
                page.ocr = "done"
            # enterprise-gate: broad-except-ok reason=ocr-engine-boundary-page-marked-failed-and-routed-to-review
            except Exception as exc:  # noqa: BLE001 - OCR failures are reported on the page, not raised
                logger.warning("idp_ocr_failed", page=index + 1, error_type=type(exc).__name__)
                page.source, page.ocr = "empty", "failed"
        else:
            page.source = "empty"
            page.ocr = "unavailable" if ocr else "not_needed"
        group_lines(page.words)
        pages.append(page)
    document.close()
    return pages


def image_pages(stream: bytes, *, ocr: bool = True) -> list[Page]:
    """A single image as one page, OCR'd when the engine is installed."""
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(stream))
        image.load()
    # enterprise-gate: broad-except-ok reason=ocr-engine-boundary-page-marked-failed-and-routed-to-review
    except Exception as exc:  # noqa: BLE001 - the decoder's own error types vary
        raise DocumentError(422, "image_unreadable", f"The image could not be opened: {type(exc).__name__}") from None
    page = Page(number=1, width=float(image.width), height=float(image.height), source="empty")
    if ocr and ocr_available():
        try:
            page.words, page.script = ocr_words(image.convert("RGB"))
            page.source = "ocr" if page.words else "empty"
            page.ocr = "done"
        # enterprise-gate: broad-except-ok reason=ocr-engine-boundary-page-marked-failed-and-routed-to-review
        except Exception as exc:  # noqa: BLE001
            logger.warning("idp_ocr_failed", page=1, error_type=type(exc).__name__)
            page.ocr = "failed"
    else:
        page.ocr = "unavailable" if ocr else "not_needed"
    group_lines(page.words)
    return [page]


def load_pages(stream: bytes, mime_type: str, *, ocr: bool = True) -> list[Page]:
    if len(stream) > MAX_BYTES:
        raise DocumentError(413, "too_large", f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB")
    if not stream:
        raise DocumentError(422, "empty_file", "The file is empty")
    mime = (mime_type or "").split(";")[0].strip().lower()
    if mime in PDF_MIMES or stream[:5] == b"%PDF-":
        return pdf_pages(stream, ocr=ocr)
    if mime in IMAGE_MIMES or mime.startswith("image/"):
        return image_pages(stream, ocr=ocr)
    raise DocumentError(415, "unsupported_type", f"Unsupported file type {mime or 'unknown'}; give a PDF or an image")
