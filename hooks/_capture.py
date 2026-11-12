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
import noise  # noqa: E402  (the deterministic noise heuristics — pure regex)
from _switch import hook_context  # noqa: E402,F401  (the one output shape hooks print)

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


def quest_session() -> bool:
    """True when this session is an Agent Quest quest, which capture must skip.

    Agent Quest exports AQ_QUEST_ID (non-empty) on every quest spawn; it exports
    it empty for sessions that are not quests, so only a non-empty value counts.
    Without the guard the nightly Biographer would groom the ledger's capture of
    its own quests (the capture-side twin of ledgerLoop.js keeping quest loops
    out of the Backlog import). Turn it off with `"skip_quest_capture": false`
    in settings.json or $MEMORY_LEDGER_NO_SKIP_QUEST_CAPTURE=1."""
    if not os.environ.get("AQ_QUEST_ID", "").strip():
        return False
    try:
        return graph.flag("skip_quest_capture")
    except Exception:
        return True


def connect():
    return graph.connect()


def realm() -> str:
    return graph.DEFAULT_REALM


def title_of(text: str, n: int = 80) -> str:
    t = " ".join((text or "").split())
    return (t[:n] + "…") if len(t) > n else (t or "(empty)")


def insert_node(con, ntype, title, body="", props=None, status="", realm_=None,
                nid=None) -> str:
    """Insert a node (mirrors graph.cmd_add_node's write) and queue it for neo4j —
    WITHOUT the CLI's stdout print, so hooks stay silent unless they choose to talk.
    `nid` pins a deterministic id (an upsert key); otherwise one is generated."""
    nid = nid or graph._gen_id(con, ntype, title)
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


# --- the capture noise filter (CONVENTIONS §0) -----------------------------
# `capture_filter` setting: "drop" (default) skips routine prompts, "cold" keeps
# them in the `cold` realm instead, "off" captures everything as before.
# `capture_min_chars` is the length under which a prompt with no verb is noise.
NOISE_SETTING = "capture_filter"
NOISE_MIN_SETTING = "capture_min_chars"
COLD_REALM = "cold"


def noise_mode() -> str:
    try:
        mode = graph.setting(NOISE_SETTING, "drop").strip().lower()
    except Exception:
        return "drop"
    return mode if mode in ("drop", "cold", "off") else "drop"


def noise_min_chars() -> int:
    try:
        return max(0, int(graph.setting(NOISE_MIN_SETTING, str(noise.DEFAULT_MIN_CHARS))))
    except Exception:
        return noise.DEFAULT_MIN_CHARS


def filter_prompt(text: str) -> tuple[bool, str]:
    """(keep, reason) for a raw prompt. Runs before any write and never calls a
    model. A prompt that carries an explicit to-do cue is always kept, so the
    filter can never swallow something `detect_tasks` would have turned into a
    task."""
    if noise_mode() == "off":
        return is_capturable(text), "filter-off"
    keep, reason = noise.classify(text, noise_min_chars())
    if not keep and reason in ("ack", "short-no-verb", "slash-command") and detect_tasks(text):
        return True, "task-cue"
    return keep, reason


def record_noise(con, session_id: str, cwd: str, reason: str, cold: bool) -> None:
    """Count a filtered prompt on the session's conversation node, so nothing is
    silently lost: `props.noise_dropped` (or `noise_cold` when kept in the cold
    realm) plus `props.noise_reasons` per rule."""
    conv = conversation_for(con, session_id, cwd)
    if not conv:
        return
    row = con.execute("SELECT props FROM nodes WHERE id=?", (conv,)).fetchone()
    props = json.loads((row["props"] if row else "") or "{}")
    key = "noise_cold" if cold else "noise_dropped"
    reasons = dict(props.get("noise_reasons") or {})
    reasons[reason] = int(reasons.get(reason) or 0) + 1
    graph.api_update_node(con, conv, props_patch={
        key: int(props.get(key) or 0) + 1, "noise_reasons": reasons})


