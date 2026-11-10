"""Agent Quest quest sessions are not captured (the feedback-loop guard).

    python -m pytest tests/test_quest_capture.py

Agent Quest's nightly Biographer grooms the ledger; if the hooks stored the
quests it runs, it would groom its own residue. The signal is `AQ_QUEST_ID`,
which Agent Quest sets (non-empty) on every quest spawn. Runs the real hooks
against a throwaway brain. The `skip_quest_capture` setting (default on) and
`$MEMORY_LEDGER_NO_SKIP_QUEST_CAPTURE` turn the guard off.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import HOME_VAR, NO_AUTOSYNC_VAR, ROOT, UTF8

HOOKS = ROOT / "hooks"
QUEST_VAR = "AQ_QUEST_ID"
OFF_VAR = "MEMORY_LEDGER_NO_SKIP_QUEST_CAPTURE"
PROMPT = "please refactor the shapefile exporter and add tests"
ANSWER = "I refactored the exporter and added three tests for the projection."


def run_hook(home: Path, script: str, payload: dict, **env: str) -> None:
    base = {k: v for k, v in os.environ.items()
            if not k.startswith("AQ_") and k != OFF_VAR}
    base.update({HOME_VAR: str(home), NO_AUTOSYNC_VAR: "1",
                 "MEMORY_LEDGER_NO_CONSOLIDATE": "1"}, **env)
    subprocess.run([sys.executable, str(HOOKS / script)], input=json.dumps(payload),
                   capture_output=True, text=True, encoding=UTF8, errors="replace",
                   env=base, check=True)


def captures(home: Path) -> int:
    db = home / "graph.db"
    if not db.exists():
        return 0
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT COUNT(*) FROM nodes WHERE type='capture'").fetchone()[0]
    finally:
        con.close()


def turn(home: Path, sid: str, **env: str) -> int:
    run_hook(home, "capture_prompt.py", {"session_id": sid, "cwd": "/tmp", "prompt": PROMPT}, **env)
    run_hook(home, "capture_answer.py",
             {"session_id": sid, "cwd": "/tmp", "last_assistant_message": ANSWER}, **env)
    return captures(home)


def test_an_ordinary_session_is_captured(brain_home):
    assert turn(brain_home, "s-plain") == 2


def test_a_quest_session_is_not_captured_prompt_or_answer(brain_home):
    assert turn(brain_home, "s-quest", **{QUEST_VAR: "q-123"}) == 0


def test_an_empty_quest_id_is_not_a_quest(brain_home):
    # Agent Quest exports AQ_QUEST_ID="" for non-quest sessions.
    assert turn(brain_home, "s-empty", **{QUEST_VAR: ""}) == 2


def test_the_env_switch_turns_the_guard_off(brain_home):
    assert turn(brain_home, "s-env", **{QUEST_VAR: "q-1", OFF_VAR: "1"}) == 2


def test_the_setting_turns_the_guard_off(brain_home):
    (brain_home / "settings.json").write_text(json.dumps({"skip_quest_capture": False}), encoding=UTF8)
    assert turn(brain_home, "s-set", **{QUEST_VAR: "q-1"}) == 2


@pytest.mark.parametrize("script", ["capture_prompt.py", "capture_answer.py"])
def test_a_quest_hook_leaves_no_conversation_behind(brain_home, script):
    run_hook(brain_home, script, {"session_id": "s-q2", "cwd": "/tmp", "prompt": PROMPT,
                                  "last_assistant_message": ANSWER}, **{QUEST_VAR: "q-9"})
    db = brain_home / "graph.db"
    if db.exists():
        con = sqlite3.connect(str(db))
        n = con.execute("SELECT COUNT(*) FROM nodes WHERE type IN ('capture','conversation','agent')").fetchone()[0]
        con.close()
        assert n == 0
