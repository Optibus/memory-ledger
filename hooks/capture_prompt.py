#!/usr/bin/env python3
"""UserPromptSubmit hook — capture the QUESTION with no model call.

Fires on every user prompt in every session where the brain is enabled. Stores
the prompt directly in the graph (Python/SQL, no model call) as a `capture`
node, and dynamically creates `task` nodes for any explicit to-dos it contains,
linked back to the turn. Recall stays free — the data is in the DB for
graph.py / brain_query.py to read without a model call.

Routine prompts (bare "ok", a slash command, a pasted system-reminder, ...) are
filtered first by the deterministic `scripts/noise.py` — dropped and counted on
the conversation node, or kept in the `cold` realm (`capture_filter` setting).

By default it prints nothing: "capture is on" is said once per session by the
SessionStart brief, where it sits in the cached prefix, instead of on every
prompt. With the opt-in `prompt_recall` setting it may inject up to two short
snippets of matching nodes (gated; see `_capture.gated_recall`). See
CONVENTIONS §0.

stdin: Claude Code JSON incl. `prompt`, `session_id`, `cwd`.
stdout: nothing, or {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
"additionalContext": "..."}} when gated recall found something.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _capture as C  # noqa: E402


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    if not C.brain_enabled():
        return
    # field name varies by Claude Code version: prompt | user_input
    prompt = (data.get("prompt") or data.get("user_input") or "").strip()
    keep, why = C.filter_prompt(prompt)
    sid = data.get("session_id") or ""
    cwd = data.get("cwd") or ""
    cold = False
    if not keep:
        if why in ("empty", "filter-off"):
            return
        # Routine prompt (CONVENTIONS §0). Never lost silently: it is counted on
        # the conversation node, or — with `capture_filter=cold` — stored in the
        # `cold` realm instead of dropped.
        cold = C.noise_mode() == "cold"
        try:
            con = C.connect()
            C.record_noise(con, sid, cwd, why, cold)
        except Exception:
            return
        if not cold:
            return
    try:
        con = C.connect()
        # The turn belongs to an AGENT, and that agent to this session's
        # conversation (§2k) — so a branch is always walkable from the root, and
        # a subagent's discarded alternative never dies with its branch.
        agent = C.root_agent(con, sid, cwd)
        # Before the insert, so this turn never matches itself.
        recalled = []
        if not cold and C.recall_enabled():
            try:
                recalled = C.gated_recall(con, prompt, sid)
            except Exception:
                recalled = []  # recall is a bonus; never cost the capture
        props = {"role": "user", "session_id": sid, "cwd": cwd,
                 "agent": agent, "captured_at": C.graph.now()}
        if recalled:
            # the log the hit rate is measured from, and the session's dedupe
            props["recalled"] = [n["id"] for n in recalled]
        if cold:
            props["noise_reason"] = why
        turn = C.insert_node(
            con, "capture", C.title_of(prompt), body=prompt[:4000], props=props,
            realm_=C.COLD_REALM if cold else None)
        if agent:
            C.insert_edge(con, turn, agent, C.graph.AREA_REL)
        # dynamically spin up tasks + links for explicit to-dos in the prompt.
        # Infer the assignee (mine / someone else / unknown) instead of assuming
        # it's the owner's — so the dashboard's assignee filter is meaningful and
        # someone-else's ingested to-dos don't masquerade as the owner's action.
        # Through graph.owner(), so a name set in Settings decides whose to-do
        # this is. Reading the env var alone left the hook assigning work to a
        # hardcoded name on every machine but one.
        owner = C.graph.owner()
        people = C.known_people(con)
        for title in ([] if cold else C.detect_tasks(prompt)):
            who = C.infer_assignee(title, owner, people) or C.infer_assignee(prompt, owner, people)
            props = {"source": "auto-capture", "session_id": sid}
            if who:
                props["assignee"] = who
            tid = C.insert_node(con, "task", title, status="open", props=props)
            C.insert_edge(con, tid, turn, "captured-from")
        C.flush_async()
    except Exception:
        return  # never break the session

    if recalled:
        print(C.hook_context("UserPromptSubmit", C.recall_note(recalled)))


if __name__ == "__main__":
    main()
