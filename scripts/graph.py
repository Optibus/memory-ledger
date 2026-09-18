#!/usr/bin/env python3
"""second-brain graph engine.

A tiny, dependency-free graph database over SQLite. Everything the plugin
remembers is a NODE (a person, task, requirement, decision, note, writing
sample, reminder...) and everything that connects them is an EDGE
(relates-to, blocks, part-of, assigned-to, about, depends-on, mentions...).

Skills never touch SQL directly; they call these subcommands and read the
JSON that comes back. Machine output is always JSON on stdout.

DB location: $SECOND_BRAIN_DB, else <plugin>/memory/graph.db
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import subprocess
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

def _env(name: str, default: str = "") -> str:
    """Config env lookup: SECOND_BRAIN_<name>, else the default."""
    return os.environ.get("SECOND_BRAIN_" + name) or default


# Default section (realm) for new nodes; override with $SECOND_BRAIN_REALM or
# --realm, or set it once in <home>/settings.json. Assigned below, after the
# settings reader it depends on — see `settings()`.
DEFAULT_REALM = "optibus"

# ---------------------------------------------------------------------------
# vocabulary (soft — not enforced, just documented + used for defaults)
# ---------------------------------------------------------------------------
NODE_TYPES = [
    "person", "task", "subtask", "requirement", "decision", "note",
    "project", "reminder", "writing-sample", "association", "meeting",
    "jira-issue", "resource", "skill", "area", "quote", "parked",
    # §3 also names these; they were missing here, so anything deriving the
    # vocabulary from the code (the skill tree, the UI facets) could not see them.
    "team", "channel", "rule", "spec", "process", "pr", "email", "message",
    # traceability (§2k): the agent tree, what was considered, and what was
    # discarded under a condition that may later stop holding.
    "conversation", "agent", "idea", "transition", "gauge", "context-event",
    # the skill tree (§5a): one node per node TYPE, so two skills touching the
    # same type are visibly coupled even when neither one names the other.
    "node-type",
]

# Default character budget for read paths that would otherwise return whole
# bodies. `search` used to be uncapped and returned ~9k chars where `recall`
# answered the same cue in ~1.5k — the single largest avoidable read cost.
READ_BUDGET = 2600

# An AREA is a domain hub: quotes, health, process... Content links to it with
# `part-of`, so a domain is one edge walk away instead of a full-text guess.
# Area ids are `hub_<domain>` by convention (hub_quote, hub_health, hub_process).
AREA_TYPE = "area"
AREA_REL = "part-of"

# --- The GENOME: the brain's copy of how its own Claude layer is built ---------
# The plugin ships a base layer; the DB is where it is kept, mutated and re-read.
# `rule` nodes hold the conventions (scenario-matched, so a request loads only the
# 1-3 that apply instead of the whole CONVENTIONS.md); `spec` nodes hold the
# rebuild blueprint (hooks, storage, API surface, restore steps) so ANY AI can
# reconstitute the interaction layer from the graph alone if the plugin vanishes.
RULE_TYPE = "rule"
SPEC_TYPE = "spec"
AREA_CONVENTION = "hub_convention"
AREA_GENOME = "hub_genome"
# A user-authored rule outranks the plugin's shipped default of the same key.
RULE_PRECEDENCE = {"user": 100, "learned": 50, "plugin": 0}
# Sections that bind on EVERY request, so match-rules appends them regardless of
# the cue. Keep this set tiny: each one is a fixed cost paid on every lookup.
ALWAYS_APPLY_SECTIONS = {"0", "1"}
EDGE_RELS = [
    "relates-to", "blocks", "blocked-by", "part-of", "has-subtask",
    "assigned-to", "about", "depends-on", "causes", "caused-by",
    "mentions", "authored-by", "example-of", "follows-up",
    # neuroplasticity / learned skills
    "applies-to", "learned-from", "reinforces", "triggered-by",
    # traceability (§2k): `part-of` carries the agent tree and message->agent,
    # `about` a transition to its idea, `blocked-by` an idea to the gauge that
    # held it back, `caused-by` a transition to the turn that caused it.
    "supersedes", "captured-from",
    # the skill tree (§5a). `requires` is hard; `waits-for` is optional-ordered
    # (starting before it makes this skill's output wrong); `enriched-by` is
    # optional-enriching (consume it if it landed, never wait). `reads`/`writes`
    # point at a `node-type`, which is where the real coupling shows.
    "requires", "waits-for", "enriched-by", "reads", "writes",
]

# Skill-tree edges are machine-generated from the declarations, so sync owns them
# and prunes the ones that stopped being declared. A hand-made edge never carries
# this prop and is therefore never touched.
TREE_EDGE_PROP = {"src": "skill-tree"}
TREE_HUBS = {
    "plugin": ("hub_skills_builtin", "Built-in skills — the plugin's contract corpus"),
    "learned": ("hub_skills_user", "User skills — learned in this brain at runtime"),
}

# statuses that count as "archived" — kept forever, hidden from active views
DONE_STATUSES = ("done", "cancelled")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def home_dir() -> Path:
    """The brain's home — a single global directory OUTSIDE any repo, so every
    project on the machine shares one brain (memory, learned skills, configs,
    routines, definitions). Override with $SECOND_BRAIN_HOME. Default:
    ~/.second_brain."""
    env = _env("HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".second_brain"


def db_path() -> Path:
    """The graph database file. $SECOND_BRAIN_DB overrides the exact file;
    otherwise it lives at <home>/graph.db."""
    env = _env("DB")
    if env:
        return Path(env).expanduser()
    return home_dir() / "graph.db"


def learned_skills_dir() -> Path:
    """Where runtime-learned user skills live (one folder per skill, each with
    a SKILL.md). This is the plastic layer — the brain writes new abilities here
    and records in the graph when to fire them."""
    return home_dir() / "learned_skills"


# ---------------------------------------------------------------------------
# settings: preferences that outlive one shell
#
# Env vars are per-process, so `export SECOND_BRAIN_TZ=…` only holds for the
# terminal that ran it — a cron, a hook, or the web server never sees it. This
# file is the machine-wide answer, and the web UI's Settings page edits it.
# Precedence everywhere: explicit flag > env var > this file > built-in default.
# ---------------------------------------------------------------------------
SETTING_KEYS = ["tz", "realm", "backend", "map_poll_seconds", "dash_refresh_seconds"]

# `backend` is where READS come from by default (writes always hit SQLite first
# and mirror onward, whatever this says). `auto` is the honest setting for a
# container that can be stopped: neo4j when it answers, the local mirror when it
# does not. `scripts/neo4j_local.py up` writes it.
BACKENDS = ("sqlite", "neo4j", "auto")


def settings_path() -> Path:
    return home_dir() / "settings.json"


def settings() -> dict:
    """Read the preferences file. Missing or corrupt reads as empty — a bad
    settings file must never stop the brain from answering."""
    try:
        val = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return val if isinstance(val, dict) else {}


def save_settings(patch: dict) -> dict:
    """Merge `patch` into the file and return the whole result. A value of ""
    or None REMOVES the key, which is how you go back to the env/system default
    rather than pinning an empty string."""
    cur = settings()
    for k, v in (patch or {}).items():
        if k not in SETTING_KEYS:
            continue
        if v in (None, ""):
            cur.pop(k, None)
        else:
            cur[k] = v
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cur, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return cur


def setting(name: str, default: str = "") -> str:
    """One setting, env first. Same order as everything else in the engine."""
    val = _env(name.upper())
    if val:
        return val
    val = settings().get(name)
    return str(val) if val not in (None, "") else default


DEFAULT_REALM = setting("realm", "optibus")


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40] or "node"


def connect():
    import sqlite3
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(con)
    return con


# The cue text a node is matched on, over and above its prose. `match-rules` reads
# `props.scenario`, `match-skills` reads `props.triggers` / `props.description` —
# authors write those fields precisely so a rule fires on words its body never uses.
# FTS5 external-content tables can only index real columns of the content table, so
# the JSON is surfaced as one VIRTUAL generated column and indexed like any other.
# Without it every one of those cue words is dead weight (fixed 2026-07-31).
_CUES_SQL = ("ifnull(json_extract(props,'$.scenario'),'') || ' ' || "
             "ifnull(json_extract(props,'$.triggers'),'') || ' ' || "
             "ifnull(json_extract(props,'$.description'),'')")


def _generated_cols_ok() -> bool:
    """Generated columns need SQLite 3.31 (2020). Older builds keep the
    two-column index — cue matching degrades to prose, it does not break."""
    import sqlite3
    return sqlite3.sqlite_version_info >= (3, 31)


def _ensure_fts(con) -> None:
    """Create the FTS index and its sync triggers, migrating older brains.

    Older databases have a two-column index (title, body) and triggers to match.
    When the shape changes, the index and triggers are dropped and rebuilt from the
    content table — cheap, since `nodes` is the source of truth and a personal brain
    is small. Falls back to the two-column shape on SQLite older than 3.31, which
    cannot add a generated column.
    """
    # table_xinfo, NOT table_info: the latter omits VIRTUAL generated columns, so
    # `cues` would look missing on every connect and the ALTER would fail second run.
    cols = [r[1] for r in con.execute("PRAGMA table_xinfo(nodes)").fetchall()]
    if _generated_cols_ok() and "cues" not in cols:
        con.execute("ALTER TABLE nodes ADD COLUMN cues TEXT "
                    f"GENERATED ALWAYS AS ({_CUES_SQL}) VIRTUAL")
        cols.append("cues")

    want = ["title", "body"] + (["cues"] if "cues" in cols else [])
    have = [r[1] for r in con.execute("PRAGMA table_info(nodes_fts)").fetchall()]
    if have and have != want:                      # stale shape — rebuild it
        con.executescript("DROP TRIGGER IF EXISTS nodes_ai;"
                          "DROP TRIGGER IF EXISTS nodes_ad;"
                          "DROP TRIGGER IF EXISTS nodes_au;"
                          "DROP TABLE IF EXISTS nodes_fts;")
        have = []
    cl = ", ".join(want)
    if not have:
        con.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5("
                    f"{cl}, content='nodes', content_rowid='rowid')")
        con.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")

    # Triggers keep the index in step with writes. Generated columns are readable
    # through NEW/OLD, so the same column list works here.
    new = ", ".join(f"new.{c}" for c in want)
    old = ", ".join(f"old.{c}" for c in want)
    con.executescript(f"""
        CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
            INSERT INTO nodes_fts(rowid, {cl}) VALUES (new.rowid, {new});
        END;
        CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
            INSERT INTO nodes_fts(nodes_fts, rowid, {cl})
            VALUES ('delete', old.rowid, {old});
        END;
        CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
            INSERT INTO nodes_fts(nodes_fts, rowid, {cl})
            VALUES ('delete', old.rowid, {old});
            INSERT INTO nodes_fts(rowid, {cl}) VALUES (new.rowid, {new});
        END;
    """)


def _ensure_schema(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS nodes (
            id       TEXT PRIMARY KEY,
            type     TEXT NOT NULL,
            title    TEXT NOT NULL,
            body     TEXT DEFAULT '',
            props    TEXT DEFAULT '{}',   -- JSON blob
            status   TEXT DEFAULT '',     -- e.g. open|doing|done|blocked
            due      TEXT DEFAULT '',     -- ISO date/time, optional
            created  TEXT NOT NULL,
            updated  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS edges (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            src     TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
            dst     TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
            rel     TEXT NOT NULL,
            props   TEXT DEFAULT '{}',
            created TEXT NOT NULL,
            UNIQUE(src, dst, rel)
        );
        CREATE INDEX IF NOT EXISTS idx_nodes_type   ON nodes(type);
        CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);
        CREATE INDEX IF NOT EXISTS idx_nodes_due    ON nodes(due);
        CREATE INDEX IF NOT EXISTS idx_edges_src    ON edges(src);
        CREATE INDEX IF NOT EXISTS idx_edges_dst    ON edges(dst);
        """
    )
    _ensure_fts(con)
    # --- realm (section) column + neo4j sync outbox ---
    cols = [r[1] for r in con.execute("PRAGMA table_info(nodes)").fetchall()]
    if "realm" not in cols:
        con.execute("ALTER TABLE nodes ADD COLUMN realm TEXT DEFAULT ''")
        con.execute("UPDATE nodes SET realm=? WHERE realm IS NULL OR realm=''",
                    (DEFAULT_REALM,))
    con.execute(
        "CREATE TABLE IF NOT EXISTS outbox ("
        "seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, ref TEXT, created TEXT)")
    # Hebbian tally: how often two nodes have co-activated in recall. Local only —
    # it never syncs, and an EDGE is only created once a pair repeats (see
    # HEBBIAN_THRESHOLD), so a single lexical coincidence cannot wire the graph.
    con.execute(
        "CREATE TABLE IF NOT EXISTS coactivation ("
        "a TEXT, b TEXT, n INTEGER DEFAULT 0, last TEXT, PRIMARY KEY(a,b))")
    _ensure_blocked_index(con)
    con.commit()


