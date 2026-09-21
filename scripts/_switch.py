#!/usr/bin/env python3
"""Where the ledger lives, and whether it is on.

This is the smallest module in the tree on purpose. It holds the three answers
a caller needs *before* it can decide to do nothing — which config prefix wins,
where home is, and whether the user turned the ledger off — and it imports
nothing but `os` and `pathlib` to give them.

The reason is the SessionStart path. `hooks/open_loops_brief.py` runs on every
session and its first act is to ask "am I even enabled?". Reaching that answer
through the full engine costs ~60 ms of transitive imports (urllib, argparse,
email), and it costs it *most* on the path that was supposed to be free: the
one where the user has turned capture off and the honest response is to exit.
A switch you pay for whether or not you flip it is a badly placed switch.

So the predicate moves down here and `graph.py` imports it back up, rather than
the hooks reaching down through `graph`. There is still exactly one
implementation — CONVENTIONS §2d — and `graph.disabled()` / `graph.enabled()`
still resolve to it, so nothing that already reads it through the engine has to
change. Never re-implement these; import them.
"""

import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    """Config env lookup: MEMORY_LEDGER_<name>, else the legacy
    SECOND_BRAIN_<name>, else the default. The MEMORY_LEDGER_ prefix is
    canonical; the SECOND_BRAIN_ one keeps every existing shell rc, cron and
    launchd agent working and is not going away."""
    return (os.environ.get("MEMORY_LEDGER_" + name)
            or os.environ.get("SECOND_BRAIN_" + name)
            or default)


def home_dir() -> Path:
    """The brain's home — a single global directory OUTSIDE any repo, so every
    project on the machine shares one brain (memory, learned skills, configs,
    routines, definitions). Override with $MEMORY_LEDGER_HOME (or the legacy
    $SECOND_BRAIN_HOME). Default: ~/.second_brain."""
    env = _env("HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".second_brain"


# ---------------------------------------------------------------------------
# the on/off switch
#
# The ledger used to be "enabled" when its home dir existed, which made
# `rm -rf <home>` the off switch — the same act as deleting your data. Now the
# home dir is created automatically on first session (hooks/bootstrap.py), so
# absence can no longer mean "off": a switch that auto-init silently undoes is
# not a switch. Being OFF is therefore something you state explicitly, and it is
# stated in the one place auto-init will never overwrite.
# ---------------------------------------------------------------------------
DISABLE_MARKER = ".disabled"

# Named apart from the `_TRUTHY` used by the gauge coercion in graph.py: that
# module imports these names, and two constants sharing one name would shadow.
_OFF_TRUTHY = {"1", "true", "yes", "on"}


def disabled() -> bool:
    """True when the ledger has been explicitly turned off, by either switch:

    - `$MEMORY_LEDGER_OFF=1` — per-process, so it scopes to one shell, one CI
      job, one session. Env vars do not reach a hook or a cron you did not
      launch yourself, which is exactly why it is not the only switch.
    - `<home>/.disabled` — machine-wide and persistent; written by `graph.py
      disable` (`/ledger off`) and removed by `graph.py enable`.

    Every capture hook and the bootstrap check this first and no-op when true.
    """
    if _env("OFF").strip().lower() in _OFF_TRUTHY:
        return True
    try:
        return (home_dir() / DISABLE_MARKER).exists()
    except Exception:
        return False        # never let a stat() failure silence the ledger


def enabled() -> bool:
    return not disabled()
