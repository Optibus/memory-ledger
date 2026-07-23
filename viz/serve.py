#!/usr/bin/env python3
"""work-brain graph visualizer — a tiny, dependency-free local web server.

Reads the same brain the CLI uses (~/.work_brain/graph.db, or $WORK_BRAIN_DB /
$WORK_BRAIN_HOME) and serves an interactive force-directed graph you can zoom,
pan, filter, search, and click through. The page polls the API so edits made by
any skill show up live.

    python3 viz/serve.py [--port 8770] [--no-open]
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import graph  # noqa: E402  (the engine — single source of truth for the DB)

INDEX = HERE / "index.html"


def graph_payload() -> dict:
    con = graph.connect()
    node_rows = con.execute("SELECT * FROM nodes").fetchall()
    edge_rows = con.execute("SELECT src, dst, rel FROM edges").fetchall()

    degree: dict[str, int] = {}
    for e in edge_rows:
        degree[e["src"]] = degree.get(e["src"], 0) + 1
        degree[e["dst"]] = degree.get(e["dst"], 0) + 1

    nodes = []
    for r in node_rows:
        d = graph._node_dict(r)
        nodes.append({
            "id": d["id"], "type": d["type"], "title": d["title"],
            "status": d["status"], "due": d["due"], "body": d["body"],
            "props": d["props"], "degree": degree.get(d["id"], 0),
        })
    edges = [{"src": e["src"], "dst": e["dst"], "rel": e["rel"]} for e in edge_rows]

    by_type: dict[str, int] = {}
    for n in nodes:
        by_type[n["type"]] = by_type.get(n["type"], 0) + 1

    return {
        "nodes": nodes, "edges": edges,
        "meta": {"nodes": len(nodes), "edges": len(edges),
                 "by_type": by_type, "generated": graph.now(),
                 "db": str(graph.db_path())},
        # cheap change token so the client only re-lays-out when something moved
        "version": f"{len(nodes)}-{len(edges)}-{max((n['props'].get('updated_at','') for n in nodes), default='')}",
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/", "/index.html"):
                self._send(200, INDEX.read_text(encoding="utf-8"),
                           "text/html; charset=utf-8")
            elif path == "/api/graph":
                self._send(200, json.dumps(graph_payload()),
                           "application/json")
            else:
                self._send(404, json.dumps({"error": "not found"}),
                           "application/json")
        except Exception as e:  # keep the server alive, report to client
            self._send(500, json.dumps({"error": str(e)}), "application/json")

    def log_message(self, *_):  # quiet
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    graph.connect().close()  # ensure the brain exists
    url = f"http://{args.host}:{args.port}/"
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"work-brain map → {url}")
    print(f"  brain: {graph.db_path()}")
    print("  Ctrl-C to stop.")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
