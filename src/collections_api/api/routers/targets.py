"""Quarterly targets: serve the latest document, accept a new one as a PDF."""

import os
from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile

from collections_api.api.common import ERROR_RESPONSES, ok_response, respond
from collections_api.api.context import AppContext
from collections_api.api.dependencies import Ctx
from collections_api.api.models import TargetRow, TargetUploadResult
from collections_api.core.errors import ApiError
from collections_api.domains.targets import extractor

router = APIRouter(prefix="/dashboard", tags=["targets"], responses=ERROR_RESPONSES)

UPLOAD_CHUNK_BYTES = 1024 * 1024
NO_TARGETS = "No targets document has been uploaded yet."


@router.get(
    "/targets",
    operation_id="targets",
    summary="Latest targets",
    response_model=None,
    responses=ok_response(TargetRow),
)
def targets(ctx: Ctx, request: Request):
    """The targets of the latest uploaded document, one row per portfolio.

    Before any upload the answer is 200 with an empty `data` list and one entry in
    `errors`, so a page can show an empty state without treating it as a failure.
    """
    document = ctx.targets.latest()
    if document is None:
        return respond(ctx, request, [], sources=[], errors=[{"message": NO_TARGETS}])
    meta = document["meta"]
    return respond(
        ctx,
        request,
        document["rows"],
        sources=[meta.get("source_file", "")],
        filters={"period": meta.get("period")},
        quality={
            "validated": meta.get("validated", False),
            "months": meta.get("months", []),
            "extracted_at": meta.get("extracted_at"),
        },
    )


def _check_origin(ctx: AppContext, request: Request) -> None:
    """Reject a browser request from an origin that is not on the CORS list.

    A multipart form post does not trigger a CORS preflight, so a page on another site
    could submit one with the user's cached credentials. Requests with no ``Origin``
    header (scripts, curl) are not browser form posts and pass.
    """
    origin = (request.headers.get("origin") or "").strip()
    allowed = ctx.settings.cors_allow_origins
    if origin and "*" not in allowed and origin not in allowed:
        raise ApiError(403, "This origin may not upload targets.")


def _save_upload(ctx: AppContext, upload: UploadFile, destination: str) -> None:
    """Copy the upload to ``destination`` in chunks, stopping at the size ceiling."""
    limit = ctx.settings.targets_upload_max_bytes
    written = 0
    with open(destination, "wb") as target:
        while chunk := upload.file.read(UPLOAD_CHUNK_BYTES):
            written += len(chunk)
            if written > limit:
                raise ApiError(413, f"The file is larger than {limit} bytes.")
            target.write(chunk)


@router.post(
    "/targets/upload",
    operation_id="targets_upload",
    summary="Upload a targets PDF",
    response_model=None,
    responses={
        **ok_response(TargetUploadResult, many=False),
        403: ERROR_RESPONSES[401] | {"description": "The request origin is not allowed."},
        413: ERROR_RESPONSES[400] | {"description": "The upload is larger than the limit."},
    },
)
def targets_upload(
    ctx: Ctx,
    request: Request,
    file: Annotated[UploadFile, File(description="The targets PDF (`multipart/form-data`).")],
):
    """Extract the table from a PDF and make it the latest targets document.

    The extraction is checked twice: each row's total must equal cash plus recovery
    value, and the rows must add up to the document's TOTAL row. If either check fails
    nothing is stored and the response is 422 with the reason. The size limit is
    `TARGETS_UPLOAD_MAX_BYTES`. Requires the `pdf` extra on the server.
    """
    _check_origin(ctx, request)
    name = os.path.basename(file.filename or "")
    if not name.lower().endswith(".pdf"):
        raise ApiError(400, "Upload a PDF file.")
    scratch = ctx.targets.scratch_path()
    try:
        _save_upload(ctx, file, str(scratch))
        layout = extractor.Layout(group_names=ctx.settings.target_group_names)
        try:
            document = extractor.extract_pdf(str(scratch), source_name=name, layout=layout)
        except extractor.ExtractionError as exc:
            raise ApiError(422, str(exc)) from exc
        period = ctx.targets.save(document)
    finally:
        scratch.unlink(missing_ok=True)
    return respond(
        ctx,
        request,
        {"period": period, "row_count": document["meta"]["row_count"]},
        sources=[name],
    )
