#!/usr/bin/env python3
"""The OpenAPI 3.1 description of the brain's HTTP API.

WHY THIS FILE EXISTS, and why it is Python rather than a checked-in YAML:
`scripts/serve.py` dispatches on a `path ==` chain for reads and a dict for
writes. A hand-written spec beside it is a second description of the same
surface, and the second description is always the one that rots. So:

  * the spec is BUILT here, from one table per method;
  * `scripts/serve.py` serves it at `/api/openapi.json` and a Swagger UI at
    `/api/docs`, so what a reader browses is what the running process holds;
  * `tests/test_openapi.py` reads serve.py's ACTUAL routes out of its source and
    fails if either side grows an endpoint the other has never heard of.

That last point is the whole design. Documentation nobody checks is a rumour.

Run it directly to write `docs/openapi.json`:

    python3 scripts/openapi.py --out docs/openapi.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Reusable pieces
# ---------------------------------------------------------------------------

#: Every response carries `ok`; a failure carries `error` with it.
ENVELOPE = {
    "type": "object",
    "required": ["ok"],
    "properties": {
        "ok": {"type": "boolean"},
        "error": {"type": "string",
                  "description": "Present, and worth showing, only when ok is false."},
    },
}


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def _envelope_with(**props) -> dict:
    """An ok-envelope plus the fields a particular endpoint adds to it."""
    return {"allOf": [_ref("Envelope"), {"type": "object", "properties": props}]}


def _json(schema: dict, description: str) -> dict:
    return {"description": description,
            "content": {"application/json": {"schema": schema}}}


def _query(name: str, description: str, *, required=False, schema=None) -> dict:
    return {"name": name, "in": "query", "required": required,
            "description": description, "schema": schema or {"type": "string"}}


SCHEMAS: dict[str, dict] = {
    "Envelope": ENVELOPE,
    "Node": {
        "type": "object",
        "description": "One node of the graph, as the map draws it.",
        "properties": {
            "id": {"type": "string", "examples": ["task_ship-ui_a1b2"]},
            "type": {"type": "string",
                     "description": "OPEN vocabulary — the engine never restricts it.",
                     "examples": ["task"]},
            "title": {"type": "string"},
            "status": {"type": "string"},
            "realm": {"type": "string",
                      "description": "Empty is normalised to the server's default realm."},
            "kind": {"type": "string",
                     "description": "props.kind, or the none-token for a node carrying none."},
            "due": {"type": "string"},
            "degree": {"type": "integer",
                       "description": "How many edges touch it — the map sizes the dot by this."},
            "body": {"type": "string"},
            "props": {"type": "object", "additionalProperties": True},
        },
    },
    "Edge": {
        "type": "object",
        "required": ["src", "dst", "rel"],
        "properties": {
            "src": {"type": "string"},
            "dst": {"type": "string"},
            "rel": {"type": "string",
                    "description": "A CLOSED set, enforced on write. See /api/meta."},
        },
    },
    "NeighbourEdge": {
        "type": "object",
        "description": "An edge seen from one node, so it carries its direction.",
        "properties": {
            "dir": {"type": "string", "enum": ["out", "in"]},
            "rel": {"type": "string"},
            "id": {"type": "string"},
            "title": {"type": "string"},
            "type": {"type": "string"},
        },
    },
    "TaskRow": {
        "type": "object",
        "description": "The slim row the date views return — small on purpose.",
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string"},
            "title": {"type": "string"},
            "status": {"type": "string"},
            "realm": {"type": "string"},
            "due": {"type": "string"},
            "due_local": {
                "type": "string",
                "description": "The due date resolved into the brain's timezone, "
                               "or '--' when there is none.",
            },
            "waiting_on": {"type": "string"},
            "assignee": {"type": "string"},
            "kind": {"type": "string"},
            "delegated": {"type": "string"},
            "mine": {"type": "boolean"},
            "blocked_by": {"type": "array", "items": _ref("RelatedRef")},
            "blocks": {"type": "array", "items": _ref("RelatedRef")},
            "related": {"type": "array", "items": _ref("RelatedRef")},
        },
    },
    "RelatedRef": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "title": {"type": "string"},
                       "type": {"type": "string"}},
    },
    "RealmCount": {
        "type": "object",
        "description": "Two counts, because they answer different questions: "
                       "`nodes` is what the map filters over, `open_tasks` is what "
                       "the dashboard filters over.",
        "properties": {"nodes": {"type": "integer"},
                       "open_tasks": {"type": "integer"}},
    },
    "SectionShape": {
        "type": "object",
        "description": "One heading of a contract shape (CONVENTIONS §5.1, §5b.1). "
                       "The UI builds its forms from these rather than hard-coding "
                       "the headings — a heading written into the page would be a "
                       "second definition of the shape, and the one nothing checks.",
        "properties": {
            "field": {"type": "string"},
            "heading": {"type": "string"},
            "required": {"type": "boolean"},
            "generated": {"type": "boolean",
                          "description": "Rendered from the declarations; the form leaves it out."},
        },
    },
    "Skill": {
        "type": "object",
        "description": "A skill contract, with its children nested by `requires` "
                       "and nothing else (CONVENTIONS §5a).",
        "properties": {
            "id": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "triggers": {"type": "string"},
            "kind": {"type": "string", "enum": ["plugin", "learned"]},
            "uses": {"type": "integer"},
            "requires": {"type": "array", "items": {"type": "string"}},
            "waits_for": {"type": "array", "items": {"type": "string"}},
            "enriched_by": {"type": "array", "items": {"type": "string"}},
            "reads": {"type": "array", "items": {"type": "string"}},
            "writes": {"type": "array", "items": {"type": "string"}},
            "reads_other": {"type": "string"},
            "writes_other": {"type": "string"},
            "domains": {"type": "array", "items": {"type": "string"}},
            "declared": {"type": "boolean"},
            "rules": {"type": "array", "items": _ref("SkillRuleRef")},
            "children": {"type": "array", "items": _ref("Skill")},
        },
    },
    "SkillRuleRef": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "title": {"type": "string"},
            "source": {"type": "string"},
            "non_negotiable": {"type": "boolean"},
            "why": {
                "type": "string",
                "enum": ["declared", "matched", "always"],
                "description": "Three different questions. `declared` is what the "
                               "skill says it is accountable for; `matched` is what "
                               "match-rules hands it at runtime; `always` is a "
                               "non-negotiable, appended to every match.",
            },
        },
    },
    "Rule": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "title": {"type": "string"},
            "source": {"type": "string"},
            "section": {"type": "string"},
            "non_negotiable": {"type": "boolean"},
            "scenario": {"type": "string",
                         "description": "The cue match-rules actually fires on — not the body."},
            "domains": {"type": "array", "items": {"type": "string"}},
            "chars": {"type": "integer"},
        },
    },
    "NodeTypeInfo": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "description": {"type": "string"},
            "count": {"type": "integer"},
            "readers": {"type": "array", "items": {"type": "string"}},
            "writers": {"type": "array", "items": {"type": "string"}},
        },
    },
    "LinkSuggestion": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "title": {"type": "string"},
            "type": {"type": "string"},
            "rel": {"type": "string"},
            "cue": {"type": "string",
                    "description": "The wording in the text that produced this guess."},
        },
    },
    "BundleItem": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "kind": {"type": "string", "enum": ["skill", "rule"]},
            "exists": {"type": "boolean",
                       "description": "This brain already holds it — the only interesting case."},
        },
    },
}

# ---------------------------------------------------------------------------
# GET — reads
#
# One entry per path serve.py's do_GET answers. `tests/test_openapi.py` reads
# those paths out of the source, so this table cannot quietly fall behind.
# ---------------------------------------------------------------------------

VIEW_PARAMS = [
    _query("mine", "Only rows that need action from the brain's owner.",
           schema={"type": "string", "enum": ["1"]}),
    _query("assignee", "Comma list of assignee tokens. `unassigned` is one of them."),
    _query("realm", "Comma list of realms. Omit for every realm."),
    _query("kind", "Comma list of props.kind values. `(none)` selects nodes carrying none."),
    _query("tz", "IANA name overriding the brain's configured timezone for this call."),
    _query("backend", "sqlite | neo4j | auto. Defaults to auto, the same store the map reads."),
    _query("status", "range only: any | open | done."),
    _query("from", "range only: ISO date, inclusive."),
    _query("to", "range only: ISO date, inclusive."),
    _query("since_days", "closed only: how far back to look.",
           schema={"type": "integer", "minimum": 1}),
]

GET_ROUTES: dict[str, dict] = {
    "/api/graph": {
        "summary": "The whole graph, for the map",
        "description": "Nodes, edges, and the facet counts the map builds its "
                       "filters from. Read from the neo4j master when one is "
                       "reachable, else from the local sqlite mirror.",
        "tags": ["graph"],
        "response": _envelope_with(
            nodes={"type": "array", "items": _ref("Node")},
            edges={"type": "array", "items": _ref("Edge")},
            meta={
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                    "edges": {"type": "integer"},
                    "by_type": {"type": "object", "additionalProperties": {"type": "integer"}},
                    "by_realm": {"type": "object", "additionalProperties": {"type": "integer"}},
                    "by_kind": {"type": "object", "additionalProperties": {"type": "integer"}},
                    "backend": {"type": "string"},
                    "boot": {"type": "string",
                             "description": "Identifies THIS process. The page reloads "
                                            "itself when it changes, so an open tab never "
                                            "keeps a page older than the API behind it."},
                },
            },
        ),
    },
    "/api/meta": {
        "summary": "The vocabulary, so no client hard-codes it",
        "description": "Two of these sets are CLOSED — `rels` and `statuses` are "
                       "enforced on write. `realms`, `kinds` and `types` are OPEN: "
                       "they are whatever this brain actually contains, and nobody's "
                       "brain has the same list.",
        "tags": ["vocabulary"],
        "response": _envelope_with(
            rels={"type": "array", "items": {"type": "string"}},
            statuses={"type": "array", "items": {"type": "string"}},
            realms={"type": "object", "additionalProperties": _ref("RealmCount")},
            default_realm={"type": "string"},
            kinds={"type": "object", "additionalProperties": _ref("RealmCount")},
            no_kind_token={"type": "string"},
            types={"type": "object", "additionalProperties": {"type": "integer"}},
            skill_sections={"type": "array", "items": _ref("SectionShape")},
            rule_sections={"type": "array", "items": _ref("SectionShape")},
            decl_labels={"type": "array", "items": {"type": "object"}},
            rule_sources={"type": "array", "items": {"type": "string"}},
            node_types={"type": "array", "items": {"type": "string"}},
            writable={"type": "boolean",
                      "description": "False under --no-write; the UI hides its editing controls."},
            boot={"type": "string"},
        ),
    },
    "/api/views": {
        "summary": "The names of the date views",
        "tags": ["dashboard"],
        "response": _envelope_with(views={"type": "array", "items": {"type": "string"}}),
    },
    "/api/assignees": {
        "summary": "Every assignee token on an open task, with counts",
        "tags": ["vocabulary"],
        "response": _envelope_with(
            assignees={"type": "object", "additionalProperties": {"type": "integer"}}),
    },
    "/api/realms": {
        "summary": "Every realm in the brain, with two counts each",
        "tags": ["vocabulary"],
        "response": _envelope_with(
            realms={"type": "object", "additionalProperties": _ref("RealmCount")},
            default={"type": "string"}),
    },
    "/api/kinds": {
        "summary": "Every props.kind in the brain, with the same two counts",
        "tags": ["vocabulary"],
        "response": _envelope_with(
            kinds={"type": "object", "additionalProperties": _ref("RealmCount")},
            none_token={"type": "string"}),
    },
    "/api/node": {
        "summary": "One node in full, plus its named neighbours",
        "tags": ["graph"],
        "params": [_query("id", "The node id.", required=True)],
        "response": _envelope_with(
            id={"type": "string"}, type={"type": "string"}, title={"type": "string"},
            status={"type": "string"}, due={"type": "string"}, realm={"type": "string"},
            body={"type": "string"}, props={"type": "object", "additionalProperties": True},
            edges={"type": "array", "items": _ref("NeighbourEdge")}),
    },
    "/api/people": {
        "summary": "Person nodes, for the assignee picker",
        "tags": ["people"],
        "response": _envelope_with(people={"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "title": {"type": "string"},
                           "aliases": {"type": "array", "items": {"type": "string"}},
                           "realm": {"type": "string"}}}}),
    },
    "/api/person-duplicates": {
        "summary": "People who look like the same person",
        "description": "Read-only: it proposes, and `POST /api/merge-person` acts.",
        "tags": ["people"],
        "response": _envelope_with(duplicates={"type": "array", "items": {"type": "object"}}),
    },
    "/api/skills": {
        "summary": "The two skill trees and the rule catalogue",
        "description": "Built from the edges `sync-skill-tree` wrote, never "
                       "re-derived from the files here: two readers of one "
                       "declaration cannot disagree, two parsers of it eventually do.",
        "tags": ["contracts"],
        "response": _envelope_with(
            trees={"type": "array", "items": {
                "type": "object",
                "properties": {"kind": {"type": "string"}, "hub": {"type": "string"},
                               "title": {"type": "string"}, "count": {"type": "integer"},
                               "skills": {"type": "array", "items": _ref("Skill")}}}},
            rules={"type": "array", "items": _ref("Rule")},
            node_types={"type": "array", "items": _ref("NodeTypeInfo")},
            areas={"type": "object", "additionalProperties": {"type": "string"}}),
    },
    "/api/rule": {
        "summary": "One rule in full",
        "tags": ["contracts"],
        "params": [_query("id", "The rule id.", required=True)],
        "response": _envelope_with(rule={"type": "object", "nullable": True}),
    },
    "/api/bundle": {
        "summary": "Download an exported bundle",
        "description": "`name` is reduced to a bare filename, so it can only name "
                       "a file in `<home>/bundles`.",
        "tags": ["contracts"],
        "params": [_query("name", "The bundle filename.", required=True)],
        "produces": "application/zip",
        "response": {"type": "string", "format": "binary"},
    },
    "/api/search": {
        "summary": "Find a node by title, for the relation picker",
        "description": "Under two characters it answers with an empty list rather "
                       "than scanning the brain.",
        "tags": ["graph"],
        "params": [_query("q", "Title substring. Two characters minimum.", required=True)],
        "response": _envelope_with(results={"type": "array", "items": _ref("Node")}),
    },
    "/api/suggest-links": {
        "summary": "What a piece of text mentions, and how",
        "description": "Scans the text for nodes this brain already holds and reads "
                       "the wording around each mention to pick a relation: "
                       "'blocked by PR #7366' becomes `blocked-by`, 'part of …' "
                       "becomes `part-of`. A suggestion engine, not an oracle — the "
                       "caller is expected to let a person confirm each one.",
        "tags": ["graph"],
        "params": [
            _query("text", "The text to scan — a task title, usually.", required=True),
            _query("exclude", "A node id to leave out, normally the one being edited."),
        ],
        "response": _envelope_with(count={"type": "integer"},
                                   suggestions={"type": "array", "items": _ref("LinkSuggestion")}),
    },
    "/api/settings": {
        "summary": "Stored preferences, and what is actually in effect",
        "description": "They differ whenever an environment variable overrides the "
                       "file, and a settings page that hides that is lying — so "
                       "`env` names every key the shell has taken over.",
        "tags": ["settings"],
        "response": _envelope_with(
            writable={"type": "boolean"},
            keys={"type": "array", "items": {"type": "string"}},
            settings={"type": "object", "additionalProperties": True},
            env={"type": "object", "additionalProperties": {"type": "string"}},
            path={"type": "string"},
            effective={"type": "object", "additionalProperties": True}),
    },
    "/api/timezones": {
        "summary": "Every IANA zone name, from the stdlib",
        "tags": ["settings"],
        "response": _envelope_with(zones={"type": "array", "items": {"type": "string"}}),
    },
    "/api/status": {
        "summary": "Where the data lives and whether the master is reachable",
        "description": "The neo4j password is NEVER sent to the browser; this "
                       "reports `password_set` only.",
        "tags": ["settings"],
        "response": _envelope_with(
            db={"type": "string"}, home={"type": "string"},
            nodes={"type": "integer"}, edges={"type": "integer"},
            pending_ops={"type": "integer"}, loose_captures={"type": "integer"},
            boot={"type": "string"}, writable={"type": "boolean"},
            neo4j={"type": "object", "additionalProperties": True}),
    },
    "/api/openapi.json": {
        "summary": "This document",
        "tags": ["meta"],
        "response": {"type": "object", "additionalProperties": True},
    },
    "/api/docs": {
        "summary": "Swagger UI over this document",
        "tags": ["meta"],
        "produces": "text/html",
        "response": {"type": "string"},
    },
}

#: The date views share one shape and one parameter set, so they share one entry
#: rather than seven near-identical ones.
VIEW_PATH = "/api/{view}"

# ---------------------------------------------------------------------------
# POST — writes
#
# Keys here MUST match `Handler.do_POST`'s dispatch table exactly.
# ---------------------------------------------------------------------------

def _body(required: list[str], **props) -> dict:
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


_ID = {"type": "string"}
_STR = {"type": "string"}

POST_ROUTES: dict[str, dict] = {
    "/api/set-due": {
        "summary": "Set or clear a due date",
        "tags": ["tasks"],
        "body": _body(["id"], id=_ID,
                      due={"type": "string",
                           "description": "ISO timestamp. Empty clears it."}),
    },
    "/api/add-task": {
        "summary": "Create a task",
        "description": "Created `open`, with `props.source = \"web-ui\"` so UI-origin "
                       "data stays distinguishable from hook-captured and "
                       "skill-written data.",
        "tags": ["tasks"],
        "body": _body(["title"], title=_STR,
                      type={"type": "string", "enum": ["task", "subtask"]},
                      assignee=_STR, priority=_STR, realm=_STR, due=_STR, body=_STR,
                      links={"type": "array", "items": {
                          "type": "object",
                          "properties": {"id": _ID, "rel": _STR}}}),
    },
    "/api/complete": {
        "summary": "Archive a task",
        "description": "Sets `status=done` and stamps `completed_at`. Never deletes "
                       "— finishing work is archiving (CONVENTIONS §2a).",
        "tags": ["tasks"],
        "body": _body(["id"], id=_ID, note=_STR),
    },
    "/api/set-status": {
        "summary": "Move a task's state",
        "description": "Refused with the allowed set in the error string when the "
                       "status is not one the engine enforces.",
        "tags": ["tasks"],
        "body": _body(["id", "status"], id=_ID, status=_STR, waiting_on=_STR),
    },
    "/api/set-assignee": {
        "summary": "Reassign a task",
        "tags": ["tasks"],
        "body": _body(["id"], id=_ID,
                      assignee={"type": "string", "description": "Empty un-assigns."}),
    },
    "/api/update-node": {
        "summary": "Rename or edit any node",
        "tags": ["graph"],
        "body": _body(["id"], id=_ID, title=_STR, body=_STR, realm=_STR,
                      priority=_STR, due=_STR,
                      props={"type": "object", "additionalProperties": True}),
    },
    "/api/assign": {
        "summary": "Point a task at a person node",
        "description": "Rewires the `assigned-to` edge as well as the prop, which is "
                       "why it is a separate call from set-assignee.",
        "tags": ["people"],
        "body": _body(["id"], id=_ID, person_id=_ID),
    },
    "/api/merge-person": {
        "summary": "Merge two person nodes",
        "tags": ["people"],
        "body": _body([], keep=_ID, drop=_ID),
    },
    "/api/link": {
        "summary": "Create a relation",
        "tags": ["graph"],
        "body": _body(["src", "dst", "rel"], src=_ID, dst=_ID, rel=_STR),
    },
    "/api/unlink": {
        "summary": "Remove a relation",
        "tags": ["graph"],
        "body": _body(["src", "dst", "rel"], src=_ID, dst=_ID, rel=_STR),
    },
    "/api/settings": {
        "summary": "Save preferences",
        "description": "Writes `<home>/settings.json`. Only these keys, each "
                       "validated. An empty string REMOVES a key, falling back to "
                       "the environment variable or the built-in default.",
        "tags": ["settings"],
        "body": _body([], tz=_STR, realm=_STR,
                      map_poll_seconds={"type": "integer"},
                      dash_refresh_seconds={"type": "integer"}),
    },
    "/api/neo4j-config": {
        "summary": "Point the brain at a master",
        "description": "Writes `<home>/neo4j.json` mode 600 and probes it. A blank "
                       "password keeps the stored one; an empty url deletes the file "
                       "and resets the read backend to sqlite.",
        "tags": ["settings"],
        "body": _body(["url"], url=_STR, db=_STR, user=_STR, password=_STR),
    },
    "/api/neo4j-test": {
        "summary": "Probe the configured master",
        "tags": ["settings"],
        "body": _body([]),
    },
    "/api/flush": {
        "summary": "Drain the outbox to the master",
        "tags": ["settings"],
        "body": _body([]),
    },
    "/api/prune": {
        "summary": "Run maintain",
        "description": "Consolidates old captures. Non-destructive — deletes nothing.",
        "tags": ["settings"],
        "body": _body([], keep_days={"type": "integer", "default": 2}),
    },
    "/api/create-rule": {
        "summary": "Author a rule (CONVENTIONS §5b)",
        "description": "STRICTER than the CLI, not looser: refused unless "
                       "`sections.the_rule` and `sections.judged_against` are filled. "
                       "A form has all the fields in front of it and no excuse to "
                       "submit one that cannot be checked.",
        "tags": ["contracts"],
        "body": _body(["title", "scenario", "sections"],
                      title=_STR, scenario=_STR, source=_STR, section=_STR, realm=_STR,
                      non_negotiable={"type": "boolean"}, overrides=_STR,
                      sections={"type": "object", "additionalProperties": {"type": "string"}}),
    },
    "/api/create-skill": {
        "summary": "Author a skill (CONVENTIONS §5)",
        "description": "Refused unless the six typed sections of §5.1 are filled. "
                       "`new_rules[]` are whole rule bodies created FIRST in the same "
                       "call, and a failure in one aborts everything — a skill is "
                       "never left accountable to a rule that does not exist.",
        "tags": ["contracts"],
        "body": _body(["name", "title", "sections"],
                      name=_STR, title=_STR, description=_STR, realm=_STR,
                      sections={"type": "object", "additionalProperties": {"type": "string"}},
                      requires={"type": "array", "items": _STR},
                      waits_for={"type": "array", "items": _STR},
                      enriched_by={"type": "array", "items": _STR},
                      reads={"type": "array", "items": _STR},
                      writes={"type": "array", "items": _STR},
                      rules={"type": "array", "items": _STR},
                      new_rules={"type": "array", "items": {"type": "object"}}),
    },
    "/api/export-skills": {
        "summary": "Write the skill tree out as markdown",
        "tags": ["contracts"],
        "body": _body([], kind=_STR, dir=_STR),
    },
    "/api/ingest-skills": {
        "summary": "Read a markdown folder back in",
        "description": "Re-running is safe: a skill is refreshed, and a rule this "
                       "brain already holds is kept, never overwritten.",
        "tags": ["contracts"],
        "body": _body([], dir=_STR),
    },
    "/api/export-bundle": {
        "summary": "Export a selection as one zip",
        "description": "Returns the path, a same-origin download URL, and any skill "
                       "REQUIRED by something exported that did not travel — those "
                       "links cannot form on the other brain.",
        "tags": ["contracts"],
        "body": _body([], skills={"type": "array", "items": _STR},
                      rules={"type": "array", "items": _STR}),
        "response": _envelope_with(
            skills={"type": "integer"}, rules={"type": "integer"},
            path={"type": "string", "description": "Where the zip landed on disk."},
            name={"type": "string"},
            download={"type": "string",
                      "description": "A same-origin URL, so the browser can download it too."},
            missing_skills={"type": "array", "items": _STR},
            cycles={"type": "array", "items": {"type": "array", "items": _STR}}),
    },
    "/api/upload-bundle": {
        "summary": "Stage a bundle and read what is in it",
        "description": "READS ONLY — nothing is written. Returns a one-shot token "
                       "and every item with whether this brain already holds it. "
                       "Capped at 32MB, because the upload arrives base64-encoded in "
                       "one JSON body and is read into memory whole.",
        "tags": ["contracts"],
        "body": _body(["filename", "data"], filename=_STR,
                      data={"type": "string", "format": "byte"}),
        "response": _envelope_with(
            token={"type": "string",
                   "description": "One-shot; consumed by /api/import-bundle."},
            counts={"type": "object", "additionalProperties": {"type": "integer"}},
            items={"type": "array", "items": _ref("BundleItem")}),
    },
    "/api/import-bundle": {
        "summary": "Apply a staged bundle",
        "description": "Applies in the plan's order and consumes the token. An item "
                       "not mentioned in `select` is created when new and untouched "
                       "when it exists.",
        "tags": ["contracts"],
        "body": _body(["token", "select"], token=_STR,
                      select={"type": "object",
                              "additionalProperties": {"type": "string",
                                                       "enum": ["update", "replace", "ignore"]}}),
        "response": _envelope_with(
            applied={"type": "array", "items": {
                "type": "object",
                "properties": {"id": _STR, "action": _STR,
                               "rules_not_found": {"type": "array", "items": _STR}}}},
            tree={"type": "object",
                  "properties": {"added": {"type": "integer"}, "pruned": {"type": "integer"}}},
            missing_skills={"type": "array", "items": _STR}),
    },
    "/api/delete-contracts": {
        "summary": "Delete skills and rules, with their edges",
        "description": "A built-in is REFUSED unless `force`, since its file would "
                       "re-ingest it on the next sync.",
        "tags": ["contracts"],
        "body": _body(["ids"], ids={"type": "array", "items": _STR},
                      force={"type": "boolean"}),
        "response": _envelope_with(
            count={"type": "integer"},
            deleted={"type": "array", "items": {"type": "object"}},
            refused={"type": "array", "items": {
                "type": "object",
                "properties": {"id": _STR,
                               "why": {"type": "string",
                                       "description": "Usually: it ships as a file."}}}},
            not_found={"type": "array", "items": _STR}),
    },
}

TAGS = [
    {"name": "graph", "description": "Nodes, edges, and what connects to what."},
    {"name": "dashboard", "description": "The token-free date views."},
    {"name": "tasks", "description": "Creating and moving work."},
    {"name": "people", "description": "Person nodes and assignment."},
    {"name": "contracts", "description": "Skills and rules — the two trees, and the "
                                         "file and bundle routes between machines."},
    {"name": "vocabulary", "description": "The open and closed sets a client must "
                                          "read rather than hard-code."},
    {"name": "settings", "description": "Preferences, store health, and upkeep."},
    {"name": "meta", "description": "This document, and the UI over it."},
]

DESCRIPTION = """\
The local HTTP API over one person's memory graph.

