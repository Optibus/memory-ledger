"""Traceability tests (CONVENTIONS §2k) - the agent tree, ideas, and recheck.

    python -m pytest tests/test_traceability.py

Runs the real CLI and the real hooks against a THROWAWAY brain (see conftest.py),
never the owner's. What it guards is the part that would fail silently: a
conditional discard accepted without a structured condition, or a gauge change
that stops unblocking what was waiting on it. Both look fine until someone needs
the answer months later, which is exactly when it is too late to notice.

The first half of the file builds on one brain and one story (tree -> discards ->
gauges -> recheck -> lifecycle), so the tests run in file order and share the
module-scoped fixtures below. The hook tests that follow each get their own.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from conftest import HOME_VAR, NO_AUTOSYNC_VAR, ROOT, UTF8

GRAPH_SCRIPT = ROOT / "scripts" / "graph.py"
HOOKS = ROOT / "hooks"

SESSION = "sess-abc"
GAUGE_VRAM = "gpu.vram_gb"
GAUGE_RAM = "ram.total_gb"
CONDITION_VRAM = f"{GAUGE_VRAM} >= 16"
CONDITION_RAM = f"{GAUGE_RAM} >= 64"
CONDITIONAL = "conditional"
PERMANENT = "permanent"
STATUS_DISCARDED = "discarded"
STATUS_REVIVED = "revived"
OUTCOME_COMPLETED = "completed"
ROLE_MAIN = "main"

# A hook gets this long against a master that takes 5 s to answer.
SLOW_MASTER_DELAY_S = 5
HOOK_BUDGET_MS = 2000
TRANSCRIPT_BUDGET_MS = 1000

Graph = Callable[..., dict[str, Any]]
Hook = Callable[..., subprocess.CompletedProcess]


# ----------------------------------------------------------------- fixtures
def _run_graph(env: dict[str, str], *args: str) -> dict[str, Any]:
    p = subprocess.run([sys.executable, str(GRAPH_SCRIPT), *args], capture_output=True,
                       text=True, encoding=UTF8, errors="replace", env=env)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"ok": False, "raw": p.stdout[:300], "stderr": p.stderr[:300]}


def _run_hook(env: dict[str, str], script: str, payload: Any, *args: str) -> subprocess.CompletedProcess:
    """Run a hook the way Claude Code does: JSON on stdin, argv for the phase."""
    return subprocess.run(
        [sys.executable, str(HOOKS / script), *args],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True, text=True, encoding=UTF8, errors="replace", env=env)


@pytest.fixture(scope="module")
def graph_env(module_brain_home: Path) -> dict[str, str]:
    """The environment of every child process in the shared story: the module's
    brain, autosync off, and nothing inherited that points at a real service."""
    return {**os.environ, HOME_VAR: str(module_brain_home), NO_AUTOSYNC_VAR: "1"}


@pytest.fixture(scope="module")
def g(graph_env: dict[str, str]) -> Graph:
    """`g("subcommand", ...)`: run a graph.py subcommand on the shared brain and
    return its parsed JSON envelope."""
    def run(*args: str) -> dict[str, Any]:
        return _run_graph(graph_env, *args)

    assert run("init")["ok"], "the shared brain did not initialise"
    return run


@pytest.fixture(scope="module")
def hook(graph_env: dict[str, str]) -> Hook:
    """`hook(script, payload, *args, env=...)` on the shared brain by default."""
    def run(script: str, payload: Any, *args: str, env: dict[str, str] | None = None):
        return _run_hook(env or graph_env, script, payload, *args)
    return run


class Tree(NamedTuple):
    conversation: dict[str, Any]
    research: str
    src_a: str
    src_b: str
    write: str


@pytest.fixture(scope="module")
def tree(g: Graph) -> Tree:
    """research -> (source A, source B), and a sibling `write` agent, all under
    one conversation. Nothing is asserted here; `test_tree_*` do that."""
    conversation = g("conversation-start", "--session", SESSION, "--cwd", "/tmp")
    research = g("agent-spawn", "--session", SESSION, "--role", "pesquisa",
                 "--model", "opus")["node"]["id"]
    src_a = g("agent-spawn", "--parent", research, "--role", "fonte A")["node"]["id"]
    src_b = g("agent-spawn", "--parent", research, "--role", "fonte B")["node"]["id"]
    write = g("agent-spawn", "--session", SESSION, "--role", "redacao")["node"]["id"]
    return Tree(conversation, research, src_a, src_b, write)


@pytest.fixture(scope="module")
def idea(g: Graph, tree: Tree) -> str:
    """The idea the discard and recheck tests are about, proposed by source A."""
    return g("idea-propose", "--title", "run the 13B model locally", "--agent", tree.src_a,
             "--detail", "local inference, no API cost")["node"]["id"]


def _node(g: Graph, node_id: str) -> dict[str, Any]:
    return g("get-node", "--id", node_id)["node"]


def _propose_and_block(g: Graph, title: str, reason: str, *conditions: str, agent: str | None = None) -> str:
    """Propose an idea and discard it conditionally on `conditions`."""
    propose = ["idea-propose", "--title", title] + (["--agent", agent] if agent else [])
    node_id = g(*propose)["node"]["id"]
    blocked = [arg for c in conditions for arg in ("--blocked-by", c)]
    g("idea-discard", "--id", node_id, "--type", CONDITIONAL, "--reason", reason, *blocked)
    return node_id


# ----------------------------------------------------------------- the tree
def test_conversation_is_created_once(g: Graph, tree: Tree) -> None:
    assert tree.conversation["ok"] and tree.conversation["created"]
    again = g("conversation-start", "--session", SESSION)
    assert again["node"]["id"] == tree.conversation["node"]["id"]


def test_depth_is_recorded(g: Graph, tree: Tree) -> None:
    assert _node(g, tree.src_a)["props"]["depth"] == 2


@pytest.mark.parametrize("parent, why", [
    ("nope", "an agent needs a parent that exists"),
    ("gauge_nope", "and a parent that can hold one"),
])
def test_agent_spawn_refuses_a_bad_parent(g: Graph, parent: str, why: str) -> None:
    assert not g("agent-spawn", "--parent", parent, "--role", "x")["ok"], why


# ------------------------------------------------- the critical hook: discard
@pytest.mark.parametrize("discard_args, why", [
    (["--type", CONDITIONAL, "--reason", "not enough memory"],
     "conditional discard with no condition"),
    (["--type", CONDITIONAL, "--reason", "x", "--blocked-by", CONDITION_VRAM],
     "a condition on an uncatalogued gauge"),
    (["--type", PERMANENT, "--reason", "x", "--blocked-by", CONDITION_VRAM],
     "a permanent discard cannot carry a condition"),
    (["--type", CONDITIONAL, "--reason", "x", "--blocked-by", "we just need more memory"],
     "free text is not a condition"),
])
def test_a_discard_must_carry_its_condition(g: Graph, idea: str, discard_args: list[str], why: str) -> None:
    r = g("idea-discard", "--id", idea, *discard_args)
    assert not r["ok"], why


# ------------------------------------------------- the catalog and recheck
def test_gauge_catalog_accepts_a_key_and_rejects_a_malformed_one(g: Graph) -> None:
    assert g("gauge-set", "--key", GAUGE_VRAM, "--value", "8", "--unit", "GB",
             "--probe", "echo 8", "--description", "VRAM on the box")["ok"]
    assert not g("gauge-set", "--key", "bad key!", "--value", "1")["ok"]


def test_conditional_discard_is_indexed(g: Graph, tree: Tree, idea: str) -> None:
    r = g("idea-discard", "--id", idea, "--type", CONDITIONAL,
          "--reason", "memoria insuficiente para o modelo pretendido",
          "--blocked-by", CONDITION_VRAM, "--recheck-on", "hardware.changed",
          "--caused-by", tree.src_a)
    assert r["ok"] and r["indexed_conditions"] == 1 and r["rechecks_automatically"]
    assert _node(g, idea)["status"] == STATUS_DISCARDED
    assert g("gauges")["results"][0]["blocking"] == 1


def test_a_gauge_change_unblocks_the_idea(g: Graph, idea: str) -> None:
    r = g("recheck")
    assert r["unblocked"] == 0 and r["still_blocked"] == 1, "still blocked at 8GB"

    rc = g("gauge-set", "--key", GAUGE_VRAM, "--value", "24", "--apply")["recheck"]
    assert rc["unblocked"] == 1 and rc["revived"] == [idea], rc["notice"]
    assert _node(g, idea)["status"] == STATUS_REVIVED
    assert g("recheck")["unblocked"] == 0, "a second recheck stays quiet"


def test_multiple_conditions_are_a_conjunction(g: Graph, tree: Tree) -> None:
    g("gauge-set", "--key", GAUGE_RAM, "--value", "16", "--unit", "GB")
    i2 = _propose_and_block(g, "train locally", "box too small",
                            CONDITION_VRAM, CONDITION_RAM, agent=tree.src_b)
    r = g("recheck")
    assert r["unblocked"] == 0 and r["still_blocked"] == 1, "one of two met is still blocked"

    g("gauge-set", "--key", GAUGE_RAM, "--value", "64", "--no-recheck")
    r = g("recheck", "--apply")
    assert r["unblocked"] == 1 and r["revived"] == [i2], "both met unblocks it"


def test_only_a_conditional_discard_comes_back_on_its_own(g: Graph) -> None:
    i3 = g("idea-propose", "--title", "use a queue")["node"]["id"]
    g("idea-discard", "--id", i3, "--type", "preference", "--reason", "we chose polling")
    i4 = g("idea-propose", "--title", "store plaintext secrets")["node"]["id"]
    g("idea-discard", "--id", i4, "--type", PERMANENT, "--reason", "wrong, full stop")

    assert g("recheck")["still_blocked"] == 0, "preference and permanent are never indexed"
    assert g("ideas")["count"] == 4, "every alternative is listed, not just the winner"
    assert g("ideas", "--status", STATUS_REVIVED)["count"] == 2


# ------------------------------------------------------ the comparisons
def test_the_full_history_is_reconstructable(g: Graph, idea: str) -> None:
    states = [s["to"] for s in g("idea-history", "--id", idea)["history"]]
    assert states == ["proposed", STATUS_DISCARDED, STATUS_REVIVED]


@pytest.mark.parametrize("key, first, condition, final, final_label", [
    ("dependency.pandas.version", "2.9.0", "dependency.pandas.version >= 2.10.0", "2.10.1",
     "2.9.0 < 2.10.0 (version-aware, not lexical); 2.10.1 satisfies >= 2.10.0"),
    ("feature.streaming.available", "false", "feature.streaming.available == true", "yes",
     "false does not equal true; yes does"),
])
def test_gauge_comparisons(g: Graph, key: str, first: str, condition: str, final: str,
                           final_label: str) -> None:
    g("gauge-set", "--key", key, "--value", first)
    _propose_and_block(g, f"blocked on {key}", "not yet", condition)
    assert g("recheck")["unblocked"] == 0, final_label
    r = g("gauge-set", "--key", key, "--value", final, "--apply")
    assert r["recheck"]["unblocked"] == 1, final_label


def test_an_unmeasured_gauge_never_silently_satisfies(g: Graph) -> None:
    g("gauge-set", "--key", "budget.monthly", "--unit", "USD")
    _propose_and_block(g, "buy the bigger box", "no budget", "budget.monthly >= 500")
    r = g("recheck")
    assert r["unblocked"] == 0
    assert r.get("unmeasured_gauges") == ["budget.monthly"], "and it is named in the result"
    assert "budget.monthly" in g("gauges").get("warning", ""), \
        "the catalog warns about a probe-less blocker"


# ----------------------------------------------- probes and the projection
def test_a_probe_runs_and_records(g: Graph) -> None:
    p = g("gauge-probe", "--key", GAUGE_VRAM)
    assert p["probed"] == [{"key": GAUGE_VRAM, "value": "8"}]
    assert any(s["why"] == "no probe" for s in g("gauge-probe")["skipped"]), \
        "a probe-less gauge is skipped, not failed"


def test_the_index_rebuilds_from_the_transition_log(g: Graph) -> None:
    before = g("recheck")["still_blocked"]
    reb = g("recheck", "--rebuild")
    assert reb["ok"] and reb["rebuilt"] >= 1
    assert g("recheck")["still_blocked"] == before, "and answers the same afterwards"


# ------------------------------------------------- context as an event stream
def test_the_context_window_replays_from_the_deltas(g: Graph, tree: Tree) -> None:
    for op, item_type, ref, tokens in (("added", "document", "doc:123", "1200"),
                                       ("added", "topic", "topic:x", "480"),
                                       ("removed", "topic", "topic:x", "480")):
        g("context-event", "--agent", tree.research, "--op", op, "--item-type", item_type,
          "--item-ref", ref, "--tokens", tokens)
    w = g("context-at", "--agent", tree.research)
    assert w["tokens"] == 1200 and len(w["items"]) == 1, w["items"]


# ----------------------------------------------------- supersede, finish, end
def test_select_supersedes_the_replaced_idea(g: Graph) -> None:
    i8 = g("idea-propose", "--title", "approach A")["node"]["id"]
    i9 = g("idea-propose", "--title", "approach B")["node"]["id"]
    g("idea-select", "--id", i9, "--reason", "simpler", "--supersedes", i8)
    assert _node(g, i8)["status"] == "superseded"
    assert _node(g, i9)["status"] == "selected"


def test_ending_a_conversation_abandons_open_subagents(g: Graph, tree: Tree) -> None:
    g("agent-finish", "--id", tree.src_a, "--outcome", OUTCOME_COMPLETED)
    g("conversation-end", "--session", SESSION, "--reason", "test")
    assert _node(g, tree.write)["props"]["outcome"] == "abandoned", \
        "a subagent still open at the end is abandoned, not completed"
    assert _node(g, tree.src_a)["props"]["outcome"] == OUTCOME_COMPLETED, \
        "a finished agent keeps its own outcome"


def test_the_tree_shows_both_branches_and_their_discards(g: Graph) -> None:
    t = g("tree", "--session", SESSION)["tree"]
    assert len(t["children"]) == 2
    assert any("ideas" in k for k in t["children"][0].get("children", [])), \
        "a subagent's discards are visible from the root"


# ---------------------------------------------------------------- the hooks
@pytest.fixture(scope="module")
def hooked_session(g: Graph, hook: Hook) -> dict[str, Any]:
    """Drive the real hooks with Claude Code payloads, then return what they built:
    the session id, the tree, and the prompt hook's stdout."""
    sid = "11111111-2222-3333-4444-555555555555"
    ti = {"subagent_type": "Explore", "prompt": "find every caller", "description": "trace callers"}
    ti2 = {"subagent_type": "Plan", "prompt": "design the fix"}

    prompt = hook("capture_prompt.py", {"prompt": "how does the exporter handle empty timetables",
                                        "session_id": sid, "cwd": "/repo"})
    hook("capture_agent.py", {"session_id": sid, "tool_name": "Task", "tool_input": ti}, "pre")
    hook("capture_agent.py", {"session_id": sid, "tool_name": "Task", "tool_input": ti,
                              "tool_response": {"content": "done"}}, "post")
    hook("capture_agent.py", {"session_id": sid, "tool_name": "Task", "tool_input": ti2}, "pre")
    hook("capture_agent.py", {"session_id": sid, "tool_name": "Task", "tool_input": ti2,
                              "tool_response": {"is_error": True}}, "post")
    hook("capture_agent.py", {"session_id": sid, "tool_name": "Bash",
                              "tool_input": {"command": "ls"}}, "pre")
    hook("capture_answer.py", {"session_id": sid, "cwd": "/repo",
                               "last_assistant_message": "It drops them before the header."})
    hook("close_conversation.py", {"session_id": sid, "trigger": "manual"}, "compact")
    return {"sid": sid, "tree": g("tree", "--session", sid)["tree"], "prompt": prompt}


