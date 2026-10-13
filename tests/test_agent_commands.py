"""Slash commands of the Agents tab (scripts/agent_commands.py), against a THROWAWAY ~/.claude.

    python -m pytest tests/test_agent_commands.py

Nothing real is read or written: CLAUDE_CONFIG_DIR points at a temp directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent_commands as ac

UTF8 = "utf-8"
ALPHA, BETA, GONE = "alpha@m", "beta@m", "gone@m"
SETTINGS = "settings.json"
ENABLED_PLUGINS = "enabledPlugins"
BROKEN_SETTINGS = "{not json"


def put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=UTF8)


def plugin(home: Path, name: str) -> Path:
    root = home / "plugins" / "cache" / name
    put(root / "skills" / "send-note" / "SKILL.md",
        "---\nname: send-note\ndescription: >-\n  Send a note\n  to someone.\n---\nbody\n")
    put(root / "skills" / "recall" / "SKILL.md",
        "---\nname: recall\ndescription: Look things up\n---\n")
    put(root / "commands" / "recall.md", "---\ndescription: Look things up (command)\n---\n")
    return root


def saved_settings(home: Path) -> dict:
    return json.loads((home / SETTINGS).read_text(encoding=UTF8))


@pytest.fixture
def claude_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway ~/.claude with two plugins (beta disabled), one empty install
    and one user skill."""
    home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    alpha, beta = plugin(home, "alpha"), plugin(home, "beta")
    put(home / "plugins" / "installed_plugins.json", json.dumps({"plugins": {
        ALPHA: [{"scope": "user", "installPath": str(alpha), "version": "1.0"}],
        BETA: [{"scope": "user", "installPath": str(beta), "version": "2.0"}],
        GONE: []}}))
    put(home / SETTINGS, json.dumps(
        {"theme": "dark", ENABLED_PLUGINS: {ALPHA: True, BETA: False}}))
    put(home / "skills" / "mine" / "SKILL.md", "---\nname: mine\ndescription: my own\n---\n")
    return home


@pytest.fixture
def payload(claude_home: Path) -> dict:
    return ac.payload()


def test_folded_description_is_joined(claude_home: Path) -> None:
    fm = ac.frontmatter(claude_home / "plugins" / "cache" / "alpha" / "skills"
                        / "send-note" / "SKILL.md")
    assert fm["description"] == "Send a note to someone."


def test_no_frontmatter_gives_nothing(claude_home: Path) -> None:
    assert ac.frontmatter(claude_home / "missing.md") == {}


def test_enabled_plugin_commands_are_namespaced(payload: dict) -> None:
    names = [c["name"] for c in payload["commands"]]
    assert "alpha:send-note" in names
    assert "alpha:recall" in names


def test_disabled_plugin_commands_are_hidden(payload: dict) -> None:
    assert not any(c["name"].startswith("beta:") for c in payload["commands"])


def test_user_skill_is_unnamespaced(payload: dict) -> None:
    assert "mine" in [c["name"] for c in payload["commands"]]


def test_skill_and_command_of_one_name_are_one_entry(payload: dict) -> None:
    assert [c["name"] for c in payload["commands"]].count("alpha:recall") == 1


def test_every_installed_plugin_is_listed_and_empty_installs_skipped(payload: dict) -> None:
    assert [x["key"] for x in payload["plugins"]] == [ALPHA, BETA]


def test_enabled_flag_follows_settings(payload: dict) -> None:
    assert [x["enabled"] for x in payload["plugins"]] == [True, False]


def test_writes_is_guessed_from_the_name(payload: dict) -> None:
    assert {c["name"]: c["writes"] for c in payload["commands"]} == {
        "alpha:recall": False, "alpha:send-note": True, "mine": False}


def test_toggle_on_enables_the_plugin(claude_home: Path) -> None:
    res = ac.set_plugin(BETA, True)
    assert res["ok"]
    assert saved_settings(claude_home)[ENABLED_PLUGINS][BETA] is True


def test_toggle_leaves_other_keys_alone(claude_home: Path) -> None:
    ac.set_plugin(BETA, True)
    saved = saved_settings(claude_home)
    assert saved["theme"] == "dark"
    assert saved[ENABLED_PLUGINS][ALPHA] is True


def test_toggled_plugins_commands_now_appear(claude_home: Path) -> None:
    res = ac.set_plugin(BETA, True)
    assert any(c["name"].startswith("beta:") for c in res["commands"])


def test_toggle_off_disables_it(claude_home: Path) -> None:
    ac.set_plugin(BETA, True)
    assert ac.set_plugin(BETA, False)["ok"]
    assert saved_settings(claude_home)[ENABLED_PLUGINS][BETA] is False


def test_unknown_plugin_is_refused(claude_home: Path) -> None:
    assert ac.set_plugin("nope@m", True)["ok"] is False


def test_unreadable_settings_are_left_untouched(claude_home: Path) -> None:
    put(claude_home / SETTINGS, BROKEN_SETTINGS)
    assert ac.set_plugin(ALPHA, False)["ok"] is False
    assert (claude_home / SETTINGS).read_text(encoding=UTF8) == BROKEN_SETTINGS