**It calls no model.** Every endpoint here is the graph engine behind a socket:
plain SQL through `graph.py`'s `api_*` helpers, so everything it does is
token-free. Leaving a client polling it costs nothing.

## Security — read this before changing `--host`

**The write API is unauthenticated.** There is no token, no session, no CSRF
protection. Anything that can reach the port can rewrite the graph.

- The default bind is `127.0.0.1`. Keep it there.
- If you must expose it (`--host 0.0.0.0` for phone access), pass `--no-write`:
  every `POST` then returns 403 and `/api/meta` reports `writable: false`, which
  is what makes the UI hide its editing controls.
- Do not put it behind a plain reverse proxy and call it done; that publishes an
  unauthenticated write API to whoever can reach the proxy.

## Conventions

- Every response is JSON with an `ok` boolean; failures carry `error`, and the
  error is the reason worth showing a person rather than a status code.
- String inputs are length-capped server-side. Over-long values are truncated,
  not rejected.
- Open vocabulary (`realms`, `kinds`, node `types`) is read from `/api/meta`,
  never shipped in a client. Closed sets (`rels`, `statuses`) are enforced here.
- A disconnected client is not an error: the map polls, so a reload aborts a
  response mid-write, and the server returns silently rather than reporting a
  500 down a socket that has already closed.