def test_the_prompt_hook_is_silent_by_default(hooked_session: dict[str, Any]) -> None:
    # "capture is on" moved to the SessionStart brief, and gated recall (opt-in)
    # is the only thing this hook may inject.
    assert hooked_session["prompt"].stdout.strip() == ""


def test_hooks_build_a_conversation_with_a_main_agent(hooked_session: dict[str, Any]) -> None:
    t = hooked_session["tree"]
    main = t["children"][0]
    assert t["type"] == "conversation" and main["role"] == ROLE_MAIN
    assert main.get("messages") == 2, "both turns hang off the main agent"


def test_each_task_call_becomes_a_subagent(hooked_session: dict[str, Any]) -> None:
    main = hooked_session["tree"]["children"][0]
    kids = {k["role"]: k for k in main.get("children", [])}
    assert set(kids) == {"Explore", "Plan"}
    assert kids["Explore"].get("outcome") == OUTCOME_COMPLETED, "a clean Task is completed"
    assert kids["Plan"].get("outcome") == "failed", "a failing Task is failed"
    assert len(main.get("children", [])) == 2, "other tools are ignored"


def test_precompact_leaves_one_summarized_event(g: Graph, hooked_session: dict[str, Any]) -> None:
    main = hooked_session["tree"]["children"][0]
    assert g("context-at", "--agent", main["id"])["events_replayed"] == 1


