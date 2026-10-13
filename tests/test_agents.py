#!/usr/bin/env python3
"""Agents (docs/agents.md): conversations, their stateless turns, and the two
connectors, against a THROWAWAY brain.

    python3 tests/test_agents.py

`claude` is replaced by a script that records its argv and prints a canned
stream-json run (a Grep, a Skill, a reply ending in the question marker), and
Ollama by a local HTTP server that answers with one tool call and then text.
Nothing leaves the machine and no model is called. Stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = tempfile.mkdtemp(prefix="second-brain-agents-")
os.environ["SECOND_BRAIN_HOME"] = HOME
os.environ["SECOND_BRAIN_NO_AUTOSYNC"] = "1"
os.environ["MEMORY_LEDGER_OWNER"] = "Owner"
for k in list(os.environ):
    if k.startswith(("SECOND_BRAIN_NEO4J", "MEMORY_LEDGER_NEO4J")):
        del os.environ[k]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from _fakes import make_exe  # noqa: E402

import agents  # noqa: E402
import graph  # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool, extra: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
system = ""
if "--append-system-prompt-file" in argv:
    with open(argv[argv.index("--append-system-prompt-file") + 1], encoding="utf-8") as f:
        system = f.read()
with open(os.path.join(os.environ["SECOND_BRAIN_HOME"], "claude-argv.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": argv, "stdin": sys.stdin.read(), "system": system}) + "\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
if mode == "crash":
    sys.stderr.write("boom: no credentials\n"); sys.exit(2)
if mode == "slow":
    import time; time.sleep(60)
def p(o): print(json.dumps(o), flush=True)
p({"type": "system", "subtype": "init", "model": "claude-sonnet-5-5", "session_id": "s"})
p({"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 1000, "output_tokens": 20},
   "content": [{"type": "thinking", "thinking": "look it up"},
               {"type": "tool_use", "id": "t1", "name": "Grep", "input": {"pattern": "permit"}},
               {"type": "tool_use", "id": "t2", "name": "Skill", "input": {"skill": "recall"}}]}})
p({"type": "user", "message": {"content": [
   {"type": "tool_result", "tool_use_id": "t1", "content": "1 match"},
   {"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "denied"}], "is_error": True}]}})
p({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.0421,
   "usage": {"input_tokens": 1200, "cache_read_input_tokens": 300, "output_tokens": 80},
   "result": "The permit is due Oct 5.\nShall I add a task?\n[[needs-answer]]"})
'''


class FakeOllama(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            return self._json({"models": [{"name": "tiny:1b"}, {"name": "notools:1b"}]})
        self._json({"error": "nope"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        FakeOllama.calls.append(body)
        if body.get("model") == "notools:1b" and body.get("tools"):
            return self._json({"error": "registry.ollama.ai/notools does not support tools"}, 400)
        if body.get("tools") and not any(m["role"] == "tool" for m in body["messages"]):
            tool = "add_task" if "message:\nmake a task" in body["messages"][-1]["content"] else "recall"
            args = {"title": "Renew permit"} if tool == "add_task" else {"query": "permit"}
            return self._json({"message": {"role": "assistant", "content": "",
                                           "tool_calls": [{"function": {"name": tool, "arguments": args}}]},
                               "prompt_eval_count": 50, "eval_count": 5})
        return self._json({"message": {"role": "assistant", "content": "<think>hm</think>Done."},
                           "prompt_eval_count": 70, "eval_count": 9})


def wait_status(con, rid, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        r = con.execute("SELECT status FROM agent_msg WHERE id=?", (rid,)).fetchone()
        if r and r["status"] not in agents.PENDING:
            return r["status"]
        time.sleep(0.1)
    return "timeout"


def reply_of(con, rid):
    return agents._msg_row(con.execute("SELECT * FROM agent_msg WHERE id=?", (rid,)).fetchone())


def main() -> int:
    bin_path = make_exe(Path(HOME) / "fake-claude", FAKE_CLAUDE)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"

    print("config")
    res = agents.save_config({"connectors": [
        {"kind": "claude", "name": "Claude", "bin": str(bin_path), "default_model": "claude-sonnet-5-5"},
        {"kind": "ollama", "name": "Local", "url": url},
        {"kind": "ollama", "name": "Local"},
    ]})
    ids = [c["id"] for c in res["config"]["connectors"]]
    ck("connector ids are unique", ids == ["claude", "local", "local-2"], str(ids))
    ck("an unknown kind is refused",
       not agents.save_config({"connectors": [{"kind": "chatgpt"}]})["ok"])
    ck("an Ollama url must be http", not agents.save_config(
        {"connectors": [{"kind": "ollama", "url": "file:///etc"}]})["ok"])
    agents.save_config({"connectors": [res["config"]["connectors"][0], res["config"]["connectors"][1]]})
    payload = {c["id"]: c for c in agents.connectors_payload()}
    ck("an Ollama connector lists its host's models", payload["local"]["models"] == ["notools:1b", "tiny:1b"])
    ck("and defaults to one of them", payload["local"]["default_model"] == "notools:1b")

    con = agents.db()
    graph.api_create_node(graph.connect(), "task", "Renew the parking permit", "", {}, "open", "2026-10-05")

    print("attachments")
    import base64
    import documents
    saved = documents.save_attachment("shot.png", base64.b64encode(b"\x89PNG").decode())
    bad = agents.send(con, "see", attachments=["../etc/passwd"], start=False)
    ck("an attachment outside the attachments folder is refused", not bad["ok"])
    r0 = agents.send(con, "", attachments=[saved["path"]], start=False)
    ck("attachments alone make a message, titled by the file", r0["ok"] and r0["conversation"]["title"] == "shot.png")
    turn = agents.build_turn(con, dict(con.execute("SELECT * FROM agent_msg WHERE id=?", (r0["reply_id"],)).fetchone()))
    ck("the agent is told where the file is on disk", str(documents.root("attachments") / saved["path"]) in turn["prompt"])

    print("claude, read-only")
    r = agents.send(con, "What must I renew?", start=False)
    ck("a first message makes the conversation, titled from it",
       r["ok"] and r["conversation"]["title"] == "What must I renew?")
    ck("it starts read-only", r["conversation"]["full_tools"] is False)
    out = agents.run_reply(r["reply_id"])
    ck("the reply runs", out == {"ok": True, "status": "question"}, str(out))
    m = reply_of(con, r["reply_id"])
    ck("the question marker is gone from the text", m["text"] == "The permit is due Oct 5.\nShall I add a task?",
       repr(m["text"]))
    kinds = [(e["kind"], e["name"]) for e in m["events"]]
    ck("tools, skills and thinking are logged", kinds == [("thinking", "thinking"), ("tool", "Grep"), ("skill", "recall")],
       str(kinds))
    ck("a tool's result lands on its call", m["events"][1]["output"] == "1 match" and m["events"][2]["error"])
    ck("claude's own cost wins over the estimate", m["cost_usd"] == 0.0421 and not m["cost_estimated"])
    ck("tokens come from the final usage", (m["tokens_in"], m["tokens_out"]) == (1500, 80))
    ck("the recalled task was in the turn's context",
       any(c.startswith("task_renew-the-parking-permit") for c in m["context"]), str(m["context"]))
    call = json.loads((Path(HOME) / "claude-argv.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    argv, prompt = call["argv"], call["stdin"]
    ck("read-only cuts the tool set and denies the rest",
       "--tools=Read,Grep,Glob,WebSearch,WebFetch,Skill,Bash" in argv and "dontAsk" in argv
       and "--dangerously-skip-permissions" not in argv)
    allowed = next(a for a in argv if a.startswith("--allowedTools=")).split("=", 1)[1].split(",")
    ck("read-only Bash is the brain's own commands only",
       [a for a in allowed if a.startswith("Bash")] == [f"Bash({c}:*)" for c in agents.BRAIN_CMDS], str(allowed))
    ck("read-only connectors read and never write",
       "mcp__atlassian__getJiraIssue" in allowed and "mcp__claude_ai_Slack__slack_read_thread" in allowed
       and not any(w in a for a in allowed for w in ("send", "create", "edit", "update", "delete", "trash",
                                                      "executeWrite", "executeDestructive", "comment")), str(allowed))
    system = call["system"]
    ck("neither prompt is on the command line: the prompt is on stdin, the system prompt in a file",
       "--append-system-prompt-file" in argv and "-p" in argv and argv[argv.index("-p") + 1].startswith("--")
       and not any("What must I renew?" in a for a in argv))
    ck("the system prompt names the conversation and how to read it",
       r["conversation"]["id"] in system and f"{agents.THREAD_CMD} {r['conversation']['id']}" in system, system)
    ck("the system prompt gives the brain's commands, to read and write without asking",
       f"{agents.GRAPH_CMD} match-rules" in system and f"{agents.GRAPH_CMD} add-node" in system
       and "without asking" in system and "READ-ONLY" in system, system)
    ck("the prompt carries the message and the recalled node",
       "What must I renew?" in prompt and "Renew the parking permit" in prompt)
    ck("the conversation badge is a question", agents.conv_state(con, r["conversation"]["id"]) == "question")
    node = con.execute("SELECT * FROM nodes WHERE id=?", (m["node_id"],)).fetchone()
    ck("the exchange is written to the graph", node is not None and "Oct 5" in node["body"])
    conv = agents.get_conv(con, r["conversation"]["id"])
    edge = con.execute("SELECT 1 FROM edges WHERE src=? AND dst=? AND rel='part-of'",
                       (m["node_id"], conv["node_id"])).fetchone()
    ck("under the conversation's node", edge is not None)

    print("the context window")
    cid = conv["id"]
    r2 = agents.send(con, "yes", cid, full_tools=True, start=False)
    agents.run_reply(r2["reply_id"])
    call = json.loads((Path(HOME) / "claude-argv.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    argv, prompt = call["argv"], call["stdin"]
    ck("one earlier exchange is carried by default", "Earlier in this conversation" in prompt
       and "What must I renew?" in prompt)
    ck("tools on runs without prompts", "--dangerously-skip-permissions" in argv and "--tools=" not in " ".join(argv))
    agents.save_conversation(con, cid, context_turns=0)
    r3 = agents.send(con, "and?", cid, start=False)
    agents.run_reply(r3["reply_id"])
    call = json.loads((Path(HOME) / "claude-argv.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    argv, prompt = call["argv"], call["stdin"]
    ck("zero carries none", "Earlier in this conversation" not in prompt)
    ck("the window is clamped", agents.save_conversation(con, cid, context_turns=99)["conversation"]["context_turns"] == 10)

    print("failures")
    os.environ["FAKE_CLAUDE_MODE"] = "crash"
    r4 = agents.send(con, "again", cid, start=False)
    out = agents.run_reply(r4["reply_id"])
    del os.environ["FAKE_CLAUDE_MODE"]
    m = reply_of(con, r4["reply_id"])
    ck("a crash is an error with claude's reason", m["status"] == "error" and "no credentials" in m["error"], m["error"])
    ck("the badge says so", agents.conv_state(con, cid) == "error")
    ck("retry queues it again", agents.retry(con, r4["reply_id"], start=False)["ok"]
       and reply_of(con, r4["reply_id"])["status"] == "queued")
    ck("a done reply cannot be retried", not agents.retry(con, r["reply_id"], start=False)["ok"])

    print("switching mid-conversation, and ollama")
    res = agents.save_conversation(con, cid, connector="local", model="tiny:1b")
    ck("connector and model switch", res["ok"] and res["conversation"]["connector"] == "local")
    ck("an unknown connector is refused", not agents.save_conversation(con, cid, connector="nope")["ok"])
    r5 = agents.send(con, "what is due?", cid, full_tools=False, start=False)
    out = agents.run_reply(r5["reply_id"])
    m = reply_of(con, r5["reply_id"])
    ck("ollama answers after its tool call", out["ok"] and m["text"] == "Done.", repr(m["text"]))
    ck("the tool call is logged with its result", m["events"][0]["name"] == "recall"
       and "parking permit" in m["events"][0]["output"].lower(), str(m["events"][:1]))
    ck("read-only ollama still reads and writes the brain",
       {t["function"]["name"] for t in FakeOllama.calls[0]["tools"]}
       == {"recall", "search", "get_node", "read_thread", "add_note", "add_task"})
    ck("ollama is told to read the thread with its tool",
       "read_thread" in FakeOllama.calls[0]["messages"][0]["content"])
    ck("read_thread reads this conversation back",
       "What must I renew?" in agents.ollama_tool_call("read_thread", {}, cid))
    last2 = agents.ollama_tool_call("read_thread", {"limit": 2}, cid)
    ck("read_thread keeps the last N", "(2 messages)" in last2 and "Oct 5" not in last2, last2)
    ck("a local model costs nothing, tokens are counted", m["cost_usd"] == 0 and m["tokens_in"] == 120)
    ck("each reply records who wrote it", m["model"] == "tiny:1b" and reply_of(con, r["reply_id"])["model"] == "claude-sonnet-5-5")
    ck("a brain write works while read-only", agents.ollama_tool_call("add_task", {"title": "x"})["ok"])
    FakeOllama.calls.clear()
    r6 = agents.send(con, "make a task", cid, full_tools=True, start=False)
    agents.run_reply(r6["reply_id"])
    ck("tools on, ollama writes too", graph.connect().execute(
        "SELECT 1 FROM nodes WHERE title='Renew permit' AND type='task'").fetchone() is not None)
    agents.save_conversation(con, cid, model="notools:1b")
    r7 = agents.send(con, "plain?", cid, start=False)
    out = agents.run_reply(r7["reply_id"])
    m = reply_of(con, r7["reply_id"])
    ck("a model without tools answers without them", out["ok"] and m["events"][0]["name"] == "no tools", str(out))

    print("list, hide, threads")
    agents.save_conversation(con, cid, hidden=True)
    rows = agents.conversations(con)
    ck("a hidden conversation is still listed, flagged", rows[0]["hidden"] is True)
    t = agents.thread(con, cid)
    ck("the thread sums its cost", round(t["conversation"]["cost_usd"], 4) == round(0.0421 * 3, 4),
       str(t["conversation"]["cost_usd"]))
    ck("messages alternate, the reply after its question",
       [x["role"] for x in t["messages"][:2]] == ["user", "agent"])

    out = subprocess.run([sys.executable, str(agents.SCRIPT), "thread", cid], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env=os.environ)
    ck("the thread command prints the thread", out.returncode == 0 and "Owner:" in out.stdout
       and "Oct 5" in out.stdout and agents.QUESTION_MARK not in out.stdout, out.stdout + out.stderr)

    print("background dispatch and recovery")
    agents.save_conversation(con, cid, connector="claude", model="claude-sonnet-5-5")
    r8 = agents.send(con, "async", cid)
    ck("a sent message runs in the background", wait_status(con, r8["reply_id"]) == "question")
    con.execute("UPDATE agent_msg SET status='running', beat_at=0 WHERE id=?", (r8["reply_id"],))
    con.commit()
    ck("a reply left running fails on restart", agents.recover() == 1
       and "interrupted" in reply_of(con, r8["reply_id"])["error"])
    con.execute("UPDATE agent_msg SET status='running', owner_pid=?, beat_at=0 WHERE id=?",
                (os.getppid(), r8["reply_id"]))
    con.commit()
    ck("a reply whose server is still alive survives another server's start",
       agents.recover() == 0 and reply_of(con, r8["reply_id"])["status"] == "running")
    con.execute("UPDATE agent_msg SET owner_pid=0, beat_at=? WHERE id=?",
                (time.time(), r8["reply_id"]))
    con.commit()
    ck("a reply with a fresh heartbeat survives even if its pid is unseen",
       agents.recover() == 0 and reply_of(con, r8["reply_id"])["status"] == "running")
    con.execute("UPDATE agent_msg SET status='error' WHERE id=?", (r8["reply_id"],))
    con.commit()
    ck("the next-action line is split off the reply",
       agents.strip_next("Done.\n[[next: Run the tests]]") == ("Done.", "Run the tests")
       and agents.strip_next("Plain") == ("Plain", ""))
    ck("an empty message is refused", not agents.send(con, "  ", cid, start=False)["ok"])

    print("cancel")
    r9 = agents.send(con, "queued only", cid, start=False)
    res = agents.cancel(con, r9["reply_id"])
    m = reply_of(con, r9["reply_id"])
    ck("a queued reply cancels at once, as a failure Retry can run",
       res["ok"] and res["was"] == "queued" and m["status"] == "error"
       and m["error"] == agents.CANCELLED, str(res))
    ck("a reply that ended cannot be cancelled", not agents.cancel(con, r9["reply_id"])["ok"])
    ck("and it never starts", not agents.run_reply(r9["reply_id"])["ok"]
       and reply_of(con, r9["reply_id"])["error"] == agents.CANCELLED)
    os.environ["FAKE_CLAUDE_MODE"] = "slow"
    try:
        r10 = agents.send(con, "take your time", cid)
        end = time.time() + 10
        while time.time() < end and r10["reply_id"] not in agents._procs:
            time.sleep(0.05)
        t0 = time.time()
        res = agents.cancel(con, r10["reply_id"])
        ck("a running reply cancels, its session killed",
           res["ok"] and res["was"] == "running" and wait_status(con, r10["reply_id"]) == "error"
           and time.time() - t0 < 5, str(res))
        end = time.time() + 10
        while time.time() < end and agents.running_count():
            time.sleep(0.05)
        m = reply_of(con, r10["reply_id"])
        ck("it stays cancelled after its thread ends", m["error"] == agents.CANCELLED
           and not agents._cancelled and not agents._procs, m["error"])
        os.environ["FAKE_CLAUDE_MODE"] = "ok"
        agents.retry(con, r10["reply_id"])
        ck("and Retry runs it again", wait_status(con, r10["reply_id"]) == "question")
    finally:
        os.environ["FAKE_CLAUDE_MODE"] = "ok"

    srv.shutdown()
    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'ALL PASS'}")
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
