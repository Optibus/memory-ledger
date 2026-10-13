#!/usr/bin/env python3
"""The blocks of one custom trigger, run once (docs/trigger.md#custom-triggers).

Two halves, one file, both stdlib only:

- `run_blocks(defn, ctx, io)`: the engine. Pure: every outside effect (HTTP,
  the clock, a user script) goes through `io`, so a test drives it with no
  network and no subprocess. No `eval`, no `exec`: a `{{path}}` is walked,
  and a `condition` is parsed with `ast` against a list of allowed nodes.
- `main()`: what `custom_trigger.py` starts as `python -I` with a clean
  environment. It reads one JSON context on stdin, runs the blocks, and writes
  one JSON result on stdout. A `script` block runs in here too, through
  `runpy`, so the owner's code shares the isolation, the timeout and the
  memory limit the parent set on this process.

The context: {"trigger": {...the definition...}, "config": {}, "checkpoint": "",
"now": "<iso>", "secrets": {NAME: value}, "scripts": {name: absolute path},
"max_events": 20, "memory_mb": 256}.
The result: {"status": "ok" | "skipped" | "error", "events": [...],
"checkpoint": str | None, "log": [...], "error": ""}.
"""

from __future__ import annotations

import ast
import contextlib
import json
import re
import runpy
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BLOCK_TYPES = ("http_request", "condition", "transform", "emit", "stop", "script")
FLOW = ("continue", "stop")
# What a `{{...}}` may start with. `item` only inside an `emit` that has `each`;
# `secrets` only for a NAME the trigger declares.
REF_ROOTS = ("prev", "config", "checkpoint", "now", "secrets", "item")
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")
HTTP_TIMEOUT_MAX = 30
BODY_MAX = 200_000
LOG_MAX = 200

_TPL_RE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
_PART_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)|\[(-?\d+)\]|\[\"([^\"]*)\"\]|\['([^']*)'\]")


class BlockError(Exception):
    """A block could not do what it says. The run ends `error`."""


# ---------------------------------------------------------------- paths
def parse_path(path: str) -> list | None:
    """`a.b[0]["c d"]` -> ["a", "b", 0, "c d"]. None when it is not a path."""
    path = (path or "").strip()
    parts, pos = [], 0
    while pos < len(path):
        if parts and path[pos] == ".":
            pos += 1
        m = _PART_RE.match(path, pos)
        if not m or (m.group(1) is not None and parts and path[pos - 1] != "."):
            return None
        if m.group(1) is not None:
            parts.append(m.group(1))
        elif m.group(2) is not None:
            parts.append(int(m.group(2)))
        else:
            parts.append(m.group(3) if m.group(3) is not None else m.group(4))
        pos = m.end()
    return parts or None


def walk(scope: dict, parts: list):
    """The value at `parts`, or None. Never raises: a missing field is None."""
    cur = scope
    for p in parts:
        if isinstance(p, int):
            if not isinstance(cur, list) or not -len(cur) <= p < len(cur):
                return None
            cur = cur[p]
        elif isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
        if cur is None:
            return None
    return cur


def lookup(scope: dict, path: str):
    parts = parse_path(path)
    return walk(scope, parts) if parts else None


def refs(value) -> list:
    """Every `{{path}}` in a value, recursively, as written."""
    if isinstance(value, str):
        return _TPL_RE.findall(value)
    if isinstance(value, dict):
        return [r for v in value.values() for r in refs(v)]
    if isinstance(value, list):
        return [r for v in value for r in refs(v)]
    return []


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def resolve(value, scope: dict):
    """Fill in `{{path}}`. A string that is one reference and nothing else
    keeps the value's type (a number stays a number, a list a list); inside
    other text it becomes text, and a missing value becomes empty."""
    if isinstance(value, str):
        m = _TPL_RE.fullmatch(value.strip())
        if m:
            return lookup(scope, m.group(1))
        return _TPL_RE.sub(lambda mm: _text(lookup(scope, mm.group(1))), value)
    if isinstance(value, dict):
        return {k: resolve(v, scope) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, scope) for v in value]
    return value


