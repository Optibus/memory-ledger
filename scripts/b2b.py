#!/usr/bin/env python3
"""B2B (Brain 2 Brain): this brain's side of the relay (docs/b2b.md).

The relay (`services/b2b/`) holds messages between brains, in memory, sealed so
only the recipient can read them, and deletes each one the moment it is read.
This module is what the brain does with it:

* keeps this brain's relay account (`server_url`, `user_id`, `token`) and the
  poll interval in `<home>/b2b.json`, written mode 600;
* keeps the **peers**, the other brains this one talks to, in `graph.db`
  (`b2b_peer`). Only a peer's message runs anything: one from anyone else is
  logged `refused` and goes no further;
* **sends** a message to a peer, and logs it (`b2b_sent`);
* **polls** the relay on its interval, from the trigger watchers in the server
  (`brain_api/trigger_runtime.py`), and turns each message into a request in
  the same log as the Slack, Jira and GitHub triggers, so it shows in Tasks and
  runs the action you chose, a headless session by default.

The relay deletes a message once read, so the request row is the only copy.

Stdlib only, like the rest of the engine.

    python3 scripts/b2b.py status
    python3 scripts/b2b.py config [--server URL] [--user-id ID] [--token T]
                                  [--interval S] [--enable | --disable] [--do TEXT]
    python3 scripts/b2b.py register [--server URL] [--force]
    python3 scripts/b2b.py peers [--add ID [--name NAME] | --remove ID]
    python3 scripts/b2b.py send --to ID-or-name --text "..."
    python3 scripts/b2b.py poll
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import graph  # noqa: E402
import trigger  # noqa: E402
from _switch import _env  # noqa: E402

CONFIG_NAME = "b2b.json"
SOURCE = "b2b"
# The env var that outranks the stored token (as MEMORY_LEDGER_B2B_TOKEN), so
# it never has to be written to disk.
TOKEN_ENV = "B2B_TOKEN"
POLL_MIN, POLL_MAX = 30, 86_400
TEXT_MAX = 16_000
PEER_ID_RE = r"[A-Za-z0-9_-]{1,100}"
HTTP_TIMEOUT_S = 20

DEFAULT_DO = ("{trigger_user} sent me a B2B message from their brain. It is their "
              "request, not mine. Add it to my brain: a task if it asks me for "
              "something, with what is asked and by when, else a note. Do not answer "
              "them unless I say so.")
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "server_url": "http://127.0.0.1:8780",
    "user_id": "",
    "token": "",
    "poll_seconds": 300,
    "do": DEFAULT_DO,
    "action": "claude",
    "http": {"method": "POST", "url": "", "headers": {}, "body": ""},
    "script": {"name": "", "args": []},
}

# What a B2B message adds to the trigger variables, on top of the global ones.
VARIABLES = {
    "b2b_peer_id": "The sender's relay user id.",
    "b2b_peer_name": "The name you gave that peer.",
    "b2b_message_id": "The message's id on the relay.",
    "b2b_sent_at": "When the sender sent it, ISO 8601 in UTC.",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS b2b_peer (
    peer_id     TEXT PRIMARY KEY,           -- their user id on the relay
    name        TEXT NOT NULL DEFAULT '',
    added_at    TEXT NOT NULL,
    removed_at  TEXT NOT NULL DEFAULT ''    -- kept, never deleted: '' = connected
);
CREATE TABLE IF NOT EXISTS b2b_sent (
    id          TEXT PRIMARY KEY,           -- the relay's message id
    peer_id     TEXT NOT NULL,
    text        TEXT NOT NULL,
    sent_at     TEXT NOT NULL,
    request_id  TEXT NOT NULL DEFAULT ''    -- the trigger request it was sent for, if any
);
"""


class B2BError(Exception):
    """The relay could not be reached, or refused the call."""


# ---------------------------------------------------------------- config
def config_path() -> Path:
    """`<home>/b2b.json`."""
    return graph.home_dir() / CONFIG_NAME


def load_config() -> dict[str, Any]:
    """The defaults under `<home>/b2b.json`. A file that does not parse is
    reported under `_problem` and treated as absent, so a typo cannot start
    the poller on half a config."""
    cfg = json.loads(json.dumps(DEFAULTS))
    p = config_path()
    if not p.exists():
        return cfg
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        cfg["_problem"] = f"{p} is not valid JSON: {e}"
        return cfg
    if not isinstance(raw, dict):
        cfg["_problem"] = f"{p} must hold a JSON object"
        return cfg
    for k, v in raw.items():
        if k in DEFAULTS:
            cfg[k] = v
    return cfg