def test_the_end_hook_closes_the_conversation(g: Graph, hook: Hook, hooked_session: dict[str, Any]) -> None:
    sid = hooked_session["sid"]
    hook("close_conversation.py", {"session_id": sid, "reason": "clear"}, "end")
    conv = _node(g, f"conversation_{sid}")
    assert conv["status"] == "done" and conv["props"]["ended_at"]
    main = hooked_session["tree"]["children"][0]
    assert _node(g, main["id"])["props"]["outcome"] == OUTCOME_COMPLETED, "the main agent completes"


def test_a_hook_never_breaks_the_session(hook: Hook, graph_env: dict[str, str], tmp_path: Path) -> None:
    assert hook("capture_agent.py", "not json", "pre").returncode == 0, "malformed stdin is survivable"
    off = {**graph_env, HOME_VAR: str(tmp_path / "missing")}
    p = hook("capture_agent.py", {"session_id": "x", "tool_name": "Task",
                                  "tool_input": {"subagent_type": "X"}}, "pre", env=off)
    assert p.returncode == 0 and not p.stdout.strip(), "a disabled brain is a no-op"


# ------------------------------------------------------------------- tokens
def test_token_counts_per_turn_and_per_branch(g: Graph, hook: Hook, tmp_path: Path) -> None:
    """Where the token went - read from the transcript Claude Code already wrote."""
    sid = "tok-session"
    tp = tmp_path / "t.jsonl"
    with open(tp, "w", encoding=UTF8) as f:
        for _ in range(4000):                      # a long, realistic transcript
            f.write(json.dumps({"message": {"role": "user", "content": "pad " * 40}}) + "\n")
        f.write(json.dumps({"message": {
            "role": "assistant", "content": [{"type": "text", "text": "answer"}],
            "usage": {"input_tokens": 2, "output_tokens": 153,
                      "cache_read_input_tokens": 26570,
                      "cache_creation_input_tokens": 19848}}}) + "\n")

    hook("capture_prompt.py", {"session_id": sid, "cwd": "/r",
                               "prompt": "a real question about the exporter"})
    t0 = time.perf_counter()
    hook("capture_answer.py", {"session_id": sid, "transcript_path": str(tp),
                               "last_assistant_message": "It drops them."})
    ms = (time.perf_counter() - t0) * 1000
    assert ms < TRANSCRIPT_BUDGET_MS, f"a {tp.stat().st_size // 1024}KB transcript is a bounded read: {ms:.0f} ms"

    caps = [c for c in g("list", "--type", "capture", "--all")["results"]
            if c["props"].get("role") == "assistant" and c["props"].get("session_id") == sid]
    assert caps, "the answer was captured"
    assert caps[0]["props"].get("tokens_out") == 153, "the turn carries its token counts"

    agent = g("tree", "--session", sid)["tree"]["children"][0]["id"]
    hook("capture_answer.py", {"session_id": sid, "transcript_path": str(tp),
                               "last_assistant_message": "And again."})
    props = _node(g, agent)["props"]
    assert props.get("tokens_out") == 306 and props.get("turns") == 2, \
        {k: v for k, v in props.items() if k.startswith("tokens") or k == "turns"}

    missing = hook("capture_answer.py", {"session_id": sid, "transcript_path": str(tp.with_name("nope")),
                                         "last_assistant_message": "x" * 20})
    assert missing.returncode == 0, "a missing transcript is survivable"


