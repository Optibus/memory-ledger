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
                     "MEMORY_LEDGER_JIRA_", "MEMORY_LEDGER_GITHUB_")):
        del os.environ[k]
sys.path.insert(0, str(ROOT / "scripts"))

import trigger  # noqa: E402

REAL_HTTP_JSON = trigger.http_json

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
if prompt.startswith("Rewrite NOTICE"):
    # A translation: mark it, so a test can see it happened and to what.
    notice = prompt.split("<<<NOTICE\\n", 1)[1].split("\\nNOTICE>>>", 1)[0]
    print(json.dumps({"result": "[translated] " + notice})); sys.exit(0)
if prompt.startswith("Post one Slack message"):
    where = "DM" if "FORCE-DM" in prompt else "THREAD"
    print(json.dumps({"result": "posted it\\n" + where})); sys.exit(0)
if "FAIL-ME" in prompt:
    print(json.dumps({"is_error": True, "result": "it broke"})); sys.exit(1)
print(json.dumps({"result": "replied in thread", "total_cost_usd": 0.0123}))
""")
FAKE_CLAUDE.chmod(FAKE_CLAUDE.stat().st_mode | stat.S_IEXEC)

CFG = {
    "enabled": True,
    # The notice tests below are about translating; the default, refusing with
    # no model call at all, has its own (test_access).
    "refuse_without_model": False,
    "signature": "_sent by {owner}'s second brain_",
    "claude": {"bin": str(FAKE_CLAUDE), "timeout_s": 60, "mcp_config": "",
               "extra_args": ["--model", "x"]},
    "slack": {"enabled": True, "owner_user_id": "UOWNER", "bot_token": "xoxb-t",
              "app_token": "xapp-t", "user_token": "xoxp-t"},
    "jira": {"enabled": True, "base_url": "https://j.example", "email": "o@example.com",
             "token": "jt", "owner_account_id": "acc-owner", "marker": "@cérebro"},
    "github": {"enabled": True, "token": "ghp_t", "login": "owner", "marker": "/brain",
               "events": {"ci_failed": {"on": True}, "merged": {"on": True},
                          "pushed": {"on": True}}},
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
    posts = calls_to(fake, "chat.postMessage")
    ck("the refusal is posted once, in the thread, with the USER token",
       len(posts) == 1 and posts[0]["form"]["thread_ts"] == "100.1")
    ck("translated into the message's language, the signature left as it is",
       posts[0]["form"]["text"].startswith("[translated] I'm not enabled")
       and posts[0]["form"]["text"].endswith("\n_sent by Owner's second brain_"))
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


# ================================================================= logging
def test_logging(cfg):
    print("\n== logging: the server's line for every new request and task ==")
    import logging
    path = trigger.setup_logging()
    ck("setting it up twice does not double the handlers",
       trigger.setup_logging() == path and len(trigger.log.handlers) == 2)
    lines = []

    class Keep(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    keep = Keep()
    trigger.log.addHandler(keep)
    try:
        install_http()
        ran, dispatch = dispatcher()
        ev = trigger.slack_event(slack_msg("650.1", "UOWNER", "<@UBOT> log me"), "C1", cfg,
                                 bot_user_id="UBOT")
        rid = trigger.handle(ev, cfg, dispatch)["request_id"]
        ck("a new request is logged with its source, requester and decision",
           any(rid in m and "new request" in m and "slack" in m and "(owner)" in m
               and "log me" in m and "queued" in m for m in lines), lines[-1:])
        trigger.run_request(rid, cfg)
        ck("its task is logged when it starts and when it ends, with the cost",
           any(f"task {rid} started" in m for m in lines)
           and any(f"task {rid} done" in m and "$0.01" in m for m in lines), lines[-2:])
        n = len(lines)
        trigger.handle(ev, cfg, dispatch)
        ck("a mention seen before is not logged again", len(lines) == n)
        ev = trigger.slack_event(slack_msg("650.2", "USTRANGER", "<@UBOT> hi"), "C9", cfg,
                                 bot_user_id="UBOT")
        trigger.handle(ev, cfg, dispatch)
        ck("a refusal is logged as one", "refused" in lines[-1], lines[-1:])
    finally:
        for h in list(trigger.log.handlers):
            trigger.log.removeHandler(h)
            h.close()
        trigger.log._brain_path = None
    ck("and it all lands in <home>/logs/trigger.log",
       "new request" in path.read_text() and path.parent == Path(HOME) / "logs")
    sys.path.insert(0, str(ROOT / "scripts"))
    from brain_api import trigger_runtime
    summary = trigger_runtime._summary(cfg, 3)
    ck("the start line names the sources, the pool and the log",
       "slack" in summary and "jira (poll 60s)" in summary and "3 sessions" in summary
       and str(path) in summary, summary)


# ============================================================ run commands
def test_run_commands(cfg):
    print("\n== run commands: a template per trigger, with variables ==")
    install_http()
    ran, dispatch = dispatcher()
    ev = trigger.slack_event(slack_msg("1700000000.000200", "UOWNER", "<@UBOT> sum it up"),
                             "C1", cfg, bot_user_id="UBOT")
    req = trigger.get_request(trigger.db(), trigger.handle(ev, cfg, dispatch)["request_id"])
    v = trigger.request_variables(req, cfg)
    ck("Slack: source, kind, user, content, link, channel and thread",
       v["trigger_source"] == "slack" and v["trigger_kind"] == "mention"
       and v["trigger_user"] == "Name UOWNER" and v["trigger_user_id"] == "UOWNER"
       and v["trigger_user_is_owner"] == "true" and v["trigger_content"] == "<@UBOT> sum it up"
       and v["trigger_link"] == ev["permalink"] and v["slack_channel"] == "C1"
       and v["slack_thread"] == "1700000000.000200" and v["github_repo"] == "", v)
    ck("the trigger time is the message's own, not when it was read",
       v["trigger_time"] == "2023-11-14T22:13:20+00:00" and v["trigger_received"])
    ck("every catalogued variable has a value, even if empty",
       set(v) == set(trigger.VARIABLES))

    ck("no run command: the prompt has no instructions block",
       "<<<INSTRUCTIONS" not in trigger.build_prompt(req, cfg))
    pcfg = json.loads(json.dumps(cfg))
    pcfg["slack"]["prompt"] = ("{trigger_source} sent this task. Pick it up and reply to "
                               "{trigger_user} in {trigger_where}. Braces {like this} stay.")
    ev = trigger.slack_event(slack_msg("1700000000.000201", "UOWNER", "<@UBOT> sum it up"),
                             "C1", pcfg, bot_user_id="UBOT")
    preq = trigger.get_request(trigger.db(), trigger.handle(ev, pcfg, dispatch)["request_id"])
    ck("the run command is kept on the request, as it was when it fired",
       preq["meta"]["do"].startswith("{trigger_source} sent this task")
       and preq["meta"]["action"] == {"type": "claude"})
    prompt = trigger.build_prompt(preq, cfg)
    ck("a run command is rendered into the prompt, ahead of the guardrails",
       "slack sent this task. Pick it up and reply to Name UOWNER in Slack channel C1" in prompt
       and "Braces {like this} stay." in prompt
       and prompt.index("<<<INSTRUCTIONS") < prompt.index(" decline --request "))
    ck("and the message is still quoted verbatim below it", "<<<MESSAGE\n<@UBOT> sum it up" in prompt)

    issue = {"key": "K-7", "fields": {"summary": "Fix the export"}}
    comment = {"id": "9", "body": "@cérebro please", "created": "2026-09-23T11:18:00.000-0300",
               "author": {"accountId": "acc-owner", "displayName": "Owner"}}
    jev = trigger.jira_event(issue, comment, cfg, base="https://j.example", owner_id="acc-owner")
    jreq = trigger.get_request(trigger.db(), trigger.handle(jev, cfg, dispatch)["request_id"])
    jv = trigger.request_variables(jreq, cfg)
    ck("Jira: the issue, its summary, and the comment's time in UTC",
       jv["jira_issue"] == "K-7" and jv["trigger_title"] == "Fix the export"
       and jv["trigger_time"] == "2026-09-23T14:18:00+00:00", jv)

    res = trigger.save_config({"slack": {"prompt": "hi {trigger_usr}"}})
    ck("saving an unknown variable is refused, naming it and the real ones",
       not res["ok"] and "{trigger_usr}" in res["error"] and "{trigger_user}" in res["error"])
    res = trigger.save_config({"github": {"events": {"merged": {"do": "{github_repo} {oops}"}}}})
    ck("so is one in a GitHub event's command", not res["ok"] and "{oops}" in res["error"])
    res = trigger.save_config({"jira": {"prompt": "{trigger_user} on {jira_issue}"}})
    ck("a good one saves, and the page gets the catalogue",
       res["ok"] and res["config"]["jira"]["prompt"] == "{trigger_user} on {jira_issue}"
       and any(x["name"] == "trigger_content" and x["global"] for x in res["config"]["variables"])
       and any(x["name"] == "github_repo" and x["sources"] == ["github"]
               for x in res["config"]["variables"]))
    trigger.save_config({"jira": {"prompt": ""}})


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
    fake.on("POST", s + "users.conversations",
            lambda **_: {"ok": True, "channels": [{"id": "C1"}]})
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


# ================================================================ channels
def test_channels(cfg):
    print("\n== channels: an open list in the brain, removals kept ==")
    fake = install_http()
    fake.on("POST", trigger.SLACK_API + "conversations.info",
            lambda form, **_: {"ok": True, "channel": {"name": f"name-{form['channel']}"}})
    con = trigger.db()
    ck("an id is a channel", trigger.parse_channel("c0abc123") == "C0ABC123")
    ck("so is a link to it",
       trigger.parse_channel("https://x.slack.com/archives/C0ABC123/p1700000000") == "C0ABC123")
    ck("anything else is not", trigger.parse_channel("#general") == "")
    res = trigger.add_channel(con, "C0ABC123", by="test", cfg=cfg)
    ck("adding one opens it, its name looked up from Slack",
       res["ok"] and res["channel"]["name"] == "name-C0ABC123"
       and trigger.gate_enabled(con, "slack:C0ABC123"))
    trigger.add_channel(con, "C0DEF456", name="#ops", by="test", cfg=cfg)
    ck("a name given by hand wins, without the #",
       any(c["name"] == "ops" for c in trigger.list_channels(con)["active"]))
    res = trigger.remove_channel(con, "C0ABC123", by="test")
    lst = trigger.list_channels(con)
    ck("removing one closes it", res["ok"] and not trigger.gate_enabled(con, "slack:C0ABC123"))
    ck("but keeps its row, marked removed, with who and when",
       [c["channel_id"] for c in lst["removed"]] == ["C0ABC123"]
       and lst["removed"][0]["removed_by"] == "test" and lst["removed"][0]["removed_at"])
    ck("removing a channel that is not open says so",
       trigger.remove_channel(con, "C0ABC123")["ok"] is False)
    res = trigger.add_channel(con, "C0ABC123", by="again", cfg=cfg)
    lst = trigger.list_channels(con)
    ck("re-adding restores the same row rather than making a second",
       res["ok"] and "C0ABC123" in [c["channel_id"] for c in lst["active"]]
       and not lst["removed"]
       and con.execute("SELECT count(*) FROM trigger_channel WHERE channel_id='C0ABC123'"
                       ).fetchone()[0] == 1)
    ck("a channel-wide gate IS the list: set_gate writes it",
       trigger.set_gate(con, "slack:C0DEF456", False, by="t")["ok"]
       and "C0DEF456" in [c["channel_id"] for c in trigger.list_channels(con)["removed"]])
    ck("status carries the list", "C0ABC123" in
       [c["channel_id"] for c in trigger.status_payload()["channels"]["active"]])


# ================================================================= notices
def _req(cfg, ts, user="UOTHER", text="<@UBOT> por favor faz isso"):
    ev = trigger.slack_event(slack_msg(ts, user, text), "C9", cfg, bot_user_id="UBOT")
    rid = trigger.intake(ev, cfg)["request_id"]      # no notify: posted by hand below
    return rid


def test_notices(cfg):
    print("\n== notices: translated, as the owner, DM when the channel is out of reach ==")
    con = trigger.db()

    fake = install_http()
    rid = _req(cfg, "700.1")
    res = trigger.deliver_notice(rid, "Not here.", cfg)
    post = calls_to(fake, "chat.postMessage")[-1]["form"]
    ck("with a user token: translated, posted in the thread",
       res["how"] == "thread" and post["text"] == "[translated] Not here.\n"
                                                  "_sent by Owner's second brain_")
    ck("and logged as a refusal notice",
       trigger.actions_for(con, rid)[-1]["kind"] == "refusal-notice")

    fake = install_http()
    sent = []

    def post_msg(form, **_):
        sent.append(form)
        if form["channel"] == "C9":
            return {"ok": False, "error": "channel_not_found"}
        return {"ok": True}
    fake.on("POST", trigger.SLACK_API + "chat.postMessage", post_msg)
    fake.on("POST", trigger.SLACK_API + "conversations.open",
            lambda form, **_: {"ok": True, "channel": {"id": f"D-{form['users']}"}})
    rid = _req(cfg, "700.2")
    res = trigger.deliver_notice(rid, "Not here.", cfg)
    ck("the owner cannot reach the channel: the requester gets a DM instead",
       res["how"] == "dm" and sent[-1]["channel"] == "D-UOTHER")
    ck("with a link to what they wrote, the signature still last",
       "https://x.slack.com/p700.2" in sent[-1]["text"]
       and sent[-1]["text"].endswith("_sent by Owner's second brain_"))
    ck("logged as a DM", trigger.actions_for(con, rid)[-1]["kind"] == "refusal-notice-dm")

    def denied(form, **_):
        return {"ok": False, "error": "ratelimited"}
    fake.on("POST", trigger.SLACK_API + "chat.postMessage", denied)
    rid = _req(cfg, "700.3")
    res = trigger.deliver_notice(rid, "Not here.", cfg)
    ck("any other failure is a failure, not a DM",
       res["ok"] is False and trigger.actions_for(con, rid)[-1]["kind"] == "refusal-notice-failed")

    no_user = json.loads(json.dumps(cfg))
    no_user["slack"].pop("user_token")
    fake = install_http()
    rid = _req(no_user, "700.4")
    before = len((Path(HOME) / "claude-calls.jsonl").read_text().splitlines())
    res = trigger.deliver_notice(rid, "Not here.", no_user)
    calls = [json.loads(x) for x in (Path(HOME) / "claude-calls.jsonl").read_text().splitlines()]
    prompt = calls[-1]["args"][1]
    ck("without a user token: one session through the Slack MCP, no direct post",
       res["ok"] and len(calls) == before + 1 and not calls_to(fake, "chat.postMessage"))
    ck("told to translate, sign untranslated, and DM when it cannot post",
       "language the ORIGINAL MESSAGE" in prompt and "untranslated" in prompt
       and "direct message to the user UOTHER" in prompt and "por favor" in prompt)
    ck("and where it landed is logged", "thread (via the Slack MCP)"
       in trigger.actions_for(con, rid)[-1]["detail"])
    rid = _req(no_user, "700.5", text="<@UBOT> FORCE-DM please")
    trigger.deliver_notice(rid, "Not here.", no_user)
    ck("a session that had to DM says so, and it is logged as a DM",
       trigger.actions_for(con, rid)[-1]["kind"] == "refusal-notice-dm")

    plain = json.loads(json.dumps(cfg))
    plain["translate_notices"] = False
    fake = install_http()
    rid = _req(plain, "700.6")
    before = len((Path(HOME) / "claude-calls.jsonl").read_text().splitlines())
    trigger.deliver_notice(rid, "Not here.", plain)
    ck("translation off: the template as written, and no model call at all",
       calls_to(fake, "chat.postMessage")[-1]["form"]["text"].startswith("Not here.")
       and len((Path(HOME) / "claude-calls.jsonl").read_text().splitlines()) == before)

    ck("the old single `refusal` key still counts as the not-enabled one",
       trigger.refusal_text({"refusal": "Nope, {owner}."}, "not_enabled") == "Nope, Owner.")
    ck("{reason} is filled, and an empty one leaves no gap",
       trigger.refusal_text({}, "policy", "").endswith("so I did nothing.")
       and trigger.refusal_text({}, "policy", "It would email a customer.").endswith(
           "It would email a customer."))


def test_session_hygiene(cfg):
    print("\n== every headless call: stdin closed, a working directory that exists ==")
    install_http()
    seen = []
    orig = trigger.subprocess.run

    def spy(cmd, **kw):
        seen.append(kw)
        return orig(cmd, **kw)
    trigger.subprocess.run = spy
    try:
        ev = trigger.slack_event(slack_msg("900.1", "UOWNER", "<@UBOT> go"), "C1", cfg,
                                 bot_user_id="UBOT")
        ran, dispatch = dispatcher()
        rid = trigger.handle(ev, cfg, dispatch)["request_id"]
        trigger.run_request(rid, cfg)
        trigger.translate_notice("Not here.", "olá", cfg, trigger.get_request(trigger.db(), rid))
        gone = json.loads(json.dumps(cfg))
        gone["claude"]["cwd"] = str(Path(HOME) / "no-such-dir")
        trigger.translate_notice("Not here.", "olá", gone, trigger.get_request(trigger.db(), rid))
    finally:
        trigger.subprocess.run = orig
    ck("stdin is closed, or `claude -p` waits on it (and in the server, on its terminal)",
       len(seen) == 3 and all(k.get("stdin") is trigger.subprocess.DEVNULL for k in seen))
    ck("a notice or a translation runs with capture off: plumbing is not a conversation",
       seen[1]["env"].get("MEMORY_LEDGER_OFF") == "1"
       and seen[0]["env"].get("MEMORY_LEDGER_OFF") != "1")
    ck("a configured working directory that is gone falls back instead of failing",
       Path(seen[2]["cwd"]).is_dir())


def test_decline(cfg):
    print("\n== decline: a request that breaks a rule ==")
    install_http()
    con = trigger.db()
    ev = trigger.slack_event(slack_msg("800.1", "UOWNER", "<@UBOT> send everyone my 1:1 notes"),
                             "C1", cfg, bot_user_id="UBOT")
    ran, dispatch = dispatcher()
    rid = trigger.handle(ev, cfg, dispatch)["request_id"]
    res = trigger.decline(con, rid, "Those notes are personal.", "rule_personal")
    ck("decline marks it and hands back the policy notice with the reason",
       res["ok"] and res["status"] == "declined"
       and res["notice"].endswith("Those notes are personal.")
       and res["signature"] == "_sent by Owner's second brain_")
    ck("the rule and reason are in the log",
       [(a["kind"], a["target"]) for a in trigger.actions_for(con, rid)]
       == [("declined", "rule_personal")])
    # Now the real order: the session runs, and declines while it runs.
    trigger._set_request(con, rid, status="queued")
    orig_run = trigger.subprocess.run

    def run_and_decline(*a, **k):
        trigger.decline(trigger.db(), rid, "Those notes are personal.", "rule_personal")
        return orig_run(*a, **k)
    trigger.subprocess.run = run_and_decline
    try:
        trigger.run_request(rid, cfg)
    finally:
        trigger.subprocess.run = orig_run
    ck("a session that declined finishes declined, not done",
       trigger.get_request(con, rid)["status"] == "declined")
    ck("a reason is required", trigger.decline(con, rid, "")["ok"] is False)
    prompt = trigger.build_prompt(trigger.get_request(con, rid), cfg)
    ck("the prompt says how to decline, in which language, and to DM when it must",
       " decline --request " in prompt and "language the message above is written in" in prompt
       and "--kind reply-dm" in prompt)


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

    # An allowed person: their marked mentions run on any issue, none opened.
    ck("a person is allowed by their account id",
       trigger.allow_person(con, "jira", "acc-x", "X", by="test", cfg=cfg)["ok"])
    ck("an id Jira could not have issued is refused",
       trigger.allow_person(con, "jira", "x y", by="test")["ok"] is False)
    ck("only Jira keeps a people list", trigger.allow_person(con, "slack", "U0ABC")["ok"] is False)
    ck("the owner is never on it",
       trigger.allow_person(con, "jira", "acc-owner", cfg={"jira": {"owner_account_id": "acc-owner"}}
                            )["ok"] is False)
    comments3 = {"comments": [
        {"id": "21", "created": ts(20), "author": {"accountId": "acc-x", "displayName": "X"},
         "body": adf(("m", "acc-owner", "@Owner"), " @cérebro please")}]}
    fake.on("POST", base + "/rest/api/3/search/jql",
            lambda body, **_: {"issues": [{"key": "PROJ-9"}], "isLast": True})
    fake.on("GET", base + "/rest/api/3/issue/PROJ-9/comment", lambda **_: comments3)
    posted9 = []
    fake.on("POST", base + "/rest/api/3/issue/PROJ-9/comment",
            lambda body, **_: posted9.append(body) or {"id": "22"})
    trigger.jira_poll(cfg, dispatch, now=now + timedelta(minutes=21), jira=jira)
    r = {x["event_id"]: x for x in trigger.list_requests(con, 100)}["PROJ-9:21"]
    ck("an allowed person's mention runs on an issue nobody opened, with no refusal posted",
       r["status"] == "queued" and r["reason"] == "allowed user" and not posted9, r["reason"])
    ck("and opens nothing for anyone else", not trigger.gate_enabled(con, "jira:PROJ-9"))
    res = trigger.disallow_person(con, "jira", "acc-x", by="test")
    lst = trigger.list_people(con, "jira")
    ck("taking them off keeps the row, marked removed, with who",
       res["ok"] and not lst["active"] and lst["removed"][0]["removed_by"] == "test"
       and not trigger.person_allowed(con, "jira", "acc-x"))
    ck("taking off someone not on it says so", trigger.disallow_person(con, "jira", "acc-x")["ok"] is False)
    ck("allowing them again restores the same row",
       trigger.allow_person(con, "jira", "acc-x", by="again")["ok"]
       and con.execute("SELECT count(*) FROM trigger_person").fetchone()[0] == 1
       and trigger.list_people(con)["active"][0]["name"] == "X")
    ck("status carries the list", trigger.status_payload()["people"]["active"][0]["user_id"] == "acc-x")
    trigger.disallow_person(con, "jira", "acc-x")

    refused = trigger.list_requests(con, 100)
    rid8 = next(x["id"] for x in refused if x["event_id"] == "PROJ-8:11")
    res = trigger.reprocess(con, rid8)
    ck("a refused request can be reprocessed: the owner vouching for that one",
       res["ok"] and res["was"] == "refused" and trigger.get_request(con, rid8)["status"] == "queued")

    fake.on("GET", base + "/rest/api/3/user/search",
            lambda **_: [{"accountId": "acc-i", "displayName": "Isa", "active": True,
                          "accountType": "atlassian"},
                         {"accountId": "acc-app", "displayName": "Bot", "accountType": "app"},
                         {"accountId": "acc-old", "displayName": "Gone", "active": False}])
    found = trigger.find_people(cfg, "jira", "isa")
    ck("finding people returns active humans only",
       found["ok"] and [x["user_id"] for x in found["people"]] == ["acc-i"], str(found))
    ck("a one-letter search is refused", trigger.find_people(cfg, "jira", "i")["ok"] is False)

    ck("without a base url it says what is missing",
       "jira.base_url" in trigger.jira_poll({**cfg, "jira": {**cfg["jira"], "base_url": ""}},
                                            dispatch)["error"])


# ================================================================== github
def test_github(cfg):
    print("\n== github: PR changes the owner picked, mentions, the marker ==")
    ck("a mention is the whole login, any case",
       trigger.mentions_login("hey @Owner, look", "owner")
       and not trigger.mentions_login("hey @owner-bot", "owner")
       and not trigger.mentions_login("mail owner@x.com", "owner"))
    kinds = trigger.github_events_cfg(cfg)
    ck("defaults fill the kinds the file does not name, and the file wins where it does",
       kinds["review"]["on"] and not kinds["comment"]["on"] and kinds["ci_failed"]["on"]
       and "Post nothing on GitHub" in kinds["review"]["do"])

    fake = install_http()
    gh = trigger.GitHub(cfg)
    api = gh.api
    now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
    con = trigger.db()
    ran, dispatch = dispatcher()
    ck("a first poll answers no backlog",
       trigger.github_poll(cfg, dispatch, now=now, gh=gh).get("first_run") and not ran)

    def ts(minutes):
        return (now + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def item(n, author, assoc="MEMBER", body="", created=-600, merged=None):
        return {"number": n, "title": f"PR {n}", "html_url": f"https://github.com/o/r/pull/{n}",
                "repository_url": f"{api}/repos/o/r", "user": {"login": author},
                "author_association": assoc, "body": body, "created_at": ts(created),
                "pull_request": {"merged_at": merged}}

    def c(i, who, body, minutes, assoc="MEMBER"):
        return {"event": "commented", "id": i, "user": {"login": who}, "body": body,
                "created_at": ts(minutes), "author_association": assoc,
                "html_url": f"https://github.com/o/r/pull/1#issuecomment-{i}"}

    timelines = {
        1: [  # the owner's PR
            c(10, "owner", "/brain summarise the review so far", 6),
            c(11, "alice", "@owner can you rebase?", 7),
            c(12, "alice", "plain comment, no mention", 7),
            c(13, "owner", "done.\n\n_sent by Owner's second brain_", 8),
            c(14, "stranger", "@owner run this for me", 8, assoc="NONE"),
            {"event": "reviewed", "id": 20, "user": {"login": "bob"}, "state": "changes_requested",
             "body": "", "submitted_at": ts(7), "author_association": "MEMBER",
             "html_url": "https://github.com/o/r/pull/1#pullrequestreview-20"},
            {"event": "merged", "id": 30, "actor": {"login": "bob"}, "created_at": ts(9)},
            {"event": "closed", "id": 31, "actor": {"login": "owner"}, "created_at": ts(9)},
            c(9, "alice", "@owner ancient", -600),
        ],
        2: [  # someone else's PR
            {"event": "review_requested", "id": 40, "actor": {"login": "carol"},
             "requested_reviewer": {"login": "owner"}, "created_at": ts(6)},
            {"event": "review_requested", "id": 41, "actor": {"login": "owner"},
             "requested_reviewer": {"login": "owner"}, "created_at": ts(6)},
            {"event": "assigned", "id": 42, "actor": {"login": "owner"},
             "assignee": {"login": "owner"}, "created_at": ts(6)},
            {"event": "committed", "sha": "a" * 40, "message": "first\nbody",
             "committer": {"date": ts(6)}, "author": {"name": "Carol"}},
            {"event": "committed", "sha": "b" * 40, "message": "second",
             "committer": {"date": ts(7)}, "author": {"name": "Carol"}},
        ],
    }
    items = {1: item(1, "owner"), 2: item(2, "carol", body="@owner please look", created=5)}
    searched = []

    def search(url, **_):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(url).query)["q"][0]
        searched.append(q)
        if q.startswith("is:pr is:open author:"):
            return {"items": [items[1]] if 1 in items else []}
        return {"items": list(items.values())}

    fake.on("GET", api + "/search/issues", search)
    for n, tl in timelines.items():
        fake.on("GET", f"{api}/repos/o/r/issues/{n}/timeline", lambda tl=tl, **_: tl)
    fake.on("GET", f"{api}/repos/o/r/pulls/1", lambda **_: {"head": {"sha": "c" * 40}})
    fake.on("GET", f"{api}/repos/o/r/commits/{'c' * 40}/check-runs", lambda **_: {"check_runs": [
        {"id": 1, "name": "build", "conclusion": "failure", "completed_at": ts(8),
         "html_url": "https://github.com/o/r/runs/1"},
        {"id": 2, "name": "lint", "conclusion": "success", "completed_at": ts(8)},
        {"id": 3, "name": "e2e", "conclusion": "cancelled", "completed_at": ts(8)}]})
    posted = []
    fake.on("POST", f"{api}/repos/o/r/issues/1/comments", lambda body, **_: posted.append(body) or {})

    res = trigger.github_poll(cfg, dispatch, now=now + timedelta(minutes=10), gh=gh)
    ck("the poll ran", res.get("ok"), str(res))
    ck("the search is since the checkpoint, and asks for review requests and reviewed PRs",
       any(q.startswith("involves:owner updated:>=2026-09-23T11:58:00Z") for q in searched)
       and any("review-requested:owner" in q for q in searched)
       and any("reviewed-by:owner" in q for q in searched), str(searched))
    reqs = {r["event_id"]: r for r in trigger.list_requests(con, 100)}
    ids = set(reqs)
    ck("the owner's own comment with the marker runs, and opens the PR",
       reqs.get("o/r#1:c10", {}).get("status") == "queued"
       and trigger.gate_enabled(con, "github:o/r#1"))
    own = trigger.get_request(con, reqs["o/r#1:c10"]["id"])
    ck("and it carries the mention's run command, since it is a mention by the owner",
       own["meta"].get("kind") == "request"
       and own["meta"].get("do") == trigger.github_events_cfg(cfg)["mention"]["do"])
    ck("a teammate's mention runs", reqs.get("o/r#1:c11", {}).get("status") == "queued")
    ck("a comment with no mention does not, while `comment` is off", "o/r#1:c12" not in ids)
    ck("the brain's own signed comment is not a mention", "o/r#1:c13" not in ids)
    ck("a stranger's mention runs only because the owner opened this PR",
       reqs.get("o/r#1:c14", {}).get("reason") == "enabled")
    ck("a review on the owner's PR runs as a subscription",
       reqs.get("o/r#1:r20", {}).get("reason") == "subscribed: review")
    ck("so does the merge", "o/r#1:m30" in ids)
    ck("nothing older than the window", "o/r#1:c9" not in ids)
    ck("a review request for the owner runs", "o/r#2:rr40" in ids)
    ck("so does one the owner made for themselves: what they fire, they act on",
       reqs.get("o/r#2:rr41", {}).get("reason") == "subscribed: review_requested"
       and "added yourself as a reviewer" in reqs["o/r#2:rr41"]["text"])
    ck("assigned and closed stay off until switched on", not {"o/r#2:a42", "o/r#1:x31"} & ids)
    on = json.loads(json.dumps(cfg))
    on["github"]["events"] = {**(on["github"].get("events") or {}),
                              "assigned": {"on": True}, "closed": {"on": True}}
    evs = {e["event_id"]: e for n in (1, 2) for e in trigger.github_item_events(
        gh, items[n], now - timedelta(hours=1), on, "owner")}
    ck("switched on, assigning themselves and closing their own PR count, trusted",
       evs.get("o/r#2:a42", {}).get("subscribed") and evs.get("o/r#1:x31", {}).get("subscribed")
       and "You assigned yourself" in evs["o/r#2:a42"]["text"], str(list(evs)))
    ck("a description that mentions the owner runs", "o/r#2:body" in ids)
    ck("several commits are one push, named by the newest",
       "o/r#2:pbbbbbbbbbbbb" in ids and not any(":paaaa" in i for i in ids))
    ci = reqs.get("o/r#1:ci" + "c" * 12, {})
    ck("failed checks are one event per commit, and cancelled is not a failure",
       ci and "build" in ci["text"] and "e2e" not in ci["text"], ci.get("text", ""))
    order = [trigger.get_request(con, r)["event_id"] for r in ran]
    ck("handled oldest first, across PRs", order.index("o/r#1:c10") < order.index("o/r#1:c11"))

    req = trigger.get_request(con, reqs["o/r#1:r20"]["id"])
    prompt = trigger.build_prompt(req, cfg)
    ck("a subscription's prompt carries its instruction and says not to answer on GitHub",
       "github_event: review" in prompt and "<<<INSTRUCTIONS\nRead the review" in prompt
       and "not a\nmessage to answer" not in prompt and "subscribed to" in prompt)
    prompt = trigger.build_prompt(trigger.get_request(con, reqs["o/r#1:c11"]["id"]), cfg)
    ck("a mention's prompt replies with a comment",
       "add a comment on that GitHub pull request" in prompt)

    v = trigger.request_variables(req, cfg)
    ck("a GitHub request's variables: who, what, where, the title",
       v["trigger_source"] == "github" and v["trigger_kind"] == "review"
       and v["trigger_user"] and v["github_repo"] == "o/r" and v["github_number"] == "1"
       and v["github_item"] == "pull request" and v["trigger_title"]
       and v["trigger_time"].endswith("+00:00") and v["slack_channel"] == "", v)
    vcfg = json.loads(json.dumps(cfg))
    vreq = {**req, "meta": {**req["meta"], "do": "{trigger_user} reviewed {github_repo}#"
                                                  "{github_number}; see {trigger_link}. {nope}"}}
    prompt = trigger.build_prompt(vreq, vcfg)
    ck("a GitHub event's run command has its variables filled in, and an unknown one kept",
       f"{v['trigger_user']} reviewed o/r#1; see {req['permalink']}. {{nope}}" in prompt)

    before = len(trigger.list_requests(con, 500))
    trigger.github_poll(cfg, dispatch, now=now + timedelta(minutes=11), gh=gh)
    ck("an overlapping poll runs nothing twice", len(trigger.list_requests(con, 500)) == before)

    # A stranger on a PR nobody opened: refused, and by default nothing posted.
    timelines[3] = [c(50, "stranger", "@owner do my homework", 20, assoc="NONE")]
    items.clear()
    items[3] = item(3, "stranger", assoc="NONE")
    fake.on("GET", f"{api}/repos/o/r/issues/3/timeline", lambda **_: timelines[3])
    fake.on("POST", f"{api}/repos/o/r/issues/3/comments", lambda body, **_: posted.append(body) or {})
    trigger.github_poll(cfg, dispatch, now=now + timedelta(minutes=21), gh=gh)
    r = {x["event_id"]: x for x in trigger.list_requests(con, 500)}.get("o/r#3:c50", {})
    ck("an untrusted mention on a PR nobody opened is refused", r.get("status") == "refused")
    ck("and, with refusal_notice off, nothing is posted on GitHub", not posted)
    ck("a GitHub scope can be opened by hand",
       trigger.set_gate(con, "github:o/r#3", True, by="cli")["ok"]
       and not trigger.set_gate(con, "github:o/r", True)["ok"])

    loud = {**cfg, "github": {**cfg["github"], "refusal_notice": True}}
    items.clear()
    items[4] = item(4, "stranger", assoc="NONE")
    fake.on("GET", f"{api}/repos/o/r/issues/4/timeline",
            lambda **_: [c(51, "stranger", "@owner again", 25, assoc="NONE")])
    fake.on("POST", f"{api}/repos/o/r/issues/4/comments", lambda body, **_: posted.append(body) or {})
    trigger.github_poll({**loud, "translate_notices": False}, dispatch,
                        now=now + timedelta(minutes=26), gh=gh)
    ck("with refusal_notice on, one signed comment says no",
       len(posted) == 1 and "sent by Owner's second brain" in posted[0]["body"]
       and "/brain" not in posted[0]["body"], str(posted))

    ck("without a token it says what is missing",
       "token" in trigger.github_poll({**cfg, "github": {**cfg["github"], "token": ""}},
                                      dispatch)["error"])
    res = trigger.check(cfg, offline=True, only="github")
    ck("the offline check names the events that trigger",
       res["ready"] and any("ci_failed" in n for n in res["notes"]), str(res))


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
        ({"refusals": {"policy": 5}}, "must be text"),
        ({"clear": ["jira.email"]}, "is not a secret"),
        ({"github": {"token": "xoxb-nope"}}, "should start with ghp_"),
        ({"github": {"events": {"deploy": {"on": True}}}}, "is not an event"),
        ({"github": {"events": {"review": {"on": "yes"}}}}, "true or false"),
        ({"github": {"repos": ["o/r", "bad repo"]}}, "not valid"),
        ({"github": {"poll_seconds": 5}}, "from 30 to 3600"),
    ):
        r = trigger.save_config(patch)
        ck(f"refused: {needle}", r["ok"] is False and needle in r["error"], str(r))
    trigger.save_config({"refusals": {"policy": "Não posso: {reason}"}, "translate_notices": False})
    raw = json.loads(trigger.config_path().read_text())
    ck("both notices and the translation switch are saved",
       raw["refusals"]["policy"] == "Não posso: {reason}" and raw["translate_notices"] is False)
    trigger.save_config({"github": {"token": "github_pat_abc", "repos": "o/r, acme",
                                    "events": {"review": {"on": False},
                                               "ci_failed": {"on": True, "do": "  fix it  "}}}})
    trigger.save_config({"github": {"events": {"review": {"do": "tell me"}}}})
    raw = json.loads(trigger.config_path().read_text())
    ev = trigger.config_summary(trigger.load_config())["github"]["events"]
    ck("github events merge per kind and per field",
       raw["github"]["events"]["review"] == {"on": False, "do": "tell me"}
       and (ev["ci_failed"]["on"], ev["ci_failed"]["do"], ev["ci_failed"]["action"]) == (True, "fix it", "claude")
       and ev["mention"]["on"] is True
       and raw["github"]["repos"] == ["o/r", "acme"])
    ck("a fine-grained token is accepted and never shown",
       "github_pat_abc" not in json.dumps(trigger.config_summary(trigger.load_config())))
    trigger.save_config({"bogus": 1, "slack": {"bogus": 2}})
    raw = json.loads(trigger.config_path().read_text())
    ck("an unknown key is not written", "bogus" not in raw and "bogus" not in raw.get("slack", {}))


# ============================================ access + refusals with no model
def _calls():
    f = Path(HOME) / "claude-calls.jsonl"
    return len(f.read_text().splitlines()) if f.exists() else 0


def test_access(cfg):
    print("\n== access: allowed and blocked users, refusals that cost no model call ==")
    base = json.loads(json.dumps(cfg))
    base.pop("refuse_without_model")                       # the default: on
    base["slack"].update(allowed_users=["UFRIEND"], blocked_users=["UPEST"])
    ran, dispatch = dispatcher()
    con = trigger.db()

    fake = install_http()
    before = _calls()
    ev = trigger.slack_event(slack_msg("800.1", "UOTHER", "<@UBOT> please"), "C8", base,
                             bot_user_id="UBOT")
    res = trigger.handle(ev, base, dispatch, trigger.slack_notify(base))
    post = calls_to(fake, "chat.postMessage")
    ck("not accepted: refused, and the notice goes out as written, through the API",
       res["action"] == "refused" and not ran and len(post) == 1
       and post[0]["form"]["text"].startswith("I'm not enabled"), str(res))
    ck("and no model was called for it", _calls() == before)

    no_user = json.loads(json.dumps(base))
    no_user["slack"].pop("user_token")
    fake = install_http()
    ev = trigger.slack_event(slack_msg("800.2", "UOTHER", "<@UBOT> please"), "C7", no_user,
                             bot_user_id="UBOT")
    res = trigger.handle(ev, no_user, dispatch, trigger.slack_notify(no_user))
    ck("with no user token there is no model-free way to post: only logged, no session",
       res["action"] == "refused" and _calls() == before and not calls_to(fake, "chat.postMessage")
       and trigger.actions_for(con, res["request_id"])[-1]["kind"] == "refusal-notice-skipped")

    ev = trigger.slack_event(slack_msg("800.3", "UFRIEND", "<@UBOT> hi"), "C8", base,
                             bot_user_id="UBOT")
    res = trigger.handle(ev, base, dispatch)
    ck("an allowed user runs in a conversation nobody opened",
       res["action"] == "run" and ran == [res["request_id"]]
       and trigger.get_request(con, res["request_id"])["reason"] == "allowed user")
    ck("and opens nothing for anyone else", not trigger.gate_enabled(con, "slack:C8:800.3"))

    trigger.set_gate(con, "slack:C8", True, by="test")
    fake = install_http()
    ev = trigger.slack_event(slack_msg("800.4", "UPEST", "<@UBOT> hi"), "C8", base,
                             bot_user_id="UBOT")
    res = trigger.handle(ev, base, dispatch, trigger.slack_notify(base))
    post = calls_to(fake, "chat.postMessage")
    ck("a blocked user is refused even in an open channel, with the not-allowed notice",
       res["action"] == "refused" and len(ran) == 1 and post
       and post[0]["form"]["text"].startswith("You can't ask me that here")
       and trigger.get_request(con, res["request_id"])["reason"] == "blocked")
    trigger.set_gate(con, "slack:C8", False, by="test")
    ev = trigger.slack_event(slack_msg("800.5", "UOWNER", "<@UBOT> go"), "C8",
                             {**base, "slack": {**base["slack"], "blocked_users": ["UOWNER"]}},
                             bot_user_id="UBOT")
    ck("the owner is never blocked", trigger.handle(ev, base, dispatch)["action"] == "run")
    ck("the lists save upper-cased and checked",
       trigger.save_config({"slack": {"allowed_users": "u1abc, U2DEF"}})["ok"]
       and trigger.load_config()["slack"]["allowed_users"] == ["U1ABC", "U2DEF"]
       and not trigger.save_config({"slack": {"blocked_users": ["not a user"]}})["ok"])
    trigger.save_config({"slack": {"allowed_users": []}})


# ============================================================ actions
def _script(name, text, mode=0o755):
    d = trigger.graph.scripts_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)
    (d / name).chmod(mode)


ECHO = """#!/usr/bin/env python3
import json, os, sys
print(json.dumps({"argv": sys.argv[1:], "user": os.environ.get("BRAIN_TRIGGER_USER"),
                  "command": os.environ.get("BRAIN_COMMAND"),
                  "args": json.loads(os.environ.get("BRAIN_ARGS_JSON") or "[]"),
                  "rid": os.environ.get("MEMORY_LEDGER_REQUEST_ID")}))
