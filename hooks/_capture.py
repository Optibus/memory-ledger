#!/usr/bin/env python3
"""memory-ledger — token-free capture helpers.

Shared by the capture hooks (capture_prompt.py on UserPromptSubmit,
capture_answer.py on Stop). Everything here is pure Python + SQL against the
brain's graph engine — NO model, NO tokens. The hooks store the interaction
directly so questions, answers, and any tasks/links they imply are recallable
later without ever spending tokens to "remember".

Design notes:
- Raw interaction turns are stored as `capture` nodes (deliberately NOT `message`
  — that type is treated as an open loop; a captured turn is a log entry, not a
  to-do). Auto-detected to-dos ARE stored as `task` nodes (which are loops).
- Writes go to the local SQLite mirror immediately (instant, offline-safe) and
  are queued in the outbox; a detached `graph.py flush` drains them to the neo4j
  master in the background, so the hook never blocks on the network.
- Every function is defensive: a capture must never break the user's session.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

# A HOOK MUST NEVER BLOCK ON THE NETWORK. graph.py's api_* writers push to the
# neo4j master inline (`_after_write` -> `_flush`, up to a 120s client timeout),
# which is right for a CLI and wrong here: a wedged or slow master would stall
# every prompt, every Task call, and session end behind it. Measured against a
# deliberately slow master, leaving this unset cost 18-48s per hook.
#
# Set BEFORE importing graph so the flag is in place for anything read at import.
# The queue still drains — `flush_async()` spawns a detached `graph.py flush`
# that does the network work outside the session's critical path.
os.environ.setdefault("SECOND_BRAIN_NO_AUTOSYNC", "1")

import graph  # noqa: E402  (the engine — single source of truth for the DB)

GRAPH_PY = str(HERE.parent / "scripts" / "graph.py")


def brain_enabled() -> bool:
    """The brain is 'enabled' unless explicitly turned off (`graph.enabled()`).

    This used to check whether the home dir existed — but bootstrap.py
    now creates that dir automatically on every session start, so a home dir's
    mere existence stopped meaning anything. The real contract is: OFF only
    when the user said so ($MEMORY_LEDGER_OFF / <home>/.disabled), not when the
    dir happens to be missing or present."""
    try:
        return graph.enabled()
    except Exception:
        return False


def connect():
    return graph.connect()


def realm() -> str:
    return graph.DEFAULT_REALM


def title_of(text: str, n: int = 80) -> str:
    t = " ".join((text or "").split())
    return (t[:n] + "…") if len(t) > n else (t or "(empty)")


def insert_node(con, ntype, title, body="", props=None, status="", realm_=None) -> str:
    """Insert a node (mirrors graph.cmd_add_node's write) and queue it for neo4j —
    WITHOUT the CLI's stdout print, so hooks stay silent unless they choose to talk."""
    nid = graph._gen_id(con, ntype, title)
    ts = graph.now()
    con.execute(
        "INSERT INTO nodes(id,type,title,body,props,status,due,realm,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (nid, ntype, title, body or "", json.dumps(props or {}), status or "",
         "", realm_ or realm(), ts, ts))
    con.commit()
    graph._enqueue(con, "node", nid)
    con.commit()
    return nid


def insert_edge(con, src, dst, rel, props=None) -> None:
    try:
        con.execute(
            "INSERT INTO edges(src,dst,rel,props,created) VALUES(?,?,?,?,?)",
            (src, dst, rel, json.dumps(props or {}), graph.now()))
        con.commit()
    except Exception:
        pass  # UNIQUE dup -> idempotent
    graph._enqueue(con, "edge", graph.EDGE_SEP.join((src, dst, rel)))
    con.commit()


# --- traceability: the agent tree (§2k) -----------------------------------
# Every captured turn hangs off an AGENT, and every agent off exactly one parent.
# The hooks do this with direct engine calls — no model, no tokens — so the tree
# is always reconstructable even when nobody asked for it.
def conversation_for(con, session_id: str, cwd: str = "") -> str:
    """The conversation node for this session (get-or-create, idempotent)."""
    res = graph.api_conversation(con, session_id or "adhoc", cwd=cwd)
    return res["node"]["id"] if res.get("ok") else ""


