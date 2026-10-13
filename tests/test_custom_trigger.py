#!/usr/bin/env python3
"""Custom triggers (docs/trigger.md#custom-triggers): validation, the block
engine, the isolated runner, and a run end to end.

    python3 tests/test_custom_trigger.py

Stdlib only, against a THROWAWAY brain. The HTTP the triggers call is a server
on 127.0.0.1 that this file starts; nothing leaves the machine. The triggers
are written JSON-shaped (JSON is YAML), so the suite runs without PyYAML; one
check writes real YAML when PyYAML is there.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
from contextlib import redirect_stdout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = tempfile.mkdtemp(prefix="second-brain-custom-")
os.environ["SECOND_BRAIN_HOME"] = HOME
os.environ["SECOND_BRAIN_NO_AUTOSYNC"] = "1"
os.environ["MEMORY_LEDGER_OWNER"] = "Owner"
for k in list(os.environ):
    if k.startswith(("SECOND_BRAIN_NEO4J", "MEMORY_LEDGER_NEO4J")):
        del os.environ[k]
sys.path.insert(0, str(ROOT / "scripts"))

import _trigger_block_runner as blocks  # noqa: E402
import custom_trigger as ct  # noqa: E402
import graph  # noqa: E402
import trigger  # noqa: E402

FAILS: list[str] = []
SENTINEL = "s3cr3t-SENTINEL-value-42"


def ck(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


# ---------------------------------------------------------------- fixtures
class Site:
    status = 503
    body = '{"ok": false, "items": [{"id": 1}, {"id": 2}]}'
    seen_headers: list = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        Site.seen_headers.append(dict(self.headers))
        data = Site.body.encode()
        self.send_response(Site.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{SERVER.server_address[1]}/health"

CFG = {"enabled": True, "custom": {"enabled": True, "secrets": {"TOKEN": SENTINEL},
                                   "max_events_per_run": 20},
       "claude": {"bin": "false"}}

SITE_HEALTH = {
    "id": "site-health", "name": "Site health", "enabled": False, "interval": "5m",
    "do": "Tell the owner {event_title}.",
    "config": {"url": URL},
    "secrets": ["TOKEN"],
    "blocks": [
        {"id": "check", "type": "http_request", "url": "{{config.url}}",
         "headers": {"Authorization": "Bearer {{secrets.TOKEN}}"}},
        {"id": "down", "type": "condition", "if": "prev.status >= 400",
         "then": "continue", "else": "stop"},
        {"id": "alert", "type": "emit",
         "event": {"id": "site-health:{{prev.status}}", "title": "site down: {{prev.status}}",
                   "payload": {"status": "{{prev.status}}"}}},
    ],
}


def write(defn: dict, name: str = "") -> str:
    ct.triggers_dir().mkdir(parents=True, exist_ok=True)
    tid = name or defn["id"]
    (ct.triggers_dir() / f"{tid}.yaml").write_text(json.dumps(defn, indent=2), encoding="utf-8")
    return tid


def script(name: str, source: str) -> None:
    graph.scripts_dir().mkdir(parents=True, exist_ok=True)
    (graph.scripts_dir() / name).write_text(source, encoding="utf-8")


def with_blocks(*bl, **extra) -> dict:
    return {"id": "t", "interval": "1m", "blocks": list(bl), **extra}


def counts(con) -> dict:
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("trigger_request", "trigger_checkpoint", "custom_trigger_run",
                      "custom_trigger_state", "nodes")}


class FakeIO(blocks.IO):
    def __init__(self, answers):
        super().__init__(http=self._http, now=lambda: datetime(2026, 10, 12, tzinfo=timezone.utc))
        self.answers, self.calls = list(answers), []

    def _http(self, method, url, headers=None, data=None, timeout=30):
        self.calls.append((method, url, headers, data))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


# ---------------------------------------------------------------- phase 1
def test_validate():
    print("validate")
    v = ct.validate(SITE_HEALTH, CFG, expect_id="site-health")
    ck("the spec's example is valid", v["ok"] and not v["warnings"]
       or v["ok"] and all("memory" in w for w in v["warnings"]), v)

    def bad(label, defn, needle, cfg=CFG):
        r = ct.validate(defn, cfg)
        ck(label, not r["ok"] and any(needle in e for e in r["errors"]), r["errors"])

    good_emit = {"id": "e", "type": "emit", "event": {"id": "x"}}
    bad("an unsafe id", {**with_blocks(good_emit), "id": "../x"}, "id must be")
    bad("an interval under 30s", with_blocks(good_emit, interval="10s") | {"interval": "10s"},
        "at least 30s")
    bad("an interval that does not parse", with_blocks(good_emit) | {"interval": "soon"},
        "interval must look like")
    bad("a mode other than poll", with_blocks(good_emit) | {"mode": "webhook"}, "reserved")
    bad("a block id used twice", with_blocks(good_emit, dict(good_emit)), "used twice")
    bad("an unknown block type", with_blocks({"id": "a", "type": "loop"}, good_emit),
        "type must be one of")
    bad("an http_request with no url", with_blocks({"id": "a", "type": "http_request"},
                                                   good_emit), "needs url")
    bad("then that is not continue or stop",
        with_blocks({"id": "c", "type": "condition", "if": "true", "then": "jump"}, good_emit),
        "continue or stop")
    bad("prev in the first block",
        with_blocks({"id": "e", "type": "emit", "event": {"id": "{{prev.x}}"}}),
        "first block has no prev")
    bad("a reference with an unknown root",
        with_blocks({"id": "e", "type": "emit", "event": {"id": "{{env.HOME}}"}}),
        "a reference starts with")
    bad("config.x that config does not have",
        with_blocks({"id": "e", "type": "emit", "event": {"id": "{{config.nope}}"}},
                    config={"url": "x"}), "config.nope is not in config")
    bad("a secret trigger.json does not have", with_blocks(good_emit, secrets=["MISSING"]),
        "secret MISSING")
    bad("a secret used but not declared",
        with_blocks({"id": "e", "type": "emit", "event": {"id": "{{secrets.TOKEN}}"}}),
        "not one of the trigger's secrets")
    bad("a timeout out of range", with_blocks(good_emit, limits={"timeout_s": 601}),
        "limits.timeout_s")
    bad("memory out of range", with_blocks(good_emit, limits={"memory_mb": 8}),
        "limits.memory_mb")
    bad("retry attempts out of range", with_blocks(good_emit, retry={"attempts": 9}),
        "retry.attempts")
    bad("a script that is not there",
        with_blocks({"id": "s", "type": "script", "script": "nope.py"}, good_emit),
        "no script nope.py")
    script("hello.sh", "echo hi\n")
    bad("a script that is not Python",
        with_blocks({"id": "s", "type": "script", "script": "hello.sh"}, good_emit),
        "Python only")
    bad("http_request with network off",
        with_blocks({"id": "h", "type": "http_request", "url": "http://x"}, good_emit,
                    limits={"network": False}), "network is false")
    bad("a condition that calls something",
        with_blocks({"id": "c", "type": "condition", "if": "__import__('os').system('x')"},
                    good_emit), "may not use Call")
    bad("a condition that reads a dunder",
        with_blocks({"id": "c", "type": "condition", "if": "prev.__class__ == 1"}, good_emit),
        "may not start with _")
    bad("item outside an emit with each",
        with_blocks({"id": "e", "type": "emit", "event": {"id": "{{item.id}}"}}),
        "only there inside an emit with each")
    r = ct.validate(with_blocks({"id": "e", "type": "emit", "event": {"title": "x"}}), CFG)
    ck("an emit with no event id is a warning", r["ok"] and any("stable id" in w
                                                                for w in r["warnings"]), r)
    r = ct.validate(with_blocks({"id": "h", "type": "http_request", "url": "http://x"}), CFG)
    ck("no emit at the end is a warning", r["ok"] and any("always ends skipped" in w
                                                          for w in r["warnings"]), r)
    script("ok.py", "result = 1\n")
    r = ct.validate(with_blocks({"id": "s", "type": "script", "script": "ok.py"}, good_emit,
                                limits={"network": False}), CFG)
    ck("network off with a script block warns that it is not enforced",
       r["ok"] and any("can still open a connection" in w for w in r["warnings"]), r)


# ---------------------------------------------------------------- phase 2
def test_engine():
    print("engine")
    scope = {"prev": {"a": {"b": [10, {"c": "deep"}]}, "n": 3}, "config": {"u": "x"}}
    ck("a nested path", blocks.lookup(scope, "prev.a.b[1].c") == "deep")
    ck("a missing path is None", blocks.lookup(scope, "prev.a.zz.c") is None)
    ck("an index out of range is None", blocks.lookup(scope, "prev.a.b[9]") is None)
    ck("a lone reference keeps its type", blocks.resolve("{{prev.a.b}}", scope)[0] == 10)
    ck("a reference in text becomes text", blocks.resolve("n={{prev.n}}!", scope) == "n=3!")
    ck("a missing reference in text is empty", blocks.resolve("[{{prev.no}}]", scope) == "[]")
    ck("conditions compare", blocks.evaluate("prev.n >= 3 and config.u == 'x'", scope))
    ck("in and not", blocks.evaluate("not ('y' in [config.u, 'z'])", scope))
    ck("a missing field fails a comparison, it does not crash",
       blocks.evaluate("prev.none > 400", scope) is False)
    ck("{{...}} in a condition reads the same", blocks.evaluate("{{prev.n}} == 3", scope))
    try:
        blocks.evaluate("(lambda: 1)()", scope)
        ck("a lambda is refused", False)
    except blocks.BlockError:
        ck("a lambda is refused", True)

    io503 = FakeIO([(503, "down")])
    r = blocks.run_blocks(SITE_HEALTH, {"secrets": {"TOKEN": "t"}}, io503)
    ck("503 emits one event", r["status"] == "ok" and len(r["events"]) == 1
       and r["events"][0]["id"] == "site-health:503", r)
    ck("the secret reached the header", io503.calls[0][2].get("Authorization") == "Bearer t")
    r = blocks.run_blocks(SITE_HEALTH, {}, FakeIO([(200, "ok")]))
    ck("200 ends skipped", r["status"] == "skipped" and not r["events"], r)
    r = blocks.run_blocks(SITE_HEALTH, {}, FakeIO([blocks.BlockError("refused")]))
    ck("a network failure without on_error is an error", r["status"] == "error"
       and "refused" in r["error"], r)
    tolerant = json.loads(json.dumps(SITE_HEALTH))
    tolerant["blocks"][0]["on_error"] = "continue"
    tolerant["blocks"][1]["if"] = "prev.status == None or prev.status >= 400"
    r = blocks.run_blocks(tolerant, {}, FakeIO([blocks.BlockError("refused")]))
    ck("on_error: continue leaves prev.status None, and the flow goes on",
       r["status"] == "ok" and r["events"][0]["id"] == "site-health:", r)
    listing = with_blocks(
        {"id": "get", "type": "http_request", "url": "http://x"},
        {"id": "shape", "type": "transform", "set": {"items": "{{prev.json.items}}"},
         "pick": ["status"], "drop": ["status"]},
        {"id": "each", "type": "emit", "each": "{{prev.items}}",
         "event": {"id": "item-{{item.id}}", "title": "new {{item.id}}", "payload": "{{item}}"},
         "checkpoint": "{{prev.items[-1].id}}"})
    r = blocks.run_blocks(listing, {}, FakeIO([(200, '{"items": [{"id": 1}, {"id": 2}]}')]))
    ck("a list in emit is one event per item", [e["id"] for e in r["events"]]
       == ["item-1", "item-2"] and r["events"][1]["payload"] == {"id": 2}, r)
    ck("emit moves the checkpoint", r["checkpoint"] == "2", r)
    r = blocks.run_blocks(listing, {"max_events": 1},
                          FakeIO([(200, '{"items": [{"id": 1}, {"id": 2}]}')]))
    ck("more events than the limit is an error", r["status"] == "error"
       and "limit is 1" in r["error"], r)
    r = blocks.run_blocks(with_blocks({"id": "s", "type": "stop"},
                                      {"id": "e", "type": "emit", "event": {"id": "x"}}), {})
    ck("stop ends the run before emit", r["status"] == "skipped", r)


# ---------------------------------------------------------------- phase 3
def iso_ctx(defn, **extra):
    return {**ct.context(defn, CFG, ""), **extra}


def test_isolation():
    print("isolation")
    Site.status = 503
    r = ct.run_isolated(iso_ctx(SITE_HEALTH), 30)
    ck("the runner calls real HTTP and emits", r["status"] == "ok"
       and r["events"][0]["id"] == "site-health:503", r)
    script("slow.py", "import time\ntime.sleep(30)\nresult = 1\n")
    defn = with_blocks({"id": "s", "type": "script", "script": "slow.py"},
                       {"id": "e", "type": "emit", "event": {"id": "x"}})
    t0 = datetime.now()
    r = ct.run_isolated(iso_ctx(defn), 2)
    took = (datetime.now() - t0).total_seconds()
    ck("a timeout kills it", r["status"] == "error" and "killed after 2s" in r["error"]
       and took < 15, f"{r} in {took:.1f}s")
    script("loud.py", "import sys\nsys.__stdout__.write('x' * 2_000_000)\nresult = 1\n")
    r = ct.run_isolated(iso_ctx(with_blocks({"id": "s", "type": "script", "script": "loud.py"})),
                        30)
    ck("more than 1 MB of output is an error", r["status"] == "error"
       and "more than" in r["error"], r)
    script("garbage.py", "import os, sys\nsys.__stdout__.write('not json')\n"
                         "sys.__stdout__.flush()\nos._exit(0)\n")
    r = ct.run_isolated(iso_ctx(with_blocks({"id": "s", "type": "script",
                                             "script": "garbage.py"})), 30)
    ck("output that is not JSON is an error", r["status"] == "error"
       and "not JSON" in r["error"], r)
    os.environ["LEAK_ME"] = "parent-only"
    script("env.py", "import os\nresult = {'env': dict(os.environ), 'prev_in': prev}\n")
    r = ct.run_isolated(iso_ctx(with_blocks(
        {"id": "s", "type": "script", "script": "env.py"},
        {"id": "e", "type": "emit", "event": {"id": "x", "payload": "{{prev.env}}"}})), 30)
    env = r["events"][0]["payload"] if r.get("events") else {"?": "?"}
    ck("the child does not see the parent's environment", "LEAK_ME" not in env
       and "SECOND_BRAIN_HOME" not in env, sorted(env))
    script("cursor.py", "result = {'n': 5}\ncheckpoint = 'c-' + (checkpoint or '0')\n")
    r = ct.run_isolated(iso_ctx(with_blocks(
        {"id": "s", "type": "script", "script": "cursor.py"},
        {"id": "e", "type": "emit", "event": {"id": "n{{prev.n}}"}}), checkpoint="7"), 30)
    ck("a script gets prev/checkpoint and may move the checkpoint",
       r["status"] == "ok" and r["checkpoint"] == "c-7" and r["events"][0]["id"] == "n5", r)
    script("leak.py", "raise RuntimeError('token is ' + secrets['TOKEN'])\n")
    defn = with_blocks({"id": "s", "type": "script", "script": "leak.py"}, secrets=["TOKEN"])
    r = ct.redact(ct.run_isolated(iso_ctx(defn), 30), [SENTINEL])
    ck("a secret in an error is blotted out", SENTINEL not in json.dumps(r)
       and "***" in json.dumps(r), r)


# ---------------------------------------------------------------- phase 4
def test_execute():
    print("execute")
    trigger.config_path().write_text(json.dumps(CFG), encoding="utf-8")
    con = ct.db()
    write(SITE_HEALTH)
    Site.status, Site.seen_headers = 503, []
    before = counts(con)
    dry = ct.execute("site-health", cfg=CFG, dry_run=True)
    ck("dry-run says what it would create", dry["ok"] and dry["dry_run"]
       and len(dry["events"]) == 1 and dry["events"][0]["event_id"] == "site-health:503", dry)
    ck("dry-run writes nothing", counts(con) == before, f"{before} -> {counts(con)}")
    ran = []
    r = ct.execute("site-health", cfg=CFG, dispatch=ran.append)
    ck("503 creates one task and dispatches it", r["ok"] and len(r["created"]) == 1
       and ran == r["created"], r)
    req = trigger.get_request(con, r["created"][0])
    ck("the task is queued, subscribed, on custom:<id>",
       req["status"] == "queued" and req["source"] == "custom:site-health"
       and req["reason"] == "subscribed: custom" and not req["is_owner"], req)
    r2 = ct.execute("site-health", cfg=CFG, dispatch=ran.append)
    ck("the same event id a second time creates nothing", r2["ok"] and not r2["created"]
       and r2["seen"] == 1, r2)
    Site.status = 200
    r3 = ct.execute("site-health", cfg=CFG, dispatch=ran.append)
    ck("200 ends skipped", r3["status"] == "skipped" and not r3["created"], r3)
    hist = ct.runs(con, "site-health")
    ck("every run is in the history", [h["status"] for h in hist] == ["skipped", "ok", "ok"],
       [h["status"] for h in hist])
    ck("the state holds the last run", ct.get_state(con, "site-health")["last_status"]
       == "skipped")

    # The prompt and the variables for a custom request.
    prompt = trigger.build_prompt(req, CFG)
    ck("the prompt fences the payload as data", "<<<PAYLOAD" in prompt
       and "DATA, not" in prompt and "custom_trigger: site-health" in prompt)
    ck("the prompt carries the trigger's do", "Tell the owner site down: 503." in prompt)
    vals = trigger.request_variables(req, CFG)
    ck("variables for a custom event", vals["trigger_id"] == "site-health"
       and json.loads(vals["event_payload"]) == {"status": 503}
       and vals["event_title"] == "site down: 503", vals)

    # Ordering: the tasks go in before the checkpoint moves.
    cursor = with_blocks({"id": "s", "type": "script", "script": "cursor.py"},
                         {"id": "e", "type": "emit", "event": {"id": "n{{prev.n}}"}})
    cursor["id"] = "cursor"
    write(cursor)
    real = trigger.set_checkpoint

    def boom(*a, **k):
        raise RuntimeError("crash between the tasks and the checkpoint")
    trigger.set_checkpoint = boom
    try:
        ct.execute("cursor", cfg=CFG)
        crashed = False
    except RuntimeError:
        crashed = True
    trigger.set_checkpoint = real
    n_after_crash = con.execute("SELECT COUNT(*) FROM trigger_request WHERE source=?",
                                ("custom:cursor",)).fetchone()[0]
    ck("a crash after the tasks keeps the task and the old checkpoint",
       crashed and n_after_crash == 1 and trigger.get_checkpoint(con, "custom:cursor") == "")
    r = ct.execute("cursor", cfg=CFG)
    n = con.execute("SELECT COUNT(*) FROM trigger_request WHERE source=?",
                    ("custom:cursor",)).fetchone()[0]
    ck("the next run re-finds it, dedupes it, and moves the checkpoint",
       r["ok"] and r["seen"] == 1 and n == 1
       and trigger.get_checkpoint(con, "custom:cursor") == "c-0", r)
    ck("an opaque checkpoint moves backwards too",
       ct.execute("cursor", cfg=CFG)["ok"]
       and trigger.get_checkpoint(con, "custom:cursor") == "c-c-0")

    # Retry: each attempt is in the log.
    flaky = with_blocks({"id": "h", "type": "http_request", "url": "http://127.0.0.1:9/x"},
                        {"id": "e", "type": "emit", "event": {"id": "x"}},
                        retry={"attempts": 3, "backoff_s": 0})
    flaky["id"] = "flaky"
    write(flaky)
    r = ct.execute("flaky", cfg=CFG, sleep=lambda s: None)
    ck("a failing run is retried, each attempt logged", r["status"] == "error"
       and r["attempts"] == 3 and len(ct.runs(con, "flaky")[0]["log"]) == 3, r)

    # The sentinel secret never lands anywhere.
    echo = with_blocks({"id": "h", "type": "http_request", "url": URL + "?k={{secrets.TOKEN}}"},
                       {"id": "e", "type": "emit",
                        "event": {"id": "echo", "title": "{{secrets.TOKEN}}",
                                  "payload": {"h": "{{secrets.TOKEN}}"}}},
                       secrets=["TOKEN"])
    echo["id"] = "echo"
    write(echo)
    Site.status = 200
    buf = io.StringIO()
    with redirect_stdout(buf):
        trigger.main(["custom", "dry-run", "echo"])
        trigger.main(["custom", "run", "echo"])
        trigger.main(["custom", "runs", "echo"])
        trigger.main(["custom", "show", "echo"])
    dump = buf.getvalue()
    for table in ("trigger_request", "custom_trigger_run", "custom_trigger_state",
                  "trigger_checkpoint", "trigger_action", "nodes"):
        dump += json.dumps([dict(r) for r in con.execute(f"SELECT * FROM {table}")], default=str)
    ck("the secret is in no output, no table and no node", SENTINEL not in dump
       and "***" in dump)
    ck("but it was sent", any(SENTINEL in (h.get("Authorization") or "")
                              for h in Site.seen_headers))


# ---------------------------------------------------------------- phase 6
def test_cli():
    print("cli")
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = trigger.main(["custom", "list"])
    listed = json.loads(buf.getvalue())
    ck("list shows every trigger", code == 0 and {t["id"] for t in listed["triggers"]}
       >= {"site-health", "cursor", "flaky", "echo"}, listed)
    write({"id": "broken", "interval": "1s", "blocks": []})
    with redirect_stdout(io.StringIO()):
        ck("validate exits 1 on a bad trigger", trigger.main(["custom", "validate", "broken"]) == 1)
        ck("validate exits 0 on a good one",
           trigger.main(["custom", "validate", "site-health"]) == 0)
        ck("enable refuses a broken trigger", trigger.main(["custom", "enable", "broken"]) == 1)
    (ct.triggers_dir() / "commented.yaml").write_text(
        "# keep me\nid: commented\nenabled: false  # off for now\ninterval: 1m\nblocks: []\n",
        encoding="utf-8")
    ct.set_enabled("commented", True)
    text = (ct.triggers_dir() / "commented.yaml").read_text(encoding="utf-8")
    ck("enable rewrites only the enabled line", text.startswith("# keep me\nid: commented\n"
                                                                "enabled: true\n"), text)
    ct.set_enabled("site-health", True)
    ck("enable on a JSON-shaped file", ct.load("site-health")[0]["enabled"] is True)
    trigger.config_path().write_text(json.dumps({**CFG, "custom": {**CFG["custom"],
                                                                   "enabled": False}}),
                                     encoding="utf-8")
    with redirect_stdout(io.StringIO()):
        ck("run refuses while custom triggers are off",
           trigger.main(["custom", "run", "site-health"]) == 1)
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("  SKIP  real YAML (PyYAML is not installed)")
        return
    (ct.triggers_dir() / "yamly.yaml").write_text(
        "id: yamly\ninterval: 5m\nblocks:\n  - id: e\n    type: emit\n    event:\n"
        "      id: fixed\n", encoding="utf-8")
    defn, err = ct.load("yamly")
    ck("real YAML loads", not err and defn["blocks"][0]["event"]["id"] == "fixed", err)


if __name__ == "__main__":
    graph.connect().close()
    test_validate()
    test_engine()
    test_isolation()
    test_execute()
    test_cli()
    SERVER.shutdown()
    print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'all passed'}")
    sys.exit(1 if FAILS else 0)