# --- the recheck index (§2k) -------------------------------------------------
# What is consulted BY TRIGGER lives in an indexed table; what is read in
# sequence to reconstruct lives in the log. The log here is the `transition`
# NODES (immutable, synced to the master); this table is their PROJECTION —
# derived, local, and rebuildable at any time with `recheck --rebuild`. Nothing
# writes it directly: `idea-discard` appends the transition and then projects.
#
# One row per CONDITION, so a multi-condition discard is several rows sharing a
# `source_ref`; the recheck treats a source_ref group as a conjunction.
def _ensure_blocked_index(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS blocked_item (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type   TEXT NOT NULL,      -- 'idea' today, anything tomorrow
            item_ref    TEXT NOT NULL,      -- the node held back
            gauge_key   TEXT NOT NULL,      -- catalog key (see `gauges`)
            op          TEXT NOT NULL,
            required    TEXT NOT NULL DEFAULT '',
            source_ref  TEXT NOT NULL,      -- transition node that wrote it
            created     TEXT NOT NULL,
            resolved_at TEXT NOT NULL DEFAULT '',
            UNIQUE(item_ref, gauge_key, op, required, source_ref)
        );
        CREATE INDEX IF NOT EXISTS idx_blocked_gauge
            ON blocked_item(gauge_key, resolved_at);
        CREATE INDEX IF NOT EXISTS idx_blocked_item ON blocked_item(item_ref);
        CREATE INDEX IF NOT EXISTS idx_blocked_src  ON blocked_item(source_ref);
        """
    )


# ---------------------------------------------------------------------------
# neo4j master + offline outbox
# ---------------------------------------------------------------------------
# neo4j is the unified master store. Every mutation also writes to the local
# SQLite mirror (durable + offline) and is queued in `outbox`; we push to neo4j
# immediately, and if neo4j is down the queue drains automatically on the next
# successful op or an explicit `flush`.

def _neo4j_cfg() -> dict:
    cfg = {}
    f = home_dir() / "neo4j.json"
    if f.exists():
        try:
            cfg = json.loads(f.read_text())
        except Exception:
            cfg = {}
    return {
        "url": _env("NEO4J_URL", cfg.get("url", "")),
        "db": _env("NEO4J_DB", cfg.get("db", "neo4j")),
        "user": _env("NEO4J_USER", cfg.get("user", "neo4j")),
        "password": _env("NEO4J_PASSWORD", cfg.get("password", "")),
    }


def _neo4j_call(statements, timeout=30):
    """POST Cypher to neo4j's HTTP tx endpoint. Returns (ok, error_or_None).

    NOTE: the timeout is client-side only. A request that times out here may
    still commit on the server, so callers must treat a timeout as "unknown",
    not as "did not happen" — every statement we send is idempotent for exactly
    that reason (MERGE, not CREATE).
    """
    cfg = _neo4j_cfg()
    if not cfg["url"] or not cfg["password"]:
        return False, "neo4j not configured"
    endpoint = cfg["url"].rstrip("/") + f"/db/{cfg['db']}/tx/commit"
    auth = base64.b64encode(f"{cfg['user']}:{cfg['password']}".encode()).decode()
    req = urllib.request.Request(
        endpoint, data=json.dumps({"statements": statements}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
        errs = body.get("errors") or []
        return (not errs), (errs[0].get("message") if errs else None)
    except Exception as e:
        return False, str(e)


_NODE_ID_CONSTRAINT = (
    "CREATE CONSTRAINT node_id_unique IF NOT EXISTS "
    "FOR (n:Node) REQUIRE n.id IS UNIQUE")


def _ensure_neo4j_schema() -> tuple:
    """Make :Node(id) unique on the master.

    Without this, `MERGE (x:Node {id:...})` is not safe under concurrency: two
    flushes racing on the same id each fail to find it and each create one. That
    is how 22 ids ended up duplicated (one of them three times). The constraint
    makes MERGE actually idempotent, which the whole sync design assumes.
    Cheap and idempotent, so callers can just run it."""
    return _neo4j_call([{"statement": _NODE_ID_CONSTRAINT}], timeout=30)


def _neo4j_up() -> bool:
    cfg = _neo4j_cfg()
    if not cfg["url"]:
        return False
    try:
        urllib.request.urlopen(cfg["url"].rstrip("/") + "/", timeout=3).read()
        return True
    except Exception:
        return False


def _clean_props(props: dict) -> dict:
    out = {}
    for k, v in (props or {}).items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v):
            out[k] = v
        else:
            out[k] = json.dumps(v, ensure_ascii=False)
    return out


EDGE_SEP = "\x1f"

_NODE_UPSERT = (
    "UNWIND $nodes AS n MERGE (x:Node {id:n.id}) "
    "SET x += n.props, x.realm=n.realm, x.type=n.type, x.title=n.title, "
    "x.body=n.body, x.status=n.status, x.due=n.due, x.created=n.created, x.updated=n.updated")
_EDGE_UPSERT = ("UNWIND $edges AS e MATCH (a:Node {id:e.src}),(b:Node {id:e.dst}) "
                "MERGE (a)-[r:REL {rel:e.rel}]->(b)")
_NODE_DEL = "UNWIND $ids AS i MATCH (x:Node {id:i}) DETACH DELETE x"
_EDGE_DEL = ("UNWIND $edges AS e MATCH (a:Node {id:e.src})-[r:REL {rel:e.rel}]->"
             "(b:Node {id:e.dst}) DELETE r")


def _enqueue(con, kind, ref):
    con.execute("INSERT INTO outbox(kind,ref,created) VALUES(?,?,?)", (kind, ref, now()))


def _node_payload(con, nid):
    r = con.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        props = _clean_props(json.loads(d.get("props") or "{}"))
    except Exception:
        props = {}
    return {"id": d["id"], "type": d["type"], "title": d["title"], "body": d["body"],
            "status": d["status"], "due": d["due"], "created": d["created"],
            "updated": d["updated"], "realm": d.get("realm") or DEFAULT_REALM, "props": props}


FLUSH_BATCH = 300      # ops per transaction; ~2s against the master at this size
FLUSH_TIMEOUT = 120    # generous: a timeout here wedges the queue, see _flush


def _flush_batch(con, rows):
    """Push one batch of outbox rows. Returns (ok, error, counts)."""
    node_ids, edge_refs, del_ids, del_edges = set(), set(), set(), set()
    for r in rows:
        if r["kind"] == "node":
            node_ids.add(r["ref"])
        elif r["kind"] == "edge":
            edge_refs.add(r["ref"])
        elif r["kind"] == "del_node":
            del_ids.add(r["ref"])
        elif r["kind"] == "del_edge":
            del_edges.add(r["ref"])
    nodes = [n for n in (_node_payload(con, i) for i in node_ids) if n]
    edges = []
    for ref in edge_refs:
        s, d, rel = ref.split(EDGE_SEP)
        if con.execute("SELECT 1 FROM edges WHERE src=? AND dst=? AND rel=?", (s, d, rel)).fetchone():
            edges.append({"src": s, "dst": d, "rel": rel})
    dedges = [dict(zip(("src", "dst", "rel"), ref.split(EDGE_SEP))) for ref in del_edges]
    counts = {"nodes": len(nodes), "edges": len(edges),
              "del_nodes": len(del_ids), "del_edges": len(dedges)}
    stmts = []
    if nodes:
        stmts.append({"statement": _NODE_UPSERT, "parameters": {"nodes": nodes}})
    if edges:
        stmts.append({"statement": _EDGE_UPSERT, "parameters": {"edges": edges}})
    if dedges:
        stmts.append({"statement": _EDGE_DEL, "parameters": {"edges": dedges}})
    if del_ids:
        stmts.append({"statement": _NODE_DEL, "parameters": {"ids": list(del_ids)}})
    if not stmts:
        return True, None, counts          # nothing left to push; safe to dequeue
    ok, err = _neo4j_call(stmts, timeout=FLUSH_TIMEOUT)
    return ok, err, counts


def _flush(con):
    """Drain the outbox to neo4j in bounded batches. Upserts current state of
    queued nodes/edges and applies queued deletes.

    Batching is load-bearing, not tidiness. The outbox used to be sent as ONE
    transaction: a daily consolidate pass can enqueue ~1,700 ops, that payload
    needed ~14s against the master, and the client gave up at 6s. Nothing was
    dequeued on failure, so every later write made the batch bigger and slower
    — a one-way ratchet that the queue could never escape. Bounded batches mean
    a backlog always drains, just over more round-trips.

    Each batch is deleted from the queue only after its push succeeds, so an
    interruption costs at most a replay of one batch (every statement is an
    idempotent MERGE/DELETE, so replay is safe)."""
    pushed = {"nodes": 0, "edges": 0, "del_nodes": 0, "del_edges": 0}
    batches = 0
    while True:
        rows = con.execute(
            "SELECT seq,kind,ref FROM outbox ORDER BY seq LIMIT ?", (FLUSH_BATCH,)).fetchall()
        if not rows:
            break
        ok, err, counts = _flush_batch(con, rows)
        if not ok:
            pending = con.execute("SELECT COUNT(*) c FROM outbox").fetchone()["c"]
            out = {"ok": False, "error": err, "pending": pending, "batches": batches}
            if batches:
                out["pushed"] = pushed        # partial progress is real progress
            return out
        con.executemany("DELETE FROM outbox WHERE seq=?", [(r["seq"],) for r in rows])
        con.commit()
        for k in pushed:
            pushed[k] += counts[k]
        batches += 1
    return {"ok": True, "pending": 0, "batches": batches,
            "pushed": pushed if batches else 0}


def _after_write(con, nodes=(), edges=(), del_nodes=(), del_edges=()):
    """Queue changes for neo4j and (unless suppressed) push immediately."""
    for nid in nodes:
        _enqueue(con, "node", nid)
    for e in edges:
        _enqueue(con, "edge", EDGE_SEP.join(e))
    for nid in del_nodes:
        _enqueue(con, "del_node", nid)
    for e in del_edges:
        _enqueue(con, "del_edge", EDGE_SEP.join(e))
    con.commit()
    if _env("NO_AUTOSYNC"):
        return None
    return _flush(con)


def _node_dict(row) -> dict:
    d = dict(row)
    d.pop("cues", None)   # generated FTS input, not part of the node (see _ensure_fts)
    try:
        d["props"] = json.loads(d.get("props") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["props"] = {}
    return d


def _gen_id(con, node_type: str, title: str) -> str:
    base = f"{slugify(node_type)}_{slugify(title)}"
    for _ in range(50):
        cand = f"{base}_{secrets.token_hex(2)}"
        exists = con.execute("SELECT 1 FROM nodes WHERE id=?", (cand,)).fetchone()
        if not exists:
            return cand
    return f"{base}_{secrets.token_hex(6)}"


def out(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")


def _parse_props(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {"value": val}
    except json.JSONDecodeError:
        return {"note": raw}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_init(args):
    connect().close()
    out({"ok": True, "db": str(db_path())})


def cmd_add_node(args):
    con = connect()
    area = getattr(args, "area", None)
    aid = None
    if area:
        # Resolve BEFORE creating, so a typo'd area never leaves an orphan node
        # sitting outside every domain.
        aid, err = _resolve_area(con, area)
        if err:
            out(err); return
    # A person goes through the identity guard, because this is the door the
    # ingest skills come through — the same colleague arrives under a nickname one
    # week and a full name the next, and two nodes is how their tasks get split.
    # --force-new is the escape hatch for two people who really do share a name.
    if args.type == "person" and not getattr(args, "force_new", False) and not args.id:
        res = api_create_person(con, args.title, args.body,
                                _parse_props(args.props),
                                getattr(args, "realm", None) or "")
    else:
        res = api_create_node(con, args.type, args.title, args.body,
                              _parse_props(args.props), args.status, args.due,
                              getattr(args, "realm", None), args.id)
    if aid and res.get("ok"):
        api_link(con, res["node"]["id"], aid, AREA_REL)
        res["area"] = aid
    out(res)


def cmd_people(args):
    con = connect()
    people = person_rows(con, include_superseded=getattr(args, "all", False))
    out({"ok": True, "count": len(people), "people": [
        {"id": d["id"], "title": d["title"], "realm": d.get("realm", ""),
         "aliases": person_aliases(d)[1:], "edges": _person_degree(con, d["id"]),
         "superseded_by": (d["props"] or {}).get("superseded_by", "")}
        for d in people]})


def cmd_who(args):
    """Which person node does this spelling mean? The read side of the guard."""
    out(resolve_person(connect(), args.name))


def cmd_person_dupes(args):
    con = connect()
    groups = person_duplicates(con)
    out({"ok": True, "groups": len(groups), "duplicates": groups,
         "hint": "graph.py merge-person --into <canonical> --from <duplicate>"})


def cmd_merge_person(args):
    out(api_merge_person(connect(), args.into, args.from_id))


def cmd_add_alias(args):
    out(api_add_alias(connect(), args.id, args.alias))


def cmd_update_node(args):
    con = connect()
    out(api_update_node(con, args.id, title=args.title, body=args.body,
                        status=args.status, due=args.due,
                        realm=getattr(args, "realm", None),
                        props_patch=(_parse_props(args.props)
                                     if args.props is not None else None)))


def cmd_del_node(args):
    con = connect()
    cur = con.execute("DELETE FROM nodes WHERE id=?", (args.id,))
    con.commit()
    sync = _after_write(con, del_nodes=[args.id])
    out({"ok": cur.rowcount > 0, "deleted": args.id, "sync": sync})


def cmd_add_edge(args):
    con = connect()
    out(api_link(con, args.src, args.dst, args.rel, _parse_props(args.props)))


def cmd_del_edge(args):
    con = connect()
    cur = con.execute(
        "DELETE FROM edges WHERE src=? AND dst=? AND rel=?",
        (args.src, args.dst, args.rel),
    )
    con.commit()
    sync = _after_write(con, del_edges=[(args.src, args.dst, args.rel)])
    out({"ok": cur.rowcount > 0, "sync": sync})


def _edges_of(con, nid):
    rows = con.execute(
        "SELECT e.src,e.dst,e.rel,e.props,"
        " s.title AS src_title, s.type AS src_type,"
        " d.title AS dst_title, d.type AS dst_type"
        " FROM edges e JOIN nodes s ON s.id=e.src JOIN nodes d ON d.id=e.dst"
        " WHERE e.src=? OR e.dst=?",
        (nid, nid),
    ).fetchall()
    return [dict(r) for r in rows]


def cmd_get_node(args):
    con = connect()
    row = con.execute("SELECT * FROM nodes WHERE id=?", (args.id,)).fetchone()
    if not row:
        out({"ok": False, "error": f"no node {args.id}"}); return
    out({"ok": True, "node": _node_dict(row), "edges": _edges_of(con, args.id)})


def cmd_search(args):
    con = connect()
    q = (args.query or "").strip()
    params, where = [], []
    if q:
        # quote each term for FTS to tolerate punctuation / partial words
        terms = " ".join(f'"{t}"*' for t in re.findall(r"\w+", q))
        rows = con.execute(
            "SELECT n.* FROM nodes_fts f JOIN nodes n ON n.rowid=f.rowid"
            " WHERE nodes_fts MATCH ? ORDER BY rank",
            (terms,),
        ).fetchall() if terms else []
    else:
        rows = con.execute("SELECT * FROM nodes ORDER BY updated DESC").fetchall()
    results = [_node_dict(r) for r in rows]
    if args.type:
        results = [r for r in results if r["type"] == args.type]
    results = results[: args.limit]
    if getattr(args, "full", False):
        out({"ok": True, "count": len(results), "results": results}); return
    # BUDGETED BY DEFAULT. Uncapped, this returned whole bodies — ~9,000 chars
    # where `recall` answered the same cue in ~1,500. Bodies are trimmed to share
    # the budget; read one in full with `get-node --id`.
    budget = int(getattr(args, "budget", None) or READ_BUDGET)
    # Only the props a caller routinely decides on. Emitting every prop is what
    # made the budget non-binding: props alone can outweigh the body.
    KEEP_PROPS = ("priority", "assignee", "waiting_on", "jira", "pr", "state",
                  "due", "section", "source", "attributed_to")
    slim, used, truncated = [], 0, 0
    for i, r in enumerate(results):
        room = max(160, (budget - used) // max(1, len(results) - i))
        body = r["body"] or ""
        entry = {"id": r["id"], "type": r["type"], "title": r["title"]}
        for k in ("status", "due"):
            if r.get(k):
                entry[k] = r[k]
        p = r["props"] or {}
        keep = {k: p[k] for k in KEEP_PROPS if p.get(k)}
        if keep:
            entry["props"] = keep
        entry["body"] = body[:room]
        if len(body) > room:
            entry["truncated"] = True; truncated += 1
        used += len(json.dumps(entry))
        slim.append(entry)
        if used >= budget:
            break
    out({"ok": True, "count": len(slim), "of": len(results), "chars": used,
         "budget": budget, "truncated": truncated,
         "hint": "full body: get-node --id · everything: --full",
         "results": slim})


def cmd_list(args):
    con = connect()
    where, params = [], []
    area = getattr(args, "area", None)
    if area:
        aid, err = _resolve_area(con, area)
        if err:
            out(err); return
        where.append(
            "id IN (SELECT src FROM edges WHERE dst=? AND rel=?)")
        params.extend([aid, AREA_REL])
    if args.type:
        where.append("type=?"); params.append(args.type)
    if args.status:
        where.append("status=?"); params.append(args.status)
    elif not args.all:
        # hide the archive from active views unless explicitly asked
        placeholders = ",".join("?" * len(DONE_STATUSES))
        where.append(f"status NOT IN ({placeholders})")
        params.extend(DONE_STATUSES)
    if args.due_before:
        where.append("due!='' AND due<=?"); params.append(args.due_before)
    sql = "SELECT * FROM nodes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE WHEN due='' THEN 1 ELSE 0 END, due, updated DESC"
    sql += f" LIMIT {int(args.limit)}"
    rows = con.execute(sql, params).fetchall()
    out({"ok": True, "count": len(rows), "results": [_node_dict(r) for r in rows]})


def _parse_dt(s):
    """Parse an ISO-8601 due string to an aware UTC datetime. Naive values are
    assumed UTC. Compare these, never the raw strings — mixed tz offsets make
    lexical comparison wrong (07:20-03:00 is LATER than 08:00+00:00)."""
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def cmd_due(args):
    con = connect()
    now_dt = datetime.now(timezone.utc)
    horizon_dt = now_dt + timedelta(days=args.within_days)
    rows = con.execute(
        "SELECT * FROM nodes WHERE due!='' "
        "AND status NOT IN ('done','cancelled')",
    ).fetchall()
    items = []
    for r in rows:
        d = _node_dict(r)
        due_dt = _parse_dt(d["due"])
        if due_dt is None or due_dt > horizon_dt:
            continue
        d["overdue"] = due_dt < now_dt
        d["_sort"] = due_dt
        items.append(d)
    items.sort(key=lambda x: x.pop("_sort"))
    out({"ok": True, "now": now_dt.isoformat(timespec="seconds"),
         "count": len(items), "results": items})


def _walk(con, start, depth, rel_filter):
    seen = {start}
    frontier = [start]
    collected_edges = []
    for _ in range(depth):
        nxt = []
        for nid in frontier:
            for e in _edges_of(con, nid):
                if rel_filter and e["rel"] != rel_filter:
                    continue
                collected_edges.append(e)
                other = e["dst"] if e["src"] == nid else e["src"]
                if other not in seen:
                    seen.add(other); nxt.append(other)
        frontier = nxt
        if not frontier:
            break
    nodes = []
    for nid in seen:
        row = con.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
        if row:
            nodes.append(_node_dict(row))
    # de-dup edges
    uniq = {(e["src"], e["dst"], e["rel"]): e for e in collected_edges}
    return nodes, list(uniq.values())


def cmd_neighbors(args):
    con = connect()
    if not con.execute("SELECT 1 FROM nodes WHERE id=?", (args.id,)).fetchone():
        out({"ok": False, "error": f"no node {args.id}"}); return
    nodes, edges = _walk(con, args.id, args.depth, args.rel)
    out({"ok": True, "center": args.id, "nodes": nodes, "edges": edges})


def cmd_stats(args):
    con = connect()
    by_type = {r["type"]: r["c"] for r in con.execute(
        "SELECT type, COUNT(*) c FROM nodes GROUP BY type").fetchall()}
    ntot = con.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"]
    etot = con.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"]
    out({"ok": True, "nodes": ntot, "edges": etot, "by_type": by_type,
         "db": str(db_path())})


def cmd_export(args):
    con = connect()
    nodes = [_node_dict(r) for r in con.execute(
        "SELECT * FROM nodes ORDER BY type,created").fetchall()]
    edges = [dict(r) for r in con.execute(
        "SELECT src,dst,rel,props,created FROM edges").fetchall()]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = Path(args.out).expanduser() if args.out else (
        db_path().parent / "exports" / stamp)
    base.mkdir(parents=True, exist_ok=True)

    if args.format in ("json", "both"):
        (base / "nodes.json").write_text(
            json.dumps(nodes, indent=2, ensure_ascii=False))
        (base / "edges.json").write_text(
            json.dumps(edges, indent=2, ensure_ascii=False))
    if args.format in ("md", "both"):
        adj = {}
        for e in edges:
            adj.setdefault(e["src"], []).append((e["rel"], e["dst"]))
            adj.setdefault(e["dst"], []).append(("(" + e["rel"] + ")", e["src"]))
        lines = ["# second-brain memory export", f"_generated {now()}_", ""]
        for n in nodes:
            lines.append(f"## {n['title']}  `{n['id']}`")
            meta = [f"type: {n['type']}"]
            if n["status"]:
                meta.append(f"status: {n['status']}")
            if n["due"]:
                meta.append(f"due: {n['due']}")
            lines.append("- " + " · ".join(meta))
            if n["body"]:
                lines.append("")
                lines.append(n["body"])
            links = adj.get(n["id"], [])
            if links:
                lines.append("")
                lines.append("Links: " + ", ".join(
                    f"{rel} [[{dst}]]" for rel, dst in links))
            lines.append("")
        (base / "memory.md").write_text("\n".join(lines))
    out({"ok": True, "exported_to": str(base), "nodes": len(nodes),
         "edges": len(edges), "format": args.format})


# ---------------------------------------------------------------------------
# completion / archive  (done work is moved, never deleted)
# ---------------------------------------------------------------------------
def cmd_complete(args):
    con = connect()
    out(api_complete(con, args.id, args.note))


def cmd_done(args):
    con = connect()
    rows = con.execute(
        "SELECT * FROM nodes WHERE status='done' ORDER BY updated DESC LIMIT ?",
        (int(args.limit),)).fetchall()
    out({"ok": True, "count": len(rows), "results": [_node_dict(r) for r in rows]})


# ---------------------------------------------------------------------------
# learned skills  (neuroplasticity: the brain grows + strengthens abilities)
# ---------------------------------------------------------------------------
_SKILL_TEMPLATE = """---
name: {name}
description: {title}
---

# {title}

**When to use (triggers):** {when}

{body}

---
_Learned skill — created at runtime by second-brain on {date}. The graph node
`{node_id}` tracks how often it fires and reinforces the trigger over time._
"""


def cmd_add_skill(args):
    """Create a runtime-learned skill: write learned_skills/<name>/SKILL.md AND
    a `skill` node whose body/triggers make it findable by `match-skills`."""
    con = connect()
    name = slugify(args.name)
    when = args.when or ""
    body = args.body or ""
    node_id = args.id or f"skill_{name}"
    # body carries the triggers so full-text match surfaces the skill by situation
    node_body = f"WHEN TO USE: {when}\n\n{body}"
    skill_path = learned_skills_dir() / name / "SKILL.md"

    existing = con.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    ts = now()
    if existing:
        cur = _node_dict(existing)
        props = {**cur["props"], "triggers": when, "path": str(skill_path),
                 "kind": cur["props"].get("kind") or "learned",
                 **_skill_contract_props(args),
                 "updated_at": ts}
        con.execute("UPDATE nodes SET title=?, body=?, props=?, updated=? WHERE id=?",
                    (args.title, node_body, json.dumps(props), ts, node_id))
    else:
        props = {"triggers": when, "path": str(skill_path), "kind": "learned",
                 **_skill_contract_props(args),
                 "usage_count": 0, "confidence": 1.0, "learned_at": ts}
        con.execute(
            "INSERT INTO nodes(id,type,title,body,props,status,due,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (node_id, "skill", args.title, node_body, json.dumps(props),
             "active", "", ts, ts))
    con.commit()
    sync = _after_write(con, nodes=[node_id])

    # Node-first: a learned process lives in the DB (portable, synced everywhere).
    # A markdown file is written ONLY when explicitly asked for (--with-file) — the
    # DB is the source of truth, the file is just an optional local export.
    wrote_file = False
    if args.with_file:
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(_SKILL_TEMPLATE.format(
            name=name, title=args.title, when=when or "(describe when to use)",
            body=body or "(steps)", date=ts, node_id=node_id))
        wrote_file = True

    row = con.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    out({"ok": True, "skill": _node_dict(row), "stored": "db",
         "skill_file": str(skill_path) if wrote_file else None})


def cmd_match_skills(args):
    """Given a situation, return learned skills whose triggers/body match, most
    reinforced first — this is the brain 'recognizing' a situation it has
    learned to handle."""
    con = connect()
    q = (args.query or "").strip()
    if q:
        # OR the cue words (ignore tiny stopwords) so a PARTIAL cue can trigger
        # recall — a situation need only overlap the skill's triggers, not match
        # every word. This is the associative-recall behaviour of the brain.
        words = [t for t in re.findall(r"\w+", q.lower()) if len(t) > 2]
        terms = " OR ".join(f'"{t}"*' for t in words)
        rows = con.execute(
            "SELECT n.* FROM nodes_fts f JOIN nodes n ON n.rowid=f.rowid"
            " WHERE nodes_fts MATCH ? AND n.type='skill' ORDER BY rank",
            (terms,)).fetchall() if terms else []
        results = [_node_dict(r) for r in rows]  # already in FTS relevance order
        # Rank by RELEVANCE first (FTS order), with reinforcement as a bounded
        # booster: each prior use nudges a skill up, capped, so a strongly-learned
        # skill edges ahead of equally-relevant peers but a weak match can never
        # outrank a strong one. (Before: pure usage sort buried the best match.)
        WEIGHT, CAP = 0.5, 6
        for i, r in enumerate(results):
            uses = int(r["props"].get("usage_count", 0) or 0)
            r["_score"] = i - min(uses, CAP) * WEIGHT
        results.sort(key=lambda r: r["_score"])
        for r in results:
            r.pop("_score", None)
    else:
        # no cue -> nothing to be relevant to; surface the most-reinforced skills
        rows = con.execute(
            "SELECT * FROM nodes WHERE type='skill' ORDER BY updated DESC").fetchall()
        results = [_node_dict(r) for r in rows]
        results.sort(key=lambda r: r["props"].get("usage_count", 0), reverse=True)
    results = results[: args.limit]
    # TOKEN COST: this runs on EVERY routed request (CONVENTIONS §3), and the full
    # procedure bodies made it ~8.1k chars. The caller only needs to pick a skill
    # here — it reads the procedure with `get-node --id` once it has. So return a
    # cue-sized row by default; `--full` restores the old payload.
    if not getattr(args, "full", False):
        slim = []
        for r in results:
            p = r["props"]
            slim.append({"id": r["id"], "title": r["title"],
                         "triggers": (p.get("triggers") or "")[:220],
                         "uses": int(p.get("usage_count", 0) or 0),
                         "strength": _strength(p),
                         "read_procedure": f"get-node --id {r['id']}"})
        results = slim
    con2 = connect()
    _touch(con2, [r["id"] for r in results])   # recognising a skill reinforces it
    out({"ok": True, "count": len(results), "results": results})


def _plugin_skills_dir() -> Path:
    """The plugin's authored skills live beside the engine (../skills)."""
    return Path(__file__).resolve().parent.parent / "skills"


def _parse_frontmatter(text: str):
    """Split a `---`-delimited YAML-ish frontmatter block from the body. Returns
    (meta, body). Values are single-line `key: value` pairs (enough for skills)."""
    meta, body = {}, text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.lstrip().startswith("#"):
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = m.group(2)
    return meta, body


def _extract_triggers(meta: dict, body: str) -> str:
    """Best-effort 'when to use' cue for a skill: an explicit When-to-use line,
    else a 'Use ...' clause in the description, else the whole description."""
    m = re.search(r"(?im)^\**\s*when to use[^\n:]*:\**\s*(.+)$", body)
    if m and m.group(1).strip():
        return m.group(1).strip()
    m = re.search(r"(?im)^\s*##+\s*when to use\s*\n+(.+?)(?:\n\s*\n|\Z)", body, re.DOTALL)
    if m and m.group(1).strip():
        return " ".join(m.group(1).split())
    desc = meta.get("description", "")
    m = re.search(r"(Use (?:for|when|this)[^.]*\.)", desc)
    return (m.group(1) if m else desc).strip()


def _ingest_skill_file(con, path: Path, kind: str, realm: str):
    """Upsert one SKILL.md as a `skill` node — triggers + full procedure land in
    the DB (portable, synced), so the skill is no longer isolated in a file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_frontmatter(text)
    name = slugify(meta.get("name") or path.parent.name or path.stem)
    title = meta.get("name") or name
    triggers = _extract_triggers(meta, body)
    node_id = f"skill_{name}"
    node_body = f"WHEN TO USE: {triggers}\n\n{body.strip()}"
    ts = now()
    existing = con.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if existing:
        cur = _node_dict(existing)
        props = {**cur["props"], "triggers": triggers, "kind": kind,
                 "source_path": str(path), "description": meta.get("description", ""),
                 "ingested_at": ts}
        con.execute("UPDATE nodes SET title=?, body=?, props=?, realm=?, updated=? WHERE id=?",
                    (title, node_body, json.dumps(props),
                     cur.get("realm") or realm, ts, node_id))
        action = "updated"
    else:
        props = {"triggers": triggers, "kind": kind, "source_path": str(path),
                 "description": meta.get("description", ""), "usage_count": 0,
                 "confidence": 1.0, "learned_at": ts, "ingested_at": ts}
        con.execute(
            "INSERT INTO nodes(id,type,title,body,props,status,due,realm,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (node_id, "skill", title, node_body, json.dumps(props),
             "active", "", realm, ts, ts))
        action = "created"
    return node_id, action


def _skill_contract_props(args) -> dict:
    """The §5a declarations a user skill carries in props, since it has no file."""
    keys = ("requires", "waits_for", "enriched_by", "reads", "writes")
    return {k: getattr(args, k) for k in keys if getattr(args, k, None)}


def _parse_depends_on(text: str) -> dict:
    """Read a SKILL.md `## Depends on` block into the declarations §5a defines.

    The file is the source of truth for a built-in skill: the same block the
    contract checker validates is the one the tree is built from, so the document,
    the skill and the graph cannot drift apart."""
    m = re.search(r"^## Depends on\s*$(.*?)(?=^## )", text, re.M | re.S)
    if not m:
        return {}
    block = m.group(1)
    kinds = {"Requires": "requires", "Optional (ordered)": "waits-for",
             "Optional (enriching)": "enriched-by",
             "Reads": "reads", "Writes": "writes"}
    found = {}
    for line in block.splitlines():
        lm = re.match(r"\s*-\s*\*\*([^:*]+):\*\*(.*)", line)
        if not lm:
            continue
        label, rest = lm.group(1).strip(), lm.group(2)
        rel = kinds.get(label)
        if not rel:
            continue
        if re.match(r"\s*none\b", rest, re.I):
            found.setdefault(rel, [])
            continue
        found.setdefault(rel, []).extend(re.findall(r"`([^`]+)`", rest))
    return found


def _skill_declarations(con, row) -> tuple:
    """(kind, declarations) for one skill node.

    A built-in skill declares in its SKILL.md and the file wins, because the
    checker gates that file at commit time. A user skill has no file, so it
    declares in props (`add-skill --requires/--reads/--writes`)."""
    props = json.loads(row["props"] or "{}")
    kind = props.get("kind")
    path = props.get("source_path") or props.get("path") or ""
    if not kind:
        kind = "plugin" if "/skills/" in path and path.endswith("SKILL.md") else "learned"
    decl = {}
    if kind == "plugin" and path and Path(path).exists():
        try:
            decl = _parse_depends_on(Path(path).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            decl = {}
    if not decl:
        decl = {rel: [t.strip() for t in str(props.get(key, "")).split(",") if t.strip()]
                for rel, key in (("requires", "requires"), ("waits-for", "waits_for"),
                                 ("enriched-by", "enriched_by"), ("reads", "reads"),
                                 ("writes", "writes"))}
    return kind, decl


def skill_trees(con, with_rules: bool = True) -> list:
    """The two skill trees as the UI draws them (CONVENTIONS §5a).

    Built from the SAME edges `sync-skill-tree` wrote, never from a second pass
    over the files, so the page cannot show a tree the graph does not hold. Run
    the sync first if a declaration changed."""
    skills = {r["id"]: _node_dict(r) for r in con.execute(
        "SELECT * FROM nodes WHERE type='skill' ORDER BY title").fetchall()}
    edges = [dict(r) for r in con.execute(
        "SELECT src,dst,rel,props FROM edges WHERE src IN "
        "(SELECT id FROM nodes WHERE type='skill')").fetchall()]
    tree_edges = [e for e in edges
                  if json.loads(e["props"] or "{}").get("src") == "skill-tree"]

    hub_of, dep, io = {}, {}, {}
    for e in tree_edges:
        if e["rel"] == "part-of" and e["dst"].startswith("hub_skills_"):
            hub_of[e["src"]] = e["dst"]
        elif e["rel"] in ("requires", "waits-for", "enriched-by"):
            dep.setdefault(e["src"], []).append((e["rel"], e["dst"]))
        elif e["rel"] in ("reads", "writes"):
            io.setdefault(e["src"], []).append((e["rel"], e["dst"][len("nodetype_"):]))

    def entry(sid):
        n = skills[sid]
        p = n["props"]
        d = sorted(dep.get(sid, []))
        row = {
            "id": sid, "title": n["title"],
            "description": p.get("description") or "",
            "triggers": p.get("triggers") or "",
            "kind": p.get("kind") or "learned",
            "uses": int(p.get("usage_count", 0) or 0),
            "requires": [x for r, x in d if r == "requires"],
            "waits_for": [x for r, x in d if r == "waits-for"],
            "enriched_by": [x for r, x in d if r == "enriched-by"],
            "reads": sorted({t for r, t in io.get(sid, []) if r == "reads"}),
            "writes": sorted({t for r, t in io.get(sid, []) if r == "writes"}),
            "reads_other": p.get("reads_other", ""),
            "writes_other": p.get("writes_other", ""),
            "declared": False, "rules": [],
        }
        if with_rules:
            # Two different questions, both worth answering. DECLARED is what the
            # skill says it is accountable for. MATCHED is what match-rules will
            # actually hand it at runtime, from THIS brain — which is where a
            # person's own rules show up against a skill that never named them.
            named = re.findall(r"rule_[a-z0-9_-]+", n["body"] or "")
            have = {r["id"]: r for r in (
                _node_dict(x) for x in con.execute(
                    "SELECT * FROM nodes WHERE type=?", (RULE_TYPE,)).fetchall())}
            seen = []
            for rid in dict.fromkeys(named):
                if rid in have:
                    seen.append({"id": rid, "title": have[rid]["title"],
                                 "source": have[rid]["props"].get("source", ""),
                                 "non_negotiable": bool(
                                     have[rid]["props"].get("non_negotiable")),
                                 "why": "declared"})
            row["declared"] = bool(seen)
            cue = " ".join(filter(None, [n["title"], row["triggers"][:400]]))
            for r in rules_for(con, cue, 6):
                if r["id"] in {x["id"] for x in seen}:
                    continue
                nn = bool(r["props"].get("non_negotiable"))
                # A non-negotiable is appended to EVERY match, so calling it
                # "matched" here would be a lie that buries the handful of rules
                # this skill actually pulled. It gets its own bucket.
                seen.append({"id": r["id"], "title": r["title"],
                             "source": r["props"].get("source", ""),
                             "non_negotiable": nn,
                             "why": "always" if nn else "matched"})
            row["rules"] = seen
        return row

    trees = []
    for kind, (hub_id, hub_title) in TREE_HUBS.items():
        members = [s for s in skills if hub_of.get(s) == hub_id]
        rows = {s: entry(s) for s in members}
        # Nest a skill under the one it REQUIRES. Requires is the only hard edge,
        # so it is the only one that makes a hierarchy rather than a cross-link;
        # waits-for and enriched-by stay listed on the card, where they read as
        # what they are — relationships, not parentage.
        children = {}
        for sid, row in rows.items():
            parent = next((r for r in row["requires"] if r in rows), None)
            children.setdefault(parent, []).append(sid)
        def branch(sid):
            node = rows[sid]
            node["children"] = [branch(c) for c in
                                sorted(children.get(sid, []),
                                       key=lambda x: rows[x]["title"].lower())]
            return node
        roots = sorted(children.get(None, []), key=lambda x: rows[x]["title"].lower())
        trees.append({"kind": kind, "hub": hub_id, "title": hub_title,
                      "count": len(members), "skills": [branch(r) for r in roots]})
    return trees


def cmd_sync_skill_tree(args):
    """Project the skill contracts into the graph as two trees (CONVENTIONS §5a).

    Built-in skills hang off `hub_skills_builtin` and declare in their SKILL.md;
    user skills hang off `hub_skills_user` and declare in props. Idempotent, and
    a true sync: edges this command previously wrote that are no longer declared
    are removed, so the graph cannot keep asserting a dependency the corpus
    dropped. Hand-made edges carry no marker and are never touched."""
    con = connect()
    ts = now()
    realm = getattr(args, "realm", None) or "shared"
    want, hubs, types_seen, unresolved = set(), {}, set(), {}
    trees = {"plugin": [], "learned": []}

    rows = con.execute("SELECT * FROM nodes WHERE type='skill' ORDER BY id").fetchall()
    if not rows:
        out({"ok": False, "error": "no skill nodes — run ingest-skills first"}); return

    for kind, (hub_id, hub_title) in TREE_HUBS.items():
        if not con.execute("SELECT 1 FROM nodes WHERE id=?", (hub_id,)).fetchone():
            api_create_node(con, AREA_TYPE, hub_title, "", {"domain": hub_id[4:]},
                            "", "", realm, hub_id)
        hubs[kind] = hub_id

    known_types = set(NODE_TYPES)
    for row in rows:
        sid = row["id"]
        kind, decl = _skill_declarations(con, row)
        trees[kind if kind in trees else "learned"].append(sid)
        want.add((sid, hubs.get(kind, hubs["learned"]), "part-of"))

        for rel in ("requires", "waits-for", "enriched-by"):
            for token in decl.get(rel, []):
                dep = token if token.startswith("skill_") else f"skill_{slugify(token)}"
                if con.execute("SELECT 1 FROM nodes WHERE id=? AND type='skill'",
                               (dep,)).fetchone() and dep != sid:
                    want.add((sid, dep, rel))
                else:
                    unresolved.setdefault(sid, []).append(f"{rel}:{token}")

        for rel in ("reads", "writes"):
            others = []
            for token in decl.get(rel, []):
                t = token.strip().lstrip("`").rstrip("`")
                if t in known_types and not args.no_node_types:
                    tid = f"nodetype_{t}"
                    if tid not in types_seen:
                        if not con.execute("SELECT 1 FROM nodes WHERE id=?", (tid,)).fetchone():
                            api_create_node(
                                con, "node-type", t,
                                f"A node type in the memory graph. Its edges show every "
                                f"skill that reads or writes it (CONVENTIONS §5a).",
                                {"node_type": t}, "", "", realm, tid)
                        types_seen.add(tid)
                    want.add((sid, tid, rel))
                else:
                    others.append(t)
            if others:
                props = json.loads(row["props"] or "{}")
                props[f"{rel.replace('-', '_')}_other"] = ", ".join(others)
                con.execute("UPDATE nodes SET props=?, updated=? WHERE id=?",
                            (json.dumps(props), ts, sid))

    have = {(r["src"], r["dst"], r["rel"]) for r in con.execute(
        "SELECT src,dst,rel,props FROM edges").fetchall()
        if json.loads(r["props"] or "{}").get("src") == "skill-tree"}

    added = [e for e in sorted(want) if e not in have]
    for src, dst, rel in added:
        api_link(con, src, dst, rel, dict(TREE_EDGE_PROP))
    pruned = [e for e in sorted(have) if e not in want]
    for src, dst, rel in pruned:
        con.execute("DELETE FROM edges WHERE src=? AND dst=? AND rel=?", (src, dst, rel))
    con.commit()
    sync = _after_write(con, del_edges=pruned) if pruned else None

    out({"ok": True,
         "trees": {"builtin": {"hub": hubs["plugin"], "skills": len(trees["plugin"])},
                   "user": {"hub": hubs["learned"], "skills": len(trees["learned"])}},
         "node_types": len(types_seen), "edges": len(want),
         "added": len(added), "pruned": len(pruned),
         "unresolved": unresolved or None, "sync": sync})


def cmd_ingest_skills(args):
    """Pull markdown skills into the brain as nodes so processes live in the DB
    (and sync everywhere), not as isolated files. Idempotent: re-run any time to
    refresh. Scans --dir (repeatable); defaults to the plugin skills + the
    runtime learned_skills tree."""
    con = connect()
    dirs = [Path(d).expanduser() for d in (args.dir or [])]
    if not dirs:
        dirs = [_plugin_skills_dir(), learned_skills_dir()]
    kind_for = {str(_plugin_skills_dir()): "plugin",
                str(learned_skills_dir()): "learned"}
    results, touched = [], []
    for d in dirs:
        if not d.exists():
            continue
        kind = kind_for.get(str(d), args.kind or "plugin")
        for path in sorted(d.rglob("SKILL.md")) + (
                sorted(d.rglob("*.md")) if args.all_md else []):
            if path.name != "SKILL.md" and not args.all_md:
                continue
            nid, action = _ingest_skill_file(con, path, kind, args.realm)
            touched.append(nid)
            results.append({"id": nid, "action": action, "kind": kind,
                            "path": str(path)})
    con.commit()
    sync = _after_write(con, nodes=touched) if touched else None
    out({"ok": True, "count": len(results), "ingested": results, "sync": sync})


def _plugin_root() -> Path:
    """The plugin checkout that ships the base layer (engine lives in ./scripts)."""
    return Path(__file__).resolve().parent.parent


def _conventions_path() -> Path:
    return _plugin_root() / "CONVENTIONS.md"


_SECTION_RE = re.compile(r"^##\s+(?P<num>[0-9]+[a-z]?)\.\s+(?P<title>.+?)\s*$", re.M)


def _parse_conventions_sections(text: str) -> list:
    """Split CONVENTIONS.md into its `## N. Title` sections.

    Section granularity is deliberate. The doc is already organised by concern, so
    one node per section is lossless and honest, and it is the unit that pays off at
    read time: a request loads the 1-3 sections that apply instead of 400+ lines.
    Finer rules are then added by hand with `add-rule` — that is the mutable part.
    """
    out_sections = []
    matches = list(_SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        # Trailing horizontal rules are separators, not content.
        body = re.sub(r"\n-{3,}\s*$", "", body).strip()
        if not body:
            continue
        out_sections.append({"section": m.group("num"),
                             "title": m.group("title").strip(),
                             "body": body})
    return out_sections


def _scenario_terms(title: str, body: str) -> str:
    """Cue words for scenario matching: the heading, plus the bolded lead-ins the
    doc uses to name each rule. Cheap, and refinable by the user afterwards."""
    bolds = re.findall(r"\*\*(.+?)\*\*", body)
    cues = [title] + [b.strip(" .:—-") for b in bolds[:14]]
    seen, keep = set(), []
    for c in cues:
        c = re.sub(r"[`*]", "", c).strip()
        if 2 < len(c) < 90 and c.lower() not in seen:
            seen.add(c.lower()); keep.append(c)
    return " · ".join(keep)


def cmd_ingest_conventions(args):
    """Load CONVENTIONS.md into the brain as `rule` nodes, one per section.

    Idempotent and NON-DESTRUCTIVE: a rule the user has authored or edited is never
    overwritten by the shipped text — it is reported as `kept`. That is what lets
    the plugin ship a base layer while the user redefines the brain on top of it.
    """
    con = connect()
    path = Path(args.file).expanduser() if args.file else _conventions_path()
    if not path.exists():
        out({"ok": False, "error": f"no conventions file at {path}"}); return
    realm = getattr(args, "realm", None) or "shared"
    aid, err = _resolve_area(con, AREA_CONVENTION)
    if err:
        out(err); return
    sections = _parse_conventions_sections(path.read_text(encoding="utf-8", errors="replace"))
    ts = now()
    results, touched = [], []
    for sec in sections:
        nid = f"rule_s{sec['section']}_{slugify(sec['title'])[:48]}"
        existing = con.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
        scenario = _scenario_terms(sec["title"], sec["body"])
        # Only the UNIVERSALLY applicable sections are forced into every answer.
        # The doc labels three sections "(non-negotiable)", but §4 (keep docs in
        # sync) is conditional — it binds when you change behaviour, not on every
        # request — so title-matching alone over-forces. Curate explicitly instead.
        forced = sec["section"] in ALWAYS_APPLY_SECTIONS
        if existing:
            cur = _node_dict(existing)
            src = cur["props"].get("source", "plugin")
            if src != "plugin" or cur["props"].get("user_edited"):
                results.append({"id": nid, "action": "kept", "source": src})
                continue
            props = {**cur["props"], "section": sec["section"], "scenario": scenario,
                     "source": "plugin", "precedence": RULE_PRECEDENCE["plugin"],
                     "ingested_at": ts, "source_path": str(path)}
            if forced:
                props["non_negotiable"] = True
            con.execute("UPDATE nodes SET title=?, body=?, props=?, updated=? WHERE id=?",
                        (sec["title"], sec["body"], json.dumps(props), ts, nid))
            results.append({"id": nid, "action": "updated", "source": "plugin"})
        else:
            props = {"section": sec["section"], "scenario": scenario,
                     "source": "plugin", "precedence": RULE_PRECEDENCE["plugin"],
                     "ingested_at": ts, "source_path": str(path)}
            if forced:
                props["non_negotiable"] = True
            con.execute(
                "INSERT INTO nodes(id,type,title,body,props,status,due,realm,created,updated)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (nid, RULE_TYPE, sec["title"], sec["body"], json.dumps(props),
                 "active", "", realm, ts, ts))
            results.append({"id": nid, "action": "created", "source": "plugin"})
        touched.append(nid)
        con.execute("INSERT OR IGNORE INTO edges(src,dst,rel,props,created)"
                    " VALUES(?,?,?,?,?)", (nid, aid, AREA_REL, "{}", ts))
    con.commit()
    sync = _after_write(con, nodes=touched) if touched else None
    out({"ok": True, "count": len(results), "file": str(path),
         "area": aid, "rules": results, "sync": sync})


def cmd_add_rule(args):
    """Author or override a convention rule. This is the neuroplastic half: a
    user rule outranks the plugin default it names via --overrides."""
    con = connect()
    aid, err = _resolve_area(con, AREA_CONVENTION)
    if err:
        out(err); return
    source = args.source or "user"
    props = {"section": args.section or "user",
             "scenario": args.scenario or args.title,
             "source": source,
             "precedence": RULE_PRECEDENCE.get(source, RULE_PRECEDENCE["user"]),
             "authored_at": now()}
    if args.non_negotiable:
        props["non_negotiable"] = True
    if args.overrides:
        props["overrides"] = args.overrides
    nid = args.id or f"rule_{source}_{slugify(args.title)[:48]}"
    res = api_create_node(con, RULE_TYPE, args.title, args.body or "", props,
                          "active", "", getattr(args, "realm", None) or "shared", nid)
    if not res.get("ok"):
        out(res); return
    api_link(con, nid, aid, AREA_REL)
    if args.overrides:
        # Retire the shipped rule rather than deleting it — history stays, and
        # match-rules stops surfacing both sides of a contradiction.
        api_update_node(con, args.overrides, status="superseded",
                        props_patch={"superseded_by": nid})
        api_link(con, nid, args.overrides, "follows-up")
    res["area"] = aid
    out(res)


def rules_for(con, query: str, limit: int = 8) -> list:
    """The rules that apply to a situation, in the order they should be read.

    Pure FTS + SQL, no model call. Extracted from cmd_match_rules so anything that
    needs to know which rules govern an action — the CLI, the web UI's skill
    view — asks the same function and cannot disagree with it."""
    q = (query or "").strip()
    hits = []
    if q:
        words = [t for t in re.findall(r"\w+", q.lower()) if len(t) > 2]
        terms = " OR ".join(f'"{t}"*' for t in words)
        if terms:
            hits = con.execute(
                "SELECT n.* FROM nodes_fts f JOIN nodes n ON n.rowid=f.rowid"
                " WHERE nodes_fts MATCH ? AND n.type=? AND n.status!='superseded'"
                " ORDER BY rank", (terms, RULE_TYPE)).fetchall()
    matched = [_node_dict(r) for r in hits]  # already in FTS relevance order
    # Precedence is a BOUNDED BOOSTER on relevance, never a re-sort: a user rule
    # edges ahead of an equally-relevant plugin default, but a barely-relevant user
    # rule must not outrank a rule that actually matches. (Same shape as
    # match-skills' reinforcement booster.)
    for i, r in enumerate(matched):
        prec = int(r["props"].get("precedence", 0) or 0)
        r["_score"] = i - (1.5 if prec >= RULE_PRECEDENCE["user"] else
                           0.75 if prec >= RULE_PRECEDENCE["learned"] else 0)
    matched.sort(key=lambda r: r["_score"])
    for r in matched:
        r.pop("_score", None)
    matched = matched[:limit]
    # Universally-applicable rules are APPENDED, never counted against the limit —
    # otherwise a handful of them fills every slot and the rule you actually asked
    # for never appears (which is exactly what happened before this).
    seen = {r["id"] for r in matched}
    forced = [_node_dict(r) for r in con.execute(
        "SELECT * FROM nodes WHERE type=? AND status!='superseded'"
        " AND json_extract(props,'$.non_negotiable')=1"
        " ORDER BY json_extract(props,'$.section')", (RULE_TYPE,)).fetchall()
        if r["id"] not in seen]
    return matched + forced


def cmd_match_rules(args):
    """Which conventions apply to THIS situation — the token-light replacement for
    reading all of CONVENTIONS.md. Non-negotiable rules are always included, then
    relevance order, then the payload is trimmed to a character budget so this can
    run on every request."""
    con = connect()
    results = rules_for(con, args.query, args.limit)
    if getattr(args, "full", False):
        out({"ok": True, "count": len(results), "results": results}); return
    budget = int(args.budget or 2600)
    slim, used = [], 0
    for r in results:
        p = r["props"]
        room = max(240, (budget - used) // max(1, len(results) - len(slim)))
        entry = {"id": r["id"], "section": p.get("section", ""),
                 "title": r["title"], "source": p.get("source", ""),
                 "rule": r["body"][:room]}
        if p.get("non_negotiable"):
            entry["non_negotiable"] = True
        if len(r["body"]) > room:
            entry["truncated"] = True
        used += len(entry["rule"])
        slim.append(entry)
        if used >= budget:
            break
    out({"ok": True, "count": len(slim), "budget": budget, "chars": used,
         "hint": "read a full rule with get-node --id", "results": slim})


def cmd_use_skill(args):
    """Reinforce a skill after it fires — strengthen the pathway (usage_count up,
    confidence nudged toward 1, last_used stamped)."""
    con = connect()
    row = con.execute("SELECT * FROM nodes WHERE id=? AND type='skill'",
                      (args.id,)).fetchone()
    if not row:
        out({"ok": False, "error": f"no skill {args.id}"}); return
    cur = _node_dict(row)
    p = cur["props"]
    p["usage_count"] = int(p.get("usage_count", 0)) + 1
    p["last_used"] = now()
    p["confidence"] = round(min(1.0, float(p.get("confidence", 0.5)) + 0.1), 3)
    con.execute("UPDATE nodes SET props=?, updated=? WHERE id=?",
                (json.dumps(p), now(), args.id))
    con.commit()
    _after_write(con, nodes=[args.id])
    row = con.execute("SELECT * FROM nodes WHERE id=?", (args.id,)).fetchone()
    out({"ok": True, "skill": _node_dict(row)})


# ---------------------------------------------------------------------------
# link recognition — "what is this text already connected to?"
# ---------------------------------------------------------------------------
# Given free text (a task title you are typing), find the nodes it mentions and
# infer HOW they relate from the wording around each mention. Pure regex + a
# scan of the node table: no model, no tokens. Same engine the web UI calls as
# you type, so the UI needs no intelligence of its own.
#
# Cue -> relation, most specific first. The relation always reads
# "<the text> --rel--> <the mentioned node>", so the reciprocal side is implied
# (brain_query._relations derives badges from both ends).
_LINK_CUES = [
    ("blocked-by",  r"(?:is\s+)?blocked\s+(?:by|on)|waiting\s+(?:on|for)"
                    r"|held\s+up\s+by|gated\s+(?:on|by)|stuck\s+(?:on|behind)"
                    r"|pending\b|behind\b"),
    ("blocks",      r"(?:this\s+)?blocks\b|is\s+blocking\b|blocking\b|unblocks\b"),
    ("caused-by",   r"caused\s+by|because\s+of|due\s+to|regression\s+(?:from|of)"
                    r"|introduced\s+(?:by|in)|broken\s+by|broke\s+in|fallout\s+(?:from|of)"),
    ("causes",      r"causes?\b|causing\b|breaks?\b|breaking\b|leads?\s+to"
                    r"|results?\s+in|regresses\b"),
    ("depends-on",  r"depends?\s+on|depending\s+on|requires?\b|needs?\b|relies\s+on"),
    ("has-subtask", r"has\s+subtasks?|includes?\b|contains?\b|breaks?\s+down\s+into"),
    ("part-of",     r"part\s+of|subtask\s+of|belongs\s+to|under\b|within\b|inside\b"),
    ("follows-up",  r"follow(?:s|ing)?[\s-]?ups?\s+(?:on|with)|follow\s+up\s+(?:on|with)"
                    r"|after\b|once\b"),
    ("assigned-to", r"assign(?:ed)?\s+to|delegate[dsn]?\s+to|hand(?:ed)?\s+off\s+to"
                    r"|owner\s*[:=]"),
    ("about",       r"about\b|regarding\b|\bre\s*:|context\s+for"),
    ("mentions",    r"\bwith\b|\bcc\b|\bfyi\b"),
]
_LINK_CUES = [(rel, re.compile(r"(?:" + pat + r")[\s:,\-–—]*$")) for rel, pat in _LINK_CUES]

# how far back from a mention we look for a cue word
_CUE_WINDOW = 40

# Filler that commonly sits between the cue and the mention — "blocked by *PR*
# #7366", "waiting on *the ticket* OS-82651". Stripped from the tail of the
# window before cue matching. Deliberately contains NO prepositions or verbs:
# cues like "depends on" / "caused by" / "part of" end in one, and eating it
# would stop them matching.
_MENTION_FILLER = re.compile(
    r"(?:\b(?:pr|mr|pull\s+request|ticket|issue|task|bug|card|node|epic|story|item"
    r"|the|a|an)\b|[\s:,\-–—]+)+$")


def _mention_keys(d: dict) -> set:
    """Distinctive strings that, appearing in free text, mean THIS node.

    Deliberately narrow: a whole title (only if long enough to be unambiguous),
    plus the identifiers people actually type — ticket keys (OS-84566), PR/issue
    numbers (#7366), channel handles (#cord-devs), and a person's first name.
    """
    title = (d.get("title") or "").strip().lower()
    if not title:
        return set()
    keys = set()
    if len(title) >= 6:
        keys.add(title)
    keys.update(re.findall(r"\b[a-z]{2,5}-\d{1,6}\b", title))   # OS-84566, MS-33
    keys.update(re.findall(r"#\d{2,6}", title))                  # #7366
    if title.startswith("#") and len(title) > 3:                 # #cord-devs
        keys.add(title)
    if d.get("type") == "person":
        for part in re.split(r"\s+", title):
            if len(part) >= 3:
                keys.add(part)                                   # first/last name
    return keys


def _find_mention(text_low: str, key: str) -> int:
    """Index of `key` in text as a whole token, or -1. Boundary-aware so 'os-8'
    does not match inside 'os-84566' and a name does not match mid-word."""
    m = re.search(r"(?<![\w#-])" + re.escape(key) + r"(?![\w-])", text_low)
    return m.start() if m else -1


def suggest_links(con, text: str, limit: int = 8, exclude: str = "") -> list:
    """Nodes this text mentions, each with the relation its wording implies."""
    text = (text or "").strip()
    if len(text) < 3:
        return []
    low = text.lower()
    rows = con.execute("SELECT id,type,title,status FROM nodes").fetchall()
    hits = {}
    for row in rows:
        d = dict(row)
        if d["id"] == exclude:
            continue
        best = -1
        matched = ""
        for key in _mention_keys(d):
            idx = _find_mention(low, key)
            # prefer the longest key that matches (most specific mention)
            if idx >= 0 and len(key) > len(matched):
                best, matched = idx, key
        if best < 0:
            continue
        before = _MENTION_FILLER.sub("", low[max(0, best - _CUE_WINDOW):best])
        after = low[best + len(matched):best + len(matched) + 12]
        rel, cue = "", ""
        for candidate, pat in _LINK_CUES:
            m = pat.search(before)
            if m:
                rel, cue = candidate, m.group(0).strip(" :,-–—")
                break
        if not rel:
            # no cue: a person is mentioned, anything else merely relates
            rel = "mentions" if d["type"] == "person" else "relates-to"
        # "needs X first" / "requires X first" is a block, not a dependency
        if rel == "depends-on" and re.match(r"\s+first\b", after):
            rel, cue = "blocked-by", (cue + " … first").strip()
        hit = {"id": d["id"], "type": d["type"], "title": d["title"],
               "status": d["status"] or "", "rel": rel, "cue": cue,
               "matched": matched, "at": best,
               # how much of this node's OWN title the mention accounts for —
               # "#7366" is most of the PR's title but incidental in a task that
               # merely quotes it
               "cover": len(matched) / max(1, len(d["title"] or ""))}
        prev = hits.get(d["id"])
        if prev is None or len(matched) > len(prev["matched"]):
            hits[d["id"]] = hit

    # One node per distinct mention. Several nodes can match the SAME text at the
    # SAME position (a task whose title quotes "PR #7366" collides with the PR).
    # Keep the one the identifier most belongs to, so the suggestion list is not
    # padded with everything that ever referenced the ticket.
    by_span = {}
    for h in hits.values():
        key = (h["matched"], h["at"])
        best_so_far = by_span.get(key)
        if best_so_far is None or h["cover"] > best_so_far["cover"]:
            by_span[key] = h
    out_ = sorted(by_span.values(), key=lambda h: (h["at"], -len(h["matched"])))
    for h in out_:
        h.pop("cover", None)
    return out_[:limit]


def cmd_suggest_links(args):
    con = connect()
    res = suggest_links(con, args.text, int(args.limit), args.exclude or "")
    out({"ok": True, "count": len(res), "text": args.text, "suggestions": res})


# ---------------------------------------------------------------------------
# person identity — one human, one node
# ---------------------------------------------------------------------------
# The same colleague arrives through many doors: a Slack display name, an email
# local part, a first name in a meeting transcript, a surname in a Jira comment.
# Left alone that produces "Isa", "Isadora" and "Isadora Martini" as three people,
# which quietly splits their tasks three ways.
#
# Two tiers, because the cost of the two mistakes is not the same. Merging two
# real people is much worse than leaving a duplicate: it silently reassigns work.
# So CERTAIN is narrow enough to act on unattended, LIKELY only ever resolves when
# exactly one candidate fits, and anything ambiguous is handed back for a human to
# decide rather than guessed at.
#
# Nothing here rewrites a name that was already written down (§ the rename rule):
# a merge leaves the duplicate node in place as a resolver pointing at the
# canonical one, and props.assignee keeps whatever string it was given. Identity
# is resolved when READ, so old captures stay honest about their wording.
_NAME_NOISE = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "the"}


def norm_name(raw: str) -> str:
    """Casefold, strip accents, drop punctuation, collapse whitespace.
    'Isadora  MARTINI' / 'isadora martini' / 'Isadóra Martini' all agree."""
    s = unicodedata.normalize("NFKD", (raw or "").strip())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s@.]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def name_parts(raw: str) -> list:
    """A name reduced to comparable parts. An email becomes its local part split
    on the usual separators, so isadora.martini@optibus.com -> [isadora, martini]."""
    s = norm_name(raw)
    if "@" in s:
        s = s.split("@", 1)[0]
    parts = [p for p in re.split(r"[\s._-]+", s) if len(p) >= 2 and p not in _NAME_NOISE]
    return parts


def name_match(a: str, b: str) -> str:
    """How confidently two names denote one person: 'certain' | 'likely' | ''.

    The load-bearing rule is the last one: two names that BOTH carry a surname and
    disagree on it are different people, however much the first name matches.
    Without it 'Gabriel Nori' and 'Gabriel Santos' collapse into one.
    """
    pa, pb = name_parts(a), name_parts(b)
    if not pa or not pb:
        return ""
    if pa == pb:
        return "certain"
    sa, sb = set(pa), set(pb)
    # one side is a bare single name — "Isa", or "Martini"
    if len(pa) == 1 or len(pb) == 1:
        short = (pa if len(pa) == 1 else pb)[0]
        long_ = pb if len(pa) == 1 else pa
        if short in set(long_):
            return "likely"                      # a first name or a surname on its own
        # a nickname clipped off the front: isa -> isadora, gab -> gabriel
        if len(short) >= 3 and any(p.startswith(short) for p in long_):
            return "likely"
        return ""
    # both sides carry at least two parts
    if sa == sb:
        return "certain"                         # "Martini, Isadora" — same parts, reordered
    if pa[0] == pb[0] and pa[-1] == pb[-1]:
        return "certain"                         # middle name added or dropped
    # Deliberately NOT matching on a shared surname plus a matching first initial.
    # It reads as a rule for "I. Martini", but that already reduces to a single
    # token and is caught above; the only thing it actually catches is
    # "Gabriel Nori" vs "Gabriela Nori" — two different people whose work would
    # then be silently pooled. A duplicate node is much cheaper than that.
    return ""


def person_aliases(d: dict) -> list:
    """Every spelling a person node answers to: pinned aliases plus its title.
    props.aliases is the human's override for what the heuristic cannot infer —
    a nickname sharing no letters with the name, a maiden name, a handle."""
    raw = (d.get("props") or {}).get("aliases") or []
    if isinstance(raw, str):
        raw = [p for p in re.split(r"\s*[,;]\s*", raw) if p.strip()]
    names = [d.get("title") or ""]
    names += [str(x) for x in raw if str(x).strip()]
    email = (d.get("props") or {}).get("email") or ""
    if email:
        names.append(str(email))
    return [n for n in names if n.strip()]


def person_rows(con, include_superseded=False) -> list:
    """Every person node, newest write last. Superseded nodes are resolvers left
    behind by a merge — real history, but not people to offer in a picker."""
    rows = con.execute(
        "SELECT * FROM nodes WHERE type='person' ORDER BY created"
    ).fetchall()
    out_ = []
    for r in rows:
        d = _node_dict(r)
        if not include_superseded and (d["props"] or {}).get("superseded_by"):
            continue
        out_.append(d)
    return out_


def resolve_person(con, name: str) -> dict:
    """Find the ONE person node `name` denotes.

    Returns {"ok": True, "id": ...} on a confident single answer;
    {"ok": False, "candidates": [...]} when the name is ambiguous or unknown, so
    the caller can ask instead of guessing. A merged-away name still resolves,
    via the alias the merge recorded on the canonical node.
    """
    if not (name or "").strip():
        return {"ok": False, "error": "missing name", "candidates": []}
    certain, likely = [], []
    for d in person_rows(con):
        best = ""
        for alias in person_aliases(d):
            m = name_match(name, alias)
            if m == "certain":
                best = "certain"
                break
            if m == "likely":
                best = best or "likely"
        if best == "certain":
            certain.append(d)
        elif best == "likely":
            likely.append(d)
    pool = certain or likely
    brief = [{"id": d["id"], "title": d["title"],
              "aliases": person_aliases(d)[1:]} for d in pool]
    if len(pool) == 1:
        return {"ok": True, "id": pool[0]["id"], "title": pool[0]["title"],
                "confidence": "certain" if certain else "likely"}
    if not pool:
        return {"ok": False, "error": f"no person matches {name!r}", "candidates": []}
    return {"ok": False, "error": f"{name!r} is ambiguous — {len(pool)} people match",
            "candidates": brief}


def person_duplicates(con) -> list:
    """Groups of person nodes that look like one human. Read-only: this reports,
    it never merges, because the ambiguous cases need a human."""
    people = person_rows(con)
    seen, groups = set(), []
    for i, a in enumerate(people):
        if a["id"] in seen:
            continue
        group = [a]
        for b in people[i + 1:]:
            if b["id"] in seen:
                continue
            if _best_match(a, b):
                group.append(b)
        if len(group) > 1:
            for g in group:
                seen.add(g["id"])
            # the most connected node is the best canonical: it carries the history,
            # so merging into it moves the fewest edges
            ranked = sorted(group, key=lambda d: -_person_degree(con, d["id"]))
            keep = ranked[0]
            groups.append({
                "canonical": {"id": keep["id"], "title": keep["title"],
                              "edges": _person_degree(con, keep["id"])},
                # scored against the CANONICAL, not against whichever node happened
                # to anchor the scan — ranking reorders the group, so anything else
                # mislabels the pair that is actually going to be merged
                "duplicates": [{"id": d["id"], "title": d["title"],
                                "edges": _person_degree(con, d["id"]),
                                "confidence": _best_match(keep, d) or "likely"}
                               for d in ranked[1:]],
            })
    return groups


def _best_match(a: dict, b: dict) -> str:
    """Strongest verdict over every pair of spellings the two nodes answer to."""
    best = ""
    for na in person_aliases(a):
        for nb in person_aliases(b):
            m = name_match(na, nb)
            if m == "certain":
                return "certain"
            if m == "likely":
                best = "likely"
    return best


def _person_degree(con, nid: str) -> int:
    row = con.execute(
        "SELECT (SELECT COUNT(*) FROM edges WHERE src=?) +"
        "       (SELECT COUNT(*) FROM edges WHERE dst=?) AS n", (nid, nid)).fetchone()
    return (dict(row) or {}).get("n", 0) or 0


def api_add_alias(con, person_id: str, alias: str) -> dict:
    """Pin a spelling onto a person, for what the heuristic cannot infer."""
    row = con.execute("SELECT * FROM nodes WHERE id=?", (person_id,)).fetchone()
    if not row:
        return {"ok": False, "error": f"no node {person_id}"}
    d = _node_dict(row)
    alias = (alias or "").strip()
    if not alias:
        return {"ok": False, "error": "missing alias"}
    have = (d["props"] or {}).get("aliases") or []
    if isinstance(have, str):
        have = [p for p in re.split(r"\s*[,;]\s*", have) if p.strip()]
    if norm_name(alias) == norm_name(d["title"]) or \
       any(norm_name(alias) == norm_name(x) for x in have):
        return {"ok": True, "node": d, "added": False}   # already answers to it
    res = api_update_node(con, person_id, props_patch={"aliases": have + [alias]})
    if res.get("ok"):
        res["added"] = True
    return res


def api_merge_person(con, canonical_id: str, duplicate_id: str) -> dict:
    """Fold a duplicate person into the canonical one, without losing the wording.

    Edges move to the canonical node, the duplicate's name is pinned there as an
    alias, and the duplicate stays in the graph as a RESOLVER: superseded_by plus
    an edge pointing at its replacement. Nothing is deleted and no already-written
    name is rewritten, so an old capture that said "Isa" still says "Isa" and still
    resolves to the right human.
    """
    if canonical_id == duplicate_id:
        return {"ok": False, "error": "cannot merge a node into itself"}
    keep = con.execute("SELECT * FROM nodes WHERE id=?", (canonical_id,)).fetchone()
    drop = con.execute("SELECT * FROM nodes WHERE id=?", (duplicate_id,)).fetchone()
    if not keep:
        return {"ok": False, "error": f"no node {canonical_id}"}
    if not drop:
        return {"ok": False, "error": f"no node {duplicate_id}"}
    keep_d, drop_d = _node_dict(keep), _node_dict(drop)
    for d in (keep_d, drop_d):
        if d["type"] != "person":
            return {"ok": False, "error": f"{d['id']} is a {d['type']}, not a person"}
    if (drop_d["props"] or {}).get("superseded_by"):
        return {"ok": False, "error": f"{duplicate_id} is already merged away"}

    moved, dropped = 0, 0
    for e in con.execute("SELECT src,dst,rel FROM edges WHERE src=? OR dst=?",
                         (duplicate_id, duplicate_id)).fetchall():
        src, dst, rel = e["src"], e["dst"], e["rel"]
        nsrc = canonical_id if src == duplicate_id else src
        ndst = canonical_id if dst == duplicate_id else dst
        con.execute("DELETE FROM edges WHERE src=? AND dst=? AND rel=?", (src, dst, rel))
        if nsrc == ndst:
            dropped += 1            # the merge made this edge a self-loop
            continue
        try:
            con.execute("INSERT INTO edges(src,dst,rel,props,created) VALUES(?,?,?,?,?)",
                        (nsrc, ndst, rel, "{}", now()))
            moved += 1
        except Exception:
            dropped += 1            # canonical already had it — UNIQUE(src,dst,rel)
    con.commit()

    api_add_alias(con, canonical_id, drop_d["title"])
    for extra in person_aliases(drop_d)[1:]:
        api_add_alias(con, canonical_id, extra)
    api_update_node(con, duplicate_id,
                    props_patch={"superseded_by": canonical_id,
                                 "superseded_at": now()})
    api_link(con, duplicate_id, canonical_id, "relates-to")
    _after_write(con, nodes=[canonical_id, duplicate_id])
    row = con.execute("SELECT * FROM nodes WHERE id=?", (canonical_id,)).fetchone()
    return {"ok": True, "canonical": _node_dict(row),
            "merged": duplicate_id, "edges_moved": moved,
            "edges_deduped": dropped}


def api_create_person(con, title: str, body="", props=None, realm="") -> dict:
    """Create a person, unless one already exists under any spelling.

    The guard is the whole point: it returns the EXISTING node instead of a second
    one, so the graph converges on one node per human even when the name arrives
    differently each time. An ambiguous name is refused with its candidates rather
    than resolved by coin flip.
    """
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "missing title"}
    hit = resolve_person(con, title)
    if hit.get("ok"):
        row = con.execute("SELECT * FROM nodes WHERE id=?", (hit["id"],)).fetchone()
        res = {"ok": True, "node": _node_dict(row), "created": False,
               "matched": hit.get("confidence")}
        # the incoming spelling is new information — record it, do not overwrite
        if norm_name(title) != norm_name(res["node"]["title"]):
            api_add_alias(con, hit["id"], title)
            row = con.execute("SELECT * FROM nodes WHERE id=?", (hit["id"],)).fetchone()
            res["node"] = _node_dict(row)
            res["alias_added"] = title
        return res
    if hit.get("candidates"):
        return {"ok": False, "error": hit["error"], "candidates": hit["candidates"],
                "hint": "merge them or pass an explicit alias"}
    res = api_create_node(con, "person", title, body, props or {}, realm=realm)
    if res.get("ok"):
        res["created"] = True
    return res


# ---------------------------------------------------------------------------
# write API — one implementation, shared by the CLI and the web UI
# ---------------------------------------------------------------------------
# These return plain dicts (no printing) so viz/serve.py can call them directly
# and stay in lockstep with the CLI: same validation, same neo4j sync.
TASK_STATUSES = ["open", "doing", "blocked", "waiting", "done", "cancelled"]


def api_create_node(con, node_type, title, body="", props=None, status="",
                    due="", realm="", node_id=""):
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "missing title"}
    # NODE_TYPES is a SOFT vocabulary (§3) — the live graph legitimately holds
    # channel/team/process/capture/pr nodes that are not in it. Callers that
    # need a closed set (e.g. the web UI) enforce it at their own boundary.
    if not (node_type or "").strip():
        return {"ok": False, "error": "missing type"}
    nid = node_id or _gen_id(con, node_type, title)
    ts = now()
    con.execute(
        "INSERT INTO nodes(id,type,title,body,props,status,due,realm,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (nid, node_type, title, body or "", json.dumps(props or {}),
         status or "", due or "", realm or DEFAULT_REALM, ts, ts))
    con.commit()
    sync = _after_write(con, nodes=[nid])
    row = con.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
    return {"ok": True, "node": _node_dict(row), "sync": sync}


def api_update_node(con, node_id, *, title=None, body=None, status=None,
                    due=None, realm=None, props_patch=None):
    row = con.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not row:
        return {"ok": False, "error": f"no node {node_id}"}
    cur = _node_dict(row)
    props = cur["props"]
    if props_patch:
        props = {**props, **props_patch}
    con.execute(
        "UPDATE nodes SET title=?,body=?,props=?,status=?,due=?,realm=?,updated=? WHERE id=?",
        (cur["title"] if title is None else title,
         cur["body"] if body is None else body,
         json.dumps(props),
         cur["status"] if status is None else status,
         cur["due"] if due is None else due,
         realm or cur.get("realm") or DEFAULT_REALM,
         now(), node_id))
    con.commit()
    sync = _after_write(con, nodes=[node_id])
    row = con.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    return {"ok": True, "node": _node_dict(row), "sync": sync}


def api_complete(con, node_id, note=""):
    """Archive, never delete (CONVENTIONS §2a): status=done + completed_at."""
    row = con.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not row:
        return {"ok": False, "error": f"no node {node_id}"}
    patch = {"completed_at": now()}
    if note:
        patch["completion_note"] = note
    res = api_update_node(con, node_id, status="done", props_patch=patch)
    if res.get("ok"):
        res["archived"] = True
    return res


def api_set_status(con, node_id, status, waiting_on=None):
    """Move a node's state. Mirrors graph set-state: stamps state_since so
    age_days stays honest, and mirrors onto status for tasks."""
    if status not in TASK_STATUSES:
        return {"ok": False, "error": f"unknown status: {status}"}
    if status == "done":
        return api_complete(con, node_id)
    patch = {"state": status, "state_since": now()}
    if waiting_on is not None:
        patch["waiting_on"] = waiting_on
    return api_update_node(con, node_id, status=status, props_patch=patch)


def api_link(con, src, dst, rel, props=None):
    # EDGE_RELS is also soft — hooks legitimately write `captured-from`. Closed
    # sets are enforced by the caller (see viz/serve.py).
    if not (rel or "").strip():
        return {"ok": False, "error": "missing relation"}
    if src == dst:
        return {"ok": False, "error": "cannot link a node to itself"}
    for end in (src, dst):
        if not con.execute("SELECT 1 FROM nodes WHERE id=?", (end,)).fetchone():
            return {"ok": False, "error": f"no node {end}"}
    try:
        con.execute("INSERT INTO edges(src,dst,rel,props,created) VALUES(?,?,?,?,?)",
                    (src, dst, rel, json.dumps(props or {}), now()))
        con.commit()
    except Exception:
        pass   # UNIQUE(src,dst,rel) -> idempotent
    sync = _after_write(con, edges=[(src, dst, rel)])
    return {"ok": True, "edge": {"src": src, "dst": dst, "rel": rel}, "sync": sync}


def api_unlink(con, src, dst, rel):
    cur = con.execute("DELETE FROM edges WHERE src=? AND dst=? AND rel=?",
                      (src, dst, rel))
    con.commit()
    sync = _after_write(con, del_edges=[(src, dst, rel)])
    return {"ok": cur.rowcount > 0, "removed": cur.rowcount,
            "edge": {"src": src, "dst": dst, "rel": rel}, "sync": sync}


# ---------------------------------------------------------------------------
# memory dynamics — strength, decay, spreading activation, Hebbian wiring
# ---------------------------------------------------------------------------
# A brain does not keep every trace at equal weight, and it does not retrieve by
# string match. Three properties we mirror here, all in pure SQL/regex (no model,
# so recall stays free):
#
#   strength   every node carries access_count/last_accessed; strength RISES with
#              use and DECAYS with disuse, so what has proven useful ranks first.
#   activation recall seeds on full-text, then spreads across edges with distance
#              decay — asking about a ticket also surfaces its PR, its blocker
#              and the person it is assigned to.
#   Hebbian    nodes that activate together get wired together, so the graph
#              densifies itself instead of relying on manual linking.
#
# Everything below is also a TOKEN optimisation: better ranking means the caller
# needs fewer results, and payloads are slimmed to a snippet instead of whole
# bodies (see _slim). match-skills alone drops ~8.1k chars -> ~0.6k.

ACTIVATION_DECAY = 0.45        # activation retained per edge hop
ACTIVATION_DEPTH = 2           # how far activation spreads
STRENGTH_HALF_LIFE = 21.0      # days for an untouched memory to halve
RECALL_BUDGET = 1600           # default char budget for a recall payload
SNIPPET = 150                  # chars of body context returned per hit
HEBBIAN_TOP = 3                # how many co-activated nodes may wire together
HEBBIAN_MAX_NEW = 2            # cap new edges per recall, so it never hairballs
HEBBIAN_THRESHOLD = 3          # co-activations required before an edge forms
# Hubs, and things that should be linked by intent rather than correlation:
# a person/channel co-activates with everything, and skills carry deliberate
# applies-to / learned-from edges (CONVENTIONS §2b).
HEBBIAN_SKIP_TYPES = {"skill", "channel", "person", "capture"}


def _days_since(iso: str) -> float:
    if not iso:
        return 999.0
    try:
        then = datetime.fromisoformat(iso)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - then).total_seconds() / 86400.0)
    except Exception:
        return 999.0


def _strength(props: dict) -> float:
    """0..1 memory strength. Repetition raises the ceiling, disuse decays toward
    it — the forgetting curve, not a hard expiry. Nothing is ever deleted."""
    uses = int(props.get("access_count", 0) or 0)
    ceiling = min(1.0, 0.25 + 0.18 * (uses ** 0.5))
    age = _days_since(props.get("last_accessed") or props.get("updated") or "")
    return round(ceiling * (0.5 ** (age / STRENGTH_HALF_LIFE)), 4)


def _touch(con, ids) -> None:
    """Reinforce on READ — reading is what strengthens a memory. Deliberately
    local-only: it does NOT enqueue a neo4j sync, because a read storm must not
    become a write storm. Strength rides along on the node's next real write."""
    ts = now()
    for nid in set(i for i in ids if i):
        row = con.execute("SELECT props FROM nodes WHERE id=?", (nid,)).fetchone()
        if not row:
            continue
        try:
            p = json.loads(row["props"] or "{}")
        except Exception:
            p = {}
        p["access_count"] = int(p.get("access_count", 0) or 0) + 1
        p["last_accessed"] = ts
        p["strength"] = _strength(p)
        con.execute("UPDATE nodes SET props=? WHERE id=?", (json.dumps(p), nid))
    con.commit()


def _excerpt(body: str, terms, width: int = SNIPPET) -> str:
    """A snippet around the first matching term — not the whole body. This is
    where most of the token saving comes from."""
    body = re.sub(r"\s+", " ", body or "").strip()
    if len(body) <= width:
        return body
    low = body.lower()
    at = -1
    for t in terms or []:
        at = low.find(t.lower())
        if at >= 0:
            break
    if at < 0:
        return body[:width].rstrip() + "…"
    start = max(0, at - width // 3)
    return ("…" if start else "") + body[start:start + width].rstrip() + "…"


def _slim(d: dict, terms=(), snippet=True) -> dict:
    """Compact form of a node for machine reading: identity, state, and a snippet
    — never the full body or the whole props blob."""
    p = d.get("props") or {}
    out_ = {"id": d["id"], "type": d["type"], "title": d["title"]}
    for k in ("status", "due", "realm"):
        if d.get(k):
            out_[k] = d[k]
    for k in ("priority", "assignee", "waiting_on", "delegated"):
        if p.get(k):
            out_[k] = p[k]
    if snippet and d.get("body"):
        out_["snippet"] = _excerpt(d["body"], terms)
    return out_


# Function words carry no cue value, and each one widens the activation sweep for
# nothing. Bilingual because this brain is driven in English and pt-BR.
_CUE_STOP = {
    "the","and","for","was","were","are","did","does","what","when","who","why","how",
    "with","from","that","this","these","those","have","has","had","will","would",
    "can","could","should","about","into","over","you","your","yours","our","ours",
    "his","her","its","their","them","they","not","but","any","all","some","more",
    "just","only","then","than","also","been","being","get","got","let","ask","asked",
    "que","com","para","por","dos","das","uma","uns","umas","nao","não","sim","meu",
    "minha","seu","sua","dele","dela","isso","esse","essa","aquilo","como","quando",
    "quem","onde","porque","porqu","tem","ter","tinha","fez","fazer","foi","era",
    "pra","pro","nos","nas","aos","mais","mas","ele","ela","eles","elas","voce","você",
}


def _fts_terms(q: str):
    words = [t for t in re.findall(r"\w+", (q or "").lower()) if len(t) > 2]
    kept = [t for t in words if t not in _CUE_STOP]
    return kept or words          # never end up with an empty cue


def cmd_recall(args):
    """Associative recall: seed on full-text, spread activation across edges,
    modulate by memory strength, return a SLIM ranked payload inside a char
    budget. One call replaces search + neighbors, and costs fewer tokens."""
    con = connect()
    q = args.query or ""
    words = _fts_terms(q)
    seeds = {}
    if words:
        terms = " OR ".join(f'"{w}"*' for w in words)
        try:
            rows = con.execute(
                "SELECT n.* FROM nodes_fts f JOIN nodes n ON n.rowid=f.rowid"
                " WHERE nodes_fts MATCH ? ORDER BY rank LIMIT 40", (terms,)).fetchall()
        except Exception:
            rows = []
        for i, r in enumerate(rows):
            seeds[r["id"]] = 1.0 / (1.0 + i)

    # Spreading activation, summing convergent paths (evidence adds up).
    #
    # Activation is normalised by FAN-OUT: a node divides what it passes among its
    # neighbours instead of handing each one a full share. Without this, hubs win
    # everything — the owner's `person` node has ~80 edges, so it collects
    # activation from every seed and then re-broadcasts it, and a query for a
    # ticket comes back as a list of people. Dividing by degree keeps a hub's
    # individual links weak while a sparse, specific link stays strong.
    act = dict(seeds)
    frontier = dict(seeds)
    for _ in range(max(0, int(args.depth))):
        nxt = {}
        for nid, a in frontier.items():
            nbrs = con.execute(
                "SELECT dst AS other FROM edges WHERE src=?"
                " UNION ALL SELECT src AS other FROM edges WHERE dst=?",
                (nid, nid)).fetchall()
            if not nbrs:
                continue
            share = a * ACTIVATION_DECAY / float(len(nbrs))
            if share < 0.005:
                continue
            for e in nbrs:
                nxt[e["other"]] = nxt.get(e["other"], 0.0) + share
                act[e["other"]] = act.get(e["other"], 0.0) + share
        frontier = nxt
        if not frontier:
            break
    if not act:
        out({"ok": True, "query": q, "count": 0, "results": []}); return

    ids = list(act)
    ph = ",".join("?" * len(ids))
    nodes = {r["id"]: _node_dict(r) for r in
             con.execute(f"SELECT * FROM nodes WHERE id IN ({ph})", ids).fetchall()}

    # degree of everything we might return, for the ubiquity penalty below
    deg = {r["id"]: r["n"] for r in con.execute(
        f"SELECT id, (SELECT COUNT(*) FROM edges e WHERE e.src=nodes.id OR e.dst=nodes.id)"
        f" AS n FROM nodes WHERE id IN ({ph})", ids).fetchall()}

    import math
    scored = []
    for nid, a in act.items():
        d = nodes.get(nid)
        if not d:
            continue
        if d["type"] == "capture" and not args.include_captures:
            continue          # the day's digest covers these (see `consolidate`)
        if d["type"] == "context-event":
            continue          # bookkeeping, never prose — read it with `context-at`
        if not args.all and (d.get("status") or "").lower() in DONE_STATUSES:
            continue
        s = _strength(d["props"])
        score = a * (0.55 + 0.45 * s)
        # A node linked to everything (the owner's own person node, a hub channel)
        # is structurally uninformative — it answers every query and distinguishes
        # none. Penalise ubiquity, but only when the node was merely ASSOCIATED;
        # a lexical hit earned its rank.
        if nid not in seeds:
            score /= (1.0 + math.log2(1.0 + deg.get(nid, 0)))
        row = _slim(d, words)
        row["score"] = round(score, 4)
        row["why"] = "matched" if nid in seeds else "associated"
        scored.append((score, row))
    scored.sort(key=lambda x: -x[0])

    results, used = [], 0
    for _, row in scored:
        cost = len(json.dumps(row, ensure_ascii=False))
        if len(results) >= int(args.limit) or (used + cost) > int(args.budget):
            break
        results.append(row); used += cost

    top = [r["id"] for r in results]
    _touch(con, top)                                  # reading reinforces
    wired = _hebbian(con, top) if args.wire else []
    out({"ok": True, "query": q, "count": len(results), "payload_chars": used,
         "activated": len(act), "wired": wired, "results": results})


def _hebbian(con, ids) -> list:
    """Fire together, wire together — but only after a pattern REPEATS.

    Each recall tallies co-activation; an edge appears only once a pair has fired
    together HEBBIAN_THRESHOLD times. That is closer to real potentiation than
    wiring on first co-occurrence, and it stops lexical coincidences (a ticket
    about "export" briefly co-activating the export-memory skill) from becoming
    permanent structure. Skills, channels and people are excluded: they are hubs,
    or they get linked deliberately (CONVENTIONS §2b), not by correlation.
    """
    made = []
    if len(ids) < 2:
        return made
    types = {}
    ph = ",".join("?" * len(ids))
    for r in con.execute(f"SELECT id,type FROM nodes WHERE id IN ({ph})", ids):
        types[r["id"]] = r["type"]
    top = [i for i in ids[:HEBBIAN_TOP]
           if i and types.get(i) not in HEBBIAN_SKIP_TYPES]
    ts = now()
    for x, y in [(p, q) for i, p in enumerate(top) for q in top[i + 1:]]:
        a, b = sorted((x, y))
        con.execute(
            "INSERT INTO coactivation(a,b,n,last) VALUES(?,?,1,?)"
            " ON CONFLICT(a,b) DO UPDATE SET n=n+1, last=excluded.last", (a, b, ts))
        n = con.execute("SELECT n FROM coactivation WHERE a=? AND b=?",
                        (a, b)).fetchone()["n"]
        # Note the parenthesisation: AND binds tighter than OR, so the pair test
        # must be grouped or the rel filter only applies to one direction.
        existing = con.execute(
            "SELECT rel, props FROM edges"
            " WHERE ((src=? AND dst=?) OR (src=? AND dst=?))", (a, b, b, a)).fetchall()
        if existing:
            # Already associated by ANY relation (has-pr, blocks, part-of…) — the
            # association exists, so do not pile a redundant relates-to on top.
            # If it is our own Hebbian edge, keep its weight current.
            for e in existing:
                if e["rel"] != "relates-to":
                    continue
                try:
                    pr = json.loads(e["props"] or "{}")
                except Exception:
                    pr = {}
                if pr.get("hebbian"):
                    pr["weight"] = n
                    pr["last_fired"] = ts
                    con.execute(
                        "UPDATE edges SET props=? WHERE rel='relates-to'"
                        " AND ((src=? AND dst=?) OR (src=? AND dst=?))",
                        (json.dumps(pr), a, b, b, a))
            continue
        if n < HEBBIAN_THRESHOLD or len(made) >= HEBBIAN_MAX_NEW:
            continue                                    # not yet a pattern
        try:
            con.execute(
                "INSERT INTO edges(src,dst,rel,props,created) VALUES(?,?,?,?,?)",
                (a, b, "relates-to",
                 json.dumps({"hebbian": True, "weight": n, "last_fired": ts}), ts))
            made.append({"src": a, "dst": b, "rel": "relates-to", "after": n})
        except Exception:
            pass
    con.commit()
    if made:
        _after_write(con, edges=[(m["src"], m["dst"], m["rel"]) for m in made])
    return made


def cmd_resume(args):
    """Rehydrate the working thread after the context window was cleared.

    THE POINT OF THIS COMMAND: `/clear` is the cheapest thing you can do to a
    conversation — it drops the accumulated window, and every later request stops
    paying for it. What normally makes clearing expensive is that you lose your
    place. You don't, because the brain kept it. So clear often, then run this.

    Returns a SMALL brief inside a char budget: what was most recently in play,
    anything parked, and the open loops. Everything here is SQL and full-text —
    no model call. Detail is fetched on demand (`get-node`, `unpark`, `recall`),
    which is the whole trade: query the brain instead of carrying it.
    """
    con = connect()
    budget = int(args.budget or 1800)
    since = (datetime.now(timezone.utc) - timedelta(days=int(args.days or 2))).isoformat()

    # What was being worked on: recently touched, minus the machinery (captures,
    # digests, people, skills, rules) and minus work explicitly owned by someone
    # else — after a clear you want YOUR thread back, not the last thing ingested.
    owner = _env("OWNER", "Gabriel").lower()
    focus = []
    for r in con.execute(
        "SELECT * FROM nodes WHERE updated>=? AND type NOT IN"
        " ('capture','parked','channel','person','skill','rule','spec','area','team',"
        "  'conversation','agent','transition','gauge','context-event')"
        " AND id NOT LIKE 'note_digest_%' ORDER BY updated DESC LIMIT 60",
            (since,)).fetchall():
        d = _node_dict(r)
        a = str((d["props"] or {}).get("assignee", "")).strip().lower()
        if a and owner not in a and a not in ("me", "unassigned", "team"):
            continue
        focus.append(d)
        if len(focus) >= int(args.limit or 8):
            break

    parked = [_node_dict(r) for r in con.execute(
        "SELECT * FROM nodes WHERE type='parked' ORDER BY updated DESC LIMIT 6"
    ).fetchall()]

    now_dt = datetime.now(timezone.utc)
    loops = []
    for r in con.execute("SELECT * FROM nodes ORDER BY updated DESC").fetchall():
        d = _node_dict(r)
        if not _is_open_loop(d):
            continue
        due_dt = _parse_dt(d["due"]) if d["due"] else None
        d["_overdue"] = bool(due_dt and due_dt < now_dt)
        loops.append(d)
    # overdue first, then whatever was touched most recently
    loops.sort(key=lambda d: (not d["_overdue"], d.get("updated") or ""), reverse=True)
    loops.sort(key=lambda d: not d["_overdue"])

    used = 0
    def build(d, extra=""):
        line = {"id": d["id"], "type": d["type"], "title": d["title"][:96]}
        if d.get("status"):
            line["status"] = d["status"]
        p = d["props"] or {}
        for k in ("priority", "jira", "pr", "waiting_on"):
            if p.get(k):
                line[k] = p[k]
        if extra:
            line["note"] = extra
        return line

    def fits(line, cap):
        """Append only if it stays inside the cap — the budget is a promise."""
        nonlocal used
        cost = len(json.dumps(line))
        if used + cost > cap:
            return False
        used += cost
        return True

    brief = {"ok": True, "budget": budget,
             "focus": [], "parked": [], "open_loops": []}
    for d in focus:
        line = build(d)
        if not fits(line, int(budget * 0.5)):
            break
        brief["focus"].append(line)
    for d in parked:
        line = build(d, f"{(d['props'] or {}).get('chars', '?')} chars — unpark --id {d['id']}")
        if not fits(line, int(budget * 0.75)):
            break
        brief["parked"].append(line)
    for d in loops:
        line = build(d)
        if not fits(line, budget):
            break
        brief["open_loops"].append(line)
    brief["chars"] = used
    brief["counts"] = {"focus": len(brief["focus"]), "parked": len(parked),
                       "open_loops": len(loops)}
    brief["hint"] = ("clear the window freely — detail lives in the brain. "
                     "Deepen any line with: recall '<cue>' · get-node --id · unpark --id")
    out(brief)


def cmd_park(args):
    """Move a blob of context OUT of the conversation and into the brain.

    Reads the content from --body or stdin, stores it, and returns a DELIBERATELY
    TINY receipt — id, size, and the first line. That is the whole point: once the
    content is in the graph the model can drop it from working context and fetch it
    back with `unpark` (or `get-node`) only if it is actually needed again.

    Nothing is summarised or lossy here; the text is stored verbatim.
    """
    con = connect()
    body = args.body
    if body is None:
        body = sys.stdin.read() if not sys.stdin.isatty() else ""
    body = body or ""
    if not body.strip():
        out({"ok": False, "error": "nothing to park (pass --body or pipe stdin)"}); return
    aid = None
    if getattr(args, "area", None):
        aid, err = _resolve_area(con, args.area)
        if err:
            out(err); return
    lines = body.count("\n") + 1
    first = next((l.strip() for l in body.splitlines() if l.strip()), "")
    title = args.title or f"Parked context — {first[:70]}"
    props = {"kind": "parked", "chars": len(body), "lines": lines,
             "parked_at": now(), "source": args.source or "conversation"}
    res = api_create_node(con, "parked", title, body, props, "", "",
                          getattr(args, "realm", None), args.id)
    if not res.get("ok"):
        out(res); return
    nid = res["node"]["id"]
    if aid:
        api_link(con, nid, aid, AREA_REL)
    for ref in (args.about or []):
        api_link(con, nid, ref, "about")
    # The receipt is the product: small enough that parking is a net saving even
    # for a few hundred chars.
    out({"ok": True, "parked": nid, "chars": len(body), "lines": lines,
         "first_line": first[:100], "area": aid,
         "hint": f"drop it from context; read it back with: unpark --id {nid}"})


def cmd_unpark(args):
    """Read parked context back — the whole thing, or a slice of it."""
    con = connect()
    row = con.execute("SELECT * FROM nodes WHERE id=?", (args.id,)).fetchone()
    if not row:
        out({"ok": False, "error": f"no node {args.id}"}); return
    d = _node_dict(row)
    body = d["body"] or ""
    start = max(0, int(args.offset or 0))
    if args.chars:
        chunk = body[start:start + int(args.chars)]
    else:
        chunk = body[start:]
    out({"ok": True, "id": d["id"], "title": d["title"], "total_chars": len(body),
         "offset": start, "returned": len(chunk),
         "more": start + len(chunk) < len(body), "body": chunk})


def _repair_digest_edges(con):
    """Point captures AT their digest, not the other way round.

    `part-of` reads "src is part of dst", so a capture belongs to its day's
    digest. Earlier consolidation runs wrote it backwards (digest part-of capture),
    which meant a capture never led to the summary that stands in for it — so
    recall spread outward into every raw capture instead of stopping at the digest.
    """
    bad = con.execute(
        "SELECT src, dst FROM edges WHERE rel='part-of' AND src LIKE 'note_digest_%'"
    ).fetchall()
    fixed = 0
    for r in bad:
        digest, capture = r["src"], r["dst"]
        con.execute("DELETE FROM edges WHERE src=? AND dst=? AND rel='part-of'",
                    (digest, capture))
        con.execute("INSERT OR IGNORE INTO edges(src,dst,rel,props,created)"
                    " VALUES(?,?,?,?,?)", (capture, digest, "part-of", "{}", now()))
        fixed += 1
    if fixed:
        con.commit()
    return fixed


def cmd_maintain(args):
    """Brain-side upkeep: consolidate, repair, and report what it saved.

    NOT the same thing as clearing context — see `resume`/`park` for that. This
    only tidies the STORE so reads stay small as it grows.

    Safe to run on a schedule and safe to run twice. NON-DESTRUCTIVE (§2a) —
    it consolidates and re-links, it never deletes. What it reduces is the SIZE OF
    A READ: once a day's captures hang off one digest, recall surfaces the digest
    instead of walking into every raw turn.
    """
    con = connect()
    keep = int(args.keep_days if args.keep_days is not None else 2)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep)).date().isoformat()

    before_rows = con.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(LENGTH(COALESCE(body,''))),0) c FROM nodes"
        " WHERE type='capture' AND substr(created,1,10)<=?"
        " AND json_extract(props,'$.consolidated_into') IS NULL", (cutoff,)).fetchone()

    repaired = _repair_digest_edges(con)

    # consolidate every day at or before the cutoff, one digest per day
    days = [r["d"] for r in con.execute(
        "SELECT DISTINCT substr(created,1,10) d FROM nodes WHERE type='capture'"
        " AND substr(created,1,10)<=? ORDER BY d", (cutoff,)).fetchall()]
    digested = []
    for day in days:
        caps = [_node_dict(r) for r in con.execute(
            "SELECT * FROM nodes WHERE type='capture' AND substr(created,1,10)=?"
            " ORDER BY created", (day,)).fetchall()]
        if not caps:
            continue
        asked = [c for c in caps if c["props"].get("role") == "user"]
        did = f"note_digest_{day}"
        lines = [f"- {re.sub(chr(92)+'s+', ' ', c['title']).strip()[:110]}" for c in asked]
        body = (f"Digest of {len(caps)} captures on {day} ({len(asked)} prompts). "
                f"Originals kept and linked; read one with get-node.\n\n"
                + "\n".join(lines[:40]))
        props = {"kind": "capture-digest", "day": day, "captures": len(caps),
                 "prompts": len(asked), "consolidated_at": now()}
        if con.execute("SELECT 1 FROM nodes WHERE id=?", (did,)).fetchone():
            api_update_node(con, did, title=f"Digest — {day}", body=body,
                            props_patch=props)
        else:
            api_create_node(con, "note", f"Digest — {day}", body, props,
                            realm=DEFAULT_REALM, node_id=did)
        newly = 0
        for c in caps:
            # correct direction: the capture is part of the digest
            api_link(con, c["id"], did, "part-of")
            if not c["props"].get("consolidated_into"):
                newly += 1
            cp = dict(c["props"]); cp["consolidated_into"] = did
            con.execute("UPDATE nodes SET props=? WHERE id=?",
                        (json.dumps(cp), c["id"]))
        con.commit()
        digested.append({"day": day, "digest": did, "captures": len(caps),
                         "newly_consolidated": newly})

    after = con.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(LENGTH(COALESCE(body,''))),0) c FROM nodes"
        " WHERE type='capture' AND substr(created,1,10)<=?"
        " AND json_extract(props,'$.consolidated_into') IS NULL", (cutoff,)).fetchone()
    # The standing saving, not just this run's delta: everything a digest now
    # stands in for, versus the size of the digests themselves.
    behind = con.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(LENGTH(COALESCE(title,''))+LENGTH(COALESCE(body,''))),0) c"
        " FROM nodes WHERE type='capture'"
        " AND json_extract(props,'$.consolidated_into') IS NOT NULL").fetchone()
    digest_chars = con.execute(
        "SELECT COALESCE(SUM(LENGTH(COALESCE(body,''))),0) c FROM nodes"
        " WHERE id LIKE 'note_digest_%'").fetchone()["c"]

    _after_write(con, nodes=[d["digest"] for d in digested])
    out({"ok": True, "destructive": False, "keep_days": keep, "cutoff": cutoff,
         "reversed_edges_repaired": repaired,
         "days_consolidated": len(digested), "digests": digested,
         "newly_consolidated": before_rows["n"] - after["n"],
         "still_loose": after["n"],
         "captures_behind_a_digest": behind["n"],
         "chars_they_hold": behind["c"],
         "digest_chars_standing_in": digest_chars,
         "read_reduction": (f"{100 * (1 - digest_chars / behind['c']):.0f}%"
                            if behind["c"] else "n/a"),
         "note": ("captures are kept and searchable; what shrinks is the read — "
                  "recall stops at the digest instead of every raw turn")})


