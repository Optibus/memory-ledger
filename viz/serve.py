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
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime
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
    mutated = ""          # newest `updated` across the graph — the change signal
    for r in node_rows:
        d = graph._node_dict(r)
        if d.get("updated", "") > mutated:
            mutated = d["updated"]
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
        # Cheap change token so the client only re-lays-out when something moved.
        # Reads the `updated` COLUMN: an in-place edit (a status flip, a new due
        # date) leaves the node/edge counts alone, so a count-only token would make
        # the write invisible to the poller. The old token read props["updated_at"],
        # which only learn-skill ever writes.
        "version": f"sqlite-{len(nodes)}-{len(edges)}-{mutated}",
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
    mutated = ""          # newest `updated` — same change signal as the sqlite path
    for d in node_rows:
        p = d["row"][0] or {}
        nid = p.get("id")
        realm = brain_query.norm_realm(p.get("realm", ""))
        if p.get("updated", "") > mutated:
            mutated = p["updated"]
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
        # had no mutation component at all, so an in-place write never moved it
        "version": f"neo4j-{len(nodes)}-{len(edges)}-{mutated}",
    }


def graph_payload() -> dict:
    """Prefer the neo4j master; fall back to the local SQLite mirror if neo4j is
    unreachable so the map still works offline."""
    return _neo4j_payload() or _sqlite_payload()


# ---------------------------------------------------------------------------
# settings + store health — what the Settings page reads and writes
#
# Preferences live in <home>/settings.json (see graph.settings), NOT in the
# browser: a timezone the UI sets has to hold for the cron, the hooks, and the
# CLI too, and localStorage cannot do that.
# ---------------------------------------------------------------------------
def timezones() -> list:
    try:
        from zoneinfo import available_timezones
        return sorted(available_timezones())
    except Exception:
        return []


def _int_setting(name: str, default: int) -> int:
    """A hand-edited settings.json (or a stray env var) must not 500 this page."""
    try:
        return int(graph.setting(name, str(default)))
    except (TypeError, ValueError):
        return default


def settings_payload() -> dict:
    """Stored values, plus what is ACTUALLY in effect. They differ whenever an
    env var overrides the file, and a settings page that hides that is lying —
    so `env` names every key the shell has taken over."""
    stored = graph.settings()
    env = {k: os.environ["SECOND_BRAIN_" + k.upper()]
           for k in graph.SETTING_KEYS
           if os.environ.get("SECOND_BRAIN_" + k.upper())}
    tz = graph.setting("tz")
    return {
        "ok": True, "writable": WRITABLE, "keys": graph.SETTING_KEYS,
        "settings": stored, "env": env, "path": str(graph.settings_path()),
        "effective": {
            "tz": tz, "tz_source": ("env" if "tz" in env else
                                    "settings" if stored.get("tz") else "system"),
            "system_tz": str(datetime.now().astimezone().tzinfo),
            "now": datetime.now(brain_query._local_tz(None)).isoformat(
                timespec="seconds"),
            "realm": graph.DEFAULT_REALM,
            # what a CLI read (`brain_query.py`) uses when given no --backend.
            # This page and the map always ask for "auto", so they are unaffected.
            "backend": brain_query.default_backend(),
            "backends": list(graph.BACKENDS),
            "map_poll_seconds": _int_setting("map_poll_seconds", 2),
            "dash_refresh_seconds": _int_setting("dash_refresh_seconds", 120),
        },
    }


def status_payload() -> dict:
    """Where the data lives and whether the master is reachable."""
    con = graph.connect()
    nodes = con.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"]
    edges = con.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"]
    pending = con.execute("SELECT COUNT(*) c FROM outbox").fetchone()["c"]
    captures = con.execute(
        "SELECT COUNT(*) c FROM nodes WHERE type='capture'"
        " AND json_extract(props,'$.consolidated_into') IS NULL").fetchone()["c"]
    cfg = graph._neo4j_cfg()
    up = graph._neo4j_up() if cfg["url"] else False
    return {
        "ok": True, "writable": WRITABLE, "boot": BOOT,
        "db": str(graph.db_path()), "home": str(graph.home_dir()),
        "nodes": nodes, "edges": edges, "pending_ops": pending,
        "loose_captures": captures,
        "neo4j": {
            "url": cfg["url"], "db": cfg["db"], "user": cfg["user"],
            "password_set": bool(cfg["password"]),
            "configured": bool(cfg["url"] and cfg["password"]),
            "up": up, "reading_from": "neo4j" if up else "sqlite (local mirror)",
            "file": str(graph.home_dir() / "neo4j.json"),
            # the file is only half the story — a shell that exports these wins
            "env": sorted(k for k in ("SECOND_BRAIN_NEO4J_URL", "SECOND_BRAIN_NEO4J_DB",
                                      "SECOND_BRAIN_NEO4J_USER",
                                      "SECOND_BRAIN_NEO4J_PASSWORD")
                          if os.environ.get(k)),
        },
    }


