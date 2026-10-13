#!/usr/bin/env python3
"""A brain made from scratch carries the built-in skills, against THROWAWAY homes.

    python3 tests/test_skill_seed.py
"""

from __future__ import annotations

import json
import os
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


def run(script: str, *args: str, home: str, **env: str) -> subprocess.CompletedProcess:
    e = {**os.environ, "SECOND_BRAIN_HOME": home, "SECOND_BRAIN_NO_AUTOSYNC": "1"}
    e.pop("SECOND_BRAIN_NO_SKILL_SEED", None)
    e.update(env)
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=e)


builtin = {p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}

home = tempfile.mkdtemp(prefix="second-brain-seed-")
r = run("graph.py", "stats", home=home)
st = json.loads(r.stdout or "{}")
ck("the first command on a fresh home seeds every built-in skill",
   st.get("by_type", {}).get("skill") == len(builtin), f"{st.get('by_type')} vs {len(builtin)}")
first = Path(home, "graph.db").stat().st_mtime_ns
run("graph.py", "stats", home=home)
ck("an existing brain is not re-seeded on connect", Path(home, "graph.db").stat().st_mtime_ns == first)

init = json.loads(run("graph.py", "init", home=home).stdout or "{}")
ck("init on a seeded brain is a no-op", init.get("skills", {}).get("ingested") == 0, str(init.get("skills")))

subprocess.run([sys.executable, "-c", "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]);"
                "c.execute(\"DELETE FROM nodes WHERE id='skill_recall'\"); c.commit()",
                str(Path(home, "graph.db"))], check=True)
init = json.loads(run("graph.py", "init", home=home).stdout or "{}")
ck("init brings back a built-in that is missing",
   init.get("skills", {}).get("missing") == [] and init["skills"].get("ingested") == 1, str(init.get("skills")))

subprocess.run([sys.executable, "-c", "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]);"
                "c.execute(\"DELETE FROM nodes WHERE id='skill_recall'\"); c.commit()",
                str(Path(home, "graph.db"))], check=True)
doc = json.loads(run("doctor.py", "--json", home=home).stdout or "{}")
ck("doctor brings it back too, and says so", (doc.get("skills") or {}).get("ingested") == 1, str(doc.get("skills")))
ck("and the brain is healthy after", doc.get("ok") is True, str(doc.get("problems")))

off = tempfile.mkdtemp(prefix="second-brain-noseed-")
st = json.loads(run("graph.py", "stats", home=off, SECOND_BRAIN_NO_SKILL_SEED="1").stdout or "{}")
ck("SECOND_BRAIN_NO_SKILL_SEED=1 leaves a fresh brain empty", not st.get("by_type", {}).get("skill"), str(st))

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
raise SystemExit(1 if FAILS else 0)
