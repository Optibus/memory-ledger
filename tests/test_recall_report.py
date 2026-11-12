"""`graph.py recall-report`: how often injected context shows up in the answer.

    python -m pytest tests/test_recall_report.py

Runs the real hooks against a THROWAWAY brain. Two prompts get the same node
injected; the answer to one cites it as `[n:<id>]`, the other does not.
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
PROMPT_HOOK = ROOT / "hooks" / "capture_prompt.py"
ANSWER_HOOK = ROOT / "hooks" / "capture_answer.py"
UTF8 = "utf-8"
ASK = "what projection does the shapefile export use?"


class Brain:
    def __init__(self, home: Path) -> None:
        self.home = home
        env = {k: v for k, v in os.environ.items() if not k.startswith("MEMORY_LEDGER_NO_")}
        env.update(SECOND_BRAIN_HOME=str(home), SECOND_BRAIN_NO_AUTOSYNC="1",
                   MEMORY_LEDGER_NO_CONSOLIDATE="1")
        self.env = env

    def run(self, script: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(script), *args], input=stdin, capture_output=True,
                              text=True, encoding=UTF8, errors="replace", env=self.env, check=True)

    def g(self, *args: str) -> dict:
        return json.loads(self.run(GRAPH_PY, *args).stdout)

    def turn(self, session: str, answer: str) -> None:
        self.run(PROMPT_HOOK, stdin=json.dumps({"session_id": session, "cwd": "/tmp", "prompt": ASK}))
        self.run(ANSWER_HOOK, stdin=json.dumps({"session_id": session, "cwd": "/tmp",
                                                "last_assistant_message": answer}))


@pytest.fixture(scope="module")
def seeded(tmp_path_factory) -> tuple[Brain, str]:
    brain = Brain(tmp_path_factory.mktemp("recall-report"))
    (brain.home / "settings.json").write_text(json.dumps({"prompt_recall": True}), encoding=UTF8)
    node = brain.g("add-node", "--type", "decision",
                   "--title", "Shapefile export uses EPSG 4326 projection",
                   "--body", "Decided with the team: shapefile exports keep WGS84 coordinates. " * 10)
    node_id = node["node"]["id"]
    brain.turn("used", f"The export keeps WGS84, as decided earlier [n:{node_id}].")
    brain.turn("unused", "It uses a geographic coordinate system.")
    return brain, node_id


def test_counts_and_rates(seeded):
    brain, node_id = seeded
    rep = brain.g("recall-report")
    assert rep["ok"] and rep["days"] == 14
    assert (rep["prompts"], rep["injected"], rep["used"]) == (2, 2, 1)
    assert rep["rate_injected"] == 1.0 and rep["rate_used"] == 0.5
    assert rep["top_nodes"] == [{"id": node_id, "injected": 2, "used": 1}]


def test_a_bare_id_counts_as_used(seeded):
    brain, node_id = seeded
    brain.turn("bare", f"See {node_id} for the projection.")
    rep = brain.g("recall-report")
    assert (rep["injected"], rep["used"]) == (3, 2)


def test_days_narrows_the_window(seeded):
    brain, _ = seeded
    con = sqlite3.connect(next(brain.home.glob("*.db")))
    con.execute("UPDATE nodes SET created='2020-01-01T00:00:00+00:00'")
    con.commit()
    con.close()
    rep = brain.g("recall-report", "--days", "14")
    assert (rep["prompts"], rep["injected"], rep["used"], rep["rate_used"]) == (0, 0, 0, 0.0)
    assert rep["top_nodes"] == []
