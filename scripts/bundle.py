#!/usr/bin/env python3
"""Build a self-contained zip of the plugin — installable with no repo access.

    python3 scripts/bundle.py                 # dist/memory-ledger-<version>.zip
    python3 scripts/bundle.py --out ~/Desktop # somewhere else
    python3 scripts/bundle.py --list          # what would go in

Why this exists: `/plugin marketplace add Optibus/memory-ledger` needs the person
to be able to read the GitHub repo. Until that access is granted, a zip is the
whole plugin — Claude Code installs a plugin from a **directory**, so unzipping is
the install (`INSTALL-FROM-ZIP.md`, generated into the archive, is the whole
procedure).

What goes in: every git-tracked file (so `.gitignore` is honoured and no
generated deck, brain data, or eval case can ride along), plus a generated
`INSTALL-FROM-ZIP.md` and `BUNDLE.txt` manifest. Files are laid out under a
single top-level `memory-ledger/` directory so unzipping never spills into the
current directory.

**It contains no brain data.** The graph lives in `~/.second_brain`, outside the
repo, and this only packs tracked repo files — the recipient gets the engine and
the skills, and starts with an empty brain of their own.

`--with-brain` is the one exception, and only when asked: it adds the person's
own brain under `memory-ledger/brain/` — a consistent snapshot of `graph.db`
(SQLite's backup API, safe while the brain is in use) and the home files the
neo4j mirror carries (settings, user scripts, references, learned skills). Never
secrets (`neo4j.json`, `trigger.json`). That zip is PRIVATE, so it defaults to
`<home>/exports/` instead of `dist/`:

    python3 scripts/bundle.py --with-brain    # ~/.second_brain/exports/memory-ledger-<v>-with-brain.zip

`--include <path>` rides an extra file along that is deliberately NOT in the
repo. The plugin is company-agnostic, so anything company-specific — an exported
set of team rules, an internal runbook — lives outside it (`~/.second_brain/…`)
and is attached at bundle time:

    python3 scripts/bundle.py --include ~/.second_brain/exports/team-rules.md
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import _platform  # noqa: E402
PLUGIN_JSON = REPO / ".claude-plugin" / "plugin.json"

# Docs a person reads first, in the order they need them. Named so a missing one
# is a visible error rather than a silent gap.
ENTRY_DOCS = ["INSTALL-FROM-ZIP.md", "INSTALL.md", "README.md", "CONVENTIONS.md"]


def version() -> str:
    try:
        return json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return "0.0.0"


def git(*args) -> str:
    return subprocess.check_output(["git", "-C", str(REPO), *args], text=True, encoding="utf-8", errors="replace").strip()


def tracked_files() -> list[str]:
    """Git is the allowlist: tracked files only, so .gitignore decides what is
    excluded (brain data, eval cases, the generated deck) with no second list to
    keep in sync."""
    return [f for f in git("ls-files").splitlines() if f]


def head_info() -> dict:
    try:
        return {"commit": git("rev-parse", "--short", "HEAD"),
                "date": git("log", "-1", "--format=%cI"),
                "subject": git("log", "-1", "--format=%s"),
                "dirty": bool(git("status", "--porcelain"))}
    except subprocess.CalledProcessError:
        return {}


INSTALL_FROM_ZIP = """# Install Memory Ledger from this zip

No GitHub access, no marketplace, no admin needed. Claude Code loads a plugin
from a **directory**, so unzipping is most of the install.

## 1. Unpack

```
unzip memory-ledger-{version}.zip -d ~/dev
```
You now have `~/dev/memory-ledger/`. Anywhere is fine — the paths below just have
to match where you put it.

## 2. Prerequisites

- **Claude Code**, recent enough for plugin marketplaces and the
  `SessionStart` / `UserPromptSubmit` / `Stop` hooks.
- **Python 3** on your `PATH`. That is all — the engine and hooks are
  stdlib-only, zero third-party packages.
- `git` only if you want the brain to keep its own history.

## 3. Install the plugin

The zip contains the plugin's own marketplace manifest
(`.claude-plugin/marketplace.json`), so a **local** marketplace works exactly
like the GitHub one. In Claude Code:

```
/plugin marketplace add ~/dev/memory-ledger
/plugin install memory-ledger@memory-ledger
```

Prefer to try it for one session without installing:

```
claude --plugin-dir ~/dev/memory-ledger
```

Restart the session (or `/reload-plugins`) afterwards.

## 4. Enable the brain

The brain is off until its home directory exists — skills and hooks no-op
silently before that. Create it once:

```
mkdir -p ~/.second_brain
python3 ~/dev/memory-ledger/scripts/graph.py init
```

{brain_note}Set your timezone once (Settings in the UI, or `export SECOND_BRAIN_TZ=...`), and
you are done. Your data is one SQLite file at `~/.second_brain/graph.db` — local,
offline, connected to nothing.

