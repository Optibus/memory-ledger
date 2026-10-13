#!/usr/bin/env python3
"""What the hooks put into context, and the opt-in gated per-prompt recall.

    python3 tests/test_prompt_recall.py

Stdlib only. Runs the real hooks against a THROWAWAY brain. What it guards: the
hooks print `additionalContext` under `hookSpecificOutput` (the only shape
Claude Code reads), the per-prompt hook is silent by default, "capture is on"
is said once at SessionStart, and gated recall is off until asked, injects at
most two short snippets, never the same node twice in a session, never a
personal node, never into a respond session, and logs what it injected.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAIN = tempfile.mkdtemp(prefix="second-brain-recall-")
ENV = {**os.environ, "SECOND_BRAIN_HOME": BRAIN}
for _k in list(ENV):
    if _k.startswith("MEMORY_LEDGER_NO_") or _k == "MEMORY_LEDGER_REQUEST_ID":
        del ENV[_k]
ENV["SECOND_BRAIN_NO_AUTOSYNC"] = "1"
ENV["MEMORY_LEDGER_NO_CONSOLIDATE"] = "1"
FAILS = []


def ck(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def g(*a):
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "graph.py"), *a],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"ok": False, "raw": p.stdout[:300], "stderr": p.stderr[:300]}


def say(session, prompt, **env):
    """A prompt through the real hook; returns the parsed stdout, or None."""
    p = subprocess.run([sys.executable, str(ROOT / "hooks" / "capture_prompt.py")],
                       input=json.dumps({"session_id": session, "cwd": "/tmp", "prompt": prompt}),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env={**ENV, **env}, check=True)
    return json.loads(p.stdout) if p.stdout.strip() else None


def context(out):
    return ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")


def settings(**kv):
    Path(BRAIN, "settings.json").write_text(json.dumps(kv), encoding="utf-8")


ASK = "what projection does the shapefile export use?"
dec = g("add-node", "--type", "decision",
        "--title", "Shapefile export uses EPSG 4326 projection",
        "--body", "Decided with the team: shapefile exports keep WGS84 coordinates. " * 10)
DEC = (dec.get("node") or {}).get("id", "")
ck("seed decision created", bool(DEC), str(dec)[:160])
g("add-node", "--type", "note", "--title", "Banana bread recipe",
  "--body", "flour sugar butter", "--realm", "personal")

# --- the SessionStart brief ------------------------------------------------
p = subprocess.run([sys.executable, str(ROOT / "hooks" / "open_loops_brief.py")],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
try:
    brief = json.loads(p.stdout)
except Exception:
    brief = {}
hso = brief.get("hookSpecificOutput") or {}
ck("brief uses hookSpecificOutput", hso.get("hookEventName") == "SessionStart"
   and "additionalContext" not in brief, p.stdout[:160])
ck("brief says capture is on, once", hso.get("additionalContext", "").count("Capture is on") == 1)

# --- off by default ----------------------------------------------------------
ck("per-prompt hook is silent by default", say("s1", ASK) is None)

# --- on ------------------------------------------------------------------------
settings(prompt_recall=True)
out = say("s2", ASK)
ctx = context(out)
ck("a clear match is injected", f"[n:{DEC}]" in ctx, ctx[:160])
ck("under hookSpecificOutput for UserPromptSubmit",
   (out or {}).get("hookSpecificOutput", {}).get("hookEventName") == "UserPromptSubmit")
snips = [line.split(") ", 1)[-1] for line in ctx.splitlines() if line.startswith("[n:")]
ck("each snippet is capped", snips and all(len(s) <= 200 for s in snips),
   str([len(s) for s in snips]))
ck("at most two snippets", ctx.count("[n:") <= 2)
ck("never the same node twice in a session", say("s2", ASK) is None)
ck("a weak match injects nothing",
   say("s3", "write a python script that parses yaml export files") is None)
ck("a past-work cue lowers the bar to one term",
   f"[n:{DEC}]" in context(say("s4", "what did we decide about shapefile?")))
ck("a ticket key is a past-work cue", f"[n:{DEC}]" in context(say("s8", "OS-1234 shapefile")))
ck("utf-8 is not a ticket key, so one term is not enough",
   say("s9", "shapefile utf-8 encoding broke in the importer") is None)
ck("a lone 'before' or 'again' is not a past-work cue",
   say("s10", "run the linter before the shapefile build again") is None)
ck("a long paste is skipped", say("s11", ("shapefile export projection " * 100)) is None)
ck("terms match whole words, not substrings",
   say("s12", "because port the shape") is None)
ck("a personal node is never injected", say("s5", "banana bread recipe flour butter") is None)
ck("never into a respond session",
   say("s6", ASK, MEMORY_LEDGER_REQUEST_ID="req-1") is None)
ck("the env escape hatch turns it off", say("s7", ASK, MEMORY_LEDGER_NO_PROMPT_RECALL="1") is None)

caps = g("search", "shapefile", "--type", "capture", "--full").get("results") or []
logged = [c for c in caps if (c.get("props") or {}).get("recalled")]
ck("every injection is logged on its capture node",
   sorted(c["props"]["session_id"] for c in logged) == ["s2", "s4", "s8"],
   str([(c["props"].get("session_id"), c["props"].get("recalled")) for c in caps]))

print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all passed'}")
sys.exit(1 if FAILS else 0)
