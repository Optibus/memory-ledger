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
# _switch, not graph: this runs on every SessionStart and the very next line may
# decide to do nothing at all. The engine costs ~55 ms of transitive imports to
# reach the same answer, and charging that to the user who turned capture OFF is
# the wrong way round. Same predicate either way — graph re-exports these.
import _switch  # noqa: E402  (the shared enablement contract — CONVENTIONS §2d)

if _switch.disabled():
    sys.exit(0)  # brain explicitly off -> silent (bootstrap.py owns dir creation)

home = str(_switch.home_dir())
graph = os.path.join(root, "scripts", "graph.py")


def revived_lines():
    """What became possible again since we last looked.

    This is the payoff of the whole traceability side (CONVENTIONS §2k): a gauge
    moved, and the things that were only ever blocked on it fall out of an
    indexed query — nobody has to remember they were waiting. It NOTIFIES; the
    ideas are proposals to reconsider, never work to resume. `recheck` keeps its
    own quiet window, so this cannot become noise a person learns to skip.
    """
    try:
        # --apply WRITES (it revives what unblocked), and a writer pushes to the
        # neo4j master inline. Session start must not wait on the network, so the
        # push is suppressed here and drained by a detached flush below — the same
        # bargain the capture hooks make.
        env = {**os.environ, "SECOND_BRAIN_NO_AUTOSYNC": "1"}
        raw = subprocess.run([sys.executable, graph, "recheck", "--apply"],
                             capture_output=True, text=True, timeout=8,
                             env=env).stdout
        items = json.loads(raw).get("items") or []
    except Exception:
        return []
    if items:
        try:
            subprocess.Popen([sys.executable, graph, "flush"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        except Exception:
            pass
    return [f"- {i.get('title', '')[:70]} (was: {i.get('reason_discarded', '')[:50]})"
            for i in items[:3]]


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
    if _switch._env("NO_CONSOLIDATE"):
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
    rev = revived_lines()
    note = "[memory-ledger] State: no open loops — all clear."
    if rev:
        note += ("\nBecame possible again (the condition that blocked these no "
                 "longer holds — offer them, don't resume them):\n" + "\n".join(rev))
    print(json.dumps({"additionalContext": note}))
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

revived = revived_lines()
revived_block = ("\nBecame possible again (a condition that blocked these no longer "
                 "holds — offer them, don't resume them):\n" + "\n".join(revived)
                 if revived else "")

msg = (
    f"[memory-ledger] STATE — {n} open loops "
    f"({d.get('overdue', 0)} overdue, {d.get('stale', 0)} stale). They stay open "
    f"until closed; surface these and nudge the user to close the top ones ASAP "
    f"(open tasks, PRs to move, emails/messages to answer):\n"
    + "\n".join(lines)
    + focus_block
    + revived_block
    + "\n(full list: open-loops · thread + parked: resume · park to offload context)"
)
print(json.dumps({"additionalContext": msg}))