_TASK_CUES = re.compile(
    r"""(?ix)
    \b(
       remind \s+ me
     | don'?t \s+ forget
     | i \s+ (?:need|have) \s+ to (?! \s+ (?:say|admit|confess|be\s+honest)\b)
     | need \s+ to \s+ (?:remember|do|send|finish|ping|follow)
     | follow \s* up
     | make \s+ sure \s+ (?:to|i)
     | todo
     | to-do
     | task \s*:
     | action \s+ item
     | note \s+ to \s+ self
    )\b
    """)


# Review findings and plan headings are not to-dos, even when they say "need to".
_NOT_A_TASK = re.compile(
    r"(?i)^\s*(?:\[(?:nit|blocker|minor|major|suggestion)\]|draft next steps\b|next steps\s*:)")


def detect_tasks(text: str) -> list[str]:
    """Conservatively pull explicit personal to-dos out of a prompt. Only fires on
    clear intent cues (not every imperative the user gives the assistant), so the
    task list stays signal, not noise. Returns short task titles."""
    out, seen = [], set()
    for raw in re.split(r"(?<=[.!?\n])\s+", text or ""):
        s = raw.strip(" -*•\t")
        if not s or _NOT_A_TASK.match(s) or not _TASK_CUES.search(s):
            continue
        if s.rstrip("\"')] ").endswith("?"):          # "what do I need to check?" asks; it does not commit
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


# --- session summary (token-free) ------------------------------------------
_REPO_URL = re.compile(r"(?:[:/])([^/:\s]+)/([^/\s]+?)(?:\.git)?/?$")
_REF_TICKET = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
_REF_NOT_TICKET = ("UTF-", "SHA-", "ISO-", "MD-")
_REF_PR = re.compile(
    r"https://github\.com/[\w.-]+/[\w.-]+/(?:pull|issues)/\d+|\b[\w.-]+/[\w.-]+#\d+\b")
SUMMARY_BODY_MAX = 1400
SUMMARY_OUTCOME_MAX = 300
SUMMARY_ASKED_MAX = 8        # user prompts listed, each clipped ...
SUMMARY_ASKED_CHARS = 90     # ... to this many chars: the session's own vocabulary


def _git(cwd: str, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=2)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def repo_of(cwd: str) -> str:
    """`owner/name` of the repo `cwd` is in, from its origin remote (ssh or https);
    else the top-level directory's name, else the cwd's. Never raises."""
    try:
        if not cwd:
            return ""
        url = _git(cwd, "config", "--get", "remote.origin.url")
        m = _REPO_URL.search(url) if url else None
        if m:
            return f"{m.group(1)}/{m.group(2)}"
        top = _git(cwd, "rev-parse", "--show-toplevel")
        return os.path.basename((top or cwd).rstrip("/\\"))
    except Exception:
        return ""


def _refs_in(text: str) -> list[str]:
    found = [m.group(0) for m in _REF_TICKET.finditer(text)
             if not m.group(0).startswith(_REF_NOT_TICKET)]
    found += [m.group(0) for m in _REF_PR.finditer(text)]
    return found


def _line(label: str, text: str, n: int) -> str:
    t = " ".join((text or "").split())
    return f"{label} {t[:n - 1] + '…' if len(t) > n else t}"