def root_agent(con, session_id: str, cwd: str = "", model: str = "") -> str:
    """The session's MAIN agent — the one the person is actually talking to.

    Messages belong to an agent, never to the conversation directly (§2k), so the
    main thread needs a node of its own or a subagent's branch would be the only
    place a message could live.
    """
    conv = conversation_for(con, session_id, cwd)
    if not conv:
        return ""
    row = con.execute(
        "SELECT n.id FROM edges e JOIN nodes n ON n.id=e.src "
        "WHERE e.dst=? AND e.rel=? AND n.type='agent' "
        "AND json_extract(n.props,'$.role')='main' LIMIT 1",
        (conv, graph.AREA_REL)).fetchone()
    if row:
        return row["id"]
    res = graph.api_agent_spawn(con, conv, "main", model=model,
                                session_id=session_id)
    return res["node"]["id"] if res.get("ok") else ""


def task_key(tool_input: dict) -> str:
    """Stable key for one Task call, so PostToolUse can find the node PreToolUse
    made. The prompt is hashed rather than stored twice — the agent node already
    carries it in its body."""
    import hashlib
    raw = (str(tool_input.get("subagent_type") or "")
           + "\x1f" + str(tool_input.get("prompt") or ""))
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def flush_async() -> None:
    """Fire-and-forget: drain the outbox to neo4j in a detached process so the
    hook returns instantly and never blocks on the network."""
    try:
        subprocess.Popen(
            [sys.executable, GRAPH_PY, "flush"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, close_fds=True)
    except Exception:
        pass


# --- heuristics (all token-free) ------------------------------------------
def is_capturable(text: str) -> bool:
    """Skip empties, slash-commands, and trivially short turns; keep the rest."""
    t = (text or "").strip()
    if len(t) < 8:
        return False
    if t.startswith("/"):          # a Claude Code slash command, not content
        return False
    return True


_TASK_CUES = re.compile(
    r"""(?ix)
    \b(
       remind \s+ me
     | don'?t \s+ forget
     | i \s+ (?:need|have|want) \s+ to
     | need \s+ to \s+ (?:remember|do|send|finish|ping|follow)
     | follow \s* up
     | make \s+ sure \s+ (?:to|that|i)
     | todo
     | to-do
     | task \s*:
     | action \s+ item
     | note \s+ to \s+ self
    )\b
    """)


def detect_tasks(text: str) -> list[str]:
    """Conservatively pull explicit personal to-dos out of a prompt. Only fires on
    clear intent cues (not every imperative the user gives the assistant), so the
    task list stays signal, not noise. Returns short task titles."""
    out, seen = [], set()
    for raw in re.split(r"(?<=[.!?\n])\s+", text or ""):
        s = raw.strip(" -*•\t")
        if not s or not _TASK_CUES.search(s):
            continue
        title = title_of(s, 90)
        key = title.lower()
        if key not in seen:
            seen.add(key)
            out.append(title)
    return out[:5]


def known_people(con):
    """First names + full names of `person` nodes, for assignee inference."""
    names = set()
    try:
        for r in con.execute("SELECT title FROM nodes WHERE type='person'").fetchall():
            t = (r["title"] or "").strip()
            if t:
                names.add(t.lower())
                names.add(t.split()[0].lower())
    except Exception:
        pass
    return names


_FIRST_PERSON = re.compile(
    r"(?i)\b(remind me|i need to|i have to|i've got to|i should|i must|i'?ll|i want to|my )\b")
# The OWNER is the actor here (they act ON the named person) — so these are the
# OWNER's tasks; the named person is the TARGET, not the assignee.
_OWNER_ACTION = re.compile(
    r"(?i)\b(ask|tell|ping|nudge|chase|remind|call|email|message|reply to|"
    r"follow[- ]?up with|get)\s+([a-z][a-z]+)")
# The named person is the actor here → the task is theirs.
_ASSIGN_OTHER = re.compile(r"(?i)\b(?:delegate to|hand off to|assign to)\s+([a-z][a-z]+)")


def infer_assignee(text: str, owner: str, people) -> str:
    """Best-effort, token-free: is this to-do the OWNER's action, someone else's,
    or unknown? Returns the owner, a person's name, or '' (leave unassigned — do
    NOT assume it's the owner's). NOTE: "ask/chase/remind <Name>" is the OWNER's
    task (they do the asking); the person is the target, not the assignee."""
    t = text or ""
    m = _ASSIGN_OTHER.search(t)          # explicitly handed to someone
    if m:
        name = m.group(1).lower()
        if name in ("me", "myself", "self"):
            return owner
        if not people or name in people:
            return name.capitalize()
    if _OWNER_ACTION.search(t):           # owner acts ON someone → owner's task
        return owner
    for p in sorted(people, key=len, reverse=True):   # "<Name> will/should/needs to…"
        if re.search(r"(?i)\b" + re.escape(p) + r"\s+(needs to|should|will|has to|is going to|to)\b", t):
            return p.split()[0].capitalize()
    if _FIRST_PERSON.search(t):
        return owner
    return ""  # unknown — leave it unassigned rather than defaulting to the owner


def summarize(text: str, limit: int = 300) -> str:
    """Extractive, token-free summary of an answer: drop fenced code blocks, take
    the opening sentences up to `limit` chars. Good enough to recall 'what was
    said' without paying a model to summarize."""
    t = re.sub(r"```.*?```", " ", text or "", flags=re.DOTALL)   # strip code fences
    t = re.sub(r"`[^`]*`", " ", t)                               # inline code
    t = " ".join(t.split())
    if len(t) <= limit:
        return t
    cut = t[:limit]
    dot = cut.rfind(". ")
    return (cut[:dot + 1] if dot > 80 else cut).rstrip() + " …"


def latest_capture(con, session_id: str, role: str) -> str | None:
    """Most recent `capture` node of a given role in this session (to link A->Q)."""
    rows = con.execute(
        "SELECT id, props FROM nodes WHERE type='capture' ORDER BY created DESC"
    ).fetchall()
    for r in rows:
        try:
            p = json.loads(r["props"] or "{}")
        except Exception:
            continue
        if p.get("session_id") == session_id and p.get("role") == role:
            return r["id"]
    return None


# How much of the transcript to read when we only want the last usage record.
# Reading the whole file would make the Stop hook cost O(session length) — on a
# long session that is megabytes, every turn, for four numbers.
_USAGE_TAIL_BYTES = 256 * 1024


def read_transcript_usage(path: str) -> dict:
    """Token counts for the last assistant message — `where did the token go?`.

    Claude Code already writes them to the transcript, so this is free
    information; the only cost worth avoiding is re-reading the whole file, hence
    the bounded seek to the tail. Returns {} when there is nothing to read — the
    shape varies between versions and a missing count is not an error.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _USAGE_TAIL_BYTES))
            chunk = fh.read().decode("utf-8", "replace")
    except Exception:
        return {}
    usage = {}
    for line in chunk.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue          # a partial first line from the seek
        try:
            obj = json.loads(line)
        except Exception:
            continue
        u = (obj.get("message") or {}).get("usage")
        if isinstance(u, dict):
            usage = u
    if not usage:
        return {}
    out = {}
    for src, dst in (("input_tokens", "tokens_in"),
                     ("output_tokens", "tokens_out"),
                     ("cache_read_input_tokens", "tokens_cache_read"),
                     ("cache_creation_input_tokens", "tokens_cache_write")):
        v = usage.get(src)
        if isinstance(v, int):
            out[dst] = v
    return out


def add_agent_tokens(con, agent_id: str, usage: dict) -> None:
    """Roll a turn's counts up onto its agent, so cost is readable per BRANCH.

    Per-message counts answer 'what did this turn cost'; the running total on the
    agent answers the question people actually ask — which part of the work was
    expensive.
    """
    if not agent_id or not usage:
        return
    try:
        row = con.execute("SELECT props FROM nodes WHERE id=?", (agent_id,)).fetchone()
        if not row:
            return
        props = json.loads(row["props"] or "{}")
        patch = {k: int(props.get(k) or 0) + int(v) for k, v in usage.items()}
        patch["turns"] = int(props.get("turns") or 0) + 1
        graph.api_update_node(con, agent_id, props_patch=patch)
    except Exception:
        pass          # a missing count must never break the capture


def read_transcript_tail(path: str):
    """Return (last_user_text, last_assistant_text) from a Claude Code transcript
    JSONL file. Robust to string- or block-form content; ignores tool traffic."""
    last_user = last_asst = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                msg = obj.get("message") or {}
                role = msg.get("role") or obj.get("type")
                content = msg.get("content")
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = " ".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
                text = text.strip()
                if not text:
                    continue
                if role == "user":
                    last_user = text
                elif role == "assistant":
                    last_asst = text
    except Exception:
        pass
    return last_user, last_asst
