#!/usr/bin/env python3
"""The learned-rules detector (CONVENTIONS §2i): it proposes, a person decides.

    python3 tests/test_learned_rules.py

Stdlib only. Runs the real CLI and the real hooks against a THROWAWAY brain,
with synthetic prompts. What it guards: the switch is OFF until asked, a
proposal is never returned by match-rules, accepting is the only road to an
active learned rule, rejecting sticks, and nothing is ever deleted.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAIN = tempfile.mkdtemp(prefix="second-brain-learned-")
ENV = {**os.environ, "SECOND_BRAIN_HOME": BRAIN, "SECOND_BRAIN_NO_AUTOSYNC": "1"}
for _k in list(ENV):
    if _k.startswith(("MEMORY_LEDGER_NO_", "SECOND_BRAIN_NO_LEARNED")):
        del ENV[_k]
ENV["SECOND_BRAIN_NO_AUTOSYNC"] = "1"
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


def say(session, prompt):
    """A prompt, captured by the real hook."""
    subprocess.run([sys.executable, str(ROOT / "hooks" / "capture_prompt.py")],
                   input=json.dumps({"session_id": session, "cwd": "/tmp", "prompt": prompt}),
                   capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV, check=True)


def count(sql):
    import sqlite3
    con = sqlite3.connect(os.path.join(BRAIN, "graph.db"))
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


# --- the pure heuristic ---------------------------------------------------
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["SECOND_BRAIN_HOME"] = BRAIN
import graph  # noqa: E402

print("the heuristic")
ce = graph.correction_of
ck("an English correction is spotted", ce("No, never use emojis in commit messages"))
ck("a Portuguese one too", ce("não, nunca use emojis nas mensagens de commit"))
ck("'pare de' opens a correction", ce("Pare de adicionar comentarios nos arquivos gerados"))
ck("'always' opens one", ce("Always run the linter before committing changes"))
ck("'I said' opens one", ce("I said keep the changelog entries short and factual"))
ck("an ordinary request is not one", ce("Please add a retry to the upload function") is None)
ck("'no' inside a sentence is not one", ce("There is no way to know which file changed") is None)
ck("a question is not one", ce("Never mind, can you explain the merge logic here?") is None)
ck("too vague is not one", ce("No, stop that") is None)
ck("a long prompt is a task, not a correction", ce("Don't " + "touch the parser " * 40) is None)
ck("code is not a correction", ce("never use this:\n```\nx = 1\n```") is None)
ck("never and always do not cluster",
   graph._lr_jaccard(ce("never squash the release commits")["tokens"],
                     ce("always squash the release commits")["tokens"]) < graph.LR_SIMILARITY)

print("off by default")
st = g("learned-rules", "status")
ck("status reports off", st.get("ok") and st.get("enabled") is False, str(st)[:160])
for s in ("s1", "s2", "s3"):
    say(s, "No, never use emojis in the commit messages please")
say("s1", "Don't forget to add the tests for the new parser module")  # a to-do, not a correction
say("s1", "Always run the formatter before you open the pull request")  # once only
off = g("propose-rules")
ck("propose-rules says it is off and how to turn it on",
   off.get("enabled") is False and off.get("ran") is False and "learned-rules on" in off.get("hint", ""),
   str(off)[:200])
ck("nothing was proposed while off", count("SELECT COUNT(*) FROM nodes WHERE type='rule-proposal'") == 0)
ck("the settings file holds no switch", "learned_rules" not in
   json.loads(Path(BRAIN, "settings.json").read_text(encoding="utf-8")) if Path(BRAIN, "settings.json").exists() else True)

print("on: repeated corrections across sessions are proposed")
on = g("learned-rules", "on")
ck("turning it on is stored and reported", on.get("enabled") is True and on.get("source") == "settings")
dry = g("propose-rules", "--dry-run")
ck("a dry run reports the cluster", len(dry.get("proposed", [])) == 1 and dry.get("dry_run") is True, str(dry)[:200])
ck("and writes nothing", count("SELECT COUNT(*) FROM nodes WHERE type='rule-proposal'") == 0)
run = g("propose-rules")
props = run.get("proposed", [])
ck("the repeated correction is proposed", len(props) == 1, str(run)[:200])
ck("the once-only correction is not", all("formatter" not in p["text"] for p in props))
ck("the to-do is not", all("tests" not in p["text"] for p in props))
pid = props[0]["id"] if props else ""
ck("it carries its evidence and counts",
   props and props[0]["count"] == 3 and props[0]["sessions"] == 3 and len(props[0]["evidence"]) == 3)
node = g("get-node", "--id", pid)
ck("stored as a rule-proposal with status proposed",
   (node.get("node") or node).get("type") == "rule-proposal" and (node.get("node") or node).get("status") == "proposed",
   str(node)[:160])
ck("linked to the prompts it came from",
   count(f"SELECT COUNT(*) FROM edges WHERE src='{pid}' AND rel='learned-from'") == 3)
again = g("propose-rules")
ck("a second run proposes nothing new", again.get("proposed") == [])
ck("still one proposal", count("SELECT COUNT(*) FROM nodes WHERE type='rule-proposal'") == 1)

print("one occurrence, or one session, is not enough")
say("s9", "Never commit directly to the release branch without review")
ck("a single occurrence is not proposed",
   all("release branch" not in p["text"] for p in g("propose-rules", "--dry-run").get("proposed", [])))
say("s9", "Never commit directly to the release branch without review")
ck("two in ONE session is not proposed either",
   all("release branch" not in p["text"] for p in g("propose-rules", "--dry-run").get("proposed", [])))
say("s10", "Never commit directly to the release branch without review")
ck("a second session makes it a proposal",
   any("release branch" in p["text"] for p in g("propose-rules", "--dry-run").get("proposed", [])))

print("a proposal is not a rule")
lst = g("rule-proposals")
ck("rule-proposals lists it, budgeted",
   lst.get("count") == 1 and lst.get("budget") and "chars" in lst and lst.get("truncated") is False
   and lst["proposals"][0]["id"] == pid, str(lst)[:160])
mr = g("match-rules", "never use emojis commit messages", "--full")
ck("match-rules does not return a proposal", pid not in json.dumps(mr) and all(
   r["props"].get("source") != "learned" for r in mr.get("results", [])))

print("accept")
g("add-rule", "--title", "Commit message emojis", "--source", "user", "--scenario",
  "emojis commit messages", "--body", "THE RULE\n\nUser version.\n\nJUDGED AGAINST\n\nn/a")
acc = g("accept-rule", "--id", pid, "--title", "No emojis in commit messages",
        "--text", "Never put emojis in commit messages.")
ck("accept makes a learned rule at precedence 50",
   acc.get("ok") and acc.get("source") == "learned" and acc.get("precedence") == 50, str(acc)[:200])
rid = acc.get("rule", "")
full = g("match-rules", "emojis commit messages", "--full")
learned = [r for r in full.get("results", []) if r["id"] == rid]
ck("the rule now matches, as learned", learned and learned[0]["props"]["source"] == "learned"
   and learned[0]["props"]["precedence"] == 50 and "Never put emojis" in learned[0]["body"],
   str([r["id"] for r in full.get("results", [])]))
ids = [r["id"] for r in full.get("results", [])]
user_ids = [r["id"] for r in full.get("results", []) if r["props"].get("source") == "user"]
ck("a user rule ranks above it", user_ids and ids.index(user_ids[0]) < ids.index(rid), str(ids))
ck("the proposal is marked accepted and kept",
   g("rule-proposals", "--status", "accepted").get("count") == 1)
ck("the rule is linked to its proposal and evidence",
   count(f"SELECT COUNT(*) FROM edges WHERE src='{rid}' AND rel='learned-from'") >= 4)
ck("accepting twice is refused", g("accept-rule", "--id", pid).get("ok") is False)

print("reject")
rp = [p for p in g("rule-proposals").get("proposals", []) if "release branch" in p["text"]]
ck("the release-branch proposal exists", len(rp) == 0)   # not yet proposed: no run since
g("propose-rules")
rp = [p for p in g("rule-proposals").get("proposals", []) if "release branch" in p["text"]]
ck("now it is proposed", len(rp) == 1)
rej = g("reject-rule", "--id", rp[0]["id"], "--reason", "not a rule")
ck("reject marks it rejected and kept", rej.get("ok") and rej.get("kept") is True, str(rej)[:160])
say("s11", "Never commit directly to the release branch without a review")
before = count("SELECT COUNT(*) FROM nodes WHERE type='rule-proposal'")
run = g("propose-rules")
ck("a rejected cluster is not proposed again, even reworded",
   run.get("proposed") == [] and count("SELECT COUNT(*) FROM nodes WHERE type='rule-proposal'") == before,
   str(run)[:200])
ck("match-rules never returns a rejected proposal",
   rp[0]["id"] not in json.dumps(g("match-rules", "release branch commit review", "--full")))

print("nothing is deleted")
ck("every capture is still there", count("SELECT COUNT(*) FROM nodes WHERE type='capture'") >= 9)
ck("both proposals are still there", count("SELECT COUNT(*) FROM nodes WHERE type='rule-proposal'") == 2)

print("off again stops the detector, keeps the data")
g("learned-rules", "off")
say("s12", "Never squash the release commits into one big commit")
say("s13", "Never squash the release commits into one big commit")
ck("off: propose-rules is a no-op", g("propose-rules").get("ran") is False)
ck("off: the proposals are still listed", g("rule-proposals", "--status", "all").get("of") == 2)
ENV2 = {**ENV, "MEMORY_LEDGER_NO_LEARNED_RULES": "1"}
g("learned-rules", "on")
p = subprocess.run([sys.executable, str(ROOT / "scripts" / "graph.py"), "learned-rules", "status"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV2)
ck("the NO_ env var outranks the file", json.loads(p.stdout).get("enabled") is False
   and json.loads(p.stdout).get("source") == "env")

print("the SessionStart hook")
g("learned-rules", "off")
stamp = Path(BRAIN, ".last_learned_rules")
hp = subprocess.run([sys.executable, str(ROOT / "hooks" / "open_loops_brief.py")],
                    input=json.dumps({"session_id": "h"}), capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
ck("off: the hook stamps nothing and runs nothing", not stamp.exists())
try:
    json.loads(hp.stdout or "{}")
    ck("off: the hook still emits valid JSON", True)
except ValueError:
    ck("off: the hook still emits valid JSON", False, hp.stdout[:120])
g("learned-rules", "on")
hp = subprocess.run([sys.executable, str(ROOT / "hooks" / "open_loops_brief.py")],
                    input=json.dumps({"session_id": "h"}), capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
ck("on: the hook stamps the day", stamp.exists())
try:
    json.loads(hp.stdout or "{}")
    ck("on: and its stdout is still valid JSON", True)
except ValueError:
    ck("on: and its stdout is still valid JSON", False, hp.stdout[:120])

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all passed")