# ---------------------------------------------------------------- conditions
_CMP = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b,
        ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
        ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
        ast.Is: lambda a, b: a is b, ast.IsNot: lambda a, b: a is not b}
_ALLOWED = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub,
            ast.Compare, ast.Name, ast.Attribute, ast.Subscript, ast.Constant, ast.List,
            ast.Tuple, ast.Load, *_CMP)
_LITERAL_NAMES = {"None": None, "null": None, "True": True, "true": True,
                  "False": False, "false": False}


def parse_condition(expr: str) -> ast.Expression:
    """Parse and check. Raises BlockError on anything outside the small
    language: comparisons, and/or/not, in, literals and paths."""
    src = _TPL_RE.sub(lambda m: m.group(1), str(expr or "")).strip()
    if not src:
        raise BlockError("empty condition")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as e:
        raise BlockError(f"condition does not parse: {e.msg}") from None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise BlockError(f"condition may not use {type(node).__name__}")
        if isinstance(node, ast.Subscript) and not isinstance(node.slice, ast.Constant):
            raise BlockError("an index in a condition must be a literal")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise BlockError("a field in a condition may not start with _")
    return tree


def condition_roots(expr: str) -> set:
    """The names a condition reads, for `validate`."""
    tree = parse_condition(expr)
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
            and n.id not in _LITERAL_NAMES}


def _eval(node, scope: dict):
    if isinstance(node, ast.Expression):
        return _eval(node.body, scope)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, scope) for e in node.elts]
    if isinstance(node, ast.Name):
        if node.id in _LITERAL_NAMES:
            return _LITERAL_NAMES[node.id]
        return scope.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _eval(node.value, scope)
        return base.get(node.attr) if isinstance(base, dict) else None
    if isinstance(node, ast.Subscript):
        base, key = _eval(node.value, scope), node.slice.value
        if isinstance(base, dict):
            return base.get(key)
        if isinstance(base, list) and isinstance(key, int) and -len(base) <= key < len(base):
            return base[key]
        return None
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, scope)
        if isinstance(node.op, ast.Not):
            return not v
        return -v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval(v, scope) for v in node.values)
        return any(_eval(v, scope) for v in node.values)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, scope)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, scope)
            try:
                ok = _CMP[type(op)](left, right)
            except TypeError:
                # 503 > None, "a" in None: a missing field fails the test,
                # it does not crash the run
                ok = False
            if not ok:
                return False
            left = right
        return True
    raise BlockError(f"condition may not use {type(node).__name__}")


def evaluate(expr: str, scope: dict) -> bool:
    return bool(_eval(parse_condition(expr), scope))


# ---------------------------------------------------------------- io
def http_raw(method: str, url: str, *, headers=None, data: bytes | None = None,
             timeout: int = 30) -> tuple:
    """(status, body text). The same call as `trigger.http_raw`, kept here so
    this process imports nothing of the brain's: it runs with `-I`, an empty
    environment and no home to open."""
    rq = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            return r.status, r.read(BODY_MAX).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(BODY_MAX).decode("utf-8", "replace")
    except urllib.error.URLError as e:
        raise BlockError(f"{method} {url.split('?')[0]}: {e.reason}") from None
    except (OSError, ValueError) as e:
        raise BlockError(f"{method} {url.split('?')[0]}: {e}") from None


def run_script(path: str, env: dict) -> dict:
    """Run one Python file with `prev`, `config`, `checkpoint`, `now` and
    `secrets` as globals. It answers by setting `result` (the next `prev`), and
    may rebind `checkpoint` or set `stop = True`. What it prints goes to
    stderr: stdout is this process's answer."""
    init = {k: env[k] for k in ("prev", "config", "checkpoint", "now", "secrets")}
    with contextlib.redirect_stdout(sys.stderr):
        g = runpy.run_path(path, init_globals=init, run_name="__trigger_block__")
    out = {"result": g.get("result"), "stop": bool(g.get("stop"))}
    if g.get("checkpoint") != env["checkpoint"]:
        out["checkpoint"] = g.get("checkpoint")
    return out


