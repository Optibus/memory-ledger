"""The server's safety net: a handler that raises, a client that hangs up.

    python -m pytest tests/test_api_faults.py

These need the app IN PROCESS: a route is made to fail on demand by swapping
the engine function under it. The app's dependencies (fastapi, uvicorn) live in
the server's own venv, which `scripts/serve.py` builds on first use; this suite
builds it the same way and puts its site-packages on the path, so it is one
ordinary pytest run (the venv is made by the same interpreter, so the compiled
wheels match).
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import serve  # stdlib-only: the launcher, not the app

LOCALHOST = "127.0.0.1"
META = "/api/meta"
REQUEST_TIMEOUT_S = 5
SERVER_UP_TIMEOUT_S = 5
DRAIN_S = 0.5
HANG_UP_PATHS = ("/api/graph", "/api/agenda", "/")
ENGINE_FAILURE = "engine exploded"
WRITE_FAILURE = "write exploded"


def _server_site_packages() -> list[Path]:
    """The site-packages of the server's venv, built if it is not there yet."""
    try:
        serve.server_python()
    except SystemExit as e:
        pytest.fail(f"the server's environment could not be built: {e}")
    venv = serve.venv_dir()
    return [p for p in (*venv.glob("lib/python*/site-packages"), venv / "Lib" / "site-packages")
            if p.is_dir()]


def _ensure_server_env() -> None:
    try:
        import uvicorn  # noqa: F401, PLC0415
        import fastapi  # noqa: F401, PLC0415
        return
    except ImportError:
        pass
    for p in _server_site_packages():
        sys.path.append(str(p))


class App:
    """The real app, served in process on a free port."""

    def __init__(self, port: int, server) -> None:
        self.port, self.server = port, server
        self.base = f"http://{LOCALHOST}:{port}"

    def status_of(self, path: str, data: bytes | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


@pytest.fixture
def app(module_brain_home: Path) -> Iterator[App]:
    _ensure_server_env()
    import uvicorn  # noqa: PLC0415

    import graph  # noqa: PLC0415
    from brain_api.app import create_app  # noqa: PLC0415

    graph.connect().close()
    with socket.socket() as s:
        s.bind((LOCALHOST, 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        create_app(serve.DIST, serve.MISSING_UI), host=LOCALHOST, port=port,
        log_level="critical"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + SERVER_UP_TIMEOUT_S
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "the in-process server never started"
    yield App(port, server)
    server.should_exit = True
    thread.join(timeout=10)


def raiser(message: str) -> Callable[..., None]:
    def boom(*_a, **_k):
        raise RuntimeError(message)
    return boom


def test_a_read_bug_comes_back_as_a_500_with_the_reason(
        app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    import brain_query  # noqa: PLC0415
    monkeypatch.setattr(brain_query, "node_detail", raiser(ENGINE_FAILURE))
    code, body = app.status_of("/api/node?id=x")
    assert code == 500, (code, body)
    assert ENGINE_FAILURE in body.get("error", ""), body


def test_a_write_bug_comes_back_as_a_500(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    import graph  # noqa: PLC0415
    monkeypatch.setattr(graph, "api_link", raiser(WRITE_FAILURE))
    code, body = app.status_of("/api/link", json.dumps(
        {"src": "a", "dst": "b", "rel": "relates-to"}).encode())
    assert code == 500, (code, body)
    assert WRITE_FAILURE in body.get("error", ""), body


def test_the_server_still_answers_after_a_handler_raised(
        app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    import brain_query  # noqa: PLC0415
    monkeypatch.setattr(brain_query, "node_detail", raiser(ENGINE_FAILURE))
    assert app.status_of("/api/node?id=x")[0] == 500
    monkeypatch.undo()
    code, body = app.status_of(META)
    assert code == 200
    assert body["ok"]


def test_aborted_requests_are_not_an_error(app: App) -> None:
    """The real-world case: the map polls, the person reloads, and the response
    is written down a socket that just closed."""
    for path in HANG_UP_PATHS:
        c = socket.create_connection((LOCALHOST, app.port))
        c.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        time.sleep(0.03)
        c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        c.close()
    time.sleep(DRAIN_S)
    code, body = app.status_of(META)
    assert code == 200
    assert body["ok"]
