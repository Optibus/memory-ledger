#!/usr/bin/env python3
"""UserPromptSubmit hook — capture the QUESTION with no model call.

Fires on every user prompt in every session where the brain is enabled. Stores
the prompt directly in the graph (Python/SQL, no model call) as a `capture`
node, and dynamically creates `task` nodes for any explicit to-dos it contains,
linked back to the turn. Recall stays free — the data is in the DB for
graph.py / brain_query.py to read without a model call.

NOTE: storing costs nothing, but the `additionalContext` printed below DOES enter
the conversation (~200 chars per prompt). Measured at ~0.01% of a session's
tokens; keep this string short. See CONVENTIONS §0.

stdin: Claude Code JSON incl. `prompt`, `session_id`, `cwd`.
stdout: optional {"additionalContext": "..."} — kept tiny on purpose.
"""

import json
import os
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
    if not C.is_capturable(prompt):
        return
    sid = data.get("session_id") or ""
    cwd = data.get("cwd") or ""
    try:
        con = C.connect()
        # The turn belongs to an AGENT, and that agent to this session's
        # conversation (§2k) — so a branch is always walkable from the root, and
        # a subagent's discarded alternative never dies with its branch.
        agent = C.root_agent(con, sid, cwd)
        turn = C.insert_node(
            con, "capture", C.title_of(prompt), body=prompt[:4000],
            props={"role": "user", "session_id": sid, "cwd": cwd,
                   "agent": agent, "captured_at": C.graph.now()})
        if agent:
            C.insert_edge(con, turn, agent, C.graph.AREA_REL)
        # dynamically spin up tasks + links for explicit to-dos in the prompt.
        # Infer the assignee (mine / someone else / unknown) instead of assuming
        # it's the owner's — so the dashboard's assignee filter is meaningful and
        # someone-else's ingested to-dos don't masquerade as the owner's action.
        made = 0
        owner = C.graph._env("OWNER", "Gabriel")
        people = C.known_people(con)
        for title in C.detect_tasks(prompt):
            who = C.infer_assignee(title, owner, people) or C.infer_assignee(prompt, owner, people)
            props = {"source": "auto-capture", "session_id": sid}
            if who:
                props["assignee"] = who
            tid = C.insert_node(con, "task", title, status="open", props=props)
            C.insert_edge(con, tid, turn, "captured-from")
            made += 1
        C.flush_async()
    except Exception:
        return  # never break the session

    note = ("[memory-ledger] Turn auto-captured to the brain (Python/SQL, no model call)"
            + (f"; {made} task(s) created" if made else "")
            + ". Recall is free via scripts/brain_query.py / graph.py search — "
              "don't spend tokens re-storing this turn.")
    print(json.dumps({"additionalContext": note}))


if __name__ == "__main__":
    main()
