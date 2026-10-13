#!/usr/bin/env python3
"""Every text-mode read, write and subprocess names its encoding.

    python3 tests/test_encoding.py

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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

FAILS: list[str] = []


def ck(name: str, ok: bool) -> None:
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def sources() -> list[Path]:
    files = [p for d in ("scripts", "hooks", "services", "tests")
             for p in (ROOT / d).rglob("*.py")
             if "node_modules" not in p.parts and "__pycache__" not in p.parts]
    hook = ROOT / ".githooks" / "pre-commit"
    if hook.is_file():
        files.append(hook)
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


def findings(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    except SyntaxError as e:
        return [f"{path.relative_to(ROOT)}: does not parse ({e})"]
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _kw(node, "encoding"):
            continue
        where = f"{path.relative_to(ROOT)}:{node.lineno}"
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


every = [x for p in sources() for x in findings(p)]
for x in every[:60]:
    print("   " + x)
ck(f"no text-mode I/O without an explicit encoding ({len(every)} found)", not every)

# the checker itself catches what it claims to
probe = ast.parse('subprocess.run(["x"], text=True)\nopen("f")\nopen("f", "rb")\n'
                  'Path("f").read_text()\nPath("f").write_text("x", encoding="utf-8")\n'
                  'subprocess.run(["x"], text=True, encoding="utf-8")\n')
tmp = ROOT / "tests" / "__encoding_probe__.py"
try:
    tmp.write_text(ast.unparse(probe), encoding="utf-8")
    got = findings(tmp)
finally:
    tmp.unlink(missing_ok=True)
ck("the checker flags text=True, a bare open() and a bare read_text()", len(got) == 3)

# the helper: a child printing bytes that are not UTF-8 does not crash the reader
import _textio  # noqa: E402

r = _textio.run_text([sys.executable, "-c",
                      "import sys; sys.stdout.buffer.write(b'caf\\xc3\\xa9 \\x81 ok')"])
ck("run_text decodes UTF-8 and replaces an invalid byte", r.stdout == "café � ok")
r = _textio.run_text([sys.executable, "-c", "import sys; print(sys.flags.utf8_mode, sys.stdout.encoding)"])
ck("run_text's child runs in UTF-8 mode", r.stdout.split() == ["1", "utf-8"])
env = _textio.child_env({"X": "1"})
ck("child_env sets PYTHONUTF8 and PYTHONIOENCODING and keeps the caller's vars",
   env["PYTHONUTF8"] == "1" and env["PYTHONIOENCODING"] == "utf-8" and env["X"] == "1")

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
raise SystemExit(1 if FAILS else 0)
