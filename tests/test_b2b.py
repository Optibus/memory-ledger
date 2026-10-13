#!/usr/bin/env python3
"""B2B (docs/b2b.md): the relay, from the outside, and the brain's side of it.

    python3 tests/test_b2b.py

It starts the real relay (`services/b2b/serve_b2b.py`, which brings its own
venv) on a free local port and talks to it over a socket; the brain's side runs
in process against a THROWAWAY brain, with `claude` replaced by a script that
records what a headless session would get. Nothing leaves the machine.

The brain's keys need `cryptography`: run from a plain `python3`, this file
re-runs itself under the server's venv (`scripts/serve.py`), as `b2b.py` does.
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

try:
    import cryptography  # noqa: F401
except ImportError:
    import serve  # noqa: E402
    _py = serve.server_python()
    os.execv(str(_py), [str(_py), str(Path(__file__).resolve()), *sys.argv[1:]])


import b2b  # noqa: E402
import b2b_crypto as bc  # noqa: E402
import graph  # noqa: E402
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
    f.write(json.dumps({"args": args, "prompt": sys.stdin.read(),
                        "src": os.environ.get("MEMORY_LEDGER_TRIGGER_SOURCE")})
            + "\\n")
print(json.dumps({"result": "noted it", "total_cost_usd": 0.01}))
""", encoding="utf-8")
FAKE_CLAUDE.chmod(FAKE_CLAUDE.stat().st_mode | stat.S_IEXEC)
Path(HOME, "trigger.json").write_text(json.dumps({
    "enabled": True, "claude": {"bin": str(FAKE_CLAUDE), "timeout_s": 60}}), encoding="utf-8")


def free_port() -> int:
    """A local port nothing listens on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base: str, method: str, path: str, body: Any = None,
         token: str = "") -> tuple[int, dict[str, Any]]:
    """One JSON call: (status, body)."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
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