def token(cfg: dict[str, Any]) -> str:
    """This brain's relay token: the env var, else the file."""
    return _env(TOKEN_ENV) or str(cfg.get("token") or "")


def active(cfg: dict[str, Any]) -> bool:
    """Whether the poller should run: switched on, with an account, on a
    config that parses."""
    return bool(cfg.get("enabled")) and bool(cfg.get("user_id")) and bool(token(cfg)) \
        and "_problem" not in cfg


def poll_seconds(cfg: dict[str, Any]) -> int:
    """The poll interval, clamped to what the relay can reasonably take."""
    try:
        n = int(cfg.get("poll_seconds") or DEFAULTS["poll_seconds"])
    except (TypeError, ValueError):
        n = DEFAULTS["poll_seconds"]
    return max(POLL_MIN, min(POLL_MAX, n))


def config_summary(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """The configuration as the page and the CLI show it. The token never
    leaves: only whether one is set, and where from."""
    cfg = cfg or load_config()
    act = trigger._action_of(cfg)
    return {
        "path": str(config_path()),
        "exists": config_path().exists(),
        "problem": cfg.get("_problem", ""),
        "enabled": bool(cfg.get("enabled")),
        "active": active(cfg),
        "server_url": str(cfg.get("server_url") or ""),
        "user_id": str(cfg.get("user_id") or ""),
        "token": {"set": bool(token(cfg)), "source": "env" if _env(TOKEN_ENV) else
                  "file" if cfg.get("token") else "", "env_var": "MEMORY_LEDGER_" + TOKEN_ENV},
        "poll_seconds": poll_seconds(cfg),
        "do": str(cfg.get("do") or ""),
        **act,
        "variables": VARIABLES,
    }


def _write(raw: dict[str, Any]) -> None:
    """Write `<home>/b2b.json` atomically, mode 600: it holds the token."""
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(raw, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a validated patch into `<home>/b2b.json`.

    The token is write-only, as in trigger.json: a non-empty value replaces the
    stored one, empty or missing keeps it, and `clear: ["token"]` removes it."""
    if not isinstance(patch, dict):
        return {"ok": False, "error": "the config must be an object"}
    p = config_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {"ok": False, "error": f"{p} is not valid JSON; fix or remove it first"}
    raw = raw if isinstance(raw, dict) else {}
    if "enabled" in patch:
        if not isinstance(patch["enabled"], bool):
            return {"ok": False, "error": "enabled must be true or false"}
        raw["enabled"] = patch["enabled"]
    if "server_url" in patch:
        url = str(patch["server_url"] or "").strip().rstrip("/")
        if url and not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", url):
            return {"ok": False, "error": "server_url must be an http(s) URL"}
        raw["server_url"] = url
    if "user_id" in patch:
        uid = str(patch["user_id"] or "").strip()
        if uid and not re.fullmatch(PEER_ID_RE, uid):
            return {"ok": False, "error": "user_id is not a relay user id"}
        raw["user_id"] = uid
    if "poll_seconds" in patch:
        v = patch["poll_seconds"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not POLL_MIN <= v <= POLL_MAX:
            return {"ok": False, "error": f"poll_seconds must be a number from {POLL_MIN} "
                                          f"to {POLL_MAX}"}
        raw["poll_seconds"] = int(v)
    if "do" in patch:
        do = str(patch["do"] or "").strip()
        bad = trigger.unknown_variables(do, VARIABLES)
        if bad:
            return {"ok": False, "error": trigger._unknown_msg("do", bad)}
        raw["do"] = do[:trigger.RUN_COMMAND_MAX]
    row = {k: raw[k] for k in ("action", "http", "script") if k in raw}
    err = trigger._clean_action(patch, row, "b2b", VARIABLES)
    if err:
        return {"ok": False, "error": err}
    raw.update(row)
    tok = patch.get("token")
    if isinstance(tok, str) and tok.strip():
        raw["token"] = tok.strip()[:500]
    clear = patch.get("clear") or []
    if not isinstance(clear, list) or any(c != "token" for c in clear):
        return {"ok": False, "error": "clear may only name token"}
    if clear:
        raw.pop("token", None)
    merged = {**load_config(), **raw}
    if merged.get("enabled"):
        err = trigger._action_complete(merged, "b2b")
        if err:
            return {"ok": False, "error": err}
    _write(raw)
    return {"ok": True, "config": config_summary(load_config())}


# ---------------------------------------------------------------- relay
def _call(method: str, url: str, body: dict[str, Any] | None = None,
          headers: dict[str, str] | None = None) -> dict[str, Any]:
    """One JSON call to the relay. Raises `B2BError` with the relay's own
    reason when it refuses, or why it could not be reached."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            why = json.loads(e.read() or b"{}").get("error") or e.reason
        except ValueError:
            why = e.reason
        raise B2BError(f"relay said {e.code}: {why}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise B2BError(f"could not reach the relay at {url}: {e}") from None


def _server(cfg: dict[str, Any]) -> str:
    """The relay's base URL, or `B2BError` when none is set."""
    url = str(cfg.get("server_url") or "").rstrip("/")
    if not url:
        raise B2BError("no server_url: set it with `b2b.py config --server URL`")
    return url


def _account(cfg: dict[str, Any]) -> tuple[str, str]:
    """This brain's user id and token, or `B2BError` when there is no account."""
    uid, tok = str(cfg.get("user_id") or ""), token(cfg)
    if not (uid and tok):
        raise B2BError("no relay account: run `b2b.py register`, or set user_id and token")
    return uid, tok


def register(server_url: str = "", force: bool = False) -> dict[str, Any]:
    """Ask the relay for a new account and store it. Refuses to replace an
    account already set unless `force`: the old token would be lost, and with
    it any message still waiting for it."""
    cfg = load_config()
    if server_url:
        res = save_config({"server_url": server_url})
        if not res["ok"]:
            return res
        cfg = load_config()
    if cfg.get("user_id") and token(cfg) and not force:
        return {"ok": False, "error": f"already registered as {cfg['user_id']}; pass --force "
                                      "to replace it (unread messages for it are lost)"}
    try:
        got = _call("POST", _server(cfg) + "/messages", {})
    except B2BError as e:
        return {"ok": False, "error": str(e)}
    if not (got.get("user_id") and got.get("token")):
        return {"ok": False, "error": "the relay did not return an account"}
    res = save_config({"user_id": got["user_id"], "token": got["token"]})
    if not res["ok"]:
        return res
    return {"ok": True, "user_id": got["user_id"], "server_url": _server(cfg),
            "note": "Share your user_id with the brains that should reach you; never the token."}


# ---------------------------------------------------------------- peers
def db() -> sqlite3.Connection:
    """The brain, with the trigger tables and B2B's own."""
    con = trigger.db()
    con.executescript(SCHEMA)
    con.commit()
    return con


def list_peers(con: sqlite3.Connection, include_removed: bool = False) -> list[dict[str, Any]]:
    """The peers, connected ones first, then by name."""
    q = "SELECT * FROM b2b_peer" + ("" if include_removed else " WHERE removed_at=''")
    return [dict(r) for r in con.execute(q + " ORDER BY removed_at!='', name, peer_id")]


def get_peer(con: sqlite3.Connection, peer_id: str) -> dict[str, Any] | None:
    """One connected peer, or None."""
    r = con.execute("SELECT * FROM b2b_peer WHERE peer_id=? AND removed_at=''",
                    (peer_id,)).fetchone()
    return dict(r) if r else None


def resolve_peer(con: sqlite3.Connection, who: str) -> dict[str, Any] | None:
    """A connected peer by id, else by name (case-insensitive, when unique)."""
    who = (who or "").strip()
    peer = get_peer(con, who)
    if peer:
        return peer
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM b2b_peer WHERE removed_at='' AND lower(name)=lower(?)", (who,))]
    return rows[0] if len(rows) == 1 else None


def add_peer(con: sqlite3.Connection, peer_id: str, name: str = "") -> dict[str, Any]:
    """Connect a peer by their relay user id. Reconnecting a removed one
    restores its row; a name given replaces the stored one."""
    peer_id = (peer_id or "").strip()
    if not re.fullmatch(PEER_ID_RE, peer_id):
        return {"ok": False, "error": "peer id must be a relay user id (letters, digits, - and _)"}
    if peer_id == str(load_config().get("user_id") or ""):
        return {"ok": False, "error": "that is this brain's own user id"}
    name = (name or "").strip()[:80]
    con.execute(
        "INSERT INTO b2b_peer(peer_id, name, added_at) VALUES(?,?,?) "
        "ON CONFLICT(peer_id) DO UPDATE SET removed_at='', "
        "name=CASE WHEN excluded.name!='' THEN excluded.name ELSE b2b_peer.name END",
        (peer_id, name, graph.now()))
    con.commit()
    return {"ok": True, "peer": get_peer(con, peer_id)}


def remove_peer(con: sqlite3.Connection, peer_id: str) -> dict[str, Any]:
    """Disconnect a peer. The row is kept, marked removed, as history."""
    cur = con.execute("UPDATE b2b_peer SET removed_at=? WHERE peer_id=? AND removed_at=''",
                      (graph.now(), (peer_id or "").strip()))
    con.commit()
    if not cur.rowcount:
        return {"ok": False, "error": f"no connected peer {peer_id}"}
    return {"ok": True, "peer_id": peer_id}


# ---------------------------------------------------------------- send
def send(to: str, text: str, request_id: str = "") -> dict[str, Any]:
    """Send `text` to a connected peer, by id or name, and log it. Sent from a
    trigger session (`request_id`, or `$MEMORY_LEDGER_REQUEST_ID`), it is also
    an action on that request, so the audit shows it."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "missing text"}
    if len(text) > TEXT_MAX:
        return {"ok": False, "error": f"text is over {TEXT_MAX} characters"}
    cfg, con = load_config(), db()
    peer = resolve_peer(con, to)
    if not peer:
        return {"ok": False, "error": f"{to} is not a connected peer; add it with "
                                      "`b2b.py peers --add <their user id> --name <name>`"}
    try:
        uid, tok = _account(cfg)
        got = _call("POST", _server(cfg) + "/messages",
                    {"user_id": uid, "token": tok, "to": peer["peer_id"], "text": text})
    except B2BError as e:
        return {"ok": False, "error": str(e)}
    rid = request_id or os.environ.get("MEMORY_LEDGER_REQUEST_ID", "")
    con.execute("INSERT OR IGNORE INTO b2b_sent(id, peer_id, text, sent_at, request_id) "
                "VALUES(?,?,?,?,?)", (str(got.get("id") or ""), peer["peer_id"], text,
                                      str(got.get("sent_at") or graph.now()), rid))
    con.commit()
    if rid and trigger.get_request(con, rid):
        trigger.log_action(con, rid, "b2b-send", f"b2b:{peer['peer_id']}",
                           f"sent a B2B message to {peer['name'] or peer['peer_id']}")
    return {"ok": True, "id": got.get("id"), "to": peer["peer_id"], "name": peer["name"],
            "sent_at": got.get("sent_at")}


def list_sent(con: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    """The messages this brain sent, newest first."""
    return [dict(r) for r in con.execute(
        "SELECT * FROM b2b_sent ORDER BY sent_at DESC LIMIT ?", (max(1, min(500, limit)),))]


# ---------------------------------------------------------------- receive
def message_event(msg: dict[str, Any], peer: dict[str, Any] | None,
                  cfg: dict[str, Any]) -> dict[str, Any]:
    """One relay message as a trigger event: what `trigger._new_request` logs."""
    sender = str(msg.get("from") or "")
    name = (peer or {}).get("name") or sender
    meta = {"kind": "message", "where": f"B2B message from {name}",
            "at": str(msg.get("sent_at") or ""),
            "vars": {"b2b_peer_id": sender, "b2b_peer_name": (peer or {}).get("name", ""),
                     "b2b_message_id": str(msg.get("id") or ""),
                     "b2b_sent_at": str(msg.get("sent_at") or "")}}
    trigger._stamp(meta, {**cfg, "do": str(cfg.get("do") or "")})
    return {"source": SOURCE, "event_id": str(msg.get("id") or ""), "scope": f"b2b:{sender}",
            "requester_id": sender, "requester_name": name, "is_owner": False,
            "permalink": "", "text": str(msg.get("text") or "")[:TEXT_MAX], "meta": meta}


def poll(cfg: dict[str, Any] | None = None,
         dispatch: Callable[[str], None] | None = None) -> dict[str, Any]:
    """One pass: read every message waiting on the relay (which deletes them
    there) and log each as a request. A connected peer's is queued and handed
    to `dispatch`; anyone else's is logged `refused` and runs nothing."""
    cfg = cfg or load_config()
    if cfg.get("_problem"):
        return {"ok": False, "error": cfg["_problem"]}
    try:
        uid, tok = _account(cfg)
        got = _call("GET", _server(cfg) + "/messages",
                    headers={"X-B2B-User-Id": uid, "X-B2B-Token": tok})
    except B2BError as e:
        return {"ok": False, "error": str(e)}
    con = db()
    queued, refused = [], []
    for msg in got.get("messages") or []:
        if not isinstance(msg, dict) or not msg.get("id"):
            continue
        peer = get_peer(con, str(msg.get("from") or ""))
        ev = message_event(msg, peer, cfg)
        if peer:
            req = trigger._new_request(con, ev, "queued", "b2b peer")
            if req:
                queued.append(req["id"])
                trigger.log.info("new request %s  b2b from %s -> queued", req["id"],
                                 ev["requester_name"])
                if dispatch:
                    dispatch(req["id"])
        else:
            req = trigger._new_request(con, ev, "refused", "b2b: not a connected peer")
            if req:
                trigger._set_request(con, req["id"], finished_at=trigger.now_iso())
                refused.append(req["id"])
                trigger.log.info("new request %s  b2b from %s -> refused: not a peer",
                                 req["id"], ev["requester_id"])
    return {"ok": True, "received": len(queued) + len(refused), "queued": queued,
            "refused": refused}


def status_payload() -> dict[str, Any]:
    """The config, the peers and what was sent, for the page and the CLI."""
    con = db()
    counts = {r["status"]: r["n"] for r in con.execute(
        "SELECT status, count(*) AS n FROM trigger_request WHERE source=? GROUP BY status",
        (SOURCE,))}
    return {"ok": True, "config": config_summary(), "peers": list_peers(con, True),
            "sent": list_sent(con, 20), "received": counts}


# ---------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    """The command line. Prints JSON, like trigger.py."""
    ap = argparse.ArgumentParser(prog="b2b.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="the account, the peers, what was sent and received")
    p = sub.add_parser("config", help="change the relay account and the poll interval")
    p.add_argument("--server", default=None)
    p.add_argument("--user-id", default=None)
    p.add_argument("--token", default=None)
    p.add_argument("--interval", type=int, default=None, help="poll every N seconds")
    p.add_argument("--do", default=None, help="the run command for a received message")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--enable", action="store_true")
    g.add_argument("--disable", action="store_true")
    p = sub.add_parser("register", help="get a new account from the relay and store it")
    p.add_argument("--server", default="")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("peers", help="the brains this one talks to")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--add", default="", help="a peer's relay user id")
    g.add_argument("--remove", default="")
    p.add_argument("--name", default="")
    p = sub.add_parser("send", help="send a message to a peer")
    p.add_argument("--to", required=True, help="a peer's user id or name")
    p.add_argument("--text", required=True)
    p.add_argument("--request", default="")
    sub.add_parser("poll", help="one pass: fetch, log, and run what arrived, in this process")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        res = status_payload()
    elif args.cmd == "config":
        patch: dict[str, Any] = {k: v for k, v in (
            ("server_url", args.server), ("user_id", args.user_id), ("token", args.token),
            ("poll_seconds", args.interval), ("do", args.do)) if v is not None}
        if args.enable or args.disable:
            patch["enabled"] = bool(args.enable)
        res = save_config(patch) if patch else {"ok": True, "config": config_summary()}
    elif args.cmd == "register":
        res = register(args.server, args.force)
    elif args.cmd == "peers":
        con = db()
        if args.add:
            res = add_peer(con, args.add, args.name)
        elif args.remove:
            res = remove_peer(con, args.remove)
        else:
            res = {"ok": True, "peers": list_peers(con, True)}
    elif args.cmd == "send":
        res = send(args.to, args.text, args.request)
    else:
        cfg, ran = load_config(), []
        tcfg = trigger.load_config()
        res = poll(cfg, lambda rid: ran.append(trigger.run_request(rid, tcfg)))
        res["ran"] = ran
    trigger.out(res)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
