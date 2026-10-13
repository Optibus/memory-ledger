#!/usr/bin/env python3
"""Capture noise filter + decay tiers (CONVENTIONS §0, §2h).

Stdlib only. The pure heuristics and the decay math are tested in process; the
hook and the `decay` command run for real against a THROWAWAY brain in a temp
dir, never ~/.memory-ledger:

    python3 tests/test_noise_decay.py
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import decay  # noqa: E402
import noise  # noqa: E402

BRAIN = tempfile.mkdtemp(prefix="ledger-noise-test-")
ENV = {**os.environ, "SECOND_BRAIN_HOME": BRAIN, "MEMORY_LEDGER_HOME": BRAIN,
       "SECOND_BRAIN_NO_AUTOSYNC": "1"}
FAILS = []


def ck(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def g(*a, env=None):
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "graph.py"), *a],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env or ENV)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"ok": False, "raw": p.stdout[:300], "stderr": p.stderr[:300]}


def prompt_hook(sid, text, env=None):
    return subprocess.run(
        [sys.executable, str(ROOT / "hooks" / "capture_prompt.py")],
        input=json.dumps({"session_id": sid, "cwd": "/tmp", "prompt": text}),
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env or ENV)


def db():
    con = sqlite3.connect(str(Path(BRAIN) / "graph.db"))
    con.row_factory = sqlite3.Row
    return con


def test_filter():
    print("\n== filter: noise is dropped ==")
    noisy = {
        "continue": "ack", "ok": "ack", "Yes, go": "ack", "sim, pode continuar": "ack",
        "/clear": "slash-command", "/memory-ledger:recall": "slash-command",
        "<system-reminder>\nthe task tools...\n</system-reminder>": "system-tag",
        "<task-notification><id>abc</id></task-notification>": "system-tag",
        '{"type":"result","ok":true}': "json-blob",
        "[Image #1]": "image-only", "[Image #1] [Image #2]": "image-only",
        "Digest": "short-no-verb", "the second one": "short-no-verb",
        "   ": "empty",
    }
    for text, why in noisy.items():
        keep, reason = noise.classify(text)
        ck(f"drop {text[:34]!r} -> {why}", (not keep) and reason == why, reason)

    print("\n== filter: signal is kept ==")
    signal = {
        "TypeError: 'NoneType' object is not subscriptable": "error",
        "got ENOENT opening the db": "error",
        "we decided to use sqlite for this": "decision",
        "always run the tests before pushing": "decision",
        "from now on use branches": "decision",
        "see https://example.com/x": "url",
        "look at scripts/graph.py": "path",
        "open ~/dev/x": "path",
        "git push origin": "config",
        "change the env var for staging": "config",
        "CORD-123": "ticket",
        "Add a decay command that expires old captures into weekly notes": "content",
        "fix it": "content",                      # short, but it has a verb
    }
    for text, why in signal.items():
        keep, reason = noise.classify(text)
        ck(f"keep {text[:34]!r} -> {why}", keep and reason == why, reason)

    ck("signal beats ack ('ok, always do X')", noise.classify("ok always do X")[0])
    ck("a system tag is noise even when it quotes an error",
       not noise.classify("<system-reminder>TypeError: boom</system-reminder>")[0])
    ck("an uppercase-less word is not an error code ('explain')",
       noise.classify("explain how decay works in the engine")[1] == "content")
    ck("min_chars is a knob", noise.classify("the second one", min_chars=5)[0])
    ck("a slash path is not a slash command", noise.classify("/Users/a/b.py why")[0])
    ck("a slash command naming a ticket is kept",
       noise.classify("/delegate OS-85380") == (True, "ticket"))
    ck("a slash command with plain args is still noise",
       noise.classify("/loop 5m") == (False, "slash-command"))


def test_raw_prompt_titles():
    print("\n== raw-prompt task titles ==")
    for t in ("Can we push it to them without they need to do anything?",
              "THE TEST BEFORE WRITING: name the… - Close the loop",
              "I want to build this tool https://x.io", "<pasted_content id=1> a",
              "please check the build"):
        ck(f"raw: {t[:40]!r}", noise.looks_like_raw_prompt(t))
    for t in ("Check Legato renewal deadline with Nitsan",
              "Schedule a meeting for Shai and Ilan", ""):
        ck(f"real task: {t[:40]!r}", not noise.looks_like_raw_prompt(t))


def test_decay_math():
    print("\n== decay math ==")
    t = decay.DEFAULT_HALF_LIVES
    ck("defaults: decision 90 / task 14 / bugfix 14 / capture 7 / change 7",
       (t["decision"], t["task"], t["bugfix"], t["capture"], t["change"])
       == (90, 14, 14, 7, 7))
    ck("weight at 0 d is 1", decay.weight(0, 7) == 1.0)
    ck("weight at one half-life is 0.5", abs(decay.weight(7, 7) - 0.5) < 1e-9)
    ck("weight at two half-lives is 0.25", abs(decay.weight(28, 14) - 0.25) < 1e-9)
    ck("weight floors, never reaches 0", decay.weight(10_000, 7) == decay.WEIGHT_FLOOR)
    ck("no half-life -> no decay", decay.weight(500, None) == 1.0)
    ck("negative age is clamped", decay.weight(-5, 7) == 1.0)
    ck("a decision outlasts a capture at 30 d",
       decay.weight(30, 90) > decay.weight(30, 7))

    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    iso = lambda d: (now - timedelta(days=d)).isoformat(timespec="seconds")
    cap = {"type": "capture", "props": {}, "created": iso(7)}
    ck("rank_weight: a 7-day capture is at 0.5",
       abs(decay.rank_weight(cap, at=now) - 0.5) < 1e-3)
    ck("rank_weight: props.kind beats type",
       decay.rank_weight({"type": "note", "props": {"kind": "decision"}, "created": iso(90)},
                         at=now) > 0.49)
    ck("rank_weight: an open task does not decay",
       decay.rank_weight({"type": "task", "status": "open", "props": {}, "created": iso(60)},
                         at=now) == 1.0)
    ck("rank_weight: a closed task decays",
       decay.rank_weight({"type": "task", "status": "done", "props": {}, "created": iso(14)},
                         at=now) < 0.51)
    ck("rank_weight: unlisted type does not decay",
       decay.rank_weight({"type": "person", "props": {}, "created": iso(900)}, at=now) == 1.0)

    ck("half_lives: override applies", decay.half_lives({"capture": 3})["capture"] == 3)
    ck("half_lives: JSON setting applies",
       decay.half_lives(configured='{"task": 30}')["task"] == 30)
    ck("half_lives: junk and non-positive values are ignored",
       decay.half_lives({"capture": 0, "task": "x", "decision": -1})
       == decay.DEFAULT_HALF_LIVES)
    ck("half_lives: broken JSON falls back",
       decay.half_lives(configured="{nope") == decay.DEFAULT_HALF_LIVES)

    ck("iso_week plain", decay.iso_week("2026-10-08") == "2026-W41")
    ck("iso_week uses the ISO year at the boundary",
       decay.iso_week("2027-01-01") == "2026-W53" and decay.iso_week("2024-12-30") == "2025-W01")


def seed(con, nid, ntype, title, age_days, props=None, status="", body="", realm="work"):
    ts = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat(timespec="seconds")
    con.execute(
        "INSERT INTO nodes(id,type,title,body,props,status,due,realm,created,updated)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (nid, ntype, title, body or title, json.dumps(props or {}), status, "", realm, ts, ts))
    con.commit()


def test_hook_filter():
    print("\n== hook: drop and count ==")
    g("init")
    for text in ("continue", "ok", "/clear", "<system-reminder>x</system-reminder>",
                 "[Image #3]", "the second one"):
        r = prompt_hook("s-noise", text)
        ck(f"hook silent on {text[:20]!r}", r.returncode == 0 and r.stdout.strip() == "",
           r.stderr[-120:])
    prompt_hook("s-noise", "Add a decay command that expires old captures into weekly notes")
    prompt_hook("s-noise", "TypeError: x is not subscriptable")
    con = db()
    caps = con.execute("SELECT title FROM nodes WHERE type='capture'").fetchall()
    ck("only the two real prompts were stored", len(caps) == 2, str([c[0][:20] for c in caps]))
    conv = con.execute("SELECT props FROM nodes WHERE type='conversation'").fetchone()
    p = json.loads(conv["props"])
    ck("dropped count is on the conversation node", p.get("noise_dropped") == 6, str(p))
    ck("per-rule reasons are kept",
       p.get("noise_reasons", {}).get("ack") == 2 and p["noise_reasons"].get("slash-command") == 1,
       str(p.get("noise_reasons")))
    ck("a to-do cue is never swallowed",
       (prompt_hook("s-noise", "remind me to ok"), True)[1]
       and db().execute("SELECT COUNT(*) FROM nodes WHERE type='task'").fetchone()[0] == 1)

    print("\n== hook: cold realm ==")
    env = {**ENV, "MEMORY_LEDGER_CAPTURE_FILTER": "cold"}
    prompt_hook("s-cold", "continue", env=env)
    row = db().execute("SELECT realm, props FROM nodes WHERE type='capture' "
                       "AND title='continue'").fetchone()
    ck("cold mode stores the raw prompt in realm 'cold'", row and row["realm"] == "cold")
    ck("cold capture says why", row and json.loads(row["props"]).get("noise_reason") == "ack")
    conv = db().execute("SELECT props FROM nodes WHERE id LIKE '%s-cold%' OR "
                        "json_extract(props,'$.session_id')='s-cold' AND type='conversation'").fetchone()
    ck("cold count is on the conversation node",
       conv and json.loads(conv["props"]).get("noise_cold") == 1)

    print("\n== hook: filter off ==")
    env = {**ENV, "MEMORY_LEDGER_CAPTURE_FILTER": "off"}
    prompt_hook("s-off", "ok sounds good", env=env)
    ck("off keeps the legacy behaviour (captures a >=8 char ack)",
       db().execute("SELECT COUNT(*) FROM nodes WHERE title='ok sounds good'").fetchone()[0] == 1)


def test_decay_command():
    print("\n== decay command ==")
    con = db()
    week_old = 9
    seed(con, "cap_old1", "capture", "refactor the recall path", week_old,
         {"role": "user", "session_id": "d"})
    seed(con, "cap_old2", "capture", "refactor the recall path", week_old,
         {"role": "user", "session_id": "d"})
    seed(con, "cap_old3", "capture", "Sure, here is the plan", week_old,
         {"role": "assistant", "session_id": "d"})
    seed(con, "cap_new", "capture", "brand new prompt", 1, {"role": "user"})
    seed(con, "t_raw", "task", "Can we push it to them without they need to do anything?",
         20, {"source": "auto-capture"}, status="open")
    seed(con, "t_trunc", "task", "I want to build this tool https://x.io and then…", 20,
         {"source": "auto-capture"}, status="open")
    seed(con, "t_real", "task", "Check Legato renewal deadline with Nitsan", 20, {"source": "auto-capture"},
         status="open")
    seed(con, "t_young", "task", "Can we ship it?", 3, {"source": "auto-capture"}, status="open")
    # prompt-shaped, but written by a person or a skill, not the capture hook
    seed(con, "t_human", "task", "Check if CI passes on OS-1?", 20, status="open")
    seed(con, "t_done", "task", "Why is the sky blue?", 30, status="done")
    con.close()
    before = db().execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    r = g("decay")
    ck("dry-run by default", r.get("dry_run") is True and r.get("ok"), str(r)[:120])
    ck("dry-run expires 3 captures", r["expire"]["captures"] == 3, str(r["expire"]))
    ck("dry-run flags the two raw-prompt tasks only",
       r["stale_tasks"]["flagged"] == 2
       and {t["id"] for t in r["stale_tasks"]["shown"]} == {"t_raw", "t_trunc"},
       str(r["stale_tasks"]))
    ck("dry-run wrote nothing", db().execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == before)
    ck("dry-run left tasks open",
       db().execute("SELECT COUNT(*) FROM nodes WHERE status='open' AND type='task'")
       .fetchone()[0] >= 5)
    ck("report carries no private '_' keys", not any(k.startswith("_") for k in r))

    r = g("decay", "--apply", "--no-backup")
    ck("apply ok", r.get("ok") and r["applied"]["captures_expired"] == 3, str(r.get("applied")))
    con = db()
    digests = con.execute("SELECT id,body,props FROM nodes WHERE type='note' "
                          "AND id LIKE 'note_week_%'").fetchall()
    ck("one weekly digest note", len(digests) == 1, str([d["id"] for d in digests]))
    body = digests[0]["body"]
    ck("digest has compressed, de-duplicated bullets",
       body.count("- refactor the recall path") == 1 and "Sure, here" not in body, body)
    ck("captures are kept, linked part-of the digest",
       con.execute("SELECT COUNT(*) FROM edges WHERE dst=? AND rel='part-of' "
                   "AND src LIKE 'cap_old%'", (digests[0]["id"],)).fetchone()[0] == 3)
    ck("expired captures are stamped",
       json.loads(con.execute("SELECT props FROM nodes WHERE id='cap_old1'")
                  .fetchone()[0]).get("expired_into") == digests[0]["id"])
    ck("a young capture is untouched",
       "expired_at" not in json.loads(con.execute("SELECT props FROM nodes WHERE id='cap_new'")
                                      .fetchone()[0]))
    st = {r[0]: r[1] for r in con.execute("SELECT id,status FROM nodes WHERE type='task'")}
    ck("raw-prompt tasks closed as dropped", st["t_raw"] == "dropped" and st["t_trunc"] == "dropped")
    ck("real, young and done tasks untouched",
       st["t_real"] == "open" and st["t_young"] == "open" and st["t_done"] == "done")
    ck("a task not spun up by the capture hook is never dropped", st["t_human"] == "open")
    ck("nothing was deleted", con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
       == before + 1)

    nodes_after = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    r = g("decay", "--apply", "--no-backup")
    ck("second apply is idempotent",
       r["applied"]["captures_expired"] == 0 and r["applied"]["tasks_dropped"] == 0
       and db().execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == nodes_after, str(r.get("applied")))

    print("\n== dropped is closed everywhere ==")
    listed = g("list", "--type", "task")
    ids = {n["id"] for n in listed.get("nodes", listed.get("results", []))}
    ck("`list` hides dropped tasks", "t_raw" not in ids and "t_real" in ids, str(sorted(ids))[:200])


def test_rank_hook():
    print("\n== recall applies the decay weight ==")
    con = db()
    seed(con, "dec_old", "decision", "quokka pipeline convention", 60)
    seed(con, "bug_old", "bugfix", "quokka pipeline convention", 60,
         {"kind": "bugfix"})
    con.close()
    r = g("recall", "quokka pipeline convention", "--limit", "5")
    rows = r.get("results") or r.get("nodes") or []
    score = {x["id"]: x.get("score") for x in rows}
    ck("both found", "dec_old" in score and "bug_old" in score, str(score))
    ck("a 60-day decision outranks a 60-day bugfix (90 d vs 14 d half-life)",
       score.get("dec_old", 0) > score.get("bug_old", 0), str(score))


def main():
    test_filter()
    test_raw_prompt_titles()
    test_decay_math()
    test_hook_filter()
    test_decay_command()
    test_rank_hook()
    shutil.rmtree(BRAIN, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