def cmd_consolidate(args):
    """Sleep-style consolidation: episodic captures -> one semantic digest per
    day, linked back to the originals. NON-DESTRUCTIVE — every capture is kept;
    the digest is what recall surfaces, so reads get cheaper without losing raw
    detail (CONVENTIONS §2a)."""
    con = connect()
    where, params = "type='capture'", []
    if args.day:
        where += " AND substr(created,1,10)=?"; params.append(args.day)
    elif args.older_than is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(args.older_than))
                  ).date().isoformat()
        where += " AND substr(created,1,10)<=?"; params.append(cutoff)
    rows = con.execute(
        f"SELECT * FROM nodes WHERE {where} ORDER BY created", params).fetchall()
    if not rows:
        out({"ok": True, "digests": [], "note": "no captures matched"}); return

    by_day = {}
    for r in rows:
        by_day.setdefault(r["created"][:10], []).append(_node_dict(r))

    digests = []
    for day, caps in sorted(by_day.items()):
        asked = [c for c in caps if (c["props"].get("role") == "user")]
        lines = []
        for c in asked:
            t = re.sub(r"\s+", " ", c["title"]).strip()
            lines.append(f"- {t[:110]}")
        did = f"note_digest_{day}"
        body = (f"Digest of {len(caps)} captures on {day} "
                f"({len(asked)} prompts). Originals kept and linked.\n\n"
                + "\n".join(lines[:40]))
        props = {"kind": "capture-digest", "day": day, "captures": len(caps),
                 "prompts": len(asked), "consolidated_at": now()}
        if con.execute("SELECT 1 FROM nodes WHERE id=?", (did,)).fetchone():
            api_update_node(con, did, title=f"Digest — {day}", body=body,
                            props_patch=props)
        else:
            api_create_node(con, "note", f"Digest — {day}", body, props,
                            realm=DEFAULT_REALM, node_id=did)
        linked = 0
        for c in caps:
            # "src is part of dst" — the CAPTURE belongs to the digest, not the
            # reverse. Backwards, recall never reached the digest from a capture.
            api_link(con, c["id"], did, "part-of")
            cp = dict(c["props"]); cp["consolidated_into"] = did
            con.execute("UPDATE nodes SET props=? WHERE id=?",
                        (json.dumps(cp), c["id"]))
            linked += 1
        con.commit()
        digests.append({"id": did, "day": day, "captures": linked,
                        "kept_originals": True})
    _after_write(con, nodes=[d["id"] for d in digests])
    out({"ok": True, "digests": digests, "destructive": False})


