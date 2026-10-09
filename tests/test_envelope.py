from collections_api.core.envelope import build_envelope
from collections_api.core.pagination import normalize_pagination, split_total


def test_envelope_has_exactly_the_documented_top_level_keys():
    envelope = build_envelope([{"a": 1}, {"a": 2}], ["auto"], run_id="srv-1")
    assert set(envelope) == {"meta", "data", "errors"}
    assert envelope["meta"]["total_rows"] == 2
    assert envelope["meta"]["sources"] == ["auto"]
    assert envelope["meta"]["run_id"] == "srv-1"
    assert envelope["meta"]["filters"] == {}
    assert envelope["meta"]["quality"] == {}
    assert "pagination" not in envelope["meta"]
    assert envelope["errors"] == []


def test_object_payload_counts_as_one_row_and_stays_an_object():
    envelope = build_envelope({"q1": 1.0}, ["auto"])
    assert envelope["data"] == {"q1": 1.0}
    assert envelope["meta"]["total_rows"] == 1


def test_pagination_errors_and_filters_are_carried():
    envelope = build_envelope(
        [],
        ["auto", "consumer"],
        errors=[{"message": "late"}],
        filters={"period": "today"},
        pagination={"limit": 5},
        quality={"stale": True},
    )
    assert envelope["meta"]["pagination"] == {"limit": 5}
    assert envelope["meta"]["filters"] == {"period": "today"}
    assert envelope["meta"]["quality"] == {"stale": True}
    assert envelope["errors"] == [{"message": "late"}]


def test_envelope_does_not_alias_its_inputs():
    filters = {"period": "today"}
    envelope = build_envelope([], [], filters=filters)
    filters["period"] = "changed"
    assert envelope["meta"]["filters"] == {"period": "today"}


def test_pagination_clamps_to_the_configured_window():
    kwargs = {"default_limit": 100, "max_limit": 500}
    assert normalize_pagination(None, None, **kwargs) == (100, 0)
    assert normalize_pagination(0, -3, **kwargs) == (100, 0)
    assert normalize_pagination(10_000, 7, **kwargs) == (500, 7)
    assert normalize_pagination(25, 40, **kwargs) == (25, 40)


def test_split_total_removes_the_helper_column():
    rows, total = split_total([{"_total_rows": 9, "a": 1}, {"_total_rows": 9, "a": 2}])
    assert total == 9
    assert rows == [{"a": 1}, {"a": 2}]
    assert split_total([]) == ([], 0)
