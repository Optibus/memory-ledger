"""The SessionStart brief stays small however many loops are open.

    python -m pytest tests/test_brief_cap.py

Runs the real hook against a THROWAWAY brain seeded with synthetic stale tasks.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH_PY = ROOT / "scripts" / "graph.py"
BRIEF_HOOK = ROOT / "hooks" / "open_loops_brief.py"
UTF8 = "utf-8"
BRIEF_MAX_CHARS = 1500
OLD = "2020-01-01T00:00:00+00:00"


def _env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEMORY_LEDGER_NO_")}
    env.update(SECOND_BRAIN_HOME=str(home), SECOND_BRAIN_NO_AUTOSYNC="1",
               MEMORY_LEDGER_NO_CONSOLIDATE="1")
    return env


def _run(script: Path, home: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                          encoding=UTF8, errors="replace", env=_env(home))


def _seed_stale_tasks(home: Path, count: int) -> None:
    for i in range(count):
        p = _run(GRAPH_PY, home, "add-node", "--type", "task",
                 "--title", f"Synthetic stale loop number {i} with a fairly long title to take space")
        assert json.loads(p.stdout).get("ok"), p.stdout[:200]
    db = next(home.glob("*.db"))
    con = sqlite3.connect(db)
    con.execute("UPDATE nodes SET created=?, updated=? WHERE type='task'", (OLD, OLD))
    con.commit()
    con.close()


def _context(home: Path) -> str:
    p = _run(BRIEF_HOOK, home)
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


def test_twenty_stale_loops_stay_under_the_cap_and_say_how_many_are_hidden(tmp_path):
    _seed_stale_tasks(tmp_path, 20)
    ctx = _context(tmp_path)
    assert len(ctx) <= BRIEF_MAX_CHARS, len(ctx)
    assert "stale loops hidden; open-loops lists them)" in ctx
    assert ctx.count("Synthetic stale loop") == 3
