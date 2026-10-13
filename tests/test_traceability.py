#!/usr/bin/env python3
"""Traceability tests (CONVENTIONS §2k) — the agent tree, ideas, and recheck.

Stdlib only, like everything else here. Runs the real CLI and the real hooks
against a THROWAWAY brain in a temp dir, so it never touches ~/.second_brain:

    python3 tests/test_traceability.py

What it guards is the part that would fail silently: a conditional discard
accepted without a structured condition, or a gauge change that stops unblocking
what was waiting on it. Both look fine until someone needs the answer months
later, which is exactly when it is too late to notice.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAIN = tempfile.mkdtemp(prefix="second-brain-test-")
ENV = {**os.environ, "SECOND_BRAIN_HOME": BRAIN, "SECOND_BRAIN_NO_AUTOSYNC": "1"}
FAILS = []


def g(*a):
    """Run a graph.py subcommand; return its parsed JSON envelope."""
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "graph.py"), *a],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"ok": False, "raw": p.stdout[:300], "stderr": p.stderr[:300]}


def hook(script, payload, *args, env=None):
    """Run a hook the way Claude Code does: JSON on stdin, argv for the phase."""
    return subprocess.run(
        [sys.executable, str(ROOT / "hooks" / script), *args],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env or ENV)


def ck(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def test_tree():
    print("\n== the agent tree ==")
    c = g("conversation-start", "--session", "sess-abc", "--cwd", "/tmp")
    ck("conversation created", c["ok"] and c["created"], c["node"]["id"])
    ck("conversation-start is idempotent",
       g("conversation-start", "--session", "sess-abc")["node"]["id"] == c["node"]["id"])
    research = g("agent-spawn", "--session", "sess-abc", "--role", "pesquisa",
                 "--model", "opus")["node"]["id"]
    src_a = g("agent-spawn", "--parent", research, "--role", "fonte A")["node"]["id"]
    src_b = g("agent-spawn", "--parent", research, "--role", "fonte B")["node"]["id"]
    write = g("agent-spawn", "--session", "sess-abc", "--role", "redacao")["node"]["id"]
    ck("depth recorded", g("get-node", "--id", src_a)["node"]["props"]["depth"] == 2)
    ck("an agent needs a parent that exists",
       not g("agent-spawn", "--parent", "nope", "--role", "x")["ok"])
    ck("and a parent that can hold one",
       not g("agent-spawn", "--parent", "gauge_nope", "--role", "x")["ok"])
    return research, src_a, src_b, write


def test_discard_validation(src_a):
    print("\n== the critical hook: a discard must carry its condition ==")
    idea = g("idea-propose", "--title", "run the 13B model locally", "--agent", src_a,
             "--detail", "local inference, no API cost")["node"]["id"]
    r = g("idea-discard", "--id", idea, "--type", "conditional",
          "--reason", "not enough memory")
    ck("conditional discard with no condition is rejected", not r["ok"],
       r.get("error", "")[:60])
    r = g("idea-discard", "--id", idea, "--type", "conditional", "--reason", "x",
          "--blocked-by", "gpu.vram_gb >= 16")
    ck("a condition on an uncatalogued gauge is rejected", not r["ok"],
       r.get("error", "")[:50])
    r = g("idea-discard", "--id", idea, "--type", "permanent", "--reason", "x",
          "--blocked-by", "gpu.vram_gb >= 16")
    ck("a permanent discard cannot carry a condition", not r["ok"])
    r = g("idea-discard", "--id", idea, "--type", "conditional", "--reason", "x",
          "--blocked-by", "we just need more memory")
    ck("free text is not a condition", not r["ok"], r.get("error", "")[:50])
    return idea


def test_recheck(idea, src_a, src_b):
    print("\n== the catalog and the re-evaluation ==")
    ck("gauge created", g("gauge-set", "--key", "gpu.vram_gb", "--value", "8",
                          "--unit", "GB", "--probe", "echo 8",
                          "--description", "VRAM on the box")["ok"])
    ck("a malformed key is rejected",
       not g("gauge-set", "--key", "bad key!", "--value", "1")["ok"])
    r = g("idea-discard", "--id", idea, "--type", "conditional",
          "--reason", "memoria insuficiente para o modelo pretendido",
          "--blocked-by", "gpu.vram_gb >= 16", "--recheck-on", "hardware.changed",
          "--caused-by", src_a)
    ck("conditional discard accepted",
       r["ok"] and r["indexed_conditions"] == 1 and r["rechecks_automatically"])
    ck("the idea is discarded", g("get-node", "--id", idea)["node"]["status"] == "discarded")
    ck("the gauge reports what it blocks", g("gauges")["results"][0]["blocking"] == 1)

    r = g("recheck")
    ck("still blocked at 8GB", r["unblocked"] == 0 and r["still_blocked"] == 1)
    rc = g("gauge-set", "--key", "gpu.vram_gb", "--value", "24", "--apply")["recheck"]
    ck("a gauge change unblocks it", rc["unblocked"] == 1 and rc["revived"] == [idea],
       rc["notice"][0] if rc["notice"] else "")
    ck("the idea is revived", g("get-node", "--id", idea)["node"]["status"] == "revived")
    ck("a second recheck stays quiet", g("recheck")["unblocked"] == 0)

    print("\n== multiple conditions are a conjunction ==")
    g("gauge-set", "--key", "ram.total_gb", "--value", "16", "--unit", "GB")
    i2 = g("idea-propose", "--title", "train locally", "--agent", src_b)["node"]["id"]
    g("idea-discard", "--id", i2, "--type", "conditional", "--reason", "box too small",
      "--blocked-by", "gpu.vram_gb >= 16", "--blocked-by", "ram.total_gb >= 64")
    r = g("recheck")
    ck("one of two met is still blocked", r["unblocked"] == 0 and r["still_blocked"] == 1)
    g("gauge-set", "--key", "ram.total_gb", "--value", "64", "--no-recheck")
    r = g("recheck", "--apply")
    ck("both met unblocks it", r["unblocked"] == 1 and r["revived"] == [i2])
    return idea


def test_discard_types():
    print("\n== only a conditional discard comes back on its own ==")
    i3 = g("idea-propose", "--title", "use a queue")["node"]["id"]
    g("idea-discard", "--id", i3, "--type", "preference", "--reason", "we chose polling")
    i4 = g("idea-propose", "--title", "store plaintext secrets")["node"]["id"]
    g("idea-discard", "--id", i4, "--type", "permanent", "--reason", "wrong, full stop")
    ck("preference and permanent are never indexed", g("recheck")["still_blocked"] == 0)
    ck("every alternative is listed, not just the winner", g("ideas")["count"] == 4)
    ck("and filterable by status", g("ideas", "--status", "revived")["count"] == 2)


def test_comparisons(idea):
    print("\n== history, versions, booleans, unmeasured ==")
    h = g("idea-history", "--id", idea)
    ck("the full history is reconstructable",
       [s["to"] for s in h["history"]] == ["proposed", "discarded", "revived"],
       str([s["to"] for s in h["history"]]))

    g("gauge-set", "--key", "dependency.pandas.version", "--value", "2.9.0")
    i5 = g("idea-propose", "--title", "use the new pandas API")["node"]["id"]
    g("idea-discard", "--id", i5, "--type", "conditional", "--reason", "needs 2.10",
      "--blocked-by", "dependency.pandas.version >= 2.10.0")
    ck("2.9.0 < 2.10.0 — version-aware, not lexical", g("recheck")["unblocked"] == 0)
    r = g("gauge-set", "--key", "dependency.pandas.version", "--value", "2.10.1", "--apply")
    ck("2.10.1 satisfies >= 2.10.0", r["recheck"]["unblocked"] == 1)

    g("gauge-set", "--key", "feature.streaming.available", "--value", "false")
    i6 = g("idea-propose", "--title", "stream the output")["node"]["id"]
    g("idea-discard", "--id", i6, "--type", "conditional", "--reason", "flag is off",
      "--blocked-by", "feature.streaming.available == true")
    ck("false does not equal true", g("recheck")["unblocked"] == 0)
    r = g("gauge-set", "--key", "feature.streaming.available", "--value", "yes", "--apply")
    ck("yes does", r["recheck"]["unblocked"] == 1)

    g("gauge-set", "--key", "budget.monthly", "--unit", "USD")
    i7 = g("idea-propose", "--title", "buy the bigger box")["node"]["id"]
    g("idea-discard", "--id", i7, "--type", "conditional", "--reason", "no budget",
      "--blocked-by", "budget.monthly >= 500")
    r = g("recheck")
    ck("an unmeasured gauge never silently satisfies", r["unblocked"] == 0)
    ck("and it is named in the result", r.get("unmeasured_gauges") == ["budget.monthly"])
    ck("the catalog warns about a probe-less blocker",
       "budget.monthly" in g("gauges").get("warning", ""))


def test_probe_and_projection():
    print("\n== probes, and the index as a projection ==")
    p = g("gauge-probe", "--key", "gpu.vram_gb")
    ck("a probe runs and records", p["probed"] == [{"key": "gpu.vram_gb", "value": "8"}],
       str(p["probed"]))
    ck("a probe-less gauge is skipped, not failed",
       any(s["why"] == "no probe" for s in g("gauge-probe")["skipped"]))
    before = g("recheck")["still_blocked"]
    reb = g("recheck", "--rebuild")
    ck("the index rebuilds from the transition log", reb["ok"] and reb["rebuilt"] >= 1)
    ck("and answers the same afterwards", g("recheck")["still_blocked"] == before)


def test_context_events(research):
    print("\n== context as an event stream ==")
    for op, it, ref, tok in (("added", "document", "doc:123", "1200"),
                             ("added", "topic", "topic:x", "480"),
                             ("removed", "topic", "topic:x", "480")):
        g("context-event", "--agent", research, "--op", op, "--item-type", it,
          "--item-ref", ref, "--tokens", tok)
    w = g("context-at", "--agent", research)
    ck("the window replays from the deltas", w["tokens"] == 1200 and len(w["items"]) == 1,
       str(w["items"]))


def test_lifecycle(src_a, write):
    print("\n== supersede, finish, end ==")
    i8 = g("idea-propose", "--title", "approach A")["node"]["id"]
    i9 = g("idea-propose", "--title", "approach B")["node"]["id"]
    g("idea-select", "--id", i9, "--reason", "simpler", "--supersedes", i8)
    ck("the replaced idea is superseded",
       g("get-node", "--id", i8)["node"]["status"] == "superseded")
    ck("the chosen one is selected",
       g("get-node", "--id", i9)["node"]["status"] == "selected")
    g("agent-finish", "--id", src_a, "--outcome", "completed")
    g("conversation-end", "--session", "sess-abc", "--reason", "test")
    ck("a subagent still open at the end is abandoned, not completed",
       g("get-node", "--id", write)["node"]["props"]["outcome"] == "abandoned")
    ck("a finished agent keeps its own outcome",
       g("get-node", "--id", src_a)["node"]["props"]["outcome"] == "completed")

    t = g("tree", "--session", "sess-abc")["tree"]
    ck("the tree holds both branches", len(t["children"]) == 2)
    ck("a subagent's discards are visible from the root",
       any("ideas" in k for k in t["children"][0].get("children", [])))


def test_hooks():
    print("\n== the hooks, driven with real Claude Code payloads ==")
    sid = "11111111-2222-3333-4444-555555555555"
    ti = {"subagent_type": "Explore", "prompt": "find every caller",
          "description": "trace callers"}
    ti2 = {"subagent_type": "Plan", "prompt": "design the fix"}

    o = hook("capture_prompt.py", {"prompt": "how does the exporter handle empty timetables",
                                   "session_id": sid, "cwd": "/repo"})
    # Silent by default: "capture is on" moved to the SessionStart brief, and
    # gated recall (opt-in) is the only thing this hook may inject.
    ck("the prompt hook is silent by default", o.stdout.strip() == "", o.stdout[:120])
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

    t = g("tree", "--session", sid)["tree"]
    main = t["children"][0]
    ck("a conversation and a main agent appear", t["type"] == "conversation"
       and main["role"] == "main")
    ck("both turns hang off the main agent", main.get("messages") == 2,
       str(main.get("messages")))
    kids = {k["role"]: k for k in main.get("children", [])}
    ck("each Task call becomes a subagent", set(kids) == {"Explore", "Plan"}, str(list(kids)))
    ck("a clean Task is completed", kids.get("Explore", {}).get("outcome") == "completed")
    ck("a failing Task is failed", kids.get("Plan", {}).get("outcome") == "failed")
    ck("other tools are ignored", len(main.get("children", [])) == 2)
    ck("PreCompact leaves one summarized event",
       g("context-at", "--agent", main["id"])["events_replayed"] == 1)

    hook("close_conversation.py", {"session_id": sid, "reason": "clear"}, "end")
    conv = g("get-node", "--id", f"conversation_{sid}")["node"]
    ck("the conversation closes", conv["status"] == "done" and conv["props"]["ended_at"])
    ck("the main agent completes",
       g("get-node", "--id", main["id"])["node"]["props"]["outcome"] == "completed")

    print("\n== a hook never breaks the session ==")
    ck("malformed stdin is survivable",
       hook("capture_agent.py", "not json", "pre").returncode == 0)
    off = {**os.environ, "SECOND_BRAIN_HOME": BRAIN + "-missing"}
    p = hook("capture_agent.py", {"session_id": "x", "tool_name": "Task",
                                  "tool_input": {"subagent_type": "X"}}, "pre", env=off)
    ck("a disabled brain is a no-op", p.returncode == 0 and not p.stdout.strip())


def test_tokens():
    """Where the token went — read from the transcript Claude Code already wrote."""
    print("\n== per-turn and per-branch token counts ==")
    sid = "tok-session"
    tdir = tempfile.mkdtemp(prefix="second-brain-test-transcript-")
    tp = Path(tdir) / "t.jsonl"
    with open(tp, "w", encoding="utf-8") as f:
        for _ in range(4000):                      # a long, realistic transcript
            f.write(json.dumps({"message": {"role": "user", "content": "pad " * 40}}) + "\n")
        f.write(json.dumps({"message": {"role": "assistant",
                "content": [{"type": "text", "text": "answer"}],
                "usage": {"input_tokens": 2, "output_tokens": 153,
                          "cache_read_input_tokens": 26570,
                          "cache_creation_input_tokens": 19848}}}) + "\n")
    size_kb = tp.stat().st_size // 1024

    hook("capture_prompt.py", {"session_id": sid, "cwd": "/r",
                               "prompt": "a real question about the exporter"})
    t0 = time.perf_counter()
    hook("capture_answer.py", {"session_id": sid, "transcript_path": str(tp),
                               "last_assistant_message": "It drops them."})
    ms = (time.perf_counter() - t0) * 1000
    ck(f"a {size_kb}KB transcript stays a bounded read", ms < 1000, f"{ms:.0f} ms")

    caps = [c for c in g("list", "--type", "capture", "--all")["results"]
            if c["props"].get("role") == "assistant"
            and c["props"].get("session_id") == sid]
    ck("the turn carries its token counts",
       caps and caps[0]["props"].get("tokens_out") == 153,
       str({k: v for k, v in (caps[0]["props"] if caps else {}).items()
            if k.startswith("tokens")}))

    agent = g("tree", "--session", sid)["tree"]["children"][0]["id"]
    hook("capture_answer.py", {"session_id": sid, "transcript_path": str(tp),
                               "last_assistant_message": "And again."})
    pr = g("get-node", "--id", agent)["node"]["props"]
    ck("the branch accumulates them", pr.get("tokens_out") == 306 and pr.get("turns") == 2,
       str({k: v for k, v in pr.items() if k.startswith("tokens") or k == "turns"}))
    ck("a missing transcript is survivable",
       hook("capture_answer.py", {"session_id": sid, "transcript_path": "/nope",
                                  "last_assistant_message": "x" * 20}).returncode == 0)
    shutil.rmtree(tdir, ignore_errors=True)


def test_hooks_never_block_on_network():
    """A hook must not wait on the neo4j master, however slow it is.

    graph.py's api_* writers push to the master inline, which is right for a CLI
    and wrong in a hook. Getting this wrong is invisible locally (no master
    configured -> the call fails instantly) and costs 18-48s per prompt on a
    machine that HAS one. So it is pinned here against a deliberately slow
    server, not left to be noticed in production.
    """
    print("\n== a hook never blocks on the network ==")
    import http.server
    import socketserver
    import threading

    class Slow(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            time.sleep(5)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"results":[],"errors":[]}')

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    try:
        srv = socketserver.TCPServer(("127.0.0.1", 0), Slow)
    except Exception as e:
        ck("slow-master harness available", False, str(e)[:60])
        return
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    brain = tempfile.mkdtemp(prefix="second-brain-test-slow-")
    with open(os.path.join(brain, "neo4j.json"), "w", encoding="utf-8") as f:
        json.dump({"url": f"http://127.0.0.1:{port}", "db": "neo4j",
                   "user": "neo4j", "password": "x"}, f)
    # deliberately WITHOUT the no-autosync flag — that is the hook's own job
    live = {k: v for k, v in os.environ.items() if k != "SECOND_BRAIN_NO_AUTOSYNC"}
    live["SECOND_BRAIN_HOME"] = brain
    live["SECOND_BRAIN_NO_CONSOLIDATE"] = "1"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "graph.py"), "init"],
                   capture_output=True,
                   env={**live, "SECOND_BRAIN_NO_AUTOSYNC": "1"})

    ti = {"subagent_type": "Explore", "prompt": "x"}
    for label, script, args, payload in (
            ("capture_prompt", "capture_prompt.py", [],
             {"session_id": "s", "prompt": "a real question here"}),
            ("capture_answer", "capture_answer.py", [],
             {"session_id": "s", "last_assistant_message": "an answer here"}),
            ("capture_agent pre", "capture_agent.py", ["pre"],
             {"session_id": "s", "tool_name": "Task", "tool_input": ti}),
            ("capture_agent post", "capture_agent.py", ["post"],
             {"session_id": "s", "tool_name": "Task", "tool_input": ti,
              "tool_response": {}}),
            ("close_conversation end", "close_conversation.py", ["end"],
             {"session_id": "s"}),
            ("open_loops_brief", "open_loops_brief.py", [], {"session_id": "s"})):
        t0 = time.perf_counter()
        hook(script, payload, *args, env=live)
        ms = (time.perf_counter() - t0) * 1000
        ck(f"{label} does not wait on a 5s master", ms < 2000, f"{ms:.0f} ms")

    srv.shutdown()
    shutil.rmtree(brain, ignore_errors=True)


def main():
    g("init")
    research, src_a, src_b, write = test_tree()
    idea = test_discard_validation(src_a)
    test_recheck(idea, src_a, src_b)
    test_discard_types()
    test_comparisons(idea)
    test_probe_and_projection()
    test_context_events(research)
    test_lifecycle(src_a, write)
    test_hooks()
    test_tokens()
    test_hooks_never_block_on_network()
    shutil.rmtree(BRAIN, ignore_errors=True)
    shutil.rmtree(BRAIN + "-missing", ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
