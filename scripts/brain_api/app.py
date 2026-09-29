"""The FastAPI app: the JSON API under /api, the built SPA everywhere else.

The OpenAPI document is generated from the routes at runtime and served at
`/api/openapi.json`, with Swagger UI at `/api/docs`. There is no checked-in copy
to regenerate: the document IS the router, so it cannot describe a server other
than the one answering.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import errors, reads, state, sync_runtime, trigger_runtime, writes

TAGS = [
    {"name": "graph", "description": "Nodes, edges, and what connects to what."},
    {"name": "dashboard", "description": "The token-free date views."},
    {"name": "tasks", "description": "Creating and moving work."},
    {"name": "people", "description": "Person nodes and assignment."},
    {"name": "contracts", "description": "Skills, rules and processes — the two trees, "
                                         "the realm-scoped processes, and the file and "
                                         "bundle routes between machines."},
    {"name": "scripts", "description": "The person's own automation in <home>/scripts: "
                                       "listed, read, exported as a zip and imported "
                                       "back. Never executed."},
    {"name": "vocabulary", "description": "The open and closed sets a client must read "
                                          "rather than hard-code."},
    {"name": "settings", "description": "Preferences, store health, and upkeep."},
    {"name": "usage", "description": "The usage report over the `usage-week` nodes, "
                                     "rendered as one HTML page with no model call."},
    {"name": "trigger", "description": "Slack, Jira and GitHub events that summon the brain: "
                                       "the request log, its actions, and the "
                                       "per-conversation gate (docs/trigger.md)."},
]


@asynccontextmanager
async def lifespan(_app):
    """The trigger watchers and the master sync live as long as the server
    does, and only when serve.py asked for them."""
    if state.TRIGGERS:
        trigger_runtime.start()
    if state.SYNC:
        sync_runtime.start()
    try:
        yield
    finally:
        if state.SYNC:
            sync_runtime.stop()
        if state.TRIGGERS:
            trigger_runtime.stop()

DESCRIPTION = """\
The local HTTP API over one person's memory graph.

**It calls no model.** Every endpoint here is the graph engine behind a socket:
plain SQL through `graph.py`'s `api_*` helpers, so everything it does is
token-free. Leaving a client polling it costs nothing.

## Security — read this before changing `--host`

**The write API is unauthenticated.** There is no token, no session, no CSRF
protection. Anything that can reach the port can rewrite the graph.

- The default bind is `127.0.0.1`. Keep it there.
- If you must expose it (`--host 0.0.0.0` for phone access), pass `--no-write`:
  every `POST` then returns 403 and `/api/meta` reports `writable: false`, which
  is what makes the UI hide its editing controls.

## Conventions

- Every response is JSON with an `ok` boolean; failures carry `error`, and the
  error is the reason worth showing a person rather than a status code.
- String inputs are length-capped server-side. Over-long values are truncated,
  not rejected. A value of the wrong type is refused (400).
- Open vocabulary (`realms`, `kinds`, node `types`) is read from `/api/meta`,
  never shipped in a client. Closed sets (`rels`, `statuses`) are enforced here.
"""

# The only file types the SPA build emits. An explicit map beats mimetypes.guess,
# which is configured per machine and has been known to answer text/plain for .js.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".map": "application/json",
}


class NoStore:
    """Every response is `Cache-Control: no-store`. The map polls, and a cached
    poll is a frozen map; a cached index.html is a page older than its API.

    Plain ASGI rather than BaseHTTPMiddleware, which buffers streaming bodies."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() != b"cache-control"]
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_header)


def create_app(dist: Path, missing_ui: str) -> FastAPI:
    """The app, serving the SPA build in `dist`.

    `missing_ui` is the page shown when `dist` holds no index.html; it has a
    `{reason}` slot, filled from `state.UI_PROBLEM`."""
    app = FastAPI(title="memory-ledger", version="2.0.0", description=DESCRIPTION,
                  openapi_tags=TAGS, docs_url="/api/docs", openapi_url="/api/openapi.json",
                  redoc_url=None, lifespan=lifespan,
                  # Relative, deliberately: "the server" is whatever origin the
                  # document was fetched from, not a fixed port.
                  servers=[{"url": "/", "description": "This server."}])
    errors.install(app)
    app.add_middleware(NoStore)
    app.include_router(reads.router)
    app.include_router(writes.router)

    @app.post("/{path:path}", include_in_schema=False)
    def unknown_post(path: str):
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)

    index = dist / "index.html"
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request):
        """The built SPA, falling back to its index for any unknown path.

        The app routes on the hash, so the fallback is belt and braces: a stray
        path still lands on the app instead of a bare 404. A path under /api/ is
        NOT the app — a client asking for an endpoint that does not exist must
        hear so, not be handed HTML."""
        if path.startswith("api/"):
            view = path[len("api/"):]
            return JSONResponse({"ok": False, "error": f"unknown view: {view}"},
                                status_code=404)
        if not index.exists():
            reason = state.UI_PROBLEM or "frontend/dist/index.html is not here"
            return HTMLResponse(missing_ui.format(reason=reason), status_code=503)
        target = index
        if path:
            # resolve() then containment-check, so `../../etc/passwd` and any
            # symlink pointing out of the build cannot be read through this.
            candidate = (dist / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                target = candidate
        return FileResponse(target, media_type=CONTENT_TYPES.get(
            target.suffix, "application/octet-stream"))

    return app
