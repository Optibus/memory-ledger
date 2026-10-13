#!/usr/bin/env python3
"""memory-ledger — open-loops SessionStart brief (+ daily consolidation).

At the start of a session (when the brain is enabled), surface the open loops
AND the thread that was in play, so the session opens knowing what's hanging and
where it left off. Emits {"hookSpecificOutput": {"hookEventName": "SessionStart",
"additionalContext": ...}} on stdout (exit 0), or nothing when the brain is
disabled. It also says, once per session, that capture is on: the per-prompt
hook stays silent, so this is the one place the model learns it.

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

# Said once here, in the cached prefix, instead of on every prompt.
CAPTURE_ON = ("[memory-ledger] Capture is on: every turn is stored without a model "
              "call; don't spend tokens re-storing it. Recall is free via "
              "graph.py search / brain_query.py.")


def emit(note):
    print(_switch.hook_context("SessionStart", CAPTURE_ON + "\n" + note))
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
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
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
    run costs nothing — the next session picks it up. Opt out in the Settings
    page, or with $MEMORY_LEDGER_NO_CONSOLIDATE (legacy
    $SECOND_BRAIN_NO_CONSOLIDATE also works).
    """
    # Via _switch.flag() rather than os.environ, so this honours both env
    # prefixes AND the `consolidate` setting the web UI writes. Reading the
    # environment directly here is how an opt-out silently stops working for
    # anyone who set it somewhere other than their own shell.
    if not _switch.flag("consolidate"):
        return
    stamp = os.path.join(home, ".last_consolidate")
    today = datetime.date.today().isoformat()
    try:
        with open(stamp, encoding="utf-8") as f:
            if f.read().strip() == today:
                return                      # already done today
    except OSError:
        pass
    try:
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(today)                  # write first: never loop on failure
        subprocess.Popen(
            # --no-backup: this fires unattended every morning, and a snapshot
            # per session start would fill the disk with copies of a run nobody
            # asked for. The snapshot is for the runs a person triggers.
            [sys.executable, graph, "consolidate", "--older-than", "1",
             "--no-backup"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass                                # a capture must never break a session


consolidate_daily()


def learned_rules_daily():
    """Once a day, while the person has opted in, look for corrections they keep
    repeating and PROPOSE rules from them.

    OFF by default: `_switch.flag("learned_rules")` is False until the setting is
    turned on, so for everyone who has not asked, this is one settings read and a
    return. On, it is the same shape as consolidate_daily — stamp first, detached,
    silent, no model — and it only ever writes `rule-proposal` nodes, never a
    rule; a person accepts those deliberately (`graph.py rule-proposals`)."""
    if not _switch.flag("learned_rules"):
        return
    stamp = os.path.join(home, ".last_learned_rules")
    today = datetime.date.today().isoformat()
    try:
        with open(stamp, encoding="utf-8") as f:
            if f.read().strip() == today:
                return
    except OSError:
        pass
    try:
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(today)                  # write first: never loop on failure
        subprocess.Popen(
            [sys.executable, graph, "propose-rules"],
            env={**os.environ, "SECOND_BRAIN_NO_AUTOSYNC": "1"},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass


learned_rules_daily()


def _detached(*argv):
    subprocess.Popen([sys.executable, *argv], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     start_new_session=True)


def mirror_sync():
    """Sync with the neo4j master, detached, when one is configured.

    Every session start: a two-way `--sync`, so this session opens on what the
    other machines wrote and they get what this one did. Once a day on top: a
    full reconcile, which re-sends anything a failed push left behind."""
    if not (os.path.exists(os.path.join(home, "neo4j.json"))
            or _switch._env("NEO4J_URL")):
        return
    script = os.path.join(root, "scripts", "brain_sync.py")
    try:
        stamp = os.path.join(home, ".last_mirror")
        today = datetime.date.today().isoformat()
        try:
            with open(stamp, encoding="utf-8") as f:
                full = f.read().strip() != today
        except OSError:
            full = True
        if full:
            with open(stamp, "w", encoding="utf-8") as f:
                f.write(today)
        # the full push after the pull, never beside it: one process, in order
        _detached("-c", "import subprocess,sys;"
                  f"subprocess.run([sys.executable,{script!r},'--sync','--quiet']);"
                  + (f"subprocess.run([sys.executable,{script!r},'--quiet'])" if full else ""))
    except Exception:
        pass                                # a session start must never break


mirror_sync()

try:
    raw = subprocess.run([sys.executable, graph, "open-loops", "--limit", "6"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8).stdout
    d = json.loads(raw)
except Exception:
    print(_switch.hook_context("SessionStart", CAPTURE_ON))
    sys.exit(0)

n = d.get("count", 0)
if not n:
    rev = revived_lines()
    note = "[memory-ledger] State: no open loops — all clear."
    if rev:
        note += ("\nBecame possible again (the condition that blocked these no "
                 "longer holds — offer them, don't resume them):\n" + "\n".join(rev))
    emit(note)
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
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8).stdout
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
emit(msg)
