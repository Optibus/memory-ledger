#!/usr/bin/env python3
"""Ledger Agent: a floating box in front of whatever you are doing, that hands
each message to Claude in a clean session (docs/agents.md, "Ledger Agent").

The box is a tiny AppKit app (agent_panel.swift), compiled on first use into
`<home>/LedgerAgent.app`. This process runs it and does the work: every message
goes through the same path as the Agents tab — `agents.send` stores it, one
stateless `claude -p` call answers it, the exchange is written to the graph —
in one conversation named "Ledger Agent" that carries no earlier turns. So the
Agents tab shows everything the box was asked, and recall finds it later.

Claude runs in the folder the box was opened from (or `--cwd`), with every
tool on unless `--read-only`.

    python3 scripts/agent_panel.py                 # the box, Claude runs here
    python3 scripts/agent_panel.py --cwd ~/dev/x   # Claude runs in ~/dev/x
    python3 scripts/agent_panel.py --read-only     # look things up, change nothing
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents  # noqa: E402
import graph  # noqa: E402

HERE = Path(__file__).resolve().parent
SRC = HERE / "agent_panel.swift"
ICON = HERE.parent / "frontend" / "public" / "favicon.svg"
STATE_NAME = "agent-panel.json"
TITLE = "Ledger Agent"
POLL_S = 0.4
EVENT_TEXT_MAX = 160
PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.memory-ledger.agent</string>
<key>CFBundleName</key><string>LedgerAgent</string>
<key>CFBundleExecutable</key><string>LedgerAgent</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSUIElement</key><true/>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
"""


def _say(*a: Any) -> None:
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- the app
def app_dir() -> Path:
    return graph.home_dir() / "LedgerAgent.app"


def panel_binary() -> Path | None:
    """The compiled box, rebuilt when the Swift source is newer than it."""
    app = app_dir()
    exe = app / "Contents" / "MacOS" / "LedgerAgent"
    if exe.exists() and exe.stat().st_mtime >= SRC.stat().st_mtime:
        return exe
    if not shutil.which("swiftc"):
        return None
    _say("Building the Ledger Agent box (first run, ~30s)…")
    exe.parent.mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Info.plist").write_text(PLIST, encoding="utf-8")
    build = subprocess.run(["swiftc", "-O", str(SRC), "-o", str(exe)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    if build.returncode != 0:
        _say("Could not build the box:", (build.stderr or "").strip()[-800:])
        return None
    subprocess.run(["codesign", "-s", "-", "-f", str(app)], capture_output=True)
    return exe


# ---------------------------------------------------------------- the conversation
def conversation(con, full_tools: bool, model: str = "") -> str:
    """The one "Ledger Agent" conversation, made on first use and remembered in
    `<home>/agent-panel.json`. It carries no earlier turns: every message is a
    clean session. Tools and model follow this run's flags."""
    path = graph.home_dir() / STATE_NAME
    try:
        cid = str(json.loads(path.read_text(encoding="utf-8")).get("conversation") or "")
    except (OSError, ValueError):
        cid = ""
    changes: dict[str, Any] = {"full_tools": full_tools, "context_turns": 0}
    if model:
        changes["model"] = model
    if cid and agents.get_conv(con, cid):
        res = agents.save_conversation(con, cid, **changes)
    else:
        res = agents.save_conversation(con, "", title=TITLE, **changes)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "could not open the Ledger Agent conversation")
    cid = res["conversation"]["id"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"conversation": cid}), encoding="utf-8")
    return cid


def event_line(ev: dict[str, Any]) -> str:
    """One line for something the agent did, or "" for what is not worth a line
    (thinking, the text it says on the way to its answer)."""
    kind = ev.get("kind")
    if kind in ("thinking", "text"):
        return ""
    name = str(ev.get("name") or ev.get("tool") or kind or "tool")
    detail = ""
    try:
        inp = json.loads(ev.get("input") or "{}")
    except ValueError:
        inp = {}
    if isinstance(inp, dict):
        for k in ("command", "file_path", "pattern", "path", "query", "url", "description", "prompt"):
            if inp.get(k):
                detail = str(inp[k])
                break
    elif ev.get("input"):
        detail = str(ev["input"])
    if kind == "skill":
        name = f"skill {name}"
    elif kind == "agent":
        name = f"agent {name}"
    line = f"{name} · {' '.join(detail.split())}" if detail else name
    if ev.get("error"):
        line += "  ✗"
    return line if len(line) <= EVENT_TEXT_MAX else line[:EVENT_TEXT_MAX - 1] + "…"


def meta_line(row: dict[str, Any], seconds: float) -> str:
    bits = []
    if row.get("cost_usd") is not None:
        bits.append(f"{'~' if row.get('cost_estimated') else ''}${float(row['cost_usd']):.2f}")
    bits.append(f"{seconds:.0f}s")
    if row.get("model"):
        bits.append(str(row["model"]))
    return " · ".join(bits)


