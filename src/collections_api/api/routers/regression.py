"""Regression of agent results on their effort."""

from fastapi import APIRouter, Request

from collections_api.api.common import ERROR_RESPONSES, ok_response, respond
from collections_api.api.dependencies import Ctx
from collections_api.api.models import RegressionData, RegressionRequest
from collections_api.core.errors import ApiError
from collections_api.domains.regression import models

router = APIRouter(prefix="/regression", tags=["regression"], responses=ERROR_RESPONSES)


@router.post(
    "/agents",
    operation_id="regression_agents",
    summary="Fit regression models on agent points",
    response_model=None,
    responses={
        **ok_response(RegressionData, many=False),
        413: ERROR_RESPONSES[400] | {"description": "Too many points or a body above the limit."},
    },
)
def regression_agents(ctx: Ctx, request: Request, body: RegressionRequest):
    """Clean the points and fit five least-squares models.

    Points with a missing field, repeated ids and interquartile-range outliers are
    dropped and counted in `cleaning`. Models are fitted on a seeded 70/30 split, so the
    same input always gives the same output. Fewer than three clean points gives an
    empty `models` list. At most `REGRESSION_MAX_POINTS` points are accepted.

    The handler is synchronous on purpose: the fit is CPU work, and FastAPI runs a
    plain `def` route in its thread pool instead of blocking the event loop.
    """
    limit = ctx.settings.regression_max_points
    if len(body.points) > limit:
        raise ApiError(413, f"At most {limit} points are accepted.")
    result = models.fit_all([point.model_dump() for point in body.points])
    return respond(
        ctx,
        request,
        models.to_dict(result),
        sources=["request"],
        filters={"point_count": len(body.points)},
    )
