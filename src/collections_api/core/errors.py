"""Errors that carry an HTTP status. Raised anywhere below the API layer."""

from __future__ import annotations


class ApiError(Exception):
    """A request failed in a way the client should see.

    The API layer turns it into the error envelope (see ``api/errors.py``). Core code
    raises it instead of FastAPI's ``HTTPException`` so that nothing below ``api/``
    imports the web framework.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
