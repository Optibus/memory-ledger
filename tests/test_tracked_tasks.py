"""External task boards (CONVENTIONS §2c): `track`, `untrack`, the list filters,
`open-loops` and the session brief.

    python -m pytest tests/test_tracked_tasks.py

Runs the real engine against a THROWAWAY brain. What it guards: a task another
app has claimed leaves the open-loops brief and is counted in one line instead;
the board can pull what is still unclaimed and list what it holds; releasing the
task puts it back; the date views still show it, so a due date the board wrote
back is not lost.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GRAPH_PY = ROOT / "scripts" / "graph.py"
QUERY_PY = ROOT / "scripts" / "brain_query.py"
BRIEF_HOOK = ROOT / "hooks" / "open_loops_brief.py"
UTF8 = "utf-8"
BOARD = "kanban"


def _graph(env: dict, *args: str) -> dict:
    proc = subprocess.run([sys.executable, str(GRAPH_PY), *args], capture_output=True,
                          text=True, encoding=UTF8, errors="replace", env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _add_task(env: dict, title: str, **extra: str) -> str:
    args = ["add-node", "--type", "task", "--title", title, "--status", "open"]
    for k, v in extra.items():
        args += [f"--{k}", v]
    res = _graph(env, *args)
    return res.get("id") or res["node"]["id"]


@pytest.fixture
def env(brain_home, brain_env, monkeypatch):
    monkeypatch.setenv("MEMORY_LEDGER_NO_CONSOLIDATE", "1")
    e = brain_env(brain_home)
    e["MEMORY_LEDGER_NO_CONSOLIDATE"] = "1"
    _graph(e, "init")
    return e


class TestTrack:
    def test_track_claims_and_untrack_releases(self, env):
        tid = _add_task(env, "Send the recap document")
        res = _graph(env, "track", "--id", tid, "--system", BOARD, "--ref", "card-1",
                     "--url", "http://board/card-1")
        claim = res["node"]["props"]["tracked_by"]
        assert claim["system"] == BOARD and claim["ref"] == "card-1"
        assert claim["url"] == "http://board/card-1" and claim["since"]
        res = _graph(env, "untrack", "--id", tid)
        assert "tracked_by" not in res["node"]["props"]

    def test_the_same_claim_keeps_its_since(self, env):
        tid = _add_task(env, "Send the recap document")
        first = _graph(env, "track", "--id", tid, "--system", BOARD, "--ref", "c")
        again = _graph(env, "track", "--id", tid, "--system", BOARD, "--ref", "c")
        assert again["node"]["props"]["tracked_by"]["since"] == \
            first["node"]["props"]["tracked_by"]["since"]

    def test_a_missing_node_or_system_is_an_error(self, env):
        assert _graph(env, "track", "--id", "task_nope", "--system", BOARD,
                      "--ref", "c")["ok"] is False
        tid = _add_task(env, "x task")
        assert _graph(env, "track", "--id", tid, "--system", " ", "--ref", "c")["ok"] is False


    def test_untracking_a_never_tracked_node_changes_nothing(self, env):
        tid = _add_task(env, "Never claimed")
        before = _graph(env, "list", "--type", "task")["results"][0]["updated"]
        res = _graph(env, "untrack", "--id", tid)
        assert res["ok"] is True and "tracked_by" not in res["node"]["props"]
        assert res["node"]["updated"] == before

    def test_a_different_board_taking_over_resets_since(self, env):
        tid = _add_task(env, "Send the recap document")
        first = _graph(env, "track", "--id", tid, "--system", BOARD, "--ref", "c")
        since = first["node"]["props"]["tracked_by"]["since"]
        with_other = _graph(env, "track", "--id", tid, "--system", "another-board", "--ref", "x")
        claim = with_other["node"]["props"]["tracked_by"]
        assert claim["system"] == "another-board"
        assert claim["since"] >= since and claim["ref"] == "x"
        # same-second runs can tie, so also prove the claim was rebuilt, not copied
        assert set(claim) == {"system", "ref", "url", "since"}


MALFORMED = ['"just-a-string"', "{}", "null"]


class TestMalformedClaims:
    """Only a dict with a non-empty `system` is a claim; python and SQL agree."""

    @pytest.mark.parametrize("raw", MALFORMED)
    def test_a_malformed_claim_counts_as_untracked_everywhere(self, env, raw):
        tid = _add_task(env, "Odd claim")
        _graph(env, "update-node", "--id", tid, "--props", '{"tracked_by": %s}' % raw)
        free = _graph(env, "list", "--type", "task", "--status", "open", "--untracked")
        assert [r["id"] for r in free["results"]] == [tid]
        held = _graph(env, "list", "--type", "task", "--tracked-by", "just-a-string")
        assert held["results"] == []
        held = _graph(env, "list", "--type", "task", "--tracked-by", BOARD)
        assert held["results"] == []
        loops = _graph(env, "open-loops")
        assert [r["id"] for r in loops["results"]] == [tid]
        assert loops["tracked"] == {}


class TestListFilters:
    def test_untracked_and_tracked_by(self, env):
        a = _add_task(env, "Claimed task")
        b = _add_task(env, "Free task")
        _graph(env, "track", "--id", a, "--system", BOARD, "--ref", "card-a")
        free = _graph(env, "list", "--type", "task", "--status", "open", "--untracked")
        assert [r["id"] for r in free["results"]] == [b]
        held = _graph(env, "list", "--type", "task", "--tracked-by", BOARD)
        assert [r["id"] for r in held["results"]] == [a]
        other = _graph(env, "list", "--type", "task", "--tracked-by", "another-board")
        assert other["results"] == []


class TestOpenLoops:
    def test_a_tracked_task_leaves_the_loops_and_is_counted(self, env):
        a = _add_task(env, "Claimed task")
        b = _add_task(env, "Free task")
        _graph(env, "track", "--id", a, "--system", BOARD, "--ref", "card-a")
        loops = _graph(env, "open-loops")
        assert [r["id"] for r in loops["results"]] == [b]
        assert loops["count"] == 1
        assert loops["tracked"] == {BOARD: 1}
        back = _graph(env, "open-loops", "--include-tracked")
        assert {r["id"] for r in back["results"]} == {a, b}
        assert back["tracked"] == {}

    def test_the_date_views_keep_it(self, env):
        a = _add_task(env, "Claimed and dated", due="2099-01-01")
        _graph(env, "track", "--id", a, "--system", BOARD, "--ref", "card-a")
        proc = subprocess.run([sys.executable, str(QUERY_PY), "open"], capture_output=True,
                              text=True, encoding=UTF8, errors="replace", env=env)
        assert proc.returncode == 0, proc.stderr
        assert "Claimed and dated" in proc.stdout


class TestBrief:
    def _brief(self, env) -> str:
        proc = subprocess.run([sys.executable, str(BRIEF_HOOK)], input="{}",
                              capture_output=True, text=True, encoding=UTF8,
                              errors="replace", env=env)
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout or "{}")
        return out.get("additionalContext") or \
            (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""

    def test_tracked_tasks_are_one_line_not_a_list(self, env):
        a = _add_task(env, "Claimed task")
        b = _add_task(env, "Free task")
        _graph(env, "track", "--id", a, "--system", BOARD, "--ref", "card-a")
        text = self._brief(env)
        loops = text.split("In play when you left off")[0]   # the thread may name it
        assert "Free task" in loops
        assert "Claimed task" not in loops
        assert f"1 in {BOARD}" in loops

    def test_all_clear_still_names_the_board(self, env):
        a = _add_task(env, "Claimed task")
        _graph(env, "track", "--id", a, "--system", BOARD, "--ref", "card-a")
        text = self._brief(env)
        assert "no open loops" in text and f"1 in {BOARD}" in text
        assert "Tracked elsewhere" in text
