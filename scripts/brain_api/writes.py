"""POST /api/* — token-free CRUD from the browser.

Every route is plain SQL through the engine's `api_*` helpers, so the UI needs
no model to feed the brain (CONVENTIONS §2g). Vocabulary and closed sets are
enforced HERE, because the engine keeps them soft and this is the untrusted
boundary. Each route answers 200 when the result is `ok`, else 400 with the
engine's own reason in `error`.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Body as BodyParam, Depends
from fastapi.responses import JSONResponse

import graph
import trigger

from . import commands, models as m, payloads, state, trigger_runtime
from .errors import Refused

ENVELOPE_RESPONSES = {
    400: {"description": "Refused. `error` says why, in the words a person can act on."},
    403: {"description": "The server was started with --no-write."},
    500: {"description": "The handler raised; the reason is in `error`."},
}


def require_writable():
    if not state.WRITABLE:
        raise Refused(403, "server started with --no-write; writes are disabled")


router = APIRouter(prefix="/api", dependencies=[Depends(require_writable)],
                   responses=ENVELOPE_RESPONSES)


def reply(res: dict) -> JSONResponse:
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


def clip(value, limit=4000) -> str:
    """The length cap every text input gets. Over-long is truncated, not refused."""
    return value.strip()[:limit] if isinstance(value, str) else ""


def body(model):
    """A body that may be missing entirely: then it is the model's defaults."""
    return BodyParam(default_factory=model)


def _realm_or_error(raw):
    realm = clip(raw, 40).lower()
    if realm and realm != graph.slugify(realm):
        return realm, {"ok": False,
                       "error": "realm must be lowercase letters, digits and dashes"}
    return realm, None


# ------------------------------------------------------------------ tasks
@router.post("/set-due", tags=["tasks"], summary="Set or clear a due date")
def set_due(data: m.SetDue = body(m.SetDue)):
    nid = clip(data.id, 200)
    if not nid:
        return reply({"ok": False, "error": "missing id"})
    res = graph.api_update_node(graph.connect(), nid, due=clip(data.due, 40))
    return reply({"ok": True, "id": nid, "due": res["node"]["due"]}
                 if res.get("ok") else res)


@router.post("/add-task", tags=["tasks"], summary="Create a task",
             description="Created `open`, with `props.source = \"web-ui\"` so UI-origin "
                         "data stays distinguishable from hook-captured and "
                         "skill-written data.")
def add_task(data: m.AddTask = body(m.AddTask)):
    title = clip(data.title, 300)
    if not title:
        return reply({"ok": False, "error": "missing title"})
    ntype = clip(data.type, 20) or "task"
    if ntype not in ("task", "subtask"):
        return reply({"ok": False, "error": "type must be task or subtask"})
    props = {"source": "web-ui"}
    for key in ("assignee", "priority"):
        val = clip(getattr(data, key), 60)
        if val:
            props[key] = val
    con = graph.connect()
    res = graph.api_create_node(con, ntype, title, clip(data.body), props,
                                status="open", due=clip(data.due, 40),
                                realm=clip(data.realm, 40))
    if not res.get("ok"):
        return reply(res)
    nid = res["node"]["id"]
    # apply whichever suggested/edited links the user kept
    links, rejected = [], []
    for item in data.links[:20]:
        if item is None:
            continue
        dst, rel = item.id[:200], item.rel[:40]
        if rel not in graph.EDGE_RELS:
            rejected.append({"id": dst, "rel": rel, "error": "unknown relation"})
            continue
        r = graph.api_link(con, nid, dst, rel)
        (links if r.get("ok") else rejected).append(
            {"id": dst, "rel": rel, **({} if r.get("ok") else {"error": r.get("error")})})
    res["links"] = links
    if rejected:
        res["rejected"] = rejected
    return reply(res)


@router.post("/complete", tags=["tasks"], summary="Archive a task",
             description="Sets `status=done` and stamps `completed_at`. Never deletes "
                         "— finishing work is archiving (CONVENTIONS §2a).")
def complete(data: m.Complete = body(m.Complete)):
    nid = clip(data.id, 200)
    if not nid:
        return reply({"ok": False, "error": "missing id"})
    return reply(graph.api_complete(graph.connect(), nid, clip(data.note, 500)))


