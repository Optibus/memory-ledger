#!/usr/bin/env python3
"""Backup and restore round trip: `bundle.py --with-brain` → `restore.py`.

    python3 tests/test_restore.py

Stdlib only. Every brain here is a THROWAWAY home; the scripts run as
subprocesses, exactly as a person runs them, so each sees only its own env.
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

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
WORK = Path(tempfile.mkdtemp(prefix="second-brain-restore-"))
FAILS: list[str] = []


def ck(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def env_for(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SECOND_BRAIN_", "MEMORY_LEDGER_"))}
    return {**env, "SECOND_BRAIN_HOME": str(home), "SECOND_BRAIN_NO_AUTOSYNC": "1"}


def run(home: Path, script: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                          env=env_for(home), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)


def nodes(home: Path) -> set:
    con = sqlite3.connect(str(home / "graph.db"))
    try:
        return {r[0] for r in con.execute("SELECT id FROM nodes")}
    finally:
        con.close()


def make_brain(home: Path) -> None:
    home.mkdir(parents=True)
    run(home, "graph.py", "add-node", "--type", "note", "--title", "Kept", "--id", "note_kept")
    (home / "settings.json").write_text('{"owner": "Original"}', encoding="utf-8")
    (home / "neo4j.json").write_text('{"password": "secret"}', encoding="utf-8")
    s = home / "scripts" / "mine.py"
    s.parent.mkdir()
    s.write_text("print('mine')\n", encoding="utf-8")
    s.chmod(0o755)


def leftovers() -> list:
    return list(Path(tempfile.gettempdir()).glob("memory-ledger-restore-*"))


def main() -> int:
    try:
        a, b = WORK / "a", WORK / "b"
        make_brain(a)
        before = leftovers()

        print("== the backup ==")
        r = run(a, "bundle.py", "--with-brain", "--out", str(WORK))
        zips = list(WORK.glob("*-with-brain.zip"))
        ck("bundle --with-brain builds a zip", r.returncode == 0 and zips, r.stderr[-300:])
        zp = zips[0]
        names = zipfile.ZipFile(zp).namelist()
        ck("it carries the brain", "memory-ledger/brain/graph.db" in names
           and "memory-ledger/brain/scripts/mine.py" in names)
        ck("and never a secret", not any(n.endswith("neo4j.json") for n in names))

        print("== restore onto a machine with nothing ==")
        b.mkdir()
        r = run(b, "restore.py", str(zp), "--dry-run")
        ck("a dry run changes nothing", r.returncode == 0 and not (b / "graph.db").exists(),
           r.stdout[-200:])
        r = run(b, "restore.py", str(zp))
        ck("the restore succeeds", r.returncode == 0, (r.stdout + r.stderr)[-400:])
        ck("the same nodes come back", nodes(b) == nodes(a))
        ck("home files come back", json.loads((b / "settings.json").read_text(encoding="utf-8"))
           == {"owner": "Original"})
        ck("a script stays executable", os.access(b / "scripts" / "mine.py", os.X_OK))
        ck("no secret is written", not (b / "neo4j.json").exists())
        ck("the restored brain works", run(b, "graph.py", "get-node", "--id", "note_kept").returncode == 0)

        print("== an existing brain is never overwritten by accident ==")
        run(b, "graph.py", "add-node", "--type", "note", "--title", "Only on b",
            "--id", "note_only_b")
        r = run(b, "restore.py", str(zp))
        ck("without --replace it refuses", r.returncode == 1 and "note_only_b" in nodes(b),
           r.stderr[-200:])
        (b / "settings.json").write_text('{"owner": "Edited on b"}', encoding="utf-8")
        r = run(b, "restore.py", str(zp), "--replace")
        kept = list((b / "backups").glob("graph.db.pre-restore-*"))
        ck("with --replace it restores", r.returncode == 0 and "note_only_b" not in nodes(b),
           (r.stdout + r.stderr)[-300:])
        ck("after snapshotting what it replaced", kept and "note_only_b" in {
            x[0] for x in sqlite3.connect(str(kept[0])).execute("SELECT id FROM nodes")})
        ck("an edited home file is kept by default",
           json.loads((b / "settings.json").read_text(encoding="utf-8"))["owner"] == "Edited on b")
        r = run(b, "restore.py", str(zp), "--replace", "--overwrite-files")
        saved = list((b / "backups").glob("pre-restore-*/files/settings.json"))
        ck("--overwrite-files replaces it", json.loads(
            (b / "settings.json").read_text(encoding="utf-8"))["owner"] == "Original")
        ck("and saves the one it replaced", saved and json.loads(
            saved[0].read_text(encoding="utf-8"))["owner"] == "Edited on b")

        print("== a bad backup restores nothing ==")
        bad = WORK / "bad.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("memory-ledger/brain/graph.db", b"not a database at all")
        ids = nodes(b)
        r = run(b, "restore.py", str(bad), "--replace")
        ck("a corrupt graph.db is refused", r.returncode == 1 and nodes(b) == ids,
           r.stderr[-200:])
        evil = WORK / "evil.zip"
        with zipfile.ZipFile(zp) as src, zipfile.ZipFile(evil, "w") as z:
            for n in src.namelist():
                z.writestr(n, src.read(n))
            z.writestr("memory-ledger/brain/../../escaped.txt", "x")
            z.writestr("memory-ledger/brain/trigger.json", '{"token": "x"}')
        c = WORK / "c"
        c.mkdir()
        r = run(c, "restore.py", str(evil))
        ck("an entry that climbs out of the home is not written",
           not (WORK / "escaped.txt").exists() and not (c.parent / "escaped.txt").exists())
        ck("nor a secret smuggled into the zip", not (c / "trigger.json").exists())

        print("== a .db snapshot restores too ==")
        snap = WORK / "snap.db"
        s, d = sqlite3.connect(str(a / "graph.db")), sqlite3.connect(str(snap))
        s.backup(d)
        d.close()
        s.close()
        e = WORK / "e"
        e.mkdir()
        r = run(e, "restore.py", str(snap))
        ck("a bare snapshot restores", r.returncode == 0 and nodes(e) == nodes(a),
           (r.stdout + r.stderr)[-300:])
        ck("and --list finds the set-aside brains",
           "graph.db.pre-restore-" in run(b, "restore.py", "--list").stdout)

        ck("no unpacked brain is left in the temp dir", leftovers() == before)
    finally:
        import shutil
        shutil.rmtree(WORK, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
