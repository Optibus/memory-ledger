#!/usr/bin/env python3
"""Stop hook — capture the ANSWER as a summary, token-free.

Fires when the assistant finishes a turn. Reads the transcript, takes an
extractive (no-model) summary of the final answer, stores it as a `capture` node,
and links it to the question turn from the same session. No tokens spent.

stdin: Claude Code JSON incl. `transcript_path`, `session_id`, `stop_hook_active`.
stdout: none (Stop hooks don't inject context).
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
    sid = data.get("session_id") or ""
    # Newer Claude Code hands the final answer directly on stdin; older versions
    # only give a transcript path we parse. Prefer the direct field, fall back.
    answer = (data.get("last_assistant_message") or "").strip()
    tp = data.get("transcript_path")
    if not answer:
        if not tp:
            return
        _user, answer = C.read_transcript_tail(tp)
    # Claude Code already wrote the turn's token counts to the transcript, so
    # recording them costs a bounded tail-read and nothing else — and it is what
    # later answers "where was the token actually spent?" (CONVENTIONS §2k).
    usage = C.read_transcript_usage(tp) if tp else {}
    if not answer or len(answer.strip()) < 8:
        return
    try:
        con = C.connect()
        summary = C.summarize(answer)
        agent = C.root_agent(con, sid, data.get("cwd") or "")
        aid = C.insert_node(
            con, "capture", C.title_of(summary), body=summary,
            props={"role": "assistant", "session_id": sid, "agent": agent,
                   "captured_at": C.graph.now(), **usage})
        if agent:
            C.insert_edge(con, aid, agent, C.graph.AREA_REL)
            C.add_agent_tokens(con, agent, usage)
        q = C.latest_capture(con, sid, "user")
        if q:
            C.insert_edge(con, aid, q, "follows-up")
        C.flush_async()
    except Exception:
        return  # never break the session


if __name__ == "__main__":
    main()
