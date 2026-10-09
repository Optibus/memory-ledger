#!/usr/bin/env python3
"""Per-type decay tiers — half-lives, rank weight, weekly digests, stale tasks.

Not every memory ages at the same speed. A decision is still true three months
later; the capture of "continue" is stale by Friday. Ported from claude-mem-lite's
type-differentiated half-lives (decision 90 d, bugfix 14 d, change 7 d): one
exponential curve, one half-life per kind, used on every ranking path.

    weight = 0.5 ** (age_days / half_life)        (floored, so nothing hits 0)

Three jobs, all pure SQL/regex — no model:

1. `rank_weight(node)`     the multiplier `recall` applies to a node's score.
2. `plan(con)` / `apply`   expire captures past their half-life into one weekly
                           `note` digest per ISO week, and list open tasks older
                           than 14 d whose title is really a pasted prompt.
3. CLI: `graph.py decay` (dry-run by default; `--apply` writes).

CONTRIBUTING rule 3 stands: nothing is deleted. An expired capture is KEPT, linked
`part-of` its weekly digest and stamped `expired_at`; a stale task is closed as
`dropped` (archived, not removed). Decay changes rank and what is surfaced, never
existence.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone

import noise

# Days for a node's weight to halve. Keyed by props.kind first, then node type.
# A kind/type not listed here does not decay (weight 1.0): person, project, skill
# and the rest are reference nodes whose rank already follows use (graph._strength).
DEFAULT_HALF_LIVES = {
    "decision": 90.0,
    "discovery": 60.0,
    "bugfix": 14.0,
    "task": 14.0,
    "change": 7.0,
    "capture": 7.0,
}
WEIGHT_FLOOR = 0.2           # an old node still outranks nothing, never vanishes
STALE_TASK_DAYS = 14.0
LIST_CAP = 25                # rows shown per list (CONTRIBUTING rule 4)
DIGEST_BULLETS = 40
BULLET_CHARS = 110
SETTING = "decay_half_lives"  # JSON object in settings.json, or $MEMORY_LEDGER_DECAY_HALF_LIVES


def half_lives(overrides: dict | None = None, configured=None) -> dict:
    """Defaults, then `configured` (the `decay_half_lives` setting: a dict or a
    JSON string — the engine reads it and passes it in, so this module never
    imports it), then `overrides`. A value that is not a positive number is
    ignored, so a hand-edited typo cannot turn decay off or divide by zero."""
    out = dict(DEFAULT_HALF_LIVES)
    sources = []
    if isinstance(configured, str) and configured.strip():
        try:
            configured = json.loads(configured)
        except ValueError:
            configured = None
    sources.append(configured)
    sources.append(overrides)
    for src in sources:
        if not isinstance(src, dict):
            continue
        for k, v in src.items():
            try:
                if float(v) > 0:
                    out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def half_life_for(ntype: str, props: dict | None, table: dict) -> float | None:
    """The half-life that governs a node: its `props.kind`, else its type."""
    kind = (props or {}).get("kind")
    if kind in table:
        return table[kind]
    return table.get(ntype)


def weight(age_days: float, half_life: float | None, floor: float = WEIGHT_FLOOR) -> float:
    """0.5 ** (age / half_life), floored. No half-life means no decay."""
    if not half_life:
        return 1.0
    return max(floor, 0.5 ** (max(0.0, age_days) / half_life))


def _age_days(iso: str, at: datetime | None = None) -> float:
    try:
        then = datetime.fromisoformat(iso)
    except Exception:
        return 0.0
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0.0, ((at or datetime.now(timezone.utc)) - then).total_seconds() / 86400.0)


def rank_weight(node: dict, table: dict | None = None, at: datetime | None = None) -> float:
    """The multiplier `recall` applies to this node's score."""
    table = table or half_lives()
    hl = half_life_for(node.get("type") or "", node.get("props"), table)
    if hl is None:
        return 1.0
    return weight(_age_days(node.get("created") or node.get("updated") or "", at), hl)


def iso_week(day: str) -> str:
    """'2026-10-08' -> '2026-W41' (ISO year, so 2026-01-01 is not 'W53 of 2026')."""
    y, w, _ = date.fromisoformat(day[:10]).isocalendar()
    return f"{y}-W{w:02d}"


def _bullet(title: str) -> str:
    t = re.sub(r"\s+", " ", title or "").strip()
    return "- " + (t[:BULLET_CHARS - 1] + "…" if len(t) > BULLET_CHARS else t)


def digest_body(week: str, caps: list[dict]) -> str:
    """Compressed bullets for one week: user prompts, de-duplicated, the opening
    of each. Answers are counted, not listed — a prompt says what was asked."""
    asked = [c for c in caps if (c["props"].get("role") == "user")]
    seen, lines = set(), []
    for c in sorted(asked, key=lambda c: c["created"]):
        b = _bullet(c["title"])
        if b.lower() not in seen:
            seen.add(b.lower())
            lines.append(b)
    more = len(lines) - DIGEST_BULLETS
    head = (f"Week {week}: {len(caps)} captures expired "
            f"({len(asked)} prompts). Originals kept and linked.")
    tail = [f"- … and {more} more prompts"] if more > 0 else []
    return head + "\n\n" + "\n".join(lines[:DIGEST_BULLETS] + tail)


