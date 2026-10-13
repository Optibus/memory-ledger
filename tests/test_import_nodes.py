"""scripts/import_nodes.py: a second-brain-nodes/1 file lands in a throwaway brain."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import SCRIPTS, UTF8

IMPORTER = SCRIPTS / "import_nodes.py"
BUNDLE = {
    "format": "second-brain-nodes/1", "owner": "Gabriel Nori",
    "nodes": [
        {"id": "task_a", "type": "task", "title": "A", "body": "b", "status": "open",
         "created": "2026-01-01T00:00:00+00:00", "props": {"assignee": "gabriel"}},
        {"id": "pr_b", "type": "pr", "title": "B", "props": {}},
    ],
    "edges": [{"src": "task_a", "dst": "pr_b", "rel": "relates-to", "props": {}},
              {"src": "task_a", "dst": "gone", "rel": "relates-to", "props": {}}],
}


def run(home: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(IMPORTER), *args], capture_output=True,
                          text=True, encoding=UTF8, check=False,
                          env={"SECOND_BRAIN_HOME": str(home), "SECOND_BRAIN_NO_AUTOSYNC": "1",
                               "PATH": "/usr/bin:/bin"})


def test_dry_run_apply_and_rerun(brain_home: Path, tmp_path: Path) -> None:
    f = tmp_path / "n.json"
    f.write_text(json.dumps(BUNDLE), encoding=UTF8)
    dry = json.loads(run(brain_home, str(f)).stdout)
    assert dry["added"] == 2 and not dry["applied"]
    assert json.loads(run(brain_home, str(f)).stdout)["added"] == 2  # nothing was written
    done = json.loads(run(brain_home, str(f), "--apply").stdout)
    assert (done["added"], done["edges_added"], done["edges_dangling"]) == (2, 1, 1)
    again = json.loads(run(brain_home, str(f), "--apply").stdout)
    assert (again["added"], again["skipped_existing"], again["edges_existing"]) == (0, 2, 1)


def test_refuses_other_format(brain_home: Path, tmp_path: Path) -> None:
    f = tmp_path / "n.json"
    f.write_text('{"format": "x"}', encoding=UTF8)
    assert run(brain_home, str(f)).returncode != 0
