"""Session summary notes, the repo-scoped SessionStart brief, and recall that reads them.

    python -m pytest tests/test_session_summary.py

Runs the real hooks against a THROWAWAY brain. Fixtures are synthetic: a fake
repo `acme/widgets`, a fake ticket `ABC-123`, a fake PR `acme/widgets#7`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GRAPH_PY = ROOT / "scripts" / "graph.py"
HOOKS = ROOT / "hooks"
UTF8 = "utf-8"

PROMPTS = [
    "please investigate the widget exporter crash tracked in ABC-123",
    "now repair the widget exporter and open the change acme/widgets#7",
    "finally write the changelog entry for the exporter",
]
ANSWERS = [
    "Found the crash in the exporter; the projection step returned empty rows for ABC-123.",
    "Repaired the exporter and opened acme/widgets#7 for review.",
    "Changelog written. The export work stops at review of the projection change.",
]


class Brain:
    def __init__(self, home: Path) -> None:
        self.home = home
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("MEMORY_LEDGER_NO_") and k != "MEMORY_LEDGER_REQUEST_ID"}
        env.update(SECOND_BRAIN_HOME=str(home), SECOND_BRAIN_NO_AUTOSYNC="1",
                   MEMORY_LEDGER_NO_CONSOLIDATE="1")
        env.pop("AQ_QUEST_ID", None)
        self.env = env

    def run(self, script: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(script), *args], input=stdin, capture_output=True,
                              text=True, encoding=UTF8, errors="replace", env=self.env)

    def g(self, *args: str) -> dict:
        return json.loads(self.run(GRAPH_PY, *args).stdout)

    def hook(self, name: str, payload: dict, *args: str) -> subprocess.CompletedProcess:
        return self.run(HOOKS / name, *args, stdin=json.dumps(payload))

    def ask(self, sid: str, cwd: str, prompt: str):
        p = self.hook("capture_prompt.py", {"session_id": sid, "cwd": cwd, "prompt": prompt})
        return json.loads(p.stdout) if p.stdout.strip() else None

    def answer(self, sid: str, cwd: str, text: str) -> None:
        self.hook("capture_answer.py", {"session_id": sid, "cwd": cwd, "last_assistant_message": text})

    def session(self, sid: str, cwd: str, prompts=PROMPTS, answers=ANSWERS) -> None:
        for q, a in zip(prompts, answers):
            self.ask(sid, cwd, q)
            self.answer(sid, cwd, a)

    def close(self, phase: str, sid: str, cwd: str) -> None:
        self.hook("close_conversation.py", {"session_id": sid, "cwd": cwd}, phase)

    def brief(self, cwd: str, sid: str) -> str:
        p = self.hook("open_loops_brief.py", {"session_id": sid, "cwd": cwd})
        return ((json.loads(p.stdout).get("hookSpecificOutput") or {}).get("additionalContext", "")
                if p.stdout.strip() else "")

    def rows(self, sql: str, *args):
        con = sqlite3.connect(str(self.home / "graph.db"))
        con.row_factory = sqlite3.Row
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def summaries(self) -> list[sqlite3.Row]:
        return self.rows("SELECT * FROM nodes WHERE id LIKE 'note_session_%'")


def git_repo(path: Path, remote: str) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
    return str(path)


@pytest.fixture(scope="module")
def brain(tmp_path_factory) -> Brain:
    return Brain(tmp_path_factory.mktemp("sessions"))


@pytest.fixture(scope="module")
def acme(tmp_path_factory) -> str:
    return git_repo(tmp_path_factory.mktemp("acme"), "git@github.com:acme/widgets.git")


@pytest.fixture(scope="module")
def other(tmp_path_factory) -> str:
    return git_repo(tmp_path_factory.mktemp("other"), "https://github.com/acme/gadgets.git")


def context(out) -> str:
    return ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")


# ------------------------------------------------------------------ the summary note
class TestSummaryNote:
    def test_end_writes_one_summary(self, brain, acme):
        brain.session("aaaa1111-first", acme)
        brain.close("end", "aaaa1111-first", acme)
        rows = brain.summaries()
        assert [r["id"] for r in rows] == ["note_session_aaaa1111"]
        r = rows[0]
        props = json.loads(r["props"])
        assert r["type"] == "note" and props["kind"] == "session-summary"
        assert props["repo"] == "acme/widgets" and props["session_id"] == "aaaa1111-first"
        assert r["title"].startswith("Session — acme/widgets — ")
        for want in ("Asked:", "Outcome:", "Refs:", "Turns: 3 / 3"):
            assert want in r["body"], r["body"]
        refs = next(line for line in r["body"].splitlines() if line.startswith("Refs:"))
        assert "ABC-123" in refs and "acme/widgets#7" in refs
        assert len(r["body"]) <= 900

    def test_it_hangs_off_the_conversation(self, brain):
        edge = brain.rows("SELECT dst, rel FROM edges WHERE src='note_session_aaaa1111'")
        assert len(edge) == 1 and edge[0]["rel"] == "part-of"
        dst = brain.rows("SELECT type FROM nodes WHERE id=?", edge[0]["dst"])
        assert dst and dst[0]["type"] == "conversation"

    def test_compact_then_end_upserts(self, brain, acme):
        sid = "bbbb2222-second"
        brain.session(sid, acme, PROMPTS[:2], ANSWERS[:2])
        brain.close("compact", sid, acme)
        first = json.loads(brain.rows("SELECT props FROM nodes WHERE id='note_session_bbbb2222'")[0]["props"])
        assert first["turns"] == 2
        brain.session(sid, acme, PROMPTS[2:], ANSWERS[2:])
        brain.close("end", sid, acme)
        rows = brain.rows("SELECT props FROM nodes WHERE id LIKE 'note_session_bbbb2222'")
        assert len(rows) == 1 and json.loads(rows[0]["props"])["turns"] == 3

    def test_a_one_prompt_session_writes_nothing(self, brain, acme):
        brain.session("cccc3333-short", acme, PROMPTS[:1], ANSWERS[:1])
        brain.close("end", "cccc3333-short", acme)
        assert not brain.rows("SELECT id FROM nodes WHERE id='note_session_cccc3333'")

    def test_a_subagent_hand_back_is_not_a_session(self, brain, acme):
        brain.session("dddd4444-agent", acme,
                      ["<agent-message> first part of a hand back report", "second part of the hand back report"],
                      ANSWERS[:2])
        brain.close("end", "dddd4444-agent", acme)
        assert not brain.rows("SELECT id FROM nodes WHERE id='note_session_dddd4444'")


# ----------------------------------------------------------------------- the brief
class TestBrief:
    def test_lists_the_repos_recent_sessions(self, brain, acme):
        ctx = brain.brief(acme, "new-session")
        assert "Recent sessions in acme/widgets" in ctx, ctx[-400:]
        assert "[n:note_session_aaaa1111]" in ctx
        block = ctx[ctx.index("Recent sessions in"):]
        assert len(block) <= 700 + 200  # the block is capped; the trailing hint line is separate

    def test_another_repo_shows_nothing(self, brain, other):
        assert "Recent sessions in" not in brain.brief(other, "new-session")

    def test_the_current_sessions_own_summary_is_excluded(self, brain, acme):
        ctx = brain.brief(acme, "aaaa1111-first")
        assert "[n:note_session_aaaa1111]" not in ctx
        assert "[n:note_session_bbbb2222]" in ctx


# ----------------------------------------------------------------------- the recall
class TestRecall:
    @pytest.fixture(scope="class", autouse=True)
    def seeded(self, brain, acme):
        (brain.home / "settings.json").write_text(json.dumps({"prompt_recall": True}), encoding=UTF8)
        brain.g("add-node", "--type", "decision", "--title", "Widget export uses UTM projection",
                "--body", "Decided: the widget export keeps UTM coordinates for every region.")
        brain.g("add-node", "--type", "task", "--title", "Tidy the agent work notes",
                "--body", "make the agent work in a cleaner way")

    def test_a_decision_is_found_by_its_words(self, brain, acme):
        ctx = context(brain.ask("r1", acme, "what did we decide about the widget export"))
        assert "Widget export uses UTM projection" in ctx, ctx

    def test_a_ticket_finds_the_session_that_names_it(self, brain, acme):
        ctx = context(brain.ask("r2", acme, "continue the ABC-123 work"))
        assert "Session — acme/widgets" in ctx and "note_session_" in ctx, ctx

    def test_a_repo_name_finds_its_latest_session(self, brain, acme):
        ctx = context(brain.ask("r3", acme, "pick up acme/widgets where we left"))
        assert "Session — acme/widgets" in ctx, ctx

    def test_a_snippet_for_a_summary_keeps_the_outcome(self, brain, acme):
        ctx = context(brain.ask("r3b", acme, "pick up acme/widgets where we left"))
        assert "Outcome:" in ctx, ctx

    @pytest.mark.parametrize("sid, prompt", [
        ("r4", "ok"),
        ("r5", "make the agent work"),
        ("r6", "please check the agent session"),
    ], ids=["bare-ack", "only-generic-words", "generic-words-again"])
    def test_these_inject_nothing(self, brain, acme, sid, prompt):
        assert brain.ask(sid, acme, prompt) is None