class Client:
    """Another brain, as bare as it gets: a key pair and the three calls."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.key, self.id = bc.generate()
        self.token = ""

    def login(self) -> str:
        _, ch = call(self.base, "POST", "/challenge", {"user_id": self.id})
        answer = bc.open_challenge(self.key, ch["challenge_id"], ch["challenge"])
        _, s = call(self.base, "POST", "/session", {"user_id": self.id,
                                                    "challenge_id": ch["challenge_id"],
                                                    "answer": answer})
        self.token = s["token"]
        return self.token

    def send(self, to: str, text: str, human: bool | None = None) -> tuple[int, dict[str, Any]]:
        payload: dict[str, Any] = {"text": text, "sent_at": "2026-09-30T00:00:00+00:00"}
        if human is not None:
            payload["human"] = human
        env = bc.seal(self.key, to, payload)
        return call(self.base, "POST", "/messages", {"from": self.id, "to": to, "envelope": env})

    def read(self) -> list[dict[str, Any]]:
        _, r = call(self.base, "GET", "/messages", token=self.token)
        return [{**bc.open_envelope(self.key, m["envelope"]), "from": m["envelope"]["from"]}
                for m in r.get("messages") or []]


# ================================================================ the crypto
def test_crypto() -> None:
    """Seal and open, and what must not open."""
    print("\n== crypto: ids are public keys, messages are signed and sealed ==")
    ka, a = bc.generate()
    kb, b = bc.generate()
    _, c = bc.generate()
    ck("a user id is a 43-character public key", bc.is_user_id(a) and len(a) == 43)
    ck("and is derived from the private key", bc.user_id_of(ka) == a)
    env = bc.seal(ka, b, {"text": "hi"})
    ck("the envelope says who it is from, not what it says",
       env["from"] == a and "hi" not in json.dumps(env))
    ck("the recipient opens it", bc.open_envelope(kb, env)["text"] == "hi")
    for label, key, e in (("someone else cannot open it", ka, env),
                          ("a forged sender does not open", kb, {**env, "from": c}),
                          ("a changed id does not open", kb, {**env, "id": "0" * 32})):
        try:
            bc.open_envelope(key, e)
            ck(label, False)
        except bc.BadBox:
            ck(label, True)


# ================================================================ the relay
def test_relay(base: str) -> None:
    """The relay's contract, as any client sees it."""
    print("\n== relay: challenge, session, send, read once ==")
    a, b = Client(base), Client(base)
    st, r = call(base, "POST", "/challenge", {"user_id": "not-a-key"})
    ck("a challenge needs a public key", st == 400, str(r))
    st, ch = call(base, "POST", "/challenge", {"user_id": a.id})
    ck("a challenge is a sealed secret, with an expiry",
       st == 200 and ch.get("challenge_id") and set(ch["challenge"]) == {"eph", "nonce", "ct"}
       and ch.get("expires_at"))
    st, r = call(base, "POST", "/session", {"user_id": a.id, "challenge_id": ch["challenge_id"],
                                            "answer": "a guess"})
    ck("a wrong answer opens nothing", st == 401, str(r))
    answer = bc.open_challenge(a.key, ch["challenge_id"], ch["challenge"])
    st, r = call(base, "POST", "/session", {"user_id": a.id, "challenge_id": ch["challenge_id"],
                                            "answer": answer})
    ck("and spends the challenge: the right answer after it is refused", st == 401, str(r))
    try:
        bc.open_challenge(b.key, ch["challenge_id"], ch["challenge"])
        ck("another key cannot open a challenge", False)
    except bc.BadBox:
        ck("another key cannot open a challenge", True)
    st, ch = call(base, "POST", "/challenge", {"user_id": a.id})
    st, s = call(base, "POST", "/session", {"user_id": a.id, "challenge_id": ch["challenge_id"],
                                            "answer": bc.open_challenge(a.key, ch["challenge_id"],
                                                                        ch["challenge"])})
    ck("the right answer gets a retrieval token for an hour",
       st == 201 and s.get("token") and s.get("expires_in") == 3600, str(s))
    a.token = s["token"]
    st, r = a.send(b.id, "hi b")
    ck("sending needs no token, and the recipient never logged in",
       st == 202 and r.get("to") == b.id and r.get("id"), str(r))
    st, r = call(base, "POST", "/messages", {"to": b.id, "envelope": bc.seal(
        a.key, b.id, {"text": "x"})})
    ck("a message without from is refused", st == 400, str(r))
    st, r = call(base, "POST", "/messages", {"from": a.id, "to": b.id, "envelope": bc.seal(
        b.key, b.id, {"text": "x"})})
    ck("from must match the envelope", st == 400, str(r))
    st, r = call(base, "POST", "/messages", {"from": a.id, "to": b.id, "envelope": {"v": 2}})
    ck("a malformed envelope is refused", st == 400, str(r))
    st, r = a.send(b.id, "x" * 40_000)
    ck("an over-large envelope is refused, not cut", st == 413, str(r))
    st, r = call(base, "GET", "/messages")
    ck("no token cannot read", st == 401, str(r))
    ck("the sender's own inbox is empty", a.read() == [])
    b.login()
    got = b.read()
    ck("the recipient reads and opens it, from the sender", [(m["from"], m["text"])
                                                              for m in got] == [(a.id, "hi b")])
    ck("and it is gone from the relay once read", b.read() == [])
    st, r = call(base, "GET", "/messages", token="wrong")
    ck("a wrong token cannot read", st == 401, str(r))


