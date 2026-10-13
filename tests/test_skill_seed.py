"""A brain made from scratch carries the built-in skills, against THROWAWAY homes.

    python -m pytest tests/test_skill_seed.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NO_SEED_VAR = "SECOND_BRAIN_NO_SKILL_SEED"
MISSING_SKILL_ID = "skill_recall"


@pytest.fixture
def builtin() -> set[str]:
    return {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}


@pytest.fixture
def seeded(brain_home, run_script):
    """A fresh home after its first command, plus a `cmd(script, *args)` helper
    that returns the JSON a script prints."""
    def cmd(script: str, *args: str, home: Path = brain_home, **env: str | None):
        r = run_script(script, *args, home=home, env={NO_SEED_VAR: None, **env})
        return json.loads(r.stdout or "{}")
    cmd("graph.py", "stats")
    cmd.home = brain_home
    return cmd


def _drop_skill(home: Path) -> None:
    con = sqlite3.connect(home / "graph.db")
    try:
        con.execute("DELETE FROM nodes WHERE id=?", (MISSING_SKILL_ID,))
        con.commit()
    finally:
        con.close()


def test_the_first_command_on_a_fresh_home_seeds_every_builtin_skill(brain_home, run_script, builtin):
    r = run_script("graph.py", "stats", home=brain_home, env={NO_SEED_VAR: None})
    by_type = json.loads(r.stdout or "{}").get("by_type", {})
    assert by_type.get("skill") == len(builtin)


def test_an_existing_brain_is_not_reseeded_on_connect(seeded):
    db = seeded.home / "graph.db"
    first = db.stat().st_mtime_ns
    seeded("graph.py", "stats")
    assert db.stat().st_mtime_ns == first


def test_init_on_a_seeded_brain_is_a_no_op(seeded):
    assert seeded("graph.py", "init").get("skills", {}).get("ingested") == 0


def test_init_brings_back_a_builtin_that_is_missing(seeded):
    _drop_skill(seeded.home)
    skills = seeded("graph.py", "init").get("skills", {})
    assert skills.get("missing") == []
    assert skills.get("ingested") == 1


def test_doctor_brings_it_back_too_and_the_brain_is_healthy(seeded):
    _drop_skill(seeded.home)
    doc = seeded("doctor.py", "--json")
    assert (doc.get("skills") or {}).get("ingested") == 1
    assert doc.get("ok") is True, doc.get("problems")


def test_the_opt_out_leaves_a_fresh_brain_empty(tmp_path, run_script):
    off = tmp_path / "noseed"
    off.mkdir()
    r = run_script("graph.py", "stats", home=off, env={NO_SEED_VAR: "1"})
    assert not json.loads(r.stdout or "{}").get("by_type", {}).get("skill")
