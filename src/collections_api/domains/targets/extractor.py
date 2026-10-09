"""Extract a targets table from a PDF by word position.

A targets document may have no detectable table grid, so the extractor works from
words and their coordinates (what ``pdfplumber`` returns from ``extract_words``). The
header row fixes the x position of every column; each word on the following lines goes to
the nearest column by its horizontal centre.

Expected layout, left to right (the 16 columns are configurable only by editing
``Layout``): office, portfolio, group, headcount, then four metrics with one column per
month for three months: cash target, recovery count, recovery value, total target. The
header row is found by the word ``Layout.anchor`` and must contain all columns; the
month columns are the first three header words of the form ``YYYYMM``.

The parsing is a pure function of the words, so the tests feed it synthetic words and
need no PDF. Only ``extract_pdf`` touches ``pdfplumber``, and it is imported lazily
(install the ``pdf`` extra).

Two checks guard against a mis-assigned column, which is the failure this approach is
prone to: on every row ``total = cash + recovery value`` for every month, and the sum of
the rows' totals equals the document's ``TOTAL`` row. If either fails nothing is
returned.
"""

from __future__ import annotations

import importlib
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

Word = Mapping[str, Any]

TOTAL_ROW = re.compile(r"^\s*TOTAL\b", re.IGNORECASE)
MONTH = re.compile(r"^\d{6}$")
_COMMA_THOUSANDS = re.compile(r"\d{1,3}(,\d{3}){2,}")
_DOT_THOUSANDS = re.compile(r"\d{1,3}(\.\d{3}){2,}")
_PERIOD_IN_NAME = re.compile(r"(\d)Q(\d{2,4})(?!\d)", re.IGNORECASE)
TOLERANCE = 0.01

METRICS = ("cash_target", "recovery_count_target", "recovery_value_target", "total_target")


class ExtractionError(ValueError):
    """The document did not match the expected layout or failed a checksum."""


@dataclass(frozen=True)
class Layout:
    group_names: tuple[str, ...] = ("Group A", "Group B")
    anchor: str = "Group"
    column_count: int = 16
    line_tolerance: float = 2.5


# --------------------------------------------------------------------------- numbers


def detect_thousands(rows: Sequence[Sequence[str]]) -> str:
    """Thousands separator of the document: ``,`` or ``.``.

    A document may use either convention. A value of a million or more settles it, because
    it has two separators; with no such value, the comma convention is assumed.
    """
    for row in rows:
        for cell in row:
            text = cell.replace(" ", "")
            if _DOT_THOUSANDS.search(text):
                return "."
            if _COMMA_THOUSANDS.search(text):
                return ","
    return ","


