#!/usr/bin/env python3
"""B2B (docs/b2b.md): the relay, from the outside, and the brain's side of it.

    python3 tests/test_b2b.py

Stdlib only. It starts the real relay (`services/b2b/serve_b2b.py`, which brings
its own venv) on a free local port and talks to it over a socket; the brain's
side runs in process against a THROWAWAY brain, with `claude` replaced by a
script that records what a headless session would get. Nothing leaves the
machine.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HOME = tempfile.mkdtemp(prefix="second-brain-b2b-")
os.environ["SECOND_BRAIN_HOME"] = HOME
os.environ["SECOND_BRAIN_NO_AUTOSYNC"] = "1"
os.environ["MEMORY_LEDGER_OWNER"] = "Owner"
for k in list(os.environ):
    if k.startswith(("SECOND_BRAIN_NEO4J", "MEMORY_LEDGER_NEO4J", "MEMORY_LEDGER_B2B_")):
        del os.environ[k]
sys.path.insert(0, str(ROOT / "scripts"))

import b2b  # noqa: E402
import trigger  # noqa: E402

FAILS: list[str] = []


def ck(label: str, cond: bool, extra: str = "") -> None:
    """Print one check, and remember it when it failed."""
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


FAKE_CLAUDE = Path(HOME) / "fake-claude"
FAKE_CLAUDE.write_text("""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.path.join(os.environ["SECOND_BRAIN_HOME"], "claude-calls.jsonl"), "a") as f:
    f.write(json.dumps({"args": args, "src": os.environ.get("MEMORY_LEDGER_TRIGGER_SOURCE")})
            + "\\n")
print(json.dumps({"result": "noted it", "total_cost_usd": 0.01}))
""")
FAKE_CLAUDE.chmod(FAKE_CLAUDE.stat().st_mode | stat.S_IEXEC)
Path(HOME, "trigger.json").write_text(json.dumps({
    "enabled": True, "claude": {"bin": str(FAKE_CLAUDE), "timeout_s": 60}}))


def free_port() -> int:
    """A local port nothing listens on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base: str, method: str, path: str, body: Any = None,
         headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    """One JSON call: (status, body)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def start_relay() -> tuple[subprocess.Popen, str]:
    """Start the relay and wait until it answers."""
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(ROOT / "services" / "b2b" / "serve_b2b.py"),
                             "--port", str(port)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 300          # the first run builds the venv
    while time.time() < deadline:
        if proc.poll() is not None:
            sys.exit("the relay exited: " + (proc.stdout.read() or b"").decode()[-2000:])
        try:
            if call(base, "GET", "/health")[1].get("ok"):
                return proc, base
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.3)
    proc.kill()
    sys.exit("the relay did not start")


def creds(user: dict[str, Any]) -> dict[str, str]:
    """The read headers for an account."""
    return {"X-B2B-User-Id": user["user_id"], "X-B2B-Token": user["token"]}


# ================================================================ the relay
def test_relay(base: str) -> None:
    """The relay's contract, as any client sees it."""
    print("\n== relay: register, send, read once ==")
    st, a = call(base, "POST", "/messages", {})
    ck("no user_id and no token registers", st == 201 and a.get("user_id") and a.get("token"))
    st, b = call(base, "POST", "/messages")
    ck("so does no body at all", st == 201 and b.get("user_id") != a.get("user_id"))
    st, r = call(base, "POST", "/messages", {"user_id": a["user_id"]})
    ck("half a credential is refused", st == 400, str(r))
    msg = {"user_id": a["user_id"], "token": a["token"], "to": b["user_id"], "text": "hi b"}
    st, r = call(base, "POST", "/messages", msg)
    ck("a message is accepted", st == 202 and r.get("to") == b["user_id"] and r.get("id"), str(r))
    st, r = call(base, "POST", "/messages", {**msg, "token": "wrong"})
    ck("a wrong token cannot send", st == 401, str(r))
    st, r = call(base, "POST", "/messages", {**msg, "to": "nobody"})
    ck("an unknown recipient is refused", st == 404, str(r))
    st, r = call(base, "POST", "/messages", {**msg, "text": "  "})
    ck("an empty message is refused", st == 400, str(r))
    st, r = call(base, "POST", "/messages", {**msg, "text": "x" * 16_001})
    ck("an over-long message is refused, not cut", st == 413, str(r))
    st, r = call(base, "GET", "/messages", headers=creds({**b, "token": a["token"]}))
    ck("someone else's token cannot read", st == 401, str(r))
    st, r = call(base, "GET", "/messages")
    ck("no credentials cannot read", st == 401, str(r))
    st, r = call(base, "GET", "/messages", headers=creds(a))
    ck("the sender's own inbox is empty", st == 200 and r.get("messages") == [])
    st, r = call(base, "GET", "/messages", headers=creds(b))
    got = r.get("messages") or []
    ck("the recipient reads it, with who sent it", st == 200 and len(got) == 1
       and got[0]["text"] == "hi b" and got[0]["from"] == a["user_id"], str(r))
    st, r = call(base, "GET", "/messages", headers=creds(b))
    ck("and it is gone from the relay once read", st == 200 and r.get("messages") == [])


