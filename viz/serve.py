#!/usr/bin/env python3
"""second-brain graph visualizer — a tiny, dependency-free local web server.

Reads the same brain the CLI uses (~/.second_brain/graph.db, or $SECOND_BRAIN_DB /
$SECOND_BRAIN_HOME) and serves an interactive force-directed graph you can zoom,
pan, filter, search, and click through. The page polls the API so edits made by
any skill show up live.

    python3 viz/serve.py [--port 8770] [--no-open] [--no-reload]

It restarts itself when its own code changes, so a long-running server never
serves a page newer than the API behind it. See RELOAD below.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import graph  # noqa: E402  (the engine — single source of truth for the DB)
import brain_query  # noqa: E402  (the off-token query layer — powers the dashboard tab)

INDEX = HERE / "index.html"

# Writes (POST /api/*) are enabled unless started with --no-write. The write API
# is unauthenticated, so bind to 127.0.0.1 or pass --no-write when exposing it.
WRITABLE = True

# Identifies THIS process. The page polls it and reloads itself when it changes,
# so a restart carries the new page to an already-open tab.
BOOT = f"{os.getpid()}-{int(time.time())}"

# ---------------------------------------------------------------------------
# RELOAD
#
# index.html is read from disk on every request, but this module and the engine
# are loaded once at startup. A server left running for hours therefore serves a
# CURRENT page against a STALE API — the page asks for a filter the process has
# never heard of and silently shows nothing. That is a confusing failure, and the
# fix is for the process to notice its own code changed and re-exec.
#
# Polling mtimes beats a filesystem-watch dependency: four files, once a second.
# ---------------------------------------------------------------------------
_RESTART = threading.Event()


def _watched() -> list[Path]:
    """This module, the engine modules it serves, and the page."""
    out = [Path(__file__).resolve(), INDEX]
    for mod in (graph, brain_query):
        f = getattr(mod, "__file__", None)
        if f:
            out.append(Path(f).resolve())
    return out


def _stamp(paths) -> dict:
    out = {}
    for p in paths:
        try:
            out[str(p)] = p.stat().st_mtime_ns
        except OSError:
            out[str(p)] = None
    return out


def _watch(httpd, interval=1.0) -> None:
    """Re-exec when a watched file changes. Refuses to restart into code that
    does not compile — a typo mid-edit should not take the server down, and the
    error is printed where the developer is already looking."""
    paths = _watched()
    seen = _stamp(paths)
    while not _RESTART.is_set():
        time.sleep(interval)
        now = _stamp(paths)
        changed = [p for p in now if now[p] != seen.get(p)]
        if not changed:
            continue
        seen = now
        broken = []
        for c in changed:
            if c.endswith(".py"):
                try:
                    compile(Path(c).read_text(encoding="utf-8"), c, "exec")
                except SyntaxError as e:
                    broken.append(f"{Path(c).name}:{e.lineno}: {e.msg}")
                except OSError:
                    pass
        if broken:
            print("  reload held — does not compile:", flush=True)
            for b in broken:
                print(f"    {b}", flush=True)
            continue
        print(f"  changed: {', '.join(Path(c).name for c in changed)} — restarting",
              flush=True)
        _RESTART.set()
        # Unblock serve_forever from this thread; the exec happens in main(),
        # after the listening socket is closed, so the port is free to rebind.
        threading.Thread(target=httpd.shutdown, daemon=True).start()
        return


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
            "realm": brain_query.norm_realm(d.get("realm", "")),
            "kind": brain_query.norm_kind(d["props"]),
            "props": d["props"], "degree": degree.get(d["id"], 0),
        })
    edges = [{"src": e["src"], "dst": e["dst"], "rel": e["rel"]} for e in edge_rows]

    by_type: dict[str, int] = {}
    by_realm: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for n in nodes:
        by_type[n["type"]] = by_type.get(n["type"], 0) + 1
        by_realm[n["realm"]] = by_realm.get(n["realm"], 0) + 1
        by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1

    return {
        "nodes": nodes, "edges": edges,
        "meta": {"nodes": len(nodes), "edges": len(edges),
                 "by_type": by_type, "by_realm": by_realm, "by_kind": by_kind,
                 "boot": BOOT, "generated": graph.now(),
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
    nodes, by_type, by_realm, by_kind = [], {}, {}, {}
    for d in node_rows:
        p = d["row"][0] or {}
        nid = p.get("id")
        realm = brain_query.norm_realm(p.get("realm", ""))
        props = {k: v for k, v in p.items() if k not in CORE}
        kind = brain_query.norm_kind(props)
        nodes.append({"id": nid, "type": p.get("type", ""), "title": p.get("title", ""),
                      "status": p.get("status", ""), "due": p.get("due", ""),
                      "body": p.get("body", ""), "realm": realm, "kind": kind,
                      "props": props, "degree": degree.get(nid, 0)})
        by_type[p.get("type", "")] = by_type.get(p.get("type", ""), 0) + 1
        by_realm[realm] = by_realm.get(realm, 0) + 1
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "nodes": nodes, "edges": edges,
        "meta": {"nodes": len(nodes), "edges": len(edges), "by_type": by_type,
                 "by_realm": by_realm, "by_kind": by_kind, "boot": BOOT,
                 "generated": graph.now(),
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
            elif path == "/api/realms":  # open vocabulary — the UI reads, never ships a list
                self._send(200, json.dumps({
                    "ok": True, "realms": brain_query.realms("auto"),
                    "default": graph.DEFAULT_REALM}), "application/json")
            elif path == "/api/node":   # full node + edges, for the expand view
                nid = (parse_qs(parsed.query).get("id") or [""])[0]
                self._send(200, json.dumps(brain_query.node_detail(nid)),
                           "application/json")
            elif path == "/api/kinds":   # props.kind — open vocabulary, same as realms
                self._send(200, json.dumps({
                    "ok": True, "kinds": brain_query.kinds("auto"),
                    "none_token": brain_query.NO_KIND}), "application/json")
            elif path == "/api/meta":   # vocabulary, so the UI never hardcodes it
                # rels and statuses are closed sets the engine enforces; realms and
                # types are OPEN — read from the brain, because they differ per person.
                self._send(200, json.dumps({
                    "ok": True, "rels": graph.EDGE_RELS,
                    "statuses": graph.TASK_STATUSES,
                    "realms": brain_query.realms("auto"),
                    "default_realm": graph.DEFAULT_REALM,
                    "kinds": brain_query.kinds("auto"),
                    "no_kind_token": brain_query.NO_KIND,
                    "types": brain_query.node_types("auto"),
                    "writable": WRITABLE, "boot": BOOT,
                }), "application/json")
            elif path == "/api/suggest-links":
                q = parse_qs(parsed.query)
                text = (q.get("text") or [""])[0]
                exclude = (q.get("exclude") or [""])[0]
                con = graph.connect()
                res = graph.suggest_links(con, text, 8, exclude)
                self._send(200, json.dumps({"ok": True, "count": len(res),
                                            "suggestions": res}), "application/json")
            elif path == "/api/search":  # node picker for the relation editor
                q = parse_qs(parsed.query)
                term = (q.get("q") or [""])[0].strip()
                if len(term) < 2:
                    self._send(200, json.dumps({"ok": True, "results": []}),
                               "application/json")
                else:
                    self._send(200, json.dumps(self._search(term, q)),
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
                   "mine", "assignee", "realm", "kind"}
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

    def _search(self, term, q):
        limit = 12
        raw = (q.get("limit") or [""])[0]
        if raw.isdigit():
            limit = max(1, min(30, int(raw)))
        con = graph.connect()
        like = f"%{term.lower()}%"
        rows = con.execute(
            "SELECT id,type,title,status FROM nodes"
            " WHERE lower(title) LIKE ? ORDER BY length(title) LIMIT ?",
            (like, limit)).fetchall()
        return {"ok": True, "results": [dict(r) for r in rows]}

    # ---------------------------------------------------------------- writes
    # Token-free CRUD from the browser: every route below is plain SQL through
    # the engine's api_* helpers, so the UI needs no model to feed the brain.
    # Vocabulary/closed sets are enforced HERE (the engine keeps them soft) —
    # this is the untrusted boundary.
    def do_POST(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        routes = {
            "/api/set-due": self._set_due,
            "/api/add-task": self._add_task,
            "/api/complete": self._complete,
            "/api/set-status": self._set_status,
            "/api/set-assignee": self._set_assignee,
            "/api/update-node": self._update_node,
            "/api/link": self._link,
            "/api/unlink": self._unlink,
        }
        handler = routes.get(path)
        try:
            if handler is None:
                self._send(404, json.dumps({"ok": False, "error": "not found"}),
                           "application/json")
                return
            if not WRITABLE:
                self._send(403, json.dumps({
                    "ok": False,
                    "error": "server started with --no-write; writes are disabled"}),
                    "application/json")
                return
            n = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            res = handler(data)
            self._send(200 if res.get("ok") else 400,
                       json.dumps(res), "application/json")
        except Exception as e:
            self._send(500, json.dumps({"ok": False, "error": str(e)}),
                       "application/json")

    @staticmethod
    def _str(data, key, limit=4000):
        v = data.get(key)
        return v.strip()[:limit] if isinstance(v, str) else ""

    def _set_due(self, data):
        nid = self._str(data, "id", 200)
        if not nid:
            return {"ok": False, "error": "missing id"}
        res = graph.api_update_node(graph.connect(), nid,
                                    due=self._str(data, "due", 40))
        return ({"ok": True, "id": nid, "due": res["node"]["due"]}
                if res.get("ok") else res)

    def _add_task(self, data):
        title = self._str(data, "title", 300)
        if not title:
            return {"ok": False, "error": "missing title"}
        ntype = self._str(data, "type", 20) or "task"
        if ntype not in ("task", "subtask"):
            return {"ok": False, "error": "type must be task or subtask"}
        props = {"source": "web-ui"}
        for key in ("assignee", "priority"):
            val = self._str(data, key, 60)
            if val:
                props[key] = val
        con = graph.connect()
        res = graph.api_create_node(
            con, ntype, title, self._str(data, "body"), props,
            status="open", due=self._str(data, "due", 40),
            realm=self._str(data, "realm", 40))
        if not res.get("ok"):
            return res
        nid = res["node"]["id"]
        # apply whichever suggested/edited links the user kept
        links, rejected = [], []
        for item in (data.get("links") or [])[:20]:
            if not isinstance(item, dict):
                continue
            dst, rel = str(item.get("id", ""))[:200], str(item.get("rel", ""))[:40]
            if rel not in graph.EDGE_RELS:
                rejected.append({"id": dst, "rel": rel, "error": "unknown relation"})
                continue
            r = graph.api_link(con, nid, dst, rel)
            (links if r.get("ok") else rejected).append(
                {"id": dst, "rel": rel, **({} if r.get("ok") else {"error": r.get("error")})})
        res["links"] = links
        if rejected:
            res["rejected"] = rejected
        return res

    def _complete(self, data):
        nid = self._str(data, "id", 200)
        if not nid:
            return {"ok": False, "error": "missing id"}
        return graph.api_complete(graph.connect(), nid, self._str(data, "note", 500))

    def _set_status(self, data):
        nid = self._str(data, "id", 200)
        status = self._str(data, "status", 20)
        if not nid:
            return {"ok": False, "error": "missing id"}
        if status not in graph.TASK_STATUSES:
            return {"ok": False,
                    "error": f"status must be one of {', '.join(graph.TASK_STATUSES)}"}
        waiting = data.get("waiting_on")
        return graph.api_set_status(
            graph.connect(), nid, status,
            waiting_on=(self._str(data, "waiting_on", 200)
                        if isinstance(waiting, str) else None))

    def _set_assignee(self, data):
        nid = self._str(data, "id", 200)
        if not nid:
            return {"ok": False, "error": "missing id"}
        # "" is meaningful: it un-assigns
        return graph.api_update_node(
            graph.connect(), nid,
            props_patch={"assignee": self._str(data, "assignee", 60)})

    def _update_node(self, data):
        nid = self._str(data, "id", 200)
        if not nid:
            return {"ok": False, "error": "missing id"}
        fields, patch = {}, {}
        if isinstance(data.get("title"), str):
            title = self._str(data, "title", 300)
            if not title:
                return {"ok": False, "error": "title cannot be empty"}
            fields["title"] = title
        if isinstance(data.get("body"), str):
            fields["body"] = self._str(data, "body")
        if isinstance(data.get("priority"), str):
            patch["priority"] = self._str(data, "priority", 20)
        if not fields and not patch:
            return {"ok": False, "error": "nothing to update"}
        return graph.api_update_node(graph.connect(), nid,
                                     props_patch=patch or None, **fields)

    def _edge_args(self, data):
        src, dst = self._str(data, "src", 200), self._str(data, "dst", 200)
        rel = self._str(data, "rel", 40)
        if not src or not dst:
            return None, {"ok": False, "error": "missing src or dst"}
        if rel not in graph.EDGE_RELS:
            return None, {"ok": False, "error": f"unknown relation: {rel}"}
        return (src, dst, rel), None

    def _link(self, data):
        args, err = self._edge_args(data)
        return err or graph.api_link(graph.connect(), *args)

    def _unlink(self, data):
        args, err = self._edge_args(data)
        return err or graph.api_unlink(graph.connect(), *args)

    def log_message(self, *_):  # quiet
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--no-reload", action="store_true",
                    help="do not restart when the server's own code changes")
    ap.add_argument("--no-write", action="store_true",
                    help="serve read-only — refuse every POST. Use this when "
                         "binding --host 0.0.0.0, since the write API is "
                         "unauthenticated and anyone on the network could edit "
                         "your brain.")
    args = ap.parse_args()

    global WRITABLE
    WRITABLE = not args.no_write
    # keep the log readable when it is a file rather than a terminal
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, OSError):
            pass

    graph.connect().close()  # ensure the brain exists
    url = f"http://{args.host}:{args.port}/"
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    # a restart re-execs with the same argv; don't open a new tab each time
    respawned = os.environ.pop("SECOND_BRAIN_VIZ_RESPAWN", "") == "1"
    if not respawned:
        print(f"second-brain map → {url}")
        print(f"  brain: {graph.db_path()}")
        print("  Ctrl-C to stop.")
    else:
        print(f"  reloaded → {url}")
    if not args.no_reload:
        threading.Thread(target=_watch, args=(httpd,), daemon=True).start()
    if not (args.no_open or respawned):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
        httpd.server_close()
        return
    httpd.server_close()          # free the port BEFORE handing over
    if _RESTART.is_set():
        os.environ["SECOND_BRAIN_VIZ_RESPAWN"] = "1"
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, *sys.argv])


if __name__ == "__main__":
    main()
