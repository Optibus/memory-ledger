#!/usr/bin/env python3
"""Deterministic capture noise filter — pure regex, no model, no DB.

Decides whether a raw prompt is worth a `capture` node. Routine turns
("continue", "ok", a bare slash command, a pasted system-reminder, a JSON blob,
an image-only paste, a short fragment with no verb) are noise; anything with an
error string, a decision word, a URL, a file path or a config/git change is
signal and always kept.

Shared by `hooks/_capture.py` (before the write) and `scripts/decay.py` (to spot
open tasks whose title is really a pasted prompt). Idea ported from
claude-mem-lite's deterministic tier-1 filter: code decides, not a model.

    classify(text) -> (keep: bool, reason: str)

`reason` is a short stable key. For a kept prompt it names the signal that
saved it ("error", "decision", "url", "path", "config"), or "content" when
nothing special fired. For a dropped one it names the rule.
"""

from __future__ import annotations

import json
import re

DEFAULT_MIN_CHARS = 40

# --- structural noise: never typed by the person -------------------------------
_SYSTEM_TAGS = re.compile(
    r"^\s*<(?:system-reminder|task-notification|command-name|command-message|"
    r"command-args|local-command-stdout|local-command-stderr|"
    r"user-prompt-submit-hook|bash-input|bash-stdout|bash-stderr)\b", re.I)
_IMAGE_REF = re.compile(r"\[Image(?:\s*#\d+|:[^\]]*)?\]", re.I)
_SLASH_ONLY = re.compile(r"^/[\w:.-]+(?:\s+\S.{0,19})?$")

# --- bare acknowledgements ---------------------------------------------------
_ACK_WORDS = {
    "continue", "continua", "continuar", "ok", "okay", "okey", "k", "kk",
    "yes", "y", "yep", "yeah", "yup", "sure", "go", "go on", "go ahead", "proceed",
    "next", "done", "thanks", "thank", "you", "thx", "ty", "please", "pls",
    "do", "it", "sim", "vai", "pode", "certo", "beleza", "obrigada", "obrigado",
    "isso", "fine", "good", "great", "cool", "nice", "right", "agreed", "yes,",
    "and", "then", "now", "so", "that", "s", "works", "lgtm",
}

# --- signals that always keep a prompt -----------------------------------------
_ERROR = re.compile(
    r"(?i)\b(?:error|exception|traceback|stack ?trace|failed|failure|fatal|panic|"
    r"segfault|timeout|timed out|denied|refused|not found|undefined|cannot|can't "
    r"find|unable to|erro|falhou)\b|(?-i:\b[A-Z][A-Za-z]*(?:Error|Exception)\b)|"
    r"(?-i:\b(?:ENOENT|EACCES|ECONN[A-Z]+|ETIMEDOUT|EADDRINUSE|EPERM|EEXIST)\b)|\b[45]\d\d\s+(?:error|bad|not|internal|forbidden|unauthorized)\b|"
    r"\bexit (?:code|status)\s*[1-9]")
_DECISION = re.compile(
    r"(?i)\b(?:decided|decision|we(?:'ll| will) go with|let'?s go with|always|never|"
    r"from now on|going forward|from here on|rule:|prefer|decidimos|decidi|"
    r"sempre|nunca|a partir de agora)\b")
_URL = re.compile(r"(?i)\bhttps?://\S+|\bwww\.\S+\.\S+")
_PATH = re.compile(
    r"(?:(?<![\w/])(?:~|\.{1,2})?/(?:[\w.@+-]+/)+[\w.@+-]*)|"      # a/b/c, ~/x/y, ./x/y
    r"\b[\w.-]+\.(?:py|js|jsx|ts|tsx|md|json|ya?ml|toml|ini|cfg|sh|sql|html|css|"
    r"go|rs|java|rb|txt|lock|env|db)\b")
