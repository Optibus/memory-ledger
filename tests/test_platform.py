#!/usr/bin/env python3
"""What differs between macOS, Linux and Windows (scripts/_platform.py), against a THROWAWAY home.

    python3 tests/test_platform.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(tempfile.mkdtemp(prefix="second-brain-platform-"))
os.environ["SECOND_BRAIN_HOME"] = str(HOME)
os.environ.pop("SECOND_BRAIN_DB", None)
sys.path.insert(0, str(ROOT / "scripts"))

import _platform  # noqa: E402
import graph  # noqa: E402
import trigger  # noqa: E402

FAILS: list[str] = []


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


print("finding programs")
ck("a path is used as given, with ~ expanded",
   _platform.resolve_bin("~/bin/claude") == str(Path("~/bin/claude").expanduser()))
ck("a bare name is looked up on PATH", _platform.resolve_bin("python3") == (shutil.which("python3") or "python3"))
ck("a name that is nowhere comes back as is, so the error names it",
   _platform.resolve_bin("no-such-program-xyz") == "no-such-program-xyz")
ck("the trigger engine resolves claude the same way",
   trigger.claude_bin({"claude": {"bin": "no-such-program-xyz"}}) == "no-such-program-xyz")

print("interpreters")
ck("a .py runs with this Python", _platform.interpreter("python", Path("x.py")) == [sys.executable])
sh = _platform.interpreter("shell", Path("x.sh"))
ck("a .sh runs with the bash on PATH", sh is not None and Path(sh[0]).name in ("bash", "bash.exe", "sh"), str(sh))
ps = _platform.interpreter("powershell", Path("x.ps1"))
ck("a .ps1 runs with PowerShell, by file, without the profile, or not at all",
   ps is None if not _platform.powershell() else (ps[-1] == "-File" and "-NoProfile" in ps), str(ps))
ck("a .bat runs with cmd on Windows only",
   (_platform.interpreter("batch", Path("x.bat")) is None) != _platform.WINDOWS)
ck("an unknown language has no interpreter", _platform.interpreter("text", Path("x.txt")) is None)
ck("graph knows .ps1 and .bat", graph._script_lang("a.ps1", "") == "powershell"
   and graph._script_lang("a.bat", "") == "batch" and graph._script_lang("a.cmd", "") == "batch")

print("a script that is not executable runs with its interpreter")
graph.scripts_dir().mkdir(parents=True, exist_ok=True)
script = graph.scripts_dir() / "hello.sh"
script.write_text('echo "olá $1"\n', encoding="utf-8")
script.chmod(0o644)
argv, err = trigger.script_argv("hello.sh")
ck("its argv is the interpreter and the path, as separate items",
   not err and argv[-1] == str(script) and len(argv) == 2, f"{argv} {err}")
if argv and sh:
    out = subprocess.run([*argv, "mundo; echo injected"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    ck("an argument is data, never shell text", out.strip() == "olá mundo; echo injected", out)

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
raise SystemExit(1 if FAILS else 0)
