#!/usr/bin/env python3
"""Slash commands of the Agents tab (scripts/agent_commands.py), against a THROWAWAY ~/.claude.

    python3 tests/test_agent_commands.py

Nothing real is read or written: CLAUDE_CONFIG_DIR points at a temp directory. Stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(tempfile.mkdtemp(prefix="second-brain-commands-"))
os.environ["CLAUDE_CONFIG_DIR"] = str(HOME)
sys.path.insert(0, str(ROOT / "scripts"))

import agent_commands as ac  # noqa: E402

FAILS: list[str] = []


def ck(name: str, ok: bool) -> None:
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def plugin(name: str, key: str) -> Path:
    root = HOME / "plugins" / "cache" / name
    put(root / "skills" / "send-note" / "SKILL.md",
        "---\nname: send-note\ndescription: >-\n  Send a note\n  to someone.\n---\nbody\n")
    put(root / "skills" / "recall" / "SKILL.md", "---\nname: recall\ndescription: Look things up\n---\n")
    put(root / "commands" / "recall.md", "---\ndescription: Look things up (command)\n---\n")
    return root


a, b = plugin("alpha", "alpha@m"), plugin("beta", "beta@m")
put(HOME / "plugins" / "installed_plugins.json", json.dumps({"plugins": {
    "alpha@m": [{"scope": "user", "installPath": str(a), "version": "1.0"}],
    "beta@m": [{"scope": "user", "installPath": str(b), "version": "2.0"}],
    "gone@m": []}}))
put(HOME / "settings.json", json.dumps({"theme": "dark", "enabledPlugins": {"alpha@m": True, "beta@m": False}}))
put(HOME / "skills" / "mine" / "SKILL.md", "---\nname: mine\ndescription: my own\n---\n")

fm = ac.frontmatter(a / "skills" / "send-note" / "SKILL.md")
ck("folded description is joined", fm["description"] == "Send a note to someone.")
ck("no frontmatter gives nothing", ac.frontmatter(HOME / "missing.md") == {})

p = ac.payload()
names = [c["name"] for c in p["commands"]]
ck("enabled plugin's commands are namespaced", "alpha:send-note" in names and "alpha:recall" in names)
ck("disabled plugin's commands are hidden", not any(n.startswith("beta:") for n in names))
ck("user skill is unnamespaced", "mine" in names)
ck("skill and command of one name are one entry", names.count("alpha:recall") == 1)
ck("every installed plugin is listed, empty installs skipped",
   [x["key"] for x in p["plugins"]] == ["alpha@m", "beta@m"])
ck("enabled flag follows settings", [x["enabled"] for x in p["plugins"]] == [True, False])
ck("writes is guessed from the name", {c["name"]: c["writes"] for c in p["commands"]}
   == {"alpha:recall": False, "alpha:send-note": True, "mine": False})

res = ac.set_plugin("beta@m", True)
saved = json.loads((HOME / "settings.json").read_text())
ck("toggle on enables the plugin", res["ok"] and saved["enabledPlugins"]["beta@m"] is True)
ck("toggle leaves other keys alone", saved["theme"] == "dark" and saved["enabledPlugins"]["alpha@m"] is True)
ck("toggled plugin's commands now appear", any(c["name"].startswith("beta:") for c in res["commands"]))
ck("toggle off disables it", ac.set_plugin("beta@m", False)["ok"]
   and json.loads((HOME / "settings.json").read_text())["enabledPlugins"]["beta@m"] is False)
ck("unknown plugin is refused", ac.set_plugin("nope@m", True)["ok"] is False)

put(HOME / "settings.json", "{not json")
ck("unreadable settings are left untouched", ac.set_plugin("alpha@m", False)["ok"] is False
   and (HOME / "settings.json").read_text() == "{not json")

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
raise SystemExit(1 if FAILS else 0)