## 5. Check it works

```
/plugin                                        # listed + enabled
/ledger what's on my plate
python3 ~/dev/memory-ledger/scripts/graph.py list --type capture --all --limit 5
```

## 6. Optional extras

- **The brain UI:** `python3 ~/dev/memory-ledger/scripts/serve.py` →
  <http://127.0.0.1:8770>.
- **A local neo4j** for graph queries (Docker, data under
  `~/.second_brain/neo4j`, restarts unless stopped):
  `python3 ~/dev/memory-ledger/scripts/neo4j_local.py up` — or `/neo4j-up` inside
  Claude Code. Entirely optional; SQLite is the default and is enough.
{extras_note}

## Updating later

This zip is a snapshot ({commit}, {date}). When repo access lands, switch to
the tracked copy and you get updates with `git pull`:

```
git clone git@github.com:Optibus/memory-ledger.git ~/dev/memory-ledger
```
(Move the zip copy aside first. `~/.second_brain` is outside the repo, so your
brain survives either way.)
"""


BRAIN_NOTE = """**This zip carries a brain** (`memory-ledger/brain/`, snapshot of {taken}).
Restore it INSTEAD of running `init`, straight from the zip:

```
python3 ~/dev/memory-ledger/scripts/restore.py memory-ledger-{version}-with-brain.zip
```

It integrity-checks the backup before touching anything, refuses to overwrite a
brain that is already there (`--replace` snapshots that one first), and keeps any
home file that already exists (`--overwrite-files` saves each one it replaces).
`--dry-run` shows what it would do.

Secrets are not in the zip: re-enter the neo4j connection (Settings › Database
connection, or `~/.second_brain/neo4j.json`) and the trigger tokens
(`~/.second_brain/trigger.json`) on the new machine.

"""


def brain_snapshot(tmp: Path) -> tuple[Path, list[dict]]:
    """A consistent copy of the live graph.db, plus the home files to carry.

    The backup API copies a page-consistent snapshot even while the server or a
    hook is writing, where a plain file copy can catch a half-written page. The
    snapshot forgets which sync writer it was (`machine_id`): two brains restored
    from one zip must not both skip each other's writes as their own."""
    import sqlite3
    import brain_sync
    import graph
    src = graph.db_path()
    if not src.is_file():
        raise SystemExit(f"--with-brain: no brain at {src}")
    dest = tmp / "graph.db"
    live = sqlite3.connect(str(src))
    snap = sqlite3.connect(str(dest))
    try:
        live.backup(snap)
        if snap.execute("SELECT 1 FROM sqlite_master WHERE name='sync_state'").fetchone():
            snap.execute("DELETE FROM sync_state WHERE key='machine_id'")
            snap.commit()
        check = snap.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        snap.close()
        live.close()
    if check != "ok":
        raise SystemExit(f"--with-brain: the snapshot failed its integrity check ({check})")
    return dest, brain_sync.local_files(graph.home_dir())


def extras_note(extras: list[str]) -> str:
    """The INSTALL-FROM-ZIP bullet for `--include`d files. Empty when there are
    none, because the plugin itself ships nothing company-specific."""
    if not extras:
        return ""
    lines = ["- **Attached alongside the plugin** (not part of it — company-specific "
             "material kept outside the repo):"]
    lines += [f"  - `{name}`" for name in extras]
    return "\n".join(lines)