# ---------------------------------------------------------------------------
# state awareness / open loops
# ---------------------------------------------------------------------------
# A node is an OPEN LOOP until its state says otherwise. State persists (stays
# the same) until explicitly changed via set-state / update-node.
CLOSED_STATES = {"done", "cancelled", "closed", "answered", "resolved",
                 "merged", "shipped", "deployed", "declined"}
LOOP_TYPES = {"task", "subtask", "reminder", "pr", "email", "message"}


def _is_open_loop(d):
    """True if node d is still an open loop. Tasks/trackables are open unless
    their status/state is a closed one; other nodes are open only if they carry
    a non-closed props.state (so notes/people aren't loops by default)."""
    status = (d.get("status") or "").lower()
    state = str(d["props"].get("state", "")).lower()
    if status in CLOSED_STATES or state in CLOSED_STATES:
        return False
    if d["type"] in LOOP_TYPES:
        return True
    return bool(state)  # any other node is a loop only if it has an open state


def cmd_set_state(args):
    """Change a node's state and stamp WHEN it changed (state_since). For tasks,
    also mirror it onto status. This is how a loop moves; until then it holds."""
    con = connect()
    row = con.execute("SELECT * FROM nodes WHERE id=?", (args.id,)).fetchone()
    if not row:
        out({"ok": False, "error": f"no node {args.id}"}); return
    d = _node_dict(row)
    p = d["props"]
    p["state"] = args.state
    p["state_since"] = now()
    if args.waiting_on is not None:
        p["waiting_on"] = args.waiting_on
    status = d["status"]
    if d["type"] in ("task", "subtask") and args.state in (
            "open", "doing", "blocked", "done", "cancelled"):
        status = args.state
    con.execute("UPDATE nodes SET status=?, props=?, updated=? WHERE id=?",
                (status, json.dumps(p), now(), args.id))
    con.commit()
    sync = _after_write(con, nodes=[args.id])
    row = con.execute("SELECT * FROM nodes WHERE id=?", (args.id,)).fetchone()
    out({"ok": True, "node": _node_dict(row), "sync": sync})


