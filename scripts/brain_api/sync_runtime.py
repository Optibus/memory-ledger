"""Two-way sync with the neo4j master for as long as the server runs.

Started from the app's lifespan when `state.SYNC` is set, which `serve.py` does
unless the server is read-only. Every `SYNC_SECONDS` (env
`$MEMORY_LEDGER_SYNC_SECONDS`, default 60) it pulls what other writers put on
the master and pushes what this one did (`brain_sync.sync_now`). A brain with no master
configured makes the loop a no-op; a master that is down just waits for the
next tick — the outbox keeps every local change until it is back.
"""
from __future__ import annotations

import logging
import threading

import brain_sync
import graph

log = logging.getLogger("memory-ledger.sync")

_stop = threading.Event()
_thread: threading.Thread | None = None
STATE: dict = {"running": False, "last": None}


def _interval() -> float:
    try:
        return max(10.0, float(graph._env("SYNC_SECONDS", "60")))
    except ValueError:
        return 60.0


def _loop() -> None:
    while not _stop.is_set():
        con = None
        try:
            con = graph.connect()
            res = brain_sync.sync_now(con)
            STATE["last"] = {"at": graph.now(), **res}
            if not res.get("ok") and res.get("error") != "neo4j not configured":
                log.warning("sync failed: %s", res.get("error"))
        except Exception as e:           # never let one bad tick end the loop
            log.warning("sync crashed: %s", e)
        finally:
            if con is not None:
                con.close()
        _stop.wait(_interval())


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="brain-sync")
    _thread.start()
    STATE["running"] = True


def stop() -> None:
    _stop.set()
    STATE["running"] = False