def manifest(files: list[str], info: dict, ver: str, extras: list[str] | None = None,
             brain: list[str] | None = None) -> str:
    extras = extras or []
    brain = brain or []
    lines = [
        "Memory Ledger (memory-ledger) — offline bundle",
        f"version      {ver}",
        f"built        {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"commit       {info.get('commit', 'unknown')}"
        + ("  (WITH UNCOMMITTED CHANGES)" if info.get("dirty") else ""),
        f"head date    {info.get('date', 'unknown')}",
        f"head subject {info.get('subject', 'unknown')}",
        f"files        {len(files)} tracked + this manifest + INSTALL-FROM-ZIP.md"
        + (f" + {len(extras)} attached" if extras else "")
        + (f" + brain ({len(brain)} files)" if brain else ""),
        "",
        "START HERE   INSTALL-FROM-ZIP.md",
        "",
        ("CONTAINS A BRAIN under brain/ (--with-brain): personal data — share it only "
         "with its owner. Secrets are never included." if brain else
         "Contains no brain data: the graph lives in ~/.second_brain, outside the repo."),
        "The plugin is company-agnostic; anything under 'Attached' below was added "
        "with --include and is not part of it.",
        "",
        "Contents:",
    ]
    lines += [f"  {f}" for f in files]
    if extras:
        lines += ["", "Attached (--include):"] + [f"  {n}" for n in extras]
    if brain:
        lines += ["", "Brain (--with-brain):"] + [f"  brain/{n}" for n in brain]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", default=None,
                   help="output directory (default dist/, or <home>/exports/ with "
                        "--with-brain)")
    p.add_argument("--name", help="archive filename (default memory-ledger-<version>.zip)")
    p.add_argument("--include", action="append", default=[], metavar="PATH",
                   help="attach a file that is NOT in the repo (repeatable) — e.g. an "
                        "exported set of team rules from ~/.second_brain. It lands at "
                        "the archive root and is listed as 'attached', never as part "
                        "of the plugin.")
    p.add_argument("--with-brain", action="store_true",
                   help="also pack your brain under brain/: a consistent snapshot of "
                        "graph.db and the non-secret home files. PRIVATE — defaults "
                        "to <home>/exports/ instead of dist/.")
    p.add_argument("--list", action="store_true", dest="list_only",
                   help="print what would be packed and exit")
    args = p.parse_args(argv)

    ver, info = version(), head_info()
    try:
        files = tracked_files()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("not a git checkout (or git is missing) — this packs git-tracked files.",
              file=sys.stderr)
        return 1
    missing = [d for d in ENTRY_DOCS if d != "INSTALL-FROM-ZIP.md" and d not in files]
    if missing:
        print("warning: expected doc(s) not tracked, so not in the bundle: "
              + ", ".join(missing), file=sys.stderr)

    attached = []
    for raw in args.include:
        src = Path(raw).expanduser()
        if not src.is_file():
            print(f"--include: not a file: {src}", file=sys.stderr)
            return 1
        if src.name in {Path(f).name for f in files} or src.name in ("BUNDLE.txt",
                                                                    "INSTALL-FROM-ZIP.md"):
            print(f"--include: {src.name} collides with a bundled file — rename it "
                  "first.", file=sys.stderr)
            return 1
        attached.append(src)
    extras = [s.name for s in attached]

    # the snapshot is personal data: whatever happens, it never outlives the run
    tmp = Path(tempfile.mkdtemp(prefix="memory-ledger-bundle-"))
    try:
        return _build(args, files, info, ver, extras, attached, tmp)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _build(args, files, info, ver, extras, attached, tmp: Path) -> int:
    db_snap, home_files = (brain_snapshot(tmp) if args.with_brain else (None, []))
    brain = (["graph.db"] + [f["path"] for f in home_files]) if args.with_brain else []

    if args.list_only:
        print(manifest(files, info, ver, extras, brain))
        return 0

    if args.out:
        out_dir = Path(args.out).expanduser()
    elif args.with_brain:
        import graph
        out_dir = graph.home_dir() / "exports"
    else:
        out_dir = REPO / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "-with-brain" if args.with_brain else ""
    target = out_dir / (args.name or f"memory-ledger-{ver}{suffix}.zip")
    root = "memory-ledger"         # single top-level dir: unzip never spills

    taken = datetime.now(timezone.utc).isoformat(timespec="seconds")
    install = INSTALL_FROM_ZIP.format(version=ver, commit=info.get("commit", "unknown"),
                                      date=(info.get("date") or "unknown")[:10],
                                      extras_note=extras_note(extras),
                                      brain_note=(BRAIN_NOTE.format(taken=taken, version=ver)
                                                  if args.with_brain else ""))
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            src = REPO / rel
            if not src.exists():        # tracked but deleted in the working tree
                continue
            z.write(src, f"{root}/{rel}")
        for src in attached:
            z.write(src, f"{root}/{src.name}")
        if db_snap:
            z.write(db_snap, f"{root}/brain/graph.db")
            import graph
            home = graph.home_dir()
            for f in home_files:       # write(), not writestr(): keeps the +x bit
                z.write(home / f["path"], f"{root}/brain/{f['path']}")
        z.writestr(f"{root}/INSTALL-FROM-ZIP.md", install)
        z.writestr(f"{root}/BUNDLE.txt", manifest(files, info, ver, extras, brain))
    if args.with_brain:
        _platform.owner_only(target)   # personal data: owner-only, like neo4j.json

    size = target.stat().st_size / 1024
    print(f"{target}  ({size:.0f} KiB, "
          f"{len(files) + len(attached) + len(brain) + 2} files)")
    if brain:
        print(f"  brain: graph.db + {len(home_files)} home files — PRIVATE, share only "
              "with its owner (secrets not included)")
    for name in extras:
        print(f"  attached: {name}  (not part of the plugin)")
    if info.get("dirty"):
        print("note: the working tree has uncommitted changes. Tracked files are "
              "packed AS THEY ARE ON DISK, so those changes are in the zip while the "
              "recorded commit does not contain them — commit first if the bundle "
              "should be reproducible.")
    print("the recipient needs: unzip, then INSTALL-FROM-ZIP.md (no repo access "
          "required)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
