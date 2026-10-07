"""Synthetic targets documents: words with coordinates, and a hand-written PDF.

The extractor works from words and positions, so the unit tests feed it invented words
and need no PDF. ``write_pdf`` draws the same words into a one-page PDF with plain text
operators, so the pdfplumber path can be tested end to end without a binary fixture.
"""

from __future__ import annotations

from pathlib import Path

COLUMN_COUNT = 16
FIRST_CENTER = 40.0
COLUMN_STEP = 50.0
LINE_STEP = 14.0
FIRST_TOP = 60.0
PAGE_WIDTH, PAGE_HEIGHT = 842, 595

HEADER = [
    "Office", "Portfolio", "Group", "Heads",
    "202601", "202602", "202603",
    "202601", "202602", "202603",
    "202601", "202602", "202603",
    "202601", "202602", "202603",
]


def row(office, portfolio, group, heads, cash, count, value, total):
    """One data row as 16 cell strings. Each of ``cash`` .. ``total`` has three months."""
    return [office, portfolio, group, heads, *cash, *count, *value, *total]


GROUP_A = row("Off1", "Port A1", "Group A", "12", ["1,000", "1,100", "1,200"], ["1", "2", "3"], ["500", "600", "700"], ["1,500", "1,700", "1,900"])
GROUP_B = row("Off1", "Port B1", "Group B", "8", ["2,000", "2,000", "2,000"], ["4", "4", "4"], ["300", "300", "300"], ["2,300", "2,300", "2,300"])
TOTAL = row("", "TOTAL", "", "", ["", "", ""], ["", "", ""], ["", "", ""], ["3,800", "4,000", "4,200"])


def center(column: int) -> float:
    return FIRST_CENTER + COLUMN_STEP * column


def line_words(cells, top, scale=5.0):
    """Words of one line. A cell with spaces becomes several words around its centre."""
    words = []
    for column, cell in enumerate(cells):
        parts = cell.split()
        if not parts:
            continue
        widths = [scale * len(part) for part in parts]
        total = sum(widths) + 3.0 * (len(parts) - 1)
        x = center(column) - total / 2
        for part, width in zip(parts, widths, strict=True):
            words.append({"text": part, "x0": x, "x1": x + width, "top": top, "bottom": top + 8})
            x += width + 3.0
    return words


def page(lines, first_top=FIRST_TOP):
    """Words of a page from rows of cell strings, one line every ``LINE_STEP``."""
    words = []
    for index, cells in enumerate(lines):
        words.extend(line_words(cells, first_top + LINE_STEP * index))
    return words


def document_lines():
    return [HEADER, GROUP_A, GROUP_B, TOTAL]


def write_pdf(path: Path, lines) -> None:
    """A one-page PDF with every cell drawn as text at its column. Standard fonts only."""
    drawing = ["BT", "/F1 7 Tf"]
    for index, cells in enumerate(lines):
        y = PAGE_HEIGHT - (FIRST_TOP + LINE_STEP * index)
        for column, cell in enumerate(cells):
            if cell:
                escaped = cell.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                drawing.append(f"1 0 0 1 {center(column) - 12:.1f} {y:.1f} Tm ({escaped}) Tj")
    drawing.append("ET")
    stream = "\n".join(drawing).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ).encode(),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(bytes(out))
