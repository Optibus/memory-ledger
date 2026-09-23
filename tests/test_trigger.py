#!/usr/bin/env python3
"""The trigger engine (docs/trigger.md): gate, audit log, loops, sources, dispatch.

    python3 tests/test_trigger.py

Stdlib only. It runs `scripts/trigger.py` in process against a THROWAWAY brain,
with Slack and Jira replaced by a fake transport (`trigger.http_json` is the
only function that talks HTTP) and `claude` replaced by a script that prints
what a headless session would. Nothing leaves the machine.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = tempfile.mkdtemp(prefix="second-brain-trigger-")
os.environ["SECOND_BRAIN_HOME"] = HOME
os.environ["SECOND_BRAIN_NO_AUTOSYNC"] = "1"
os.environ["MEMORY_LEDGER_OWNER"] = "Owner"
for k in list(os.environ):
    if k.startswith(("SECOND_BRAIN_NEO4J", "MEMORY_LEDGER_NEO4J", "MEMORY_LEDGER_SLACK_",
                     "MEMORY_LEDGER_JIRA_")):
        del os.environ[k]
sys.path.insert(0, str(ROOT / "scripts"))

import trigger  # noqa: E402

FAILS: list[str] = []


def ck(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


FAKE_CLAUDE = Path(HOME) / "fake-claude"
FAKE_CLAUDE.write_text("""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = os.path.join(os.environ["SECOND_BRAIN_HOME"], "claude-calls.jsonl")
with open(log, "a") as f:
    f.write(json.dumps({"args": args, "rid": os.environ.get("MEMORY_LEDGER_REQUEST_ID"),
                        "src": os.environ.get("MEMORY_LEDGER_TRIGGER_SOURCE")}) + "\\n")
if "--version" in args:
    print("9.9.9 (fake)"); sys.exit(0)
prompt = args[args.index("-p") + 1]
if "FAIL-ME" in prompt:
    print(json.dumps({"is_error": True, "result": "it broke"})); sys.exit(1)