def cmd_open_loops(args):
    """Everything still awaiting action, oldest-first. The state-aware view:
    open tasks, tracked PRs/emails/messages, anything with a non-closed state.
    `age_days` counts from state_since (when it entered its state) or updated."""
    con = connect()
    now_dt = datetime.now(timezone.utc)
    rows = con.execute("SELECT * FROM nodes").fetchall()
    loops = []
    for r in rows:
        d = _node_dict(r)
        if not _is_open_loop(d):
            continue
        since = d["props"].get("state_since") or d["updated"]
        since_dt = _parse_dt(since) or now_dt
        d["age_days"] = (now_dt - since_dt).days
        d["kind"] = d["type"]
        d["waiting_on"] = d["props"].get("waiting_on", "")
        due_dt = _parse_dt(d["due"]) if d["due"] else None
        d["overdue"] = bool(due_dt and due_dt < now_dt)
        d["stale"] = d["age_days"] >= args.stale_days
        loops.append(d)
    # oldest first; overdue floats up
    loops.sort(key=lambda x: (not x["overdue"], -x["age_days"]))
    by_kind, by_prio = {}, {}
    for x in loops:
        by_kind[x["kind"]] = by_kind.get(x["kind"], 0) + 1
        pr = x["props"].get("priority", "--")
        by_prio[pr] = by_prio.get(pr, 0) + 1
    out({"ok": True, "count": len(loops),
         "stale": sum(1 for x in loops if x["stale"]),
         "overdue": sum(1 for x in loops if x["overdue"]),
         "by_kind": by_kind, "by_priority": by_prio,
         "results": loops[: args.limit]})


