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
    "jira-issue", "resource", "skill",
]
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
    out(api_create_node(con, args.type, args.title, args.body,
                        _parse_props(args.props), args.status, args.due,
                        getattr(args, "realm", None), args.id))


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
    out({"ok": True, "count": len(results),
         "results": results[: args.limit]})


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

    a = sub.add_parser("flush").set_defaults(func=cmd_flush)
    sub.add_parser("neo4j-status").set_defaults(func=cmd_neo4j_status)
    sub.add_parser("sections").set_defaults(func=cmd_sections)

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

    a = sub.add_parser("suggest-links"); a.set_defaults(func=cmd_suggest_links)
    a.add_argument("text", help="free text to scan for mentions of known nodes")
    a.add_argument("--limit", type=int, default=8)
    a.add_argument("--exclude", help="node id to never suggest (the node being edited)")

    a = sub.add_parser("search"); a.set_defaults(func=cmd_search)
    a.add_argument("query", nargs="?", default="")
    a.add_argument("--type"); a.add_argument("--limit", type=int, default=15)

    a = sub.add_parser("list"); a.set_defaults(func=cmd_list)
    a.add_argument("--type"); a.add_argument("--status")
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
