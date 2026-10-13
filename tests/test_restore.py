"""Backup and restore round trip: `bundle.py --with-brain` -> `restore.py`.

    python -m pytest tests/test_restore.py

Every brain here is a THROWAWAY home; the scripts run as subprocesses, exactly
as a person runs them, so each sees only its own env. The classes build on a
module-wide backup of one brain, and the tests inside a class run in file order.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
UTF8 = "utf-8"
TIMEOUT_S = 300
ORIGINAL_OWNER = {"owner": "Original"}
EDITED_OWNER = "Edited on b"
KEPT_NODE = "note_kept"
ONLY_B_NODE = "note_only_b"
LEFTOVER_GLOB = "memory-ledger-restore-*"
EXEC_BIT = pytest.mark.skipif(os.name == "nt", reason="Windows has no exec bit")


def run(home: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SECOND_BRAIN_", "MEMORY_LEDGER_"))}
    env.update(SECOND_BRAIN_HOME=str(home), SECOND_BRAIN_NO_AUTOSYNC="1")
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args], env=env,
                          capture_output=True, text=True, encoding=UTF8, errors="replace",
                          timeout=TIMEOUT_S)


def nodes(db: Path) -> set[str]:
    con = sqlite3.connect(str(db))
    try:
        return {r[0] for r in con.execute("SELECT id FROM nodes")}
    finally:
        con.close()


def home_nodes(home: Path) -> set[str]:
    return nodes(home / "graph.db")


def settings(home: Path) -> dict:
    return json.loads((home / "settings.json").read_text(encoding=UTF8))


def make_brain(home: Path) -> None:
    home.mkdir(parents=True)
    run(home, "graph.py", "add-node", "--type", "note", "--title", "Kept", "--id", KEPT_NODE)
    (home / "settings.json").write_text(json.dumps(ORIGINAL_OWNER), encoding=UTF8)
    (home / "neo4j.json").write_text('{"password": "secret"}', encoding=UTF8)
    s = home / "scripts" / "mine.py"
    s.parent.mkdir()
    s.write_text("print('mine')\n", encoding=UTF8)
    s.chmod(0o755)


def leftovers() -> list[Path]:
    return list(Path(tempfile.gettempdir()).glob(LEFTOVER_GLOB))


# ----------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def work(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("restore")


@pytest.fixture(scope="module")
def leftovers_before() -> list[Path]:
    return leftovers()


@pytest.fixture(scope="module")
def brain_a(work: Path, leftovers_before) -> Path:
    a = work / "a"
    make_brain(a)
    return a


@pytest.fixture(scope="module")
def backup(work: Path, brain_a: Path) -> tuple[Path, subprocess.CompletedProcess]:
    r = run(brain_a, "bundle.py", "--with-brain", "--out", str(work))
    zips = list(work.glob("*-with-brain.zip"))
    assert r.returncode == 0 and zips, r.stderr[-300:]
    return zips[0], r


@pytest.fixture(scope="module")
def zip_path(backup) -> Path:
    return backup[0]


# --------------------------------------------------------------- the backup
class TestBackup:
    def test_it_carries_the_brain(self, zip_path):
        names = zipfile.ZipFile(zip_path).namelist()
        assert "memory-ledger/brain/graph.db" in names
        assert "memory-ledger/brain/scripts/mine.py" in names

    def test_and_never_a_secret(self, zip_path):
        assert not any(n.endswith("neo4j.json") for n in zipfile.ZipFile(zip_path).namelist())


# ----------------------------------------- restore onto a machine with nothing
class TestRestoreOntoEmpty:
    @pytest.fixture(scope="class")
    def restored(self, work: Path, zip_path: Path) -> tuple[Path, subprocess.CompletedProcess]:
        b = work / "b"
        b.mkdir()
        return b, run(b, "restore.py", str(zip_path))

    def test_a_dry_run_changes_nothing(self, work, zip_path):
        d = work / "dry"
        d.mkdir()
        r = run(d, "restore.py", str(zip_path), "--dry-run")
        assert r.returncode == 0, r.stdout[-200:]
        assert not (d / "graph.db").exists()

    def test_the_restore_succeeds(self, restored):
        _, r = restored
        assert r.returncode == 0, (r.stdout + r.stderr)[-400:]

    def test_the_same_nodes_come_back(self, restored, brain_a):
        assert home_nodes(restored[0]) == home_nodes(brain_a)

    def test_home_files_come_back(self, restored):
        assert settings(restored[0]) == ORIGINAL_OWNER

    @EXEC_BIT
    def test_a_script_stays_executable(self, restored):
        assert os.access(restored[0] / "scripts" / "mine.py", os.X_OK)

    def test_no_secret_is_written(self, restored):
        assert not (restored[0] / "neo4j.json").exists()

    def test_the_restored_brain_works(self, restored):
        assert run(restored[0], "graph.py", "get-node", "--id", KEPT_NODE).returncode == 0


# ------------------------------- an existing brain is never overwritten by accident
class TestExistingBrain:
    @pytest.fixture(scope="class")
    def b(self, work: Path, zip_path: Path) -> Path:
        b = work / "existing"
        b.mkdir()
        assert run(b, "restore.py", str(zip_path)).returncode == 0
        run(b, "graph.py", "add-node", "--type", "note", "--title", "Only on b", "--id", ONLY_B_NODE)
        return b

    def test_without_replace_it_refuses(self, b, zip_path):
        r = run(b, "restore.py", str(zip_path))
        assert r.returncode == 1, r.stderr[-200:]
        assert ONLY_B_NODE in home_nodes(b)

    def test_with_replace_it_restores_after_snapshotting_what_it_replaced(self, b, zip_path):
        (b / "settings.json").write_text(json.dumps({"owner": EDITED_OWNER}), encoding=UTF8)
        r = run(b, "restore.py", str(zip_path), "--replace")
        assert r.returncode == 0, (r.stdout + r.stderr)[-300:]
        assert ONLY_B_NODE not in home_nodes(b)
        kept = list((b / "backups").glob("graph.db.pre-restore-*"))
        assert kept
        assert ONLY_B_NODE in nodes(kept[0])

    def test_an_edited_home_file_is_kept_by_default(self, b):
        assert settings(b)["owner"] == EDITED_OWNER

    def test_overwrite_files_replaces_it_and_saves_the_one_it_replaced(self, b, zip_path):
        run(b, "restore.py", str(zip_path), "--replace", "--overwrite-files")
        assert settings(b)["owner"] == ORIGINAL_OWNER["owner"]
        saved = list((b / "backups").glob("pre-restore-*/files/settings.json"))
        assert saved
        assert json.loads(saved[0].read_text(encoding=UTF8))["owner"] == EDITED_OWNER

    def test_list_finds_the_set_aside_brains(self, b):
        assert "graph.db.pre-restore-" in run(b, "restore.py", "--list").stdout


# ------------------------------------------------ a bad backup restores nothing
class TestBadBackup:
    def test_a_corrupt_graph_db_is_refused(self, work):
        victim = work / "victim"
        make_brain(victim)
        ids = home_nodes(victim)
        bad = work / "bad.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("memory-ledger/brain/graph.db", b"not a database at all")
        r = run(victim, "restore.py", str(bad), "--replace")
        assert r.returncode == 1, r.stderr[-200:]
        assert home_nodes(victim) == ids

    def test_an_entry_that_climbs_out_or_smuggles_a_secret_is_not_written(self, work, zip_path):
        evil = work / "evil.zip"
        with zipfile.ZipFile(zip_path) as src, zipfile.ZipFile(evil, "w") as z:
            for n in src.namelist():
                z.writestr(n, src.read(n))
            z.writestr("memory-ledger/brain/../../escaped.txt", "x")
            z.writestr("memory-ledger/brain/trigger.json", '{"token": "x"}')
        c = work / "c"
        c.mkdir()
        run(c, "restore.py", str(evil))
        assert not (work / "escaped.txt").exists()
        assert not (c.parent / "escaped.txt").exists()
        assert not (c / "trigger.json").exists()


# ------------------------------------------------- a .db snapshot restores too
def test_a_bare_snapshot_restores(work, brain_a):
    snap = work / "snap.db"
    src, dst = sqlite3.connect(str(brain_a / "graph.db")), sqlite3.connect(str(snap))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    e = work / "e"
    e.mkdir()
    r = run(e, "restore.py", str(snap))
    assert r.returncode == 0, (r.stdout + r.stderr)[-300:]
    assert home_nodes(e) == home_nodes(brain_a)


def test_no_unpacked_brain_is_left_in_the_temp_dir(leftovers_before, backup):
    # runs after the restores above (file order); `backup` pins that the round trip happened
    assert leftovers() == leftovers_before
