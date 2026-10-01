#!/usr/bin/env python3
"""Run the B2B relay — the one command, like `scripts/serve.py` for the brain.

    python3 services/b2b/serve_b2b.py [--host 127.0.0.1] [--port 8780]

Stdlib only. The relay needs FastAPI, uvicorn and cryptography, so this file
builds a venv for it on first run from `requirements.txt` beside it, in the
cache directory, keyed by a hash of that file and the Python version, and runs
uvicorn from there. `$B2B_VENV` points it at a venv of your own instead.

The relay keeps everything in memory. Stopping this process loses every user
and every unread message; that is the design, not a missing feature.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"
READY = ".b2b-ready"


def _venv_key() -> str:
    """A hash of the pins and the interpreter: a change builds a new venv."""
    h = hashlib.sha256(REQUIREMENTS.read_bytes())
    h.update(f"{sys.version_info.major}.{sys.version_info.minor}".encode())
    return h.hexdigest()[:12]


def venv_dir() -> Path:
    """Where the relay's venv lives: `$B2B_VENV`, else the cache directory."""
    override = os.environ.get("B2B_VENV", "").strip()
    if override:
        return Path(override).expanduser()
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return cache / "memory-ledger" / f"b2b-{_venv_key()}"


def _venv_python(d: Path) -> Path:
    """The interpreter inside a venv."""
    return d / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _build_venv(d: Path) -> str:
    """Create `d` with the requirements installed. Returns "" or why not. Built
    in a temp directory and renamed into place, so a half-built one is never
    mistaken for a ready one."""
    d.parent.mkdir(parents=True, exist_ok=True)
    tmp = d.parent / f"{d.name}.building-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"setting up the B2B relay's environment (one time) in {d}", flush=True)
    try:
        subprocess.run([sys.executable, "-m", "venv", str(tmp)], check=True)
    except (subprocess.CalledProcessError, OSError) as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return f"could not create a venv ({e})"
    pip = [str(_venv_python(tmp)), "-m", "pip", "install", "--disable-pip-version-check",
           "--no-input", "-q", "-r", str(REQUIREMENTS)]
    if subprocess.run(pip).returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        return ("pip could not install services/b2b/requirements.txt — this needs the "
                "network once. Or point $B2B_VENV at a venv that has them.")
    (tmp / READY).write_text(_venv_key(), encoding="utf-8")
    try:
        tmp.rename(d)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        if not (d / READY).exists():
            return f"could not move the new environment into {d}"
    return ""


def relay_python() -> Path:
    """The interpreter the relay runs under, building its venv if needed."""
    d = venv_dir()
    if os.environ.get("B2B_VENV", "").strip():
        if not _venv_python(d).exists():
            sys.exit(f"b2b: $B2B_VENV={d} is not a venv")
        return _venv_python(d)
    if not (d / READY).exists():
        why = _build_venv(d)
        if why:
            sys.exit(f"b2b: the relay cannot start: {why}")
    return _venv_python(d)


def main(argv: list[str] | None = None) -> int:
    """Parse the flags and hand the process over to uvicorn."""
    ap = argparse.ArgumentParser(prog="serve_b2b.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default=os.environ.get("B2B_HOST", "127.0.0.1"),
                    help="0.0.0.0 to let other machines reach it (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("B2B_PORT", "8780")))
    ap.add_argument("--print-python", action="store_true",
                    help="build the venv if needed, print its interpreter, and exit")
    args = ap.parse_args(argv)
    py = relay_python()
    if args.print_python:
        print(py)
        return 0
    cmd = [str(py), "-m", "uvicorn", "b2b_app:app", "--app-dir", str(HERE),
           "--host", args.host, "--port", str(args.port), "--workers", "1"]
    sys.stdout.flush()
    sys.stderr.flush()
    # One worker, always: the store is this process's memory, and a second
    # worker would be a second, disjoint relay behind the same port.
    os.execv(str(py), cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
