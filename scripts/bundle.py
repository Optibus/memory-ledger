#!/usr/bin/env python3
"""Build a self-contained zip of the plugin — installable with no repo access.

    python3 scripts/bundle.py                 # dist/second-brain-<version>.zip
    python3 scripts/bundle.py --out ~/Desktop # somewhere else
    python3 scripts/bundle.py --list          # what would go in

Why this exists: `/plugin marketplace add Optibus/second-brain` needs the person
to be able to read the GitHub repo. Until that access is granted, a zip is the
whole plugin — Claude Code installs a plugin from a **directory**, so unzipping is
the install (`INSTALL-FROM-ZIP.md`, generated into the archive, is the whole
procedure).

What goes in: every git-tracked file (so `.gitignore` is honoured and no
generated deck, brain data, or eval case can ride along), plus a generated
`INSTALL-FROM-ZIP.md` and `BUNDLE.txt` manifest. Files are laid out under a
single top-level `second-brain/` directory so unzipping never spills into the
current directory.

**It contains no brain data.** The graph lives in `~/.second_brain`, outside the
repo, and this only packs tracked repo files — the recipient gets the engine and
the skills, and starts with an empty brain of their own.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN_JSON = REPO / ".claude-plugin" / "plugin.json"

# Docs a person reads first, in the order they need them. Named so BUNDLE.txt can
# point at them and so a missing one is a visible error rather than a silent gap.
ENTRY_DOCS = ["INSTALL-FROM-ZIP.md", "INSTALL.md", "README.md",
              "docs/optibus-team-rules.md", "CONVENTIONS.md"]


def version() -> str:
    try:
        return json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return "0.0.0"


def git(*args) -> str:
    return subprocess.check_output(["git", "-C", str(REPO), *args], text=True).strip()


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
unzip second-brain-{version}.zip -d ~/dev
```
You now have `~/dev/second-brain/`. Anywhere is fine — the paths below just have
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
/plugin marketplace add ~/dev/second-brain
/plugin install second-brain@second-brain
```

Prefer to try it for one session without installing:

```
claude --plugin-dir ~/dev/second-brain
```

Restart the session (or `/reload-plugins`) afterwards.

## 4. Enable the brain

The brain is off until its home directory exists — skills and hooks no-op
silently before that. Create it once:

```
mkdir -p ~/.second_brain
python3 ~/dev/second-brain/scripts/graph.py init
```

Set your timezone once (Settings in the UI, or `export SECOND_BRAIN_TZ=...`), and
you are done. Your data is one SQLite file at `~/.second_brain/graph.db` — local,
offline, connected to nothing.

## 5. Check it works

```
/plugin                                        # listed + enabled
/ledger what's on my plate
python3 ~/dev/second-brain/scripts/graph.py list --type capture --all --limit 5
```

## 6. Optional extras

- **The map + dashboard:** `python3 ~/dev/second-brain/viz/serve.py` →
  <http://127.0.0.1:8770>.
- **A local neo4j** for graph queries (Docker, data under
  `~/.second_brain/neo4j`, restarts unless stopped):
  `python3 ~/dev/second-brain/scripts/neo4j_local.py up` — or `/neo4j-up` inside
  Claude Code. Entirely optional; SQLite is the default and is enough.
- **The team's working rules:** `docs/optibus-team-rules.md` — thirteen rules,
  with a paste block at the end that loads them into your own brain so your
  agent pulls only the ones that apply.

## Updating later

This zip is a snapshot ({commit}, {date}). When repo access lands, switch to
the tracked copy and you get updates with `git pull`:

```
git clone git@github.com:Optibus/second-brain.git ~/dev/second-brain
```
(Move the zip copy aside first. `~/.second_brain` is outside the repo, so your
brain survives either way.)
"""


def manifest(files: list[str], info: dict, ver: str) -> str:
    lines = [
        "Memory Ledger (second-brain) — offline bundle",
        f"version      {ver}",
        f"built        {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"commit       {info.get('commit', 'unknown')}"
        + ("  (WITH UNCOMMITTED CHANGES)" if info.get("dirty") else ""),
        f"head date    {info.get('date', 'unknown')}",
        f"head subject {info.get('subject', 'unknown')}",
        f"files        {len(files)} tracked + this manifest + INSTALL-FROM-ZIP.md",
        "",
        "START HERE   INSTALL-FROM-ZIP.md",
        "",
        "Contains no brain data: the graph lives in ~/.second_brain, outside the repo.",
        "",
        "Contents:",
    ]
    lines += [f"  {f}" for f in files]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", default=str(REPO / "dist"),
                   help="output directory (default dist/)")
    p.add_argument("--name", help="archive filename (default second-brain-<version>.zip)")
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

    if args.list_only:
        print(manifest(files, info, ver))
        return 0

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / (args.name or f"second-brain-{ver}.zip")
    root = "second-brain"          # single top-level dir: unzip never spills

    install = INSTALL_FROM_ZIP.format(version=ver, commit=info.get("commit", "unknown"),
                                      date=(info.get("date") or "unknown")[:10])
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            src = REPO / rel
            if not src.exists():        # tracked but deleted in the working tree
                continue
            z.write(src, f"{root}/{rel}")
        z.writestr(f"{root}/INSTALL-FROM-ZIP.md", install)
        z.writestr(f"{root}/BUNDLE.txt", manifest(files, info, ver))

    size = target.stat().st_size / 1024
    print(f"{target}  ({size:.0f} KiB, {len(files) + 2} files)")
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