_CONFIG = re.compile(
    r"(?i)\b(?:git (?:commit|push|pull|merge|rebase|checkout|branch|reset|stash|revert|cherry-pick)|"
    r"pull request|\bPR\b|branch|commit|merge|rebase|settings?\.json|config(?:uration)?|"
    r"env(?:ironment)? var(?:iable)?s?|hooks?\.json|\.env|npm install|pip install|"
    r"dockerfile|migration|schema|deploy|release|version bump)\b")
_TICKET = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")

# --- "has a verb": a small closed list, English + pt-BR. Deterministic on purpose.
_VERBS = set("""
add fix build make run test check read write create update delete remove show list
find search open close change rename move copy merge push pull commit deploy install
explain tell give get set use try look see help need want should could would can
let review summarize summarise draft send ask remind remember save store capture
drop stop start restart enable disable turn rewrite refactor implement ship plan
compare debug trace explore design document verify validate generate convert parse
adiciona corrige cria roda testa lê escreve mostra lista busca abre fecha muda
move envia explica diz usa tenta olha ve veja preciso quero devo pode faz fazer
is are was were do does did has have why how what when where which who
""".split())
_WORD = re.compile(r"[A-Za-zÀ-ÿ']+")


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def has_verb(text: str) -> bool:
    return any(w in _VERBS for w in _words(text))


def is_json_blob(text: str) -> bool:
    t = (text or "").strip()
    if not t or t[0] not in "{[" or t[-1] not in "}]":
        return False
    try:
        json.loads(t)
        return True
    except Exception:
        return False


def is_bare_ack(text: str) -> bool:
    words = _words(text)
    return 0 < len(words) <= 4 and all(w in _ACK_WORDS for w in words)


def image_only(text: str) -> bool:
    if not _IMAGE_REF.search(text or ""):
        return False
    return len(_IMAGE_REF.sub("", text).strip(" \t\r\n.,;:-")) < 8


def signal(text: str) -> str:
    """The reason a prompt must be kept, or ''."""
    t = text or ""
    for name, rx in (("error", _ERROR), ("decision", _DECISION), ("url", _URL),
                     ("path", _PATH), ("config", _CONFIG), ("ticket", _TICKET)):
        if rx.search(t):
            return name
    return ""


def classify(text: str, min_chars: int = DEFAULT_MIN_CHARS) -> tuple[bool, str]:
    """(keep, reason). Order matters: structural noise first (a pasted
    system-reminder can quote an error and is still not the person's words),
    then the keep-signals, then the routine-prompt rules."""
    t = (text or "").strip()
    if not t:
        return False, "empty"
    if _SYSTEM_TAGS.match(t):
        return False, "system-tag"
    if is_json_blob(t):
        return False, "json-blob"
    if image_only(t):
        return False, "image-only"
    if _SLASH_ONLY.match(t):
        return False, "slash-command"
    sig = signal(t)
    if sig:
        return True, sig
    if is_bare_ack(t):
        return False, "ack"
    if len(t) < min_chars and not has_verb(t):
        return False, "short-no-verb"
    return True, "content"


# Opening words of a prompt typed to the assistant, not a to-do written for later.
_PROMPTY_START = re.compile(
    r"(?i)^\s*(?:hey|hi|hello|ok(?:ay)?|so|now|also|and|but|then|please|pls|"
    r"can you|could you|would you|will you|why|how|what|where|when|is |are |do |does |"
    r"i want|i need you|i'?d like|let'?s|show me|tell me|give me|look at|check if)\b")


def looks_like_raw_prompt(title: str) -> bool:
    """A task title that is really a pasted/truncated prompt, not a task.

    Real to-dos are short imperative statements. A prompt-shaped title is one
    that was cut off ("…"), opens like a message to the assistant, is a question,
    carries pasted markup, or is run-on prose."""
    t = " ".join((title or "").split())
    if not t:
        return False
    if t.endswith("…") or t.endswith("..."):
        return True
    if _SYSTEM_TAGS.match(t) or _IMAGE_REF.search(t) or is_json_blob(t):
        return True
    if t.endswith("?") or _PROMPTY_START.match(t):
        return True
    return len(t) > 90
