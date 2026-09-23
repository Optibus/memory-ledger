"""The trigger watchers, running inside the server (docs/trigger.md).

Everything that decides, logs and dispatches is in `scripts/trigger.py`, which
is stdlib. This module only owns what the server has and the engine may not:
the Slack Socket Mode websocket (`slack_sdk`), the Jira poll loop's thread, and
the pool the headless sessions run on — so several mentions run at once
without a watcher waiting on any of them.

Started from the app's lifespan when `state.TRIGGERS` is set, which `serve.py`
leaves off under `--no-trigger` and `--no-write`: a read-only server must not
be acting on its owner's behalf.
"""

from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import trigger

# While the socket is up every mention arrives live, so the Slack checkpoint
# can follow the clock. It trails by a margin, so a mention in flight at the
# moment of a crash is still inside the next catch-up window.
SLACK_HEARTBEAT_S = 30
SLACK_TRAIL_S = 120

_lock = threading.Lock()
# Serialises start / stop / restart: two saves from the page at once must not
# leave two sets of watchers running.
_life = threading.Lock()
# One event per start. A restart gives the new watchers a fresh one rather than
# clearing the old, so a loop from before the restart cannot miss its stop.
_stop = threading.Event()
_pool: ThreadPoolExecutor | None = None
_slack_client = None
STATE: dict = {"running": False, "reason": "not started", "started_at": "",
               "slack": {"state": "off"}, "jira": {"state": "off"}, "recovered": {}}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _note(source: str, **fields) -> None:
    with _lock:
        STATE[source] = {**STATE.get(source, {}), **fields, "at": _now()}


def _dispatch(request_id: str) -> None:
    if _pool is not None:
        _pool.submit(_run, request_id)


def _run(request_id: str) -> None:
    try:
        trigger.run_request(request_id)
    except Exception:
        traceback.print_exc()


def status() -> dict:
    with _lock:
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in STATE.items()}


def start(*, recovering: bool = True) -> None:
    """Non-blocking: the network calls happen on the watchers' own threads, so
    a slow Slack never holds up the server's start."""
    with _life:
        _start(recovering)


def _start(recovering: bool) -> None:
    global _pool, _stop
    cfg = trigger.load_config()
    STATE.update(slack={"state": "off"}, jira={"state": "off"})
    if not cfg.get("enabled") or cfg.get("_problem"):
        STATE.update(running=False, reason=cfg.get("_problem") or "triggers are switched off")
        return
    _stop = threading.Event()
    workers = max(1, min(16, int((cfg.get("claude") or {}).get("max_concurrent") or 3)))
    _pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="trigger-run")
    STATE.update(running=True, reason="", started_at=_now())
    if recovering:
        # Only when the process starts. On a restart the sessions still
        # running belong to this very process and are not orphans.
        STATE["recovered"] = trigger.recover(cfg, _dispatch)
    if trigger.source_enabled(cfg, "jira"):
        threading.Thread(target=_jira_loop, args=(cfg, _stop), daemon=True,
                         name="trigger-jira").start()
    if trigger.source_enabled(cfg, "slack"):
        threading.Thread(target=_slack_main, args=(cfg, _stop), daemon=True,
                         name="trigger-slack").start()


def stop() -> None:
    with _life:
        _stop_now()


def _stop_now() -> None:
    global _pool, _slack_client
    _stop.set()
    if _slack_client is not None:
        try:
            _slack_client.close()
        except Exception:
            pass
        _slack_client = None
    if _pool is not None:
        # Do not wait: a session may run for minutes, and it is a child that
        # outlives this process anyway. recover() marks it on the next start.
        _pool.shutdown(wait=False, cancel_futures=False)
        _pool = None
    STATE.update(running=False, reason="stopped")


def restart() -> dict:
    """Stop and start again on the config as it now is. What the page calls
    after saving, so a change applies without restarting the server."""
    with _life:
        _stop_now()
        _start(recovering=False)
    return status()


# ---------------------------------------------------------------- jira
def _jira_loop(cfg: dict, stop_event: threading.Event) -> None:
    every = max(15, int((cfg.get("jira") or {}).get("poll_seconds") or 60))
    jira = trigger.Jira(cfg)
    _note("jira", state="polling", every_s=every)
    while not stop_event.is_set():
        try:
            res = trigger.jira_poll(cfg, _dispatch, jira=jira)
            if res.get("ok"):
                _note("jira", state="polling", last_ok=_now(), last_error="",
                      last={k: res.get(k) for k in ("issues", "seen", "handled", "first_run")})
            else:
                _note("jira", state="misconfigured", last_error=res.get("error", ""))
        except Exception as e:
            _note("jira", state="erroring", last_error=str(e)[:300])
        stop_event.wait(every)


# ---------------------------------------------------------------- slack
def _slack_main(cfg: dict, stop_event: threading.Event) -> None:
    global _slack_client
    try:
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.response import SocketModeResponse
        from slack_sdk.web import WebClient
    except ImportError as e:
        _note("slack", state="unavailable", last_error=f"slack_sdk is not installed: {e}")
        return
    app_token = trigger.secret(cfg, "slack", "app_token")
    bot_token = trigger.secret(cfg, "slack", "bot_token")
    if not (app_token and bot_token):
        _note("slack", state="misconfigured", last_error="needs an app token and a bot token")
        return
    try:
        bot = trigger.slack_bot_user_id(cfg)
    except trigger.TriggerError as e:
        _note("slack", state="misconfigured", last_error=str(e))
        return
    notify = trigger.slack_notify(cfg)

    def on_mention(event: dict) -> None:
        try:
            ev = trigger.slack_event(event, event.get("channel", ""), cfg, bot_user_id=bot)
            res = trigger.handle(ev, cfg, _dispatch, notify)
            if ev:
                with trigger.db() as con:
                    trigger.set_checkpoint(con, "slack", ev["meta"]["ts"])
            _note("slack", last_event=_now(), last_action=res.get("action"))
        except Exception as e:
            _note("slack", last_error=str(e)[:300])

    def listener(client, req) -> None:
        if req.type != "events_api":
            return
        # Ack first: Slack redelivers what is not acked within 3s, and the
        # permalink lookup alone can take longer than that.
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        event = (req.payload or {}).get("event") or {}
        if event.get("type") == "app_mention":
            threading.Thread(target=on_mention, args=(event,), daemon=True).start()

    client = SocketModeClient(app_token=app_token, web_client=WebClient(token=bot_token))
    client.socket_mode_request_listeners.append(listener)
    _slack_client = client
    _note("slack", state="connecting", bot_user_id=bot)
    try:
        client.connect()
    except Exception as e:
        _note("slack", state="erroring", last_error=str(e)[:300])
    was_connected = False
    while not stop_event.is_set():
        up = False
        try:
            up = client.is_connected()
        except Exception:
            pass
        if up and not was_connected:
            # (Re)connected: whatever arrived while we were away is in history.
            _note("slack", state="connected", connected_at=_now())
            try:
                res = trigger.slack_catch_up(cfg, _dispatch)
                _note("slack", last_catch_up={k: res.get(k) for k in
                                              ("channels", "seen", "handled", "first_run")})
            except Exception as e:
                _note("slack", last_error=f"catch-up: {e}"[:300])
        elif up:
            with trigger.db() as con:
                trigger.set_checkpoint(con, "slack", f"{time.time() - SLACK_TRAIL_S:.6f}")
        elif was_connected:
            _note("slack", state="reconnecting")
        was_connected = up
        stop_event.wait(SLACK_HEARTBEAT_S if up else 5)
    try:
        client.close()
    except Exception:
        pass
