#!/usr/bin/env python3
"""SessionEnd / PreCompact hook — close the branch, record the compaction.

SessionEnd closes the conversation and stamps an outcome on every agent still
open, so no branch is left in a state that reads the same as a crash.

PreCompact records ONE context event: the window was summarized. That is the one
moment Claude Code tells us the context changed wholesale, and the delta is what
later answers "why was this in the window?" without storing the window itself
fifty times over (§2k).

stdin: Claude Code JSON incl. `session_id`; `trigger` on PreCompact.
stdout: none.
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
    phase = sys.argv[1] if len(sys.argv) > 1 else "end"
    sid = data.get("session_id") or ""
    if not sid:
        return
    try:
        con = C.connect()
        if phase == "compact":
            agent = C.root_agent(con, sid, data.get("cwd") or "")
            if agent:
                C.graph.api_context_event(
                    con, agent, "summarized", "window",
                    f"session:{sid[:8]}:{data.get('trigger') or 'auto'}")
        else:
            # one implementation for both paths — the CLI's conversation-end
            # and this hook must never disagree about what an outcome means
            C.graph.api_conversation_end(con, sid, data.get("reason") or "")
        C.flush_async()
    except Exception:
        return  # never break the session


if __name__ == "__main__":
    main()