# ---------------------------------------------------------------------------
# arg parsing
# ---------------------------------------------------------------------------
def cmd_flush(args):
    con = connect()
    pending = con.execute("SELECT COUNT(*) c FROM outbox").fetchone()["c"]
    res = _flush(con)
    res["was_pending"] = pending
    out(res)


def cmd_neo4j_status(args):
    con = connect()
    cfg = _neo4j_cfg()
    pending = con.execute("SELECT COUNT(*) c FROM outbox").fetchone()["c"]
    res = {"ok": True, "configured": bool(cfg["url"] and cfg["password"]),
           "url": cfg["url"], "up": _neo4j_up(), "pending_ops": pending}
    # `up` only proves the root URL answers a GET. It stayed true for a whole day
    # while every flush timed out, so report whether the tx endpoint actually
    # works too — that is the thing sync depends on.
    if res["configured"]:
        ok, err = _neo4j_call([{"statement": "RETURN 1"}], timeout=10)
        res["writable"] = ok
        if not ok:
            res["error"] = err
    out(res)


def cmd_sections(args):
    con = connect()
    rows = con.execute(
        "SELECT COALESCE(NULLIF(realm,''),?) AS realm, COUNT(*) c "
        "FROM nodes GROUP BY realm ORDER BY c DESC", (DEFAULT_REALM,)).fetchall()
    out({"ok": True, "default": DEFAULT_REALM,
         "sections": {r["realm"]: r["c"] for r in rows}})


def _areas(con):
    """Every area node with its member count, biggest first."""
    return con.execute(
        "SELECT n.id, n.title, n.realm, "
        " (SELECT COUNT(*) FROM edges e WHERE e.dst=n.id AND e.rel=?) AS members "
        "FROM nodes n WHERE n.type=? ORDER BY members DESC, n.id",
        (AREA_REL, AREA_TYPE)).fetchall()


def cmd_areas(args):
    con = connect()
    rows = _areas(con)
    out({"ok": True, "count": len(rows), "rel": AREA_REL,
         "areas": [{"id": r["id"], "title": r["title"],
                    "realm": r["realm"], "members": r["members"]} for r in rows]})


def cmd_add_area(args):
    """Create a domain area (idempotent on --id)."""
    con = connect()
    aid = args.id or f"hub_{slugify(args.title)}"
    row = con.execute("SELECT * FROM nodes WHERE id=?", (aid,)).fetchone()
    if row:
        out({"ok": True, "existed": True, "node": _node_dict(row)})
        return
    out(api_create_node(con, AREA_TYPE, args.title, args.body or "",
                        {"domain": args.domain or slugify(args.title)},
                        "", "", getattr(args, "realm", None), aid))


def _resolve_area(con, area):
    """An area id (or bare domain name) -> the area node id, or an error dict."""
    for candidate in (area, f"hub_{slugify(area)}"):
        row = con.execute("SELECT id FROM nodes WHERE id=? AND type=?",
                          (candidate, AREA_TYPE)).fetchone()
        if row:
            return row["id"], None
    known = ", ".join(r["id"] for r in _areas(con)) or "none yet"
    return None, {"ok": False,
                  "error": f"no area {area!r} (known: {known}) — create it with add-area"}


def _api_surface() -> list:
    """Introspect this engine's own CLI. Derived, never hand-copied, so the genome
    cannot drift from the code the way a hand-written command list would."""
    parser = build_parser()
    surface = []
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, sp in action.choices.items():
            flags = []
            for a in sp._actions:
                if not a.option_strings:
                    flags.append(f"<{a.dest}>")
                    continue
                if a.dest == "help":
                    continue
                flags.append(a.option_strings[0] + ("" if a.nargs == 0 else " V"))
            surface.append({"command": name, "flags": flags,
                            "help": (sp.description or "").strip()})
    return sorted(surface, key=lambda d: d["command"])


def _db_schema(con) -> list:
    rows = con.execute(
        "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        " AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name").fetchall()
    return [{"name": r["name"], "sql": " ".join((r["sql"] or "").split())} for r in rows]


_GENOME_SPECS = {
    "spec_rebuild": ("How to rebuild the Claude layer from this database alone", """
If the plugin is gone and only `graph.db` survives, this is the restore path. Any
AI with shell access and this file can reconstitute the interaction layer.

1. **Find the brain.** `$SECOND_BRAIN_DB` -> `$SECOND_BRAIN_HOME/graph.db` ->
   `~/.second_brain/graph.db`. The file IS the brain: schema, content, conventions,
   skills and this blueprint are all inside it.
2. **Read the API surface.** `spec_api_surface` lists every engine command and flag,
   introspected from the code at snapshot time. If `scripts/graph.py` is also gone,
   `spec_db_schema` has the full DDL — the tables are plain SQLite (`nodes`, `edges`,
   `nodes_fts`, `outbox`) and can be driven with SQL directly.
3. **Load the operating rules ON DEMAND, not all at once.** `match-rules "<what you
   are about to do>"` returns only the sections that apply, inside a char budget.
   Do NOT read the whole conventions text — that is the cost this design exists to
   remove. `list --area convention` enumerates them if you need the full set.
4. **Load the skills.** They are `skill` nodes, not files: `match-skills "<situation>"`
   then `get-node --id` for the procedure. `ingest-skills` re-imports markdown skills
   if a plugin checkout reappears.
5. **Re-create the always-on capture.** `spec_hooks` describes the three hooks, what
   fires them and what each stores. They are pure Python/SQL and can be rewritten
   from that description against the schema in `spec_db_schema`.
6. **Reconnect the mirror, only if one is configured.** `spec_sync` covers the
   optional graph mirror, the outbox queue and reconciliation. Skip it otherwise —
   the brain is fully functional as a single local file, which is the default.
7. **Re-seed the base layer only if the conventions are missing.** With a plugin
   checkout present, `ingest-conventions` reloads the shipped text — it KEEPS every
   user-authored or user-edited rule, so restoring never undoes the user's own brain.

Precedence when rebuilding: a `user` rule outranks a `learned` one outranks the
`plugin` default. A rule with `status=superseded` is history — do not apply it.
"""),
    "spec_hooks": ("How the always-on capture hooks work", """
Three hooks make capture automatic, with no model call. All are pure Python + SQL.

| Hook | Script | Fires | Stores | Injects into context |
|---|---|---|---|---|
| `UserPromptSubmit` | `hooks/capture_prompt.py` | every prompt | a `capture` node; auto-creates a `task` for an explicit to-do | ~197 chars |
| `Stop` | `hooks/capture_answer.py` | after each answer | a `capture` node summarising the answer | nothing |
| `SessionStart` | open-loops brief | session start | nothing | ~850 chars, capped at 6 loops so it cannot grow with the graph |

Consequences a rebuild must preserve:
- The turn is ALREADY captured. Never spend tokens re-storing a prompt or answer;
  add only the semantic nodes the regex could not infer (a `decision`, a `person`,
  a `note`).
- "Token-free" means no model call for deciding or storing. It is not literally
  zero: the two injected strings above are real context. Say "no model call" when
  that is what is meant.
- `SessionStart` also runs the once-a-day consolidation, guarded by a
  `~/.second_brain/.last_consolidate` stamp. Opt out with `$SECOND_BRAIN_NO_CONSOLIDATE`.
"""),
    "spec_storage": ("Where the brain lives and how it is addressed", """
The brain is GLOBAL and lives outside any repo, so every project on the machine
shares one brain. It is never committed.

```
~/.second_brain/
  graph.db          the whole brain in one portable SQLite file (content + skills + genome)
  learned_skills/   optional local SKILL.md exports; never the source of truth
  exports/          export-memory output
  neo4j.json        optional graph-mirror config (secret)
  .last_consolidate stamp for the daily consolidation
```

Path resolution: `$SECOND_BRAIN_DB` (explicit file) -> `$SECOND_BRAIN_HOME/graph.db`
-> `~/.second_brain/graph.db`. Copying `graph.db` moves the brain; copying the home
directory moves brain + learned skills. Identical behaviour in Claude Code and the
Claude app. The file is the whole product — no server, no account, no network.

Two orthogonal groupings: `realm` is which section of life (`optibus`, `personal`,
`shared`); an `area` (`hub_*`, linked with `part-of`) is which domain of meaning.
"""),
    "spec_sync": ("Optional graph mirror and reconciliation", """
The brain is one local SQLite file and needs no network. OPTIONALLY, a graph mirror
can be configured (`~/.second_brain/neo4j.json` or `SECOND_BRAIN_NEO4J_*`) so several
machines share one brain — the plugin ships no default and connects to nothing unless
configured. Every write lands locally first, then mirrors. If the mirror is
unreachable the op is queued in the `outbox` table and pushed when it returns, so
losing it never blocks or loses a write. Use TLS if you configure one: credentials
and the whole graph cross the network on every write.

- `neo4j-status` — configured / reachable / pending op count.
- `flush` — drain the outbox.
- `scripts/brain_sync.py` — full bulk reconcile.
- `$SECOND_BRAIN_NO_AUTOSYNC=1` — queue without per-op push (bulk loads), then flush.

Node `type` is a PROPERTY in neo4j, not a label, so changing a node's type needs no
label surgery — re-emitting the node through the API is enough.
"""),
}


def cmd_genome(args):
    """Emit (and optionally refresh) the brain's own build blueprint.

    `--refresh` regenerates the derived specs — the API surface and DB schema come
    from introspection, so the genome tracks the code instead of drifting from it.
    """
    con = connect()
    aid, err = _resolve_area(con, AREA_GENOME)
    if err:
        out(err); return
    ts = now()
    if args.refresh:
        derived = {
            "spec_api_surface": ("Engine command surface (introspected)",
                                 json.dumps(_api_surface(), indent=1)),
            "spec_db_schema": ("Database schema (DDL)",
                               json.dumps(_db_schema(con), indent=1)),
        }
        touched = []
        for nid, (title, body) in {**_GENOME_SPECS, **derived}.items():
            props = {"source": "plugin", "refreshed_at": ts,
                     "derived": nid in derived}
            row = con.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
            if row:
                cur = _node_dict(row)
                if cur["props"].get("user_edited") and nid not in derived:
                    continue
                con.execute("UPDATE nodes SET title=?, body=?, props=?, updated=? WHERE id=?",
                            (title, body.strip(), json.dumps({**cur["props"], **props}), ts, nid))
            else:
                con.execute(
                    "INSERT INTO nodes(id,type,title,body,props,status,due,realm,created,updated)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (nid, SPEC_TYPE, title, body.strip(), json.dumps(props),
                     "active", "", "shared", ts, ts))
            con.execute("INSERT OR IGNORE INTO edges(src,dst,rel,props,created)"
                        " VALUES(?,?,?,?,?)", (nid, aid, AREA_REL, "{}", ts))
            touched.append(nid)
        con.commit()
        _after_write(con, nodes=touched)

    specs = con.execute(
        "SELECT * FROM nodes WHERE type=? AND id IN"
        " (SELECT src FROM edges WHERE dst=? AND rel=?) ORDER BY id", (SPEC_TYPE, aid, AREA_REL)
    ).fetchall()
    rules = con.execute(
        "SELECT id,title,status,props FROM nodes WHERE type=? ORDER BY id", (RULE_TYPE,)).fetchall()
    skills = con.execute("SELECT COUNT(*) c FROM nodes WHERE type='skill'").fetchone()["c"]
    payload = {
        "ok": True,
        "generated": ts,
        "db": str(db_path()),
        "specs": [{"id": r["id"], "title": r["title"]} for r in specs],
        "rules": len(rules),
        "skills": skills,
        "restore": "read spec_rebuild first, then match-rules '<task>' — never the whole conventions",
    }
    if args.out:
        d = Path(args.out).expanduser(); d.mkdir(parents=True, exist_ok=True)
        for r in specs:
            (d / f"{r['id']}.md").write_text(
                f"# {r['title']}\n\n{r['body']}\n", encoding="utf-8")
        rule_rows = con.execute(
            "SELECT id,title,body,status,props FROM nodes WHERE type=? ORDER BY id",
            (RULE_TYPE,)).fetchall()
        (d / "rules.md").write_text("\n\n".join(
            f"## {r['title']}\n\n_id: `{r['id']}` · status: {r['status'] or 'active'}_\n\n{r['body']}"
            for r in rule_rows), encoding="utf-8")
        (d / "genome.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
        payload["written_to"] = str(d)
    out(payload)


# ---------------------------------------------------------------------------
# traceability: the agent tree, ideas, and conditional discard (§2k)
#
# The brain already records WHAT was said (capture nodes). This records WHAT WAS
# CONSIDERED and WHY it was dropped — and, for the discards that were only ever
# about a missing condition, brings them back when the condition stops holding.
#
# Everything below is captured BY HOOK OR EXPLICIT CALL, never by asking a model
# to re-read a transcript: deterministic, zero tokens, zero latency.
# ---------------------------------------------------------------------------
IDEA_STATUSES = ("proposed", "selected", "discarded", "superseded", "revived")
# Only `conditional` is re-evaluated automatically. A `preference` discard comes
# back when someone asks for it; a `permanent` one never does.
DISCARD_TYPES = ("conditional", "preference", "permanent")
AGENT_OUTCOMES = ("completed", "abandoned", "failed")
CONDITION_OPS = (">=", "<=", "==", "!=", ">", "<", "exists", "not_exists")
# Ops that carry no right-hand side.
UNARY_OPS = ("exists", "not_exists")
# An unblocked item is not re-announced inside this window — a reconsider prompt
# that repeats every run is a prompt nobody reads (§2k, "notify, don't execute").
NOTIFY_QUIET_DAYS = 7
PROBE_TIMEOUT = 20


def _conv_id(session_id: str) -> str:
    """Deterministic, so conversation-start is a true upsert per session."""
    return "conversation_" + (slugify(session_id) or "adhoc")


def _gauge_id(key: str) -> str:
    return "gauge_" + (slugify(key) or "unnamed")


_COND_RE = re.compile(
    r"^\s*(?P<key>[A-Za-z0-9_.\-]+)\s*"
    r"(?P<op>>=|<=|==|!=|>|<|\bexists\b|\bnot_exists\b)\s*"
    r"(?P<val>.*?)\s*$")


def parse_condition(text: str) -> dict:
    """`"gpu.vram_gb >= 16"` -> {gauge, op, value}.

    The CLI takes the readable form; the stored form is always structured. Free
    text is what makes a discard un-recheckable, so it never reaches the store.
    Raises ValueError with a usable message — the caller turns it into `ok:false`.
    """
    m = _COND_RE.match(text or "")
    if not m:
        raise ValueError(
            f"cannot parse condition {text!r} — expected '<gauge.key> <op> <value>', "
            f"ops: {', '.join(CONDITION_OPS)}")
    op, val = m.group("op"), m.group("val").strip().strip("'\"")
    if op in UNARY_OPS:
        val = ""
    elif not val:
        raise ValueError(f"condition {text!r} has operator {op} but no value")
    return {"gauge": m.group("key"), "op": op, "value": val}


_TRUTHY = {"true", "yes", "y", "on", "1"}
_FALSY = {"false", "no", "n", "off", "0"}


def _coerce(raw):
    """Best-effort typing for a gauge value: number, bool, version tuple, or text.

    Returned as (kind, value) so the comparison never guesses twice. Version
    handling matters for `dependency.<name>.version` — `2.10.0` is above `2.9.0`
    and below it as a string.
    """
    s = str(raw if raw is not None else "").strip()
    if s == "":
        return ("empty", "")
    low = s.lower()
    if low in _TRUTHY:
        return ("bool", True)
    if low in _FALSY:
        return ("bool", False)
    try:
        return ("num", float(s))
    except ValueError:
        pass
    if re.fullmatch(r"v?\d+(\.\d+)+[A-Za-z0-9.\-+]*", s):
        head = re.match(r"v?(\d+(?:\.\d+)+)", s).group(1)
        return ("ver", tuple(int(p) for p in head.split(".")))
    return ("text", s)


def compare_values(current, op: str, required) -> bool:
    """Evaluate one condition. Unknown/unmeasured gauges are NOT satisfied —
    a blocked item stays blocked until something actually measures otherwise."""
    ck, cv = _coerce(current)
    if op == "exists":
        return ck != "empty"
    if op == "not_exists":
        return ck == "empty"
    if ck == "empty":
        return False
    rk, rv = _coerce(required)
    if ck != rk:                      # mixed kinds: fall back to text equality
        if op == "==":
            return str(current).strip() == str(required).strip()
        if op == "!=":
            return str(current).strip() != str(required).strip()
        return False
    if op == "==":
        return cv == rv
    if op == "!=":
        return cv != rv
    if ck == "bool":                  # ordering booleans is a caller mistake
        return False
    if op == ">=":
        return cv >= rv
    if op == "<=":
        return cv <= rv
    if op == ">":
        return cv > rv
    if op == "<":
        return cv < rv
    return False


# --- the agent tree ---------------------------------------------------------
def api_conversation(con, session_id: str, *, cwd="", owner="", title="") -> dict:
    """Get-or-create the conversation for a session. Idempotent by construction:
    the id is derived from the session id, so every hook in the session lands on
    the same node without a lookup round-trip."""
    cid = _conv_id(session_id)
    row = con.execute("SELECT * FROM nodes WHERE id=?", (cid,)).fetchone()
    if row:
        return {"ok": True, "node": _node_dict(row), "created": False}
    props = {"session_id": session_id, "started_at": now(),
             "owner": owner or _env("OWNER", "Gabriel")}
    if cwd:
        props["cwd"] = cwd
    res = api_create_node(con, "conversation",
                          title or f"Session {session_id[:8] or 'adhoc'}",
                          "", props, "open", "", "", cid)
    res["created"] = res.get("ok", False)
    return res


def _parent_of(con, agent_id: str):
    r = con.execute(
        "SELECT dst FROM edges WHERE src=? AND rel=? LIMIT 1",
        (agent_id, AREA_REL)).fetchone()
    return r["dst"] if r else None


def api_agent_spawn(con, parent_id: str, role: str, *, model="", body="",
                    session_id="") -> dict:
    """One node in the tree, under EXACTLY one parent.

    A parent is mandatory and must be a conversation or another agent: an agent
    with no parent is a branch that cannot be walked, and the whole point of the
    tree is that a subagent's discarded alternative stays visible from the root
    instead of dying with its branch (§3.2 of the spec).
    """
    if not (role or "").strip():
        return {"ok": False, "error": "missing role"}
    prow = con.execute("SELECT * FROM nodes WHERE id=?", (parent_id,)).fetchone()
    if not prow:
        return {"ok": False, "error": f"no parent {parent_id}"}
    if prow["type"] not in ("conversation", "agent"):
        return {"ok": False,
                "error": f"parent {parent_id} is a {prow['type']}; an agent's "
                         f"parent must be a conversation or an agent"}
    props = {"role": role, "started_at": now(), "outcome": ""}
    if model:
        props["model"] = model
    if session_id:
        props["session_id"] = session_id
    depth = 0
    walk, seen = parent_id, set()
    while walk and walk not in seen:
        seen.add(walk)
        walk = _parent_of(con, walk)
        depth += 1
    props["depth"] = depth
    res = api_create_node(con, "agent", role, body or "", props, "open",
                          "", prow["realm"] or DEFAULT_REALM)
    if res.get("ok"):
        api_link(con, res["node"]["id"], parent_id, AREA_REL)
        res["parent"] = parent_id
    return res


def api_agent_finish(con, agent_id: str, outcome: str, note="") -> dict:
    """Record the outcome. An unfinished agent is indistinguishable from one that
    crashed, so every branch closes explicitly — `abandoned` is a real answer."""
    if outcome not in AGENT_OUTCOMES:
        return {"ok": False,
                "error": f"outcome must be one of {'|'.join(AGENT_OUTCOMES)}"}
    row = con.execute("SELECT * FROM nodes WHERE id=?", (agent_id,)).fetchone()
    if not row:
        return {"ok": False, "error": f"no node {agent_id}"}
    patch = {"outcome": outcome, "ended_at": now()}
    if note:
        patch["outcome_note"] = note
    return api_update_node(con, agent_id, status="done", props_patch=patch)


def api_conversation_end(con, session_id: str, reason="") -> dict:
    """Close the conversation and every agent still open under it.

    The MAIN agent reaching the end of a session finished its work; a SUBAGENT
    still open when the session ends did not — it was abandoned. Recording both
    as the same thing would make "abandoned" meaningless, which is the one thing
    the outcome field exists to say.
    """
    cid = _conv_id(session_id)
    if not con.execute("SELECT 1 FROM nodes WHERE id=?", (cid,)).fetchone():
        return {"ok": False, "error": f"no conversation for session {session_id}"}
    closed = []
    for r in con.execute(
            "SELECT id, json_extract(props,'$.role') role FROM nodes "
            "WHERE type='agent' AND status!='done' "
            "AND json_extract(props,'$.session_id')=?", (session_id,)).fetchall():
        outcome = "completed" if r["role"] == "main" else "abandoned"
        api_agent_finish(con, r["id"], outcome, "conversation ended")
        closed.append({"id": r["id"], "outcome": outcome})
    patch = {"ended_at": now()}
    if reason:
        patch["end_reason"] = reason
    res = api_update_node(con, cid, status="done", props_patch=patch)
    res["closed_agents"] = closed
    return res


def _tree(con, root_id: str, depth=0, seen=None):
    """Walk children (anything `part-of` this node) into a nested dict."""
    seen = seen if seen is not None else set()
    if root_id in seen or depth > 12:
        return None
    seen.add(root_id)
    row = con.execute("SELECT * FROM nodes WHERE id=?", (root_id,)).fetchone()
    if not row:
        return None
    d = _node_dict(row)
    p = d["props"]
    node = {"id": d["id"], "type": d["type"], "title": d["title"]}
    for k in ("role", "model", "outcome", "discard_type"):
        if p.get(k):
            node[k] = p[k]
    if d["type"] == "idea":
        node["status"] = d["status"]
    kids, msgs, ideas = [], 0, []
    rows = con.execute(
        "SELECT n.* FROM edges e JOIN nodes n ON n.id=e.src "
        "WHERE e.dst=? AND e.rel=? ORDER BY n.created", (root_id, AREA_REL)
    ).fetchall()
    for r in rows:
        if r["type"] in ("agent", "conversation"):
            sub = _tree(con, r["id"], depth + 1, seen)
            if sub:
                kids.append(sub)
        elif r["type"] == "capture":
            msgs += 1
        elif r["type"] == "idea":
            sub = _tree(con, r["id"], depth + 1, seen)
            if sub:
                ideas.append(sub)
    if msgs:
        node["messages"] = msgs
    if ideas:
        node["ideas"] = ideas
    if kids:
        node["children"] = kids
    return node


# --- ideas ------------------------------------------------------------------
def _transition(con, idea_id: str, frm: str, to: str, *, reason="",
                discard_type="", conditions=None, recheck_on=None,
                caused_by="", realm="") -> dict:
    """Append one immutable entry to the idea's log.

    Transitions are never updated or deleted — the sequence IS the history, and
    `idea.status` is only the cached head of it.
    """
    props = {"idea": idea_id, "from_status": frm, "to_status": to,
             "at": now()}
    if reason:
        props["reason"] = reason
    if discard_type:
        props["discard_type"] = discard_type
    if conditions:
        props["blocked_by"] = conditions
    if recheck_on:
        props["recheck_on"] = recheck_on
    if caused_by:
        props["caused_by"] = caused_by
    res = api_create_node(con, "transition", f"{frm or 'new'} -> {to}", reason,
                          props, "", "", realm or DEFAULT_REALM)
    if res.get("ok"):
        tid = res["node"]["id"]
        api_link(con, tid, idea_id, "about")
        if caused_by and con.execute(
                "SELECT 1 FROM nodes WHERE id=?", (caused_by,)).fetchone():
            api_link(con, tid, caused_by, "caused-by")
    return res


def _project_blocked(con, idea_id: str, transition_id: str, conditions) -> int:
    """Write the transition's conditions into the recheck index.

    The index is a PROJECTION of the log (§9 of the spec): rebuildable, never the
    source of truth. Callers reach it through here, never with their own INSERT.
    """
    ts = now()
    n = 0
    for c in conditions or []:
        con.execute(
            "INSERT OR IGNORE INTO blocked_item"
            "(item_type,item_ref,gauge_key,op,required,source_ref,created,resolved_at)"
            " VALUES(?,?,?,?,?,?,?,'')",
            ("idea", idea_id, c["gauge"], c["op"], str(c.get("value", "")),
             transition_id, ts))
        n += 1
    con.commit()
    return n


def api_idea_propose(con, title: str, *, detail="", agent_id="", realm="") -> dict:
    """Every alternative becomes a record, not just the chosen one — that is the
    whole reason the same option does not get re-argued from zero six months on."""
    if not (title or "").strip():
        return {"ok": False, "error": "missing title"}
    props = {"proposed_at": now()}
    if agent_id:
        props["agent"] = agent_id
    res = api_create_node(con, "idea", title, detail or "", props, "proposed",
                          "", realm or DEFAULT_REALM)
    if not res.get("ok"):
        return res
    iid = res["node"]["id"]
    if agent_id and con.execute(
            "SELECT 1 FROM nodes WHERE id=?", (agent_id,)).fetchone():
        api_link(con, iid, agent_id, AREA_REL)
    _transition(con, iid, "", "proposed", realm=realm)
    return res


def api_idea_discard(con, idea_id: str, discard_type: str, reason: str, *,
                     conditions=None, recheck_on=None, caused_by="") -> dict:
    """The highest-value hook, and the easiest one to implement badly.

    A conditional discard WITHOUT a structured condition is a permanent discard
    wearing a disguise: nothing will ever re-check it, and the reason text will
    outlive everyone who could interpret it. So it is rejected at the write, not
    audited later — and every gauge it names must already be in the catalog, or
    the condition points at something nobody measures.
    """
    row = con.execute("SELECT * FROM nodes WHERE id=?", (idea_id,)).fetchone()
    if not row:
        return {"ok": False, "error": f"no node {idea_id}"}
    if row["type"] != "idea":
        return {"ok": False, "error": f"{idea_id} is a {row['type']}, not an idea"}
    if discard_type not in DISCARD_TYPES:
        return {"ok": False,
                "error": f"discard type must be one of {'|'.join(DISCARD_TYPES)}"}
    if not (reason or "").strip():
        return {"ok": False, "error": "a discard needs a human-readable reason"}
    conditions = list(conditions or [])
    if discard_type == "conditional" and not conditions:
        return {"ok": False, "error":
                "a conditional discard needs at least one structured condition "
                "(--blocked-by 'gauge.key >= value'); without one it is a "
                "permanent discard in disguise — use --type permanent instead"}
    if discard_type != "conditional" and conditions:
        return {"ok": False, "error":
                f"only a conditional discard carries conditions; "
                f"{discard_type} discards are never re-checked"}
    missing = [c["gauge"] for c in conditions
               if not con.execute("SELECT 1 FROM nodes WHERE id=? AND type='gauge'",
                                  (_gauge_id(c["gauge"]),)).fetchone()]
    if missing:
        return {"ok": False, "error":
                f"unknown gauge(s): {', '.join(sorted(set(missing)))} — add them "
                f"first (gauge-set --key <key> --value <v> --probe '<cmd>'), or "
                f"the condition names something nobody measures"}
    frm = row["status"] or "proposed"
    tr = _transition(con, idea_id, frm, "discarded", reason=reason,
                     discard_type=discard_type, conditions=conditions,
                     recheck_on=recheck_on, caused_by=caused_by,
                     realm=row["realm"])
    if not tr.get("ok"):
        return tr
    patch = {"discard_type": discard_type, "discard_reason": reason,
             "discarded_at": now()}
    if conditions:
        patch["blocked_by"] = conditions
    if recheck_on:
        patch["recheck_on"] = recheck_on
    res = api_update_node(con, idea_id, status="discarded", props_patch=patch)
    if not res.get("ok"):
        return res
    for c in conditions:                    # the graph shows the block too
        api_link(con, idea_id, _gauge_id(c["gauge"]), "blocked-by")
    res["transition"] = tr["node"]["id"]
    res["indexed_conditions"] = _project_blocked(con, idea_id, tr["node"]["id"],
                                                 conditions)
    res["rechecks_automatically"] = discard_type == "conditional"
    return res


def api_idea_move(con, idea_id: str, to: str, *, reason="", caused_by="",
                  supersedes="") -> dict:
    """selected / superseded / revived — the transitions that carry no condition."""
    row = con.execute("SELECT * FROM nodes WHERE id=?", (idea_id,)).fetchone()
    if not row:
        return {"ok": False, "error": f"no node {idea_id}"}
    if row["type"] != "idea":
        return {"ok": False, "error": f"{idea_id} is a {row['type']}, not an idea"}
    if to not in IDEA_STATUSES:
        return {"ok": False, "error": f"status must be one of {'|'.join(IDEA_STATUSES)}"}
    frm = row["status"] or "proposed"
    tr = _transition(con, idea_id, frm, to, reason=reason, caused_by=caused_by,
                     realm=row["realm"])
    if not tr.get("ok"):
        return tr
    patch = {f"{to}_at": now()}
    if reason:
        patch[f"{to}_reason"] = reason
    res = api_update_node(con, idea_id, status=to, props_patch=patch)
    if supersedes:
        api_link(con, idea_id, supersedes, "supersedes")
        api_idea_move(con, supersedes, "superseded",
                      reason=f"superseded by {idea_id}")
    if res.get("ok"):
        res["transition"] = tr["node"]["id"]
    return res


# --- the gauge catalog ------------------------------------------------------
def api_gauge_set(con, key: str, *, value=None, unit="", probe="",
                  description="", realm="") -> dict:
    """Upsert one measurable thing. A NEW GAUGE NEEDS NO NEW CODE — a row and a
    probe, and every idea blocked on it becomes re-checkable."""
    key = (key or "").strip()
    if not key:
        return {"ok": False, "error": "missing key"}
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*", key):
        return {"ok": False, "error":
                f"gauge key {key!r} must be dotted alphanumerics, e.g. gpu.vram_gb"}
    gid = _gauge_id(key)
    row = con.execute("SELECT * FROM nodes WHERE id=?", (gid,)).fetchone()
    patch = {"key": key}
    if unit:
        patch["unit"] = unit
    if probe:
        patch["probe"] = probe
    if description:
        patch["description"] = description
    previous = None
    if value is not None:
        previous = (_node_dict(row)["props"].get("current_value") if row else None)
        patch["current_value"] = value
        patch["measured_at"] = now()
    if row:
        res = api_update_node(con, gid, body=description or None,
                              props_patch=patch)
    else:
        res = api_create_node(con, "gauge", key, description, patch, "",
                              "", realm or DEFAULT_REALM, gid)
    if res.get("ok"):
        res["previous_value"] = previous
        res["changed"] = value is not None and str(previous) != str(value)
    return res


def _gauge_values(con) -> dict:
    vals = {}
    for r in con.execute("SELECT * FROM nodes WHERE type='gauge'").fetchall():
        d = _node_dict(r)
        p = d["props"]
        vals[p.get("key") or d["title"]] = p
    return vals


def api_gauge_probe(con, key="") -> dict:
    """Run the stored probes and record what they measured.

    The probe is a shell command the OWNER put in their own catalog, and it only
    runs when someone asks for it — never as a side effect of a read. A gauge
    with no probe is a gauge nobody will ever re-check, which is why the catalog
    treats one as a warning.
    """
    rows = con.execute("SELECT * FROM nodes WHERE type='gauge' ORDER BY id").fetchall()
    ran, skipped, changed = [], [], []
    for r in rows:
        d = _node_dict(r)
        k = d["props"].get("key") or d["title"]
        if key and k != key:
            continue
        probe = (d["props"].get("probe") or "").strip()
        if not probe:
            skipped.append({"key": k, "why": "no probe"})
            continue
        try:
            p = subprocess.run(probe, shell=True, capture_output=True, text=True,
                               timeout=PROBE_TIMEOUT)
        except Exception as e:
            skipped.append({"key": k, "why": f"probe failed: {e}"}); continue
        if p.returncode != 0:
            skipped.append({"key": k, "why": f"probe exit {p.returncode}",
                            "stderr": (p.stderr or "").strip()[:200]})
            continue
        val = (p.stdout or "").strip().splitlines()
        val = val[0].strip() if val else ""
        res = api_gauge_set(con, k, value=val)
        ran.append({"key": k, "value": val})
        if res.get("changed"):
            changed.append(k)
    return {"ok": True, "probed": ran, "skipped": skipped, "changed": changed}


# --- re-evaluation ----------------------------------------------------------
def _rebuild_blocked(con) -> int:
    """Re-derive the whole index from the transition log.

    The log is authoritative; this table is disposable. An item is blocked if its
    latest conditional discard has not been followed by a revival.
    """
    con.execute("DELETE FROM blocked_item")
    rows = con.execute(
        "SELECT * FROM nodes WHERE type='transition' ORDER BY created").fetchall()
    latest = {}
    for r in rows:
        d = _node_dict(r)
        p = d["props"]
        idea = p.get("idea")
        if not idea:
            continue
        latest[idea] = d           # ordered by creation, so the last one wins
    n = 0
    for idea, d in latest.items():
        p = d["props"]
        if p.get("to_status") != "discarded" or p.get("discard_type") != "conditional":
            continue
        n += _project_blocked(con, idea, d["id"], p.get("blocked_by") or [])
    con.commit()
    return n


def _blocked_groups(con, gauge_key="") -> dict:
    """Unresolved index rows grouped by (item, source transition).

    The group is the unit of evaluation because multiple conditions on one
    discard are a CONJUNCTION — all of them have to hold before anything comes
    back. Filtering by gauge only picks which groups to look at; the evaluation
    still reads the group's every condition.
    """
    if gauge_key:
        refs = [(r["item_ref"], r["source_ref"]) for r in con.execute(
            "SELECT DISTINCT item_ref, source_ref FROM blocked_item "
            "WHERE resolved_at='' AND gauge_key=?", (gauge_key,)).fetchall()]
        if not refs:
            return {}
        qs = ",".join("?" for _ in refs)
        rows = con.execute(
            f"SELECT * FROM blocked_item WHERE resolved_at='' AND source_ref IN ({qs})",
            [r[1] for r in refs]).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM blocked_item WHERE resolved_at='' ORDER BY created"
        ).fetchall()
    groups = {}
    for r in rows:
        groups.setdefault((r["item_type"], r["item_ref"], r["source_ref"]), []).append(
            {"gauge": r["gauge_key"], "op": r["op"], "value": r["required"]})
    return groups


