"""Every text-mode read, write and subprocess names its encoding.

    python -m pytest tests/test_encoding.py

Python's default text encoding is the locale's. On macOS and Linux that is
UTF-8 and nothing shows; on Windows it is cp1252 (or another ANSI code page),
so a UTF-8 file or a `claude` reply read without an explicit encoding comes out
as mojibake, and a byte cp1252 has no character for (0x81, 0x8d, ...) raises
"'charmap' codec can't decode". This suite walks the AST of every Python file
the brain ships and fails on any of:

- a call with `text=True` or `universal_newlines=True` and no `encoding=`;
- a builtin `open()` in text mode with no `encoding=`;
- `.read_text()` / `.write_text()` with no `encoding=`.

A subprocess should go through `_textio.run_text()` (or pass
`encoding="utf-8", errors="replace"` itself): a child that prints a byte that
is not UTF-8 must degrade to U+FFFD, never crash the reader.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import _textio

ROOT = Path(__file__).resolve().parent.parent
UTF8 = "utf-8"
SOURCE_DIRS = ("scripts", "hooks", "services", "tests")
SKIPPED_PARTS = ("node_modules", "__pycache__")
PRE_COMMIT_HOOK = ROOT / ".githooks" / "pre-commit"

# what the checker must flag, and what it must let through
PROBE_SOURCE = (
    'subprocess.run(["x"], text=True)\n'
    'open("f")\n'
    'open("f", "rb")\n'
    'Path("f").read_text()\n'
    'Path("f").write_text("x", encoding="utf-8")\n'
    'subprocess.run(["x"], text=True, encoding="utf-8")\n'
)
PROBE_FLAGGED = 3


def sources() -> list[Path]:
    files = [p for d in SOURCE_DIRS for p in (ROOT / d).rglob("*.py")
             if not any(part in p.parts for part in SKIPPED_PARTS)]
    if PRE_COMMIT_HOOK.is_file():
        files.append(PRE_COMMIT_HOOK)
    return sorted(files)


def _kw(call: ast.Call, name: str) -> ast.keyword | None:
    return next((k for k in call.keywords if k.arg == name), None)


def _true(k: ast.keyword | None) -> bool:
    return k is not None and isinstance(k.value, ast.Constant) and k.value.value is True


def _passes_kwargs(call: ast.Call) -> bool:
    # open(**opts) may carry the encoding; text=True next to **kw is still flagged
    return any(k.arg is None for k in call.keywords)


def _text_mode_open(call: ast.Call) -> bool:
    mode = call.args[1] if len(call.args) > 1 else (_kw(call, "mode").value if _kw(call, "mode") else None)
    if mode is None:
        return True
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return "b" not in mode.value
    return False  # a computed mode: cannot tell, do not guess


def findings(path: Path, label_root: Path = ROOT) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding=UTF8), str(path))
    except FileNotFoundError:  # a scratch file that went away since the walk
        return []
    except SyntaxError as e:
        return [f"{path.relative_to(label_root)}: does not parse ({e})"]
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _kw(node, "encoding"):
            continue
        where = f"{path.relative_to(label_root)}:{node.lineno}"
        f = node.func
        if _true(_kw(node, "text")) or _true(_kw(node, "universal_newlines")):
            out.append(f"{where}: text=True without encoding=")
        elif _passes_kwargs(node):
            continue
        elif isinstance(f, ast.Name) and f.id == "open" and _text_mode_open(node):
            out.append(f"{where}: open() in text mode without encoding=")
        elif isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
            # write_text(data, encoding) / read_text(encoding) positionally is fine too
            if len(node.args) < (2 if f.attr == "write_text" else 1):
                out.append(f"{where}: .{f.attr}() without encoding=")
    return out


def test_text_mode_io_names_its_encoding():
    every = [x for p in sources() for x in findings(p)]
    assert not every, f"{len(every)} found:\n" + "\n".join(every[:60])


def test_the_checker_flags_text_true_a_bare_open_and_a_bare_read_text(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text(ast.unparse(ast.parse(PROBE_SOURCE)), encoding=UTF8)
    assert len(findings(probe, tmp_path)) == PROBE_FLAGGED


def test_run_text_decodes_utf8_and_replaces_an_invalid_byte():
    r = _textio.run_text([sys.executable, "-c",
                          "import sys; sys.stdout.buffer.write(b'caf\\xc3\\xa9 \\x81 ok')"])
    assert r.stdout == "café � ok"


def test_run_text_child_runs_in_utf8_mode():
    r = _textio.run_text([sys.executable, "-c",
                          "import sys; print(sys.flags.utf8_mode, sys.stdout.encoding)"])
    assert r.stdout.split() == ["1", "utf-8"]


def test_child_env_sets_utf8_and_keeps_the_callers_vars():
    env = _textio.child_env({"X": "1"})
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["X"] == "1"
