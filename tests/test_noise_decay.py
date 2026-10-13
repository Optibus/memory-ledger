"""Capture noise filter + decay tiers (CONVENTIONS section 0, section 2h).

The pure heuristics and the decay math are tested in process; the hook and the
`decay` command run for real against a THROWAWAY brain, never ~/.memory-ledger:

    python -m pytest tests/test_noise_decay.py

The brain-backed classes share one brain and run in file order (the hook
captures, then the decay command acts on what was seeded, then recall ranks).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import decay
import noise

ROOT = Path(__file__).resolve().parent.parent
GRAPH_PY = ROOT / "scripts" / "graph.py"
CAPTURE_HOOK = ROOT / "hooks" / "capture_prompt.py"
UTF8 = "utf-8"
FILTER_VAR = "MEMORY_LEDGER_CAPTURE_FILTER"
REAL_PROMPT = "Add a decay command that expires old captures into weekly notes"
RAW_TASK = "Can we push it to them without they need to do anything?"
TRUNCATED_TASK = "I want to build this tool https://x.io and then…"
REAL_TASK = "Check Legato renewal deadline with Nitsan"

# ------------------------------------------------------------------- the filter
NOISY = {
    "continue": "ack", "ok": "ack", "Yes, go": "ack", "sim, pode continuar": "ack",
    "/clear": "slash-command", "/memory-ledger:recall": "slash-command",
    "<system-reminder>\nthe task tools...\n</system-reminder>": "system-tag",
    "<task-notification><id>abc</id></task-notification>": "system-tag",
    '{"type":"result","ok":true}': "json-blob",
    "[Image #1]": "image-only", "[Image #1] [Image #2]": "image-only",
    "Digest": "short-no-verb", "the second one": "short-no-verb",
    "   ": "empty",
}
SIGNAL = {
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
    "fix it": "content",  # short, but it has a verb
}


@pytest.mark.parametrize("text, why", NOISY.items(), ids=[f"{t[:24]!r}->{w}" for t, w in NOISY.items()])
def test_noise_is_dropped(text, why):
    keep, reason = noise.classify(text)
    assert (not keep) and reason == why, reason


@pytest.mark.parametrize("text, why", SIGNAL.items(), ids=[f"{t[:24]!r}->{w}" for t, w in SIGNAL.items()])
def test_signal_is_kept(text, why):
    keep, reason = noise.classify(text)
    assert keep and reason == why, reason


def test_signal_beats_ack():
    assert noise.classify("ok always do X")[0]


def test_a_system_tag_is_noise_even_when_it_quotes_an_error():
    assert not noise.classify("<system-reminder>TypeError: boom</system-reminder>")[0]


def test_an_uppercase_less_word_is_not_an_error_code():
    assert noise.classify("explain how decay works in the engine")[1] == "content"


def test_min_chars_is_a_knob():
    assert noise.classify("the second one", min_chars=5)[0]


def test_a_slash_path_is_not_a_slash_command():
    assert noise.classify("/Users/a/b.py why")[0]


def test_a_slash_command_naming_a_ticket_is_kept():
    assert noise.classify("/delegate OS-85380") == (True, "ticket")


def test_a_slash_command_with_plain_args_is_still_noise():
    assert noise.classify("/loop 5m") == (False, "slash-command")


# ---------------------------------------------------------- raw-prompt task titles
@pytest.mark.parametrize("title", [
    RAW_TASK, "THE TEST BEFORE WRITING: name the… - Close the loop",
    "I want to build this tool https://x.io", "<pasted_content id=1> a", "please check the build",
])
def test_a_raw_prompt_title_is_spotted(title):
    assert noise.looks_like_raw_prompt(title)


@pytest.mark.parametrize("title", [
    REAL_TASK, "Schedule a meeting for Shai and Ilan", "",
], ids=["legato", "meeting", "empty"])
def test_a_real_task_title_is_not(title):
    assert not noise.looks_like_raw_prompt(title)


# --------------------------------------------------------------------- decay math
def test_default_half_lives():
    t = decay.DEFAULT_HALF_LIVES
    assert (t["decision"], t["task"], t["bugfix"], t["capture"], t["change"]) == (90, 14, 14, 7, 7)


@pytest.mark.parametrize("age, half_life, expected", [
    (0, 7, 1.0), (7, 7, 0.5), (28, 14, 0.25), (500, None, 1.0), (-5, 7, 1.0),
], ids=["zero-age", "one-half-life", "two-half-lives", "no-half-life", "negative-age"])
def test_weight(age, half_life, expected):
    assert decay.weight(age, half_life) == pytest.approx(expected, abs=1e-9)


def test_weight_floors_never_reaches_zero():
    assert decay.weight(10_000, 7) == decay.WEIGHT_FLOOR


def test_a_decision_outlasts_a_capture_at_30_days():
    assert decay.weight(30, 90) > decay.weight(30, 7)


NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def _iso(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat(timespec="seconds")


def test_rank_weight_a_seven_day_capture_is_at_half():
    cap = {"type": "capture", "props": {}, "created": _iso(7)}
    assert decay.rank_weight(cap, at=NOW) == pytest.approx(0.5, abs=1e-3)


def test_rank_weight_props_kind_beats_type():
    node = {"type": "note", "props": {"kind": "decision"}, "created": _iso(90)}
    assert decay.rank_weight(node, at=NOW) > 0.49


def test_rank_weight_an_open_task_does_not_decay():
    node = {"type": "task", "status": "open", "props": {}, "created": _iso(60)}
    assert decay.rank_weight(node, at=NOW) == 1.0


def test_rank_weight_a_closed_task_decays():
    node = {"type": "task", "status": "done", "props": {}, "created": _iso(14)}
    assert decay.rank_weight(node, at=NOW) < 0.51


def test_rank_weight_an_unlisted_type_does_not_decay():
    assert decay.rank_weight({"type": "person", "props": {}, "created": _iso(900)}, at=NOW) == 1.0


def test_half_lives_override_applies():
    assert decay.half_lives({"capture": 3})["capture"] == 3


def test_half_lives_json_setting_applies():
    assert decay.half_lives(configured='{"task": 30}')["task"] == 30


def test_half_lives_junk_and_non_positive_values_are_ignored():
    assert decay.half_lives({"capture": 0, "task": "x", "decision": -1}) == decay.DEFAULT_HALF_LIVES


def test_half_lives_broken_json_falls_back():
    assert decay.half_lives(configured="{nope") == decay.DEFAULT_HALF_LIVES


def test_iso_week_plain():
    assert decay.iso_week("2026-10-08") == "2026-W41"


@pytest.mark.parametrize("day, week", [("2027-01-01", "2026-W53"), ("2024-12-30", "2025-W01")])
def test_iso_week_uses_the_iso_year_at_the_boundary(day, week):
    assert decay.iso_week(day) == week


# ------------------------------------------------- the brain: hook, command, recall
class Brain:
    def __init__(self, home: Path) -> None:
        self.home = home
        env = dict(os.environ)
        env.update(SECOND_BRAIN_HOME=str(home), MEMORY_LEDGER_HOME=str(home),
                   SECOND_BRAIN_NO_AUTOSYNC="1",
                   # a brain from scratch is seeded with the built-in skills; these
                   # checks count nodes in an empty one
                   SECOND_BRAIN_NO_SKILL_SEED="1")
        self.env = env

    def g(self, *args: str) -> dict:
        p = subprocess.run([sys.executable, str(GRAPH_PY), *args], capture_output=True, text=True,
                           encoding=UTF8, errors="replace", env=self.env)
        try:
            return json.loads(p.stdout)
        except ValueError:
            return {"ok": False, "raw": p.stdout[:300], "stderr": p.stderr[:300]}

    def prompt(self, sid: str, text: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CAPTURE_HOOK)],
            input=json.dumps({"session_id": sid, "cwd": "/tmp", "prompt": text}),
            capture_output=True, text=True, encoding=UTF8, errors="replace", env={**self.env, **env})

    def db(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.home / "graph.db"))
        con.row_factory = sqlite3.Row
        return con

    def scalar(self, sql: str, *params):
        con = self.db()
        try:
            return con.execute(sql, params).fetchone()[0]
        finally:
            con.close()

    def seed(self, nid, ntype, title, age_days, props=None, status="", body="", realm="work") -> None:
        ts = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat(timespec="seconds")
        con = self.db()
        con.execute(
            "INSERT INTO nodes(id,type,title,body,props,status,due,realm,created,updated)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (nid, ntype, title, body or title, json.dumps(props or {}), status, "", realm, ts, ts))
        con.commit()
        con.close()


@pytest.fixture(scope="module")
def brain(tmp_path_factory) -> Brain:
    b = Brain(tmp_path_factory.mktemp("noise"))
    b.g("init")
    return b


# ---------------------------------------------------------------- hook: drop and count
class TestHookFilter:
    @pytest.fixture(scope="class")
    def fed(self, brain: Brain) -> list[subprocess.CompletedProcess]:
        noisy = ("continue", "ok", "/clear", "<system-reminder>x</system-reminder>",
                 "[Image #3]", "the second one")
        results = [brain.prompt("s-noise", t) for t in noisy]
        brain.prompt("s-noise", REAL_PROMPT)
        brain.prompt("s-noise", "TypeError: x is not subscriptable")
        return results

    def test_the_hook_is_silent_on_noise(self, fed):
        for r in fed:
            assert r.returncode == 0 and r.stdout.strip() == "", r.stderr[-120:]

    def test_only_the_two_real_prompts_were_stored(self, brain, fed):
        caps = brain.db().execute("SELECT title FROM nodes WHERE type='capture'").fetchall()
        assert len(caps) == 2, [c[0][:20] for c in caps]

    def test_dropped_count_and_per_rule_reasons_are_on_the_conversation_node(self, brain, fed):
        conv = brain.db().execute("SELECT props FROM nodes WHERE type='conversation'").fetchone()
        p = json.loads(conv["props"])
        assert p.get("noise_dropped") == 6, p
        assert p.get("noise_reasons", {}).get("ack") == 2
        assert p["noise_reasons"].get("slash-command") == 1

    def test_a_to_do_cue_is_never_swallowed(self, brain, fed):
        brain.prompt("s-noise", "remind me to ok")
        assert brain.scalar("SELECT COUNT(*) FROM nodes WHERE type='task'") == 1

    @pytest.fixture(scope="class")
    def cold(self, brain: Brain, fed) -> None:
        brain.prompt("s-cold", "continue", **{FILTER_VAR: "cold"})

    def test_cold_mode_stores_the_raw_prompt_in_realm_cold_and_says_why(self, brain, cold):
        row = brain.db().execute("SELECT realm, props FROM nodes WHERE type='capture' "
                                 "AND title='continue'").fetchone()
        assert row and row["realm"] == "cold"
        assert json.loads(row["props"]).get("noise_reason") == "ack"

    def test_the_cold_count_is_on_the_conversation_node(self, brain, cold):
        conv = brain.db().execute(
            "SELECT props FROM nodes WHERE id LIKE '%s-cold%' OR "
            "json_extract(props,'$.session_id')='s-cold' AND type='conversation'").fetchone()
        assert conv and json.loads(conv["props"]).get("noise_cold") == 1

    def test_filter_off_keeps_the_legacy_behaviour(self, brain, fed):
        brain.prompt("s-off", "ok sounds good", **{FILTER_VAR: "off"})
        assert brain.scalar("SELECT COUNT(*) FROM nodes WHERE title='ok sounds good'") == 1


# ------------------------------------------------------------------ decay command
class TestDecayCommand:
    @pytest.fixture(scope="class")
    def dry(self, brain: Brain) -> SimpleNamespace:
        week_old = 9
        user = {"role": "user", "session_id": "d"}
        brain.seed("cap_old1", "capture", "refactor the recall path", week_old, user)
        brain.seed("cap_old2", "capture", "refactor the recall path", week_old, user)
        brain.seed("cap_old3", "capture", "Sure, here is the plan", week_old,
                   {"role": "assistant", "session_id": "d"})
        brain.seed("cap_new", "capture", "brand new prompt", 1, {"role": "user"})
        auto = {"source": "auto-capture"}
        brain.seed("t_raw", "task", RAW_TASK, 20, auto, status="open")
        brain.seed("t_trunc", "task", TRUNCATED_TASK, 20, auto, status="open")
        brain.seed("t_real", "task", REAL_TASK, 20, auto, status="open")
        brain.seed("t_young", "task", "Can we ship it?", 3, auto, status="open")
        # prompt-shaped, but written by a person or a skill, not the capture hook
        brain.seed("t_human", "task", "Check if CI passes on OS-1?", 20, status="open")
        brain.seed("t_done", "task", "Why is the sky blue?", 30, status="done")
        before = brain.scalar("SELECT COUNT(*) FROM nodes")
        report = brain.g("decay")
        return SimpleNamespace(report=report, before=before)

    def test_it_is_a_dry_run_by_default(self, dry):
        assert dry.report.get("dry_run") is True and dry.report.get("ok"), str(dry.report)[:120]

    def test_dry_run_expires_three_captures(self, dry):
        assert dry.report["expire"]["captures"] == 3, dry.report["expire"]

    def test_dry_run_flags_the_two_raw_prompt_tasks_only(self, dry):
        stale = dry.report["stale_tasks"]
        assert stale["flagged"] == 2
        assert {t["id"] for t in stale["shown"]} == {"t_raw", "t_trunc"}

    def test_dry_run_wrote_nothing_and_left_tasks_open(self, brain, dry):
        assert brain.scalar("SELECT COUNT(*) FROM nodes") == dry.before
        assert brain.scalar("SELECT COUNT(*) FROM nodes WHERE status='open' AND type='task'") >= 5

    def test_the_report_carries_no_private_underscore_keys(self, dry):
        assert not any(k.startswith("_") for k in dry.report)

    @pytest.fixture(scope="class")
    def applied(self, brain: Brain, dry: SimpleNamespace) -> SimpleNamespace:
        report = brain.g("decay", "--apply", "--no-backup")
        con = brain.db()
        digests = con.execute("SELECT id,body,props FROM nodes WHERE type='note' "
                              "AND id LIKE 'note_week_%'").fetchall()
        states = {r[0]: r[1] for r in con.execute("SELECT id,status FROM nodes WHERE type='task'")}
        nodes_after = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        return SimpleNamespace(report=report, digests=digests, con=con, states=states,
                               nodes_after=nodes_after, before=dry.before)

    def test_apply_is_ok_and_expires_the_captures(self, applied):
        assert applied.report.get("ok") and applied.report["applied"]["captures_expired"] == 3, \
            str(applied.report.get("applied"))

    def test_one_weekly_digest_note(self, applied):
        assert len(applied.digests) == 1, [d["id"] for d in applied.digests]

    def test_the_digest_has_compressed_de_duplicated_bullets(self, applied):
        body = applied.digests[0]["body"]
        assert body.count("- refactor the recall path") == 1 and "Sure, here" not in body, body

    def test_captures_are_kept_linked_part_of_the_digest(self, applied):
        n = applied.con.execute("SELECT COUNT(*) FROM edges WHERE dst=? AND rel='part-of' "
                                "AND src LIKE 'cap_old%'", (applied.digests[0]["id"],)).fetchone()[0]
        assert n == 3

    def test_expired_captures_are_stamped(self, applied):
        props = json.loads(applied.con.execute("SELECT props FROM nodes WHERE id='cap_old1'").fetchone()[0])
        assert props.get("expired_into") == applied.digests[0]["id"]

    def test_a_young_capture_is_untouched(self, applied):
        props = json.loads(applied.con.execute("SELECT props FROM nodes WHERE id='cap_new'").fetchone()[0])
        assert "expired_at" not in props

    def test_raw_prompt_tasks_are_closed_as_dropped(self, applied):
        assert applied.states["t_raw"] == "dropped" and applied.states["t_trunc"] == "dropped"

    def test_real_young_and_done_tasks_are_untouched(self, applied):
        s = applied.states
        assert (s["t_real"], s["t_young"], s["t_done"]) == ("open", "open", "done")

    def test_a_task_not_spun_up_by_the_capture_hook_is_never_dropped(self, applied):
        assert applied.states["t_human"] == "open"

    def test_nothing_was_deleted(self, applied):
        assert applied.nodes_after == applied.before + 1

    def test_a_second_apply_is_idempotent(self, brain, applied):
        r = brain.g("decay", "--apply", "--no-backup")
        assert r["applied"]["captures_expired"] == 0 and r["applied"]["tasks_dropped"] == 0, r.get("applied")
        assert brain.scalar("SELECT COUNT(*) FROM nodes") == applied.nodes_after

    def test_list_hides_dropped_tasks(self, brain, applied):
        listed = brain.g("list", "--type", "task")
        ids = {n["id"] for n in listed.get("nodes", listed.get("results", []))}
        assert "t_raw" not in ids and "t_real" in ids, sorted(ids)[:20]


# ------------------------------------------------ recall applies the decay weight
def test_a_sixty_day_decision_outranks_a_sixty_day_bugfix(brain):
    brain.seed("dec_old", "decision", "quokka pipeline convention", 60)
    brain.seed("bug_old", "bugfix", "quokka pipeline convention", 60, {"kind": "bugfix"})
    r = brain.g("recall", "quokka pipeline convention", "--limit", "5")
    rows = r.get("results") or r.get("nodes") or []
    score = {x["id"]: x.get("score") for x in rows}
    assert "dec_old" in score and "bug_old" in score, score
    # 90 d vs 14 d half-life
    assert score["dec_old"] > score["bug_old"], score