def session_summary(con, session_id: str, cwd: str = "") -> str | None:
    """Build and upsert the one summary note for a session; returns its id, or
    None when the session is not worth one. Reads only the session's own capture
    nodes — no model — so it is safe at SessionEnd and PreCompact."""
    if not session_id:
        return None
    caps = []
    for r in con.execute(
            "SELECT title, body, props, realm, created FROM nodes WHERE type='capture'"
            " AND json_extract(props,'$.session_id')=? ORDER BY created, rowid",
            (session_id,)):
        try:
            caps.append({**dict(r), "p": json.loads(r["props"] or "{}")})
        except Exception:
            continue
    users = [c for c in caps if c["p"].get("role") == "user" and c["realm"] != COLD_REALM]
    asst = [c for c in caps if c["p"].get("role") == "assistant"]
    if len(users) < 2 or (users[0]["title"] or "").startswith("<agent-message"):
        return None
    cwd = cwd or users[0]["p"].get("cwd") or ""
    repo = repo_of(cwd)
    started, ended = caps[0]["created"], caps[-1]["created"]
    # Every prompt, clipped, not just the first: the words the person used are
    # what a later prompt about the same work will share, so they must be in the
    # text full-text search reads. First prompts, then the last one if clipped.
    asked = users[:SUMMARY_ASKED_MAX]
    if len(users) > SUMMARY_ASKED_MAX:
        asked = users[:SUMMARY_ASKED_MAX - 1] + [users[-1]]
    lines = ["Asked: " + " | ".join(
        " ".join((u["title"] or "").split())[:SUMMARY_ASKED_CHARS] for u in asked)]
    if asst:
        lines.append(_line("Outcome:", asst[-1]["body"] or asst[-1]["title"], SUMMARY_OUTCOME_MAX))
    refs: list[str] = []
    for c in caps:
        for ref in _refs_in(f"{c['title']} {c['body']}"):
            if ref not in refs:
                refs.append(ref)
    if refs:
        lines.append("Refs: " + ", ".join(refs[:8]))
    decided = [r["title"] for r in con.execute(
        "SELECT title FROM nodes WHERE type='decision' AND created BETWEEN ? AND ?"
        " ORDER BY created LIMIT 3", (started, ended))]
    if decided:
        lines.append("Decided: " + "; ".join(d[:80] for d in decided))
    lines.append(f"Turns: {len(users)} / {len(asst)}")
    body = "\n".join(lines)[:SUMMARY_BODY_MAX]
    title = (f"Session — {repo or 'unknown'} — {started[:10]}: "
             f"{' '.join((users[0]['title'] or '').split())[:60]}")
    props = {"kind": "session-summary", "session_id": session_id, "cwd": cwd,
             "repo": repo, "started": started, "ended": ended, "turns": len(users)}
    nid = "note_session_" + session_id[:8]
    if con.execute("SELECT 1 FROM nodes WHERE id=?", (nid,)).fetchone():
        graph.api_update_node(con, nid, title=title, body=body, props_patch=props)
    else:
        insert_node(con, "note", title, body=body, props=props, nid=nid)
    conv = conversation_for(con, session_id, cwd)
    if conv:
        insert_edge(con, nid, conv, graph.AREA_REL)
    return nid


# --- gated per-prompt recall (opt-in, token-free to decide) ----------------
# Off by default (`prompt_recall` setting): unlike capture, an injection costs
# context tokens on the prompt it fires on. So it is gated hard — at most
# RECALL_MAX snippets of RECALL_SNIPPET chars, only when the prompt clearly
# matches a node (or points at past work), never the same node twice in a
# session — and every injection is logged on the turn's capture node
# (`props.recalled`), so the hit rate can be measured instead of guessed.
RECALL_SETTING = "prompt_recall"
RECALL_MAX = 2
RECALL_SNIPPET = 200
RECALL_CANDIDATES = 20
RECALL_MIN_HITS = 2          # distinct prompt terms a node must contain ...
RECALL_MIN_COVERAGE = 0.6    # ... and the share of the prompt's terms they are
RECALL_MAX_PROMPT = 2000     # a paste is not a question about past work
RECALL_MAX_TERMS = 12        # longest first; bounds the FTS OR query
RECALL_WINDOW_DAYS = 2       # how far back the per-session dedupe looks
# Worth putting in front of the model. Raw turns, rules and the agent tree are
# not: rules come through match-rules, captures are what digests summarize.
RECALL_TYPES = ("task", "subtask", "decision", "note", "project", "meeting",
                "jira-issue", "pr", "person", "requirement", "spec", "resource")
# "We decided…", "last time…", a ticket key or a PR link: the prompt is asking
# about past work, so one matching term is enough, with no coverage bar. Only
# multi-word phrases: a lone "before" or "again" is in too many ordinary prompts.
_PAST_WORK = re.compile(
    r"\b(?:last time|last week|yesterday|we (?:decided|agreed|discussed|said)|"
    r"what did (?:we|i|you)|did we|da última vez|semana passada|decidimos|"
    r"combinamos)\b", re.I)
# Case-sensitive on purpose: under re.I, "utf-8" and "sha-256" read as tickets.
_TICKET = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b|/pull/\d+")


