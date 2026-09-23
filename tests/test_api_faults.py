#!/usr/bin/env python3
"""The server's safety net: a handler that raises, a client that hangs up.

These need the app IN PROCESS — a route is made to fail on demand by swapping
the engine function under it — so unlike every other suite this one runs under
the server's own environment. It re-execs itself there (building the venv on
first use, exactly as `scripts/serve.py` does), so it is still just:

    python3 tests/test_api_faults.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import serve  # noqa: E402  (stdlib-only: the launcher, not the app)

if not serve._in(serve.venv_dir()):
    py = serve.server_python()
    os.execv(str(py), [str(py), str(Path(__file__).resolve()), *sys.argv[1:]])

HOME = tempfile.mkdtemp(prefix="second-brain-faults-")
os.environ["SECOND_BRAIN_HOME"] = HOME
os.environ["SECOND_BRAIN_NO_AUTOSYNC"] = "1"

import uvicorn  # noqa: E402

import brain_query  # noqa: E402
import graph  # noqa: E402
from brain_api.app import create_app  # noqa: E402

FAILS: list[str] = []


def ck(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def status_of(url, data=None):
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def main():
    graph.connect().close()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        create_app(serve.DIST, serve.MISSING_UI), host="127.0.0.1", port=port,
        log_level="critical"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)

    print("\n== a handler that raises is a 500 with the reason ==")
    orig = brain_query.node_detail
    brain_query.node_detail = lambda _id: (_ for _ in ()).throw(
        RuntimeError("engine exploded"))
    try:
        code, body = status_of(f"{base}/api/node?id=x")
        ck("a read bug comes back as a 500 with the reason",
           code == 500 and "engine exploded" in body.get("error", ""), f"{code} {body}")
    finally:
        brain_query.node_detail = orig

    orig_link = graph.api_link
    graph.api_link = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("write exploded"))
    try:
        code, body = status_of(f"{base}/api/link", json.dumps(
            {"src": "a", "dst": "b", "rel": "relates-to"}).encode())
        ck("a write bug comes back as a 500", code == 500 and "write exploded" in
           body.get("error", ""), f"{code} {body}")
    finally:
        graph.api_link = orig_link

    code, body = status_of(f"{base}/api/meta")
    ck("and the server is still answering afterwards", code == 200 and body["ok"])

    print("\n== a client that hangs up is not an error ==")
    # The real-world case: the map polls, the person reloads, and the response
    # is written down a socket that just closed.
    for path in ("/api/graph", "/api/agenda", "/"):
        c = socket.create_connection(("127.0.0.1", port))
        c.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        time.sleep(0.03)
        c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        c.close()
    time.sleep(0.5)
    code, body = status_of(f"{base}/api/meta")
    ck("three aborted requests later the server is still answering",
       code == 200 and body["ok"])

    server.should_exit = True
    thread.join(timeout=10)
    shutil.rmtree(HOME, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
