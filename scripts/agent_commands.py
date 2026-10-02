#!/usr/bin/env python3
"""The slash commands the Agents tab offers: skills and plugin commands, and the plugins.

Read from disk, no model call (docs/agents.md). A command is a `SKILL.md` or a
`commands/*.md` file with a `name` and a `description` in its frontmatter. Where
they come from:

- plugins listed in `~/.claude/plugins/installed_plugins.json`, namespaced
  `plugin:name` (a plugin's own name, before the `@`);
- the user's `~/.claude/skills`, and the project's `.claude/skills`, unnamespaced.

A plugin is on when `~/.claude/settings.json` has `enabledPlugins[key] = true`.
Only the commands of enabled plugins are offered; every installed plugin is
listed, so it can be switched on. Toggling edits that one key and nothing else.

`writes` is a guess from the name and description, not a declaration: it greys
the command out in a read-only conversation, where a skill that sends or
changes things would be refused its tools anyway.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

WRITE_WORDS = re.compile(
    r"\b(send|post|publish|deploy|merge|push|commit|squash|delete|remove|create|add|"
    r"complete|write|edit|update|remember|ingest|delegate|apply|schedule|remind|"
    r"transition|approve|install|init|reindex|capture)\b", re.I)


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _read_json(p: Path) -> dict[str, Any]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def frontmatter(path: Path) -> dict[str, str]:
    """The `key: value` pairs of a markdown file's leading `---` block (folded values joined)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    key = ""
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if m and not line.startswith(" "):
            key, val = m.group(1), m.group(2).strip()
            out[key] = "" if val in (">-", ">", "|", "|-") else val.strip("'\"")
        elif key and line.strip():
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def _entry(name: str, plugin: str, kind: str, fm: dict[str, str]) -> dict[str, Any]:
    desc = " ".join((fm.get("description") or "").split())
    return {"name": f"{plugin}:{name}" if plugin else name, "plugin": plugin, "kind": kind,
            "description": desc[:300], "writes": bool(WRITE_WORDS.search(f"{name} {desc[:120]}"))}


def _scan(root: Path, plugin: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for md in sorted(root.glob("skills/*/SKILL.md")):
        fm = frontmatter(md)
        found.append(_entry(fm.get("name") or md.parent.name, plugin, "skill", fm))
    for md in sorted(root.glob("commands/*.md")):
        fm = frontmatter(md)
        found.append(_entry(md.stem, plugin, "command", fm))
    return found


def _project_skills(project: Path | None) -> list[dict[str, Any]]:
    out = _scan(claude_home(), "")
    if project:
        seen = {c["name"] for c in out}
        out += [c for c in _scan(project / ".claude", "") if c["name"] not in seen]
    return out


def plugins(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every installed plugin: `{key, name, version, enabled, path, commands}`."""
    home = claude_home()
    enabled = (settings if settings is not None else _read_json(home / "settings.json")
               ).get("enabledPlugins") or {}
    installed = (_read_json(home / "plugins" / "installed_plugins.json").get("plugins") or {})
    out = []
    for key, installs in sorted(installed.items()):
        if not installs:
            continue
        # A user-scope install wins over a project-scoped one.
        inst = next((i for i in installs if i.get("scope") == "user"), installs[0])
        path = Path(inst.get("installPath") or "")
        out.append({"key": key, "name": key.split("@")[0], "version": inst.get("version", ""),
                    "enabled": enabled.get(key) is True, "path": str(path),
                    "commands": len(_scan(path, "x")) if path.is_dir() else 0})
    return out


def payload(project: Path | None = None) -> dict[str, Any]:
    """Everything the picker needs: the commands of enabled plugins and of the user, and the plugins."""
    pls = plugins()
    commands = _project_skills(project)
    for p in pls:
        if p["enabled"] and Path(p["path"]).is_dir():
            commands += _scan(Path(p["path"]), p["name"])
    # A plugin often ships a skill and a command of one name; they are one entry.
    commands = list({c["name"]: c for c in commands}.values())
    commands.sort(key=lambda c: c["name"].lower())
    return {"ok": True, "commands": commands, "plugins": pls}


def set_plugin(key: str, enabled: bool) -> dict[str, Any]:
    """Switch one installed plugin on or off in `~/.claude/settings.json`; nothing else in it changes."""
    if not any(p["key"] == key for p in plugins()):
        return {"ok": False, "error": f"no installed plugin {key}"}
    path = claude_home() / "settings.json"
    data = _read_json(path)
    if path.exists() and not data:
        return {"ok": False, "error": f"cannot read {path}; left untouched"}
    ep = data.get("enabledPlugins")
    data["enabledPlugins"] = ep = ep if isinstance(ep, dict) else {}
    ep[key] = bool(enabled)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return {**payload(), "key": key, "enabled": bool(enabled)}


def main(argv: list[str] | None = None) -> int:
    args = (argv if argv is not None else sys.argv[1:]) or ["list"]
    if args[0] == "toggle" and len(args) == 3:
        res = set_plugin(args[1], args[2] == "on")
    else:
        res = payload(Path.cwd())
    print(json.dumps(res, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
