"""The writes that run `graph.py` out of process, and the files they pass it.

Stdlib only, like `payloads`: nothing here needs the web framework, so the
contract suite can exercise it without the server's environment.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
from pathlib import Path

import graph

GRAPH_PY = Path(__file__).resolve().parent.parent / "graph.py"

# An uploaded bundle is a zip of markdown, so a few MB is already generous. The
# cap exists because the upload arrives base64-encoded in one JSON body, which is
# read into memory whole: without a ceiling a single POST can exhaust the process.
MAX_BUNDLE_BYTES = 32 * 1024 * 1024


def graph_cmd(argv, timeout=300) -> dict:
    """Run a graph.py command out of process and hand back its JSON.

    These commands report by printing, and a long scan must not be able to
    wedge the server's own connection."""
    cmd = [sys.executable, str(GRAPH_PY), *argv]
    try:
        pr = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"{argv[0]} timed out"}
    if pr.returncode != 0:
        return {"ok": False,
                "error": (pr.stderr or pr.stdout or "failed").strip()[-400:]}
    try:
        return json.loads(pr.stdout)
    except ValueError:
        return {"ok": False, "error": (pr.stdout or "no output").strip()[-400:]}


def maintain(confirm, keep_days) -> dict:
    """The shared body of prune and consolidate: `graph.py maintain`.

    Never runs on a bare POST: it rewrites props on every capture it touches,
    and "non-destructive" is a promise about the data, not about whether you
    wanted it. The page previews first (`GET /api/consolidate-preview`) and
    sends `confirm: true` only after a human has read what it would do."""
    if confirm is not True:
        return {"ok": False,
                "error": "refused: this rewrites every capture it "
                         "consolidates. Preview it first "
                         "(GET /api/consolidate-preview), then send "
                         "confirm: true.",
                "needs_confirmation": True}
    try:
        keep = max(0, min(365, int(keep_days)))
    except (TypeError, ValueError):
        return {"ok": False, "error": "keep_days must be a whole number"}
    res = graph_cmd(["maintain", "--keep-days", str(keep)])
    if res.get("error") == "maintain timed out":
        res["error"] = "prune timed out after 5 minutes"
    return res


# ------------------------------------------------- bundles (§5a, selective)
# The selection travels as a JSON spec file rather than as argv: a person can
# tick a hundred rules, and a hundred --rule flags is an argv length limit
# waiting to be hit on the one export that mattered.
def incoming_dir() -> Path:
    d = graph.bundles_dir() / "incoming"
    d.mkdir(parents=True, exist_ok=True)
    return d


def spec_file(payload) -> Path:
    p = incoming_dir() / f"spec-{secrets.token_hex(8)}.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def staged(token) -> Path | None:
    """The staged upload a token names, or None.

    The token is matched against a strict pattern and then used as a BARE
    FILENAME inside one directory. A token is a value a client sends, and a
    client that sends `../../graph.db` must not be able to name a file outside
    the staging area."""
    if not re.fullmatch(r"[0-9a-f]{8,64}", token or ""):
        return None
    p = graph.bundles_dir() / "incoming" / f"{token}.zip"
    return p if p.is_file() else None


def bundle_path(name: str) -> Path | None:
    """An exported bundle by name, or None.

    `name` is reduced to a BARE FILENAME before it is used. The query string is
    client input, and `?name=../graph.db` on a route that joins it onto a
    directory would serve the whole brain to anyone who can reach the port."""
    name = Path(name or "").name
    path = graph.bundles_dir() / name
    if not name.endswith(".zip") or not path.is_file():
        return None
    return path