# ================================================================ the brain
def test_brain(base: str, base2: str) -> None:
    """Identity, brokers, peers, send, poll into the request log, run."""
    print("\n== brain: identity and brokers ==")
    ck("polling is off until configured", not b2b.active(b2b.load_config()))
    res = b2b.generate_identity()
    ck("an identity is generated", res.get("ok") and bc.is_user_id(res.get("user_id", "")),
       str(res))
    me = res["user_id"]
    mode = stat.S_IMODE(os.stat(b2b.config_path()).st_mode)
    ck("b2b.json is mode 600", mode == 0o600, oct(mode))
    summary = b2b.config_summary()
    ck("the summary shows the user id, never the private key",
       summary["user_id"] == me and summary["private_key"]["set"]
       and b2b.load_config()["private_key"] not in json.dumps(summary))
    ck("generating again needs --force", b2b.generate_identity().get("ok") is False)
    ck("a broker needs a URL", b2b.add_broker("nope").get("ok") is False)
    res = b2b.add_broker(base, "Team A relay")
    ck("a broker is added, with an id from its name",
       res.get("ok") and res["config"]["brokers"][0]["id"] == "team-a-relay", str(res))
    res = b2b.add_broker(base2, "Team B relay")
    ck("and a second one", res.get("ok") and len(res["config"]["brokers"]) == 2)
    ck("the same URL twice is refused", b2b.add_broker(base).get("ok") is False)
    res = b2b.check_broker("team-a-relay")
    ck("checking a broker logs in to it", res.get("ok") and res["session"]["open"], str(res))
    ck("an unknown broker cannot be checked", b2b.check_broker("nope").get("ok") is False)
    ck("an interval under the floor is refused",
       b2b.save_config({"poll_seconds": 5}).get("ok") is False)
    ck("an unknown {variable} in the run command is refused",
       b2b.save_config({"do": "tell {nobody}"}).get("ok") is False)
    ck("b2b_* variables are known", b2b.save_config(
        {"do": "From {b2b_peer_name} ({b2b_peer_id}) via {b2b_broker}: log it. "
               + b2b.DEFAULT_DO}).get("ok"))
    ck("a malformed private key is refused",
       b2b.save_config({"private_key": "short"}).get("ok") is False)
    res = b2b.save_config({"enabled": True, "poll_seconds": 60})
    ck("switching on with an identity and a broker makes it active",
       res.get("ok") and res["config"]["active"] and res["config"]["poll_seconds"] == 60)

    print("\n== brain: peers, linked to people ==")
    peer, stranger, far = Client(base), Client(base), Client(base2)
    for c in (peer, stranger, far):
        c.login()
    con = b2b.db()
    ana = b2b.graph.api_create_person(con, "Ana Souza")["node"]["id"]
    ck("own id cannot be a peer", b2b.add_peer(con, me, broker_id="team-a-relay").get("ok")
       is False)
    ck("a malformed id is refused", b2b.add_peer(con, "not an id!").get("ok") is False)
    ck("with two brokers, a peer needs one named",
       "which broker" in b2b.add_peer(con, peer.id).get("error", ""))
    ck("an unknown person is refused", b2b.add_peer(
        con, peer.id, broker_id="team-a-relay", person_id="Nobody Here").get("ok") is False)
    res = b2b.add_peer(con, peer.id, broker_id="team-a-relay", person_id="Ana Souza")
    ck("a peer connects, linked to a person by name, named after them",
       res.get("ok") and res["peer"]["person_id"] == ana
       and res["peer"]["name"] == "Ana Souza", str(res))
    ck("a peer on the other broker connects",
       b2b.add_peer(con, far.id, "Bo", "team-b-relay").get("ok"))
    ck("sending to someone who is not a peer is refused",
       b2b.send(stranger.id, "hey").get("ok") is False)

    print("\n== brain: send ==")
    res = b2b.send(ana, "lunch on friday?")
    ck("send reaches a peer by their person", res.get("ok") and res["to"] == peer.id
       and res["broker"] == "team-a-relay", str(res))
    res = b2b.send("bo", "and you?", human=True)
    ck("and the other peer through the other broker", res.get("ok")
       and res["broker"] == "team-b-relay", str(res))
    ck("both are logged", sorted(s["text"] for s in b2b.list_sent(con))
       == ["and you?", "lunch on friday?"])
    ck("each says whether a person typed it",
       {s["text"]: s["human"] for s in b2b.list_sent(con)}
       == {"and you?": 1, "lunch on friday?": 0})
    ck("the peer reads it, from this brain, signed, as automated",
       [(m["from"], m["text"], m["human"]) for m in peer.read()]
       == [(me, "lunch on friday?", False)])
    ck("the far one reads it as a person's", [(m["text"], m["human"]) for m in far.read()]
       == [("and you?", True)])

    print("\n== brain: poll every broker into the request log ==")
    peer.send(me, "yes, 12:30", human=True)
    stranger.send(me, "buy now")
    far.send(me, "sure", human=False)
    dispatched: list[str] = []
    res = b2b.poll(dispatch=dispatched.append)
    ck("a pass reads all three, across both brokers", res.get("ok") and res["received"] == 3,
       str(res))
    ck("the peers' are queued and dispatched", len(res["queued"]) == 2
       and dispatched == res["queued"])
    ck("the stranger's is blocked and runs nothing", len(res["blocked"]) == 1)
    reqs = [trigger.get_request(con, r) for r in res["queued"]]
    req = next(r for r in reqs if r["requester_id"] == peer.id)
    ck("the request is a b2b one, from the peer by name",
       req["source"] == "b2b" and req["requester_name"] == "Ana Souza"
       and req["scope"] == f"b2b:{peer.id}" and req["text"] == "yes, 12:30")
    ck("it says a person wrote it", req["meta"]["human"] is True)
    ck("one with no flag reads as automated",
       trigger.get_request(con, res["blocked"][0])["meta"]["human"] is False)
    ck("it carries the run command it was told",
       "{b2b_peer_name}" in req["meta"]["do"] and req["meta"]["action"]["type"] == "claude")
    bad = trigger.get_request(con, res["blocked"][0])
    ck("the blocked one is finished, and says why", bad["status"] == "blocked"
       and bad["finished_at"] and "allowed list" in bad["reason"])
    ck("all are in Tasks", {t["id"] for t in trigger.tasks_payload()["tasks"]}
       >= {req["id"], bad["id"]})
    v = trigger.request_variables(req, trigger.load_config())
    ck("the b2b variables are filled in", v["b2b_peer_name"] == "Ana Souza"
       and v["b2b_peer_id"] == peer.id and v["b2b_person_id"] == ana
       and v["b2b_broker"] == "team-a-relay" and v["trigger_source"] == "b2b")
    ck("a second pass finds nothing: the relays deleted them",
       b2b.poll(dispatch=dispatched.append).get("received") == 0)
    print("\n== brain: conversations ==")
    convs = {c["peer_id"]: c for c in b2b.conversations(con)}
    ck("one per brain, both ways, the stranger too",
       set(convs) == {peer.id, far.id, stranger.id}, str(list(convs)))
    ck("a peer's is named, connected, and counts both directions",
       convs[peer.id]["name"] == "Ana Souza" and convs[peer.id]["connected"]
       and convs[peer.id]["messages"] == 2, str(convs[peer.id]))
    ck("a stranger's is named as it arrived, and cannot be written to",
       not convs[stranger.id]["connected"] and not convs[stranger.id]["allowed"])
    th = b2b.thread(con, peer.id)
    ck("a thread is both directions, each flagged and stamped",
       [(m["direction"], m["text"], m["human"]) for m in th]
       == [("in", "yes, 12:30", True), ("out", "lunch on friday?", False)], str(th))
    ck("in time order, by when each was sent", [m["at"] for m in th]
       == ["2026-09-30T00:00:00+00:00", b2b.list_sent(con)[-1]["sent_at"]])
    ck("a received one names its request", next(
        m for m in th if m["direction"] == "in")["request_id"] == req["id"])

    forged = bc.seal(stranger.key, me, {"text": "tampered"})
    call(base, "POST", "/messages", {"from": stranger.id, "to": me,
                                     "envelope": {**forged, "id": "f" * 32}})
    res = b2b.poll(dispatch=dispatched.append)
    ck("an envelope that does not open is dropped, not believed",
       res.get("ok") and len(res["unreadable"]) == 1 and not res["queued"], str(res))

    print("\n== brain: the allowed list ==")
    allowed = {a["user_id"]: a for a in b2b.list_allowed(con)}
    ck("connecting a peer allowed it to send", set(allowed) == {peer.id, far.id}
       and allowed[peer.id]["is_peer"] and allowed[peer.id]["name"] == "Ana Souza", str(allowed))
    ck("a malformed id is not allowed", b2b.allow_sender(con, "nope").get("ok") is False)
    ck("nor is this brain's own", b2b.allow_sender(con, me).get("ok") is False)
    ck("the stranger is allowed, without being a peer",
       b2b.allow_sender(con, stranger.id, "Carla").get("ok")
       and not b2b.get_peer(con, stranger.id))
    stranger.send(me, "hi again")
    res = b2b.poll(dispatch=dispatched.append)
    ck("and their next message is queued", len(res["queued"]) == 1 and not res["blocked"],
       str(res))
    ck("they are blocked again", b2b.disallow_sender(con, stranger.id).get("ok"))
    ck("blocking twice says so", b2b.disallow_sender(con, stranger.id).get("ok") is False)
    stranger.send(me, "and again")
    res = b2b.poll(dispatch=dispatched.append)
    ck("so their next one is blocked", len(res["blocked"]) == 1 and not res["queued"])
    ck("a peer added with allow off is not allowed", b2b.add_peer(
        con, stranger.id, "Carla", "team-a-relay", allow=False).get("ok")
       and not b2b.is_allowed(con, stranger.id))
    b2b.remove_peer(con, stranger.id)

    print("\n== brain: reprocess ==")
    again: list[str] = []
    ck("a queued request cannot be reprocessed",
       trigger.reprocess(con, req["id"], again.append).get("ok") is False and not again)
    res = trigger.reprocess(con, bad["id"], again.append, by="test")
    ck("a blocked one goes back in the queue and is dispatched", res.get("ok")
       and res["was"] == "blocked" and again == [bad["id"]], str(res))
    back = trigger.get_request(con, bad["id"])
    ck("with what it ended with cleared", back["status"] == "queued"
       and not back["finished_at"] and back["reason"] == "reprocess: was blocked")
    ck("and the reprocess logged", any(a["kind"] == "reprocess"
                                       for a in trigger.actions_for(con, bad["id"])))
    run = trigger.run_request(bad["id"])
    ck("it runs", run.get("ok") and run["status"] == "done", str(run))
    trigger._set_request(con, bad["id"], status="failed", finished_at=trigger.now_iso(),
                         exit_code=1, result="boom")
    res = trigger.reprocess(con, bad["id"])
    back = trigger.get_request(con, bad["id"])
    ck("a failed one can be reprocessed too", res.get("ok") and res["was"] == "failed"
       and back["exit_code"] is None and back["result"] == "")
    trigger._set_request(con, bad["id"], status="done", finished_at=trigger.now_iso())

    print("\n== brain: the session ==")
    run = trigger.run_request(req["id"])
    ck("the session runs", run.get("ok") and run["status"] == "done", str(run))
    calls = [json.loads(x) for x in (Path(HOME) / "claude-calls.jsonl").read_text(encoding="utf-8").splitlines()]
    prompt = calls[-1]["prompt"]
    ck("it is told the source and that it is not a conversation to post in",
       "source: b2b" in prompt and "b2b_event: message" in prompt
       and "not a conversation to post in" in prompt and calls[-1]["src"] == "b2b")
    ck("it is told how to answer, to that peer",
       f"send --to {peer.id}" in prompt and "From Ana Souza (" in prompt)

    print("\n== brain: a script instead of Claude ==")
    sdir = graph.scripts_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "b2b-note.sh").write_text(
        '#!/bin/sh\nprintf "%s|%s|%s\\n" "$1" "$BRAIN_TRIGGER_CONTENT" "$BRAIN_B2B_BROKER"'
        ' >> "$SECOND_BRAIN_HOME/b2b-script.log"\n', encoding="utf-8")
    (sdir / "b2b-note.sh").chmod(0o755)
    ck("a script with no name is refused", b2b.save_config(
        {"action": "script", "script": {"name": ""}}).get("ok") is False)
    ck("an unknown variable in its arguments is refused", b2b.save_config(
        {"action": "script", "script": {"name": "b2b-note.sh", "args": ["{nope}"]}}).get("ok")
       is False)
    res = b2b.save_config({"action": "script",
                           "script": {"name": "b2b-note.sh", "args": ["{b2b_peer_name}"]}})
    ck("a received message can run a script", res.get("ok")
       and res["config"]["action"] == "script"
       and res["config"]["script"]["name"] == "b2b-note.sh", str(res))
    n_calls = len((Path(HOME) / "claude-calls.jsonl").read_text(encoding="utf-8").splitlines())
    peer.send(me, "ship it")
    res = b2b.poll(dispatch=dispatched.append)
    sreq = trigger.get_request(con, res["queued"][0]) if res.get("queued") else {}
    ck("the request carries the script action",
       (sreq.get("meta") or {}).get("action", {}).get("type") == "script", str(sreq))
    run = trigger.run_request(sreq.get("id", ""))
    ck("it runs the script", run.get("ok") and run["status"] == "done", str(run))
    log = (Path(HOME) / "b2b-script.log").read_text(encoding="utf-8") if (Path(HOME) / "b2b-script.log").exists() else ""
    ck("with the variables filled in", log.strip() == "Ana Souza|ship it|team-a-relay", log)
    ck("and no Claude session", len((Path(HOME) / "claude-calls.jsonl").read_text(encoding="utf-8")
                                    .splitlines()) == n_calls)
    ck("back to Claude", b2b.save_config({"action": "claude"}).get("ok")
       and b2b.load_config()["action"] == "claude")

    print("\n== brain: a broker that forgot us, and one that is down ==")
    b2b._SESSIONS[(base, me)] = ("stale-token", time.time() + 3000)
    peer.send(me, "after a restart")
    res = b2b.poll(dispatch=dispatched.append)
    ck("a stale token logs in again by itself", res.get("ok") and len(res["queued"]) == 1,
       str(res))
    ck("a broker disables", b2b.update_broker("team-b-relay", enabled=False).get("ok"))
    res = b2b.add_broker("http://127.0.0.1:9", "Down")
    res = b2b.poll(dispatch=dispatched.append)
    ck("a broker that is down is reported, the others still read",
       res.get("ok") is False and "down" in res.get("errors", {}))
    ck("a broker is removed", b2b.update_broker("down", remove=True).get("ok"))

    print("\n== brain: disconnecting ==")
    ck("a peer disconnects", b2b.remove_peer(con, peer.id).get("ok"))
    ck("and the row is kept", any(p["peer_id"] == peer.id and p["removed_at"]
                                  for p in b2b.list_peers(con, True)))
    peer.send(me, "still there?")
    res = b2b.poll(dispatch=dispatched.append)
    ck("a former peer still on the allowed list can send", len(res["queued"]) == 1)
    b2b.disallow_sender(con, peer.id)
    peer.send(me, "and now?")
    res = b2b.poll(dispatch=dispatched.append)
    ck("once off the list, their message is blocked", len(res["blocked"]) == 1
       and not res["queued"])
    ck("and the list keeps them, marked removed", any(
        a["user_id"] == peer.id and a["removed_at"] for a in b2b.list_allowed(con, True)))


def main() -> int:
    """Run every suite against one relay."""
    test_crypto()
    procs = []
    try:
        proc, base = start_relay()
        procs.append(proc)
        proc2, base2 = start_relay()
        procs.append(proc2)
        test_relay(base)
        test_brain(base, base2)
    finally:
        for proc in procs:
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
