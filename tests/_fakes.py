"""Fake programs for the suites, runnable on every OS.

A fake is a Python script. On macOS and Linux it is the file itself, with a
shebang and the exec bit; Windows runs neither, so there it is `<name>.py`
beside a `<name>.cmd` shim that runs it with this interpreter. Either way the
returned path is what goes in a config's `bin`.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


def make_exe(path: Path, source: str) -> Path:
    path = Path(path)
    if os.name == "nt":
        script = path.with_suffix(".py")
        script.write_text(source, encoding="utf-8")
        shim = path.with_suffix(".cmd")
        shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        return shim
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path