def api_recheck(con, *, gauge_key="", apply=False, quiet_days=NOTIFY_QUIET_DAYS,
                force=False) -> dict:
    """What has become possible again?

    One indexed pass over `blocked_item` — no scan of the graph, no model call.
    It NOTIFIES; it never resumes the work. The context around a discarded idea
    has moved on too, and only a person can say whether it still makes sense.
    """
    gauges = _gauge_values(con)
    unblocked, still, unmeasured = [], 0, set()
    for (item_type, item_ref, src), conds in _blocked_groups(con, gauge_key).items():
        checks, ok_all = [], True
        for c in conds:
            g = gauges.get(c["gauge"]) or {}
            cur = g.get("current_value")
            if cur in (None, "") and c["op"] not in UNARY_OPS:
                unmeasured.add(c["gauge"])
            met = compare_values(cur, c["op"], c["value"])
            ok_all = ok_all and met
            checks.append({"gauge": c["gauge"], "op": c["op"],
                           "required": c["value"], "current": cur, "met": met})
        if not ok_all:
            still += 1
            continue
        row = con.execute("SELECT * FROM nodes WHERE id=?", (item_ref,)).fetchone()
        if not row:
            continue
        d = _node_dict(row)
        last = d["props"].get("notified_at") or ""
        if not force and last and _within_days(last, quiet_days):
            continue                      # already asked recently; stay quiet
        unblocked.append({"item_type": item_type, "id": item_ref,
                          "title": d["title"], "source_ref": src,
                          "reason_discarded": d["props"].get("discard_reason", ""),
                          "conditions": checks})
    res = {"ok": True, "gauge": gauge_key or "(all)", "unblocked": len(unblocked),
           "still_blocked": still, "items": unblocked}
    if unmeasured:
        res["unmeasured_gauges"] = sorted(unmeasured)
        res["warning"] = ("these gauges have no measurement, so anything waiting "
                          "on them can never be re-checked — give them a probe "
                          "and run gauge-probe")
    if apply:
        applied = []
        for it in unblocked:
            con.execute("UPDATE blocked_item SET resolved_at=? WHERE source_ref=? "
                        "AND resolved_at=''", (now(), it["source_ref"]))
            met = "; ".join(f"{c['gauge']} {c['op']} {c['required']} "
                            f"(now {c['current']})" for c in it["conditions"])
            api_idea_move(con, it["id"], "revived",
                          reason=f"condition no longer holds: {met}")
            api_update_node(con, it["id"], props_patch={"notified_at": now()})
            applied.append(it["id"])
        con.commit()
        res["revived"] = applied
    res["notice"] = [
        f"\"{it['title']}\" was discarded because {it['reason_discarded'] or 'a condition did not hold'}. "
        f"That condition now holds. Reconsider?" for it in unblocked]
    res["hint"] = ("this is a proposal, not a resumption — a person decides "
                   "whether it still makes sense" if unblocked else
                   "nothing became possible")
    return res


def _within_days(iso: str, days: int) -> bool:
    try:
        t = datetime.fromisoformat(iso)
    except Exception:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t) < timedelta(days=days)


# --- context as an event stream ---------------------------------------------
def api_context_event(con, agent_id: str, op: str, item_type: str,
                      item_ref: str, tokens=0, message_id="") -> dict:
    """Record the CHANGE to the window, not a photograph of it.

    A snapshot per turn stores the same document fifty times; the deltas store it
    once and still reconstruct any instant by replay — and they answer the
    question a snapshot cannot: why was this in the window at all?
    """
    if op not in ("added", "removed", "summarized"):
        return {"ok": False, "error": "op must be added|removed|summarized"}
    props = {"op": op, "item_type": item_type, "item_ref": item_ref,
             "tokens": int(tokens or 0), "agent": agent_id, "at": now()}
    if message_id:
        props["message"] = message_id
    res = api_create_node(con, "context-event", f"{op} {item_type}:{item_ref}",
                          "", props, "", "", DEFAULT_REALM)
    if res.get("ok") and agent_id and con.execute(
            "SELECT 1 FROM nodes WHERE id=?", (agent_id,)).fetchone():
        api_link(con, res["node"]["id"], agent_id, AREA_REL)
    return res


def api_context_at(con, agent_id: str, at="") -> dict:
    """Replay the events up to `at` and return the window as it stood."""
    rows = con.execute(
        "SELECT * FROM nodes WHERE type='context-event' ORDER BY created").fetchall()
    window, total, seen = {}, 0, 0
    for r in rows:
        d = _node_dict(r)
        p = d["props"]
        if p.get("agent") != agent_id:
            continue
        if at and (p.get("at") or d["created"]) > at:
            continue
        seen += 1
        ref = f"{p.get('item_type')}:{p.get('item_ref')}"
        if p.get("op") == "removed":
            window.pop(ref, None)
        else:
            window[ref] = {"item_type": p.get("item_type"),
                           "item_ref": p.get("item_ref"),
                           "tokens": p.get("tokens", 0),
                           "op": p.get("op"), "at": p.get("at")}
    total = sum(int(v.get("tokens") or 0) for v in window.values())
    return {"ok": True, "agent": agent_id, "at": at or now(),
            "events_replayed": seen, "items": list(window.values()),
            "tokens": total}


# --- commands ---------------------------------------------------------------
def cmd_conversation_start(args):
    con = connect()
    out(api_conversation(con, args.session, cwd=args.cwd or "",
                         owner=args.owner or "", title=args.title or ""))


def cmd_conversation_end(args):
    con = connect()
    out(api_conversation_end(con, args.session, args.reason or ""))


def cmd_agent_spawn(args):
    con = connect()
    parent = args.parent
    if not parent:
        if not args.session:
            out({"ok": False, "error": "pass --parent, or --session to hang it "
                                       "off that session's conversation"}); return
        conv = api_conversation(con, args.session, cwd=args.cwd or "")
        if not conv.get("ok"):
            out(conv); return
        parent = conv["node"]["id"]
    out(api_agent_spawn(con, parent, args.role, model=args.model or "",
                        body=args.body or "", session_id=args.session or ""))


def cmd_agent_finish(args):
    con = connect()
    out(api_agent_finish(con, args.id, args.outcome, args.note or ""))


def cmd_tree(args):
    """The whole branch, token-free — including what a subagent discarded."""
    con = connect()
    root = args.id or (_conv_id(args.session) if args.session else "")
    if not root:
        out({"ok": False, "error": "pass --id or --session"}); return
    t = _tree(con, root)
    if not t:
        out({"ok": False, "error": f"no node {root}"}); return
    out({"ok": True, "tree": t})


def _conditions_from(args):
    return [parse_condition(c) for c in (args.blocked_by or [])]


def cmd_idea_propose(args):
    con = connect()
    agent = args.agent or ""
    if not agent and args.session:
        conv = api_conversation(con, args.session)
        agent = conv["node"]["id"] if conv.get("ok") else ""
    out(api_idea_propose(con, args.title, detail=args.detail or "",
                         agent_id=agent, realm=getattr(args, "realm", "") or ""))


def cmd_idea_discard(args):
    con = connect()
    try:
        conds = _conditions_from(args)
    except ValueError as e:
        out({"ok": False, "error": str(e)}); return
    out(api_idea_discard(con, args.id, args.type, args.reason or "",
                         conditions=conds, recheck_on=args.recheck_on or [],
                         caused_by=args.caused_by or ""))


def cmd_idea_select(args):
    con = connect()
    out(api_idea_move(con, args.id, "selected", reason=args.reason or "",
                      caused_by=args.caused_by or "",
                      supersedes=args.supersedes or ""))


def cmd_idea_revive(args):
    con = connect()
    con.execute("UPDATE blocked_item SET resolved_at=? WHERE item_ref=? AND "
                "resolved_at=''", (now(), args.id))
    con.commit()
    out(api_idea_move(con, args.id, "revived", reason=args.reason or "revived by hand"))


def cmd_ideas(args):
    """Every alternative, not just the one that won."""
    con = connect()
    sql = "SELECT * FROM nodes WHERE type='idea'"
    params = []
    if args.status:
        sql += " AND status=?"; params.append(args.status)
    sql += " ORDER BY updated DESC LIMIT ?"
    params.append(int(args.limit or 30))
    rows = con.execute(sql, params).fetchall()
    res = []
    for r in rows:
        d = _node_dict(r)
        p = d["props"]
        if args.agent and p.get("agent") != args.agent:
            continue
        item = {"id": d["id"], "title": d["title"], "status": d["status"]}
        for k in ("discard_type", "discard_reason", "agent"):
            if p.get(k):
                item[k] = p[k]
        if p.get("blocked_by"):
            item["blocked_by"] = [f"{c['gauge']} {c['op']} {c.get('value','')}".strip()
                                  for c in p["blocked_by"]]
        res.append(item)
    out({"ok": True, "count": len(res), "results": res})


