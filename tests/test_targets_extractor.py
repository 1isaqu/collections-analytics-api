"""Targets extraction from words with coordinates, the checksums, and the real PDF path."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from collections_api.domains.targets import extractor
from collections_api.domains.targets.extractor import ExtractionError, Layout
from tests import targets_pdf as t

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def build(lines, name="targets_3Q2026.pdf", **options):
    return extractor.build_document([t.page(lines)], name, now=NOW, **options)


def edited(row, **cells):
    """A copy of ``row`` with cells replaced by column index (``c5="9"``)."""
    copy = list(row)
    for key, value in cells.items():
        copy[int(key[1:])] = value
    return copy


# ---------------------------------------------------------------- numbers


@pytest.mark.parametrize(
    ("raw", "thousands", "expected"),
    [
        ("1,500", ",", 1500.0),
        ("1 46,411", ",", 146411.0),  # the text layer sometimes splits a number
        ("1.234,50", ".", 1234.5),
        ("12", ",", 12.0),
        ("-", ",", None),
        ("N/A", ",", None),
        ("", ",", None),
        (None, ",", None),
        ("abc", ",", None),
    ],
)
def test_parse_number(raw, thousands, expected):
    assert extractor.parse_number(raw, thousands) == expected


def test_the_thousands_separator_is_read_from_a_value_with_two_of_them():
    assert extractor.detect_thousands([["1,234,567"]]) == ","
    assert extractor.detect_thousands([["1.234.567,50"]]) == "."
    assert extractor.detect_thousands([["1,500", "2,000"]]) == ","  # nothing decisive: comma
    assert extractor.detect_thousands([]) == ","


def test_a_document_that_writes_numbers_with_dots_is_read_correctly():
    rows = [
        t.HEADER,
        edited(
            t.GROUP_A,
            c4="1.000.000,50",
            c13="1.000.500,50",
            c10="500",
            c5="0",
            c6="0",
            c11="0",
            c12="0",
            c14="0",
            c15="0",
        ),
        edited(t.TOTAL, c13="1.000.500,50", c14="0", c15="0"),
    ]
    document = build(rows)
    assert document["rows"][0]["cash_target"]["202601"] == 1_000_000.5
    assert document["meta"]["total_row_first_month"] == 1_000_500.5


# ---------------------------------------------------------------- positions


def test_words_on_one_line_are_grouped_within_the_tolerance():
    words = [
        {"text": "b", "x0": 50, "x1": 60, "top": 100.0},
        {"text": "a", "x0": 10, "x1": 20, "top": 101.0},
        {"text": "c", "x0": 10, "x1": 20, "top": 130.0},
    ]
    lines = extractor.group_lines(words, 2.5)
    assert [sorted(w["text"] for w in line) for line in lines] == [["a", "b"], ["c"]]


def test_a_word_goes_to_the_column_whose_interval_holds_its_centre():
    centers = [10.0, 30.0, 50.0]
    words = [
        {"text": "x", "x0": 5, "x1": 15},  # centre 10
        {"text": "y", "x0": 22, "x1": 36},  # centre 29, still the second column
        {"text": "z", "x0": 42, "x1": 48},
        {"text": "w", "x0": 90, "x1": 99},  # far right: the last column
    ]
    assert extractor.assign_columns(words, centers) == ["x", "y", "z w"]


# ---------------------------------------------------------------- documents


def test_a_valid_document_is_parsed_with_its_metadata():
    document = build(t.document_lines())
    assert document["meta"] == {
        "period": "3Q26",
        "months": ["202601", "202602", "202603"],
        "extracted_at": NOW.isoformat(),
        "source_file": "targets_3Q2026.pdf",
        "row_count": 2,
        "validated": True,
        "rows_total_first_month": 3800.0,
        "total_row_first_month": 3800.0,
    }
    first, second = document["rows"]
    assert first == {
        "office": "Off1",
        "portfolio": "Port A1",
        "group": "Group A",
        "headcount": 12.0,
        "cash_target": {"202601": 1000.0, "202602": 1100.0, "202603": 1200.0},
        "recovery_count_target": {"202601": 1, "202602": 2, "202603": 3},
        "recovery_value_target": {"202601": 500.0, "202602": 600.0, "202603": 700.0},
        "total_target": {"202601": 1500.0, "202602": 1700.0, "202603": 1900.0},
    }
    assert second["group"] == "Group B" and second["recovery_count_target"] == {
        "202601": 4,
        "202602": 4,
        "202603": 4,
    }


def test_titles_and_footers_are_not_data_rows():
    lines = [["Quarterly", "targets"], *t.document_lines(), ["Prepared", "by", "Agent 01"]]
    document = build(lines)
    assert [r["portfolio"] for r in document["rows"]] == ["Port A1", "Port B1"]


def test_a_document_can_span_pages_with_the_header_on_the_first():
    pages = [t.page([t.HEADER, t.GROUP_A]), t.page([t.GROUP_B, t.TOTAL])]
    document = extractor.build_document(pages, "t.pdf", now=NOW)
    assert document["meta"]["row_count"] == 2


def test_the_group_names_come_from_the_layout():
    renamed = Layout(group_names=("Group A", "Group C"))
    with pytest.raises(ExtractionError, match="Validation failed"):
        build(t.document_lines(), layout=renamed)  # Group B no longer counts, the sums break
    document = build(
        [t.HEADER, t.GROUP_A, edited(t.GROUP_B, c2="Group C"), t.TOTAL], layout=renamed
    )
    assert document["rows"][1]["group"] == "Group C"


def test_a_long_portfolio_name_that_spills_into_the_group_column_is_put_back():
    words = t.page([t.HEADER, edited(t.GROUP_A, c2=""), t.GROUP_B, t.TOTAL])
    top = t.FIRST_TOP + t.LINE_STEP
    column = t.center(2)
    for text, start in (("Overflow", column - 24), ("Group", column + 2), ("A", column + 16)):
        words.append({"text": text, "x0": start, "x1": start + 5 * len(text), "top": top})
    document = extractor.build_document([words], "t.pdf", now=NOW)
    assert document["rows"][0]["portfolio"] == "Port A1 Overflow"
    assert document["rows"][0]["group"] == "Group A"


def test_the_last_total_row_is_the_grand_total():
    subtotal = edited(t.TOTAL, c13="1", c14="1", c15="1")
    assert build([t.HEADER, t.GROUP_A, t.GROUP_B, subtotal, t.TOTAL])["meta"]["validated"] is True


# ---------------------------------------------------------------- checksums and failures


def test_a_row_whose_total_is_not_cash_plus_recovery_value_is_rejected():
    wrong = edited(t.GROUP_A, c14="9,999")
    with pytest.raises(ExtractionError) as caught:
        build([t.HEADER, wrong, t.GROUP_B, t.TOTAL])
    assert "Port A1 202602" in str(caught.value) and "cash + recovery value" in str(caught.value)


def test_rows_that_do_not_add_up_to_the_total_row_are_rejected():
    wrong_total = edited(t.TOTAL, c13="3,801")
    with pytest.raises(ExtractionError) as caught:
        build([t.HEADER, t.GROUP_A, t.GROUP_B, wrong_total])
    assert "202601: rows sum to 3,800.00, TOTAL row says 3,801.00" in str(caught.value)


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ([["Quarterly", "targets"], ["no", "table", "here"]], "Column header row not found"),
        ([t.HEADER, t.TOTAL], "No data rows"),
        ([t.HEADER, t.GROUP_A, t.GROUP_B], "TOTAL row not found"),
        (
            [
                edited(
                    t.HEADER,
                    c4="x",
                    c5="y",
                    c6="z",
                    c7="a",
                    c8="b",
                    c9="c",
                    c10="d",
                    c11="e",
                    c12="f",
                    c13="g",
                    c14="h",
                    c15="i",
                ),
                t.GROUP_A,
            ],
            "Month columns",
        ),
    ],
)
def test_structural_failures_name_the_problem(lines, message):
    with pytest.raises(ExtractionError, match=message):
        build(lines)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("targets_3Q2026.pdf", "3Q26"),
        ("/some/dir/targets 1Q26 final.pdf", "1Q26"),
        ("2Q2027.PDF", "2Q27"),
        ("no-period-here.pdf", "3Q26"),  # falls back to the current quarter of NOW
    ],
)
def test_the_period_comes_from_the_file_name(name, expected):
    assert extractor.period_from_name(name, NOW) == expected


def test_the_fallback_period_follows_the_quarter_of_the_date():
    assert extractor.period_from_name("x.pdf", datetime(2026, 1, 2)) == "1Q26"
    assert extractor.period_from_name("x.pdf", datetime(2026, 12, 31)) == "4Q26"


# ---------------------------------------------------------------- the PDF path


def test_reading_a_pdf_needs_the_optional_dependency(monkeypatch):
    def missing(name):
        raise ImportError(name)

    monkeypatch.setattr(extractor.importlib, "import_module", missing)
    with pytest.raises(ExtractionError, match="'pdf' extra"):
        extractor.extract_pdf("anything.pdf")


def test_a_generated_pdf_goes_through_pdfplumber_and_the_same_checks(tmp_path):
    pytest.importorskip("pdfplumber")
    path = tmp_path / "targets_2Q2026.pdf"
    t.write_pdf(path, t.document_lines())
    document = extractor.extract_pdf(str(path), source_name="targets_2Q2026.pdf")
    assert document["meta"]["period"] == "2Q26" and document["meta"]["row_count"] == 2
    assert [r["total_target"]["202603"] for r in document["rows"]] == [1900.0, 2300.0]


def test_a_generated_pdf_with_a_wrong_total_is_rejected(tmp_path):
    pytest.importorskip("pdfplumber")
    path = tmp_path / "bad.pdf"
    t.write_pdf(path, [t.HEADER, t.GROUP_A, t.GROUP_B, edited(t.TOTAL, c15="1")])
    with pytest.raises(ExtractionError, match="Validation failed"):
        extractor.extract_pdf(str(path))


def test_a_file_that_is_not_a_pdf_is_an_extraction_error_not_a_parser_crash(tmp_path):
    pytest.importorskip("pdfplumber")
    path = tmp_path / "garbage.pdf"
    path.write_bytes(b"this is not a pdf")
    with pytest.raises(ExtractionError, match="could not be read as a PDF"):
        extractor.extract_pdf(str(path))
