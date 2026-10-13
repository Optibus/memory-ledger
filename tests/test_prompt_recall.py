"""What the hooks put into context, and the opt-in gated per-prompt recall.

    python -m pytest tests/test_prompt_recall.py

Runs the real hooks against a THROWAWAY brain. What it guards: the hooks print
`additionalContext` under `hookSpecificOutput` (the only shape Claude Code
reads), the per-prompt hook is silent by default, "capture is on" is said once
at SessionStart, and gated recall is off until asked, injects at most two short
snippets, never the same node twice in a session, never a personal node, never
into a respond session, and logs what it injected.

One brain for the module; the classes run in file order (silent by default,
then the switch is turned on).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GRAPH_PY = ROOT / "scripts" / "graph.py"
CAPTURE_HOOK = ROOT / "hooks" / "capture_prompt.py"
BRIEF_HOOK = ROOT / "hooks" / "open_loops_brief.py"
UTF8 = "utf-8"
ASK = "what projection does the shapefile export use?"
REQUEST_VAR = "MEMORY_LEDGER_REQUEST_ID"
OFF_VAR = "MEMORY_LEDGER_NO_PROMPT_RECALL"
SNIPPET_MAX_CHARS = 200
MAX_SNIPPETS = 2


class Brain:
    def __init__(self, home: Path) -> None:
        self.home = home
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("MEMORY_LEDGER_NO_") and k != REQUEST_VAR}
        env.update(SECOND_BRAIN_HOME=str(home), SECOND_BRAIN_NO_AUTOSYNC="1",
                   MEMORY_LEDGER_NO_CONSOLIDATE="1")
        self.env = env

    def _run(self, script: Path, *args: str, stdin: str | None = None, env: dict | None = None,
             check: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(script), *args], input=stdin, capture_output=True,
                              text=True, encoding=UTF8, errors="replace", env=env or self.env, check=check)

    def g(self, *args: str) -> dict:
        p = self._run(GRAPH_PY, *args)
        try:
            return json.loads(p.stdout)
        except ValueError:
            return {"ok": False, "raw": p.stdout[:300], "stderr": p.stderr[:300]}

    def say(self, session: str, prompt: str, **env: str) -> dict | None:
        """A prompt through the real hook; returns the parsed stdout, or None."""
        p = self._run(CAPTURE_HOOK, stdin=json.dumps({"session_id": session, "cwd": "/tmp", "prompt": prompt}),
                      env={**self.env, **env}, check=True)
        return json.loads(p.stdout) if p.stdout.strip() else None

    def brief(self) -> subprocess.CompletedProcess:
        return self._run(BRIEF_HOOK)

    def settings(self, **kv) -> None:
        (self.home / "settings.json").write_text(json.dumps(kv), encoding=UTF8)


def context(out: dict | None) -> str:
    return ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")


@pytest.fixture(scope="module")
def brain(tmp_path_factory) -> Brain:
    return Brain(tmp_path_factory.mktemp("recall"))


@pytest.fixture(scope="module")
def decision(brain: Brain) -> str:
    """The seeded decision the prompts ask about (and a personal note that must never surface)."""
    dec = brain.g("add-node", "--type", "decision",
                  "--title", "Shapefile export uses EPSG 4326 projection",
                  "--body", "Decided with the team: shapefile exports keep WGS84 coordinates. " * 10)
    node_id = (dec.get("node") or {}).get("id", "")
    assert node_id, str(dec)[:160]
    brain.g("add-node", "--type", "note", "--title", "Banana bread recipe",
            "--body", "flour sugar butter", "--realm", "personal")
    return node_id


# ------------------------------------------------------------ the SessionStart brief
class TestBrief:
    def test_it_uses_hook_specific_output(self, brain, decision):
        p = brain.brief()
        brief = json.loads(p.stdout)
        hso = brief.get("hookSpecificOutput") or {}
        assert hso.get("hookEventName") == "SessionStart", p.stdout[:160]
        assert "additionalContext" not in brief

    def test_it_says_capture_is_on_once(self, brain, decision):
        hso = json.loads(brain.brief().stdout).get("hookSpecificOutput") or {}
        assert hso.get("additionalContext", "").count("Capture is on") == 1


# ------------------------------------------------------------------ off by default
def test_the_per_prompt_hook_is_silent_by_default(brain, decision):
    assert brain.say("s1", ASK) is None


# ----------------------------------------------------------------------------- on
class TestRecallOn:
    @pytest.fixture(scope="class", autouse=True)
    def switched_on(self, brain: Brain, decision: str) -> None:
        brain.settings(prompt_recall=True)

    def test_a_clear_match_is_injected_under_user_prompt_submit(self, brain, decision):
        out = brain.say("s2", ASK)
        assert f"[n:{decision}]" in context(out), context(out)[:160]
        assert (out or {}).get("hookSpecificOutput", {}).get("hookEventName") == "UserPromptSubmit"

    def test_each_snippet_is_capped_and_there_are_at_most_two(self, brain, decision):
        ctx = context(brain.say("s2b", ASK))
        snips = [line.split(") ", 1)[-1] for line in ctx.splitlines() if line.startswith("[n:")]
        assert snips and all(len(s) <= SNIPPET_MAX_CHARS for s in snips), [len(s) for s in snips]
        assert ctx.count("[n:") <= MAX_SNIPPETS

    def test_never_the_same_node_twice_in_a_session(self, brain, decision):
        brain.say("s2", ASK)
        assert brain.say("s2", ASK) is None

    @pytest.mark.parametrize("session, prompt", [
        ("s3", "write a python script that parses yaml export files"),
        ("s9", "shapefile utf-8 encoding broke in the importer"),
        ("s10", "run the linter before the shapefile build again"),
        ("s11", "shapefile export projection " * 100),
        ("s12", "because port the shape"),
        ("s5", "banana bread recipe flour butter"),
    ], ids=["weak-match", "utf-8-is-not-a-ticket-key", "lone-before-or-again-is-not-a-cue",
            "long-paste-is-skipped", "whole-words-not-substrings", "personal-node-never-injected"])
    def test_these_inject_nothing(self, brain, decision, session, prompt):
        assert brain.say(session, prompt) is None

    @pytest.mark.parametrize("session, prompt", [
        ("s4", "what did we decide about shapefile?"),
        ("s8", "OS-1234 shapefile"),
    ], ids=["past-work-cue-lowers-the-bar", "ticket-key-is-a-cue"])
    def test_a_past_work_cue_lowers_the_bar_to_one_term(self, brain, decision, session, prompt):
        assert f"[n:{decision}]" in context(brain.say(session, prompt))

    def test_never_into_a_respond_session(self, brain, decision):
        assert brain.say("s6", ASK, **{REQUEST_VAR: "req-1"}) is None

    def test_the_env_escape_hatch_turns_it_off(self, brain, decision):
        assert brain.say("s7", ASK, **{OFF_VAR: "1"}) is None


# --------------------------------------------------------- every injection is logged
def test_every_injection_is_logged_on_its_capture_node(brain, decision):
    # runs after the class above (file order), which made the injections
    caps = brain.g("search", "shapefile", "--type", "capture", "--full").get("results") or []
    logged = [c for c in caps if (c.get("props") or {}).get("recalled")]
    assert sorted(c["props"]["session_id"] for c in logged) == ["s2", "s2b", "s4", "s8"], \
        [(c["props"].get("session_id"), c["props"].get("recalled")) for c in caps]