"""


def build() -> dict:
    """The whole document."""
    paths: dict[str, dict] = {}

    for path, spec in GET_ROUTES.items():
        produces = spec.get("produces", "application/json")
        op = {
            "summary": spec["summary"],
            "tags": spec.get("tags", []),
            "operationId": _operation_id("get", path),
            "responses": {
                "200": {"description": "The read succeeded.",
                        "content": {produces: {"schema": spec["response"]}}},
                "500": _json(_ref("Envelope"), "The handler raised; the reason is in `error`."),
            },
        }
        if spec.get("description"):
            op["description"] = spec["description"]
        if spec.get("params"):
            op["parameters"] = spec["params"]
        paths[path] = {"get": op}

    paths[VIEW_PATH] = {
        "get": {
            "summary": "Run one date view",
            "description": "The same query layer the CLI uses, over HTTP. `realm` "
                           "and `kind` take comma lists; omitting one means every "
                           "value, so there is no 'all' token to get out of sync.",
            "tags": ["dashboard"],
            "operationId": "getView",
            "parameters": [
                {"name": "view", "in": "path", "required": True,
                 "description": "One of the names `/api/views` returns.",
                 "schema": {"type": "string",
                            "enum": ["agenda", "open", "today", "tomorrow",
                                     "week", "closed", "range"]}},
                *VIEW_PARAMS,
            ],
            "responses": {
                "200": _json(_envelope_with(
                    view={"type": "string"}, title={"type": "string"},
                    ref={"type": "string"}, count={"type": "integer"},
                    results={"type": "array", "items": _ref("TaskRow")}),
                    "The rows the view matched."),
                "404": _json(_ref("Envelope"), "No view by that name."),
            },
        }
    }

    for path, spec in POST_ROUTES.items():
        op = {
            "summary": spec["summary"],
            "tags": spec.get("tags", []),
            "operationId": _operation_id("post", path),
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": spec["body"]}},
            },
            "responses": {
                "200": _json(spec.get("response", _ref("Envelope")),
                             "The write was applied."),
                "400": _json(_ref("Envelope"),
                             "Refused. `error` says why, in the words a person can act on."),
                "403": _json(_ref("Envelope"),
                             "The server was started with --no-write."),
                "500": _json(_ref("Envelope"), "The handler raised."),
            },
        }
        if spec.get("description"):
            op["description"] = spec["description"]
        paths.setdefault(path, {})["post"] = op

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "memory-ledger",
            "version": VERSION,
            "description": DESCRIPTION,
            "license": {"name": "See the repository"},
        },
        # Relative, deliberately. The document is served by the process it
        # describes, so "the server" is whatever origin you fetched this from —
        # naming a fixed port would point Try-it-out at the default 8770 even
        # when you are reading it on 8793.
        "servers": [{"url": "/", "description": "This server."}],
        "tags": TAGS,
        "paths": paths,
        "components": {"schemas": SCHEMAS},
    }


def _operation_id(method: str, path: str) -> str:
    """`/api/set-due` + post -> `postSetDue`. Stable, and unique per operation."""
    parts = [p for p in path.replace("/api/", "").replace(".json", "").split("/") if p]
    words = []
    for part in parts:
        words.extend(w for w in part.replace("-", " ").replace("_", " ").split() if w)
    return method + "".join(w[:1].upper() + w[1:] for w in words)


#: Swagger UI, from a CDN. The spec itself is served by the local process, so the
#: page always describes the running server; only the viewer is remote, and the
#: whole API stays usable with curl when it is not reachable.
SWAGGER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>memory-ledger API</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.17.14/swagger-ui.min.css">
<style>
  body { margin: 0; background: #fafafa; }
  .offline { display: none; font: 14px/1.6 ui-sans-serif, system-ui, sans-serif;
             padding: 24px; max-width: 46rem; margin: 0 auto; color: #24292f; }
  .offline code { background: #eef1f4; padding: 1px 5px; border-radius: 4px; }
</style>
</head>
<body>
<div id="swagger"></div>
<div class="offline" id="offline">
  <h1>The API browser needs the network</h1>
  <p>Swagger UI loads from a CDN and this machine could not reach it. The API
  itself is entirely local and still works:</p>
  <p><code>curl http://127.0.0.1:8770/api/openapi.json</code> is the same
  document this page would have rendered, and
  <code>python3 scripts/openapi.py --out docs/openapi.json</code> writes it to
  disk.</p>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/5.17.14/swagger-ui-bundle.min.js"></script>
<script>
  if (window.SwaggerUIBundle) {
    SwaggerUIBundle({
      url: "/api/openapi.json",
      dom_id: "#swagger",
      deepLinking: true,
      docExpansion: "list",
      defaultModelsExpandDepth: 1,
      tryItOutEnabled: true
    });
  } else {
    document.getElementById("offline").style.display = "block";
  }
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="write the document here instead of to stdout")
    args = ap.parse_args()
    doc = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(doc, encoding="utf-8")
        print(f"wrote {path} ({len(doc)} bytes)")
    else:
        sys.stdout.write(doc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