def ask(conv_id: str, text: str, full_tools: bool, emit: Callable[[dict[str, Any]], None],
        on_reply: Callable[[str], None] = lambda _id: None) -> dict[str, Any]:
    """Send one message and run its reply here, telling `emit` about each step
    and the end. Returns the reply row."""
    con = agents.db()
    started = time.monotonic()
    res = agents.send(con, text, conv_id, full_tools=full_tools, context_turns=0, start=False)
    if not res.get("ok"):
        emit({"t": "error", "text": str(res.get("error") or "could not send")})
        return {}
    reply_id = res["reply_id"]
    on_reply(reply_id)
    worker = threading.Thread(target=agents.run_reply, args=(reply_id,), daemon=True)
    worker.start()
    seen = 0
    while True:
        alive = worker.is_alive()
        row = dict(con.execute("SELECT * FROM agent_msg WHERE id=?", (reply_id,)).fetchone())
        events = json.loads(row.get("events") or "[]")
        for ev in events[seen:]:
            line = event_line(ev)
            if line:
                emit({"t": "event", "text": line})
        seen = len(events)
        if not alive or row["status"] not in agents.PENDING:
            if alive:
                worker.join(timeout=5)
            break
        time.sleep(POLL_S)
    row = dict(con.execute("SELECT * FROM agent_msg WHERE id=?", (reply_id,)).fetchone())
    if row["status"] in ("done", "question"):
        emit({"t": "done", "text": row["text"] or "(no answer)",
              "meta": meta_line(row, time.monotonic() - started)})
    else:
        emit({"t": "error", "text": row.get("error") or f"the reply ended {row['status']}"})
    return row


# ---------------------------------------------------------------- the loop
def run(cwd: Path, full_tools: bool, model: str = "") -> int:
    if sys.platform != "darwin":
        _say("The Ledger Agent box needs macOS. Use the Agents tab instead.")
        return 1
    exe = panel_binary()
    if exe is None:
        _say("No box (needs the Xcode command line tools: xcode-select --install).")
        return 1
    agents.CLAUDE_CWD = cwd
    agents.SURFACE = "the Ledger Agent, a small floating box over the owner's work"
    con = agents.db()
    conv_id = conversation(con, full_tools, model)
    home = str(Path.home())
    where = str(cwd).replace(home, "~", 1) if str(cwd).startswith(home) else str(cwd)
    subtitle = f"{where} · {'tools on' if full_tools else 'read-only'}"
    proc = subprocess.Popen([str(exe), str(ICON) if ICON.exists() else "", subtitle],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", bufsize=1)
    lock = threading.Lock()
    running: dict[str, str] = {}

    def emit(msg: dict[str, Any]) -> None:
        with lock:
            try:
                proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")   # type: ignore[union-attr]
                proc.stdin.flush()                                              # type: ignore[union-attr]
            except (BrokenPipeError, ValueError, OSError):
                pass

    def turn(text: str) -> None:
        try:
            ask(conv_id, text, full_tools, emit, on_reply=lambda rid: running.update(id=rid))
        except Exception as e:   # noqa: BLE001 — the box is waiting for an end, always give one
            emit({"t": "error", "text": f"{type(e).__name__}: {e}"})
        finally:
            running.pop("id", None)

    asked = 0
    try:
        for line in proc.stdout:   # type: ignore[union-attr]
            verb, _, rest = line.rstrip("\n").partition("\t")
            if verb == "Ask":
                try:
                    text = json.loads(rest)
                except ValueError:
                    continue
                asked += 1
                threading.Thread(target=turn, args=(str(text),), daemon=True).start()
            elif verb == "Cancel":
                if running.get("id"):
                    agents.cancel(agents.db(), running["id"])
            elif verb == "Done":
                break
    except KeyboardInterrupt:
        pass
    finally:
        if running.get("id"):          # closing the box stops what it started
            agents.cancel(agents.db(), running["id"])
        if proc.poll() is None:
            try:
                proc.stdin.close()     # type: ignore[union-attr]
            except OSError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.terminate()
    _say(f"Ledger Agent: {asked} message(s). They are in the Agents tab under \"{TITLE}\".")
    return 0


def launch(cwd: str = "") -> dict[str, Any]:
    """Open the box from the brain UI, detached from the server: it lives on when
    the tab closes, and the server never waits on it."""
    if sys.platform != "darwin":
        return {"ok": False, "error": "the Ledger Agent box needs macOS"}
    folder = Path(cwd).expanduser() if cwd else HERE.parent
    if not folder.is_dir():
        return {"ok": False, "error": f"no folder {folder}"}
    if panel_binary() is None:
        return {"ok": False, "error": "could not build the box (needs xcode-select --install)"}
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--cwd", str(folder)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, cwd=str(folder))
    return {"ok": True, "cwd": str(folder)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cwd", default="", help="the folder Claude runs in (default: this one)")
    ap.add_argument("--read-only", action="store_true", help="look things up, change nothing")
    ap.add_argument("--model", default="", help="the Claude model (default: the connector's)")
    a = ap.parse_args(argv)
    cwd = Path(a.cwd).expanduser().resolve() if a.cwd else Path.cwd()
    if not cwd.is_dir():
        _say(f"No folder {cwd}")
        return 1
    return run(cwd, not a.read_only, a.model)


if __name__ == "__main__":
    sys.exit(main())
