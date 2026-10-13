"""Text I/O that reads the same on every OS.

Python's default text encoding is the locale's: UTF-8 on macOS and Linux, a
cp125x code page on Windows. Everything the brain reads or writes is UTF-8, so
every text-mode call names it, and a subprocess decodes with
`errors="replace"`: a child that prints a byte that is not UTF-8 costs one
U+FFFD, never a "'charmap' codec can't decode" crash. tests/test_encoding.py
fails on any text-mode call that does not.

Stdlib only; safe to import from the engine, the hooks and the API.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Mapping

ENCODING = "utf-8"
TEXT: dict[str, Any] = {"text": True, "encoding": ENCODING, "errors": "replace"}


def child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for a child process: `env` (or ours) with Python in UTF-8 mode.

    A Python child then reads and writes UTF-8 whatever the console code page is.
    """
    out = dict(os.environ if env is None else env)
    out["PYTHONUTF8"] = "1"
    out["PYTHONIOENCODING"] = ENCODING
    return out


def run_text(cmd: Any, **kw: Any) -> subprocess.CompletedProcess:
    """subprocess.run with captured, UTF-8 decoded output and a UTF-8 child env.

    Any subprocess.run keyword passes through; capture_output defaults to True
    unless stdout or stderr is given.
    """
    if "stdout" not in kw and "stderr" not in kw:
        kw.setdefault("capture_output", True)
    kw["env"] = child_env(kw.get("env"))
    return subprocess.run(cmd, **TEXT, **kw)


def popen_text(cmd: Any, **kw: Any) -> subprocess.Popen:
    """subprocess.Popen in UTF-8 text mode with a UTF-8 child env."""
    kw["env"] = child_env(kw.get("env"))
    return subprocess.Popen(cmd, **TEXT, **kw)