def recall_enabled() -> bool:
    # A respond session answers someone else under the owner's name: nothing
    # from the brain is pushed into it unasked (the skill recalls deliberately).
    if os.environ.get("MEMORY_LEDGER_REQUEST_ID"):
        return False
    try:
        return graph.flag(RECALL_SETTING)
    except Exception:
        return False


def _recalled_in_session(con, session_id: str) -> set[str]:
    seen: set[str] = set()
    if not session_id:
        return seen
    # Bounded by recency so the scan stays small however many captures exist.
    since = (graph.datetime.now(graph.timezone.utc)
             - graph.timedelta(days=RECALL_WINDOW_DAYS)).isoformat(timespec="seconds")
    for (raw,) in con.execute(
            "SELECT props FROM nodes WHERE type='capture' AND created > ?"
            " AND json_extract(props,'$.session_id')=?"
            " AND json_extract(props,'$.recalled') IS NOT NULL", (since, session_id)):
        try:
            seen.update(json.loads(raw).get("recalled") or [])
        except Exception:
            pass
    return seen


# Words that are technical noise in this corpus: they appear in nearly every
# session, so sharing one says nothing about what a prompt is asking for.
_GENERIC = {
    "work", "working", "worked", "agent", "agents", "session", "sessions", "claude",
    "make", "made", "check", "update", "updated", "fix", "fixed", "please", "want",
    "need", "thing", "things", "stuff", "look", "see", "use", "used", "using", "run",
    "running",
}
_REPO_MENTION = re.compile(r"(?<![\w./-])([\w.-]+/[\w.-]+)(?![\w/-])")
RECALL_MIN_TERMS = 2         # "ok", "yes": too little to match on
RECALL_MAX_SUMMARIES = 1     # one session summary per injection; the rest is for other nodes
RECALL_SUMMARY_CANDIDATES = 8  # the summaries' own FTS pool, beside the shared one
RECALL_SUMMARY_SNIPPET = 260


def _recall_terms(prompt: str) -> list[str]:
    """Distinct content words of the prompt: no stop-words, nothing under three
    characters, none of the generic corpus noise. Longest first."""
    kept = {t for t in re.findall(r"\w+", (prompt or "").lower())
            if len(t) >= 3 and t not in graph._CUE_STOP and t not in _GENERIC}
    return sorted(kept, key=lambda t: (-len(t), t))[:RECALL_MAX_TERMS]


def _summary_repos(con) -> set[str]:
    try:
        return {(r[0] or "").lower() for r in con.execute(
            "SELECT DISTINCT json_extract(props,'$.repo') FROM nodes"
            " WHERE id LIKE 'note_session_%'") if r[0]}
    except Exception:
        return set()


def _names_a_repo(con, prompt: str, repo: str) -> bool:
    """The prompt names `owner/name` of a repo the brain has session summaries
    for, or the current repo by its last segment."""
    known = None
    mine = repo.rsplit("/", 1)[-1].lower() if repo else ""
    # The repo's name in plain words ("memory ledger" for memory-ledger) is how
    # people say it; the hyphenated form is how git says it.
    if mine and len(mine) >= 4 and re.search(
            r"\b" + r"[\s_-]*".join(map(re.escape, re.split(r"[-_]", mine))) + r"\b",
            prompt, re.I):
        return True
    for m in _REPO_MENTION.finditer(prompt):
        ref = m.group(1).lower()
        if mine and ref.rsplit("/", 1)[-1] == mine:
            return True
        known = _summary_repos(con) if known is None else known
        if ref in known:
            return True
    return False