def parse_number(raw: str | None, thousands: str = ",") -> float | None:
    """Number from a cell. Blank, dash and ``N/A`` are ``None``.

    The PDF text layer sometimes splits a number with a space ("1 23,456"); spaces are
    removed before parsing.
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(" ", "")
    if text in ("", "-", "\u2014", "\u2013", "N/A", "n/a"):  # hyphen, em dash, en dash
        return None
    text = text.replace(".", "").replace(",", ".") if thousands == "." else text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------- positions


def group_lines(words: Sequence[Word], tolerance: float) -> list[list[Word]]:
    """Words on the same text line (vertical position within ``tolerance``), top to bottom."""
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1][0]["top"] - word["top"]) < tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
    return lines


def assign_columns(line: Sequence[Word], centers: Sequence[float]) -> list[str]:
    """Put each word in the column whose boundary interval contains its centre."""
    bounds = [(centers[i] + centers[i + 1]) / 2 for i in range(len(centers) - 1)]
    cells = [""] * len(centers)
    for word in sorted(line, key=lambda w: w["x0"]):
        centre = (word["x0"] + word["x1"]) / 2
        index = 0
        while index < len(bounds) and centre > bounds[index]:
            index += 1
        cells[index] = f"{cells[index]} {word['text']}".strip()
    return cells


def extract_cells(
    pages: Sequence[Sequence[Word]], layout: Layout
) -> tuple[list[str], list[list[str]]]:
    """``(months, rows_of_cells)`` from the words of every page."""
    months: list[str] = []
    centers: list[float] = []
    rows: list[list[str]] = []
    for words in pages:
        for line in group_lines(words, layout.line_tolerance):
            texts = [w["text"] for w in line]
            if not centers and layout.anchor in texts and len(texts) >= layout.column_count:
                centers = [(w["x0"] + w["x1"]) / 2 for w in line[: layout.column_count]]
                months = [t for t in texts if MONTH.match(t)][:3]
                continue
            if centers and texts and texts[0] != layout.anchor:
                cells = assign_columns(line, centers)
                if cells[1]:  # something in the portfolio column
                    rows.append(cells)
    if not centers:
        raise ExtractionError("Column header row not found")
    if len(months) < 3:
        raise ExtractionError(f"Month columns not found in the header: {months}")
    return months, rows


# --------------------------------------------------------------------------- rows


def _repair_dimensions(cells: Sequence[str], layout: Layout) -> tuple[str, str | None]:
    """A long portfolio name can spill into the start of the group cell; move it back."""
    portfolio, group = cells[1], cells[2]
    for name in layout.group_names:
        if group == name:
            return portfolio, name
        if group.endswith(name):
            prefix = group[: -len(name)].strip()
            return f"{portfolio} {prefix}".strip(), name
    return portfolio, (group or None)


def parse_row(
    cells: Sequence[str], months: Sequence[str], thousands: str, layout: Layout
) -> dict[str, Any] | None:
    if len(cells) < layout.column_count:
        return None
    portfolio, group = _repair_dimensions(cells, layout)
    if not portfolio:
        return None

    def block(first: int, as_int: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for offset, month in enumerate(months):
            value = parse_number(cells[first + offset], thousands) or 0.0
            out[month] = int(value) if as_int else value
        return out

    return {
        "office": cells[0] or None,
        "portfolio": portfolio,
        "group": group,
        "headcount": parse_number(cells[3], thousands),
        "cash_target": block(4),
        "recovery_count_target": block(7, as_int=True),
        "recovery_value_target": block(10),
        "total_target": block(13),
    }


def check_rows(rows: Sequence[Mapping[str, Any]], months: Sequence[str]) -> list[str]:
    """Row-level check: total target equals cash plus recovery value, per month."""
    problems: list[str] = []
    for row in rows:
        for month in months:
            expected = row["cash_target"][month] + row["recovery_value_target"][month]
            if abs(expected - row["total_target"][month]) > TOLERANCE:
                problems.append(
                    f"{row['portfolio']} {month}: cash + recovery value = {expected:,.2f}, "
                    f"total target = {row['total_target'][month]:,.2f}"
                )
    return problems


def check_total_row(
    rows: Sequence[Mapping[str, Any]], total_row: Mapping[str, Any], months: Sequence[str]
) -> list[str]:
    """Document-level check: the rows add up to the TOTAL row."""
    problems: list[str] = []
    for month in months:
        summed = sum(row["total_target"][month] for row in rows)
        if abs(summed - total_row["total_target"][month]) > TOLERANCE:
            problems.append(
                f"{month}: rows sum to {summed:,.2f}, TOTAL row says "
                f"{total_row['total_target'][month]:,.2f}"
            )
    return problems


def period_from_name(name: str, now: datetime | None = None) -> str:
    """``3Q26`` from a file name such as ``targets_3Q2026.pdf``. Falls back to the current quarter."""
    stem = os.path.splitext(os.path.basename(name))[0]
    match = _PERIOD_IN_NAME.search(stem)
    if match:
        quarter, year = match.group(1), match.group(2)
        return f"{quarter}Q{year[-2:]}"
    now = now or datetime.now()
    return f"{(now.month - 1) // 3 + 1}Q{str(now.year)[2:]}"


# --------------------------------------------------------------------------- documents


def build_document(
    pages: Sequence[Sequence[Word]],
    source_name: str,
    layout: Layout | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Parse the words of a document into the targets JSON. Raises ``ExtractionError``."""
    layout = layout or Layout()
    months, raw_rows = extract_cells(pages, layout)
    thousands = detect_thousands(raw_rows)

    rows: list[dict[str, Any]] = []
    total_row: dict[str, Any] | None = None
    for cells in raw_rows:
        parsed = parse_row(cells, months, thousands, layout)
        if parsed is None:
            continue
        if TOTAL_ROW.match(parsed["portfolio"]):
            total_row = parsed  # the last TOTAL line is the grand total
            continue
        if parsed["group"] not in layout.group_names:
            continue  # titles, footers and signatures are not data rows
        rows.append(parsed)

    if not rows:
        raise ExtractionError("No data rows found")
    if total_row is None:
        raise ExtractionError("TOTAL row not found")
    problems = check_rows(rows, months) + check_total_row(rows, total_row, months)
    if problems:
        raise ExtractionError("Validation failed:\n" + "\n".join(f"  - {p}" for p in problems))

    first = months[0]
    moment = now or datetime.now(tz=UTC)
    return {
        "meta": {
            "period": period_from_name(source_name, moment),
            "months": list(months),
            "extracted_at": moment.isoformat(),
            "source_file": os.path.basename(source_name),
            "row_count": len(rows),
            "validated": True,
            "rows_total_first_month": round(sum(r["total_target"][first] for r in rows), 2),
            "total_row_first_month": round(total_row["total_target"][first], 2),
        },
        "rows": rows,
    }


def extract_pdf(
    path: str, source_name: str | None = None, layout: Layout | None = None
) -> dict[str, Any]:
    """Read a PDF with ``pdfplumber`` and build the targets document."""
    try:
        pdfplumber = importlib.import_module("pdfplumber")
    except ImportError as exc:
        raise ExtractionError("Reading PDFs needs the 'pdf' extra (pdfplumber)") from exc
    try:
        with pdfplumber.open(path) as pdf:
            pages = [page.extract_words() for page in pdf.pages]
    except Exception as exc:  # the parser raises its own types for damaged or non-PDF input
        raise ExtractionError("The file could not be read as a PDF") from exc
    return build_document(pages, source_name or path, layout)