class IO:
    """The outside world, for one run. Tests pass their own."""

    def __init__(self, http=None, now=None, script=None, scripts=None):
        self.http = http or http_raw
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.script = script or run_script
        self.scripts = scripts or {}


# ---------------------------------------------------------------- blocks
def _http(block: dict, scope: dict, io: IO, network: bool) -> dict:
    if not network:
        raise BlockError("network is off for this trigger (limits.network: false)")
    method = str(block.get("method") or "GET").upper()
    url = _text(resolve(block.get("url") or "", scope))
    if not re.match(r"https?://[^\s/]+", url):
        raise BlockError(f"not an http(s) URL: {url[:200]}")
    headers = {str(k): " ".join(_text(v).split())
               for k, v in (resolve(block.get("headers") or {}, scope) or {}).items()}
    body = resolve(block.get("body"), scope)
    data = None
    if body is not None and method not in ("GET", "HEAD"):
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            if not any(k.lower() == "content-type" for k in headers):
                headers["Content-Type"] = "application/json; charset=utf-8"
        else:
            data = _text(body).encode("utf-8")
    timeout = max(1, min(HTTP_TIMEOUT_MAX, int(block.get("timeout_s") or HTTP_TIMEOUT_MAX)))
    status, text = io.http(method, url, headers=headers, data=data, timeout=timeout)
    try:
        parsed = json.loads(text) if text and text.lstrip()[:1] in ("{", "[") else None
    except ValueError:
        parsed = None
    return {"status": status, "ok": status < 400, "body": text[:BODY_MAX], "json": parsed}


def _transform(block: dict, prev, scope: dict):
    out = dict(prev) if isinstance(prev, dict) else {"value": prev} if prev is not None else {}
    if block.get("pick"):
        out = {k: out.get(k) for k in block["pick"]}
    for k in block.get("drop") or []:
        out.pop(k, None)
    for k, v in (block.get("set") or {}).items():
        out[k] = resolve(v, scope)
    return out


def _emit(block: dict, scope: dict, trigger_id: str, now_iso: str) -> list:
    spec = block.get("event") or {}
    items = [None]
    if block.get("each") is not None:
        got = resolve(block["each"], scope)
        items = got if isinstance(got, list) else [] if got is None else [got]
    events = []
    for item in items:
        sc = {**scope, "item": item}
        ev = resolve(spec, sc) if isinstance(spec, dict) else {}
        eid = _text(ev.get("id")).strip() or f"{trigger_id}:{now_iso}"
        payload = ev.get("payload")
        events.append({"id": eid[:500], "title": _text(ev.get("title")).strip()[:500]
                       or f"{trigger_id} fired",
                       "permalink": _text(ev.get("permalink")).strip()[:1000],
                       "payload": payload if payload is not None else {}})
    return events