def cmd_idea_history(args):
    """The full transition log for one idea — reconstructable, in order."""
    con = connect()
    row = con.execute("SELECT * FROM nodes WHERE id=?", (args.id,)).fetchone()
    if not row:
        out({"ok": False, "error": f"no node {args.id}"}); return
    d = _node_dict(row)
    rows = con.execute(
        "SELECT n.* FROM edges e JOIN nodes n ON n.id=e.src "
        "WHERE e.dst=? AND e.rel='about' AND n.type='transition' ORDER BY n.created",
        (args.id,)).fetchall()
    hist = []
    for r in rows:
        p = _node_dict(r)["props"]
        step = {"at": p.get("at"), "from": p.get("from_status"),
                "to": p.get("to_status")}
        for k in ("reason", "discard_type", "blocked_by", "recheck_on", "caused_by"):
            if p.get(k):
                step[k] = p[k]
        hist.append(step)
    out({"ok": True, "id": d["id"], "title": d["title"], "status": d["status"],
         "count": len(hist), "history": hist})


def cmd_gauge_set(args):
    con = connect()
    res = api_gauge_set(con, args.key, value=args.value, unit=args.unit or "",
                        probe=args.probe or "", description=args.description or "")
    if res.get("ok") and res.get("changed") and not args.no_recheck:
        # `resource.changed` is the trigger that closes the loop: a measurement
        # moves, and the list of things that became possible falls out of an
        # indexed query — nobody has to remember they were waiting on it.
        res["recheck"] = api_recheck(con, gauge_key=args.key, apply=args.apply)
    out(res)


def cmd_gauges(args):
    con = connect()
    rows = con.execute("SELECT * FROM nodes WHERE type='gauge' ORDER BY id").fetchall()
    res = []
    for r in rows:
        d = _node_dict(r)
        p = d["props"]
        k = p.get("key") or d["title"]
        waiting = con.execute(
            "SELECT COUNT(DISTINCT item_ref) c FROM blocked_item "
            "WHERE gauge_key=? AND resolved_at=''", (k,)).fetchone()["c"]
        item = {"key": k, "current_value": p.get("current_value"),
                "unit": p.get("unit", ""), "measured_at": p.get("measured_at", ""),
                "has_probe": bool(p.get("probe")), "blocking": waiting}
        if args.full and p.get("probe"):
            item["probe"] = p["probe"]
        if p.get("description"):
            item["description"] = p["description"]
        res.append(item)
    no_probe = [i["key"] for i in res if not i["has_probe"] and i["blocking"]]
    payload = {"ok": True, "count": len(res), "results": res}
    if no_probe:
        payload["warning"] = (f"blocking with no probe: {', '.join(no_probe)} — "
                              f"a gauge nobody measures never re-checks anything")
    out(payload)


def cmd_gauge_probe(args):
    con = connect()
    res = api_gauge_probe(con, args.key or "")
    if res.get("changed") and not args.no_recheck:
        res["recheck"] = {k: api_recheck(con, gauge_key=k, apply=args.apply)
                          for k in res["changed"]}
    out(res)


def cmd_recheck(args):
    con = connect()
    if args.rebuild:
        n = _rebuild_blocked(con)
        out({"ok": True, "rebuilt": n,
             "hint": "index re-derived from the transition log"}); return
    out(api_recheck(con, gauge_key=args.key or "", apply=args.apply,
                    quiet_days=int(args.quiet_days), force=args.force))


def cmd_context_event(args):
    con = connect()
    out(api_context_event(con, args.agent, args.op, args.item_type, args.item_ref,
                          tokens=args.tokens or 0, message_id=args.message or ""))


def cmd_context_at(args):
    con = connect()
    out(api_context_at(con, args.agent, at=args.at or ""))


def build_parser():
    p = argparse.ArgumentParser(prog="graph", description="second-brain graph engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("stats").set_defaults(func=cmd_stats)

    a = sub.add_parser("add-node"); a.set_defaults(func=cmd_add_node)
    a.add_argument("--type", required=True)
    a.add_argument("--title", required=True)
    a.add_argument("--body"); a.add_argument("--props")
    a.add_argument("--status"); a.add_argument("--due"); a.add_argument("--id")
    a.add_argument("--realm", help="section: optibus | personal | ... (default $SECOND_BRAIN_REALM or optibus)")
    a.add_argument("--area", help="file under a domain area (hub_quote | quote | hub_health ...) "
                                 "— adds the part-of edge; errors if the area does not exist")
    a.add_argument("--force-new", action="store_true",
                   help="person only: skip the identity guard and create a second "
                        "node even if the name matches an existing person")

    a = sub.add_parser("people"); a.set_defaults(func=cmd_people)
    a.add_argument("--all", action="store_true",
                   help="include nodes merged away (resolvers), normally hidden")

    a = sub.add_parser("who"); a.set_defaults(func=cmd_who)
    a.add_argument("name", help="any spelling — nickname, surname, email")

    a = sub.add_parser("person-dupes").set_defaults(func=cmd_person_dupes)

    a = sub.add_parser("merge-person"); a.set_defaults(func=cmd_merge_person)
    a.add_argument("--into", required=True, metavar="ID",
                   help="the person node to KEEP (canonical)")
    a.add_argument("--from", required=True, dest="from_id", metavar="ID",
                   help="the duplicate to fold in — kept as a resolver, never deleted")

    a = sub.add_parser("add-alias"); a.set_defaults(func=cmd_add_alias)
    a.add_argument("--id", required=True)
    a.add_argument("--alias", required=True,
                   help="a spelling the heuristic cannot infer (a nickname, a handle)")

    a = sub.add_parser("flush").set_defaults(func=cmd_flush)
    sub.add_parser("neo4j-status").set_defaults(func=cmd_neo4j_status)
    sub.add_parser("sections").set_defaults(func=cmd_sections)
    sub.add_parser("areas").set_defaults(func=cmd_areas)

    a = sub.add_parser("add-area"); a.set_defaults(func=cmd_add_area)
    a.add_argument("--title", required=True)
    a.add_argument("--id", help="default hub_<slug of title>")
    a.add_argument("--domain", help="short domain key (default: slug of title)")
    a.add_argument("--body"); a.add_argument("--realm")

    a = sub.add_parser("ingest-conventions", description=(
        "Load CONVENTIONS.md into the brain as rule nodes, one per section. "
        "Idempotent; never overwrites a user-authored or user-edited rule."))
    a.set_defaults(func=cmd_ingest_conventions)
    a.add_argument("--file", help="default: the plugin's CONVENTIONS.md")
    a.add_argument("--realm", help="default shared")

    a = sub.add_parser("add-rule", description=(
        "Author or override a convention rule (the mutable half of the genome)."))
    a.set_defaults(func=cmd_add_rule)
    a.add_argument("--title", required=True)
    a.add_argument("--body"); a.add_argument("--id")
    a.add_argument("--scenario", help="cue words for match-rules (default: the title)")
    a.add_argument("--section", help="provenance label, e.g. 2e or 'user'")
    a.add_argument("--source", choices=["user", "learned", "plugin"], default="user")
    a.add_argument("--overrides", help="rule id this replaces (it is marked superseded)")
    a.add_argument("--non-negotiable", dest="non_negotiable", action="store_true",
                   help="always returned by match-rules, cue or no cue")
    a.add_argument("--realm")

    a = sub.add_parser("match-rules", description=(
        "Which conventions apply to THIS situation — the token-light replacement "
        "for reading all of CONVENTIONS.md. No model call."))
    a.set_defaults(func=cmd_match_rules)
    a.add_argument("query")
    a.add_argument("--limit", type=int, default=6)
    a.add_argument("--budget", type=int, default=2600, help="max chars of rule text")
    a.add_argument("--full", action="store_true", help="whole rule bodies, unbudgeted")

    a = sub.add_parser("genome", description=(
        "The brain's own build blueprint — how to rebuild the Claude layer from the "
        "DB alone. --refresh re-derives the API surface and schema from the code."))
    a.set_defaults(func=cmd_genome)
    a.add_argument("--refresh", action="store_true")
    a.add_argument("--out", help="write the full kit (specs + rules) to a directory")

    a = sub.add_parser("update-node"); a.set_defaults(func=cmd_update_node)
    a.add_argument("--id", required=True)
    a.add_argument("--title"); a.add_argument("--body"); a.add_argument("--props")
    a.add_argument("--status"); a.add_argument("--due"); a.add_argument("--realm")

    a = sub.add_parser("del-node"); a.set_defaults(func=cmd_del_node)
    a.add_argument("--id", required=True)

    a = sub.add_parser("get-node"); a.set_defaults(func=cmd_get_node)
    a.add_argument("--id", required=True)

    a = sub.add_parser("add-edge"); a.set_defaults(func=cmd_add_edge)
    a.add_argument("--src", required=True); a.add_argument("--dst", required=True)
    a.add_argument("--rel", required=True); a.add_argument("--props")

    a = sub.add_parser("del-edge"); a.set_defaults(func=cmd_del_edge)
    a.add_argument("--src", required=True); a.add_argument("--dst", required=True)
    a.add_argument("--rel", required=True)

    a = sub.add_parser("recall"); a.set_defaults(func=cmd_recall)
    a.add_argument("query", nargs="?", default="")
    a.add_argument("--limit", type=int, default=8)
    a.add_argument("--depth", type=int, default=ACTIVATION_DEPTH,
                   help="how far activation spreads across edges (default 2)")
    a.add_argument("--budget", type=int, default=RECALL_BUDGET,
                   help="max chars of payload to return (token guard)")
    a.add_argument("--all", action="store_true", help="include done/cancelled")
    a.add_argument("--include-captures", action="store_true",
                   help="include raw capture nodes (normally the digest covers them)")
    a.add_argument("--no-wire", dest="wire", action="store_false",
                   help="do not create Hebbian co-activation edges")
    a.set_defaults(wire=True)

    a = sub.add_parser("consolidate"); a.set_defaults(func=cmd_consolidate)
    a.add_argument("--day", help="YYYY-MM-DD (default: every day with captures)")
    a.add_argument("--older-than", type=int, dest="older_than",
                   help="only days at least N days old")

    a = sub.add_parser("suggest-links"); a.set_defaults(func=cmd_suggest_links)
    a.add_argument("text", help="free text to scan for mentions of known nodes")
    a.add_argument("--limit", type=int, default=8)
    a.add_argument("--exclude", help="node id to never suggest (the node being edited)")

    a = sub.add_parser("search", description=(
        "Full-text search. BUDGETED by default (bodies trimmed to share "
        "--budget chars); --full restores whole bodies."))
    a.set_defaults(func=cmd_search)
    a.add_argument("query", nargs="?", default="")
    a.add_argument("--type"); a.add_argument("--limit", type=int, default=15)
    a.add_argument("--budget", type=int, default=READ_BUDGET,
                   help=f"max chars of body text (default {READ_BUDGET})")
    a.add_argument("--full", action="store_true", help="whole bodies, unbudgeted")

    a = sub.add_parser("resume", description=(
        "Rehydrate the working thread after clearing the context window: what was "
        "in play, what is parked, what is still open — inside a char budget, no "
        "model call. Run this right after /clear."))
    a.set_defaults(func=cmd_resume)
    a.add_argument("--budget", type=int, default=1800, help="max chars of brief")
    a.add_argument("--days", type=int, default=2, help="how far back 'in play' reaches")
    a.add_argument("--limit", type=int, default=8, help="max focus items")

    a = sub.add_parser("park", description=(
        "Move context OUT of the conversation into the brain and return a tiny "
        "receipt, so the model can drop the content from its window."))
    a.set_defaults(func=cmd_park)
    a.add_argument("--body", help="content to park (default: read stdin)")
    a.add_argument("--title"); a.add_argument("--id"); a.add_argument("--realm")
    a.add_argument("--area", help="file it under a domain area")
    a.add_argument("--source", help="where it came from (default: conversation)")
    a.add_argument("--about", action="append",
                   help="node id this context is about (repeatable)")

    a = sub.add_parser("unpark", description="Read parked context back, whole or sliced.")
    a.set_defaults(func=cmd_unpark)
    a.add_argument("--id", required=True)
    a.add_argument("--offset", type=int, help="start at this character")
    a.add_argument("--chars", type=int, help="return at most this many chars")

    a = sub.add_parser("maintain", description=(
        "Brain-side upkeep: consolidate old captures into per-day digests, repair "
        "reversed digest links, report what it saved. Non-destructive, idempotent, "
        "safe to schedule. This tidies the STORE, not the context window."))
    a.set_defaults(func=cmd_maintain)
    a.add_argument("--keep-days", type=int, dest="keep_days", default=2,
                   help="leave the last N days of captures loose (default 2)")

    a = sub.add_parser("list"); a.set_defaults(func=cmd_list)
    a.add_argument("--type"); a.add_argument("--status")
    a.add_argument("--area", help="only what is part-of this domain area "
                                 "(hub_quote | quote | hub_health ...)")
    a.add_argument("--due-before", dest="due_before")
    a.add_argument("--all", action="store_true",
                   help="include archived (done/cancelled) items")
    a.add_argument("--limit", type=int, default=50)

    a = sub.add_parser("complete"); a.set_defaults(func=cmd_complete)
    a.add_argument("--id", required=True)
    a.add_argument("--note", help="optional note about how it was completed")

    a = sub.add_parser("done"); a.set_defaults(func=cmd_done)
    a.add_argument("--limit", type=int, default=50)

    a = sub.add_parser("add-skill"); a.set_defaults(func=cmd_add_skill)
    a.add_argument("--name", required=True, help="short slug, e.g. triage-sentry")
    a.add_argument("--title", required=True)
    a.add_argument("--when", help="trigger conditions / when to use this skill")
    a.add_argument("--body", help="the procedure / steps")
    a.add_argument("--id")
    a.add_argument("--with-file", action="store_true",
                   help="also export a local SKILL.md (default: DB-only, the source of truth)")
    a.add_argument("--no-file", action="store_true",
                   help="(deprecated no-op — DB-only is now the default)")
    a.add_argument("--requires", help="skills this one's output is WRONG without (comma-separated)")
    a.add_argument("--waits-for", dest="waits_for",
                   help="skills that must finish first when they are also running (comma-separated)")
    a.add_argument("--enriched-by", dest="enriched_by",
                   help="skills to consume if they landed, never wait for (comma-separated)")
    a.add_argument("--reads", help="node types this skill consults (comma-separated)")
    a.add_argument("--writes", help="node types this skill leaves in the graph (comma-separated)")

    a = sub.add_parser("sync-skill-tree", description=(
        "Project the skill contracts into the graph as two trees (CONVENTIONS §5a): "
        "built-in skills under hub_skills_builtin, user skills under hub_skills_user. "
        "Built-in skills declare in their SKILL.md '## Depends on' block, user skills "
        "in props (add-skill --requires/--reads/--writes). Idempotent, and a true sync: "
        "generated edges that stopped being declared are pruned. Hand-made edges are "
        "never touched."))
    a.set_defaults(func=cmd_sync_skill_tree)
    a.add_argument("--realm", default="shared", help="section for the hubs and node-type nodes")
    a.add_argument("--no-node-types", action="store_true",
                   help="skip the node-type nodes; keep reads/writes as props only")

    a = sub.add_parser("ingest-skills"); a.set_defaults(func=cmd_ingest_skills)
    a.add_argument("--dir", action="append",
                   help="skill dir to scan (repeatable); default: plugin skills + learned_skills")
    a.add_argument("--realm", default="shared",
                   help="section for new skill nodes (default shared — process applies everywhere)")
    a.add_argument("--kind", help="label for skills from a custom --dir (default plugin)")
    a.add_argument("--all-md", action="store_true",
                   help="also ingest non-SKILL.md markdown files in the dirs")

    a = sub.add_parser("match-skills"); a.set_defaults(func=cmd_match_skills)
    a.add_argument("query", nargs="?", default="")
    a.add_argument("--limit", type=int, default=5)
    a.add_argument("--full", action="store_true",
                   help="return full skill bodies (default: cue-sized rows)")

    a = sub.add_parser("use-skill"); a.set_defaults(func=cmd_use_skill)
    a.add_argument("--id", required=True)

    a = sub.add_parser("open-loops"); a.set_defaults(func=cmd_open_loops)
    a.add_argument("--stale-days", dest="stale_days", type=int, default=2)
    a.add_argument("--limit", type=int, default=50)

    a = sub.add_parser("set-state"); a.set_defaults(func=cmd_set_state)
    a.add_argument("--id", required=True)
    a.add_argument("--state", required=True,
                   help="open|doing|blocked|waiting|needs-reply|done|closed|answered|...")
    a.add_argument("--waiting-on", dest="waiting_on")

    a = sub.add_parser("due"); a.set_defaults(func=cmd_due)
    a.add_argument("--within-days", dest="within_days", type=int, default=7)

    a = sub.add_parser("neighbors"); a.set_defaults(func=cmd_neighbors)
    a.add_argument("--id", required=True)
    a.add_argument("--rel"); a.add_argument("--depth", type=int, default=1)

    # --- traceability (§2k): the agent tree, ideas, gauges, recheck ---------
    a = sub.add_parser("conversation-start", description=(
        "Get-or-create the conversation node for a session (idempotent)."))
    a.set_defaults(func=cmd_conversation_start)
    a.add_argument("--session", required=True)
    a.add_argument("--cwd"); a.add_argument("--owner"); a.add_argument("--title")

    a = sub.add_parser("conversation-end", description=(
        "Close the conversation; any agent still open is marked abandoned."))
    a.set_defaults(func=cmd_conversation_end)
    a.add_argument("--session", required=True)
    a.add_argument("--reason")

    a = sub.add_parser("agent-spawn", description=(
        "Add an agent under EXACTLY one parent — a conversation or another agent."))
    a.set_defaults(func=cmd_agent_spawn)
    a.add_argument("--role", required=True)
    a.add_argument("--parent", help="parent node id; omit to use --session's conversation")
    a.add_argument("--session"); a.add_argument("--model")
    a.add_argument("--body"); a.add_argument("--cwd")

    a = sub.add_parser("agent-finish"); a.set_defaults(func=cmd_agent_finish)
    a.add_argument("--id", required=True)
    a.add_argument("--outcome", required=True, choices=list(AGENT_OUTCOMES))
    a.add_argument("--note")

    a = sub.add_parser("tree", description=(
        "The agent tree for a session or node, with its ideas. Token-free."))
    a.set_defaults(func=cmd_tree)
    a.add_argument("--session"); a.add_argument("--id")

    a = sub.add_parser("idea-propose", description=(
        "Record an alternative — every one, not just the chosen one."))
    a.set_defaults(func=cmd_idea_propose)
    a.add_argument("--title", required=True)
    a.add_argument("--detail"); a.add_argument("--agent"); a.add_argument("--session")
    a.add_argument("--realm")

    a = sub.add_parser("idea-select"); a.set_defaults(func=cmd_idea_select)
    a.add_argument("--id", required=True)
    a.add_argument("--reason"); a.add_argument("--caused-by", dest="caused_by")
    a.add_argument("--supersedes", help="idea id this one replaces")

    a = sub.add_parser("idea-discard", description=(
        "Discard an alternative WITH its reason. A conditional discard must "
        "carry at least one structured condition, or it is rejected."))
    a.set_defaults(func=cmd_idea_discard)
    a.add_argument("--id", required=True)
    a.add_argument("--type", required=True, choices=list(DISCARD_TYPES))
    a.add_argument("--reason", required=True, help="human-readable; never used in logic")
    a.add_argument("--blocked-by", dest="blocked_by", action="append",
                   metavar="'gauge.key >= value'",
                   help="structured condition; repeatable (all must hold)")
    a.add_argument("--recheck-on", dest="recheck_on", action="append",
                   metavar="EVENT", help="events that should trigger a re-check")
    a.add_argument("--caused-by", dest="caused_by", help="the turn that caused it")

    a = sub.add_parser("idea-revive"); a.set_defaults(func=cmd_idea_revive)
    a.add_argument("--id", required=True); a.add_argument("--reason")

    a = sub.add_parser("ideas"); a.set_defaults(func=cmd_ideas)
    a.add_argument("--status", choices=list(IDEA_STATUSES))
    a.add_argument("--agent"); a.add_argument("--limit", type=int, default=30)

    a = sub.add_parser("idea-history"); a.set_defaults(func=cmd_idea_history)
    a.add_argument("--id", required=True)

    a = sub.add_parser("gauge-set", description=(
        "Upsert one measurable thing in the catalog. A value change triggers "
        "a recheck of everything waiting on it."))
    a.set_defaults(func=cmd_gauge_set)
    a.add_argument("--key", required=True, metavar="gpu.vram_gb")
    a.add_argument("--value"); a.add_argument("--unit")
    a.add_argument("--probe", help="shell command whose first stdout line is the value")
    a.add_argument("--description")
    a.add_argument("--apply", action="store_true", help="also revive what unblocked")
    a.add_argument("--no-recheck", dest="no_recheck", action="store_true")

    a = sub.add_parser("gauges"); a.set_defaults(func=cmd_gauges)
    a.add_argument("--full", action="store_true", help="include probe commands")

    a = sub.add_parser("gauge-probe", description=(
        "Run the catalog's probes and record what they measured."))
    a.set_defaults(func=cmd_gauge_probe)
    a.add_argument("--key", help="probe one gauge (default: all with a probe)")
    a.add_argument("--apply", action="store_true")
    a.add_argument("--no-recheck", dest="no_recheck", action="store_true")

    a = sub.add_parser("recheck", description=(
        "What became possible again? One indexed pass, no model call. "
        "Notifies; --apply also marks the ideas revived."))
    a.set_defaults(func=cmd_recheck)
    a.add_argument("--key", help="only what waits on this gauge")
    a.add_argument("--apply", action="store_true")
    a.add_argument("--force", action="store_true", help="ignore the quiet window")
    a.add_argument("--quiet-days", dest="quiet_days", type=int,
                   default=NOTIFY_QUIET_DAYS)
    a.add_argument("--rebuild", action="store_true",
                   help="re-derive the index from the transition log")

    a = sub.add_parser("context-event", description=(
        "Record a CHANGE to the context window, not a snapshot of it."))
    a.set_defaults(func=cmd_context_event)
    a.add_argument("--agent", required=True)
    a.add_argument("--op", required=True, choices=["added", "removed", "summarized"])
    a.add_argument("--item-type", dest="item_type", required=True)
    a.add_argument("--item-ref", dest="item_ref", required=True)
    a.add_argument("--tokens", type=int); a.add_argument("--message")

    a = sub.add_parser("context-at", description=(
        "Replay the events and return the window as it stood."))
    a.set_defaults(func=cmd_context_at)
    a.add_argument("--agent", required=True)
    a.add_argument("--at", help="ISO instant (default: now)")

    a = sub.add_parser("export"); a.set_defaults(func=cmd_export)
    a.add_argument("--format", choices=["json", "md", "both"], default="both")
    a.add_argument("--out")

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except Exception as e:  # keep output machine-parseable
        out({"ok": False, "error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
