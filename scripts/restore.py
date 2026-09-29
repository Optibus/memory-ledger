#!/usr/bin/env python3
"""restore — put a backup of the brain back in place.

Takes either kind of backup the brain makes:
  - a zip from `bundle.py --with-brain` (graph.db + the home files), or
  - a `.db` snapshot from `<home>/backups/` (what consolidate and `backup` write).

    python3 scripts/restore.py --list                  # the backups it can find
    python3 scripts/restore.py BACKUP --dry-run        # what would happen, change nothing
    python3 scripts/restore.py BACKUP                  # restore onto a home with no brain
    python3 scripts/restore.py BACKUP --replace        # restore over the current brain
    python3 scripts/restore.py BACKUP --home ~/other   # somewhere other than the brain home

Nothing is lost on the way in:
  - the backup is unpacked to a temp dir and integrity-checked BEFORE anything
    is touched; a backup that fails the check restores nothing;
  - an existing brain is never overwritten without --replace, and with it the
    current graph.db is first snapshotted to `<home>/backups/graph.db.pre-restore-*`
    (never pruned; a file too corrupt to snapshot is moved aside instead);
  - home files that already exist are kept unless --overwrite-files, which saves
    each one it replaces under `<home>/backups/pre-restore-<stamp>/files/`.

The database is written with SQLite's backup API, into the existing file, so a
running brain server sees the restored brain on its next read — no restart, no
file swapped out from under an open connection.

Secrets are never in a backup: re-enter `neo4j.json` / `trigger.json` by hand.
If a neo4j master is configured, the next sync pulls down whatever was written
there after the backup was taken — right for a lost or broken machine, and the
thing to know before using a restore to roll back.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent

SECRET_FILES = {"neo4j.json", "neo4j_bkp.json", "trigger.json"}
COUNTED = ("nodes", "edges", "trigger_request", "coactivation")


def _graph(home: str | None):
    """The engine, pointed at `home` when given. Env first, then import, so the
    engine resolves the same home everything else in this run does."""
    if home:
        h = str(Path(home).expanduser().resolve())
        for prefix in ("MEMORY_LEDGER_", "SECOND_BRAIN_"):
            os.environ[prefix + "HOME"] = h
            os.environ.pop(prefix + "DB", None)
    sys.path.insert(0, str(HERE))
    import graph
    return graph


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _counts(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in COUNTED if t in have}
    finally:
        con.close()


def _check(path: Path) -> str:
    """'ok', or why this file is not a brain that can be restored."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = con.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                return f"integrity check failed: {result}"
            if not con.execute("SELECT 1 FROM sqlite_master WHERE name='nodes'").fetchone():
                return "not a brain (no nodes table)"
        finally:
            con.close()
    except sqlite3.DatabaseError as e:
        return f"not a readable SQLite file: {e}"
    return "ok"


def _safe_rel(name: str) -> PurePosixPath | None:
    """A zip entry's path, or None if it could land outside the home (absolute,
    `..`) or is a file a backup must never write (a secret, a conflict copy)."""
    rel = PurePosixPath(name)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        return None
    if rel.name in SECRET_FILES or ".conflict-" in rel.name:
        return None
    return rel


# ---------------------------------------------------------------- unpack

