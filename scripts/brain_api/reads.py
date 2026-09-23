"""GET /api/* — the reads the page and the map make.

Most of these answer 200 even when `ok` is false (an unknown node, a keep_days
that is not a number): the read worked, and what it found is "nothing". The
exceptions are the dashboard views (400 on a bad query, 404 on an unknown view)
and the bundle download (404).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

import brain_query
import graph
import trigger

from . import commands, payloads, state, trigger_runtime

router = APIRouter(prefix="/api")


@router.get("/graph", tags=["graph"], summary="The whole graph, for the map",
            description="Nodes, edges, and the facet counts the map builds its filters "
                        "from. Read from the neo4j master when one is reachable, else "
                        "from the local sqlite mirror.")
def get_graph():
    return payloads.graph_payload()


@router.get("/views", tags=["dashboard"], summary="The names of the date views")
def views():
    return {"ok": True, "views": brain_query.VIEWS}


@router.get("/assignees", tags=["vocabulary"],
            summary="Every assignee token on an open task, with counts")
def assignees():
    return {"ok": True, "assignees": brain_query.assignees("auto")}


@router.get("/realms", tags=["vocabulary"],
            summary="Every realm in the brain, with two counts each")
def realms():
    # open vocabulary — the UI reads, never ships a list
    return {"ok": True, "realms": brain_query.realms("auto"),
            "default": graph.DEFAULT_REALM}


@router.get("/node", tags=["graph"], summary="One node in full, plus its named neighbours")
def node(id: str = Query("", description="The node id.")):
    return brain_query.node_detail(id)


@router.get("/people", tags=["people"], summary="Person nodes, for the assignee picker")
def people():
    rows = graph.person_rows(graph.connect())
    return {"ok": True, "people": [
        {"id": d["id"], "title": d["title"], "aliases": graph.person_aliases(d)[1:],
         "realm": d.get("realm", "")}
        for d in sorted(rows, key=lambda x: x["title"].lower())]}


@router.get("/capture", tags=["settings"], summary="Is the ledger recording?",
            description="$MEMORY_LEDGER_OFF is per-process and OUTRANKS the "
                        "`<home>/.disabled` marker, so `env_off` tells a client whether "
                        "offering to turn capture back on would be a lie.")
def capture():
    return payloads.capture_payload()


@router.get("/ui-prefs", tags=["settings"],
            summary="The browser's own view state, if it is being synced",
            description="NOT settings: the browser owns it, and storing it here is "
                        "opt-in (`ui_sync`). The blob is opaque on purpose.")
def ui_prefs():
    return {"ok": True, "synced": graph.flag("ui_sync"),
            "path": str(graph.ui_prefs_path()), "prefs": graph.ui_prefs()}


@router.get("/backups", tags=["settings"],
            summary="The snapshots consolidation leaves behind",
            description="Point-in-time copies of the whole graph. Restore one by "
                        "copying it back over graph.db.")
def backups():
    return {"ok": True, "dir": str(graph.backups_dir()),
            "keep_days": graph.BACKUP_KEEP_DAYS, "backups": graph.list_backups()}


@router.get("/doctor", tags=["settings"], summary="The health check, as JSON",
            description="The same report `scripts/doctor.py` prints. `ok` means the "
                        "CALL succeeded; `healthy` is the verdict.")
def doctor():
    return payloads.doctor_payload()


@router.get("/consolidate-preview", tags=["settings"],
            summary="What consolidation WOULD do",
            description="A read: it computes the plan and writes nothing, from the same "
                        "`graph.consolidation_plan` the run itself uses.")
def consolidate_preview(keep_days: str = Query(
        "", description="Leave the last N days of captures loose. Default 2.")):
    return payloads.consolidate_preview(keep_days)


@router.get("/person-duplicates", tags=["people"],
            summary="People who look like the same person",
            description="Read-only: it proposes, and `POST /api/merge-person` acts.")
def person_duplicates():
    return {"ok": True, "duplicates": graph.person_duplicates(graph.connect())}


@router.get("/kinds", tags=["vocabulary"],
            summary="Every props.kind in the brain, with the same two counts")
def kinds():
    return {"ok": True, "kinds": brain_query.kinds("auto"),
            "none_token": brain_query.NO_KIND}


@router.get("/skills", tags=["contracts"],
            summary="The two skill trees and the rule catalogue",
            description="Built from the edges `sync-skill-tree` wrote, never re-derived "
                        "from the files here: two readers of one declaration cannot "
                        "disagree, two parsers of it eventually do.")
def skills():
    con = graph.connect()
    return {"ok": True, "trees": graph.skill_trees(con),
            "rules": graph.rule_catalog(con),
            # processes are realm-scoped: every realm is sent and the page
            # narrows, defaulting to the realm new ones land in
            "processes": graph.process_catalog(con), "realm": graph.current_realm(),
            "node_types": graph.node_type_catalog(con), "areas": graph.area_titles(con)}


@router.get("/bundle", tags=["contracts"], summary="Download an exported bundle",
            response_class=FileResponse,
            responses={200: {"content": {"application/zip": {}}},
                       404: {"description": "No such bundle."}},
            description="`name` is reduced to a bare filename, so it can only name a "
                        "file in `<home>/bundles`.")
def bundle(name: str = Query("", description="The bundle filename.")):
    path = commands.bundle_path(name)
    if not path:
        return JSONResponse({"ok": False, "error": "no such bundle"}, status_code=404)
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.get("/rule", tags=["contracts"], summary="One rule in full")
def rule(id: str = Query("", description="The rule id.")):
    row = graph.connect().execute("SELECT * FROM nodes WHERE id=? AND type=?",
                                  (id, graph.RULE_TYPE)).fetchone()
    return {"ok": bool(row), "rule": graph._node_dict(row) if row else None}


@router.get("/settings", tags=["settings"],
            summary="Stored preferences, and what is actually in effect",
            description="They differ whenever an environment variable overrides the "
                        "file, so `env` names every key the shell has taken over.")
def settings():
    return payloads.settings_payload()


@router.get("/timezones", tags=["settings"], summary="Every IANA zone name, from the stdlib")
def timezones():
    return {"ok": True, "zones": payloads.timezones()}


@router.get("/status", tags=["settings"],
            summary="Where the data lives and whether the master is reachable",
            description="The neo4j password is NEVER sent to the browser; this reports "
                        "`password_set` only.")
def status():
    return payloads.status_payload()


@router.get("/trigger", tags=["trigger"],
            summary="Trigger watchers, config and the newest requests",
            description="What `<home>/trigger.json` configures (never the tokens, only "
                        "whether each is set), what the watchers in this process are "
                        "doing, the request counts by status, the conversations enabled, "
                        "and the newest requests.")
def trigger_status(limit: int = Query(10, ge=1, le=200)):
    return {**trigger.status_payload(limit), "runtime": trigger_runtime.status(),
            "watching": state.TRIGGERS}


@router.get("/trigger-request", tags=["trigger"],
            summary="One request and every action taken for it")
def trigger_request(id: str = Query("", description="The request id (UUID).")):
    return trigger.request_detail(id)


@router.get("/meta", tags=["vocabulary"], summary="The vocabulary, so no client hard-codes it",
            description="`rels` and `statuses` are CLOSED and enforced on write. "
                        "`realms`, `kinds` and `types` are OPEN: whatever this brain "
                        "actually contains.")
def meta():
    return {
        "ok": True, "rels": graph.EDGE_RELS, "statuses": graph.TASK_STATUSES,
        "realms": brain_query.realms("auto"), "default_realm": graph.DEFAULT_REALM,
        "kinds": brain_query.kinds("auto"), "no_kind_token": brain_query.NO_KIND,
        "types": brain_query.node_types("auto"),
        # The contract shapes (§5.1, §5b.1) come from the engine too. A form that
        # hard-coded the eight headings would be a second definition of the
        # shape, and the one nothing checks.
        "skill_sections": [
            {"field": f, "heading": h, "required": r,
             "generated": f in ("rules_honored", "depends_on")}
            for f, h, r in graph.SKILL_SECTIONS],
        "rule_sections": [{"field": f, "heading": h, "required": r}
                          for f, h, r in graph.RULE_SECTIONS],
        "decl_labels": [{"field": f, "label": lab} for f, lab in graph.DECL_LABELS],
        "rule_sources": sorted(graph.RULE_PRECEDENCE),
        # the declarable vocabulary, not just the types in use: a new skill
        # legitimately writes a type this brain has never held
        "node_types": graph.NODE_TYPES,
        "writable": state.WRITABLE, "boot": state.BOOT,
    }


@router.get("/suggest-links", tags=["graph"], summary="What a piece of text mentions, and how",
            description="Scans the text for nodes this brain already holds and reads "
                        "the wording around each mention to pick a relation. A "
                        "suggestion engine, not an oracle — let a person confirm each.")
def suggest_links(text: str = Query("", description="The text to scan."),
                  exclude: str = Query("", description="A node id to leave out.")):
    res = graph.suggest_links(graph.connect(), text, 8, exclude)
    return {"ok": True, "count": len(res), "suggestions": res}


@router.get("/search", tags=["graph"], summary="Find a node by title, for the relation picker",
            description="Under two characters it answers with an empty list rather than "
                        "scanning the brain.")
def search(q: str = Query("", description="Title substring. Two characters minimum."),
           limit: str = Query("", description="1–30, default 12.")):
    term = q.strip()
    if len(term) < 2:
        return {"ok": True, "results": []}
    n = max(1, min(30, int(limit))) if limit.isdigit() else 12
    rows = graph.connect().execute(
        "SELECT id,type,title,status FROM nodes"
        " WHERE lower(title) LIKE ? ORDER BY length(title) LIMIT ?",
        (f"%{term.lower()}%", n)).fetchall()
    return {"ok": True, "results": [dict(r) for r in rows]}


# ------------------------------------------------------------ date views
# Registered LAST: `/api/{view}` would otherwise shadow every route above.
@router.get("/{view}", tags=["dashboard"], summary="Run one date view",
            responses={400: {"description": "The query was refused."},
                       404: {"description": "No view by that name."}},
            description="The same query layer the CLI uses, over HTTP. `realm` and "
                        "`kind` take comma lists; omitting one means every value.")
def dashboard(
    view: str,
    mine: Optional[str] = Query(None, description="Only rows that need action from the "
                                                  "brain's owner. `0`/`false`/`no` = off."),
    assignee: Optional[str] = Query(None, description="Comma list of assignee tokens. "
                                                      "`unassigned` is one of them."),
    realm: Optional[str] = Query(None, description="Comma list of realms."),
    kind: Optional[str] = Query(None, description="Comma list of props.kind values. "
                                                  "`(none)` selects nodes carrying none."),
    tz: Optional[str] = Query(None, description="IANA name overriding the configured "
                                                "timezone for this call."),
    backend: Optional[str] = Query(None, description="sqlite | neo4j | auto (default)."),
    status: Optional[str] = Query(None, description="range only: any | open | done."),
    date_from: Optional[str] = Query(None, alias="from",
                                     description="range only: ISO date, inclusive."),
    date_to: Optional[str] = Query(None, alias="to",
                                   description="range only: ISO date, inclusive."),
    since_days: Optional[str] = Query(None, description="closed only: how far back."),
):
    if view not in brain_query.VIEWS:
        return JSONResponse({"ok": False, "error": f"unknown view: {view}"},
                            status_code=404)
    # default to 'auto' so the dashboard reflects the SAME store as the map
    # (neo4j master when reachable, local sqlite mirror when offline)
    opts = {"backend": "auto"}
    for key, val in (("assignee", assignee), ("realm", realm), ("kind", kind),
                     ("tz", tz), ("backend", backend), ("status", status),
                     ("date_from", date_from), ("date_to", date_to)):
        if val:
            opts[key] = val
    if mine:
        opts["mine"] = mine not in ("0", "false", "no")
    if since_days and since_days.isdigit():
        opts["since_days"] = int(since_days)
    try:
        return brain_query.run_json(view, **opts)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
