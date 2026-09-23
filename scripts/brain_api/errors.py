"""Every failure leaves as the envelope: `{"ok": false, "error": "<reason>"}`.

The page shows `error` to a person (frontend/src/api/client.ts), so FastAPI's
own shapes — `{"detail": ...}`, a 422 with a list of locations — would reach the
screen as "request failed (422)". These handlers put each of them back into the
one shape a client can rely on.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

NO_STORE = {"Cache-Control": "no-store"}


class Refused(Exception):
    """A refusal raised from a dependency, where a route cannot return one."""

    def __init__(self, status: int, error: str):
        self.status, self.error = status, error


def _validation_reason(exc: RequestValidationError) -> str:
    """One sentence out of pydantic's error list: the first field, and why."""
    errors = exc.errors()
    if not errors:
        return "the request was not understood"
    first = errors[0]
    if first.get("type") == "json_invalid":
        return "the body is not valid JSON"
    loc = [str(p) for p in first.get("loc", ()) if p not in ("body", "query")]
    where = ".".join(loc)
    if not where:
        return "the body must be a JSON object"
    return f"{where}: {first.get('msg', 'invalid')}"


def install(app: FastAPI) -> None:
    @app.exception_handler(Refused)
    async def _refused(_req: Request, exc: Refused):
        return JSONResponse({"ok": False, "error": exc.error}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_req: Request, exc: RequestValidationError):
        return JSONResponse({"ok": False, "error": _validation_reason(exc)},
                            status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(req: Request, exc: StarletteHTTPException):
        # A method a route does not take is, to a client, a route that is not
        # there — the old server answered 404 and the page is written for that.
        code = 404 if exc.status_code == 405 else exc.status_code
        error = "not found" if code == 404 else str(exc.detail)
        return JSONResponse({"ok": False, "error": error}, status_code=code)

    @app.exception_handler(Exception)
    async def _crash(_req: Request, exc: Exception):
        # Keep the server alive, and tell the client what broke.
        return JSONResponse({"error": str(exc)}, status_code=500, headers=NO_STORE)