def unpack(src: Path, tmp: Path) -> tuple[Path, list[tuple[PurePosixPath, Path, int]]]:
    """(snapshot db in tmp, [(home-relative path, extracted file, mode)])."""
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            dbs = [i for i in z.infolist() if i.filename.endswith("brain/graph.db")]
            if not dbs:
                raise SystemExit(f"{src} has no brain/graph.db — build it with "
                                 "`bundle.py --with-brain`, not a plain bundle.")
            prefix = dbs[0].filename[:-len("graph.db")]          # e.g. memory-ledger/brain/
            db = tmp / "graph.db"
            with z.open(dbs[0]) as fin, open(db, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            files = []
            for info in z.infolist():
                if (info.is_dir() or not info.filename.startswith(prefix)
                        or info.filename == dbs[0].filename):
                    continue
                rel = _safe_rel(info.filename[len(prefix):])
                if rel is None:
                    print(f"  skipped unsafe entry: {info.filename}", file=sys.stderr)
                    continue
                out = tmp / "files" / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as fin, open(out, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
                mode = (info.external_attr >> 16) & 0o777
                files.append((rel, out, mode or 0o644))
        return db, files
    # a bare snapshot: copy it through the backup API, which also proves it opens
    db = tmp / "graph.db"
    try:
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        d = sqlite3.connect(str(db))
        try:
            s.backup(d)
        finally:
            d.close()
            s.close()
    except sqlite3.DatabaseError as e:
        raise SystemExit(f"{src} is neither a --with-brain zip nor a readable "
                         f"SQLite snapshot: {e}")
    return db, []


# ---------------------------------------------------------------- restore

def _has_brain(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        return sum(_counts(path).values()) > 0
    except sqlite3.DatabaseError:
        return True                  # unreadable is still somebody's brain


def set_aside(graph, live: Path, stamp: str) -> str:
    """Keep the current brain before it is overwritten: a snapshot when it opens,
    the file moved aside whole when it does not. Named `graph.db.pre-restore-*`,
    not `*.db`, on purpose — the engine prunes `backups/*.db` after
    BACKUP_KEEP_DAYS, and the brain a restore replaced must not expire quietly."""
    graph.backups_dir().mkdir(parents=True, exist_ok=True)
    if _check(live) == "ok":
        dest = graph.backups_dir() / f"graph.db.pre-restore-{stamp}"
        s = sqlite3.connect(str(live), timeout=30)
        d = sqlite3.connect(str(dest))
        try:
            s.backup(d)
        finally:
            d.close()
            s.close()
        return str(dest)
    dest = graph.backups_dir() / f"graph.db.unreadable-{stamp}"
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = Path(str(live) + suffix)
        if p.exists():
            shutil.move(str(p), str(dest) + suffix)
    return str(dest)


def write_db(snapshot: Path, live: Path) -> None:
    live.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(str(snapshot))
    d = sqlite3.connect(str(live), timeout=30)     # waits out a write in progress
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def write_files(graph, home: Path, files, overwrite: bool, stamp: str, dry: bool) -> dict:
    res = {"written": [], "same": [], "kept": [], "saved": []}
    for rel, src, mode in files:
        dest = home / Path(*rel.parts)
        if dest.is_file() and dest.read_bytes() == src.read_bytes():
            res["same"].append(str(rel))
            continue
        if dest.exists() and not overwrite:
            res["kept"].append(str(rel))
            continue
        if dest.exists():
            keep = graph.backups_dir() / f"pre-restore-{stamp}" / "files" / Path(*rel.parts)
            if not dry:
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, keep)
            res["saved"].append(str(keep))
        if not dry:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            dest.chmod(mode)
        res["written"].append(str(rel))
    return res


def list_backups(graph) -> int:
    home = graph.home_dir()
    zips = sorted((home / "exports").glob("*with-brain*.zip"), key=os.path.getmtime,
                  reverse=True) if (home / "exports").is_dir() else []
    snaps = graph.list_backups()
    print(f"brain home: {home}")
    print("\nzips (bundle.py --with-brain):")
    for z in zips:
        print(f"  {z}  ({z.stat().st_size / 1e6:.1f} MB, "
              f"{datetime.fromtimestamp(z.stat().st_mtime).isoformat(timespec='minutes')})")
    if not zips:
        print("  none")
    print(f"\nsnapshots ({graph.backups_dir()}, kept {graph.BACKUP_KEEP_DAYS} days):")
    for s in snaps:
        print(f"  {s['path']}  ({s['bytes'] / 1e6:.1f} MB, {s['created']})")
    if not snaps:
        print("  none")
    kept = sorted(graph.backups_dir().glob("graph.db.pre-*"), key=os.path.getmtime,
                  reverse=True) if graph.backups_dir().is_dir() else []
    print("\nbrains set aside by a restore or --pull --replace (never pruned):")
    for k in kept:
        print(f"  {k}  ({k.stat().st_size / 1e6:.1f} MB)")
    if not kept:
        print("  none")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("backup", nargs="?", help="a --with-brain zip or a .db snapshot")
    ap.add_argument("--list", action="store_true", help="list the backups it can find")
    ap.add_argument("--home", help="restore into this brain home (default: the brain home)")
    ap.add_argument("--replace", action="store_true",
                    help="restore over an existing brain (it is snapshotted first)")
    ap.add_argument("--overwrite-files", action="store_true",
                    help="replace home files that already exist (each is saved first)")
    ap.add_argument("--dry-run", action="store_true", help="report, change nothing")
    args = ap.parse_args(argv)

    graph = _graph(args.home)
    if args.list:
        return list_backups(graph)
    if not args.backup:
        ap.error("give a backup to restore, or --list")
    src = Path(args.backup).expanduser()
    if not src.is_file():
        print(f"no such backup: {src}", file=sys.stderr)
        return 2

    home, live, stamp = graph.home_dir(), graph.db_path(), _stamp()
    tmp = Path(tempfile.mkdtemp(prefix="memory-ledger-restore-"))
    try:                  # the unpacked brain is personal data: never left behind
        snapshot, files = unpack(src, tmp)
        verdict = _check(snapshot)
        if verdict != "ok":
            print(f"REFUSED: the backup's graph.db — {verdict}. Nothing was changed.",
                  file=sys.stderr)
            return 1
        incoming = _counts(snapshot)
        print(f"backup   {src}")
        print(f"         {', '.join(f'{v} {k}' for k, v in incoming.items())}"
              + (f", {len(files)} home files" if files else ""))
        print(f"target   {live}")
        exists = _has_brain(live)
        if exists:
            try:
                print(f"         holds {', '.join(f'{v} {k}' for k, v in _counts(live).items())}")
            except sqlite3.DatabaseError:
                print("         holds a brain that cannot be read")
            if not args.replace:
                print("REFUSED: there is already a brain here. Pass --replace to restore "
                      "over it (it is snapshotted first).", file=sys.stderr)
                return 1

        if args.dry_run:
            print("\nDRY RUN — would:")
            if exists:
                print("  snapshot the current brain to backups/graph.db.pre-restore-*")
            print("  write the backup's graph.db into place")
            plan = write_files(graph, home, files, args.overwrite_files, stamp, dry=True)
            print(f"  write {len(plan['written'])} home files, keep {len(plan['kept'])} "
                  f"existing, skip {len(plan['same'])} identical")
            return 0

        if exists:
            print(f"kept     current brain -> {set_aside(graph, live, stamp)}")
        write_db(snapshot, live)
        after = _counts(live)             # before anything else can write to it
        if after != incoming:
            print(f"ERROR: restored counts {after} do not match the backup {incoming}",
                  file=sys.stderr)
            return 1
        # an older snapshot gets today's schema and mirror triggers
        graph.connect().close()
        print(f"restored {', '.join(f'{v} {k}' for k, v in after.items())} "
              f"(integrity: {_check(live)})")
        res = write_files(graph, home, files, args.overwrite_files, stamp, dry=False)
        if files:
            print(f"files    {len(res['written'])} written, {len(res['same'])} identical, "
                  f"{len(res['kept'])} existing kept"
                  + ("" if args.overwrite_files or not res["kept"]
                     else " (--overwrite-files to replace them)"))
            if res["saved"]:
                print(f"         replaced files saved under "
                      f"{graph.backups_dir() / f'pre-restore-{stamp}'}")

        missing = [f for f in ("neo4j.json", "trigger.json") if not (home / f).exists()]
        if missing:
            print(f"\nnot in any backup, re-enter by hand if you use them: {', '.join(missing)}")
        cfg = graph._neo4j_cfg()
        if cfg["url"] and cfg["password"]:
            print("\nneo4j master configured: the next sync brings down whatever was "
                  "written there after this backup was taken.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
