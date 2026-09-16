#!/usr/bin/env python3
"""PreToolUse / PostToolUse hook (matcher: Task) — the agent tree, token-free.

A subagent is the place where alternatives get raised and dropped, and where the
work most reliably disappears: the branch ends and takes its reasoning with it.
This hook puts every subagent in the tree as it is spawned, and stamps its
outcome as it returns — pure Python + SQL, no model, no added latency.

Two phases, one script (the phase comes from --phase, so hooks.json stays flat):

  pre   a `Task` call is about to run -> agent node under this session's main
        agent, carrying role (subagent_type), the prompt, and a task_key so the
        post phase can find it again.
  post  the call returned -> outcome recorded (completed, or failed when the
        tool reports an error).

stdin: Claude Code JSON incl. `session_id`, `tool_name`, `tool_input`,
       and on post `tool_response`.
stdout: none — a tool hook that talks costs tokens on every tool call.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _capture as C  # noqa: E402


def _errored(resp) -> bool:
    """Did the Task come back as a failure? Shape varies by version, so this
    reads defensively and treats 'cannot tell' as success."""
    if isinstance(resp, dict):
        if resp.get("is_error") or resp.get("error"):
            return True
        for k in ("status", "outcome"):
            if str(resp.get(k, "")).lower() in ("error", "failed", "failure"):
                return True
    return False


def _pending(con, sid: str, key: str):
    """The still-open agent this session spawned for this exact Task call."""
    rows = con.execute(
        "SELECT id FROM nodes WHERE type='agent' AND status!='done' "
        "AND json_extract(props,'$.task_key')=? "
        "AND json_extract(props,'$.session_id')=? ORDER BY created DESC LIMIT 1",
        (key, sid)).fetchall()
    return rows[0]["id"] if rows else None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if not C.brain_enabled():
        return
    if (data.get("tool_name") or "") != "Task":
        return
    phase = sys.argv[1] if len(sys.argv) > 1 else "pre"
    sid = data.get("session_id") or ""
    ti = data.get("tool_input") or {}
    if not isinstance(ti, dict):
        return
    key = C.task_key(ti)
    try:
        con = C.connect()
        if phase == "pre":
            parent = C.root_agent(con, sid, data.get("cwd") or "")
            if not parent:
                return
            role = str(ti.get("subagent_type") or ti.get("description")
                       or "subagent")
            res = C.graph.api_agent_spawn(
                con, parent, role, model=str(ti.get("model") or ""),
                body=str(ti.get("prompt") or "")[:4000], session_id=sid)
            if res.get("ok"):
                C.graph.api_update_node(
                    con, res["node"]["id"],
                    props_patch={"task_key": key,
                                 "description": ti.get("description") or ""})
        else:
            aid = _pending(con, sid, key)
            if aid:
                # An outcome is always recorded. An agent with no outcome cannot
                # be told apart from one that crashed, and the tree stops being
                # trustworthy the moment that is true.
                C.graph.api_agent_finish(
                    con, aid,
                    "failed" if _errored(data.get("tool_response")) else "completed")
        C.flush_async()
    except Exception:
        return  # never break the session


if __name__ == "__main__":
    main()
