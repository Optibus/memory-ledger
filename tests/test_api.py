#!/usr/bin/env python3
"""The HTTP API, pinned from the outside (docs/api.md).

This suite is the specification of `scripts/serve.py` as a client sees it: the
routes, the envelope, the status codes, the refusals and the SPA fallback. It
starts the real entry point against a THROWAWAY brain and talks to it over a
socket, and never imports the server — so it describes behaviour, not whichever
framework happens to produce it. It was written against the stdlib server and
kept unchanged across the move to FastAPI; that is the point of it.

    python3 tests/test_api.py

Stdlib only, like every suite here. The server it starts brings its own
dependencies (see `scripts/serve.py`); this file needs none.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVE = ROOT / "scripts" / "serve.py"
GRAPH = ROOT / "scripts" / "graph.py"
FAILS: list[str] = []

# The whole route table. A route that appears or disappears on the server
# without appearing or disappearing here is a contract change nobody wrote down.
GET_ROUTES = [
    "/api/openapi.json", "/api/docs", "/api/graph", "/api/views", "/api/assignees",
    "/api/realms", "/api/node", "/api/people", "/api/capture", "/api/ui-prefs",
    "/api/backups", "/api/doctor", "/api/consolidate-preview",
    "/api/person-duplicates", "/api/kinds", "/api/skills", "/api/bundle",
    "/api/rule", "/api/settings", "/api/timezones", "/api/status", "/api/meta",
    "/api/suggest-links", "/api/search", "/api/trigger", "/api/trigger-request",
]
POST_ROUTES = [
    "/api/set-due", "/api/add-task", "/api/complete", "/api/set-status",
    "/api/set-assignee", "/api/assign", "/api/merge-person", "/api/add-alias",
    "/api/update-node", "/api/link", "/api/unlink", "/api/settings",
    "/api/neo4j-config", "/api/neo4j-test", "/api/flush", "/api/prune",
    "/api/capture", "/api/consolidate", "/api/ui-prefs", "/api/backup",
    "/api/create-rule", "/api/create-skill", "/api/create-process",
    "/api/export-skills", "/api/ingest-skills", "/api/export-bundle",
    "/api/upload-bundle", "/api/import-bundle", "/api/delete-contracts",
    "/api/trigger-gate", "/api/trigger-action",
]
VIEWS = ["agenda", "open", "today", "tomorrow", "week", "closed", "range"]

FULL_SKILL = {
    "when_to_use": "A second machine needs what this one learned.",
    "given": "The learned tree, and a destination directory.",
    "deliverables": "**The folder is self-contained.** Rules sit beside skills.",
    "judged_against": "Ingesting the folder alone restores both halves.",
    "guardrails": "**Must not overwrite a rule the other brain holds.**",
    "reply": "One line: how many moved, and where.",
}
FULL_RULE = {
    "the_rule": "An export carries the rules its skills honor.",
    "why": "A skill whose rules stayed behind is accountable to nothing.",
    "judged_against": "The exported folder has a rules/ entry per honored id.",
}


def ck(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def env_for(home):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SECOND_BRAIN_NEO4J", "MEMORY_LEDGER_NEO4J"))
           and k not in ("SECOND_BRAIN_OFF", "MEMORY_LEDGER_OFF")}
    return {**env, "SECOND_BRAIN_HOME": home, "SECOND_BRAIN_NO_AUTOSYNC": "1"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Resp:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.raw = status, headers, body

    @property
    def ctype(self):
        return (self.headers.get("content-type") or "").split(";")[0].strip()

    def json(self):
        try:
            return json.loads(self.raw)
        except ValueError:
            return {"__not_json__": self.raw[:200].decode("utf-8", "replace")}


class Server:
    """One real serve.py process on a free port, over a fresh brain."""

    def __init__(self, *flags):
        self.home = tempfile.mkdtemp(prefix="second-brain-api-")
        self.port = free_port()
        self.log = Path(self.home) / "server.log"
        self._out = open(self.log, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVE), "--port", str(self.port),
             "--no-open", "--no-reload", "--no-build", *flags],
            env=env_for(self.home), stdout=self._out, stderr=subprocess.STDOUT)
        # Generous: a first start may be creating the server's environment.
        deadline = time.time() + 240
        while time.time() < deadline:
            if self.proc.poll() is not None:
                break
            try:
                if self.get("/api/meta").status == 200:
                    return
            except OSError:
                pass
            time.sleep(0.25)
        self.stop()
        raise SystemExit("the server never came up:\n" +
                         self.log.read_text(errors="replace")[-2000:])

    def request(self, method, path, body=None, raw_body=None, timeout=60):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        headers = {}
        data = raw_body
        if body is not None:
            data = json.dumps(body).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=headers)
        r = c.getresponse()
        out = Resp(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
        c.close()
        return out

    def get(self, path, **q):
        if q:
            path += "?" + urllib.parse.urlencode(q)
        return self.request("GET", path)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body={} if body is None and "raw_body" not in kw
                            else body, **kw)

    def cli(self, *args):
        p = subprocess.run([sys.executable, str(GRAPH), *args], capture_output=True,
                           text=True, env=env_for(self.home))
        try:
            return json.loads(p.stdout)
        except ValueError:
            return {"ok": False, "raw": p.stdout[:300], "stderr": p.stderr[:300]}

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._out.close()
        shutil.rmtree(self.home, ignore_errors=True)


def refused(r: Resp, status=400, needle=""):
    """The failure envelope: a status, ok:false, and an error worth showing."""
    j = r.json()
    return (r.status == status and j.get("ok") is False
            and isinstance(j.get("error"), str) and needle in j["error"])


# =========================================================== the envelope
def test_envelope(s: Server):
    print("\n== every answer is the same shape ==")
    r = s.get("/api/meta")
    ck("an API answer is JSON", r.ctype == "application/json", r.ctype)
    ck("and is never cached — the map polls, and a cached poll is a frozen map",
       r.headers.get("cache-control") == "no-store", r.headers.get("cache-control"))
    ck("and carries ok", r.json().get("ok") is True)

    r = s.get("/")
    ck("the app is never cached either", r.headers.get("cache-control") == "no-store",
       r.headers.get("cache-control"))

    r = s.get("/api/no-such-view")
    ck("an unknown GET under /api is a JSON 404, not the app",
       refused(r, 404, "unknown view: no-such-view") and r.ctype == "application/json",
       f"{r.status} {r.raw[:80]!r}")
    r = s.post("/api/no-such-route", {})
    ck("an unknown POST is a JSON 404", refused(r, 404, "not found"), f"{r.status} {r.raw[:80]!r}")
    r = s.post("/api/graph", {})
    ck("a POST to a read-only route is a 404 too, not a 405",
       refused(r, 404, "not found"), f"{r.status} {r.raw[:80]!r}")
    r = s.get("/api/add-task")
    ck("a GET to a write-only route is an unknown view",
       refused(r, 404, "unknown view: add-task"), f"{r.status} {r.raw[:80]!r}")

    r = s.post("/api/add-task", raw_body=b"{not json")
    ck("a body that is not JSON is refused as a bad request, not a crash",
       r.status == 400 and r.json().get("ok") is False, f"{r.status} {r.raw[:80]!r}")
    r = s.post("/api/add-task", raw_body=b"[1, 2]")
    ck("a JSON body that is not an object is refused too",
       r.status == 400 and r.json().get("ok") is False, f"{r.status} {r.raw[:80]!r}")
    r = s.post("/api/add-task", raw_body=b"")
    ck("an empty body is an empty object, and refused on its merits",
       refused(r, 400, "missing title"), f"{r.status} {r.raw[:80]!r}")
    r = s.post("/api/add-task", {"title": 123})
    ck("a field of the wrong type is refused, never stored as its repr",
       r.status == 400 and r.json().get("ok") is False, f"{r.status} {r.raw[:80]!r}")


# ============================================================= the docs
def test_docs(s: Server):
    print("\n== the API describes itself, from the running process ==")
    r = s.get("/api/openapi.json")
    doc = r.json()
    ck("the document is served", r.status == 200 and r.ctype == "application/json")
    ck("and is OpenAPI 3", str(doc.get("openapi", "")).startswith("3."),
       str(doc.get("openapi")))
    paths = doc.get("paths", {})
    # The document and its viewer are served (checked below) but are not
    # endpoints OF the API, so the document does not have to list itself.
    self_describing = {"/api/openapi.json", "/api/docs"}
    missing_get = [p for p in GET_ROUTES
                   if p not in self_describing and "get" not in paths.get(p, {})]
    missing_post = [p for p in POST_ROUTES if "post" not in paths.get(p, {})]
    ck("every read route is in it", not missing_get, ", ".join(missing_get))
    ck("every write route is in it", not missing_post, ", ".join(missing_post))
    ck("and the dashboard views, as the one templated route",
       "get" in paths.get("/api/{view}", {}), str([p for p in paths if "{" in p]))
    known = set(GET_ROUTES) | set(POST_ROUTES) | {"/api/{view}"}
    extra = sorted(p for p in paths if p not in known)
    ck("and nothing this suite has never heard of", not extra, ", ".join(extra))

    r = s.get("/api/docs")
    ck("a browsable UI is served over it",
       r.status == 200 and r.ctype == "text/html" and b"swagger" in r.raw.lower(),
       f"{r.status} {r.ctype}")
    ck("pointing at the document on the same port", b"/api/openapi.json" in r.raw)


# ============================================================ the SPA
def test_static(s: Server):
    print("\n== anything that is not /api is the app ==")
    index = (ROOT / "frontend" / "dist" / "index.html").read_bytes()
    r = s.get("/")
    ck("/ is the built index", r.status == 200 and r.ctype == "text/html"
       and r.raw == index, f"{r.status} {r.ctype}")
    r = s.get("/dashboard/deep/link")
    ck("a deep link falls back to the index", r.status == 200 and r.raw == index,
       f"{r.status}")
    js = ROOT / "frontend" / "dist" / "assets" / "app.js"
    r = s.get("/assets/app.js")
    ck("a built asset is served as itself", r.status == 200 and r.raw == js.read_bytes())
    ck("with a type a browser will execute", r.ctype == "text/javascript", r.ctype)
    r = s.get("/assets/app.css")
    ck("and CSS as CSS", r.ctype == "text/css", r.ctype)
    for path in ("/../../scripts/graph.py", "/%2e%2e/%2e%2e/scripts/graph.py",
                 "/assets/../../../scripts/graph.py"):
        r = s.get(path)
        ck(f"{path} never reads outside the build",
           b"memory-ledger graph engine" not in r.raw and r.status in (200, 404),
           f"{r.status}")


# ============================================================== reads
def test_reads(s: Server):
    print("\n== the reads answer, with the fields the page reads ==")
    s.cli("add-node", "--type", "person", "--title", "Ada Lovelace")
    s.cli("add-node", "--type", "task", "--title", "Ship the thing",
          "--props", json.dumps({"assignee": "ada lovelace"}), "--due", "2026-09-30")

    meta = s.get("/api/meta").json()
    for key in ("rels", "statuses", "realms", "default_realm", "kinds",
                "no_kind_token", "types", "skill_sections", "rule_sections",
                "decl_labels", "rule_sources", "node_types", "writable", "boot"):
        ck(f"meta.{key}", key in meta)
    ck("meta says the server is writable", meta.get("writable") is True)
    ck("the relation vocabulary is the engine's", "relates-to" in meta.get("rels", []))
    ck("the generated skill sections are flagged",
       {x["field"] for x in meta.get("skill_sections", []) if x.get("generated")} ==
       {"rules_honored", "depends_on"})

    g = s.get("/api/graph").json()
    ck("graph: nodes and edges", isinstance(g.get("nodes"), list) and
       isinstance(g.get("edges"), list))
    ck("graph: a node carries what the map draws",
       g["nodes"] and set(g["nodes"][0]) >= {"id", "type", "title", "status", "due",
                                             "body", "realm", "kind", "props", "degree"},
       str(sorted(g["nodes"][0]) if g["nodes"] else []))
    ck("graph: meta counts and the boot token",
       set(g.get("meta", {})) >= {"nodes", "edges", "by_type", "by_realm", "by_kind",
                                  "boot", "generated", "source", "ego"})
    ck("graph: reads from the local store with no master configured",
       g["meta"].get("source") == "sqlite (local)", g["meta"].get("source"))
    ck("graph: a version token", str(g.get("version", "")).startswith("sqlite-"))
    ck("graph: boot matches meta", g["meta"]["boot"] == meta["boot"])

    ck("views", s.get("/api/views").json().get("views") == VIEWS)
    for view in VIEWS:
        if view == "range":
            continue
        r = s.get(f"/api/{view}")
        ck(f"view {view} answers", r.status == 200 and r.json().get("ok") is True,
           f"{r.status} {r.raw[:80]!r}")
    ck("a bare range is refused: it needs both ends",
       refused(s.get("/api/range"), 400, "range needs date_from and date_to"))
    r = s.get("/api/range", **{"from": "2026-01-01", "to": "2026-12-31"})
    ck("range takes from/to", r.status == 200 and r.json().get("ok"), r.raw[:120])
    ck("and finds the task due in it",
       "Ship the thing" in r.raw.decode(), r.raw[:120])
    r = s.get("/api/range", **{"from": "2026-01-01", "to": "oops"})
    ck("a bad query to a view is a 400 envelope", refused(r, 400), f"{r.status}")
    r = s.get("/api/open", mine="1", since_days="x", nonsense="1")
    ck("junk and unknown view params are ignored", r.status == 200, f"{r.status}")

    a = s.get("/api/assignees").json()
    ck("assignees are open-task counts per person",
       a.get("ok") and a.get("assignees", {}).get("ada lovelace") == 1, str(a)[:120])
    rl = s.get("/api/realms").json()
    ck("realms, with the default", rl.get("ok") and "realms" in rl and "default" in rl)
    k = s.get("/api/kinds").json()
    ck("kinds, with the none token", k.get("ok") and "kinds" in k and "none_token" in k)

    p = s.get("/api/people").json()
    ada = [x for x in p.get("people", []) if x["title"] == "Ada Lovelace"]
    ck("people lists the person", len(ada) == 1, str(p)[:120])
    ck("with id, aliases and realm", ada and set(ada[0]) == {"id", "title", "aliases",
                                                             "realm"})

    task = next(n for n in g["nodes"] if n["title"] == "Ship the thing")
    d = s.get("/api/node", id=task["id"]).json()
    ck("node detail is the node, flat, with its edges",
       d.get("ok") and d.get("id") == task["id"] and isinstance(d.get("edges"), list),
       str(d)[:120])
    r = s.get("/api/node", id="nope_nothing")
    ck("an unknown node is ok:false, still a 200", r.status == 200
       and r.json().get("ok") is False, f"{r.status}")
    r = s.get("/api/node")
    ck("no id at all is ok:false too", r.status == 200 and r.json().get("ok") is False)

    c = s.get("/api/capture").json()
    ck("capture state", set(c) >= {"enabled", "marker", "marker_present", "env_off",
                                   "source"} and c["enabled"] is True, str(c)[:120])
    u = s.get("/api/ui-prefs").json()
    ck("ui prefs", u.get("ok") and set(u) >= {"synced", "path", "prefs"})
    b = s.get("/api/backups").json()
    ck("backups", b.get("ok") and set(b) >= {"dir", "keep_days", "backups"})
    doc = s.get("/api/doctor").json()
    ck("doctor: the call worked, and health is its own field",
       doc.get("ok") is True and isinstance(doc.get("healthy"), bool), str(doc)[:120])

    cp = s.get("/api/consolidate-preview").json()
    ck("consolidate preview is a dry run", cp.get("ok") and cp.get("dry_run") is True
       and cp.get("keep_days") == 2, str(cp)[:120])
    ck("keep_days is read", s.get("/api/consolidate-preview", keep_days="5")
       .json().get("keep_days") == 5)
    ck("and clamped", s.get("/api/consolidate-preview", keep_days="9999")
       .json().get("keep_days") == 365)
    r = s.get("/api/consolidate-preview", keep_days="abc")
    ck("a non-number is ok:false, still a 200", r.status == 200 and
       r.json().get("ok") is False, f"{r.status}")

    ck("person duplicates", isinstance(s.get("/api/person-duplicates").json()
                                       .get("duplicates"), list))
    sk = s.get("/api/skills").json()
    ck("skills carries both trees and the catalogs",
       sk.get("ok") and set(sk) >= {"trees", "rules", "processes", "realm",
                                    "node_types", "areas"}, str(sorted(sk))[:160])

    r = s.get("/api/rule", id="rule_nothing")
    ck("an unknown rule is ok:false with rule null", r.status == 200 and
       r.json() == {"ok": False, "rule": None}, r.raw[:80])

    st = s.get("/api/settings").json()
    ck("settings: stored, env and what is in effect",
       st.get("ok") and set(st) >= {"writable", "keys", "settings", "env", "path",
                                    "effective", "capture"})
    ck("settings: effective carries the resolved values",
       set(st.get("effective", {})) >= {"tz", "tz_source", "system_tz", "now", "realm",
                                        "owner", "owner_source", "backend", "backends",
                                        "autosync", "map_poll_seconds",
                                        "dash_refresh_seconds"})
    tz = s.get("/api/timezones").json()
    ck("timezones, from the stdlib", tz.get("ok") and "UTC" in tz.get("zones", []))
    status = s.get("/api/status").json()
    ck("status", status.get("ok") and set(status) >= {"writable", "boot", "db", "home",
                                                      "nodes", "edges", "pending_ops",
                                                      "loose_captures", "neo4j"})
    ck("status never sends the password",
       "password" not in status.get("neo4j", {}) and "password_set" in status.get("neo4j", {}))

    sl = s.get("/api/suggest-links", text="Ship the thing").json()
    ck("suggest links", sl.get("ok") and sl.get("count") == len(sl.get("suggestions", [])))

    ck("a one-letter search is empty, not a scan",
       s.get("/api/search", q="S").json() == {"ok": True, "results": []})
    sr = s.get("/api/search", q="ship").json()
    ck("search is case-insensitive on the title",
       any(x["title"] == "Ship the thing" for x in sr.get("results", [])))
    ck("and returns id, type, title, status",
       sr["results"] and set(sr["results"][0]) == {"id", "type", "title", "status"})
    ck("limit is clamped to at least one",
       len(s.get("/api/search", q="th", limit="0").json()["results"]) <= 1)

    r = s.get("/api/bundle", name="nothing.zip")
    ck("a missing bundle is a 404", refused(r, 404, "no such bundle"), f"{r.status}")
    r = s.get("/api/bundle", name="../graph.db")
    ck("a name pointing out of the bundle dir serves nothing", refused(r, 404))


# ============================================================= writes
def test_task_writes(s: Server):
    print("\n== the task writes ==")
    ck("add-task needs a title", refused(s.post("/api/add-task", {}), 400, "missing title"))
    ck("and a known type", refused(s.post("/api/add-task", {"title": "x", "type": "note"}),
                                   400, "type must be task or subtask"))
    ada = s.cli("add-node", "--type", "person", "--title", "Grace Hopper")["node"]["id"]
    r = s.post("/api/add-task", {
        "title": "Write the spec", "body": "b", "due": "2026-10-01",
        "assignee": "grace hopper", "priority": "P2",
        "links": [{"id": ada, "rel": "about"}, {"id": ada, "rel": "made-up"}, "junk"]})
    j = r.json()
    ck("add-task writes the node", r.status == 200 and j.get("ok"), r.raw[:120])
    tid = j["node"]["id"]
    ck("marked as coming from the web UI", j["node"]["props"].get("source") == "web-ui")
    ck("with its props", j["node"]["props"].get("priority") == "P2")
    ck("open, with its due date", j["node"]["status"] == "open" and
       j["node"]["due"].startswith("2026-10-01"))
    ck("the kept links are applied", j.get("links") == [{"id": ada, "rel": "about"}],
       str(j.get("links")))
    ck("an unknown relation is reported back, not applied",
       j.get("rejected", [{}])[0].get("error") == "unknown relation", str(j.get("rejected")))

    ck("set-due needs an id", refused(s.post("/api/set-due", {}), 400, "missing id"))
    r = s.post("/api/set-due", {"id": tid, "due": "2026-11-02"})
    ck("set-due", r.json() == {"ok": True, "id": tid, "due": r.json().get("due")}
       and r.json()["due"].startswith("2026-11-02"), r.raw[:120])

    ck("set-status needs a known status",
       refused(s.post("/api/set-status", {"id": tid, "status": "nah"}), 400,
               "status must be one of open, doing"))
    ck("set-status needs an id", refused(s.post("/api/set-status", {"status": "open"}),
                                         400, "missing id"))
    r = s.post("/api/set-status", {"id": tid, "status": "waiting", "waiting_on": "Grace"})
    ck("set-status", r.status == 200 and r.json().get("ok"), r.raw[:120])

    ck("set-assignee needs an id", refused(s.post("/api/set-assignee", {}), 400, "missing id"))
    ck("set-assignee", s.post("/api/set-assignee", {"id": tid, "assignee": "x"}).json()
       .get("ok"))

    ck("assign needs an id", refused(s.post("/api/assign", {}), 400, "missing id"))
    ck("assign refuses a node that is not there",
       refused(s.post("/api/assign", {"id": tid, "person_id": "person_nope"}), 400,
               "no node person_nope"))
    ck("assign refuses a node that is not a person",
       refused(s.post("/api/assign", {"id": tid, "person_id": tid}), 400, "not a person"))
    r = s.post("/api/assign", {"id": tid, "person_id": ada})
    ck("assign", r.status == 200 and r.json().get("ok"), r.raw[:120])
    edges = s.get("/api/node", id=tid).json().get("edges", [])
    ck("assign writes the edge", any(e["rel"] == "assigned-to" for e in edges))
    ck("and the assignee string",
       s.get("/api/node", id=tid).json()["props"]["assignee"] == "Grace Hopper")
    s.post("/api/assign", {"id": tid, "person_id": ""})
    edges = s.get("/api/node", id=tid).json().get("edges", [])
    ck("an empty person un-assigns and drops the edge",
       not any(e["rel"] == "assigned-to" for e in edges))

    ck("update-node needs an id", refused(s.post("/api/update-node", {}), 400, "missing id"))
    ck("and something to change", refused(s.post("/api/update-node", {"id": tid}), 400,
                                          "nothing to update"))
    ck("a title cannot be blanked", refused(
        s.post("/api/update-node", {"id": tid, "title": "  "}), 400, "title cannot be empty"))
    ck("priority is a closed set here", refused(
        s.post("/api/update-node", {"id": tid, "priority": "urgent"}), 400,
        "priority must be one of"))
    ck("due is a date", refused(
        s.post("/api/update-node", {"id": tid, "due": "friday"}), 400,
        "due must be YYYY-MM-DD"))
    before = s.get("/api/graph").json()["version"]
    time.sleep(1.1)
    r = s.post("/api/update-node", {"id": tid, "title": "Write the real spec",
                                    "priority": "", "due": "2026-12-01", "realm": ""})
    ck("update-node", r.status == 200 and r.json()["node"]["title"] == "Write the real spec",
       r.raw[:120])
    ck("an in-place edit moves the graph's version token",
       s.get("/api/graph").json()["version"] != before)

    ck("link needs both ends", refused(s.post("/api/link", {"src": tid}), 400,
                                       "missing src or dst"))
    ck("link needs a known relation", refused(
        s.post("/api/link", {"src": tid, "dst": ada, "rel": "loves"}), 400,
        "unknown relation: loves"))
    ck("link", s.post("/api/link", {"src": tid, "dst": ada, "rel": "mentions"}).json()
       .get("ok"))
    ck("unlink", s.post("/api/unlink", {"src": tid, "dst": ada, "rel": "mentions"}).json()
       .get("ok"))

    ck("complete needs an id", refused(s.post("/api/complete", {}), 400, "missing id"))
    r = s.post("/api/complete", {"id": tid, "note": "done"})
    ck("complete", r.status == 200 and r.json().get("ok"), r.raw[:120])


def test_people_writes(s: Server):
    print("\n== the people writes ==")
    a = s.cli("add-node", "--type", "person", "--title", "Alan Turing")["node"]["id"]
    b = s.cli("add-node", "--type", "person", "--title", "A. Turing",
              "--force-new")["node"]["id"]
    ck("add-alias needs both halves",
       refused(s.post("/api/add-alias", {"id": a}), 400, "id and alias are both required"))
    ck("add-alias", s.post("/api/add-alias", {"id": a, "alias": "Prof"}).json().get("ok"))
    ck("merge-person refuses nonsense",
       s.post("/api/merge-person", {"canonical": "", "duplicate": ""}).status == 400)
    r = s.post("/api/merge-person", {"canonical": a, "duplicate": b})
    ck("merge-person", r.status == 200 and r.json().get("ok"), r.raw[:160])
    ck("a merged person cannot be assigned to", refused(
        s.post("/api/assign", {"id": s.post("/api/add-task", {"title": "t"})
                               .json()["node"]["id"], "person_id": b}), 400, "merged into"))


def test_settings_writes(s: Server):
    print("\n== the settings writes ==")
    ck("nothing to save", refused(s.post("/api/settings", {}), 400, "nothing to save"))
    ck("an unknown timezone", refused(s.post("/api/settings", {"tz": "Mars/Olympus"}), 400,
                                      "unknown timezone: Mars/Olympus"))
    ck("a realm that is not a slug", refused(s.post("/api/settings", {"realm": "Big Realm"}),
                                             400, "realm must be lowercase"))
    ck("a switch that is not a boolean", refused(
        s.post("/api/settings", {"autosync": "yes"}), 400, "autosync must be true or false"))
    ck("an unknown backend", refused(s.post("/api/settings", {"backend": "redis"}), 400,
                                     "backend must be one of"))
    ck("a poll interval out of range", refused(
        s.post("/api/settings", {"map_poll_seconds": 0}), 400,
        "map_poll_seconds must be between 1 and 600"))
    ck("a poll interval that is not a number", refused(
        s.post("/api/settings", {"dash_refresh_seconds": "soon"}), 400,
        "dash_refresh_seconds must be a whole number"))
    r = s.post("/api/settings", {"tz": "Asia/Jerusalem", "realm": "personal",
                                 "owner": "Grace Hopper", "ui_sync": True,
                                 "map_poll_seconds": "5", "consolidate": None})
    j = r.json()
    ck("a valid save answers with the whole settings payload",
       r.status == 200 and j.get("ok") and "effective" in j, r.raw[:120])
    ck("and it took", j["effective"]["tz"] == "Asia/Jerusalem" and
       j["effective"]["owner"] == "Grace Hopper" and j["effective"]["map_poll_seconds"] == 5
       and j["effective"]["ui_sync"] is True, str(j.get("effective"))[:200])
    ck("a realm set here applies to the next write, not the next restart",
       s.post("/api/add-task", {"title": "realm probe"}).json()["node"]["realm"] == "personal")

    ck("the ui prefs are stored", s.post("/api/ui-prefs", {"prefs": {"tab": "map"}})
       .json().get("synced") is True)
    ck("and read back", s.get("/api/ui-prefs").json()["prefs"].get("tab") == "map")

    ck("capture needs a boolean", refused(s.post("/api/capture", {"enabled": "off"}), 400,
                                          "enabled must be true or false"))
    off = s.post("/api/capture", {"enabled": False}).json()
    ck("capture can be switched off", off.get("ok") and off["capture"]["enabled"] is False
       and off["capture"]["source"] == "marker", str(off)[:120])
    on = s.post("/api/capture", {"enabled": True}).json()
    ck("and back on", on.get("ok") and on["capture"]["enabled"] is True)

    ck("neo4j wants an http url", refused(s.post("/api/neo4j-config", {"url": "bolt://x"}),
                                          400, "url must be the neo4j HTTP endpoint"))
    ck("and a password", refused(s.post("/api/neo4j-config", {"url": "http://127.0.0.1:1"}),
                                 400, "password required"))
    ck("testing an unconfigured master", refused(s.post("/api/neo4j-test", {}), 400,
                                                 "neo4j not configured"))
    r = s.post("/api/neo4j-config", {"url": ""})
    ck("an empty url clears it", r.status == 200 and r.json().get("cleared") is True,
       r.raw[:120])

    r = s.post("/api/flush", {})
    ck("flush with no master is refused, and still reports what was pending",
       refused(r, 400, "neo4j not configured") and "was_pending" in r.json(), r.raw[:120])
    b = s.post("/api/backup", {"label": "spec"}).json()
    ck("backup", b.get("ok") and set(b) >= {"created", "removed", "backups"}, str(b)[:120])

    for route in ("/api/prune", "/api/consolidate"):
        r = s.post(route, {})
        ck(f"{route} refuses without confirm", refused(r, 400, "Preview it first")
           and r.json().get("needs_confirmation") is True)
        ck(f"{route} refuses a bad keep_days", refused(
            s.post(route, {"confirm": True, "keep_days": "x"}), 400,
            "keep_days must be a whole number"))
    r = s.post("/api/consolidate", {"confirm": True, "keep_days": 30})
    ck("consolidate runs when confirmed", r.status == 200 and r.json().get("ok"),
       r.raw[:160])


def test_contract_writes(s: Server):
    print("\n== skills, rules, processes, bundles ==")
    ck("a rule needs a title", refused(s.post("/api/create-rule", {}), 400,
                                       "a rule needs a title"))
    ck("and a known source", refused(s.post("/api/create-rule",
                                            {"title": "x", "source": "made-up"}), 400,
                                     "unknown source: made-up"))
    ck("and a way to check it", refused(s.post("/api/create-rule", {
        "title": "Half", "sections": {"the_rule": "Do it."}}), 400, "JUDGED AGAINST"))
    r = s.post("/api/create-rule", {"title": "Spec rule", "sections": FULL_RULE,
                                    "scenario": "write a spec"})
    ck("create-rule", r.status == 200 and r.json().get("ok"), r.raw[:120])
    rid = r.json()["node"]["id"]
    ck("and the rule can be read in full",
       s.get("/api/rule", id=rid).json()["rule"]["id"] == rid)

    ck("a skill needs a title", refused(s.post("/api/create-skill", {}), 400,
                                        "a skill needs a title"))
    r = s.post("/api/create-skill", {
        "name": "spec-sweep", "title": "Spec sweep", "description": "d",
        "sections": FULL_SKILL, "reads": ["task", "note"], "writes": "note",
        "rules": [rid], "new_rules": [{"title": "Inline rule", "sections": FULL_RULE,
                                       "scenario": "inline"}, "junk"]})
    j = r.json()
    ck("create-skill, with an inline rule", r.status == 200 and j.get("ok")
       and len(j.get("created_rules", [])) == 1, r.raw[:160])
    ck("declarations arrive as a list or a string",
       j["node"]["props"].get("reads") == "task, note")
    r = s.post("/api/create-skill", {"name": "nope", "title": "Nope",
                                     "sections": FULL_SKILL, "new_rules": [{}]})
    ck("a broken inline rule aborts the skill", refused(r, 400))
    ck("and the skill was not written", not s.get("/api/node", id="skill_nope").json()["ok"])

    ck("a process realm must be a slug", refused(
        s.post("/api/create-process", {"title": "p", "realm": "Not A Slug"}), 400,
        "realm must be lowercase"))
    r = s.post("/api/create-process", {"title": "Spec process", "body": "Step one.",
                                       "trigger": "when asked"})
    ck("create-process", r.status == 200 and r.json().get("ok"), r.raw[:120])

    ck("an export to a bad kind is refused",
       refused(s.post("/api/export-skills", {"kind": "nonsense"}), 400))
    exp = s.post("/api/export-skills", {"kind": "learned"}).json()
    ck("export-skills", exp.get("ok") and exp.get("skills") == 1, str(exp)[:120])
    ing = s.post("/api/ingest-skills", {"dir": exp.get("dir", "")}).json()
    ck("ingest-skills", ing.get("ok") and ing.get("count", 0) >= 1, str(ing)[:120])

    exp = s.post("/api/export-bundle", {"skills": ["skill_spec-sweep"], "rules": []}).json()
    ck("export-bundle", exp.get("ok") and exp.get("download", "").startswith(
        "/api/bundle?name="), str(exp)[:160])
    r = s.request("GET", exp["download"])
    ck("the bundle downloads as a zip", r.status == 200 and r.raw[:2] == b"PK"
       and r.ctype == "application/zip", f"{r.status} {r.ctype}")
    ck("as an attachment", "attachment" in r.headers.get("content-disposition", ""))

    ck("an upload with no file", refused(s.post("/api/upload-bundle", {}), 400,
                                         "no file was sent"))
    ck("an upload that is not base64", refused(
        s.post("/api/upload-bundle", {"data": "not base64 at all!!"}), 400,
        "not valid base64"))
    up = s.post("/api/upload-bundle", {"filename": "b.zip",
                                       "data": base64.b64encode(r.raw).decode()}).json()
    ck("upload inspects before writing", up.get("ok") and up.get("token")
       and up.get("filename") == "b.zip", str(up)[:160])
    ck("import needs a staged token", refused(
        s.post("/api/import-bundle", {"token": "../../graph", "select": {"x": "update"}}),
        400, "no longer staged"))
    ck("import needs a selection", refused(
        s.post("/api/import-bundle", {"token": up["token"], "select": {"x": "explode"}}),
        400, "nothing was selected"))
    imp = s.post("/api/import-bundle", {"token": up["token"],
                                        "select": {"skill_spec-sweep": "replace"}}).json()
    ck("import-bundle", imp.get("ok"), str(imp)[:160])

    d = s.post("/api/delete-contracts", {"ids": ["skill_spec-sweep"]}).json()
    ck("delete-contracts", d.get("ok") and d.get("count") == 1, str(d)[:160])


# ================================================================ triggers
def test_trigger_routes(s: Server):
    print("\n== triggers: the request log, its actions, the gate ==")
    t = s.get("/api/trigger").json()
    ck("status answers with no trigger.json, and says why",
       t.get("ok") and t["config"]["enabled"] is False and "does not exist" in t["config"]["problem"])
    ck("without a config the watchers do not start, and say why",
       t["runtime"]["running"] is False and "does not exist" in t["runtime"]["reason"],
       str(t.get("runtime"))[:160])

    # A mention, logged the way a watcher would log it: through the engine.
    snippet = (
        "import sys, json; sys.path.insert(0, %r); import trigger; "
        "ev = {'source': 'slack', 'event_id': 'C1:1.0', 'scope': 'slack:C1:1.0', "
        "'requester_id': 'U1', 'is_owner': True, 'text': 'hello', 'meta': {}}; "
        "print(json.dumps(trigger.intake(ev, trigger.load_config())))"
        % str(ROOT / "scripts"))
    p = subprocess.run([sys.executable, "-c", snippet], capture_output=True, text=True,
                       env=env_for(s.home))
    rid = json.loads(p.stdout or "{}").get("request_id", "")
    ck("a request can be logged into the served brain", bool(rid), p.stderr[-200:])

    d = s.get("/api/trigger-request", id=rid).json()
    ck("trigger-request returns it", d.get("ok") and d["request"]["id"] == rid
       and d["actions"] == [])
    ck("an unknown request is a successful read of nothing",
       s.get("/api/trigger-request", id="nope").json().get("ok") is False)
    ck("status lists it", any(r["id"] == rid for r in s.get("/api/trigger").json()["recent"]))

    a = s.post("/api/trigger-action", {"request_id": rid, "kind": "reply",
                                       "target": "https://x", "detail": "answered"})
    ck("trigger-action appends", a.status == 200 and a.json().get("ok"), str(a.raw[:120]))
    ck("and the request shows it",
       [x["kind"] for x in s.get("/api/trigger-request", id=rid).json()["actions"]] == ["reply"])
    ck("an action needs a request", refused(s.post("/api/trigger-action", {"kind": "x"}),
                                            400, "missing request_id"))
    ck("for a request that exists", refused(s.post("/api/trigger-action",
                                                   {"request_id": "nope", "kind": "x"}),
                                            400, "no request nope"))
    ck("and a kind", refused(s.post("/api/trigger-action", {"request_id": rid}),
                             400, "missing kind"))

    g = s.post("/api/trigger-gate", {"scope": "jira:PROJ-1", "enabled": True})
    ck("trigger-gate opens a conversation", g.status == 200 and g.json().get("enabled") is True)
    ck("which status then lists",
       any(x["scope"] == "jira:PROJ-1" for x in s.get("/api/trigger").json()["enabled_scopes"]))
    s.post("/api/trigger-gate", {"scope": "jira:PROJ-1", "enabled": False})
    ck("and closes it again",
       not any(x["scope"] == "jira:PROJ-1"
               for x in s.get("/api/trigger").json()["enabled_scopes"]))
    ck("enabled must be a boolean", refused(s.post("/api/trigger-gate",
                                                   {"scope": "jira:PROJ-1", "enabled": "yes"}),
                                            400, "true or false"))
    ck("a malformed scope is refused", refused(s.post("/api/trigger-gate",
                                                      {"scope": "bogus", "enabled": True}),
                                               400, "scope must look like"))


# ========================================================== read-only mode
def test_read_only():
    print("\n== --no-write refuses every write, and says so ==")
    s = Server("--no-write")
    try:
        ck("meta says so", s.get("/api/meta").json().get("writable") is False)
        ck("settings says so", s.get("/api/settings").json().get("writable") is False)
        for route in POST_ROUTES:
            r = s.post(route, {})
            if not refused(r, 403, "--no-write"):
                ck(f"{route} is refused with a 403", False, f"{r.status} {r.raw[:80]!r}")
                break
        else:
            ck("every write route is refused with a 403", True)
        ck("an unknown POST is still a 404", refused(s.post("/api/nope", {}), 404))
        ck("reads still work", s.get("/api/graph").status == 200)
        ck("and a read-only server never acts: no trigger watchers",
           s.get("/api/trigger").json().get("watching") is False)
    finally:
        s.stop()


def main():
    s = Server()
    try:
        test_envelope(s)
        test_docs(s)
        test_static(s)
        test_reads(s)
        test_task_writes(s)
        test_people_writes(s)
        test_settings_writes(s)
        test_contract_writes(s)
        test_trigger_routes(s)
    finally:
        s.stop()
    test_read_only()
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