def gated_recall(con, prompt: str, session_id: str, cwd: str = "") -> list[dict]:
    """The nodes worth injecting for this prompt — usually none. Pure SQL/FTS."""
    if len(prompt) > RECALL_MAX_PROMPT:
        return []
    terms = _recall_terms(prompt)
    if len(terms) < RECALL_MIN_TERMS:
        return []
    repo = repo_of(cwd) if cwd else ""
    past = bool(_PAST_WORK.search(prompt) or _TICKET.search(prompt)
                or _names_a_repo(con, prompt, repo))
    need = 1 if past else min(RECALL_MIN_HITS, len(terms))
    seen = _recalled_in_session(con, session_id)
    marks = ",".join("?" * len(RECALL_TYPES))
    match = " OR ".join(f'"{t}"*' for t in terms)
    rows = con.execute(
        "SELECT n.id, n.type, n.title, n.body, n.props, n.updated FROM nodes_fts f"
        " JOIN nodes n ON n.rowid=f.rowid WHERE nodes_fts MATCH ?"
        f" AND n.type IN ({marks}) AND n.realm != 'personal'"
        " AND n.status != 'cancelled' ORDER BY rank LIMIT ?",
        (match, *RECALL_TYPES, RECALL_CANDIDATES)).fetchall()
    # Session summaries get a pool of their own: in the shared pool a long
    # resource or decision outranks them on term count, and the summary that
    # says where the work stopped never reaches the candidate list.
    rows += con.execute(
        "SELECT n.id, n.type, n.title, n.body, n.props, n.updated FROM nodes_fts f"
        " JOIN nodes n ON n.rowid=f.rowid WHERE nodes_fts MATCH ?"
        " AND n.id LIKE 'note_session_%' AND n.realm != 'personal'"
        " ORDER BY rank LIMIT ?", (match, RECALL_SUMMARY_CANDIDATES)).fetchall()
    cands, taken = [], set()
    for nid, ntype, title, body, props, updated in rows:
        if nid in taken:
            continue
        taken.add(nid)
        if nid in seen or (title or "").startswith("Digest —"):
            continue
        # Whole words, prefix-matched the way FTS `"t"*` matches: "use" must not
        # count inside "because".
        words = set(re.findall(r"\w+", f"{title} {body}".lower()))
        hits = sum(1 for t in terms if any(w.startswith(t) for w in words))
        if hits < need or (not past and hits / len(terms) < RECALL_MIN_COVERAGE):
            continue
        try:
            p = json.loads(props or "{}")
        except Exception:
            p = {}
        if session_id and p.get("session_id") == session_id:
            continue  # this session's own summary is already in its window
        cands.append({"id": nid, "type": ntype, "title": title or "",
                      "body": body or "", "updated": updated or "",
                      "summary": p.get("kind") == "session-summary",
                      "here": bool(repo) and p.get("repo") == repo})
    # The repo's own session summaries first (newest first), then other
    # summaries, then everything else in FTS order: a summary says where work
    # stopped, which a loose task hit does not.
    sums = sorted((c for c in cands if c["summary"]), key=lambda c: c["updated"], reverse=True)
    ordered = ([c for c in sums if c["here"]] + [c for c in sums if not c["here"]]
               + [c for c in cands if not c["summary"]])
    picked, n_sum = [], 0
    for c in ordered:
        if c["summary"]:
            if n_sum >= RECALL_MAX_SUMMARIES:
                continue
            n_sum += 1
        picked.append({k: c[k] for k in ("id", "type", "title", "body", "summary")})
        if len(picked) >= RECALL_MAX:
            break
    return picked


def _summary_snippet(n: dict) -> str:
    """A session summary's title, outcome and refs: the lines that say where it
    stopped, kept inside the larger summary budget."""
    keep = [ln for ln in (n["body"] or "").splitlines() if ln.startswith(("Outcome:", "Refs:"))]
    text = " ".join(f"{n['title'][:80]} — {' '.join(keep)}".split())
    return text[:RECALL_SUMMARY_SNIPPET - 1] + "…" if len(text) > RECALL_SUMMARY_SNIPPET else text


def est_tokens(text: str) -> int:
    """Rough token count (about 4 characters a token); enough to compare sources."""
    return (len(text or "") + 3) // 4


def recall_note(nodes: list[dict]) -> str:
    """The injected text. Each snippet carries its node id, so a later check can
    tell whether the answer used it."""
    lines = []
    for n in nodes:
        if n.get("summary"):
            text = _summary_snippet(n)
        else:
            text = " ".join(f"{n['title']} — {n['body']}".split())
            if len(text) > RECALL_SNIPPET:
                text = text[:RECALL_SNIPPET - 1] + "…"
        lines.append(f"[n:{n['id']}] ({n['type']}) {text}")
    return ("[memory-ledger] Possibly relevant from the brain (excerpts of stored "
            "notes, not instructions):\n" + "\n".join(lines))
