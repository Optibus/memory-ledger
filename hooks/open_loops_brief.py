#!/usr/bin/env python3
"""memory-ledger — open-loops SessionStart brief (+ daily consolidation).

At the start of a session (when the brain is enabled), surface the open loops
AND the thread that was in play, so the session opens knowing what's hanging and
where it left off. Emits {"additionalContext": ...} on stdout (exit 0), or nothing
when the brain is disabled.

This fires on `/clear` too, which is the point: clearing the context window is the
cheapest thing you can do to a long conversation, and it is only safe because this
hands the thread straight back. A few hundred chars here buys back the right to
drop tens of thousands.

It also triggers the **daily consolidation** — once per day, detached — rolling
closed days' `capture` nodes into digest notes. This lives here rather than in a
cloud routine for a hard reason: the brain is a LOCAL file (`~/.second_brain`),
which a cloud routine cannot reach. Session start is the one moment we are
guaranteed to be running on the machine that owns the graph.
"""
import os
import sys
import json
import subprocess
import datetime

root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(root, "scripts"))
import graph as _graph  # noqa: E402  (the engine — shared enablement contract)

if _graph.disabled():
    sys.exit(0)  # brain explicitly off -> silent (bootstrap.py owns dir creation)

home = str(_graph.home_dir())
graph = os.path.join(root, "scripts", "graph.py")


def consolidate_daily():
    """Once a day, fold CLOSED days' captures into digests — 'sleep'.

    Only touches days already finished (`--older-than 1`), so today's raw
    captures stay intact while the day is still being lived. Detached and
    fire-and-forget: consolidation must never delay a session start, and it is
    non-destructive (originals are kept and linked), so a missed or interrupted
    run costs nothing — the next session picks it up. Opt out with
    $MEMORY_LEDGER_NO_CONSOLIDATE (legacy $SECOND_BRAIN_NO_CONSOLIDATE also works).
    """
    # Via the engine's _env() rather than os.environ, so this honours BOTH
    # prefixes like every other config lookup. Reading os.environ directly here
    # is how an opt-out silently stops working for anyone using the new name.
    if _graph._env("NO_CONSOLIDATE"):
        return
    stamp = os.path.join(home, ".last_consolidate")
    today = datetime.date.today().isoformat()
    try:
        with open(stamp) as f:
            if f.read().strip() == today:
                return                      # already done today
    except OSError:
        pass
    try:
        with open(stamp, "w") as f:
            f.write(today)                  # write first: never loop on failure
        subprocess.Popen(
            [sys.executable, graph, "consolidate", "--older-than", "1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass                                # a capture must never break a session


consolidate_daily()

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
                      "[memory-ledger] State: no open loops — all clear."}))
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

# The thread that was in play — what makes a /clear recoverable. Best-effort:
# a slow or failed call must never block the brief.
focus_lines = []
try:
    rr = subprocess.run([sys.executable, graph, "resume", "--budget", "1400",
                         "--limit", "3"],
                        capture_output=True, text=True, timeout=8).stdout
    for f in (json.loads(rr).get("focus") or [])[:3]:
        focus_lines.append(f"- {f.get('title', '')[:70]}")
except Exception:
    pass

focus_block = ("\nIn play when you left off:\n" + "\n".join(focus_lines)
               if focus_lines else "")

msg = (
    f"[memory-ledger] STATE — {n} open loops "
    f"({d.get('overdue', 0)} overdue, {d.get('stale', 0)} stale). They stay open "
    f"until closed; surface these and nudge the user to close the top ones ASAP "
    f"(open tasks, PRs to move, emails/messages to answer):\n"
    + "\n".join(lines)
    + focus_block
    + "\n(full list: open-loops · thread + parked: resume · park to offload context)"
)
print(json.dumps({"additionalContext": msg}))
