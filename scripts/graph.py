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
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

def _env(name: str, default: str = "") -> str:
    """Config env lookup: SECOND_BRAIN_<name>, else the default."""
    return os.environ.get("SECOND_BRAIN_" + name) or default


# Default section (realm) for new nodes; override with $SECOND_BRAIN_REALM or --realm.
DEFAULT_REALM = _env("REALM", "optibus")

# ---------------------------------------------------------------------------
# vocabulary (soft — not enforced, just documented + used for defaults)
# ---------------------------------------------------------------------------
NODE_TYPES = [
    "person", "task", "subtask", "requirement", "decision", "note",
    "project", "reminder", "writing-sample", "association", "meeting",
    "jira-issue", "resource", "skill", "area", "quote",
]

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
]

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

        CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
            title, body, content='nodes', content_rowid='rowid'
        );
        """
    )
    # FTS triggers (contentless-synced). rowid maps to nodes.rowid.
    con.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
            INSERT INTO nodes_fts(rowid, title, body)
            VALUES (new.rowid, new.title, new.body);
        END;
        CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
            INSERT INTO nodes_fts(nodes_fts, rowid, title, body)
            VALUES ('delete', old.rowid, old.title, old.body);
        END;
        CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
            INSERT INTO nodes_fts(nodes_fts, rowid, title, body)
            VALUES ('delete', old.rowid, old.title, old.body);
            INSERT INTO nodes_fts(rowid, title, body)
            VALUES (new.rowid, new.title, new.body);
        END;
        """
    )
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
    con.commit()


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


def _neo4j_call(statements, timeout=6):
    """POST Cypher to neo4j's HTTP tx endpoint. Returns (ok, error_or_None)."""
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


def _flush(con):
    """Drain the outbox to neo4j. Upserts current state of queued nodes/edges and
    applies queued deletes. On failure (neo4j down) leaves the queue intact."""
    rows = con.execute("SELECT seq,kind,ref FROM outbox ORDER BY seq").fetchall()
    if not rows:
        return {"ok": True, "pending": 0, "pushed": 0}
    node_ids, edge_refs, del_ids, del_edges, seqs = [], [], [], [], []
    for r in rows:
        seqs.append(r["seq"])
        if r["kind"] == "node":
            node_ids.append(r["ref"])
        elif r["kind"] == "edge":
            edge_refs.append(r["ref"])
        elif r["kind"] == "del_node":
            del_ids.append(r["ref"])
        elif r["kind"] == "del_edge":
            del_edges.append(r["ref"])
    nodes = [n for n in (_node_payload(con, i) for i in set(node_ids)) if n]
    edges = []
    for ref in set(edge_refs):
        s, d, rel = ref.split(EDGE_SEP)
        if con.execute("SELECT 1 FROM edges WHERE src=? AND dst=? AND rel=?", (s, d, rel)).fetchone():
            edges.append({"src": s, "dst": d, "rel": rel})
    dedges = [dict(zip(("src", "dst", "rel"), ref.split(EDGE_SEP))) for ref in set(del_edges)]
    stmts = []
    if nodes:
        stmts.append({"statement": _NODE_UPSERT, "parameters": {"nodes": nodes}})
    if edges:
        stmts.append({"statement": _EDGE_UPSERT, "parameters": {"edges": edges}})
    if dedges:
        stmts.append({"statement": _EDGE_DEL, "parameters": {"edges": dedges}})
    if del_ids:
        stmts.append({"statement": _NODE_DEL, "parameters": {"ids": list(set(del_ids))}})
    if not stmts:
        con.executemany("DELETE FROM outbox WHERE seq=?", [(s,) for s in seqs]); con.commit()
        return {"ok": True, "pending": 0, "pushed": 0}
    ok, err = _neo4j_call(stmts)
    if not ok:
        return {"ok": False, "error": err, "pending": len(seqs)}
    con.executemany("DELETE FROM outbox WHERE seq=?", [(s,) for s in seqs]); con.commit()
    return {"ok": True, "pending": 0,
            "pushed": {"nodes": len(nodes), "edges": len(edges),
                       "del_nodes": len(set(del_ids)), "del_edges": len(dedges)}}


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
    res = api_create_node(con, args.type, args.title, args.body,
                          _parse_props(args.props), args.status, args.due,
                          getattr(args, "realm", None), args.id)
    if aid and res.get("ok"):
        api_link(con, res["node"]["id"], aid, AREA_REL)
        res["area"] = aid
    out(res)


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
    out({"ok": True, "count": len(results), "results": results})


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
                 "updated_at": ts}
        con.execute("UPDATE nodes SET title=?, body=?, props=?, updated=? WHERE id=?",
                    (args.title, node_body, json.dumps(props), ts, node_id))
    else:
        props = {"triggers": when, "path": str(skill_path),
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


def cmd_match_rules(args):
    """Which conventions apply to THIS situation — the token-light replacement for
    reading all of CONVENTIONS.md.

    Pure FTS + SQL, no model call. Non-negotiable rules are always included
    (they apply whether or not the cue mentions them), then relevance order, then
    the payload is trimmed to a character budget so this can run on every request.
    """
    con = connect()
    q = (args.query or "").strip()
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
    matched = matched[: args.limit]
    # Universally-applicable rules are APPENDED, never counted against the limit —
    # otherwise a handful of them fills every slot and the rule you actually asked
    # for never appears (which is exactly what happened before this).
    seen = {r["id"] for r in matched}
    forced = [_node_dict(r) for r in con.execute(
        "SELECT * FROM nodes WHERE type=? AND status!='superseded'"
        " AND json_extract(props,'$.non_negotiable')=1"
        " ORDER BY json_extract(props,'$.section')", (RULE_TYPE,)).fetchall()
        if r["id"] not in seen]
    results = matched + forced
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
            api_link(con, did, c["id"], "part-of")
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
    out({"ok": True, "configured": bool(cfg["url"] and cfg["password"]),
         "url": cfg["url"], "up": _neo4j_up(), "pending_ops": pending})


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
6. **Reconnect the master store.** `spec_sync` covers the neo4j mirror, the outbox
   queue and reconciliation. Optional — the brain is fully functional on SQLite.
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
  neo4j.json        optional master-store config (secret)
  .last_consolidate stamp for the daily consolidation
```

Path resolution: `$SECOND_BRAIN_DB` (explicit file) -> `$SECOND_BRAIN_HOME/graph.db`
-> `~/.second_brain/graph.db`. Copying `graph.db` moves the brain; copying the home
directory moves brain + learned skills. Identical behaviour in Claude Code and the
Claude app.

Two orthogonal groupings: `realm` is which section of life (`optibus`, `personal`,
`shared`); an `area` (`hub_*`, linked with `part-of`) is which domain of meaning.
"""),
    "spec_sync": ("Master store, mirror and reconciliation", """
The unified source of truth is a neo4j graph (config `~/.second_brain/neo4j.json`);
local SQLite is a durable mirror. Every write goes to both. If neo4j is down the op
is queued in the `outbox` table and pushed when it returns.

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

    a = sub.add_parser("search"); a.set_defaults(func=cmd_search)
    a.add_argument("query", nargs="?", default="")
    a.add_argument("--type"); a.add_argument("--limit", type=int, default=15)

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
