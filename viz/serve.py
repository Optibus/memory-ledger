#!/usr/bin/env python3
"""second-brain graph visualizer — a tiny, dependency-free local web server.

Reads the same brain the CLI uses (~/.second_brain/graph.db, or $SECOND_BRAIN_DB /
$SECOND_BRAIN_HOME) and serves an interactive force-directed graph you can zoom,
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
import brain_query  # noqa: E402  (the off-token query layer — powers the dashboard tab)

INDEX = HERE / "index.html"


def _sqlite_payload() -> dict:
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
                 "source": "sqlite (local)", "db": str(graph.db_path())},
        # cheap change token so the client only re-lays-out when something moved
        "version": f"sqlite-{len(nodes)}-{len(edges)}-{max((n['props'].get('updated_at','') for n in nodes), default='')}",
    }


def _neo4j_payload():
    """Fetch the graph from the neo4j master. Returns None if not configured /
    unreachable, so the caller can fall back to the local SQLite mirror."""
    import base64
    import urllib.request
    cfg = graph._neo4j_cfg()
    if not (cfg["url"] and cfg["password"]):
        return None
    endpoint = cfg["url"].rstrip("/") + f"/db/{cfg['db']}/tx/commit"
    auth = base64.b64encode(f"{cfg['user']}:{cfg['password']}".encode()).decode()
    body = json.dumps({"statements": [
        {"statement": "MATCH (n:Node) RETURN n"},
        {"statement": "MATCH (a:Node)-[r:REL]->(b:Node) RETURN a.id, b.id, r.rel"},
    ]}).encode()
    req = urllib.request.Request(endpoint, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        if data.get("errors"):
            return None
        node_rows = data["results"][0]["data"]
        edge_rows = data["results"][1]["data"]
    except Exception:
        return None

    CORE = {"id", "type", "title", "body", "status", "due", "created", "updated"}
    degree = {}
    edges = []
    for d in edge_rows:
        s, dst, rel = d["row"]
        edges.append({"src": s, "dst": dst, "rel": rel})
        degree[s] = degree.get(s, 0) + 1
        degree[dst] = degree.get(dst, 0) + 1
    nodes, by_type, by_realm = [], {}, {}
    for d in node_rows:
        p = d["row"][0] or {}
        nid = p.get("id")
        realm = p.get("realm", "")
        props = {k: v for k, v in p.items() if k not in CORE}
        nodes.append({"id": nid, "type": p.get("type", ""), "title": p.get("title", ""),
                      "status": p.get("status", ""), "due": p.get("due", ""),
                      "body": p.get("body", ""), "props": props,
                      "degree": degree.get(nid, 0)})
        by_type[p.get("type", "")] = by_type.get(p.get("type", ""), 0) + 1
        by_realm[realm] = by_realm.get(realm, 0) + 1
    return {
        "nodes": nodes, "edges": edges,
        "meta": {"nodes": len(nodes), "edges": len(edges), "by_type": by_type,
                 "by_realm": by_realm, "generated": graph.now(),
                 "source": f"neo4j ({cfg['url']})"},
        "version": f"neo4j-{len(nodes)}-{len(edges)}",
    }


def graph_payload() -> dict:
    """Prefer the neo4j master; fall back to the local SQLite mirror if neo4j is
    unreachable so the map still works offline."""
    return _neo4j_payload() or _sqlite_payload()


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
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                self._send(200, INDEX.read_text(encoding="utf-8"),
                           "text/html; charset=utf-8")
            elif path == "/api/graph":
                self._send(200, json.dumps(graph_payload()),
                           "application/json")
            elif path == "/api/views":
                self._send(200, json.dumps({"ok": True, "views": brain_query.VIEWS}),
                           "application/json")
            elif path == "/api/assignees":
                self._send(200, json.dumps({"ok": True,
                           "assignees": brain_query.assignees("auto")}), "application/json")
            elif path == "/api/node":   # full node + edges, for the expand view
                nid = (parse_qs(parsed.query).get("id") or [""])[0]
                self._send(200, json.dumps(brain_query.node_detail(nid)),
                           "application/json")
            elif path.startswith("/api/"):   # dashboard views: /api/<view>?params
                self._dashboard(path[len("/api/"):], parse_qs(parsed.query))
            else:
                self._send(404, json.dumps({"error": "not found"}),
                           "application/json")
        except Exception as e:  # keep the server alive, report to client
            self._send(500, json.dumps({"error": str(e)}), "application/json")

    # the off-token dashboard views, reusing the exact CLI query layer
    def _dashboard(self, view, qs):
        if view not in brain_query.VIEWS:
            self._send(404, json.dumps({"ok": False, "error": f"unknown view: {view}"}),
                       "application/json")
            return
        alias = {"from": "date_from", "to": "date_to"}
        allowed = {"date_from", "date_to", "status", "since_days", "tz", "backend",
                   "mine", "assignee"}
        # default to 'auto' so the dashboard reflects the SAME store as the map
        # (neo4j master when reachable, local sqlite mirror when offline)
        opts = {"backend": "auto"}
        for k, v in qs.items():
            k = alias.get(k, k)
            val = v[0] if v else ""
            if k not in allowed or val == "":
                continue
            if k == "since_days" and val.isdigit():
                opts[k] = int(val)
            elif k == "mine":
                opts[k] = val not in ("0", "false", "no")
            else:
                opts[k] = val
        try:
            self._send(200, json.dumps(brain_query.run_json(view, **opts)),
                       "application/json")
        except Exception as e:
            self._send(400, json.dumps({"ok": False, "error": str(e)}),
                       "application/json")

    # --- writes: set a task's due date from the web UI (token-free) ---
    def do_POST(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        try:
            if path == "/api/set-due":
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    data = json.loads(self.rfile.read(n) or b"{}")
                except Exception:
                    data = {}
                res = self._set_due(data.get("id", ""), data.get("due", ""))
                self._send(200 if res.get("ok") else 400,
                           json.dumps(res), "application/json")
            else:
                self._send(404, json.dumps({"ok": False, "error": "not found"}),
                           "application/json")
        except Exception as e:
            self._send(500, json.dumps({"ok": False, "error": str(e)}),
                       "application/json")

    def _set_due(self, node_id, due):
        if not node_id:
            return {"ok": False, "error": "missing id"}
        con = graph.connect()
        if not con.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone():
            return {"ok": False, "error": f"no node {node_id}"}
        con.execute("UPDATE nodes SET due=?, updated=? WHERE id=?",
                    (due or "", graph.now(), node_id))
        con.commit()
        try:
            graph._after_write(con, nodes=[node_id])   # mirror + sync to neo4j
        except Exception:
            pass
        return {"ok": True, "id": node_id, "due": due or ""}

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
    print(f"second-brain map → {url}")
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
