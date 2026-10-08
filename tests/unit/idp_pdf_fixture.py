# SPDX-License-Identifier: Apache-2.0
"""A minimal PDF built by hand for the document-processing tests: one page per document, Helvetica text at known places.

No PDF library writes it, so the tests exercise the reader against a file
whose text positions are known exactly: each line sits 22 points below the
one before, and a line with cells (two spaces apart) places each cell 110
points to the right of the previous one.
"""

from __future__ import annotations

import io

LINE_STEP = 22
CELL_STEP = 110
LEFT = 60
TOP = 80


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(*documents: list[str], width: int = 595, height: int = 842, table_columns: bool = True) -> bytes:
    """A PDF with one page per document; an empty document is a blank page."""
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    page_ids: list[int] = []
    next_id = 3
    contents: list[tuple[int, bytes]] = []
    for lines in documents or ([],):
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        page_ids.append(page_id)
        ops: list[str] = []
        y = height - TOP
        for line in lines:
            x = LEFT
            cells = [c for c in line.split("  ") if c.strip()] if table_columns and "  " in line else [line]
            for cell in cells:
                ops.append(f"BT /F1 11 Tf {x} {y} Td ({_escape(cell.strip())}) Tj ET")
                x += CELL_STEP
            y -= LINE_STEP
        stream = "\n".join(ops).encode("latin-1")
        contents.append(
            (content_id, b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        )
        objects.append(b"")  # placeholder for the page, filled below
        objects.append(b"")  # placeholder for the content
    font_id = next_id
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.insert(1, f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    for pid, (cid, content) in zip(page_ids, contents, strict=True):
        objects[pid - 1] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Contents {cid} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        ).encode()
        objects[cid - 1] = content
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()
