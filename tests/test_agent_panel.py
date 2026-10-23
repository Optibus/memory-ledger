"""Ledger Agent, the floating box (scripts/agent_panel.py), against a THROWAWAY
brain. The AppKit box itself is not opened: these drive the work behind it — the
one "Ledger Agent" conversation, a message run to its end with every step
reported, the folder Claude runs in — with `claude` replaced by a script that
records where and how it ran. Nothing leaves the machine and no model is called.

    python -m pytest tests/test_agent_panel.py
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_panel
import agents
import graph
from _fakes import make_exe

MODEL = "claude-sonnet-5-5"
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
system = open(argv[argv.index("--append-system-prompt-file") + 1], encoding="utf-8").read()
with open(os.path.join(os.environ["SECOND_BRAIN_HOME"], "panel-claude.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": argv, "cwd": os.getcwd(), "stdin": sys.stdin.read(), "system": system}) + "\n")
if os.environ.get("FAKE_PANEL_MODE") == "crash":
    sys.stderr.write("boom: no credentials\n"); sys.exit(2)
def p(o): print(json.dumps(o), flush=True)
p({"type": "system", "subtype": "init", "model": "claude-sonnet-5-5", "session_id": "s"})
p({"type": "assistant", "message": {"id": "m1", "content": [
   {"type": "thinking", "thinking": "look"},
   {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git status"}},
   {"type": "tool_use", "id": "t2", "name": "Skill", "input": {"skill": "recall"}}]}})
p({"type": "user", "message": {"content": [
   {"type": "tool_result", "tool_use_id": "t1", "content": "clean"},
   {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]}})
p({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.0312,
   "usage": {"input_tokens": 900, "output_tokens": 40},
   "result": "The tree is **clean**.\n[[next: push it]]"})
'''


@pytest.fixture(scope="module")
def ctx(module_brain_home: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[SimpleNamespace]:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MEMORY_LEDGER_OWNER", "Owner")
        mp.delenv("FAKE_PANEL_MODE", raising=False)
        bin_path = make_exe(module_brain_home / "fake-claude", FAKE_CLAUDE)
        agents.save_config({"connectors": [
            {"kind": "claude", "name": "Claude", "bin": str(bin_path), "default_model": MODEL}]})
        work = tmp_path_factory.mktemp("work")
        mp.setattr(agents, "CLAUDE_CWD", work)
        yield SimpleNamespace(home=module_brain_home, work=work, con=agents.db())


def calls(home: Path) -> list[dict]:
    path = home / "panel-claude.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


class TestConversation:
    def test_it_is_made_once_and_carries_no_turns(self, ctx):
        first = agent_panel.conversation(ctx.con, full_tools=True)
        again = agent_panel.conversation(ctx.con, full_tools=False)
        conv = agents.get_conv(ctx.con, again)
        assert (first, conv["title"], conv["context_turns"]) == (again, "Ledger Agent", 0)

    def test_it_follows_this_runs_tools_flag(self, ctx):
        cid = agent_panel.conversation(ctx.con, full_tools=False)
        assert not agents.get_conv(ctx.con, cid)["full_tools"]

    def test_a_deleted_conversation_is_made_again(self, ctx):
        (ctx.home / agent_panel.STATE_NAME).write_text(json.dumps({"conversation": "aconv_gone"}))
        cid = agent_panel.conversation(ctx.con, full_tools=True)
        assert cid != "aconv_gone" and agents.get_conv(ctx.con, cid)


class TestAsk:
    @pytest.fixture(scope="class")
    def ran(self, ctx) -> SimpleNamespace:
        cid = agent_panel.conversation(ctx.con, full_tools=True)
        agent_panel.ask(cid, "warm-up", True, lambda _m: None)
        sent: list[dict] = []
        row = agent_panel.ask(cid, "is the tree clean?", True, sent.append)
        return SimpleNamespace(sent=sent, row=row, call=calls(ctx.home)[-1], cid=cid)

    def test_each_tool_and_skill_is_reported(self, ran):
        assert [m["text"] for m in ran.sent if m["t"] == "event"] == ["Bash · git status", "skill recall"]

    def test_the_answer_ends_it_without_the_next_suggestion(self, ran):
        assert ran.sent[-1]["t"] == "done" and ran.sent[-1]["text"] == "The tree is **clean**."

    def test_the_end_says_cost_and_model(self, ran):
        assert ran.sent[-1]["meta"].startswith("$0.03 · ") and ran.sent[-1]["meta"].endswith(MODEL)

    def test_claude_runs_in_the_boxs_folder(self, ran, ctx):
        assert Path(ran.call["cwd"]).resolve() == ctx.work.resolve()

    def test_and_is_told_so(self, ran, ctx):
        assert str(ctx.work) in ran.call["system"]

    def test_every_message_is_a_clean_session(self, ran):
        # Memory may still recall the earlier exchange; the thread itself is not carried.
        assert "Earlier in this conversation:" not in ran.call["stdin"]

    def test_tools_on_means_no_permission_prompts(self, ran):
        assert "--dangerously-skip-permissions" in ran.call["argv"]

    def test_the_exchange_is_in_the_agents_tab(self, ran, ctx):
        texts = [m["text"] for m in agents.thread(ctx.con, ran.cid)["messages"]]
        assert "is the tree clean?" in texts

    def test_a_failed_run_ends_with_an_error(self, ctx, monkeypatch):
        monkeypatch.setenv("FAKE_PANEL_MODE", "crash")
        sent: list[dict] = []
        agent_panel.ask(agent_panel.conversation(ctx.con, True), "again", True, sent.append)
        assert sent[-1]["t"] == "error" and "no credentials" in sent[-1]["text"]


class TestEventLine:
    def test_thinking_and_interim_text_are_not_lines(self):
        assert agent_panel.event_line({"kind": "thinking"}) == agent_panel.event_line({"kind": "text"}) == ""

    def test_a_failed_tool_is_marked(self):
        line = agent_panel.event_line({"kind": "tool", "name": "Read", "input": '{"file_path": "/a"}', "error": True})
        assert line == "Read · /a  ✗"

    def test_a_long_line_is_cut(self):
        line = agent_panel.event_line({"kind": "tool", "name": "Bash", "input": json.dumps({"command": "x" * 500})})
        assert len(line) == agent_panel.EVENT_TEXT_MAX and line.endswith("…")

