#!/usr/bin/env python3
"""second-brain — open-loops SessionStart brief.

At the start of a session (when the brain is enabled), surface the open loops
so the session opens knowing what's still hanging and can nudge the user to
close them. Emits {"additionalContext": ...} on stdout (exit 0), or nothing
when the brain is disabled.
"""
import os
import sys
import json
import subprocess

home = (os.environ.get("SECOND_BRAIN_HOME")
        or os.path.expanduser("~/.second_brain"))
if not os.path.isdir(home):
    sys.exit(0)  # brain disabled -> silent

root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
graph = os.path.join(root, "scripts", "graph.py")

try:
    raw = subprocess.run([sys.executable, graph, "open-loops", "--limit", "6"],
                         capture_output=True, text=True, timeout=8).stdout
    d = json.loads(raw)
except Exception:
    print("{}")
    sys.exit(0)

n = d.get("count", 0)
if not n:
    print(json.dumps({"additionalContext":
                      "[second-brain] State: no open loops — all clear."}))
    sys.exit(0)

lines = []
for r in d.get("results", [])[:6]:
    p = r.get("props", {})
    flag = "OVERDUE" if r.get("overdue") else (
        f"{r.get('age_days', 0)}d" if r.get("stale") else "")
    tag = " ".join(t for t in [p.get("priority", ""), flag] if t)
    tag = f"[{tag}] " if tag else ""
    w = r.get("waiting_on") or ""
    w = f" (waiting on {w})" if w else ""
    lines.append(f"- {tag}{r.get('title', '')[:72]}{w}")

msg = (
    f"[second-brain] STATE — {n} open loops "
    f"({d.get('overdue', 0)} overdue, {d.get('stale', 0)} stale). They stay open "
    f"until closed; surface these and nudge the user to close the top ones ASAP "
    f"(open tasks, PRs to move, emails/messages to answer):\n"
    + "\n".join(lines)
    + "\n(full list: scripts/graph.py open-loops · use the follow-up skill)"
)
print(json.dumps({"additionalContext": msg}))