# --------------------------------------------------- never block on the network
class SlowMaster(BaseHTTPRequestHandler):
    """A neo4j master that takes SLOW_MASTER_DELAY_S to answer a write."""

    def do_POST(self) -> None:
        time.sleep(SLOW_MASTER_DELAY_S)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"results":[],"errors":[]}')

    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args: Any) -> None:
        pass


_TASK_INPUT = {"subagent_type": "Explore", "prompt": "x"}


@pytest.mark.parametrize("script, args, payload", [
    pytest.param("capture_prompt.py", [], {"session_id": "s", "prompt": "a real question here"},
                 id="capture_prompt"),
    pytest.param("capture_answer.py", [], {"session_id": "s", "last_assistant_message": "an answer here"},
                 id="capture_answer"),
    pytest.param("capture_agent.py", ["pre"],
                 {"session_id": "s", "tool_name": "Task", "tool_input": _TASK_INPUT},
                 id="capture_agent-pre"),
    pytest.param("capture_agent.py", ["post"],
                 {"session_id": "s", "tool_name": "Task", "tool_input": _TASK_INPUT, "tool_response": {}},
                 id="capture_agent-post"),
    pytest.param("close_conversation.py", ["end"], {"session_id": "s"}, id="close_conversation-end"),
    pytest.param("open_loops_brief.py", [], {"session_id": "s"}, id="open_loops_brief"),
])
def test_a_hook_never_blocks_on_the_network(http_server, tmp_path: Path, script: str,
                                            args: list[str], payload: dict[str, Any]) -> None:
    """A hook must not wait on the neo4j master, however slow it is.

    graph.py's api_* writers push to the master inline, which is right for a CLI
    and wrong in a hook. Getting this wrong is invisible locally (no master
    configured -> the call fails instantly) and costs 18-48s per prompt on a
    machine that HAS one. So it is pinned here against a deliberately slow
    server, not left to be noticed in production."""
    master = http_server(SlowMaster)
    brain = tmp_path / "brain"
    brain.mkdir()
    (brain / "neo4j.json").write_text(
        json.dumps({"url": master.url, "db": "neo4j", "user": "neo4j", "password": "x"}),
        encoding=UTF8)
    # deliberately WITHOUT the no-autosync flag - that is the hook's own job
    live = {k: v for k, v in os.environ.items() if k != NO_AUTOSYNC_VAR}
    live[HOME_VAR] = str(brain)
    live["SECOND_BRAIN_NO_CONSOLIDATE"] = "1"
    subprocess.run([sys.executable, str(GRAPH_SCRIPT), "init"], capture_output=True,
                   env={**live, NO_AUTOSYNC_VAR: "1"})

    t0 = time.perf_counter()
    _run_hook(live, script, payload, *args)
    ms = (time.perf_counter() - t0) * 1000
    assert ms < HOOK_BUDGET_MS, f"{script} waited on a {SLOW_MASTER_DELAY_S}s master: {ms:.0f} ms"