@router.post("/set-status", tags=["tasks"], summary="Move a task's state",
             description="Refused with the allowed set in the error string when the "
                         "status is not one the engine enforces.")
def set_status(data: m.SetStatus = body(m.SetStatus)):
    nid, status = clip(data.id, 200), clip(data.status, 20)
    if not nid:
        return reply({"ok": False, "error": "missing id"})
    if status not in graph.TASK_STATUSES:
        return reply({"ok": False, "error": "status must be one of "
                      f"{', '.join(graph.TASK_STATUSES)}"})
    return reply(graph.api_set_status(
        graph.connect(), nid, status,
        waiting_on=clip(data.waiting_on, 200) if data.waiting_on is not None else None))


@router.post("/set-assignee", tags=["tasks"], summary="Reassign a task")
def set_assignee(data: m.SetAssignee = body(m.SetAssignee)):
    nid = clip(data.id, 200)
    if not nid:
        return reply({"ok": False, "error": "missing id"})
    # "" is meaningful: it un-assigns
    return reply(graph.api_update_node(
        graph.connect(), nid, props_patch={"assignee": clip(data.assignee, 60)}))


# The priorities the UI offers. Open-ended in the store (it is just a prop), so a
# value already in the graph is left alone — this only gates what the UI writes.
PRIORITIES = ("", "P1", "P2", "P3", "P4")


