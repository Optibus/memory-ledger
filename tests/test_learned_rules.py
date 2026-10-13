"""The learned-rules detector (CONVENTIONS section 2i): it proposes, a person decides.

    python -m pytest tests/test_learned_rules.py

Runs the real CLI and the real hooks against a THROWAWAY brain, with synthetic
prompts. What it guards: the switch is OFF until asked, a proposal is never
returned by match-rules, accepting is the only road to an active learned rule,
rejecting sticks, and nothing is ever deleted.

The phases build on one brain (the order in the file is the order they run in):
captures, then proposals, then accept, then reject, then off again.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import graph

ROOT = Path(__file__).resolve().parent.parent
GRAPH_PY = ROOT / "scripts" / "graph.py"
CAPTURE_HOOK = ROOT / "hooks" / "capture_prompt.py"
BRIEF_HOOK = ROOT / "hooks" / "open_loops_brief.py"
UTF8 = "utf-8"

EMOJI_PROMPT = "No, never use emojis in the commit messages please"
TODO_PROMPT = "Don't forget to add the tests for the new parser module"
ONCE_PROMPT = "Always run the formatter before you open the pull request"
RELEASE_PROMPT = "Never commit directly to the release branch without review"
RELEASE_PROMPT_REWORDED = "Never commit directly to the release branch without a review"
SQUASH_PROMPT = "Never squash the release commits into one big commit"
EMOJI_RULE_TITLE = "No emojis in commit messages"
EMOJI_RULE_TEXT = "Never put emojis in commit messages."
EMOJI_SCENARIO = "emojis commit messages"
NO_LEARNED_VAR = "MEMORY_LEDGER_NO_LEARNED_RULES"
PROPOSAL_COUNT_SQL = "SELECT COUNT(*) FROM nodes WHERE type='rule-proposal'"


class Brain:
    """One throwaway brain, driven through the real CLI and hooks."""

    def __init__(self, home: Path) -> None:
        self.home = home
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("MEMORY_LEDGER_NO_", "SECOND_BRAIN_NO_LEARNED"))}
        env.update(SECOND_BRAIN_HOME=str(home), SECOND_BRAIN_NO_AUTOSYNC="1",
                   # a brain from scratch is seeded with the built-in skills; these
                   # checks count nodes in an empty one
                   SECOND_BRAIN_NO_SKILL_SEED="1")
        self.env = env

    def _run(self, script: Path, *args: str, env: dict | None = None, stdin: str | None = None,
             check: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(script), *args], input=stdin,
                              capture_output=True, text=True, encoding=UTF8, errors="replace",
                              env=env or self.env, check=check)

    def g(self, *args: str, env: dict | None = None) -> dict:
        p = self._run(GRAPH_PY, *args, env=env)
        try:
            return json.loads(p.stdout)
        except ValueError:
            return {"ok": False, "raw": p.stdout[:300], "stderr": p.stderr[:300]}

    def say(self, session: str, prompt: str) -> None:
        """A prompt, captured by the real hook."""
        self._run(CAPTURE_HOOK, stdin=json.dumps({"session_id": session, "cwd": "/tmp", "prompt": prompt}),
                  check=True)

    def brief(self) -> subprocess.CompletedProcess:
        return self._run(BRIEF_HOOK, stdin=json.dumps({"session_id": "h"}))

    def count(self, sql: str, *params) -> int:
        con = sqlite3.connect(str(self.home / "graph.db"))
        try:
            return con.execute(sql, params).fetchone()[0]
        finally:
            con.close()

    def proposals(self) -> int:
        return self.count(PROPOSAL_COUNT_SQL)

    def dry_texts(self) -> list[str]:
        return [p["text"] for p in self.g("propose-rules", "--dry-run").get("proposed", [])]


@pytest.fixture(scope="module")
def brain(tmp_path_factory) -> Brain:
    return Brain(tmp_path_factory.mktemp("learned"))


@pytest.fixture(scope="module")
def captured(brain: Brain) -> Brain:
    """Three sessions saying the same correction, plus two prompts that must not count."""
    for s in ("s1", "s2", "s3"):
        brain.say(s, EMOJI_PROMPT)
    brain.say("s1", TODO_PROMPT)   # a to-do, not a correction
    brain.say("s1", ONCE_PROMPT)   # once only
    return brain


# ---------------------------------------------------------- the pure heuristic
correction_of = graph.correction_of


@pytest.mark.parametrize("prompt", [
    "No, never use emojis in commit messages",
    "não, nunca use emojis nas mensagens de commit",
    "Pare de adicionar comentarios nos arquivos gerados",
    "Always run the linter before committing changes",
    "I said keep the changelog entries short and factual",
], ids=["english", "portuguese", "pare-de", "always", "i-said"])
def test_a_correction_is_spotted(prompt):
    assert correction_of(prompt)


@pytest.mark.parametrize("prompt", [
    "Please add a retry to the upload function",
    "There is no way to know which file changed",
    "Never mind, can you explain the merge logic here?",
    "No, stop that",
    "Don't " + "touch the parser " * 40,
    "never use this:\n```\nx = 1\n```",
], ids=["ordinary-request", "no-inside-a-sentence", "question", "too-vague", "long-task", "code"])
def test_these_are_not_corrections(prompt):
    assert correction_of(prompt) is None


def test_never_and_always_do_not_cluster():
    sim = graph._lr_jaccard(correction_of("never squash the release commits")["tokens"],
                            correction_of("always squash the release commits")["tokens"])
    assert sim < graph.LR_SIMILARITY


# ------------------------------------------------------------ off by default
class TestOffByDefault:
    def test_status_reports_off(self, brain):
        st = brain.g("learned-rules", "status")
        assert st.get("ok") and st.get("enabled") is False, st

    def test_propose_rules_says_it_is_off_and_how_to_turn_it_on(self, captured):
        off = captured.g("propose-rules")
        assert off.get("enabled") is False and off.get("ran") is False
        assert "learned-rules on" in off.get("hint", "")

    def test_nothing_was_proposed_while_off(self, captured):
        assert captured.proposals() == 0

    def test_the_settings_file_holds_no_switch(self, captured):
        path = captured.home / "settings.json"
        if path.exists():
            assert "learned_rules" not in json.loads(path.read_text(encoding=UTF8))


# ------------------------------- on: repeated corrections across sessions are proposed
@pytest.fixture(scope="module")
def proposed(captured: Brain) -> SimpleNamespace:
    on = captured.g("learned-rules", "on")
    dry = captured.g("propose-rules", "--dry-run")
    after_dry = captured.proposals()
    run = captured.g("propose-rules")
    props = run.get("proposed", [])
    return SimpleNamespace(brain=captured, on=on, dry=dry, after_dry=after_dry, run=run,
                           props=props, pid=props[0]["id"] if props else "")


class TestOn:
    def test_turning_it_on_is_stored_and_reported(self, proposed):
        assert proposed.on.get("enabled") is True and proposed.on.get("source") == "settings"

    def test_a_dry_run_reports_the_cluster_and_writes_nothing(self, proposed):
        assert len(proposed.dry.get("proposed", [])) == 1 and proposed.dry.get("dry_run") is True
        assert proposed.after_dry == 0

    def test_the_repeated_correction_is_proposed(self, proposed):
        assert len(proposed.props) == 1, proposed.run

    def test_the_once_only_correction_is_not(self, proposed):
        assert all("formatter" not in p["text"] for p in proposed.props)

    def test_the_to_do_is_not(self, proposed):
        assert all("tests" not in p["text"] for p in proposed.props)

    def test_it_carries_its_evidence_and_counts(self, proposed):
        p = proposed.props[0]
        assert (p["count"], p["sessions"], len(p["evidence"])) == (3, 3, 3)

    def test_stored_as_a_rule_proposal_with_status_proposed(self, proposed):
        node = proposed.brain.g("get-node", "--id", proposed.pid)
        node = node.get("node") or node
        assert (node.get("type"), node.get("status")) == ("rule-proposal", "proposed")

    def test_linked_to_the_prompts_it_came_from(self, proposed):
        n = proposed.brain.count("SELECT COUNT(*) FROM edges WHERE src=? AND rel='learned-from'",
                                 proposed.pid)
        assert n == 3

    def test_a_second_run_proposes_nothing_new(self, proposed):
        assert proposed.brain.g("propose-rules").get("proposed") == []
        assert proposed.brain.proposals() == 1


# ------------------------------------ one occurrence, or one session, is not enough
class TestThreshold:
    def test_a_single_occurrence_is_not_proposed(self, proposed):
        proposed.brain.say("s9", RELEASE_PROMPT)
        assert all("release branch" not in t for t in proposed.brain.dry_texts())

    def test_two_in_one_session_is_not_proposed_either(self, proposed):
        proposed.brain.say("s9", RELEASE_PROMPT)
        assert all("release branch" not in t for t in proposed.brain.dry_texts())

    def test_a_second_session_makes_it_a_proposal(self, proposed):
        proposed.brain.say("s10", RELEASE_PROMPT)
        assert any("release branch" in t for t in proposed.brain.dry_texts())


# ------------------------------------------------------ a proposal is not a rule
class TestProposalIsNotARule:
    def test_rule_proposals_lists_it_budgeted(self, proposed):
        lst = proposed.brain.g("rule-proposals")
        assert lst.get("count") == 1 and lst.get("budget") and "chars" in lst, lst
        assert lst.get("truncated") is False
        assert lst["proposals"][0]["id"] == proposed.pid

    def test_match_rules_does_not_return_a_proposal(self, proposed):
        mr = proposed.brain.g("match-rules", "never use emojis commit messages", "--full")
        assert proposed.pid not in json.dumps(mr)
        assert all(r["props"].get("source") != "learned" for r in mr.get("results", []))


# ----------------------------------------------------------------------- accept
@pytest.fixture(scope="module")
def accepted(proposed: SimpleNamespace) -> SimpleNamespace:
    b = proposed.brain
    b.g("add-rule", "--title", "Commit message emojis", "--source", "user", "--scenario",
        EMOJI_SCENARIO, "--body", "THE RULE\n\nUser version.\n\nJUDGED AGAINST\n\nn/a")
    acc = b.g("accept-rule", "--id", proposed.pid, "--title", EMOJI_RULE_TITLE, "--text", EMOJI_RULE_TEXT)
    full = b.g("match-rules", EMOJI_SCENARIO, "--full")
    return SimpleNamespace(brain=b, pid=proposed.pid, acc=acc, rid=acc.get("rule", ""), full=full,
                           again=b.g("accept-rule", "--id", proposed.pid))


class TestAccept:
    def test_accept_makes_a_learned_rule_at_precedence_50(self, accepted):
        acc = accepted.acc
        assert acc.get("ok") and acc.get("source") == "learned" and acc.get("precedence") == 50, acc

    def test_the_rule_now_matches_as_learned(self, accepted):
        learned = [r for r in accepted.full.get("results", []) if r["id"] == accepted.rid]
        assert learned, [r["id"] for r in accepted.full.get("results", [])]
        assert learned[0]["props"]["source"] == "learned"
        assert learned[0]["props"]["precedence"] == 50
        assert "Never put emojis" in learned[0]["body"]

    def test_a_user_rule_ranks_above_it(self, accepted):
        results = accepted.full.get("results", [])
        ids = [r["id"] for r in results]
        user_ids = [r["id"] for r in results if r["props"].get("source") == "user"]
        assert user_ids and ids.index(user_ids[0]) < ids.index(accepted.rid), ids

    def test_the_proposal_is_marked_accepted_and_kept(self, accepted):
        assert accepted.brain.g("rule-proposals", "--status", "accepted").get("count") == 1

    def test_the_rule_is_linked_to_its_proposal_and_evidence(self, accepted):
        n = accepted.brain.count("SELECT COUNT(*) FROM edges WHERE src=? AND rel='learned-from'",
                                 accepted.rid)
        assert n >= 4

    def test_accepting_twice_is_refused(self, accepted):
        assert accepted.again.get("ok") is False


# ----------------------------------------------------------------------- reject
class TestReject:
    @pytest.fixture(scope="class")
    def release(self, accepted: SimpleNamespace) -> SimpleNamespace:
        b = accepted.brain
        before_run = [p for p in b.g("rule-proposals").get("proposals", []) if "release branch" in p["text"]]
        b.g("propose-rules")
        after_run = [p for p in b.g("rule-proposals").get("proposals", []) if "release branch" in p["text"]]
        return SimpleNamespace(brain=b, before=before_run, after=after_run)

    def test_the_release_branch_proposal_does_not_exist_before_a_run(self, release):
        assert release.before == []

    def test_now_it_is_proposed(self, release):
        assert len(release.after) == 1

    def test_reject_marks_it_rejected_and_kept(self, release):
        rej = release.brain.g("reject-rule", "--id", release.after[0]["id"], "--reason", "not a rule")
        assert rej.get("ok") and rej.get("kept") is True, rej

    def test_a_rejected_cluster_is_not_proposed_again_even_reworded(self, release):
        b = release.brain
        b.say("s11", RELEASE_PROMPT_REWORDED)
        before = b.proposals()
        run = b.g("propose-rules")
        assert run.get("proposed") == [], run
        assert b.proposals() == before

    def test_match_rules_never_returns_a_rejected_proposal(self, release):
        mr = release.brain.g("match-rules", "release branch commit review", "--full")
        assert release.after[0]["id"] not in json.dumps(mr)


# ------------------------------------------------------------ nothing is deleted
class TestNothingIsDeleted:
    def test_every_capture_is_still_there(self, accepted):
        assert accepted.brain.count("SELECT COUNT(*) FROM nodes WHERE type='capture'") >= 9

    def test_both_proposals_are_still_there(self, accepted):
        assert accepted.brain.proposals() == 2


# ------------------------------------ off again stops the detector, keeps the data
class TestOffAgain:
    @pytest.fixture(scope="class")
    def off(self, accepted: SimpleNamespace) -> Brain:
        b = accepted.brain
        b.g("learned-rules", "off")
        b.say("s12", SQUASH_PROMPT)
        b.say("s13", SQUASH_PROMPT)
        return b

    def test_propose_rules_is_a_no_op(self, off):
        assert off.g("propose-rules").get("ran") is False

    def test_the_proposals_are_still_listed(self, off):
        assert off.g("rule-proposals", "--status", "all").get("of") == 2

    def test_the_no_env_var_outranks_the_file(self, off):
        off.g("learned-rules", "on")
        st = off.g("learned-rules", "status", env={**off.env, NO_LEARNED_VAR: "1"})
        assert st.get("enabled") is False and st.get("source") == "env"


# --------------------------------------------------------- the SessionStart hook
class TestSessionStartHook:
    @pytest.fixture(scope="class")
    def stamp(self, accepted: SimpleNamespace) -> Path:
        return accepted.brain.home / ".last_learned_rules"

    def test_off_the_hook_stamps_nothing_and_still_emits_valid_json(self, accepted, stamp):
        accepted.brain.g("learned-rules", "off")
        hp = accepted.brain.brief()
        assert not stamp.exists()
        json.loads(hp.stdout or "{}")

    def test_on_the_hook_stamps_the_day_and_its_stdout_is_still_valid_json(self, accepted, stamp):
        accepted.brain.g("learned-rules", "on")
        hp = accepted.brain.brief()
        assert stamp.exists()
        json.loads(hp.stdout or "{}")
