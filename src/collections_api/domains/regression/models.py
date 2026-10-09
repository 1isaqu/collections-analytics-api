"""Regression models for agent performance, in plain numpy.

Input is one point per agent: a value (the outcome) and the drivers that might explain
it. The service cleans the points, then fits five ordinary-least-squares models on a
70/30 train/test split and reports fit quality and coefficient standard errors.

* ``simple``: value ~ efficiency
* ``loglog``: ln(value) ~ ln(efficiency)
* ``poly2``: value ~ efficiency + efficiency^2
* ``volume``: value ~ attempts + contacts
* ``full``: value ~ attempts + contacts + cpc + conversion

The split is a seeded permutation, so the same input always gives the same output.
Standard errors come from the training rows the model was fitted on.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

NUMERIC_FIELDS = ("efficiency", "value", "attempts", "contacts", "cpc", "conversion")
MIN_POINTS_TO_FIT = 3
MIN_POINTS_PER_MODEL = 4
LOG_FLOOR = 1e-9


@dataclass
class CoefficientEstimate:
    name: str
    value: float
    se: float


@dataclass
class ModelResult:
    id: str
    label: str
    description: str
    drawable: bool
    r2_train: float
    r2_test: float
    adj_r2: float
    train_n: int
    test_n: int
    intercept: float
    intercept_se: float
    coefficients: list[CoefficientEstimate] = field(default_factory=list)


@dataclass
class CleaningSummary:
    raw_count: int
    clean_count: int
    removed_nulls: int
    removed_duplicates: int
    removed_outliers: int


@dataclass
class RegressionResult:
    cleaning: CleaningSummary
    models: list[ModelResult]


def _is_missing(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return not math.isfinite(value)
    return True  # strings and anything else are not numbers


def clean_points(
    points: Sequence[Mapping[str, Any]],
) -> tuple[np.ndarray, np.ndarray, CleaningSummary]:
    """Drop incomplete points, repeated ids and IQR outliers.

    Returns the design matrix (efficiency, attempts, contacts, cpc, conversion), the
    outcome vector and counts of what was removed. A point is an outlier when any numeric
    field falls outside 1.5 interquartile ranges of its quartiles; fields with a zero
    spread are skipped, and nothing is trimmed below four points.
    """
    raw_count = len(points)
    complete = [p for p in points if not any(_is_missing(p.get(f)) for f in NUMERIC_FIELDS)]
    removed_nulls = raw_count - len(complete)

    seen: set[Any] = set()
    unique: list[Mapping[str, Any]] = []
    for point in complete:
        identifier = point.get("id")
        # Points without an id cannot be duplicates of each other.
        if identifier is not None and identifier in seen:
            continue
        seen.add(identifier)
        unique.append(point)
    removed_duplicates = len(complete) - len(unique)

    kept = list(unique)
    removed_outliers = 0
    if len(kept) >= 4:
        outliers: set[int] = set()
        for name in NUMERIC_FIELDS:
            values = sorted(p[name] for p in kept)
            q1 = values[len(values) // 4]
            q3 = values[(3 * len(values)) // 4]
            spread = q3 - q1
            if spread == 0:
                continue
            low, high = q1 - 1.5 * spread, q3 + 1.5 * spread
            outliers.update(i for i, p in enumerate(kept) if p[name] < low or p[name] > high)
        removed_outliers = len(outliers)
        kept = [p for i, p in enumerate(kept) if i not in outliers]

    design = np.array(
        [[p["efficiency"], p["attempts"], p["contacts"], p["cpc"], p["conversion"]] for p in kept],
        dtype=np.float64,
    ).reshape(len(kept), 5)
    outcome = np.array([p["value"] for p in kept], dtype=np.float64)
    summary = CleaningSummary(
        raw_count, len(kept), removed_nulls, removed_duplicates, removed_outliers
    )
    return design, outcome, summary


def _split(n: int, test_size: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    order = np.random.default_rng(seed).permutation(n)
    n_test = math.ceil(test_size * n)
    return order[n_test:], order[:n_test]


def _fit(features: np.ndarray, target: np.ndarray) -> tuple[float, np.ndarray]:
    """Least squares with an intercept. Returns ``(intercept, coefficients)``."""
    augmented = np.column_stack([np.ones(len(features)), features])
    solution, *_ = np.linalg.lstsq(augmented, target, rcond=None)
    return float(solution[0]), solution[1:]


def _r_squared(
    features: np.ndarray, target: np.ndarray, intercept: float, coef: np.ndarray
) -> float:
    if len(target) == 0:
        return 0.0
    residual = float(np.sum((target - (intercept + features @ coef)) ** 2))
    total = float(np.sum((target - target.mean()) ** 2))
    return 0.0 if total == 0 else 1.0 - residual / total


def _standard_errors(features: np.ndarray, target: np.ndarray, intercept: float, coef: np.ndarray):
    """Standard errors of ``[intercept, *coefficients]`` from the training fit."""
    n, p = features.shape
    residual = float(np.sum((target - (intercept + features @ coef)) ** 2))
    sigma2 = residual / max(1, n - (p + 1))
    augmented = np.column_stack([np.ones(n), features])
    try:
        covariance = sigma2 * np.linalg.inv(augmented.T @ augmented)
        return np.sqrt(np.maximum(np.diag(covariance), 0.0))
    except np.linalg.LinAlgError:
        return np.zeros(p + 1)


def _adjusted_r2(r2: float, n: int, p: int) -> float:
    return 0.0 if n <= p + 1 else 1.0 - (1.0 - r2) * (n - 1) / (n - p - 1)


def _fit_model(
    features: np.ndarray,
    target: np.ndarray,
    *,
    model_id: str,
    label: str,
    description: str,
    drawable: bool,
    names: Sequence[str],
    seed: int = 42,
    test_size: float = 0.3,
) -> ModelResult:
    n, p = features.shape
    if n < MIN_POINTS_PER_MODEL:
        return ModelResult(
            model_id,
            label,
            description,
            drawable,
            0.0,
            0.0,
            0.0,
            n,
            0,
            0.0,
            0.0,
            [CoefficientEstimate(name, 0.0, 0.0) for name in names],
        )
    train, test = _split(n, test_size, seed)
    intercept, coef = _fit(features[train], target[train])
    r2_train = _r_squared(features[train], target[train], intercept, coef)
    r2_test = _r_squared(features[test], target[test], intercept, coef)
    errors = _standard_errors(features[train], target[train], intercept, coef)
    return ModelResult(
        id=model_id,
        label=label,
        description=description,
        drawable=drawable,
        r2_train=r2_train,
        r2_test=r2_test,
        adj_r2=_adjusted_r2(r2_train, len(train), p),
        train_n=len(train),
        test_n=len(test),
        intercept=intercept,
        intercept_se=float(errors[0]),
        coefficients=[
            CoefficientEstimate(name, float(coef[i]), float(errors[i + 1]))
            for i, name in enumerate(names)
        ],
    )


def fit_all(points: Sequence[Mapping[str, Any]]) -> RegressionResult:
    """Clean the points and fit the five models. Fewer than three clean points: no models."""
    design, outcome, cleaning = clean_points(points)
    if design.shape[0] < MIN_POINTS_TO_FIT:
        return RegressionResult(cleaning, [])

    efficiency = design[:, [0]]
    models = [
        _fit_model(
            efficiency,
            outcome,
            model_id="simple",
            label="Simple",
            description="value ~ efficiency",
            drawable=True,
            names=["x"],
        ),
        _fit_model(
            np.log(np.maximum(efficiency, LOG_FLOOR)),
            np.log(np.maximum(outcome, LOG_FLOOR)),
            model_id="loglog",
            label="Log-log",
            description="ln(value) ~ ln(efficiency)",
            drawable=True,
            names=["ln(x)"],
        ),
        _fit_model(
            np.hstack([efficiency, efficiency**2]),
            outcome,
            model_id="poly2",
            label="Polynomial",
            description="value ~ efficiency + efficiency^2",
            drawable=True,
            names=["x", "x^2"],
        ),
        _fit_model(
            design[:, [1, 2]],
            outcome,
            model_id="volume",
            label="Volume",
            description="value ~ attempts + contacts",
            drawable=False,
            names=["attempts", "contacts"],
        ),
        _fit_model(
            design[:, [1, 2, 3, 4]],
            outcome,
            model_id="full",
            label="Full",
            description="value ~ attempts + contacts + cpc + conversion",
            drawable=False,
            names=["attempts", "contacts", "cpc", "conversion"],
        ),
    ]
    return RegressionResult(cleaning, models)


def to_dict(result: RegressionResult, digits: int = 6) -> dict[str, Any]:
    """JSON-ready form, with floats rounded to ``digits`` places."""
    return {
        "cleaning": {
            "raw_count": result.cleaning.raw_count,
            "clean_count": result.cleaning.clean_count,
            "removed_nulls": result.cleaning.removed_nulls,
            "removed_duplicates": result.cleaning.removed_duplicates,
            "removed_outliers": result.cleaning.removed_outliers,
        },
        "models": [
            {
                "id": m.id,
                "label": m.label,
                "description": m.description,
                "drawable": m.drawable,
                "r2_train": round(m.r2_train, digits),
                "r2_test": round(m.r2_test, digits),
                "adj_r2": round(m.adj_r2, digits),
                "train_n": m.train_n,
                "test_n": m.test_n,
                "intercept": round(m.intercept, digits),
                "intercept_se": round(m.intercept_se, digits),
                "coefficients": [
                    {"name": c.name, "value": round(c.value, digits), "se": round(c.se, digits)}
                    for c in m.coefficients
                ],
            }
            for m in result.models
        ],
    }