@router.post("/update-node", tags=["graph"], summary="Rename or edit any node")
def update_node(data: m.UpdateNode = body(m.UpdateNode)):
    nid = clip(data.id, 200)
    if not nid:
        return reply({"ok": False, "error": "missing id"})
    fields, patch = {}, {}
    if data.title is not None:
        title = clip(data.title, 300)
        if not title:
            return reply({"ok": False, "error": "title cannot be empty"})
        fields["title"] = title
    if data.body is not None:
        fields["body"] = clip(data.body)
    if data.priority is not None:
        pri = clip(data.priority, 20)
        if pri not in PRIORITIES:
            return reply({"ok": False, "error": "priority must be one of "
                          f"{', '.join(p or '(none)' for p in PRIORITIES)}"})
        patch["priority"] = pri
    # due and realm ride along so one edit in the card is one write — one
    # `updated` bump, one neo4j sync, one version-token move for the poller.
    if data.due is not None:
        due = clip(data.due, 40)
        if due and not re.match(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?", due):
            return reply({"ok": False,
                          "error": "due must be YYYY-MM-DD or YYYY-MM-DD HH:MM"})
        fields["due"] = due
    if data.realm is not None:
        realm = clip(data.realm, 40)
        if realm:
            fields["realm"] = realm      # "" would mean "keep", not "clear"
    if not fields and not patch:
        return reply({"ok": False, "error": "nothing to update"})
    return reply(graph.api_update_node(graph.connect(), nid,
                                       props_patch=patch or None, **fields))


# ----------------------------------------------------------------- people
@router.post("/assign", tags=["people"], summary="Point a task at a person node",
             description="Rewires the `assigned-to` edge as well as the prop, which is "
                         "why it is a separate call from set-assignee.")
def assign(data: m.Assign = body(m.Assign)):
    """Assign to an existing PERSON NODE, never a free-typed name.

    Writes both halves of the truth: props.assignee (the string every query
    path already filters on) and the structural assigned-to edge, so the map
    shows the link. person_id="" un-assigns and drops the edge."""
    nid = clip(data.id, 200)
    if not nid:
        return reply({"ok": False, "error": "missing id"})
    con = graph.connect()
    prev = [e["dst"] for e in con.execute(
        "SELECT dst FROM edges WHERE src=? AND rel='assigned-to'", (nid,)).fetchall()]
    pid = clip(data.person_id, 200)
    if not pid:
        for old in prev:
            graph.api_unlink(con, nid, old, "assigned-to")
        return reply(graph.api_update_node(con, nid, props_patch={"assignee": ""}))
    row = con.execute("SELECT * FROM nodes WHERE id=?", (pid,)).fetchone()
    if not row:
        return reply({"ok": False, "error": f"no node {pid}"})
    person = graph._node_dict(row)
    if person["type"] != "person":
        return reply({"ok": False, "error": f"{pid} is a {person['type']}, not a person"})
    if (person["props"] or {}).get("superseded_by"):
        return reply({"ok": False, "error": f"{pid} was merged into "
                      f"{person['props']['superseded_by']}; assign to that node"})
    for old in prev:
        if old != pid:
            graph.api_unlink(con, nid, old, "assigned-to")
    graph.api_link(con, nid, pid, "assigned-to")
    # the person's own title is the token: norm_assignee lowercases it, so the
    # dashboard's chips and the "mine" filter keep working unchanged
    return reply(graph.api_update_node(con, nid, props_patch={"assignee": person["title"]}))


@router.post("/merge-person", tags=["people"], summary="Merge two person nodes")
def merge_person(data: m.MergePerson = body(m.MergePerson)):
    return reply(graph.api_merge_person(graph.connect(), clip(data.canonical, 200),
                                        clip(data.duplicate, 200)))


@router.post("/add-alias", tags=["people"], summary="Pin another spelling onto a person",
             description="Goes through the engine's `api_add_alias`, so the rules that "
                         "decide whether two names are one human are the ones already "
                         "in use. A spelling the person already answers to is a no-op, "
                         "not a duplicate.")
def add_alias(data: m.AddAlias = body(m.AddAlias)):
    pid, alias = clip(data.id, 200), clip(data.alias, 120)
    if not pid or not alias:
        return reply({"ok": False, "error": "id and alias are both required"})
    return reply(graph.api_add_alias(graph.connect(), pid, alias))


# ------------------------------------------------------------------ edges
def _edge_args(data: m.Edge):
    src, dst, rel = clip(data.src, 200), clip(data.dst, 200), clip(data.rel, 40)
    if not src or not dst:
        return None, {"ok": False, "error": "missing src or dst"}
    if rel not in graph.EDGE_RELS:
        return None, {"ok": False, "error": f"unknown relation: {rel}"}
    return (src, dst, rel), None


@router.post("/link", tags=["graph"], summary="Create a relation")
def link(data: m.Edge = body(m.Edge)):
    args, err = _edge_args(data)
    return reply(err or graph.api_link(graph.connect(), *args))


@router.post("/unlink", tags=["graph"], summary="Remove a relation")
def unlink(data: m.Edge = body(m.Edge)):
    args, err = _edge_args(data)
    return reply(err or graph.api_unlink(graph.connect(), *args))


# ------------------------------------------------ settings + maintenance
@router.post("/settings", tags=["settings"], summary="Save preferences",
             description="Writes `<home>/settings.json`. Only these keys, each "
                         "validated. An empty string REMOVES a key, falling back to "
                         "the environment variable or the built-in default.")
def save_settings(data: m.Settings = body(m.Settings)):
    """Every value is validated here, because a bad timezone would otherwise
    silently fall back to the system one and look like the setting did nothing."""
    sent = data.model_fields_set
    patch = {}
    if "tz" in sent:
        tz = clip(data.tz, 60)
        if tz:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(tz)
            except Exception:
                return reply({"ok": False, "error": f"unknown timezone: {tz}"})
        patch["tz"] = tz
    if "realm" in sent:
        realm, err = _realm_or_error(data.realm)
        if err:
            return reply(err)
        patch["realm"] = realm
    if "owner" in sent:
        # Deliberately permissive: a person's name is a person's name, and the
        # only thing the engine does with it is compare it to whatever is written
        # in an assignee field. Validating the SHAPE of a human name is how you
        # refuse to store someone's actual name.
        patch["owner"] = clip(data.owner, 80)
    for key in graph.BOOL_SETTINGS:
        if key not in sent:
            continue
        raw = getattr(data, key)
        if raw in (None, ""):
            patch[key] = None          # back to the default
        elif isinstance(raw, bool):
            patch[key] = raw
        else:
            return reply({"ok": False, "error": f"{key} must be true or false"})
    if "backend" in sent:
        be = clip(data.backend, 10).lower()
        if be and be not in graph.BACKENDS:
            return reply({"ok": False,
                          "error": "backend must be one of " + ", ".join(graph.BACKENDS)})
        patch["backend"] = be
    for key, lo, hi in (("map_poll_seconds", 1, 600), ("dash_refresh_seconds", 5, 3600)):
        if key not in sent:
            continue
        raw = getattr(data, key)
        if raw in (None, ""):
            patch[key] = ""
            continue
        try:
            val = int(raw)
        except (TypeError, ValueError):
            return reply({"ok": False, "error": f"{key} must be a whole number"})
        if not lo <= val <= hi:
            return reply({"ok": False, "error": f"{key} must be between {lo} and {hi}"})
        patch[key] = val
    if not patch:
        return reply({"ok": False, "error": "nothing to save"})
    graph.save_settings(patch)
    # This process read DEFAULT_REALM once, at import. Re-resolve it so a realm
    # changed in the browser applies to the next write, not the next restart.
    graph.DEFAULT_REALM = graph.setting("realm", "optibus")
    return reply(payloads.settings_payload())


@router.post("/neo4j-config", tags=["settings"], summary="Point the brain at a master",
             description="Writes `<home>/neo4j.json` mode 600 and probes it. A blank "
                         "password keeps the stored one; an empty url deletes the file "
                         "and resets the read backend to sqlite.")
def save_neo4j(data: m.Neo4jConfig = body(m.Neo4jConfig)):
    url = clip(data.url, 300)
    if url and not url.startswith(("http://", "https://")):
        return reply({"ok": False, "error": "url must be the neo4j HTTP endpoint, "
                                            "e.g. http://host:7474"})
    f = graph.home_dir() / "neo4j.json"
    if not url:
        f.unlink(missing_ok=True)
        # Leaving `backend: neo4j` behind would break every CLI read with no
        # master to read from, so removing the master resets the default too.
        if graph.settings().get("backend") in ("neo4j", "auto"):
            graph.save_settings({"backend": "sqlite"})
        return reply({"ok": True, "cleared": True, **payloads.status_payload()})
    cur = graph._neo4j_cfg()
    cfg = {"url": url, "db": clip(data.db, 60) or "neo4j",
           "user": clip(data.user, 60) or "neo4j"}
    # The password is never sent TO the browser, so a blank field means "leave
    # it alone" rather than "erase it".
    cfg["password"] = clip(data.password, 200) or cur["password"]
    if not cfg["password"]:
        return reply({"ok": False, "error": "password required"})
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    try:
        f.chmod(0o600)      # it holds a password
    except OSError:
        pass
    ok, err = graph._neo4j_call([{"statement": "RETURN 1"}])
    return reply({"ok": True, "saved": True, "reachable": ok, "error": err,
                  **payloads.status_payload()})


@router.post("/neo4j-test", tags=["settings"], summary="Probe the configured master")
def probe_neo4j(_data: m.Empty = body(m.Empty)):
    cfg = graph._neo4j_cfg()
    if not (cfg["url"] and cfg["password"]):
        return reply({"ok": False, "error": "neo4j not configured"})
    ok, err = graph._neo4j_call([{"statement": "RETURN 1"}])
    return reply({"ok": True, "reachable": ok, "error": err, "url": cfg["url"]})


@router.post("/flush", tags=["settings"], summary="Drain the outbox to the master")
def flush(_data: m.Empty = body(m.Empty)):
    con = graph.connect()
    pending = con.execute("SELECT COUNT(*) c FROM outbox").fetchone()["c"]
    res = graph._flush(con)
    res.setdefault("ok", True)
    res["was_pending"] = pending
    return reply(res)


_MAINTAIN = ("Non-destructive: every capture is kept and linked to the digest that "
             "now stands in for it (§2a). REFUSED without `confirm: true`, because it "
             "still rewrites props on every capture it touches — preview with "
             "GET /api/consolidate-preview first. A snapshot of the whole graph is "
             "taken before the first write.")


@router.post("/prune", tags=["settings"],
             summary="Run maintain — consolidate old captures", description=_MAINTAIN)
def prune(data: m.Maintain = body(m.Maintain)):
    return reply(commands.maintain(data.confirm, data.keep_days))


@router.post("/consolidate", tags=["settings"],
             summary="Consolidate (the same engine as prune)",
             description="Identical to /api/prune, under the name the CLI uses. "
                         + _MAINTAIN)
def consolidate(data: m.Maintain = body(m.Maintain)):
    return reply(commands.maintain(data.confirm, data.keep_days))


@router.post("/capture", tags=["settings"], summary="Turn capture on or off, machine-wide",
             description="Writes the same `<home>/.disabled` marker `graph.py disable` "
                         "writes — one switch, two front doors. Refused while "
                         "$MEMORY_LEDGER_OFF is set in the server's environment, "
                         "because that outranks the marker.")
def set_capture(data: m.Capture = body(m.Capture)):
    want = data.enabled
    if not isinstance(want, bool):
        return reply({"ok": False, "error": "enabled must be true or false"})
    if payloads.capture_payload()["env_off"] and want:
        return reply({"ok": False,
                      "error": "$MEMORY_LEDGER_OFF is set in the server's "
                               "environment and outranks this switch — unset it "
                               "there (rc file, cron, launchd) and restart"})
    marker = graph.home_dir() / graph.DISABLE_MARKER
    try:
        if want:
            marker.unlink(missing_ok=True)
        else:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch(exist_ok=True)
    except OSError as e:
        return reply({"ok": False, "error": f"could not write {marker}: {e}"})
    return reply({"ok": True, "capture": payloads.capture_payload()})


@router.post("/ui-prefs", tags=["settings"],
             summary="Store the browser's view state in the brain",
             description="REPLACES the stored blob rather than merging into it. "
                         "Accepted even when `ui_sync` is off, because the toggle that "
                         "enables syncing is itself one of these preferences.")
def save_ui_prefs(data: m.UiPrefs = body(m.UiPrefs)):
    res = graph.save_ui_prefs(data.prefs)
    if not res.get("ok"):
        return reply(res)
    return reply({**res, "synced": graph.flag("ui_sync")})


@router.post("/backup", tags=["settings"], summary="Snapshot the graph now",
             description="Takes a point-in-time copy and expires the ones past their "
                         "retention window.")
def backup(data: m.Backup = body(m.Backup)):
    res = graph.backup_db(clip(data.label, 40) or "manual")
    if not res.get("ok"):
        return reply(res)
    return reply({"ok": True, "created": res, "removed": graph.prune_backups(),
                  "backups": graph.list_backups()})


# ---------------------------------------------------------------- trigger
@router.post("/trigger-gate", tags=["trigger"], summary="Open or close a conversation",
             description="An open conversation runs other people's mentions; a closed one "
                         "refuses them. The owner's own mention always runs and opens it.")
def trigger_gate(data: m.TriggerGate = body(m.TriggerGate)):
    if not isinstance(data.enabled, bool):
        return reply({"ok": False, "error": "enabled must be true or false"})
    return reply(trigger.set_gate(trigger.db(), clip(data.scope, 200), data.enabled,
                                  by="web-ui"))


@router.post("/trigger-config", tags=["trigger"], summary="Change the trigger configuration",
             description="Merges the keys sent into `<home>/trigger.json` (mode 600) and, "
                         "when this server runs the watchers, restarts them on it. Secrets "
                         "are write-only: empty keeps the stored one, `clear` removes it, "
                         "and none is ever sent back.")
def trigger_config(data: m.TriggerConfig = body(m.TriggerConfig)):
    patch = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    if "clear" in patch:
        patch["clear"] = [c for c in patch["clear"] if isinstance(c, str)]
    res = trigger.save_config(patch)
    if res.get("ok"):
        res["runtime"] = trigger_runtime.restart() if state.TRIGGERS else trigger_runtime.status()
        res["watching"] = state.TRIGGERS
    return reply(res)


@router.post("/trigger-check", tags=["trigger"], summary="Check that a service is ready",
             description="Validates the configuration, and unless `offline`, calls Slack, "
                         "Jira and GitHub with the stored tokens to prove they work. Starts nothing and "
                         "writes nothing. `ready` is the verdict; `ok` means the check ran.")
def trigger_check(data: m.TriggerCheck = body(m.TriggerCheck)):
    return reply(trigger.check(trigger.load_config(), offline=data.offline is True,
                               only=clip(data.only, 20)))


@router.post("/trigger-channel", tags=["trigger"], summary="Open or close a Slack channel",
             description="An open channel runs everyone's mentions in every thread. Closing "
                         "one keeps its row, marked removed, so the list is also the history "
                         "of who opened what.")
def trigger_channel(data: m.TriggerChannel = body(m.TriggerChannel)):
    if not isinstance(data.enabled, bool):
        return reply({"ok": False, "error": "enabled must be true or false"})
    con = trigger.db()
    if data.enabled:
        res = trigger.add_channel(con, clip(data.channel, 300), clip(data.name, 80),
                                  by="web-ui", cfg=trigger.load_config())
    else:
        res = trigger.remove_channel(con, clip(data.channel, 300), by="web-ui")
    if res.get("ok"):
        res["channels"] = trigger.list_channels(con)
    return reply(res)


@router.post("/trigger-decline", tags=["trigger"], summary="Decline a request that breaks a rule",
             description="What the session calls instead of doing the work. Marks the request "
                         "`declined`, logs why, and returns the policy notice and the "
                         "signature for the session to post.")
def trigger_decline(data: m.TriggerDecline = body(m.TriggerDecline)):
    rid = clip(data.request_id, 60)
    if not rid:
        return reply({"ok": False, "error": "missing request_id"})
    return reply(trigger.decline(trigger.db(), rid, clip(data.reason, 300), clip(data.rule, 120)))


@router.post("/trigger-action", tags=["trigger"], summary="Log an action taken for a request",
             description="Append-only. What the headless session did, under the request id "
                         "it was started with.")
def trigger_action(data: m.TriggerAction = body(m.TriggerAction)):
    rid = clip(data.request_id, 60)
    if not rid:
        return reply({"ok": False, "error": "missing request_id"})
    return reply(trigger.log_action(trigger.db(), rid, clip(data.kind, 60),
                                    clip(data.target, 500), clip(data.detail, 4000)))


# ------------------------------------------- authoring skills and rules
# These build the contract through `graph.build_*_contract` and refuse an
# incomplete one, so this path is checked at write time — stricter than the
# CLI's, where `audit-contracts` reports the gap afterwards.
def _sections(raw, spec) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {f: clip(raw[f], 12000) for f, _h, _r in spec if isinstance(raw.get(f), str)}


def _rule_args(data: m.CreateRule):
    title = clip(data.title, 300)
    if not title:
        return None, {"ok": False, "error": "a rule needs a title"}
    source = clip(data.source, 20) or "user"
    if source not in graph.RULE_PRECEDENCE:
        return None, {"ok": False, "error": f"unknown source: {source}"}
    return {
        "title": title,
        "sections": _sections(data.sections, graph.RULE_SECTIONS),
        "scenario": clip(data.scenario, 2000),
        "source": source,
        "section": clip(data.section, 40),
        "non_negotiable": bool(data.non_negotiable),
        "overrides": clip(data.overrides, 200),
        "realm": clip(data.realm, 40),
        "node_id": clip(data.id, 200),
    }, None


@router.post("/create-rule", tags=["contracts"], summary="Author a rule (CONVENTIONS §5b)",
             description="Refused unless `sections.the_rule` and "
                         "`sections.judged_against` are filled.")
def create_rule(data: m.CreateRule = body(m.CreateRule)):
    args, err = _rule_args(data)
    return reply(err or graph.api_add_rule(graph.connect(), **args))


@router.post("/create-skill", tags=["contracts"], summary="Author a skill (CONVENTIONS §5)",
             description="Refused unless the typed sections of §5.1 are filled. "
                         "`new_rules[]` are created FIRST in the same call, and a failure "
                         "in one aborts everything — a skill is never left accountable "
                         "to a rule that does not exist.")
def create_skill(data: m.CreateSkill = body(m.CreateSkill)):
    con = graph.connect()
    title = clip(data.title, 300)
    name = clip(data.name, 120) or title
    if not title:
        return reply({"ok": False, "error": "a skill needs a title"})
    decl = {}
    for field, _label in graph.DECL_LABELS:
        val = getattr(data, field)
        if isinstance(val, list):
            decl[field] = [str(x)[:120] for x in val[:40]]
        elif isinstance(val, str):
            decl[field] = val[:1200]
    rule_ids = [r[:200] for r in data.rules[:40] if r and r.strip()]
    created = []
    for item in data.new_rules[:20]:
        if item is None:
            continue
        args, err = _rule_args(item)
        if err:
            return reply(err)
        res = graph.api_add_rule(con, **args)
        if not res.get("ok"):
            return reply({"ok": False, "error": f"rule {args['title']!r}: {res.get('error')}",
                          "created_rules": created})
        created.append(res["node"]["id"])
        rule_ids.append(res["node"]["id"])
    res = graph.api_add_skill(
        con, name=name, title=title, description=clip(data.description, 1000),
        triggers=clip(data.triggers, 2000),
        sections=_sections(data.sections, graph.SKILL_SECTIONS), decl=decl,
        rules=list(dict.fromkeys(rule_ids)), realm=clip(data.realm, 40),
        node_id=clip(data.id, 200))
    if created:
        res["created_rules"] = created
    return reply(res)


@router.post("/create-process", tags=["contracts"], summary="Record a process in a realm",
             description="A `process` node, part-of `hub_process`. `realm` defaults to "
                         "the configured one; an existing process `id` is refreshed "
                         "rather than duplicated.")
def create_process(data: m.CreateProcess = body(m.CreateProcess)):
    realm, err = _realm_or_error(data.realm)
    if err:
        return reply(err)
    return reply(graph.api_add_process(
        graph.connect(), title=clip(data.title, 300), body=clip(data.body, 20000),
        trigger=clip(data.trigger, 1000), realm=realm, node_id=clip(data.id, 200)))


# ------------------------------------------------ markdown sync (§5a)
@router.post("/export-skills", tags=["contracts"],
             summary="Write the skill tree out as markdown")
def export_skills(data: m.ExportSkills = body(m.ExportSkills)):
    d = clip(data.dir, 500)
    return reply(commands.graph_cmd(["export-skills", "--kind", clip(data.kind, 10)
                                     or "learned"] + (["--dir", d] if d else [])))


@router.post("/ingest-skills", tags=["contracts"], summary="Read a markdown folder back in",
             description="Re-running is safe: a skill is refreshed, and a rule this "
                         "brain already holds is kept, never overwritten.")
def ingest_skills(data: m.IngestSkills = body(m.IngestSkills)):
    d = clip(data.dir, 500)
    return reply(commands.graph_cmd(["ingest-skills"] + (["--dir", d] if d else [])))


# ------------------------------------------------- bundles (§5a, selective)
def _ids(values, limit=2000):
    return [x[:200] for x in values[:limit] if x and x.strip()]


@router.post("/export-bundle", tags=["contracts"], summary="Export a selection as one zip",
             description="Returns the path, a same-origin download URL, and any skill "
                         "REQUIRED by something exported that did not travel.")
def export_bundle(data: m.ExportBundle = body(m.ExportBundle)):
    spec = commands.spec_file({"skills": _ids(data.skills), "rules": _ids(data.rules),
                               "processes": _ids(data.processes)})
    try:
        res = commands.graph_cmd(["export-bundle", "--spec", str(spec)])
    finally:
        spec.unlink(missing_ok=True)
    if res.get("ok"):
        # the page offers a download; the path is kept too, because the point
        # of a bundle is often to copy it, not to re-download it
        res["download"] = "/api/bundle?name=" + quote(res["name"])
    return reply(res)


@router.post("/upload-bundle", tags=["contracts"],
             summary="Stage a bundle and read what is in it",
             description="READS ONLY — nothing is written. Returns a one-shot token and "
                         "every item with whether this brain already holds it. Capped at "
                         "32MB.")
def upload_bundle(data: m.UploadBundle = body(m.UploadBundle)):
    if not data.data:
        return reply({"ok": False, "error": "no file was sent"})
    try:
        blob = base64.b64decode(data.data.split(",")[-1], validate=True)
    except (binascii.Error, ValueError):
        return reply({"ok": False, "error": "the upload was not valid base64"})
    if len(blob) > commands.MAX_BUNDLE_BYTES:
        return reply({"ok": False, "error": "that bundle is larger than "
                      f"{commands.MAX_BUNDLE_BYTES // (1024 * 1024)}MB"})
    token = secrets.token_hex(16)
    path = commands.incoming_dir() / f"{token}.zip"
    path.write_bytes(blob)
    res = commands.graph_cmd(["inspect-bundle", "--zip", str(path)])
    if not res.get("ok"):
        path.unlink(missing_ok=True)
        return reply(res)
    res["token"] = token
    res["filename"] = clip(data.filename, 200)
    return reply(res)


@router.post("/import-bundle", tags=["contracts"], summary="Apply a staged bundle",
             description="Applies in the plan's order and consumes the token. An item "
                         "not mentioned in `select` is created when new and untouched "
                         "when it exists.")
def import_bundle(data: m.ImportBundle = body(m.ImportBundle)):
    path = commands.staged(clip(data.token, 64))
    if not path:
        return reply({"ok": False,
                      "error": "that upload is no longer staged — choose the file again"})
    select = {k[:200]: v for k, v in list((data.select or {}).items())[:2000]
              if v in ("update", "replace", "ignore")}
    if not select:
        return reply({"ok": False, "error": "nothing was selected to import"})
    realm, err = _realm_or_error(data.process_realm)
    if err:
        return reply(err)
    spec = commands.spec_file({"select": select})
    try:
        res = commands.graph_cmd(["import-bundle", "--zip", str(path), "--spec", str(spec)]
                                 + (["--process-realm", realm] if realm else []))
    finally:
        spec.unlink(missing_ok=True)
        # the staged zip has served its purpose; leaving it behind would
        # accumulate every bundle anyone ever previewed
        path.unlink(missing_ok=True)
    return reply(res)


# --------------------------------------------- local scripts (§5c)
@router.post("/export-scripts", tags=["scripts"], summary="Zip local scripts",
             description="All of them when `names` is empty. The zip lands in "
                         "`<home>/bundles` and downloads from `/api/bundle`.")
def export_scripts(data: m.ExportScripts = body(m.ExportScripts)):
    res = graph.export_scripts([n[:200] for n in data.names[:500] if n])
    if res.get("ok"):
        res["download"] = "/api/bundle?name=" + quote(res["name"])
    return reply(res)


@router.post("/upload-scripts", tags=["scripts"],
             summary="Stage a scripts zip (or one file) and read what is in it",
             description="READS ONLY — nothing is written to `<home>/scripts`. Returns "
                         "a one-shot token and every script with whether a local file "
                         "by that name exists and is identical.")
def upload_scripts(data: m.UploadScripts = body(m.UploadScripts)):
    if not data.data:
        return reply({"ok": False, "error": "no file was sent"})
    try:
        blob = base64.b64decode(data.data.split(",")[-1], validate=True)
    except (binascii.Error, ValueError):
        return reply({"ok": False, "error": "the upload was not valid base64"})
    if len(blob) > commands.MAX_BUNDLE_BYTES:
        return reply({"ok": False, "error": "that file is larger than "
                      f"{commands.MAX_BUNDLE_BYTES // (1024 * 1024)}MB"})
    filename = clip(data.filename, 200)
    token = secrets.token_hex(16)
    path = commands.incoming_dir() / f"{token}.zip"
    if blob[:2] == b"PK" or filename.lower().endswith(".zip"):
        path.write_bytes(blob)
    else:
        wrapped = graph.wrap_script(blob, filename, path)
        if not wrapped.get("ok"):
            return reply(wrapped)
    res = graph.inspect_scripts(path)
    if not res.get("ok"):
        path.unlink(missing_ok=True)
        return reply(res)
    res.pop("path", None)
    res["token"] = token
    res["filename"] = filename
    return reply(res)


@router.post("/import-scripts", tags=["scripts"], summary="Apply a staged scripts upload",
             description="Consumes the token. A new script is written for either mode; "
                         "an existing one is overwritten only for `replace`, after its "
                         "old bytes are copied to `<home>/backups/scripts-<stamp>/`.")
def import_scripts(data: m.ImportScripts = body(m.ImportScripts)):
    path = commands.staged(clip(data.token, 64))
    if not path:
        return reply({"ok": False,
                      "error": "that upload is no longer staged — choose the file again"})
    select = {k[:200]: v for k, v in list((data.select or {}).items())[:500]
              if v in graph.SCRIPT_MODES}
    if not select:
        return reply({"ok": False, "error": "nothing was selected to import"})
    try:
        return reply(graph.import_scripts(path, select))
    finally:
        path.unlink(missing_ok=True)


@router.post("/delete-contracts", tags=["contracts"],
             summary="Delete skills and rules, with their edges",
             description="A built-in is REFUSED unless `force`, since its file would "
                         "re-ingest it on the next sync.")
def delete_contracts(data: m.DeleteContracts = body(m.DeleteContracts)):
    spec = commands.spec_file({"ids": _ids(data.ids)})
    try:
        return reply(commands.graph_cmd(["del-contracts", "--spec", str(spec)]
                                        + (["--force"] if data.force else [])))
    finally:
        spec.unlink(missing_ok=True)
