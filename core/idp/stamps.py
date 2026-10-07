# SPDX-License-Identifier: Apache-2.0
"""Stamp and seal detection on a page image: regions of coloured ink that are not text.

A stamp or a seal is a block of saturated ink (blue, purple, red, green) on
a page whose text is black. The page is downscaled, pixels with enough
saturation are counted per cell of a grid, dense cells are grouped into
regions, and each region becomes a candidate with its box, colour, coverage
and a confidence from its size and density. What this verifies is that a
stamp is present where one is expected and roughly how large and what
colour it is, never that it is genuine.
"""

from __future__ import annotations

import colorsys
import io
from collections import deque
from dataclasses import dataclass
from typing import Any

CELL = 16  # pixels of the downscaled page per grid cell
MAX_SIDE = 600  # the page is downscaled to at most this many pixels on its longer side
SATURATION = 0.35
VALUE_MIN = 0.2
VALUE_MAX = 0.95
DENSITY = 0.12  # share of saturated pixels that makes a cell dense
MIN_CELLS = 4
EXPECTED: dict[str, tuple[str, ...]] = {
    "cheque": ("bank",),
    "agreement": ("seal", "signature"),
    "property_document": ("registrar",),
    "kyc_form": ("bank",),
    "loan_application": ("bank",),
}


@dataclass
class Candidate:
    page: int
    bbox: tuple[float, float, float, float]  # page points
    colour: str
    coverage: float  # share of the page area
    density: float
    confidence: float
    overlaps_text: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "bbox": [round(v, 1) for v in self.bbox],
            "colour": self.colour,
            "coverage": round(self.coverage, 4),
            "density": round(self.density, 2),
            "confidence": round(self.confidence, 2),
            "overlaps_text": self.overlaps_text,
        }


def colour_name(hue: float) -> str:
    degrees = hue * 360.0
    if degrees < 20 or degrees >= 330:
        return "red"
    if degrees < 70:
        return "orange"
    if degrees < 170:
        return "green"
    if degrees < 260:
        return "blue"
    return "purple"


def ink_cells(image: Any) -> tuple[list[list[float]], list[list[float]], float]:
    """Per grid cell: the share of saturated pixels and the mean hue of them; plus the downscale factor."""
    from PIL import Image

    rgb = image.convert("RGB")
    factor = max(rgb.width, rgb.height) / MAX_SIDE
    if factor > 1:
        rgb = rgb.resize((max(1, int(rgb.width / factor)), max(1, int(rgb.height / factor))), Image.BILINEAR)
    else:
        factor = 1.0
    width, height = rgb.size
    columns, rows = (width + CELL - 1) // CELL, (height + CELL - 1) // CELL
    counts = [[0] * columns for _ in range(rows)]
    hues = [[0.0] * columns for _ in range(rows)]
    totals = [[0] * columns for _ in range(rows)]
    pixels = rgb.load()
    for y in range(height):
        row = y // CELL
        for x in range(width):
            column = x // CELL
            totals[row][column] += 1
            r, g, b = pixels[x, y]
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            if s >= SATURATION and VALUE_MIN <= v <= VALUE_MAX:
                counts[row][column] += 1
                hues[row][column] += h
    shares = [[counts[r][c] / totals[r][c] if totals[r][c] else 0.0 for c in range(columns)] for r in range(rows)]
    mean_hues = [[hues[r][c] / counts[r][c] if counts[r][c] else 0.0 for c in range(columns)] for r in range(rows)]
    return shares, mean_hues, factor


def regions(shares: list[list[float]]) -> list[list[tuple[int, int]]]:
    """Groups of adjacent dense cells."""
    rows = len(shares)
    columns = len(shares[0]) if rows else 0
    seen: set[tuple[int, int]] = set()
    out: list[list[tuple[int, int]]] = []
    for r in range(rows):
        for c in range(columns):
            if shares[r][c] < DENSITY or (r, c) in seen:
                continue
            group = []
            queue = deque([(r, c)])
            seen.add((r, c))
            while queue:
                cr, cc = queue.popleft()
                group.append((cr, cc))
                for nr, nc in ((cr + 1, cc), (cr - 1, cc), (cr, cc + 1), (cr, cc - 1)):
                    if 0 <= nr < rows and 0 <= nc < columns and (nr, nc) not in seen and shares[nr][nc] >= DENSITY:
                        seen.add((nr, nc))
                        queue.append((nr, nc))
            if len(group) >= MIN_CELLS:
                out.append(group)
    return out


def detect(
    image: Any,
    *,
    page_number: int,
    page_size: tuple[float, float],
    text_boxes: list[tuple[float, float, float, float]] | None = None,
) -> list[Candidate]:
    """Stamp candidates on a page image, boxes in page points."""
    shares, hues, factor = ink_cells(image)
    scale_x = page_size[0] / max(1.0, image.width)
    scale_y = page_size[1] / max(1.0, image.height)
    page_area = max(1.0, page_size[0] * page_size[1])
    out: list[Candidate] = []
    for group in regions(shares):
        rows_ = [r for r, _ in group]
        cols = [c for _, c in group]
        x0 = min(cols) * CELL * factor * scale_x
        y0 = min(rows_) * CELL * factor * scale_y
        x1 = (max(cols) + 1) * CELL * factor * scale_x
        y1 = (max(rows_) + 1) * CELL * factor * scale_y
        density = sum(shares[r][c] for r, c in group) / len(group)
        hue = sum(hues[r][c] * shares[r][c] for r, c in group) / max(1e-9, sum(shares[r][c] for r, c in group))
        coverage = ((x1 - x0) * (y1 - y0)) / page_area
        overlaps = any(not (x1 < b[0] or b[2] < x0 or y1 < b[1] or b[3] < y0) for b in (text_boxes or []))
        confidence = min(0.95, 0.4 + 0.3 * min(1.0, len(group) / 12) + 0.3 * min(1.0, density / 0.5))
        if overlaps:
            confidence *= 0.8
        out.append(
            Candidate(
                page_number, (x0, y0, x1, y1), colour_name(hue), coverage, density, round(confidence, 2), overlaps
            )
        )
    out.sort(key=lambda c: -c.confidence)
    return out[:10]


def detect_from_png(
    png: bytes,
    *,
    page_number: int,
    page_size: tuple[float, float],
    text_boxes: list[tuple[float, float, float, float]] | None = None,
) -> list[Candidate]:
    from PIL import Image

    image = Image.open(io.BytesIO(png))
    image.load()
    return detect(image, page_number=page_number, page_size=page_size, text_boxes=text_boxes)


def verify(document_type: str, candidates: list[Candidate]) -> dict[str, Any]:
    """Whether a stamp is present where the document type expects one; what is checked is presence, not authenticity."""
    expected = EXPECTED.get(document_type, ())
    present = [c for c in candidates if c.confidence >= 0.5]
    return {
        "expected": list(expected),
        "present": bool(present),
        "candidates": [c.to_dict() for c in candidates],
        "status": "present" if present else ("missing" if expected else "not_expected"),
        "note": "Presence, colour and size of ink regions; not an authenticity check.",
    }
