#!/usr/bin/env python3
"""scripts/setup.py, against a THROWAWAY home, with the slow steps skipped.

    python3 tests/test_setup.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILS: list[str] = []


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


home = tempfile.mkdtemp(prefix="second-brain-setup-")
env = {**os.environ, "SECOND_BRAIN_HOME": home, "SECOND_BRAIN_NO_AUTOSYNC": "1"}
env.pop("SECOND_BRAIN_NO_SKILL_SEED", None)
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "setup.py"), "--json", "--skip-venv", "--skip-ui"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=600)
try:
    rep = json.loads(r.stdout)
except ValueError:
    rep = {}
ck("stdout is one JSON report, whatever the steps print", bool(rep), r.stdout[-300:] + r.stderr[-300:])
by = {s["step"]: s for s in rep.get("steps", [])}
ck("it walks every step, in order",
   [s["step"] for s in rep.get("steps", [])][:3] == ["python", "server venv", "ui"]
   and {"tts engine", "stt", "brain", "doctor"} <= set(by), str(list(by)))
ck("a skipped step is neither ok nor a failure", by.get("ui", {}).get("ok") is None)
ck("the brain is created, with the built-in skills", by.get("brain", {}).get("ok") is True
   and Path(home, "graph.db").exists(), str(by.get("brain")))
ck("doctor runs last and decides", rep.get("steps", [{}])[-1].get("step") == "doctor")
ck("a missing TTS engine or STT Python is reported, never a failure",
   by.get("tts engine", {}).get("ok") is not False and by.get("stt", {}).get("ok") is not False)
ck("the exit code follows the report", (r.returncode == 0) == (rep.get("ok") is True), f"rc={r.returncode}")
ck("and here it is ready", rep.get("ok") is True, str(rep.get("failed")))

r2 = subprocess.run([sys.executable, str(ROOT / "scripts" / "setup.py"), "--json", "--skip-venv", "--skip-ui"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=600)
ck("running it again is a no-op that still passes", r2.returncode == 0 and json.loads(r2.stdout)["ok"])

bash = shutil.which("bash")
if bash and os.name != "nt":
    ck("setup.sh parses", subprocess.run([bash, "-n", str(ROOT / "scripts" / "setup.sh")]).returncode == 0)
ps = shutil.which("pwsh") or shutil.which("powershell")
if ps:
    chk = ("$e = $null; [System.Management.Automation.Language.Parser]::ParseFile("
           f"'{ROOT / 'scripts' / 'setup.ps1'}', [ref]$null, [ref]$e) | Out-Null; exit $e.Count")
    ck("setup.ps1 parses", subprocess.run([ps, "-NoProfile", "-Command", chk]).returncode == 0)

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
raise SystemExit(1 if FAILS else 0)
