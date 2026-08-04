#!/usr/bin/env python3
"""second-brain off-token query layer.

Read-only, dependency-free views over the brain that answer the everyday
questions — *what's due today / tomorrow / this week*, *what did I close*,
*show me a date window* — WITHOUT invoking Claude. This is the cheap path:
run it in a terminal, from a cron on the homelab, or behind a tiny web/phone
backend. It never writes, never calls the model, and needs no packages beyond
the stdlib.

It reuses the same engine + store as `graph.py` (the SQLite mirror `graph.py`
keeps in sync on every write), so it always sees current state. A `--backend
neo4j` mode reads the unified master over HTTP for the server case; the
`sqlite` backend is local, instant, and offline-friendly. With no flag the
backend is the `backend` setting (`<home>/settings.json`, written by the UI's
Settings page or by `scripts/neo4j_local.py up`), falling back to `sqlite`.

Output is a compact text table by default (read it with your eyes, no tokens);
pass `--json` to feed the web UI / phone backend.

Views:
  today            items due today (local tz)
  tomorrow         items due tomorrow
  week             items due within the next 7 days (overdue floats up)
  agenda           overdue + today + tomorrow — the "what now?" glance
  closed           recently closed/archived items (by completion/update time)
  range --from --to [--status open|done|any]   any date window, filtered

Dates are bucketed in your LOCAL timezone (override with --tz "America/Sao_Paulo",
$SECOND_BRAIN_TZ, or the `tz` setting in <home>/settings.json — the web UI's
Settings page writes that one). DB location follows the same rules as graph.py
($SECOND_BRAIN_DB / $SECOND_BRAIN_HOME).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta, date

# Reuse the engine: same DB path, config, parsing, and vocabulary as graph.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph  # noqa: E402


# ---------------------------------------------------------------------------
# timezone: buckets ("today") are local-calendar, not UTC
# ---------------------------------------------------------------------------
def _local_tz(name: str | None):
    """Resolve the timezone used to decide which calendar day a due-date is on.
    Order: explicit --tz, then $SECOND_BRAIN_TZ, then the `tz` setting in
    <home>/settings.json (what the web UI's Settings page writes), else the
    system local tz."""
    name = name or graph.setting("tz")
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            pass  # unknown name -> fall back to system local
    return datetime.now().astimezone().tzinfo


def _local_date(due: str, tz) -> date | None:
    """Local calendar date of a node's due string, or None if it has no/invalid due."""
    if not due:
        return None
    dt = graph._parse_dt(due)          # aware UTC datetime (or None)
    return dt.astimezone(tz).date() if dt else None


# ---------------------------------------------------------------------------
# backends: read every node, then filter in Python (a personal brain is small)
# ---------------------------------------------------------------------------
def _fetch_sqlite() -> list[dict]:
    con = graph.connect()
    rows = con.execute("SELECT * FROM nodes").fetchall()
    con.close()
    return [graph._node_dict(r) for r in rows]


def _fetch_neo4j() -> list[dict]:
    cfg = graph._neo4j_cfg()
    if not (cfg["url"] and cfg["password"]):
        raise SystemExit("neo4j backend requested but not configured "
                         "(set SECOND_BRAIN_NEO4J_URL/_PASSWORD or ~/.second_brain/neo4j.json)")
    stmt = {"statement": "MATCH (x:Node) RETURN properties(x) AS p"}
    endpoint = cfg["url"].rstrip("/") + f"/db/{cfg['db']}/tx/commit"
    import base64, urllib.request
    auth = base64.b64encode(f"{cfg['user']}:{cfg['password']}".encode()).decode()
    req = urllib.request.Request(
        endpoint, data=json.dumps({"statements": [stmt]}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        body = json.loads(resp.read())
    if body.get("errors"):
        raise SystemExit(f"neo4j error: {body['errors'][0].get('message')}")
    nodes = []
    for row in body["results"][0]["data"]:
        p = row["row"][0] or {}
        # neo4j stores fields + props flattened on the node; split them back out.
        node = {k: p.get(k, "") for k in
                ("id", "type", "title", "body", "status", "due", "realm",
                 "created", "updated")}
        node["props"] = {k: v for k, v in p.items() if k not in node}
        nodes.append(node)
    return nodes


def default_backend() -> str:
    """No flag, no argument → the `backend` setting (env first, then
    <home>/settings.json), else the SQLite mirror. Written by the Settings page
    and by `neo4j_local.py up`; an unknown value degrades to sqlite rather than
    failing a read."""
    val = graph.setting("backend", "sqlite")
    return val if val in graph.BACKENDS else "sqlite"


def _fetch(backend: str | None) -> list[dict]:
    if backend is None:
        backend = default_backend()
    if backend == "neo4j":
        return _fetch_neo4j()
    if backend == "auto":
        # prefer the master when it's actually reachable, else the local mirror
        if graph._neo4j_up() and graph._neo4j_cfg()["password"]:
            try:
                return _fetch_neo4j()
            except SystemExit:
                pass
        return _fetch_sqlite()
    return _fetch_sqlite()


# ---------------------------------------------------------------------------
# filtering
# ---------------------------------------------------------------------------
def _is_closed(d: dict) -> bool:
    status = (d.get("status") or "").lower()
    state = str((d.get("props") or {}).get("state", "")).lower()
    return status in graph.CLOSED_STATES or state in graph.CLOSED_STATES


# who "me" is — the brain owner. Tasks with no assignee default to the owner.
OWNER = graph._env("OWNER", "Gabriel")
# a task is NOT waiting on me if it's in one of these holding states
_PARKED_STATES = {"waiting", "blocked", "delegated", "needs-review"}


def norm_assignee(raw: str, owner: str = OWNER) -> str:
    """Normalize an assignee to a stable token for filtering/grouping:
    '' -> 'unassigned'; the owner / me / gabriel -> 'me'; else the lowercased name."""
    a = (raw or "").strip()
    if a == "":
        return "unassigned"
    o = owner.lower()
    if a.lower() in (o, "me", "self") or "gabriel" in a.lower():
        return "me"
    return a.lower()


def is_mine_actionable(d: dict, owner: str = OWNER) -> bool:
    """True only if the OWNER must act on it NOW: assigned explicitly to the owner
    (NOT unassigned, NOT someone else), not delegated away, and not parked
    waiting/blocked. This is the strict 'my plate' definition."""
    props = d.get("props") or {}
    if norm_assignee(props.get("assignee", ""), owner) != "me":
        return False
    if props.get("delegated"):
        return False
    if str(props.get("state", "")).lower() in _PARKED_STATES:
        return False
    return True


# edge relations that surface as dashboard flags
def _load_edges():
    con = graph.connect()
    rows = con.execute("SELECT src, dst, rel FROM edges").fetchall()
    con.close()
    return [(r["src"], r["dst"], r["rel"]) for r in rows]


def _relations(all_nodes, edges):
    """id -> {blocked_by:[titles], blocks:[titles], related:[titles]} from the graph."""
    title = {n["id"]: (n.get("title") or n["id"]) for n in all_nodes}
    rel = {}

    def add(nid, key, other):
        rel.setdefault(nid, {"blocked_by": [], "blocks": [], "related": []})[key].append(
            title.get(other, other))
    for s, dst, r in edges:
        if r in ("blocked-by", "depends-on"):
            add(s, "blocked_by", dst); add(dst, "blocks", s)
        elif r == "blocks":
            add(s, "blocks", dst); add(dst, "blocked_by", s)
        elif r == "relates-to":
            add(s, "related", dst); add(dst, "related", s)
    return rel


def _closed_at(d: dict) -> datetime | None:
    """When a closed item was closed — completed_at if recorded, else updated."""
    p = d.get("props") or {}
    return graph._parse_dt(p.get("completed_at") or d.get("updated") or "")


def _dated_open(nodes, tz, lo: date, hi: date) -> list[dict]:
    """Open (non-closed) nodes whose due-date falls in [lo, hi] inclusive."""
    out = []
    for d in nodes:
        if _is_closed(d):
            continue
        ld = _local_date(d.get("due", ""), tz)
        if ld and lo <= ld <= hi:
            out.append(d)
    return out


def _sort_by_due(nodes, tz):
    return sorted(nodes, key=lambda d: (_local_date(d.get("due", ""), tz) or date.max,
                                        d.get("title", "")))


def view_today(nodes, tz, ref: date):
    return _dated_open(nodes, tz, ref, ref)


def view_tomorrow(nodes, tz, ref: date):
    t = ref + timedelta(days=1)
    return _dated_open(nodes, tz, t, t)


def view_week(nodes, tz, ref: date):
    # overdue included so nothing quietly slips past the horizon
    return _sort_by_due(_dated_open(nodes, tz, date.min, ref + timedelta(days=7)), tz)


def view_agenda(nodes, tz, ref: date):
    return _sort_by_due(_dated_open(nodes, tz, date.min, ref + timedelta(days=1)), tz)


def view_closed(nodes, tz, ref: date, since_days: int):
    floor = datetime.now(timezone.utc) - timedelta(days=since_days)
    out = []
    for d in nodes:
        if not _is_closed(d):
            continue
        ca = _closed_at(d)
        if ca is None or ca >= floor:
            out.append((ca, d))
    out.sort(key=lambda t: t[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return [d for _, d in out]


def view_range(nodes, tz, lo: date, hi: date, status: str):
    out = []
    for d in nodes:
        closed = _is_closed(d)
        if status == "open" and closed:
            continue
        if status == "done" and not closed:
            continue
        ld = _local_date(d.get("due", ""), tz)
        if ld and lo <= ld <= hi:
            out.append(d)
    return _sort_by_due(out, tz)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def _due_cell(d: dict, tz) -> str:
    ld = _local_date(d.get("due", ""), tz)
    return ld.isoformat() if ld else "--"


def render_text(title: str, rows: list[dict], tz) -> str:
    lines = [f"{title}  ({len(rows)})"]
    if not rows:
        lines.append("  (nothing)")
        return "\n".join(lines)
    cols = []
    for d in rows:
        cols.append((
            _due_cell(d, tz),
            (d.get("type") or "")[:8],
            (d.get("status") or "-")[:9],
            (d.get("realm") or "")[:8],
            (d.get("title") or "").replace("\n", " ")[:60],
        ))
    w = [max(len(r[i]) for r in cols) for i in range(4)]
    for c in cols:
        lines.append(f"  {c[0]:<{w[0]}}  {c[1]:<{w[1]}}  {c[2]:<{w[2]}}  "
                     f"{c[3]:<{w[3]}}  {c[4]}")
    return "\n".join(lines)


def _slim(d: dict, tz) -> dict:
    """The fields a UI/phone client needs — small on purpose (cheap payloads)."""
    props = d.get("props") or {}
    rel = d.get("_rel") or {}
    return {"id": d.get("id"), "type": d.get("type"), "title": d.get("title"),
            "status": d.get("status"), "realm": d.get("realm"),
            "due": d.get("due") or "", "due_local": _due_cell(d, tz),
            "waiting_on": props.get("waiting_on", ""),
            "assignee": norm_assignee(props.get("assignee", "")),
            "kind": norm_kind(props),
            "delegated": props.get("delegated", ""),
            "mine": is_mine_actionable(d),
            "blocked_by": rel.get("blocked_by", []),
            "blocks": rel.get("blocks", []),
            "related": rel.get("related", [])}


def node_detail(node_id: str) -> dict:
    """Full node + its edges (named neighbors, with direction) — for the expand
    view. Read straight from the local mirror; token-free."""
    con = graph.connect()
    row = con.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not row:
        return {"ok": False, "error": f"no node {node_id}"}
    d = graph._node_dict(row)
    edges = []
    for e in graph._edges_of(con, node_id):
        if e["src"] == node_id:
            edges.append({"dir": "out", "rel": e["rel"], "id": e["dst"],
                          "title": e.get("dst_title", e["dst"]), "type": e.get("dst_type", "")})
        else:
            edges.append({"dir": "in", "rel": e["rel"], "id": e["src"],
                          "title": e.get("src_title", e["src"]), "type": e.get("src_type", "")})
    con.close()
    return {"ok": True, "id": d["id"], "type": d["type"], "title": d["title"],
            "status": d["status"], "due": d["due"], "realm": d.get("realm", ""),
            "body": d["body"], "props": d["props"], "edges": edges}


def assignees(backend="sqlite") -> dict:
    """Distinct assignee tokens across tasks (+counts) — powers the UI dropdown."""
    counts = {}
    for n in _fetch(backend):
        if n.get("type") not in ("task", "subtask"):
            continue
        if _is_closed(n):
            continue
        a = norm_assignee((n.get("props") or {}).get("assignee", ""))
        counts[a] = counts.get(a, 0) + 1
    return counts


def norm_realm(raw: str) -> str:
    """A node with no realm belongs to the configured default — same convention
    the engine applies on write, so filtering never silently drops those nodes."""
    return (raw or "").strip().lower() or graph.DEFAULT_REALM


def realms(backend="sqlite") -> dict:
    """Every realm present in the brain, with two counts each.

    Realms are open vocabulary — anyone can invent one with `--realm`, so the UI
    must read them from here instead of shipping a list. `nodes` counts the whole
    graph (what the map filters over); `open_tasks` counts only actionable rows
    (what the dashboard filters over) so a chip's number matches the list it
    produces. A realm with 40 nodes and 0 open tasks is real, and saying so beats
    showing a count that filters down to nothing.
    """
    out: dict[str, dict] = {}
    for n in _fetch(backend):
        r = out.setdefault(norm_realm(n.get("realm", "")),
                           {"nodes": 0, "open_tasks": 0})
        r["nodes"] += 1
        if n.get("type") in ("task", "subtask") and not _is_closed(n):
            r["open_tasks"] += 1
    return out


NO_KIND = "(none)"


def norm_kind(props: dict | None) -> str:
    """`props.kind` is optional and sparse — most nodes have none. Rather than
    hide those, they get an explicit bucket so "show me everything without a kind"
    is a filter you can actually express."""
    k = str((props or {}).get("kind", "") or "").strip().lower()
    return k or NO_KIND


def kinds(backend="sqlite") -> dict:
    """Every `props.kind` in the brain, with the same two counts as realms.

    Free-form and open vocabulary: skills use it for learned/plugin, notes use it
    for whatever the author needed. Read it, never ship a list."""
    out: dict[str, dict] = {}
    for n in _fetch(backend):
        k = out.setdefault(norm_kind(n.get("props")), {"nodes": 0, "open_tasks": 0})
        k["nodes"] += 1
        if n.get("type") in ("task", "subtask") and not _is_closed(n):
            k["open_tasks"] += 1
    return out


def node_types(backend="sqlite") -> dict:
    """Distinct node types (+counts). Open vocabulary, same as realms — the engine
    never restricts `--type`, so the UI has no business hardcoding the list."""
    counts: dict[str, int] = {}
    for n in _fetch(backend):
        t = n.get("type") or ""
        counts[t] = counts.get(t, 0) + 1
    return counts


VIEWS = ["agenda", "open", "today", "tomorrow", "week", "closed", "range"]


def view_open(nodes, tz):
    """All open (non-closed) tasks/subtasks regardless of due date — the full
    backlog. Dated first (soonest), then undated by title. This is the view where
    the assignee filter is most useful (undated tasks show here too)."""
    out = [n for n in nodes if n.get("type") in ("task", "subtask") and not _is_closed(n)]
    return sorted(out, key=lambda d: (_local_date(d.get("due", ""), tz) or date.max,
                                      d.get("title", "")))


def compute(view, *, date_from=None, date_to=None, status="any",
            since_days=7, tz=None, backend=None, mine=False, assignee=None,
            realm=None, kind=None):
    """Run a view and return (title, rows, tz, ref). The single entry point both
    the CLI and the web/phone server call — no query logic lives anywhere else.
    `mine=True` keeps only tasks the owner needs to act on; `assignee` (comma list
    of normalized tokens: me / unassigned / <name>) filters by owner; `realm`
    (comma list) scopes to one or more domains, `kind` (comma list, `(none)` for
    nodes carrying no kind) scopes by `props.kind`. Rows are annotated with
    blocked_by / blocks / related from the graph edges."""
    tzinfo = _local_tz(tz)
    ref = datetime.now(tzinfo).date()
    all_nodes = _fetch(backend)
    nodes = all_nodes
    if realm:
        wanted_realms = {w.strip().lower() for w in realm.split(",") if w.strip()}
        nodes = [n for n in nodes if norm_realm(n.get("realm", "")) in wanted_realms]
    if kind:
        wanted_kinds = {w.strip().lower() for w in kind.split(",") if w.strip()}
        nodes = [n for n in nodes if norm_kind(n.get("props")) in wanted_kinds]
    if mine:
        nodes = [n for n in nodes if is_mine_actionable(n)]
    if assignee:
        wanted = {w.strip().lower() for w in assignee.split(",") if w.strip()}

        def _amatch(n):
            na = norm_assignee((n.get("props") or {}).get("assignee", ""))
            if na not in wanted:
                return False
            # selecting "me" means MY actionable work, not every task tagged me
            return is_mine_actionable(n) if na == "me" else True
        nodes = [n for n in nodes if _amatch(n)]

    if view == "open":
        title, rows = "Open tasks (all, by due)", view_open(nodes, tzinfo)
    elif view == "today":
        title, rows = f"Due today ({ref})", view_today(nodes, tzinfo, ref)
    elif view == "tomorrow":
        t = ref + timedelta(days=1)
        title, rows = f"Due tomorrow ({t})", view_tomorrow(nodes, tzinfo, ref)
    elif view == "week":
        title, rows = "Due within 7 days (+ overdue)", view_week(nodes, tzinfo, ref)
    elif view == "agenda":
        title, rows = "Agenda — overdue + today + tomorrow", view_agenda(nodes, tzinfo, ref)
    elif view == "closed":
        title, rows = (f"Closed in the last {since_days} day(s)",
                       view_closed(nodes, tzinfo, ref, since_days))
    elif view == "range":
        if not (date_from and date_to):
            raise ValueError("range needs date_from and date_to (YYYY-MM-DD)")
        lo, hi = date.fromisoformat(date_from), date.fromisoformat(date_to)
        title, rows = (f"{lo}..{hi}  (status={status})",
                       view_range(nodes, tzinfo, lo, hi, status))
    else:
        raise ValueError(f"unknown view: {view}")

    rel = _relations(all_nodes, _load_edges())
    for r in rows:
        r["_rel"] = rel.get(r["id"], {})
    return title, rows, tzinfo, ref


def run_json(view, **opts) -> dict:
    """The JSON contract the web UI + phone backend consume (slim payloads)."""
    title, rows, tz, ref = compute(view, **opts)
    if opts.get("mine"):
        title += "  · mine"
    if opts.get("realm"):
        title += f"  · {opts['realm']}"
    if opts.get("kind"):
        title += f"  · kind={opts['kind']}"
    return {"ok": True, "view": view, "title": title, "ref": ref.isoformat(),
            "count": len(rows), "results": [_slim(d, tz) for d in rows]}


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(prog="brain-query",
                                description="off-token read-only views over the brain")
    p.add_argument("view", choices=VIEWS)
    p.add_argument("--from", dest="date_from", help="range start YYYY-MM-DD")
    p.add_argument("--to", dest="date_to", help="range end YYYY-MM-DD")
    p.add_argument("--status", choices=["open", "done", "any"], default="any",
                   help="range filter (default any)")
    p.add_argument("--since-days", type=int, default=7,
                   help="closed: look back this many days (default 7)")
    p.add_argument("--tz", help="IANA tz for day buckets (default $SECOND_BRAIN_TZ / system)")
    p.add_argument("--backend", choices=list(graph.BACKENDS), default=None,
                   help="where to read: sqlite mirror · neo4j master over HTTP · "
                        "auto (neo4j when reachable, else the mirror). Default is "
                        "the `backend` setting, else sqlite.")
    p.add_argument("--mine", action="store_true",
                   help="only tasks the owner ($SECOND_BRAIN_OWNER) needs to act on "
                        "(assigned to me, not delegated, not parked waiting)")
    p.add_argument("--assignee",
                   help="comma list of assignee tokens to keep: me | unassigned | <name>")
    p.add_argument("--realm",
                   help="comma list of realms to keep (optibus | personal | ...); "
                        "omit for every realm")
    p.add_argument("--kind",
                   help="comma list of props.kind values to keep; '(none)' selects "
                        "nodes that carry no kind")
    p.add_argument("--json", action="store_true", help="emit JSON (for UI/phone backend)")
    args = p.parse_args(argv)

    try:
        title, rows, tz, ref = compute(
            args.view, date_from=args.date_from, date_to=args.date_to,
            status=args.status, since_days=args.since_days, tz=args.tz,
            backend=args.backend, mine=args.mine, assignee=args.assignee,
            realm=args.realm, kind=args.kind)
    except ValueError as e:
        p.error(str(e))
    if args.mine:
        title += "  · mine"
    if args.assignee:
        title += f"  · {args.assignee}"
    if args.realm:
        title += f"  · {args.realm}"
    if args.kind:
        title += f"  · kind={args.kind}"

    if args.json:
        json.dump({"ok": True, "view": args.view, "title": title,
                   "ref": ref.isoformat(), "count": len(rows),
                   "results": [_slim(d, tz) for d in rows]},
                  sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        print(render_text(title, rows, tz))


if __name__ == "__main__":
    main()
