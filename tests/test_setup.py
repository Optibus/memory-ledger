"""scripts/setup.py, against a THROWAWAY home, with the slow steps skipped.

    python -m pytest tests/test_setup.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SETUP_PY = ROOT / "scripts" / "setup.py"
SETUP_SH = ROOT / "scripts" / "setup.sh"
SETUP_PS1 = ROOT / "scripts" / "setup.ps1"
SKIP_SLOW = ("--json", "--skip-venv", "--skip-ui")
UTF8 = "utf-8"
SETUP_TIMEOUT_S = 600


def _setup(home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "SECOND_BRAIN_HOME": str(home), "SECOND_BRAIN_NO_AUTOSYNC": "1"}
    env.pop("SECOND_BRAIN_NO_SKILL_SEED", None)
    return subprocess.run([sys.executable, str(SETUP_PY), *SKIP_SLOW], capture_output=True,
                          text=True, encoding=UTF8, errors="replace", env=env,
                          timeout=SETUP_TIMEOUT_S)


@pytest.fixture(scope="module")
def first_run(module_brain_home: Path):
    """The first `setup.py` over a fresh home: the process and its parsed report."""
    proc = _setup(module_brain_home)
    try:
        report = json.loads(proc.stdout)
    except ValueError:
        report = {}
    assert report, "stdout is one JSON report, whatever the steps print: " + proc.stdout[-300:] + proc.stderr[-300:]
    return proc, report


@pytest.fixture(scope="module")
def steps(first_run) -> dict[str, dict]:
    return {s["step"]: s for s in first_run[1].get("steps", [])}


def test_it_walks_every_step_in_order(first_run, steps):
    order = [s["step"] for s in first_run[1]["steps"]]
    assert order[:3] == ["python", "server venv", "ui"]
    assert {"tts engine", "stt", "brain", "doctor"} <= set(steps)


def test_a_skipped_step_is_neither_ok_nor_a_failure(steps):
    assert steps["ui"].get("ok") is None


def test_the_brain_is_created_with_the_builtin_skills(steps, module_brain_home):
    assert steps["brain"].get("ok") is True
    assert (module_brain_home / "graph.db").exists()


def test_doctor_runs_last_and_decides(first_run):
    assert first_run[1]["steps"][-1]["step"] == "doctor"


@pytest.mark.parametrize("step", ["tts engine", "stt"])
def test_a_missing_optional_engine_is_reported_never_a_failure(steps, step):
    assert steps[step].get("ok") is not False


def test_the_exit_code_follows_the_report(first_run):
    proc, report = first_run
    assert (proc.returncode == 0) == (report.get("ok") is True)


def test_here_it_is_ready(first_run):
    assert first_run[1].get("ok") is True, first_run[1].get("failed")


def test_running_it_again_is_a_no_op_that_still_passes(first_run, module_brain_home):
    again = _setup(module_brain_home)
    assert again.returncode == 0
    assert json.loads(again.stdout)["ok"]


@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="needs bash on POSIX")
def test_setup_sh_parses():
    assert subprocess.run([shutil.which("bash"), "-n", str(SETUP_SH)]).returncode == 0


@pytest.mark.skipif(not (shutil.which("pwsh") or shutil.which("powershell")), reason="needs PowerShell")
def test_setup_ps1_parses():
    ps = shutil.which("pwsh") or shutil.which("powershell")
    check = ("$e = $null; [System.Management.Automation.Language.Parser]::ParseFile("
             f"'{SETUP_PS1}', [ref]$null, [ref]$e) | Out-Null; exit $e.Count")
    assert subprocess.run([ps, "-NoProfile", "-Command", check]).returncode == 0
