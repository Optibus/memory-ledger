"""What differs between macOS, Linux and Windows, in one place.

Finding a program on PATH (`claude` is `claude.cmd` under npm on Windows),
choosing the interpreter for a script by its kind (bash from PATH, PowerShell
for a .ps1, cmd for a .bat), and nothing built as a string to hand a shell.

Stdlib only; safe to import from the engine, the hooks and the API.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

WINDOWS = os.name == "nt"


def resolve_bin(name: str) -> str:
    """The program to run for `name`: a path as given (with `~` expanded), or
    the first match on PATH. On Windows `shutil.which` follows PATHEXT, so
    `claude` finds `claude.cmd` or `claude.exe`. Falls back to `name` itself,
    so the error a caller reports names what was asked for."""
    name = str(name or "").strip()
    if not name:
        return name
    if os.sep in name or (os.altsep and os.altsep in name) or name.startswith("~"):
        return str(Path(name).expanduser())
    return shutil.which(name) or name


def bash() -> str | None:
    """A bash to run a shell script with: PATH first (Git Bash, WSL's, Homebrew's),
    then the usual places, then sh. None when there is no POSIX shell at all."""
    found = shutil.which("bash")
    if found:
        return found
    for cand in ("/bin/bash", "/usr/bin/bash", "/usr/local/bin/bash", "/opt/homebrew/bin/bash"):
        if Path(cand).exists():
            return cand
    return shutil.which("sh") or ("/bin/sh" if Path("/bin/sh").exists() else None)


def powershell() -> str | None:
    """PowerShell 7 (pwsh) when installed, else Windows PowerShell."""
    return shutil.which("pwsh") or shutil.which("powershell")


def python3() -> list[str]:
    """argv prefix that runs a Python 3: this interpreter, else `py -3` on
    Windows, else python3 on PATH."""
    if sys.executable:
        return [sys.executable]
    if WINDOWS and shutil.which("py"):
        return [shutil.which("py") or "py", "-3"]
    return [shutil.which("python3") or shutil.which("python") or "python3"]


def interpreter(lang: str, path: Path) -> list[str] | None:
    """argv prefix that runs the script at `path`, whose language is `lang`
    (graph._script_lang). None when this machine has nothing that runs it."""
    suffix = path.suffix.lower()
    if lang == "powershell" or suffix == ".ps1":
        ps = powershell()
        return [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"] if ps else None
    if lang == "batch" or suffix in (".bat", ".cmd"):
        return [os.environ.get("COMSPEC") or "cmd.exe", "/d", "/c"] if WINDOWS else None
    if lang == "python":
        return python3()
    if lang == "shell":
        sh = bash()
        return [sh] if sh else None
    prog = {"javascript": "node", "ruby": "ruby", "perl": "perl"}.get(lang)
    if prog:
        found = shutil.which(prog)
        return [found] if found else None
    return None