def run_blocks(defn: dict, ctx: dict, io: IO | None = None) -> dict:
    """Run the blocks in order. `prev` is what the last block produced."""
    io = io or IO()
    tid = str(defn.get("id") or "")
    now = ctx.get("now") or io.now().astimezone(timezone.utc).isoformat(timespec="seconds")
    network = (defn.get("limits") or {}).get("network", True) is not False
    scope = {"prev": None, "config": defn.get("config") or {},
             "checkpoint": ctx.get("checkpoint") or "", "now": now,
             "secrets": ctx.get("secrets") or {}}
    events, checkpoint, log = [], None, []
    status = "skipped"

    def note(block_id, what, detail=""):
        if len(log) < LOG_MAX:
            log.append({"block": block_id, "status": what, "detail": str(detail)[:500]})

    for block in defn.get("blocks") or []:
        bid, kind = str(block.get("id") or ""), block.get("type")
        try:
            if kind == "http_request":
                try:
                    scope["prev"] = _http(block, scope, io, network)
                except BlockError as e:
                    if block.get("on_error") != "continue":
                        raise
                    scope["prev"] = {"status": None, "ok": False, "body": "", "json": None,
                                     "error": str(e)}
                    note(bid, "ok", f"failed, continuing: {e}")
                    continue
                note(bid, "ok", f"{block.get('method') or 'GET'} -> {scope['prev']['status']}")
            elif kind == "condition":
                hit = evaluate(block.get("if") or "", scope)
                flow = block.get("then" if hit else "else") or ("continue" if hit else "stop")
                note(bid, "ok" if flow == "continue" else "stop", f"{hit} -> {flow}")
                if flow == "stop":
                    break
            elif kind == "transform":
                scope["prev"] = _transform(block, scope["prev"], scope)
                note(bid, "ok")
            elif kind == "emit":
                new = _emit(block, scope, tid, now)
                events += new
                if block.get("checkpoint") is not None:
                    checkpoint = _text(resolve(block["checkpoint"], scope))
                note(bid, "ok", f"{len(new)} event(s)")
            elif kind == "stop":
                note(bid, "stop")
                break
            elif kind == "script":
                path = io.scripts.get(str(block.get("script") or ""))
                if not path:
                    raise BlockError(f"no script {block.get('script')!r}")
                res = io.script(path, {**scope, "checkpoint": scope["checkpoint"]})
                scope["prev"] = res.get("result")
                if "checkpoint" in res:
                    checkpoint = _text(res["checkpoint"])
                    scope["checkpoint"] = checkpoint
                note(bid, "stop" if res.get("stop") else "ok")
                if res.get("stop"):
                    break
            else:
                raise BlockError(f"unknown block type {kind!r}")
        except BlockError as e:
            note(bid, "error", e)
            return {"status": "error", "events": [], "checkpoint": None, "log": log,
                    "error": f"block {bid}: {e}"}
        except Exception as e:  # the owner's script, or a value of the wrong kind
            note(bid, "error", f"{type(e).__name__}: {e}")
            return {"status": "error", "events": [], "checkpoint": None, "log": log,
                    "error": f"block {bid}: {type(e).__name__}: {e}"}
    max_events = int(ctx.get("max_events") or 20)
    if len(events) > max_events:
        return {"status": "error", "events": [], "checkpoint": None, "log": log,
                "error": f"{len(events)} events in one run; the limit is {max_events}"}
    if events:
        status = "ok"
    return {"status": status, "events": events, "checkpoint": checkpoint, "log": log,
            "error": ""}


# ---------------------------------------------------------------- process
def _limit_memory(mb: int) -> str:
    """Best effort: an address-space cap where the OS has one. Says why not
    when it cannot (Windows has no `resource`; macOS may refuse it)."""
    if not mb:
        return ""
    try:
        import resource
    except ImportError:
        return "memory_mb is not enforced on this OS"
    try:
        n = int(mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (n, n))
    except (ValueError, OSError) as e:
        return f"memory_mb is not enforced here: {e}"
    return ""


def main() -> int:
    try:
        ctx = json.loads(sys.stdin.read() or "{}")
    except ValueError as e:
        sys.stdout.write(json.dumps({"status": "error", "error": f"bad context: {e}"}))
        return 2
    warn = _limit_memory(int(ctx.get("memory_mb") or 0))
    res = run_blocks(ctx.get("trigger") or {}, ctx, IO(scripts=ctx.get("scripts") or {}))
    if warn:
        res["log"].insert(0, {"block": "", "status": "warning", "detail": warn})
    sys.stdout.write(json.dumps(res, ensure_ascii=False, default=str))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