_CLOSED = ("done", "cancelled", "dropped", "closed", "answered", "resolved",
           "merged", "shipped", "deployed", "declined")


def plan(con, at: datetime | None = None, table: dict | None = None,
         stale_days: float = STALE_TASK_DAYS) -> dict:
    """Read-only: what `--apply` would do. Never writes."""
    table = table or half_lives()
    at = at or datetime.now(timezone.utc)
    cap_hl = table.get("capture", DEFAULT_HALF_LIVES["capture"])

    expire, by_week, hist = [], {}, {}
    for r in con.execute("SELECT id,title,props,created FROM nodes WHERE type='capture'"):
        try:
            props = json.loads(r["props"] or "{}")
        except Exception:
            props = {}
        age = _age_days(r["created"], at)
        bucket = "<7d" if age < 7 else "7-14d" if age < 14 else "14d+"
        hist[bucket] = hist.get(bucket, 0) + 1
        if props.get("expired_at") or age <= cap_hl:
            continue
        c = {"id": r["id"], "title": r["title"], "props": props, "created": r["created"]}
        expire.append(c)
        by_week.setdefault(iso_week(r["created"]), []).append(c)

    # Retroactive tally: which stored prompts would today's filter have refused?
    noisy: dict = {}
    for r in con.execute("SELECT title, body, props FROM nodes WHERE type='capture'"):
        try:
            p = json.loads(r["props"] or "{}")
        except Exception:
            continue
        if p.get("role") != "user":
            continue
        keep, why = noise.classify(r["body"] or r["title"])
        if not keep:
            noisy[why] = noisy.get(why, 0) + 1

    marks = ",".join("?" * len(_CLOSED))
    tasks, open_tasks = [], 0
    for r in con.execute(
            f"SELECT id,title,created,props FROM nodes WHERE type='task' "
            f"AND lower(status) NOT IN ({marks}) ORDER BY created", _CLOSED):
        open_tasks += 1
        age = _age_days(r["created"], at)
        if age > stale_days and noise.looks_like_raw_prompt(r["title"]):
            tasks.append({"id": r["id"], "title": r["title"], "age_days": round(age, 1)})

    return {"half_lives": table, "capture_age_buckets": hist,
            "expire": {"captures": len(expire),
                       "weeks": {w: len(v) for w, v in sorted(by_week.items())}},
            "would_filter_today": {"user_prompts": sum(noisy.values()), "by_reason": noisy},
            "stale_tasks": {"open_tasks": open_tasks, "flagged": len(tasks),
                            "older_than_days": stale_days,
                            "shown": tasks[:LIST_CAP],
                            "truncated": len(tasks) > LIST_CAP},
            "_expire": by_week, "_tasks": tasks}


def apply(con, p: dict, g, at: datetime | None = None) -> dict:
    """Write what `plan` found. Digests first, then the links, then the tasks."""
    graph = g          # the engine module, passed in (importing it here would load it twice)
    ts = (at or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    touched, digests = [], []
    for week, caps in sorted(p["_expire"].items()):
        did = f"note_week_{week}"
        # Rebuild from every capture already folded into this week, so a second
        # run in the same week extends the digest instead of replacing it.
        have = {c["id"] for c in caps}
        for r in con.execute(
                "SELECT id,title,props,created FROM nodes WHERE type='capture' "
                "AND json_extract(props,'$.expired_into')=?", (did,)):
            if r["id"] not in have:
                caps = caps + [{"id": r["id"], "title": r["title"], "created": r["created"],
                                "props": json.loads(r["props"] or "{}")}]
        body = digest_body(week, caps)
        props = {"kind": "week-digest", "week": week, "captures": len(caps),
                 "expired_at": ts}
        if con.execute("SELECT 1 FROM nodes WHERE id=?", (did,)).fetchone():
            graph.api_update_node(con, did, title=f"Week digest — {week}", body=body,
                                  props_patch=props)
        else:
            graph.api_create_node(con, "note", f"Week digest — {week}", body, props,
                                  realm=graph.DEFAULT_REALM, node_id=did)
        for c in p["_expire"][week]:
            cp = dict(c["props"]); cp["expired_at"] = ts; cp["expired_into"] = did
            con.execute("UPDATE nodes SET props=? WHERE id=?", (json.dumps(cp), c["id"]))
            con.execute("INSERT OR IGNORE INTO edges(src,dst,rel,props,created) "
                        "VALUES(?,?,?,?,?)", (c["id"], did, "part-of", "{}", ts))
            touched.append(c["id"])
        con.commit()
        digests.append({"id": did, "week": week, "captures": len(caps)})
    dropped = []
    for t in p["_tasks"]:
        row = con.execute("SELECT props FROM nodes WHERE id=?", (t["id"],)).fetchone()
        tp = json.loads((row["props"] if row else "") or "{}")
        tp.update({"dropped_at": ts, "drop_reason": "stale raw-prompt task (decay)"})
        con.execute("UPDATE nodes SET status='dropped', props=?, updated=? WHERE id=?",
                    (json.dumps(tp), ts, t["id"]))
        dropped.append(t["id"])
    con.commit()
    graph._after_write(con, nodes=[d["id"] for d in digests] + touched + dropped)
    return {"digests": digests, "captures_expired": len(touched),
            "tasks_dropped": len(dropped)}
