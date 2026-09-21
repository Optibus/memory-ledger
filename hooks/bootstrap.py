#!/usr/bin/env python3
"""SessionStart hook — auto-initialize the ledger on first run.

Installing the plugin used not to be enough: you had to `mkdir
~/.second_brain` and run `graph.py init` by hand from a local clone before any
hook would do anything. That made installing straight from GitHub (no local
clone, no manual init step) impossible — every fresh machine needed a human to
run a setup command the plugin itself could trivially run.

This hook removes that step. `graph.connect()` already creates the home dir
and the schema if they don't exist, so "initialize the ledger" is just calling
it once. It also adopts the canonical home (`~/.memory-ledger`), symlinking it
to a pre-rename `~/.second_brain` when that is where the data actually is. Firing this on every SessionStart makes init happen transparently on
first use and is a cheap no-op (open a SQLite connection, check the schema)
every time after.

The one thing this must never do is resurrect a ledger the user explicitly
turned off — so it checks `graph.disabled()` first and, if true, does
nothing at all: no directory, no DB, no output. `graph.disabled()` only
`stat()`s the home dir looking for the `.disabled` marker; it never creates
it, so checking before connecting cannot itself bring a disabled ledger's
home dir into existence.

Prints nothing on success — it runs on every session start and must be
invisible once the ledger is initialized. Wrapped so it always exits 0: a
broken bootstrap must never block a session.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import graph  # noqa: E402  (the engine — single source of truth for the DB)


def main():
    try:
        if graph.disabled():
            return  # explicitly off -> never resurrect, never touch disk
        # Point ~/.memory-ledger at the pre-rename ~/.second_brain if that is
        # where the brain actually lives. A symlink, not a move: nothing is
        # copied and every absolute path already baked into a cron or a backup
        # job keeps resolving. No-op once adopted, and a failure here is
        # deliberately silent -- the legacy path still resolves on its own.
        graph.adopt_canonical_home()
        graph.connect().close()
    except Exception:
        pass  # a broken bootstrap must never block a session


if __name__ == "__main__":
    main()
    sys.exit(0)