# ================================================================ the brain
def test_brain(base: str) -> None:
    """Register, peers, send, poll into the request log, run."""
    print("\n== brain: config, register, peers ==")
    ck("polling is off until configured", not b2b.active(b2b.load_config()))
    res = b2b.register(base)
    ck("register stores an account", res.get("ok") and res.get("user_id"), str(res))
    me = res["user_id"]
    mode = stat.S_IMODE(os.stat(b2b.config_path()).st_mode)
    ck("b2b.json is mode 600", mode == 0o600, oct(mode))
    summary = b2b.config_summary()
    ck("the summary says a token is set, never what it is",
       summary["token"]["set"] and b2b.load_config()["token"] not in json.dumps(summary))
    ck("registering again needs --force", b2b.register().get("ok") is False)
    ck("an interval under the floor is refused",
       b2b.save_config({"poll_seconds": 5}).get("ok") is False)
    ck("an unknown {variable} in the run command is refused",
       b2b.save_config({"do": "tell {nobody}"}).get("ok") is False)
    ck("b2b_* variables are known", b2b.save_config(
        {"do": "From {b2b_peer_name} ({b2b_peer_id}): log it. " + b2b.DEFAULT_DO}).get("ok"))
    ck("switching on an http action with no url is refused",
       b2b.save_config({"enabled": True, "action": "http"}).get("ok") is False)
    res = b2b.save_config({"enabled": True, "poll_seconds": 60})
    ck("switching on with an account makes it active",
       res.get("ok") and res["config"]["active"] and res["config"]["poll_seconds"] == 60)

    _, peer = call(base, "POST", "/messages", {})
    _, stranger = call(base, "POST", "/messages", {})
    con = b2b.db()
    ck("own id cannot be a peer", b2b.add_peer(con, me).get("ok") is False)
    ck("a malformed id is refused", b2b.add_peer(con, "not an id!").get("ok") is False)
    ck("a peer connects", b2b.add_peer(con, peer["user_id"], "Ana").get("ok"))
    ck("sending to someone who is not a peer is refused",
       b2b.send(stranger["user_id"], "hey").get("ok") is False)

    print("\n== brain: send ==")
    res = b2b.send("ana", "lunch on friday?")
    ck("send reaches a peer by name", res.get("ok") and res["to"] == peer["user_id"], str(res))
    ck("and is logged", [s["text"] for s in b2b.list_sent(con)] == ["lunch on friday?"])
    _, r = call(base, "GET", "/messages", headers=creds(peer))
    ck("the peer reads it, from this brain",
       [(m["from"], m["text"]) for m in r.get("messages", [])] == [(me, "lunch on friday?")])

    print("\n== brain: poll into the request log ==")
    call(base, "POST", "/messages", {"user_id": peer["user_id"], "token": peer["token"],
                                     "to": me, "text": "yes, 12:30"})
    call(base, "POST", "/messages", {"user_id": stranger["user_id"],
                                     "token": stranger["token"], "to": me, "text": "buy now"})
    dispatched: list[str] = []
    res = b2b.poll(dispatch=dispatched.append)
    ck("a pass reads both", res.get("ok") and res["received"] == 2, str(res))
    ck("the peer's is queued and dispatched", len(res["queued"]) == 1
       and dispatched == res["queued"])
    ck("the stranger's is refused and runs nothing", len(res["refused"]) == 1)
    req = trigger.get_request(con, res["queued"][0])
    ck("the request is a b2b one, from the peer by name",
       req["source"] == "b2b" and req["requester_name"] == "Ana"
       and req["scope"] == f"b2b:{peer['user_id']}" and req["text"] == "yes, 12:30")
    ck("it carries the run command it was told",
       "{b2b_peer_name}" in req["meta"]["do"] and req["meta"]["action"]["type"] == "claude")
    bad = trigger.get_request(con, res["refused"][0])
    ck("the refused one is finished", bad["status"] == "refused" and bad["finished_at"])
    ck("both are in Tasks", {t["id"] for t in trigger.tasks_payload()["tasks"]}
       >= {req["id"], bad["id"]})
    v = trigger.request_variables(req, trigger.load_config())
    ck("the b2b variables are filled in", v["b2b_peer_name"] == "Ana"
       and v["b2b_peer_id"] == peer["user_id"] and v["trigger_source"] == "b2b")
    ck("a second pass finds nothing: the relay deleted them",
       b2b.poll(dispatch=dispatched.append).get("received") == 0)

    print("\n== brain: the session ==")
    run = trigger.run_request(req["id"])
    ck("the session runs", run.get("ok") and run["status"] == "done", str(run))
    calls = [json.loads(x) for x in (Path(HOME) / "claude-calls.jsonl").read_text().splitlines()]
    prompt = calls[-1]["args"][calls[-1]["args"].index("-p") + 1]
    ck("it is told the source and that it is not a conversation to post in",
       "source: b2b" in prompt and "b2b_event: message" in prompt
       and "not a conversation to post in" in prompt and calls[-1]["src"] == "b2b")
    ck("it is told how to answer, to that peer",
       f"send --to {peer['user_id']}" in prompt and "From Ana (" in prompt)

    print("\n== brain: disconnecting ==")
    ck("a peer disconnects", b2b.remove_peer(con, peer["user_id"]).get("ok"))
    ck("and the row is kept", any(p["peer_id"] == peer["user_id"] and p["removed_at"]
                                  for p in b2b.list_peers(con, True)))
    call(base, "POST", "/messages", {"user_id": peer["user_id"], "token": peer["token"],
                                     "to": me, "text": "still there?"})
    res = b2b.poll(dispatch=dispatched.append)
    ck("a former peer's message is refused", len(res["refused"]) == 1 and not res["queued"])
    ck("a bad relay URL is an error, not a crash",
       b2b.poll({**b2b.load_config(), "server_url": "http://127.0.0.1:9"}).get("ok") is False)


def main() -> int:
    """Run every suite against one relay."""
    proc, base = start_relay()
    try:
        test_relay(base)
        test_brain(base)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'ALL PASS'}")
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
