#!/usr/bin/env python3
"""SessionStart hook — keep the usage-week nodes fresh, without anyone remembering.

Starts `scripts/usage_ingest.py run` as a DETACHED background process at most once
every six hours, then returns at once. The hook itself only stat()s one stamp file
and, when it is due, forks: it never parses a log, never touches the graph, never
waits, never calls a model, and prints nothing (the protocol wants valid JSON or
silence). Wrapped so it always exits 0 — a broken ingest must never block a
session. Opt out with $MEMORY_LEDGER_NO_USAGE_INGEST=1 or "usage_ingest": false in
settings.json; a ledger that is switched off or not yet created is left alone.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))


def main():
    try:
        import usage_ingest  # noqa: PLC0415
        usage_ingest.spawn()
    except Exception:
        pass  # never block a session


if __name__ == "__main__":
    main()
    sys.exit(0)