# The browser hanging up mid-response is NORMAL here, not an error: the map
# re-reads /api/graph every couple of seconds, and a reload, a tab close or a
# navigation aborts whatever was in flight. Python raises it from the socket
# write, and the default handling printed a full traceback per occurrence —
# which buried the one line that mattered when something was actually wrong.
CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


class QuietServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer that does not shout when a client walks away."""

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], CLIENT_GONE):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _fail(self, exc):
        """Report a handler error to the client, unless the client is the reason.

        Trying to write a 500 down a socket that just closed raises a SECOND
        exception out of the error path, which is what turned one aborted poll
        into two stacked tracebacks."""
        if isinstance(exc, CLIENT_GONE):
            return
        try:
            self._send(500, json.dumps({"error": str(exc)}), "application/json")
        except CLIENT_GONE:
            pass

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
            elif path == "/api/people":  # person nodes, for the assignee dropdown
                con = graph.connect()
                people = graph.person_rows(con)
                self._send(200, json.dumps({"ok": True, "people": [
                    {"id": d["id"], "title": d["title"],
                     "aliases": graph.person_aliases(d)[1:],
                     "realm": d.get("realm", "")}
                    for d in sorted(people, key=lambda x: x["title"].lower())]}),
                    "application/json")
            elif path == "/api/person-duplicates":   # the review list, read-only
                self._send(200, json.dumps(
                    {"ok": True, "duplicates": graph.person_duplicates(graph.connect())}),
                    "application/json")
            elif path == "/api/kinds":   # props.kind — open vocabulary, same as realms
                self._send(200, json.dumps({
                    "ok": True, "kinds": brain_query.kinds("auto"),
                    "none_token": brain_query.NO_KIND}), "application/json")
            elif path == "/api/skills":  # the two skill trees (CONVENTIONS §5a)
                # Read from the edges sync-skill-tree wrote, never re-derived from
                # the files here: two readers of one declaration cannot disagree,
                # two parsers of it eventually do.
                con = graph.connect()
                self._send(200, json.dumps({
                    "ok": True, "trees": graph.skill_trees(con),
                    "rules": graph.rule_catalog(con),
                    # what the pickers offer, so a card can show more than a
                    # bare token the person is expected to already recognise
                    "node_types": graph.node_type_catalog(con),
                    "areas": graph.area_titles(con)}), "application/json")
            elif path == "/api/rule":    # one rule in full, for the skill drawer
                rid = (parse_qs(parsed.query).get("id") or [""])[0]
                con = graph.connect()
                row = con.execute("SELECT * FROM nodes WHERE id=? AND type=?",
                                  (rid, graph.RULE_TYPE)).fetchone()
                self._send(200, json.dumps(
                    {"ok": bool(row),
                     "rule": graph._node_dict(row) if row else None}),
                    "application/json")
            elif path == "/api/settings":   # what the Settings page reads
                self._send(200, json.dumps(settings_payload()), "application/json")
            elif path == "/api/timezones":  # the IANA list, from the stdlib
                self._send(200, json.dumps({"ok": True, "zones": timezones()}),
                           "application/json")
            elif path == "/api/status":     # store health, for the Settings page
                self._send(200, json.dumps(status_payload()), "application/json")
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
                    # The contract shapes (§5.1, §5b.1) come from the engine too.
                    # A form that hard-coded the eight headings would be a second
                    # definition of the shape, and the one nothing checks.
                    "skill_sections": [
                        {"field": f, "heading": h, "required": r,
                         "generated": f in ("rules_honored", "depends_on")}
                        for f, h, r in graph.SKILL_SECTIONS],
                    "rule_sections": [{"field": f, "heading": h, "required": r}
                                      for f, h, r in graph.RULE_SECTIONS],
                    "decl_labels": [{"field": f, "label": l}
                                    for f, l in graph.DECL_LABELS],
                    "rule_sources": sorted(graph.RULE_PRECEDENCE),
                    # the declarable vocabulary, not just the types in use: a new
                    # skill legitimately writes a type this brain has never held
                    "node_types": graph.NODE_TYPES,
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
            self._fail(e)

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
        # Run the query BEFORE opening the response: wrapping the send meant a
        # client that hung up mid-write was reported as a bad query, and the 400
        # went down the same dead socket.
        try:
            payload = json.dumps(brain_query.run_json(view, **opts))
        except Exception as e:
            self._send(400, json.dumps({"ok": False, "error": str(e)}),
                       "application/json")
            return
        self._send(200, payload, "application/json")

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
            "/api/assign": self._assign,
            "/api/merge-person": self._merge_person,
            "/api/update-node": self._update_node,
            "/api/link": self._link,
            "/api/unlink": self._unlink,
            "/api/settings": self._save_settings,
            "/api/neo4j-config": self._save_neo4j,
            "/api/neo4j-test": self._test_neo4j,
            "/api/flush": self._flush,
            "/api/prune": self._prune,
            "/api/create-rule": self._create_rule,
            "/api/create-skill": self._create_skill,
            "/api/export-skills": self._export_skills,
            "/api/ingest-skills": self._ingest_skills,
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
            self._fail(e)

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

    # The priorities the UI offers. Open-ended in the store (it is just a prop), so
    # a value already in the graph is left alone — this only gates what the UI writes.
    PRIORITIES = ("", "P1", "P2", "P3", "P4")

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
            pri = self._str(data, "priority", 20)
            if pri not in self.PRIORITIES:
                return {"ok": False,
                        "error": f"priority must be one of {', '.join(p or '(none)' for p in self.PRIORITIES)}"}
            patch["priority"] = pri
        # due and realm ride along so one edit in the card is one write — one
        # `updated` bump, one neo4j sync, one version-token move for the poller.
        if isinstance(data.get("due"), str):
            due = self._str(data, "due", 40)
            if due and not re.match(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?", due):
                return {"ok": False, "error": "due must be YYYY-MM-DD or YYYY-MM-DD HH:MM"}
            fields["due"] = due
        if isinstance(data.get("realm"), str):
            realm = self._str(data, "realm", 40)
            if realm:
                fields["realm"] = realm      # "" would mean "keep", not "clear"
        if not fields and not patch:
            return {"ok": False, "error": "nothing to update"}
        return graph.api_update_node(graph.connect(), nid,
                                     props_patch=patch or None, **fields)

    def _assign(self, data):
        """Assign to an existing PERSON NODE, never a free-typed name.

        Writes both halves of the truth: props.assignee (the string every query
        path already filters on) and the structural assigned-to edge, so the map
        shows the link. person_id="" un-assigns and drops the edge.
        """
        nid = self._str(data, "id", 200)
        if not nid:
            return {"ok": False, "error": "missing id"}
        con = graph.connect()
        prev = [e["dst"] for e in con.execute(
            "SELECT dst FROM edges WHERE src=? AND rel='assigned-to'", (nid,)).fetchall()]
        pid = self._str(data, "person_id", 200)
        if not pid:
            for old in prev:
                graph.api_unlink(con, nid, old, "assigned-to")
            return graph.api_update_node(con, nid, props_patch={"assignee": ""})
        row = con.execute("SELECT * FROM nodes WHERE id=?", (pid,)).fetchone()
        if not row:
            return {"ok": False, "error": f"no node {pid}"}
        person = graph._node_dict(row)
        if person["type"] != "person":
            return {"ok": False,
                    "error": f"{pid} is a {person['type']}, not a person"}
        if (person["props"] or {}).get("superseded_by"):
            return {"ok": False, "error": f"{pid} was merged into "
                    f"{person['props']['superseded_by']}; assign to that node"}
        for old in prev:
            if old != pid:
                graph.api_unlink(con, nid, old, "assigned-to")
        graph.api_link(con, nid, pid, "assigned-to")
        # the person's own title is the token: norm_assignee lowercases it, so the
        # dashboard's chips and the "mine" filter keep working unchanged
        return graph.api_update_node(con, nid,
                                     props_patch={"assignee": person["title"]})

    def _merge_person(self, data):
        return graph.api_merge_person(graph.connect(),
                                      self._str(data, "canonical", 200),
                                      self._str(data, "duplicate", 200))

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

    # ------------------------------------------- authoring skills and rules
    # The Skills page used to be read-only, on the argument that a text box here
    # would be a third way to write a contract and the one nothing validates.
    # That argument was about validation, not about the browser: these routes
    # build the contract through `graph.build_*_contract` and refuse an
    # incomplete one, so this path is checked at write time, which is stricter
    # than the CLI's (where `audit-contracts` reports the gap afterwards).

    def _sections(self, data, key, spec):
        """The contract sections out of a request body, keyed by field name."""
        raw = data.get(key)
        if not isinstance(raw, dict):
            return {}
        return {f: self._str(raw, f, 12000) for f, _h, _r in spec
                if isinstance(raw.get(f), str)}

    def _rule_payload(self, data):
        title = self._str(data, "title", 300)
        if not title:
            return None, {"ok": False, "error": "a rule needs a title"}
        source = self._str(data, "source", 20) or "user"
        if source not in graph.RULE_PRECEDENCE:
            return None, {"ok": False, "error": f"unknown source: {source}"}
        return {
            "title": title,
            "sections": self._sections(data, "sections", graph.RULE_SECTIONS),
            "scenario": self._str(data, "scenario", 2000),
            "source": source,
            "section": self._str(data, "section", 40),
            "non_negotiable": bool(data.get("non_negotiable")),
            "overrides": self._str(data, "overrides", 200),
            "realm": self._str(data, "realm", 40),
            "node_id": self._str(data, "id", 200),
        }, None

    def _create_rule(self, data):
        args, err = self._rule_payload(data)
        return err or graph.api_add_rule(graph.connect(), **args)

    def _create_skill(self, data):
        """Create a skill, and the rules it was given inline, in one call.

        Inline rules are created FIRST and only inside this call: the modal can
        author a rule without leaving the skill form, and abandoning the skill
        leaves nothing behind. A rule that fails to write aborts the skill rather
        than producing one accountable to a rule that does not exist.
        """
        con = graph.connect()
        title = self._str(data, "title", 300)
        name = self._str(data, "name", 120) or title
        if not title:
            return {"ok": False, "error": "a skill needs a title"}
        sections = self._sections(data, "sections", graph.SKILL_SECTIONS)
        decl = {}
        for field, _label in graph.DECL_LABELS:
            val = data.get(field)
            if isinstance(val, list):
                decl[field] = [str(x)[:120] for x in val[:40]]
            elif isinstance(val, str):
                decl[field] = val[:1200]
        rule_ids = [str(r)[:200] for r in (data.get("rules") or [])[:40]
                    if isinstance(r, str) and r.strip()]
        created = []
        for item in (data.get("new_rules") or [])[:20]:
            if not isinstance(item, dict):
                continue
            args, err = self._rule_payload(item)
            if err:
                return err
            res = graph.api_add_rule(con, **args)
            if not res.get("ok"):
                return {"ok": False,
                        "error": f"rule {args['title']!r}: {res.get('error')}",
                        "created_rules": created}
            created.append(res["node"]["id"])
            rule_ids.append(res["node"]["id"])
        res = graph.api_add_skill(
            con, name=name, title=title,
            description=self._str(data, "description", 1000),
            triggers=self._str(data, "triggers", 2000),
            sections=sections, decl=decl,
            rules=list(dict.fromkeys(rule_ids)),
            realm=self._str(data, "realm", 40),
            node_id=self._str(data, "id", 200))
        if created:
            res["created_rules"] = created
        return res

    # ------------------------------------------------ markdown sync (§5a)
    def _export_skills(self, data):
        """Write the learned tree out as markdown — the file route between two
        machines, for when there is no shared master to mirror through."""
        return self._graph_cmd(
            ["export-skills", "--kind",
             self._str(data, "kind", 10) or "learned"] +
            (["--dir", self._str(data, "dir", 500)] if self._str(data, "dir", 500) else []))

    def _ingest_skills(self, data):
        cmd = ["ingest-skills"]
        d = self._str(data, "dir", 500)
        if d:
            cmd += ["--dir", d]
        return self._graph_cmd(cmd)

    @staticmethod
    def _graph_cmd(argv, timeout=300):
        """Run a graph.py command out of process and hand back its JSON.

        Same reason `prune` shells out: these commands report by printing, and a
        long scan must not be able to wedge the server's own connection."""
        import subprocess
        cmd = [sys.executable, str(HERE.parent / "scripts" / "graph.py"), *argv]
        try:
            pr = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"{argv[0]} timed out"}
        if pr.returncode != 0:
            return {"ok": False,
                    "error": (pr.stderr or pr.stdout or "failed").strip()[-400:]}
        try:
            return json.loads(pr.stdout)
        except ValueError:
            return {"ok": False, "error": (pr.stdout or "no output").strip()[-400:]}

    # -------------------------------------------------- settings + maintenance
    def _save_settings(self, data):
        """Write <home>/settings.json. Every value is validated here, because a
        bad timezone would otherwise silently fall back to the system one and
        look like the setting did nothing."""
        patch = {}
        if "tz" in data:
            tz = self._str(data, "tz", 60)
            if tz:
                try:
                    from zoneinfo import ZoneInfo
                    ZoneInfo(tz)
                except Exception:
                    return {"ok": False, "error": f"unknown timezone: {tz}"}
            patch["tz"] = tz
        if "realm" in data:
            realm = self._str(data, "realm", 40).lower()
            if realm and realm != graph.slugify(realm):
                return {"ok": False,
                        "error": "realm must be lowercase letters, digits and dashes"}
            patch["realm"] = realm
        if "backend" in data:
            be = self._str(data, "backend", 10).lower()
            if be and be not in graph.BACKENDS:
                return {"ok": False,
                        "error": "backend must be one of " + ", ".join(graph.BACKENDS)}
            patch["backend"] = be
        for key, lo, hi in (("map_poll_seconds", 1, 600),
                            ("dash_refresh_seconds", 5, 3600)):
            if key not in data:
                continue
            raw = data.get(key)
            if raw in (None, ""):
                patch[key] = ""
                continue
            try:
                val = int(raw)
            except (TypeError, ValueError):
                return {"ok": False, "error": f"{key} must be a whole number"}
            if not lo <= val <= hi:
                return {"ok": False, "error": f"{key} must be between {lo} and {hi}"}
            patch[key] = val
        if not patch:
            return {"ok": False, "error": "nothing to save"}
        graph.save_settings(patch)
        # This process read DEFAULT_REALM once, at import. Re-resolve it so a
        # realm changed in the browser applies to the next write, not the next
        # restart.
        graph.DEFAULT_REALM = graph.setting("realm", "optibus")
        return settings_payload()

    def _save_neo4j(self, data):
        """Write <home>/neo4j.json — the master-store connection. An empty url
        removes the file, which is how you go back to sqlite-only."""
        url = self._str(data, "url", 300)
        if url and not url.startswith(("http://", "https://")):
            return {"ok": False,
                    "error": "url must be the neo4j HTTP endpoint, e.g. "
                             "http://host:7474"}
        f = graph.home_dir() / "neo4j.json"
        if not url:
            try:
                f.unlink()
            except FileNotFoundError:
                pass
            # Leaving `backend: neo4j` behind would break every CLI read with no
            # master to read from, so removing the master resets the default too.
            if graph.settings().get("backend") in ("neo4j", "auto"):
                graph.save_settings({"backend": "sqlite"})
            return {"ok": True, "cleared": True, **status_payload()}
        cur = graph._neo4j_cfg()
        cfg = {"url": url,
               "db": self._str(data, "db", 60) or "neo4j",
               "user": self._str(data, "user", 60) or "neo4j"}
        # The password is never sent TO the browser, so a blank field means
        # "leave it alone" rather than "erase it".
        pw = self._str(data, "password", 200)
        cfg["password"] = pw or cur["password"]
        if not cfg["password"]:
            return {"ok": False, "error": "password required"}
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        try:
            f.chmod(0o600)      # it holds a password
        except OSError:
            pass
        ok, err = graph._neo4j_call([{"statement": "RETURN 1"}])
        return {"ok": True, "saved": True, "reachable": ok, "error": err,
                **status_payload()}

    def _test_neo4j(self, _data):
        cfg = graph._neo4j_cfg()
        if not (cfg["url"] and cfg["password"]):
            return {"ok": False, "error": "neo4j not configured"}
        ok, err = graph._neo4j_call([{"statement": "RETURN 1"}])
        return {"ok": True, "reachable": ok, "error": err, "url": cfg["url"]}

    def _flush(self, _data):
        con = graph.connect()
        pending = con.execute("SELECT COUNT(*) c FROM outbox").fetchone()["c"]
        res = graph._flush(con)
        res.setdefault("ok", True)
        res["was_pending"] = pending
        return res

    def _prune(self, data):
        """Run `graph.py maintain` — consolidate old captures into per-day
        digests so reads stay small. NON-DESTRUCTIVE: nothing is deleted, the
        digest just stands in for the raw turns (CONVENTIONS §2a).

        Shelled out rather than called in-process because cmd_maintain reports
        by printing JSON, and this way a long run can't wedge the server's own
        connection."""
        import subprocess
        keep = data.get("keep_days", 2)
        try:
            keep = max(0, min(365, int(keep)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "keep_days must be a whole number"}
        cmd = [sys.executable, str(HERE.parent / "scripts" / "graph.py"),
               "maintain", "--keep-days", str(keep)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "prune timed out after 5 minutes"}
        if p.returncode != 0:
            return {"ok": False,
                    "error": (p.stderr or p.stdout or "maintain failed").strip()[-400:]}
        try:
            return json.loads(p.stdout)
        except ValueError:
            return {"ok": False, "error": (p.stdout or "no output").strip()[-400:]}

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
    httpd = QuietServer((args.host, args.port), Handler)
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