print(json.dumps({"result": "replied in thread", "total_cost_usd": 0.0123}))
""")
FAKE_CLAUDE.chmod(FAKE_CLAUDE.stat().st_mode | stat.S_IEXEC)

CFG = {
    "enabled": True,
    "signature": "_sent by {owner}'s second brain_",
    "claude": {"bin": str(FAKE_CLAUDE), "timeout_s": 60, "mcp_config": "",
               "extra_args": ["--model", "x"]},
    "slack": {"enabled": True, "owner_user_id": "UOWNER", "bot_token": "xoxb-t",
              "app_token": "xapp-t", "user_token": "xoxp-t", "channels": ["C1"]},
    "jira": {"enabled": True, "base_url": "https://j.example", "email": "o@example.com",
             "token": "jt", "owner_account_id": "acc-owner", "marker": "@cérebro"},
}


def write_cfg(cfg=CFG):
    trigger.config_path().parent.mkdir(parents=True, exist_ok=True)
    trigger.config_path().write_text(json.dumps(cfg))
    return trigger.load_config()


class FakeHTTP:
    """Answers by (method, url-without-query); records every call."""

    def __init__(self):
        self.calls, self.routes = [], {}

    def on(self, method, url, fn):
        self.routes[(method, url)] = fn

    def __call__(self, method, url, *, headers=None, body=None, form=None, timeout=30):
        self.calls.append({"method": method, "url": url, "body": body, "form": form})
        fn = self.routes.get((method, url.split("?")[0]))
        if fn is None:
            raise trigger.TriggerError(f"no fake for {method} {url}")
        return fn(url=url, body=body, form=form)


def install_http():
    fake = FakeHTTP()
    trigger.http_json = fake
    trigger._SLACK_BOT_ID.clear()
    s = trigger.SLACK_API
    fake.on("POST", s + "auth.test", lambda **_: {"ok": True, "user_id": "UBOT"})
    fake.on("POST", s + "chat.getPermalink",
            lambda form, **_: {"ok": True, "permalink": f"https://x.slack.com/p{form['message_ts']}"})
    fake.on("POST", s + "users.info",
            lambda form, **_: {"ok": True, "user": {"profile": {"real_name": f"Name {form['user']}"}}})
    fake.on("POST", s + "chat.postMessage", lambda **_: {"ok": True})
    return fake


def calls_to(fake, suffix):
    return [c for c in fake.calls if c["url"].split("?")[0].endswith(suffix)]


def dispatcher():
    ran = []
    return ran, (lambda rid: ran.append(rid))


def slack_msg(ts, user, text, thread=None):
    m = {"type": "app_mention", "ts": ts, "user": user, "text": text, "channel": "C1"}
    if thread:
        m["thread_ts"] = thread
    return m


# ================================================================== config
def test_config():
    print("\n== config: absent, broken, merged, secrets from env first ==")
    cfg = trigger.load_config()
    ck("no file means disabled, and says why",
       not trigger.source_enabled(cfg, "slack") and "does not exist" in cfg["_problem"])
    trigger.config_path().write_text("{not json")
    cfg = trigger.load_config()
    ck("a file that does not parse turns nothing on",
       "not valid JSON" in cfg["_problem"] and not trigger.source_enabled(cfg, "jira"))
    cfg = write_cfg()
    ck("the file merges over the defaults",
       cfg["jira"]["poll_seconds"] == 60 and cfg["jira"]["marker"] == "@cérebro")
    os.environ["MEMORY_LEDGER_SLACK_BOT_TOKEN"] = "xoxb-env"
    ck("an env var outranks the file", trigger.secret(cfg, "slack", "bot_token") == "xoxb-env")
    del os.environ["MEMORY_LEDGER_SLACK_BOT_TOKEN"]
    ck("the signature names the owner", trigger.signature(cfg) == "_sent by Owner's second brain_")
    summary = json.dumps(trigger.config_summary(cfg))
    ck("the summary never carries a token", "xoxb-t" not in summary and "jt\"" not in summary
       and trigger.config_summary(cfg)["secrets"]["slack.bot_token"]["set"] is True)


# ==================================================================== gate
def test_gate(cfg):
    print("\n== the gate: closed until the owner opens it ==")
    fake = install_http()
    ran, dispatch = dispatcher()
    bot = "UBOT"
    ev = trigger.slack_event(slack_msg("100.1", "UOTHER", "<@UBOT> do the thing"), "C1", cfg,
                             bot_user_id=bot)
    ck("a message mentioning the bot is a mention", ev and ev["scope"] == "slack:C1:100.1")
    ck("its permalink and requester name are resolved",
       ev["permalink"] == "https://x.slack.com/p100.1" and ev["requester_name"] == "Name UOTHER")
    ck("a message not mentioning the bot is not",
       trigger.slack_event(slack_msg("1.0", "U", "hello"), "C1", cfg, bot_user_id=bot) is None)

    res = trigger.handle(ev, cfg, dispatch, trigger.slack_notify(cfg))
    ck("someone else, not enabled: refused and nothing runs",
       res["action"] == "refused" and not ran, str(res))
    ck("the refusal is posted once, in the thread, with the USER token",
       len(calls_to(fake, "chat.postMessage")) == 1
       and calls_to(fake, "chat.postMessage")[0]["form"]["thread_ts"] == "100.1")
    con = trigger.db()
    req = trigger.get_request(con, res["request_id"])
    ck("the refusal is logged anyway", req["status"] == "refused" and req["reason"] == "not-enabled")
    ck("with the notice as an action",
       [a["kind"] for a in trigger.actions_for(con, req["id"])] == ["refusal-notice"])

    ev2 = trigger.slack_event(slack_msg("100.2", "UOTHER", "<@UBOT> again", thread="100.1"),
                              "C1", cfg, bot_user_id=bot)
    res = trigger.handle(ev2, cfg, dispatch, trigger.slack_notify(cfg))
    ck("a second refusal in the same thread is logged but not re-posted",
       res["action"] == "refused" and len(calls_to(fake, "chat.postMessage")) == 1)

    own = trigger.slack_event(slack_msg("100.3", "UOWNER", "<@UBOT> summarise", thread="100.1"),
                              "C1", cfg, bot_user_id=bot)
    res = trigger.handle(own, cfg, dispatch)
    ck("the owner runs anywhere", res["action"] == "run" and ran == [res["request_id"]])
    ck("and opens the conversation", trigger.gate_enabled(con, "slack:C1:100.1"))

    ev3 = trigger.slack_event(slack_msg("100.4", "UOTHER", "<@UBOT> now?", thread="100.1"),
                              "C1", cfg, bot_user_id=bot)
    res = trigger.handle(ev3, cfg, dispatch)
    ck("someone else in an opened conversation runs", res["action"] == "run" and len(ran) == 2)

    other = trigger.slack_event(slack_msg("200.1", "UOTHER", "<@UBOT> hi"), "C1", cfg,
                                bot_user_id=bot)
    ck("another thread in the channel is still closed",
       trigger.handle(other, cfg, dispatch)["action"] == "refused")
    trigger.set_gate(con, "slack:C1", True, by="test")
    chan = trigger.slack_event(slack_msg("300.1", "UOTHER", "<@UBOT> hi"), "C1", cfg,
                               bot_user_id=bot)
    ck("opening the whole channel opens every thread in it",
       trigger.handle(chan, cfg, dispatch)["action"] == "run")
    trigger.set_gate(con, "slack:C1", False, by="test")
    ck("a malformed scope is refused",
       trigger.set_gate(con, "slack C1; drop", True)["ok"] is False)


# =================================================================== loops
def test_loops(cfg):
    print("\n== loops: the brain never answers itself, and nothing runs twice ==")
    install_http()
    ran, dispatch = dispatcher()
    msg = slack_msg("400.1", "UOWNER", "<@UBOT> done.\n_sent by Owner's second brain_")
    ev = trigger.slack_event(msg, "C1", cfg, bot_user_id="UBOT")
    ck("a message carrying the signature is ignored",
       trigger.handle(ev, cfg, dispatch)["reason"] == "own reply" and not ran)
    ev = trigger.slack_event(slack_msg("400.2", "UOWNER", "<@UBOT> go"), "C1", cfg,
                             bot_user_id="UBOT")
    first = trigger.handle(ev, cfg, dispatch)
    second = trigger.handle(dict(ev), cfg, dispatch)
    ck("the same event twice runs once",
       first["action"] == "run" and second == {"action": "ignore", "reason": "seen"}
       and len(ran) == 1)
    bot_msg = {**slack_msg("400.3", "UX", "<@UBOT> hi"), "bot_id": "B1"}
    ck("a bot's message is not a mention",
       trigger.slack_event(bot_msg, "C1", cfg, bot_user_id="UBOT") is None)


# ============================================================ audit + node
def test_audit(cfg):
    print("\n== the audit log: a request id, its actions, and a node ==")
    install_http()
    ran, dispatch = dispatcher()
    ev = trigger.slack_event(slack_msg("500.1", "UOWNER", "<@UBOT> move PROJ-1 to review"),
                             "C1", cfg, bot_user_id="UBOT")
    rid = trigger.handle(ev, cfg, dispatch)["request_id"]
    con = trigger.db()
    req = trigger.get_request(con, rid)
    ck("the request id is a UUID", len(rid) == 36 and rid.count("-") == 4)
    ck("who, where, when and what are recorded",
       req["requester_id"] == "UOWNER" and req["scope"] == "slack:C1:500.1"
       and req["received_at"] and "PROJ-1" in req["text"] and req["permalink"])
    node = con.execute("SELECT * FROM nodes WHERE id=?", (req["node_id"],)).fetchone()
    ck("and mirrored as a `request` node", node is not None and node["type"] == "request")
    a = trigger.log_action(con, rid, "jira-transition", "PROJ-1", "moved to In Review")
    b = trigger.log_action(con, rid, "reply", ev["permalink"], "told them")
    ck("actions append under the request", a["ok"] and b["ok"]
       and [x["kind"] for x in trigger.actions_for(con, rid)] == ["jira-transition", "reply"])
    props = json.loads(con.execute("SELECT props FROM nodes WHERE id=?",
                                   (req["node_id"],)).fetchone()["props"])
    ck("and are copied onto the node", [x["kind"] for x in props["actions"]]
       == ["jira-transition", "reply"])
    ck("an action for no request is refused",
       trigger.log_action(con, "nope", "reply")["ok"] is False)
    ck("an action with no kind is refused", trigger.log_action(con, rid, "")["ok"] is False)
    ck("the CLI logs an action from $MEMORY_LEDGER_REQUEST_ID", _cli_action(rid))


def _cli_action(rid):
    import subprocess
    env = {**os.environ, "MEMORY_LEDGER_REQUEST_ID": rid}
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "trigger.py"), "action",
                        "--kind", "slack-message", "--detail", "cli"],
                       capture_output=True, text=True, env=env)
    try:
        return p.returncode == 0 and json.loads(p.stdout)["ok"]
    except ValueError:
        return False


# ================================================================ dispatch
def test_dispatch(cfg):
    print("\n== dispatch: one stateless claude -p per mention ==")
    install_http()
    ran, dispatch = dispatcher()
    ev = trigger.slack_event(slack_msg("600.1", "UOWNER", "<@UBOT> summarise this"),
                             "C1", cfg, bot_user_id="UBOT")
    rid = trigger.handle(ev, cfg, dispatch)["request_id"]
    res = trigger.run_request(rid, cfg)
    con = trigger.db()
    req = trigger.get_request(con, rid)
    ck("a session that succeeds is done", res["status"] == "done" and req["status"] == "done")
    ck("its result and cost are recorded",
       req["result"] == "replied in thread" and abs(req["cost_usd"] - 0.0123) < 1e-9)
    calls = [json.loads(line) for line in
             (Path(HOME) / "claude-calls.jsonl").read_text().splitlines()]
    last = calls[-1]
    args = last["args"]
    ck("headless, JSON out, permissions skipped",
       args[0] == "-p" and "--dangerously-skip-permissions" in args
       and args[args.index("--output-format") + 1] == "json")
    ck("extra args ride along", args[-2:] == ["--model", "x"])
    ck("the request id and source reach the session's env",
       last["rid"] == rid and last["src"] == "slack")
    prompt = args[1]
    ck("the prompt carries the text, the permalink and the source",
       "summarise this" in prompt and ev["permalink"] in prompt and "source: slack" in prompt)
    ck("and names the respond skill and the signature",
       "`respond` skill" in prompt and "_sent by Owner's second brain_" in prompt)
    ck("a request that is not queued is not run twice",
       trigger.run_request(rid, cfg)["ok"] is False)

    ev = trigger.slack_event(slack_msg("600.2", "UOWNER", "<@UBOT> FAIL-ME"), "C1", cfg,
                             bot_user_id="UBOT")
    rid = trigger.handle(ev, cfg, dispatch)["request_id"]
    ck("a session that fails is failed, with its reason",
       trigger.run_request(rid, cfg)["status"] == "failed"
       and trigger.get_request(con, rid)["result"] == "it broke")

    mcfg = json.loads(json.dumps(cfg))
    mcfg["claude"].update(mcp_config="~/mcp.json", strict_mcp_config=True)
    cmd = trigger.build_command("p", mcfg)
    ck("--mcp-config is expanded, and strict when asked",
       f"--mcp-config={Path('~/mcp.json').expanduser()}" in cmd
       and "--strict-mcp-config" in cmd)

    bcfg = json.loads(json.dumps(cfg))
    bcfg["claude"]["bin"] = str(Path(HOME) / "no-such-claude")
    ev = trigger.slack_event(slack_msg("600.3", "UOWNER", "<@UBOT> x"), "C1", cfg,
                             bot_user_id="UBOT")
    rid = trigger.handle(ev, cfg, dispatch)["request_id"]
    ck("a claude that cannot start is failed, not a crash",
       trigger.run_request(rid, bcfg)["status"] == "failed")

    ev = trigger.slack_event(slack_msg("600.4", "UOWNER", "<@UBOT> y"), "C1", cfg,
                             bot_user_id="UBOT")
    queued = trigger.handle(ev, cfg, dispatch)["request_id"]
    ev = trigger.slack_event(slack_msg("600.5", "UOWNER", "<@UBOT> z"), "C1", cfg,
                             bot_user_id="UBOT")
    running = trigger.handle(ev, cfg, dispatch)["request_id"]
    trigger._set_request(con, running, status="running")
    again = []
    res = trigger.recover(cfg, again.append)
    ck("on start, a running request is interrupted",
       trigger.get_request(con, running)["status"] == "interrupted" and res["interrupted"] >= 1)
    ck("and a queued one is dispatched", queued in again)


# ============================================================ slack catch-up
def test_slack_catch_up(cfg):
    print("\n== slack catch-up: history and threads since the checkpoint ==")
    fake = install_http()
    con = trigger.db()
    con.execute("DELETE FROM trigger_checkpoint WHERE source='slack'")
    con.commit()
    ran, dispatch = dispatcher()
    res = trigger.slack_catch_up(cfg, dispatch, now=1000.0)
    ck("a first run answers no backlog, and starts from now",
       res.get("first_run") and trigger.get_checkpoint(con, "slack") == "1000.000000")
    s = trigger.SLACK_API
    history = {"ok": True, "messages": [
        {"ts": "1001.0", "user": "UOWNER", "text": "<@UBOT> new top-level"},
        {"ts": "900.0", "user": "UOWNER", "text": "<@UBOT> older than the checkpoint"},
        {"ts": "800.0", "user": "UX", "text": "an old thread", "reply_count": 2,
         "latest_reply": "1002.0"},
        {"ts": "1003.0", "user": "UOWNER", "text": "no mention here"}]}
    replies = {"ok": True, "messages": [
        {"ts": "800.0", "user": "UX", "text": "an old thread"},
        {"ts": "1002.0", "user": "UOWNER", "text": "<@UBOT> in an old thread",
         "thread_ts": "800.0"}]}
    fake.on("POST", s + "conversations.history", lambda **_: history)
    fake.on("POST", s + "conversations.replies", lambda **_: replies)
    res = trigger.slack_catch_up(cfg, dispatch, now=2000.0)
    got = sorted(trigger.get_request(con, r)["event_id"] for r in ran)
    ck("new mentions run, including a reply inside an older thread",
       got == ["C1:1001.0", "C1:1002.0"], str(got))
    ck("the thread reply keeps its thread as the conversation",
       any(trigger.get_request(con, r)["scope"] == "slack:C1:800.0" for r in ran))
    ck("the checkpoint moves to the start of the pass",
       trigger.get_checkpoint(con, "slack") == "2000.000000")
    ck("history reached back for threads, not only to the checkpoint",
       float(calls_to(fake, "conversations.history")[-1]["form"]["oldest"]) < 1000.0)
    trigger.slack_catch_up(cfg, dispatch, now=3000.0)
    ck("a second pass over the same messages runs nothing new", len(ran) == 2)
    trigger.set_checkpoint(con, "slack", "10.0")
    ck("the checkpoint never moves backwards",
       trigger.get_checkpoint(con, "slack") == "3000.000000")


# ==================================================================== jira
def adf(*parts):
    content = []
    for p in parts:
        content.append({"type": "mention", "attrs": {"id": p[1], "text": p[2]}}
                       if isinstance(p, tuple) else {"type": "text", "text": p})
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": content}]}


def test_jira(cfg):
    print("\n== jira: a marker plus a mention of the owner, polled ==")
    ck("ADF reads as text, mentions as their display names",
       trigger.adf_text(adf(("m", "acc-owner", "@Owner"), " @Cerebro please")).strip()
       == "@Owner @Cerebro please")
    ck("the marker is case- and accent-blind",
       trigger.has_marker("hey @CEREBRO", "@cérebro") and not trigger.has_marker("hey", "@x"))

    fake = install_http()
    jira = trigger.Jira(cfg)
    base = cfg["jira"]["base_url"]
    now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
    con = trigger.db()
    ran, dispatch = dispatcher()
    res = trigger.jira_poll(cfg, dispatch, now=now, jira=jira)
    ck("a first poll answers no backlog", res.get("first_run") and not ran)

    def ts(minutes):
        return (now + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

    comments = {"comments": [
        {"id": "5", "created": ts(9), "author": {"accountId": "acc-owner", "displayName": "Owner"},
         "body": adf("done.\n", "sent by Owner's second brain")},
        {"id": "4", "created": ts(8), "author": {"accountId": "acc-x", "displayName": "X"},
         "body": adf("@cérebro no owner mention")},
        {"id": "3", "created": ts(7), "author": {"accountId": "acc-x", "displayName": "X"},
         "body": adf(("m", "acc-owner", "@Owner"), " @cérebro please triage")},
        {"id": "2", "created": ts(6), "author": {"accountId": "acc-owner", "displayName": "Owner"},
         "body": adf("@Cerebro move to review")},
        {"id": "1", "created": ts(-600), "author": {"accountId": "acc-owner"},
         "body": adf("@cérebro ancient")}]}
    searched = []
    fake.on("POST", base + "/rest/api/3/search/jql",
            lambda body, **_: searched.append(body) or {"issues": [
                {"key": "PROJ-7", "fields": {"summary": "A bug"}}], "isLast": True})
    fake.on("GET", base + "/rest/api/3/issue/PROJ-7/comment", lambda **_: comments)
    fake.on("POST", base + "/rest/api/3/issue/PROJ-7/comment", lambda **_: {"id": "99"})
    res = trigger.jira_poll(cfg, dispatch, now=now + timedelta(minutes=10), jira=jira)
    ck("the search is relative, so the Jira profile's timezone cannot shift it",
       searched[-1]["jql"].startswith("updated >= -12m"), searched[-1]["jql"])
    events = sorted(trigger.get_request(con, r)["event_id"] for r in ran)
    ck("the owner's own marked comment runs", "PROJ-7:2" in events, str(events))
    reqs = {r["event_id"]: r for r in trigger.list_requests(con, 100)}
    ck("a colleague's reply after the owner opened the issue runs, in the same pass",
       "PROJ-7:3" in events and reqs["PROJ-7:3"]["status"] == "queued")
    ck("the owner's comment opens the issue", trigger.gate_enabled(con, "jira:PROJ-7"))
    ck("a marker without a mention of the owner is not a mention", "PROJ-7:4" not in reqs)
    ck("the brain's own signed comment is not a mention", "PROJ-7:5" not in reqs)
    ck("comments older than the checkpoint window are not read", "PROJ-7:1" not in reqs)
    ck("the permalink focuses the comment",
       reqs["PROJ-7:2"]["permalink"] == f"{base}/browse/PROJ-7?focusedCommentId=2")

    # Refusal path, on a fresh issue that nobody opened.
    comments2 = {"comments": [
        {"id": "11", "created": ts(15), "author": {"accountId": "acc-x", "displayName": "X"},
         "body": adf(("m", "acc-owner", "@Owner"), " @cérebro please")}]}
    fake.on("POST", base + "/rest/api/3/search/jql",
            lambda body, **_: {"issues": [{"key": "PROJ-8"}], "isLast": True})
    fake.on("GET", base + "/rest/api/3/issue/PROJ-8/comment", lambda **_: comments2)
    posted = []
    fake.on("POST", base + "/rest/api/3/issue/PROJ-8/comment",
            lambda body, **_: posted.append(body) or {"id": "12"})
    trigger.jira_poll(cfg, dispatch, now=now + timedelta(minutes=16), jira=jira)
    r = {x["event_id"]: x for x in trigger.list_requests(con, 100)}["PROJ-8:11"]
    ck("someone else on an issue nobody opened is refused", r["status"] == "refused")
    sig = json.dumps(posted[0]) if posted else ""
    ck("and gets one comment, signed in italics, with no marker in it",
       len(posted) == 1 and "\"em\"" in sig and "sent by Owner's second brain" in sig
       and "rebro" not in sig)

    ck("without a base url it says what is missing",
       "jira.base_url" in trigger.jira_poll({**cfg, "jira": {**cfg["jira"], "base_url": ""}},
                                            dispatch)["error"])


# =================================================================== check
def test_check(cfg):
    print("\n== check: ready or not, starting nothing ==")
    install_http()
    res = trigger.check(cfg, offline=True)
    ck("a complete config checks out", res["ok"] and res["ready"], str(res["problems"]))
    bad = json.loads(json.dumps(cfg))
    bad["slack"].pop("app_token")
    bad["slack"]["owner_user_id"] = ""
    res = trigger.check(bad, offline=True)
    ck("a missing token and owner are named",
       not res["ready"] and any("app_token" in p for p in res["problems"])
       and any("owner_user_id" in p for p in res["problems"]))
    off = json.loads(json.dumps(cfg))
    off["jira"]["enabled"] = False
    off["jira"]["base_url"] = ""
    res = trigger.check(off, offline=True, only="jira")
    ck("one service can be checked while it is still switched off",
       res["target"] == "jira" and any("jira.base_url" in p for p in res["problems"]))
    res = trigger.check(off, offline=True, only="slack")
    ck("and checking one does not report on the others",
       res["ready"] and not any("jira" in p for p in res["problems"]))
    ck("an unknown target is refused", trigger.check(cfg, only="email")["ok"] is False)

    fake = install_http()
    s = trigger.SLACK_API
    fake.on("POST", s + "apps.connections.open", lambda **_: {"ok": True, "url": "wss://x"})
    fake.on("POST", s + "auth.test", lambda form=None, **_: {"ok": True, "user_id": "UBOT",
                                                             "user": "me"})
    res = trigger.check(cfg, only="slack")
    ck("online, the Slack check proves each token against Slack",
       res["ready"] and sum("token" in n for n in res["notes"]) >= 3, str(res["notes"]))
    fake.on("POST", s + "apps.connections.open", lambda **_: {"ok": False, "error": "invalid_auth"})
    res = trigger.check(cfg, only="slack")
    ck("and names the token Slack rejected",
       not res["ready"] and any(p.startswith("app token") for p in res["problems"]))


# ================================================================== saving
def test_save():
    print("\n== save_config: validated, secrets write-only, mode 600 ==")
    trigger.config_path().unlink(missing_ok=True)
    res = trigger.save_config({"enabled": True, "jira": {"enabled": True,
                                                         "base_url": "https://j.example/",
                                                         "token": "sekrit-9f2", "poll_seconds": 30}})
    ck("a first save creates the file", res["ok"] and trigger.config_path().exists(), str(res))
    ck("mode 600, because it holds tokens",
       (trigger.config_path().stat().st_mode & 0o777) == 0o600)
    summary = res["config"]
    ck("the summary says a token is stored, never what it is",
       summary["secrets"]["jira.token"]["set"] and "sekrit-9f2" not in json.dumps(summary))
    ck("a trailing slash on the base url is dropped", summary["jira"]["base_url"] == "https://j.example")
    res = trigger.save_config({"jira": {"token": "", "marker": "@brain"}})
    raw = json.loads(trigger.config_path().read_text())
    ck("an empty secret keeps the stored one", raw["jira"]["token"] == "sekrit-9f2")
    ck("and the other keys survive a partial save",
       raw["jira"]["poll_seconds"] == 30 and raw["enabled"] is True and raw["jira"]["marker"] == "@brain")
    trigger.save_config({"clear": ["jira.token"]})
    ck("clear removes a secret", "token" not in json.loads(trigger.config_path().read_text())["jira"])
    os.environ["MEMORY_LEDGER_JIRA_TOKEN"] = "from-env"
    ck("a secret from the environment is reported as such",
       trigger.config_summary(trigger.load_config())["secrets"]["jira.token"]["source"] == "env")
    del os.environ["MEMORY_LEDGER_JIRA_TOKEN"]
    for patch, needle in (
        ({"enabled": "yes"}, "true or false"),
        ({"jira": {"poll_seconds": 1}}, "from 15 to 3600"),
        ({"jira": {"base_url": "ftp://x"}}, "http(s) URL"),
        ({"slack": {"bot_token": "xoxp-wrong"}}, "should start with xoxb-"),
        ({"slack": {"channels": ["C1", "not a channel"]}}, "not valid"),
        ({"clear": ["jira.email"]}, "is not a secret"),
    ):
        r = trigger.save_config(patch)
        ck(f"refused: {needle}", r["ok"] is False and needle in r["error"], str(r))
    trigger.save_config({"slack": {"channels": "C1, C2 C3"}})
    ck("channels may be typed as one string",
       json.loads(trigger.config_path().read_text())["slack"]["channels"] == ["C1", "C2", "C3"])
    trigger.save_config({"bogus": 1, "slack": {"bogus": 2}})
    raw = json.loads(trigger.config_path().read_text())
    ck("an unknown key is not written", "bogus" not in raw and "bogus" not in raw["slack"])


def main():
    try:
        test_config()
        cfg = write_cfg()
        test_gate(cfg)
        test_loops(cfg)
        test_audit(cfg)
        test_dispatch(cfg)
        test_slack_catch_up(cfg)
        test_jira(cfg)
        test_check(cfg)
        test_save()
    finally:
        import shutil
        shutil.rmtree(HOME, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
