"""Cleaning and the five least-squares models. Everything is deterministic."""

from __future__ import annotations

import math

import pytest

from collections_api.domains.regression import models


def point(i, **overrides):
    """A well-behaved invented agent: value is exactly 3 + 2 * efficiency."""
    row = {
        "id": f"agent_{i:02d}",
        "efficiency": float(i),
        "value": 3.0 + 2.0 * i,
        "attempts": 10.0 + 2 * i + (i % 3),
        "contacts": 5.0 + i + (i % 4),
        "cpc": 20.0 + i,
        "conversion": 10.0 + (i % 5),
    }
    row.update(overrides)
    return row


def points(n=20):
    return [point(i) for i in range(1, n + 1)]


# ---------------------------------------------------------------- cleaning


def test_clean_points_of_a_tidy_set_removes_nothing():
    design, outcome, summary = models.clean_points(points())
    assert design.shape == (20, 5) and outcome.shape == (20,)
    assert (summary.raw_count, summary.clean_count) == (20, 20)
    assert (summary.removed_nulls, summary.removed_duplicates, summary.removed_outliers) == (0, 0, 0)


def test_points_with_a_missing_or_non_numeric_field_are_dropped_and_counted():
    dirty = points()
    dirty[0]["cpc"] = None
    dirty[1]["value"] = "12"  # a string is not a number here
    dirty[2]["attempts"] = float("nan")
    dirty[3]["conversion"] = True  # nor is a boolean
    del dirty[4]["contacts"]
    _, _, summary = models.clean_points(dirty)
    assert summary.removed_nulls == 5 and summary.clean_count == 15


def test_a_repeated_id_keeps_the_first_point_only():
    repeated = points() + [point(1, value=999.0)]
    design, outcome, summary = models.clean_points(repeated)
    assert summary.removed_duplicates == 1 and summary.clean_count == 20
    assert outcome[0] == 5.0  # the first write, not the 999


def test_points_without_an_id_cannot_be_duplicates_of_each_other():
    anonymous = [point(i, id=None) for i in range(1, 11)] + [point(1, id=None)]
    assert models.clean_points(anonymous)[2].removed_duplicates == 0


def test_an_outlier_on_any_field_is_removed_with_the_interquartile_rule():
    skewed = points() + [point(21, cpc=5000.0)]
    _, outcome, summary = models.clean_points(skewed)
    assert summary.removed_outliers == 1 and summary.clean_count == 20
    assert 3.0 + 2.0 * 21 not in outcome


def test_a_field_with_no_spread_never_makes_an_outlier():
    flat = [point(i, conversion=7.0) for i in range(1, 21)]
    assert models.clean_points(flat)[2].removed_outliers == 0


def test_nothing_is_trimmed_below_four_points():
    few = [point(1), point(2), point(3, value=10_000.0)]
    assert models.clean_points(few)[2].removed_outliers == 0


# ---------------------------------------------------------------- fitting


def fits():
    return {m.id: m for m in models.fit_all(points()).models}


def test_five_models_are_fitted_in_a_fixed_order():
    result = models.fit_all(points())
    assert [m.id for m in result.models] == ["simple", "loglog", "poly2", "volume", "full"]
    assert [m.drawable for m in result.models] == [True, True, True, False, False]
    assert result.cleaning.clean_count == 20


def test_a_linear_relation_is_recovered_exactly():
    simple = fits()["simple"]
    assert simple.intercept == pytest.approx(3.0, abs=1e-8)
    assert simple.coefficients[0].name == "x"
    assert simple.coefficients[0].value == pytest.approx(2.0, abs=1e-8)
    assert simple.r2_train == pytest.approx(1.0) and simple.r2_test == pytest.approx(1.0)
    assert simple.coefficients[0].se == pytest.approx(0.0, abs=1e-6)


def test_the_split_is_seventy_thirty_and_fixed():
    simple = fits()["simple"]
    assert (simple.train_n, simple.test_n) == (14, 6)
    again = models.fit_all(points()).models[0]
    assert (again.intercept, again.r2_test) == (simple.intercept, simple.r2_test)


def test_the_polynomial_model_has_a_quadratic_term_that_is_close_to_zero_for_a_line():
    poly = fits()["poly2"]
    assert [c.name for c in poly.coefficients] == ["x", "x^2"]
    assert poly.coefficients[1].value == pytest.approx(0.0, abs=1e-6)


def test_the_log_model_fits_a_power_law_exactly():
    power = [point(i, value=4.0 * i**1.5) for i in range(1, 21)]
    loglog = {m.id: m for m in models.fit_all(power).models}["loglog"]
    assert loglog.coefficients[0].name == "ln(x)"
    assert loglog.coefficients[0].value == pytest.approx(1.5, abs=1e-6)
    assert loglog.intercept == pytest.approx(math.log(4.0), abs=1e-6)


def test_the_multi_driver_models_name_their_drivers():
    by_id = fits()
    assert [c.name for c in by_id["volume"].coefficients] == ["attempts", "contacts"]
    assert [c.name for c in by_id["full"].coefficients] == ["attempts", "contacts", "cpc", "conversion"]


def test_adjusted_r2_is_never_above_r2_for_a_noisy_model():
    noisy = [point(i, value=3.0 + 2.0 * i + (7 if i % 2 else -7)) for i in range(1, 21)]
    simple = models.fit_all(noisy).models[0]
    assert 0 < simple.adj_r2 < simple.r2_train < 1


def test_fewer_than_three_clean_points_fit_nothing():
    result = models.fit_all(points(2))
    assert result.models == [] and result.cleaning.clean_count == 2
    assert models.fit_all([]).models == []


def test_three_points_are_reported_but_not_fitted():
    result = models.fit_all(points(3))
    assert len(result.models) == 5
    assert all(m.test_n == 0 and m.r2_train == 0.0 and m.intercept == 0.0 for m in result.models)
    assert all(c.value == 0.0 for m in result.models for c in m.coefficients)


def test_a_constant_outcome_has_no_explained_variance():
    flat = [point(i, value=5.0) for i in range(1, 21)]
    simple = models.fit_all(flat).models[0]
    assert simple.r2_train == 0.0 and simple.r2_test == 0.0


def test_a_zero_efficiency_does_not_break_the_log_model():
    zeros = [point(i, efficiency=float(i - 1), value=float(i)) for i in range(1, 21)]
    loglog = {m.id: m for m in models.fit_all(zeros).models}["loglog"]
    assert math.isfinite(loglog.intercept)


# ---------------------------------------------------------------- serialisation


def test_to_dict_is_json_ready_and_rounded():
    payload = models.to_dict(models.fit_all(points()), digits=3)
    assert payload["cleaning"] == {
        "raw_count": 20, "clean_count": 20, "removed_nulls": 0, "removed_duplicates": 0, "removed_outliers": 0,
    }
    simple = payload["models"][0]
    assert simple["intercept"] == 3.0 and simple["coefficients"][0] == {"name": "x", "value": 2.0, "se": 0.0}
    assert set(simple) == {
        "id", "label", "description", "drawable", "r2_train", "r2_test", "adj_r2",
        "train_n", "test_n", "intercept", "intercept_se", "coefficients",
    }