"""


def test_commands(cfg):
    print("\n== commands: @brain <name> {a, b}, matched by a pattern, any action ==")
    _script("echo.py", ECHO)
    _script("plain.py", ECHO, mode=0o644)
    ccfg = json.loads(json.dumps(cfg))
    ccfg["slack"]["commands"] = [
        {"name": "deploy", "action": "script", "script": {"name": "echo.py"}},
        {"name": "ship", "action": "script", "anyone": True,
         "script": {"name": "plain.py", "args": ["--env={arg_1}", "{trigger_user_id}"]}},
        {"name": "ticket", "pattern": r"^ticket (?P<key>[A-Z]+-\d+)", "anyone": True,
         "action": "http", "http": {"method": "POST", "url": "https://hook.example/t/{key}?who={trigger_user}",
                                    "headers": {"X-Who": "{trigger_user}"},
                                    "body": '{"text": "{trigger_content}", "key": "{key}"}'}},
        {"name": "ask", "anyone": True, "do": "Answer {arg_1} briefly."},
        {"name": "off", "on": False, "action": "script", "script": {"name": "echo.py"}},
    ]
    ran, dispatch = dispatcher()
    con = trigger.db()
    install_http()

    cmd, v = trigger.match_command(ccfg, "<@UBOT> deploy {staging, v1.2 ,  now}", "UBOT")
    ck("the name, then the braces: split on commas and trimmed",
       cmd["name"] == "deploy" and v["args"] == "staging, v1.2 ,  now" and v["arg_1"] == "staging"
       and v["arg_2"] == "v1.2" and v["arg_3"] == "now" and v["arg_4"] == ""
       and v["args_list"] == ["staging", "v1.2", "now"], str(v))
    ck("case does not matter, a longer word is not the command, and an off one never matches",
       trigger.match_command(ccfg, "<@UBOT> DEPLOY", "UBOT")[0]["name"] == "deploy"
       and trigger.match_command(ccfg, "<@UBOT> deployment plan", "UBOT")[0] is None
       and trigger.match_command(ccfg, "<@UBOT> off {x}", "UBOT")[0] is None)
    cmd, v = trigger.match_command(ccfg, "<@UBOT> ticket PROJ-12 please", "UBOT")
    ck("a pattern's named groups are variables", cmd["name"] == "ticket" and v["key"] == "PROJ-12")

    ev = trigger.slack_event(slack_msg("900.1", "UOWNER", "<@UBOT> deploy {staging; rm -rf ~, $(id)}"),
                             "C5", ccfg, bot_user_id="UBOT")
    ck("a command is its own kind, carries its action, and opens nothing",
       ev["meta"]["kind"] == "command" and ev["meta"]["command"] == "deploy"
       and ev["meta"]["action"]["type"] == "script" and ev["opens"] is False)
    res = trigger.handle(ev, ccfg, dispatch)
    out = trigger.run_request(res["request_id"], ccfg)
    req = trigger.get_request(con, res["request_id"])
    got = json.loads(req["result"])
    ck("the owner's command runs the script, no session", out["status"] == "done"
       and not trigger.gate_enabled(con, "slack:C5:900.1"), str(out))
    ck("its arguments arrive as argv items, never through a shell",
       got["argv"] == ["staging; rm -rf ~", "$(id)"] and got["args"] == got["argv"], str(got))
    ck("with every variable in the environment, and the request id",
       got["user"] == "Name UOWNER" and got["command"] == "deploy"
       and got["rid"] == res["request_id"])
    ck("and the run is logged as an action",
       [a["kind"] for a in trigger.actions_for(con, res["request_id"])] == ["script"])

    fake = install_http()
    ev = trigger.slack_event(slack_msg("900.2", "UOTHER", "<@UBOT> deploy {prod}"), "C5", ccfg,
                             bot_user_id="UBOT")
    trigger.set_gate(con, "slack:C5", True, by="test")
    res = trigger.handle(ev, ccfg, dispatch, trigger.slack_notify(ccfg))
    ck("someone else may not run an owner-only command, even in an open channel",
       res["action"] == "refused" and trigger.get_request(con, res["request_id"])["reason"]
       == "owner-only command")
    ev = trigger.slack_event(slack_msg("900.3", "UOTHER", "<@UBOT> ship {prod}"), "C5", ccfg,
                             bot_user_id="UBOT")
    res = trigger.handle(ev, ccfg, dispatch)
    out = trigger.run_request(res["request_id"], ccfg)
    got = json.loads(trigger.get_request(con, res["request_id"])["result"])
    ck("an `anyone` command runs for someone the gate lets in, and a file that is not "
       "executable runs through its interpreter", out["status"] == "done", str(out))
    ck("its own arguments are templates, one argv item each",
       got["argv"] == ["--env=prod", "UOTHER"], str(got))
    trigger.set_gate(con, "slack:C5", False, by="test")

    sent = []
    trigger.http_raw = lambda method, url, headers=None, data=None, timeout=30: (
        sent.append({"method": method, "url": url, "headers": headers, "data": data}) or (201, "ok"))
    ev = trigger.slack_event(slack_msg("900.4", "UOWNER", '<@UBOT> ticket PROJ-9 "quoted" & more'),
                             "C5", ccfg, bot_user_id="UBOT")
    res = trigger.handle(ev, ccfg, dispatch)
    out = trigger.run_request(res["request_id"], ccfg)
    body = json.loads(sent[-1]["data"].decode())
    ck("an http command calls the API", out["status"] == "done" and sent[-1]["method"] == "POST")
    ck("values are percent-encoded in the URL",
       sent[-1]["url"] == "https://hook.example/t/PROJ-9?who=Name%20UOWNER", sent[-1]["url"])
    ck("and JSON-escaped in a JSON body, so a quote cannot end the string",
       body == {"text": '<@UBOT> ticket PROJ-9 "quoted" & more', "key": "PROJ-9"}
       and sent[-1]["headers"]["Content-Type"].startswith("application/json"), str(body))
    trigger.http_raw = lambda method, url, headers=None, data=None, timeout=30: (500, "boom")
    ev = trigger.slack_event(slack_msg("900.5", "UOWNER", "<@UBOT> ticket PROJ-10"), "C5", ccfg,
                             bot_user_id="UBOT")
    out = trigger.run_request(trigger.handle(ev, ccfg, dispatch)["request_id"], ccfg)
    ck("a 500 is a failure", out["status"] == "failed")

    ev = trigger.slack_event(slack_msg("900.6", "UOWNER", "<@UBOT> ask {the rota}"), "C5", ccfg,
                             bot_user_id="UBOT")
    req = trigger.get_request(con, trigger.handle(ev, ccfg, dispatch)["request_id"])
    prompt = trigger.build_prompt(req, ccfg)
    ck("a claude command's run command fills in its arguments, and the session is told",
       "Answer the rota briefly." in prompt and "command: ask" in prompt
       and 'command_args: ["the rota"]' in prompt and "slack_event: command" in prompt)

    for patch, needle in (
        ({"slack": {"commands": [{"name": "bad name"}]}}, "is not a name"),
        ({"slack": {"commands": [{"name": "a"}, {"name": "A"}]}}, "two commands"),
        ({"slack": {"commands": [{"name": "a", "pattern": "("}]}}, "not a regular expression"),
        ({"slack": {"commands": [{"name": "a", "pattern": "(?P<owner>x)"}]}}, "already a variable"),
        ({"slack": {"commands": [{"name": "a", "do": "{nope}"}]}}, "{nope}"),
        ({"slack": {"commands": [{"name": "a", "action": "script",
                                  "script": {"name": "missing.sh"}}]}}, "no script missing.sh"),
        ({"slack": {"commands": [{"name": "a", "action": "http"}]}}, "needs a URL"),
        ({"slack": {"commands": [{"name": "a", "action": "http",
                                  "http": {"url": "{trigger_link}"}}]}}, "must start with http"),
        ({"slack": {"commands": [{"name": "a", "action": "http",
                                  "http": {"url": "https://x", "headers": {"X": "a\nb"}}}]}},
         "one line"),
        ({"slack": {"commands": [{"name": "a", "action": "script",
                                  "script": {"name": "../x"}}]}}, "bare filename"),
        ({"jira": {"events": {"transitioned": {"on": True, "action": "shell"}}}}, "must be one of"),
    ):
        r = trigger.save_config(patch)
        ck(f"refused: {needle}", r["ok"] is False and needle in r["error"], str(r))
    r = trigger.save_config({"slack": {"commands": [
        {"name": "t", "pattern": r"^t (?P<key>\w+)", "do": "look at {key} for {trigger_user}"}]}})
    ck("a pattern's groups may be used in its own run command",
       r["ok"] and r["config"]["slack"]["commands"][0]["do"] == "look at {key} for {trigger_user}",
       str(r)[:200])
    trigger.save_config({"slack": {"commands": []}})


def test_slack_member(cfg):
    print("\n== slack: the bot added to or removed from a channel ==")
    fake = install_http()
    fake.on("POST", trigger.SLACK_API + "conversations.info",
            lambda form, **_: {"ok": True, "channel": {"name": "ops"}})
    mcfg = json.loads(json.dumps(cfg))
    joined = {"type": "member_joined_channel", "user": "UBOT", "channel": "C77",
              "inviter": "UOWNER", "event_ts": "1700000100.000001"}
    ck("switched off, it is nothing",
       trigger.slack_member_event(joined, mcfg, bot_user_id="UBOT") is None)
    mcfg["slack"]["events"] = {"joined_channel": {"on": True},
                               "left_channel": {"on": True, "action": "script",
                                                "script": {"name": "echo.py"}}}
    ck("another member joining is nothing", trigger.slack_member_event(
        {**joined, "user": "USOMEONE"}, mcfg, bot_user_id="UBOT") is None)
    ev = trigger.slack_member_event(joined, mcfg, bot_user_id="UBOT")
    ran, dispatch = dispatcher()
    con = trigger.db()
    res = trigger.handle(ev, mcfg, dispatch)
    req = trigger.get_request(con, res["request_id"])
    v = trigger.request_variables(req, mcfg)
    ck("the bot added: a subscribed run, with who and where",
       res["action"] == "run" and req["reason"] == "subscribed: joined_channel"
       and v["trigger_kind"] == "joined_channel" and v["slack_channel_name"] == "ops"
       and v["trigger_user"] == "Name UOWNER" and v["trigger_user_is_owner"] == "true")
    ck("even when the owner did it, it opens nothing: the channel is not open to everyone",
       not trigger.gate_enabled(con, "slack:C77"))
    prompt = trigger.build_prompt(req, mcfg)
    ck("the session is told it is not answering anyone",
       "slack_event: joined_channel" in prompt and "none by default" in prompt
       and "added me to #ops" in prompt)
    ev = trigger.slack_member_event({"type": "member_left_channel", "user": "UBOT",
                                     "channel": "C77", "event_ts": "1700000200.1"},
                                    mcfg, bot_user_id="UBOT")
    res = trigger.handle(ev, mcfg, dispatch)
    out = trigger.run_request(res["request_id"], mcfg)
    ck("removed, running its script", ev["meta"]["kind"] == "left_channel"
       and out["status"] == "done", str(out))


def test_jira_changes(cfg):
    print("\n== jira: transitions, assignments and comments on watched issues ==")
    fake = install_http()
    jcfg = json.loads(json.dumps(cfg))
    jcfg["jira"]["events"] = {k: {"on": True} for k in trigger.JIRA_CHANGE_KINDS}
    jira = trigger.Jira(jcfg)
    base = jcfg["jira"]["base_url"]
    now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    con = trigger.db()
    trigger.set_checkpoint(con, "jira", now.isoformat(), only_forward=False)

    def ts(minutes):
        return (now + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

    x = {"accountId": "acc-x", "displayName": "X"}
    me = {"accountId": "acc-owner", "displayName": "Owner"}
    issue = {"key": "OPS-3", "fields": {"summary": "Rotate keys"}, "changelog": {"histories": [
        {"id": "70", "created": ts(4), "author": me, "items": [
            {"field": "status", "fromString": "To Do", "toString": "In Progress"},
            {"field": "assignee", "from": None, "fromString": None, "to": "acc-owner",
             "toString": "Owner"}]},
        {"id": "71", "created": ts(6), "author": x, "items": [
            {"field": "assignee", "from": "acc-owner", "fromString": "Owner", "to": None,
             "toString": None}]},
        {"id": "60", "created": ts(-900), "author": x, "items": [
            {"field": "status", "fromString": "A", "toString": "B"}]}]}}
    searched = []

    def search(body, **_):
        searched.append(body)
        if "currentUser()" in body["jql"]:
            return {"issues": [issue], "isLast": True}
        return {"issues": [], "isLast": True}
    fake.on("POST", base + "/rest/api/3/search/jql", search)
    fake.on("GET", base + "/rest/api/3/issue/OPS-3/comment", lambda **_: {"comments": [
        {"id": "31", "created": ts(7), "author": x, "body": adf("can you rotate the staging one too?")},
        {"id": "30", "created": ts(5), "author": me, "body": adf("on it")}]})
    ran, dispatch = dispatcher()
    res = trigger.jira_poll(jcfg, dispatch, now=now + timedelta(minutes=10), jira=jira)
    watch = [b for b in searched if "currentUser()" in b["jql"]]
    ck("changes are read on the watched issues, with the changelog",
       watch and watch[-1].get("expand") == "changelog"
       and "AND (assignee = currentUser()" in watch[-1]["jql"], str(searched))
    reqs = [trigger.get_request(con, r) for r in ran]
    kinds = [r["meta"]["kind"] for r in reqs]
    ck("a transition, an assignment, an unassignment and a colleague's comment, oldest first",
       kinds == ["transitioned", "assigned", "unassigned", "comment"], str(kinds))
    v = {r["meta"]["kind"]: trigger.request_variables(r, jcfg) for r in reqs}
    ck("the transition's from and to",
       v["transitioned"]["jira_from"] == "To Do" and v["transitioned"]["jira_to"] == "In Progress"
       and v["transitioned"]["trigger_user_is_owner"] == "true")
    ck("whom it was assigned to, and who had it",
       v["assigned"]["jira_assignee"] == "Owner" and v["assigned"]["jira_assignee_id"] == "acc-owner"
       and v["unassigned"]["jira_previous_assignee"] == "Owner"
       and v["unassigned"]["jira_assignee"] == "")
    ck("the owner's own plain comment is conversation, not a trigger",
       all(r["event_id"] != "OPS-3:comment:30" for r in reqs))
    ck("a change the owner made counts, and none of it opens the issue",
       reqs[0]["reason"] == "subscribed: transitioned" and not trigger.gate_enabled(con, "jira:OPS-3"))
    ck("a change older than the window is not read", all(":h60:" not in r["event_id"] for r in reqs))
    prompt = trigger.build_prompt(reqs[0], jcfg)
    ck("the session gets the event and its run command, and posts nothing by default",
       "jira_event: transitioned" in prompt and "moved from To Do to In Progress" in prompt
       and "post on Jira only if it says to" in prompt)
    again = []
    trigger.jira_poll(jcfg, lambda rid: again.append(rid), now=now + timedelta(minutes=11), jira=jira)
    ck("an overlapping poll runs nothing twice", not again)


def test_slack_dms(cfg):
    print("\n== slack: the brain tagged in a DM with the owner ==")
    fake = install_http()
    dcfg = json.loads(json.dumps(cfg))
    dcfg.pop("refuse_without_model")
    dcfg["slack"]["dms"] = True
    ran, dispatch = dispatcher()
    con = trigger.db()
    ck("a tag with a label, as Slack writes it outside the bot's channels, is a tag",
       trigger.mentions_bot("<@UBOT|brain> test", "UBOT")
       and not trigger.mentions_bot("<@UBOTX> test", "UBOT"))

    def dm(ts, user, text, thread=None):
        m = {"type": "message", "channel_type": "im", "channel": "D1", "ts": ts, "user": user,
             "text": text}
        if thread:
            m["thread_ts"] = thread
        return m

    ev = trigger.slack_event(dm("950.1", "UANA", "<@UBOT|brain> what is on my plate?"), "D1",
                             dcfg, bot_user_id="UBOT")
    ck("a DM is one conversation, the whole DM",
       ev["scope"] == "slack:D1" and ev["parent_scope"] == "" and ev["meta"]["channel_type"] == "im")
    res = trigger.handle(ev, dcfg, dispatch, trigger.slack_notify(dcfg))
    post = calls_to(fake, "chat.postMessage")
    ck("someone else's tag in a DM nobody opened is refused, the notice in the DM, not a thread",
       res["action"] == "refused" and post and post[-1]["form"]["channel"] == "D1"
       and "thread_ts" not in post[-1]["form"], str(post[-1:]))
    ck("its link was asked for with the user token (the bot is not in the DM)",
       calls_to(fake, "chat.getPermalink")[-1]["form"]["channel"] == "D1")

    own = trigger.slack_event(dm("950.2", "UOWNER", "<@UBOT> help Ana with this"), "D1", dcfg,
                              bot_user_id="UBOT")
    res = trigger.handle(own, dcfg, dispatch)
    ck("the owner's tag runs and opens that DM", res["action"] == "run"
       and trigger.gate_enabled(con, "slack:D1"))
    again = trigger.slack_event(dm("950.3", "UANA", "<@UBOT> and the rota?"), "D1", dcfg,
                                bot_user_id="UBOT")
    res = trigger.handle(again, dcfg, dispatch)
    ck("then the other person's next tag, not in any thread, runs", res["action"] == "run")
    prompt = trigger.build_prompt(trigger.get_request(con, res["request_id"]), dcfg)
    ck("and the session replies in the DM as a new message, not a thread",
       "reply in that direct message, as the owner, as a new message" in prompt)

    open_cfg = json.loads(json.dumps(dcfg))
    open_cfg["slack"]["dms_open"] = True
    ev = trigger.slack_event(dm("950.4", "UBO", "<@UBOT> hi"), "D2", open_cfg, bot_user_id="UBOT")
    res = trigger.handle(ev, open_cfg, dispatch)
    ck("with dms_open, anyone in a DM with the owner gets in",
       res["action"] == "run"
       and trigger.get_request(con, res["request_id"])["reason"] == "direct message"
       and not trigger.gate_enabled(con, "slack:D2"))
    blocked = json.loads(json.dumps(open_cfg))
    blocked["slack"]["blocked_users"] = ["UBO"]
    ev = trigger.slack_event(dm("950.5", "UBO", "<@UBOT> hi"), "D2", blocked, bot_user_id="UBOT")
    ck("but not someone blocked", trigger.handle(ev, blocked, dispatch)["action"] == "refused")
    ck("a message without the tag is nothing",
       trigger.slack_event(dm("950.6", "UBO", "hello"), "D2", open_cfg, bot_user_id="UBOT") is None)
    no_user = json.loads(json.dumps(dcfg))
    no_user["slack"].pop("user_token")
    chk = trigger.check(trigger._merge(trigger.DEFAULTS, no_user), offline=True, only="slack")
    ck("dms without a user token is a problem the check names",
       any("slack.dms is on but there is no user_token" in x for x in chk["problems"]), str(chk))


# =================================================================== tasks
def test_tasks(cfg):
    print("\n== tasks: every request as a card, and one in full with its session ==")
    install_http()
    ran, dispatch = dispatcher()
    projects = Path(HOME) / "claude-config" / "projects"
    os.environ["CLAUDE_CONFIG_DIR"] = str(projects.parent)
    try:
        ev = trigger.slack_event(slack_msg("950.1", "UOWNER", "<@UBOT> watch me run"), "C1", cfg,
                                 bot_user_id="UBOT")
        rid = trigger.handle(ev, cfg, dispatch)["request_id"]
        trigger.run_request(rid, cfg)
        con = trigger.db()
        req = trigger.get_request(con, rid)
        args = json.loads((Path(HOME) / "claude-calls.jsonl").read_text().splitlines()[-1])["args"]
        ck("the session id is chosen up front and passed to claude",
           req["session_id"] and args[args.index("--session-id") + 1] == req["session_id"])
        ck("the command and working directory are recorded",
           json.loads(req["command"])[1:] == args and Path(req["cwd"]).is_dir())

        # The session's transcript, where Claude Code writes it.
        folder = projects / trigger._project_slug(req["cwd"])
        folder.mkdir(parents=True, exist_ok=True)
        lines = [
            {"type": "user", "timestamp": "t0", "message": {"content": "You were summoned"}},
            {"type": "assistant", "timestamp": "t1", "message": {"content": [
                {"type": "text", "text": "Reading it."},
                {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}]}},
            {"type": "user", "timestamp": "t2", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": "a.txt", "is_error": False}]}},
            {"type": "assistant", "timestamp": "t3", "isSidechain": True, "message": {"content": [
                {"type": "text", "text": "a subagent, not this session"}]}},
            {"type": "assistant", "timestamp": "t4", "message": {"content": [
                {"type": "tool_use", "id": "tu2", "name": "Read", "input": {"file": "x"}}]}},
        ]
        (folder / f"{req['session_id']}.jsonl").write_text(
            "\n".join(json.dumps(x) for x in lines) + "\nnot json\n")

        d = trigger.task_detail(rid)
        t = d["transcript"]
        ck("the detail carries the command, the whole message and the actions",
           d["ok"] and d["task"]["command"][0] == cfg["claude"]["bin"]
           and d["task"]["text"].endswith("watch me run") and isinstance(d["actions"], list))
        ck("the transcript is read: prompt, what it said, tool calls with results",
           t["found"] and t["tool_calls"] == 2
           and [e["kind"] for e in t["events"]] == ["text", "text", "tool", "tool"]
           and t["events"][0]["role"] == "prompt"
           and t["events"][2]["result"] == "a.txt" and t["events"][3]["result"] is None)
        ck("a subagent's lines and a broken line are skipped",
           not any("subagent" in (e.get("text") or "") for e in t["events"]))

        trigger._set_request(con, rid, status="running")
        p = trigger.tasks_payload(50)
        row = next(x for x in p["tasks"] if x["id"] == rid)
        ck("the list names each task and counts by status",
           p["ok"] and row["name"] == "mention" and row["source"] == "slack"
           and p["counts"].get("running", 0) >= 1 and "command" not in row)
        ck("a running task carries its progress: tool calls and the latest one",
           row["progress"]["tool_calls"] == 2 and row["progress"]["last_tool"] == "Read"
           and '"file"' in row["progress"]["last_input"])
        # What the brain's hooks write while the session runs, read back live.
        g = trigger.graph
        sid = req["session_id"]
        conv = g.api_conversation(con, sid)["node"]["id"]
        main_ = g.api_agent_spawn(con, conv, "main", session_id=sid)["node"]["id"]
        sub = g.api_agent_spawn(con, main_, "pr-reviewer", body="review it",
                                session_id=sid)["node"]["id"]
        g.api_create_node(con, "capture", "answered", "Done, replied in thread.",
                          {"role": "assistant", "session_id": sid, "captured_at": "2099-01-01T00:00:00+00:00"})
        trigger.log_action(con, rid, "reply", "https://slack.example/r", "answered")
        act = trigger.task_detail(rid)["activity"]
        ck("the detail carries the session's live activity from the database",
           act["conversation"]["status"] == "open" and len(act["agents"]) == 2
           and act["subagents_running"] == 1 and act["actions"] == 1
           and act["last_message"] == "Done, replied in thread.")
        kinds = [(e["kind"], e["title"]) for e in act["timeline"]]
        ck("its timeline has the session, the subagent, the action and the answer, in order",
           kinds[0] == ("session", "session started")
           and ("agent", "subagent pr-reviewer started") in kinds
           and ("action", "reply") in kinds and kinds[-1] == ("message", "answered"))
        g.api_update_node(con, sub, status="done", props_patch={"outcome": "completed"})
        row = next(x for x in trigger.tasks_payload(50)["tasks"] if x["id"] == rid)
        ck("the card's progress follows the database as the hooks write",
           row["progress"]["subagents"] == 1 and row["progress"]["subagents_running"] == 0
           and row["progress"]["last_action"]["kind"] == "reply")
        trigger._set_request(con, rid, status="done")

        # A request from before session ids were kept is found by its id.
        old = trigger.handle(trigger.slack_event(slack_msg("950.2", "UOWNER", "<@UBOT> old"), "C1",
                                                 cfg, bot_user_id="UBOT"), cfg, dispatch)["request_id"]
        trigger._set_request(con, old, status="done", started_at=trigger.now_iso())
        legacy = projects / trigger._project_slug(trigger.session_cwd(trigger.load_config()))
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / "legacy-session.jsonl").write_text(json.dumps(
            {"type": "user", "timestamp": "t0",
             "message": {"content": f"request_id: {old}\nsource: slack"}}) + "\n")
        ck("an older task's transcript is found by its request id, and remembered",
           trigger.task_detail(old)["transcript"]["found"]
           and trigger.get_request(con, old)["session_id"] == "legacy-session")
        ck("an unknown task is refused", trigger.task_detail("nope")["ok"] is False)

        # A run killed before claude printed its total: priced from the transcript.
        u = {"input_tokens": 1000, "output_tokens": 1000, "cache_read_input_tokens": 1_000_000,
             "cache_creation_input_tokens": 1000,
             "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 1000}}
        m = {"type": "assistant", "message": {"id": "msg1", "model": "claude-opus-5", "usage": u}}
        tp = folder / "priced.jsonl"
        tp.write_text("\n".join(json.dumps(x) for x in (m, m)) + "\n")   # one message, two blocks
        (folder / "priced" / "subagents").mkdir(parents=True, exist_ok=True)
        (folder / "priced" / "subagents" / "agent-1.jsonl").write_text(json.dumps(
            {"type": "assistant", "message": {"id": "msg2", "model": "claude-haiku-4-5",
                                              "usage": {"input_tokens": 1_000_000}}}) + "\n")
        # opus 5: 1k in $0.005 + 1k out $0.025 + 1M read $0.50 + 1k 1h-write $0.01; haiku 1M in $1
        ck("a session's cost is estimated from its token usage and its subagents', each message once",
           abs(trigger.estimate_cost(tp) - 1.54) < 1e-9, str(trigger.estimate_cost(tp)))
        ck("no transcript, no estimate", trigger.estimate_cost(folder / "missing.jsonl") is None)
        with (folder / f"{req['session_id']}.jsonl").open("a") as f:
            f.write(json.dumps(m) + "\n")
        trigger._set_request(con, rid, status="timeout", cost_usd=None, cost_estimated=0)
        row = next(x for x in trigger.tasks_payload(50)["tasks"] if x["id"] == rid)
        ck("a timed-out task without a cost gets one estimated, and it is kept",
           row["cost_estimated"] is True and row["cost_usd"] is not None
           and trigger.get_request(con, rid)["cost_estimated"] == 1)
    finally:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)


def test_http_transport():
    print("\n== http_json: a connection that breaks in transit ==")
    import http.client
    import urllib.request

    class Resp:
        def __init__(self, raw):
            self.raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            if isinstance(self.raw, Exception):
                raise self.raw
            return self.raw

    real_urlopen = urllib.request.urlopen
    script = []
    urllib.request.urlopen = lambda req, timeout=30: Resp(script.pop(0))
    try:
        script[:] = [http.client.IncompleteRead(b"[{"), b'[{"accountId": "a"}]']
        ck("a GET cut off mid-body is read again, once",
           REAL_HTTP_JSON("GET", "https://x/y") == [{"accountId": "a"}] and not script)
        script[:] = [http.client.IncompleteRead(b""), ConnectionResetError()]
        try:
            REAL_HTTP_JSON("GET", "https://x/y?q=1")
            broke = ""
        except trigger.TriggerError as e:
            broke = str(e)
        ck("twice is a TriggerError, never a crash", "connection broke" in broke and "q=1" not in broke, broke)
        script[:] = [http.client.IncompleteRead(b""), b"{}"]
        try:
            REAL_HTTP_JSON("POST", "https://x/y", body={})
            posted = "no error"
        except trigger.TriggerError as e:
            posted = str(e)
        ck("a POST is never sent twice", "connection broke" in posted and len(script) == 1, posted)
    finally:
        urllib.request.urlopen = real_urlopen


def main():
    try:
        test_config()
        test_http_transport()
        cfg = write_cfg()
        test_gate(cfg)
        test_loops(cfg)
        test_audit(cfg)
        test_dispatch(cfg)
        test_logging(cfg)
        test_run_commands(cfg)
        test_slack_catch_up(cfg)
        test_jira(cfg)
        test_github(cfg)
        test_check(cfg)
        test_channels(cfg)
        test_notices(cfg)
        test_decline(cfg)
        test_session_hygiene(cfg)
        test_tasks(cfg)
        test_access(cfg)
        test_commands(cfg)
        test_slack_member(cfg)
        test_jira_changes(cfg)
        test_slack_dms(cfg)
        test_save()
    finally:
        import shutil
        shutil.rmtree(HOME, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
