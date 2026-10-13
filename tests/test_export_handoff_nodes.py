"""scripts/export_handoff_nodes.py: what leaves the brain, and that it round-trips into import_nodes.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import graph
from conftest import SCRIPTS, UTF8

EXPORTER = SCRIPTS / "export_handoff_nodes.py"
IMPORTER = SCRIPTS / "import_nodes.py"


def run(script: Path, home: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(script), *args], capture_output=True,
                          text=True, encoding=UTF8, check=False,
                          env={"SECOND_BRAIN_HOME": str(home), "SECOND_BRAIN_NO_AUTOSYNC": "1",
                               "PATH": "/usr/bin:/bin"})


def seed(home: Path) -> None:
    run(SCRIPTS / "graph.py", home, "init")
    con = graph.connect()
    rows = [("task_mine", "task", "Mine", "open", {"assignee": "gabriel"}, "work"),
            ("task_other", "task", "Theirs", "open", {"assignee": "ana"}, "work"),
            ("task_done", "task", "Done", "done", {"assignee": "gabriel"}, "work"),
            ("task_therapy", "task", "Marcar terapia", "open", {"assignee": "gabriel"}, "work"),
            ("pr_x", "pr", "PR X", "open", {"repo": "o/r"}, "work"),
            ("note_priv", "pr", "Private", "open", {}, "personal"),
            ("msg_1", "message", "chat", "", {}, "work")]
    for nid, typ, title, status, props, realm in rows:
        con.execute("insert into nodes(id,type,title,body,status,props,realm,created,updated) "
                    "values(?,?,?,?,?,?,?,'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')",
                    (nid, typ, title, "", status, json.dumps(props), realm))
    con.execute("insert into edges(src,dst,rel,props,created) values('task_mine','pr_x','relates-to','{}','2026-01-01')")
    con.execute("insert into edges(src,dst,rel,props,created) values('task_mine','task_other','relates-to','{}','2026-01-01')")
    con.commit()


def test_help_exits_clean_and_writes_nothing(brain_home: Path) -> None:
    before = set(brain_home.rglob("*"))
    done = run(EXPORTER, brain_home, "--help")
    assert done.returncode == 0 and "--assignee" in done.stdout
    assert set(brain_home.rglob("*")) == before


def test_selection_and_round_trip(brain_home: Path, tmp_path: Path) -> None:
    seed(brain_home)
    out = tmp_path / "n.json"
    done = run(EXPORTER, brain_home, str(out), "--owner", "Gabriel", "--for", "Isa")
    assert done.returncode == 0, done.stderr
    bundle = json.loads(out.read_text(encoding=UTF8))
    assert bundle["format"] == "second-brain-nodes/1" and bundle["for"] == "Isa"
    assert {n["id"] for n in bundle["nodes"]} == {"task_mine", "pr_x"}
    assert [(e["src"], e["dst"]) for e in bundle["edges"]] == [("task_mine", "pr_x")]
    target = tmp_path / "target"
    target.mkdir()
    run(SCRIPTS / "graph.py", target, "init")
    applied = json.loads(run(IMPORTER, target, str(out), "--apply").stdout)
    assert (applied["added"], applied["edges_added"]) == (2, 1)
