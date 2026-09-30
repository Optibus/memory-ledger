#!/usr/bin/env python3
"""memory-ledger usage ingest — Claude Code's local session logs -> `usage-week` nodes.

The report (`usage_report.py`) draws "Where the Quota Went" from one `usage-week`
node per ISO week. This is the plugin-side writer for those nodes, so a person
who has run out of Claude Code quota can still see their numbers: it reads the
session logs Claude Code already keeps on disk (`~/.claude/projects/*/*.jsonl`),
aggregates them per ISO week and upserts the nodes. **No model call, no network,
no dependency** — it is `json`, `os` and SQLite, and it reads nothing but the
usage counters, timestamps, tool names and file paths in those logs. No prompt
text, no answer text and no file contents are kept anywhere.

Cost is an ESTIMATE. The logs carry token counts, not dollars, so each turn is
priced from the table below (published list price, USD per million tokens, per
model family). That is raw per-token pricing, not what a subscription charges:
read it as relative weight and quota burn. The table lives in ONE place (PRICES);
override or extend it with a JSON file (`--prices FILE` or
$MEMORY_LEDGER_USAGE_PRICES): {"<regex on the model id>": [input, output,
cache_write_5m, cache_read], ...}. A price change re-prices every stored session
on the next run.

How it stays cheap and idempotent:
  * Each log file is reduced to a small per-file summary; a checkpoint
    (<home>/usage_ingest.json) keeps the file's mtime+size and that summary, so a
    rerun re-parses only files that changed — the rest is a stat() each.
  * A week is a pure function of the stored summaries. The node id is stable
    (`usage-week_<YYYY-Www>`), so a rerun updates in place and never duplicates,
    and a week whose metrics did not change is not written at all.
  * Never deletes. A week is never overwritten by LESS data than it holds (Claude
    Code prunes old logs), nor when another tool wrote it, unless --force.

A session belongs to the ISO week (in the brain's timezone, `tz` setting, else
UTC) in which it started; its whole cost lands there.

Usage:
  usage_ingest.py run [--since YYYY-MM-DD] [--dry-run] [--json] [--quiet] [--force]
                      [--projects-dir DIR] [--prices FILE]
  usage_ingest.py status                # the checkpoint, as JSON
  usage_ingest.py spawn                 # what the SessionStart hook calls: a
                                        # throttled, detached `run`; always silent

Automatic runs: hooks/usage_ingest.py (SessionStart) calls `spawn`, which starts
a detached `run` at most every 6 hours. Opt out with $MEMORY_LEDGER_NO_USAGE_INGEST=1
or `"usage_ingest": false` in <home>/settings.json.
"""

from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _switch import _env, _env_says_off, home_dir, settings  # noqa: E402

NODE_TYPE = "usage-week"
SOURCE = "usage_ingest.py"
PARSER_VERSION = 1
CHECKPOINT = "usage_ingest.json"
LOCK = "usage_ingest.lock"
STAMP = "usage_ingest.stamp"
SPAWN_EVERY_HOURS = 6
LOCK_STALE_SECONDS = 30 * 60

# --------------------------------------------------------------------------- #
# PRICES — the one place cost comes from. An ESTIMATE.
#
# Published list price, USD per million tokens:
#   (input, output, cache write (5-minute), cache read)
# First match on the model id wins; overrides (--prices) are tried first. The
# one-hour cache write, when the log says a write was one-hour, is billed at
# CACHE_WRITE_1H_X times the input price. A model nothing matches is priced at
# DEFAULT_PRICE and named in the node's `unpricedModels`, so a stale table shows.
# --------------------------------------------------------------------------- #
PRICES: list[tuple[str, tuple[float, float, float, float]]] = [
    (r"fable|mythos", (10.0, 50.0, 12.5, 1.0)),
    (r"opus-4(-[01])?(-\d{8})?$|claude-3-opus", (15.0, 75.0, 18.75, 1.5)),
    (r"opus", (5.0, 25.0, 6.25, 0.5)),
    (r"sonnet", (3.0, 15.0, 3.75, 0.3)),
    (r"haiku-3-5|3-5-haiku", (0.8, 4.0, 1.0, 0.08)),
    (r"haiku-3|3-haiku", (0.25, 1.25, 0.3, 0.03)),
    (r"haiku", (1.0, 5.0, 1.25, 0.1)),
]
DEFAULT_PRICE = (5.0, 25.0, 6.25, 0.5)
CACHE_WRITE_1H_X = 2.0

BUCKETS = [(0, 30, "<30m"), (30, 120, "30m-2h"), (120, 240, "2-4h"),
           (240, 480, "4-8h"), (480, 10 ** 9, "8h+")]
WRITE_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
TICKET = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{2,6}\b")
NOT_TYPED = ("<local-command", "<command-", "<system-reminder", "<task-notification",
             "Caveat:", "[Request interrupted")

# Outward actions: calls that change something outside the local checkout. MCP
# tools are grouped by server; a tool whose name reads as a write counts.
MCP_WRITE = re.compile(r"(create|update|edit|send|post|reply|forward|transition|comment|"
                       r"schedule|publish|share|delete|trash|respond|submit|label|move|write)", re.I)
GH_WRITE = re.compile(r"gh\s+(pr\s+(review|comment|edit|ready|close|reopen|merge|create)"
                      r"|issue\s+(create|comment|edit|close)"
                      r"|api\s+[^|;]*(-X|--method)\s*(POST|PATCH|PUT|DELETE))")
BRAIN_WRITE = re.compile(r"graph\.py\s+(add-node|add-edge|add-rule|add-skill|update-node"
                         r"|complete|done|add-area|ingest-|set-state|park|consolidate)")
LIMIT_TEXT = re.compile(r"(usage limit reached|hit your [\w -]{0,20}limit|limit reached)", re.I)


# --------------------------------------------------------------------------- #
# prices and cost
# --------------------------------------------------------------------------- #

def load_prices(path: str | None) -> list[tuple[str, tuple[float, float, float, float]]]:
    """The price table, overrides first. A bad file is an error, not a silent
    fallback — a wrong price table is exactly what this must not hide."""
    table = list(PRICES)
    src = path or _env("USAGE_PRICES")
    if src:
        raw = json.loads(Path(src).expanduser().read_text(encoding="utf-8"))
        extra = []
        for pat, vals in raw.items():
            if not (isinstance(vals, list) and len(vals) == 4
                    and all(isinstance(v, (int, float)) for v in vals)):
                raise ValueError(f"price for {pat!r} must be [input, output, cache_write, cache_read]")
            re.compile(pat)
            extra.append((pat, tuple(float(v) for v in vals)))
        table = extra + table
    return table


def price_for(model: str, table) -> tuple[tuple[float, float, float, float], bool]:
    for pat, rates in table:
        if re.search(pat, model):
            return rates, True
    return DEFAULT_PRICE, False


def turn_cost(model: str, usage: dict, table) -> tuple[dict, bool]:
    """One API call's cost, split into the report's four classes. Pure."""
    (p_in, p_out, p_cw, p_cr), known = price_for(model, table)
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    read = int(usage.get("cache_read_input_tokens") or 0)
    write = int(usage.get("cache_creation_input_tokens") or 0)
    detail = usage.get("cache_creation") or {}
    w1h = int(detail.get("ephemeral_1h_input_tokens") or 0) if isinstance(detail, dict) else 0
    w1h = min(w1h, write)
    cache_write = ((write - w1h) * p_cw + w1h * p_in * CACHE_WRITE_1H_X) / 1e6
    return ({"input": inp * p_in / 1e6, "output": out * p_out / 1e6,
             "cacheWrite": cache_write, "cacheRead": read * p_cr / 1e6}, known), read


# --------------------------------------------------------------------------- #
# parsing one log file into a summary
# --------------------------------------------------------------------------- #

def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def get_tz(name: str | None):
    """The timezone days and weeks are cut in: --tz, else the brain's `tz`
    setting, else UTC. An unknown name falls back to UTC rather than failing."""
    name = name or _env("TZ") or str(settings().get("tz") or "")
    if not name or name.upper() == "UTC":
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def session_id_of(path: str) -> tuple[str, bool]:
    """(session id, is a subagent file). Subagents log under <sid>/subagents/."""
    p = Path(path)
    if p.parent.name == "subagents":
        return p.parent.parent.name, True
    return p.stem, False


class RepoResolver:
    """File path -> (repository name, working-directory name), from the file
    system alone: walk up to the nearest `.git`; if it is a FILE (a linked
    worktree) follow it to the common git dir, so a worktree folds back onto its
    repository. A path no longer on disk, or outside any repository, is None."""

    def __init__(self):
        self.cache: dict[str, tuple[str, str] | None] = {}

    def resolve(self, file_path: str):
        d = os.path.dirname(file_path)
        if d in self.cache:
            return self.cache[d]
        res = self._walk(d)
        self.cache[d] = res
        return res

    def _walk(self, d: str):
        cur = d
        for _ in range(40):
            git = os.path.join(cur, ".git")
            try:
                if os.path.isdir(git):
                    return os.path.basename(cur), os.path.basename(cur)
                if os.path.isfile(git):
                    line = Path(git).read_text(encoding="utf-8", errors="ignore").strip()
                    if line.startswith("gitdir:"):
                        gd = line.split(":", 1)[1].strip()
                        gd = gd if os.path.isabs(gd) else os.path.normpath(os.path.join(cur, gd))
                        # <common>/.git/worktrees/<name>  ->  <common>
                        if os.sep + "worktrees" + os.sep in gd + os.sep:
                            common = gd.split(os.sep + "worktrees" + os.sep)[0]
                            root = os.path.dirname(common) if os.path.basename(common) == ".git" else common
                            return os.path.basename(root), os.path.basename(cur)
                    return os.path.basename(cur), os.path.basename(cur)
            except OSError:
                return None
            parent = os.path.dirname(cur)
            if parent == cur:
                return None
            cur = parent
        return None


def _blocks(obj: dict):
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                yield b


def _is_typed(obj: dict) -> bool:
    if obj.get("isMeta") or obj.get("isCompactSummary") or obj.get("isSidechain"):
        return False
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        text = " ".join(str(b.get("text", "")) for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    else:
        return False
    text = text.strip()
    return bool(text) and not text.startswith(NOT_TYPED)


def _outward_label(name: str, inp: dict) -> list[tuple[str, int]]:
    if name == "Bash":
        cmd = str(inp.get("command", ""))
        found = []
        if GH_WRITE.search(cmd):
            found.append(("GitHub PR and issue writes", len(GH_WRITE.findall(cmd))))
        if BRAIN_WRITE.search(cmd):
            found.append(("Brain writes (tasks, notes, decisions, rules)", len(BRAIN_WRITE.findall(cmd))))
        return found
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3 and MCP_WRITE.search(parts[-1]):
            server = re.sub(r"^claude[_ ]ai[_ ]", "", parts[1], flags=re.I).replace("_", " ")
            return [(f"{server} writes (MCP)", 1)]
    return []


def _window_of(text: str) -> str:
    low = text.lower()
    if "opus" in low:
        return "seven_day_opus"
    if "sonnet" in low:
        return "seven_day_sonnet"
    if re.search(r"5[- ]hour|five[- ]hour|session limit", low):
        return "five_hour"
    if re.search(r"week|7[- ]day|seven", low):
        return "seven_day"
    return ""


def parse_file(path: str, table, tz, resolver: RepoResolver | None = None) -> dict | None:
    """One log file -> a small, JSON-able summary (or None if it is unreadable or
    holds no billed turn). Streaming writes the same API message across several
    records; each message id is counted once, at its final usage."""
    resolver = resolver or RepoResolver()
    sid, is_sub = session_id_of(path)
    msgs: dict[str, dict] = {}          # message id -> {model, usage, at}
    tool_seen: set[str] = set()
    first = last = None
    typed = 0
    compacted = False
    edits: collections.Counter = collections.Counter()
    dirs: collections.Counter = collections.Counter()
    outward: collections.Counter = collections.Counter()
    limits: list[dict] = []
    try:
        fh = open(path, encoding="utf-8", errors="ignore")
    except OSError:
        return None
    with fh:
        for n, line in enumerate(fh):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            at = _ts(obj.get("timestamp"))
            if at:
                first = at if first is None or at < first else first
                last = at if last is None or at > last else last
            kind = obj.get("type")
            if kind == "system" and obj.get("subtype") == "compact_boundary":
                compacted = True
            if kind == "user":
                if obj.get("isCompactSummary"):
                    compacted = True
                elif not is_sub and _is_typed(obj):
                    typed += 1
            q = obj.get("quotaLimits")
            if isinstance(q, dict) and q.get("status") == "rejected":
                limits.append({"at": str(obj.get("timestamp", "")), "session": sid[:8],
                               "window": str(q.get("rateLimitType", "")),
                               "overage": str(q.get("overageStatus", "")),
                               "reason": str(q.get("overageDisabledReason", ""))})
            if kind != "assistant":
                continue
            msg = obj.get("message") or {}
            text = " ".join(str(b.get("text", "")) for b in _blocks(obj) if b.get("type") == "text")
            if obj.get("isApiErrorMessage") and LIMIT_TEXT.search(text):
                limits.append({"at": str(obj.get("timestamp", "")), "session": sid[:8],
                               "window": _window_of(text), "overage": "", "reason": "limit_message"})
            usage = msg.get("usage")
            model = str(msg.get("model") or "")
            if isinstance(usage, dict) and model and not model.startswith("<"):
                key = str(msg.get("id") or obj.get("requestId") or f"line{n}")
                prev = msgs.get(key)
                if prev is None or int(usage.get("output_tokens") or 0) >= int(
                        prev["usage"].get("output_tokens") or 0):
                    msgs[key] = {"model": model, "usage": usage, "at": at}
            for b in _blocks(obj):
                if b.get("type") != "tool_use":
                    continue
                bid = str(b.get("id") or "")
                if bid and bid in tool_seen:
                    continue
                tool_seen.add(bid)
                name, inp = str(b.get("name") or ""), b.get("input") or {}
                if not isinstance(inp, dict):
                    continue
                if name in WRITE_TOOLS:
                    target = str(inp.get("file_path") or inp.get("notebook_path") or "")
                    hit = resolver.resolve(target) if target else None
                    if hit:
                        edits[hit[0]] += 1
                        dirs[hit[1]] += 1
                for label, cnt in _outward_label(name, inp):
                    outward[label] += cnt
    if not msgs or first is None:
        return None

    comp: collections.Counter = collections.Counter()
    by_model: collections.Counter = collections.Counter()
    by_hour: collections.Counter = collections.Counter()
    by_wd: collections.Counter = collections.Counter()
    days: set[str] = set()
    unpriced: set[str] = set()
    read_tokens = 0
    for m in msgs.values():
        (parts, known), read = turn_cost(m["model"], m["usage"], table)
        cost = sum(parts.values())
        for k, v in parts.items():
            comp[k] += v
        by_model[m["model"]] += cost
        read_tokens += read
        if not known:
            unpriced.add(m["model"])
        if m["at"]:
            local = m["at"].astimezone(tz)
            days.add(local.date().isoformat())
            by_hour[str(local.hour)] += cost
            by_wd[str(local.weekday())] += cost
    return {"sid": sid, "sub": is_sub,
            "first": first.astimezone(tz).isoformat(), "last": last.astimezone(tz).isoformat(),
            "cost": sum(comp.values()), "comp": dict(comp), "byModel": dict(by_model),
            "cacheReadTokens": read_tokens, "turns": len(msgs), "typed": typed,
            "days": sorted(days), "compacted": compacted, "edits": dict(edits),
            "dirs": dict(dirs), "outward": dict(outward), "limits": limits,
            "hour": dict(by_hour), "weekday": dict(by_wd), "unpriced": sorted(unpriced)}


# --------------------------------------------------------------------------- #
# sessions -> week metrics
# --------------------------------------------------------------------------- #

def merge_sessions(files: list[dict]) -> list[dict]:
    """Per-file summaries -> one per session (a session's subagent logs fold in)."""
    by: dict[str, dict] = {}
    for f in files:
        s = by.get(f["sid"])
        if s is None:
            by[f["sid"]] = {**f, "comp": dict(f["comp"]), "byModel": dict(f["byModel"]),
                            "edits": dict(f["edits"]), "dirs": dict(f["dirs"]),
                            "outward": dict(f["outward"]), "limits": list(f["limits"]),
                            "hour": dict(f["hour"]), "weekday": dict(f["weekday"]),
                            "days": list(f["days"]), "unpriced": list(f["unpriced"])}
            continue
        s["first"], s["last"] = min(s["first"], f["first"]), max(s["last"], f["last"])
        s["cost"] += f["cost"]
        s["cacheReadTokens"] += f["cacheReadTokens"]
        s["turns"] += f["turns"]
        s["typed"] += f["typed"]
        s["compacted"] = s["compacted"] or f["compacted"]
        s["days"] = sorted(set(s["days"]) | set(f["days"]))
        s["limits"] += f["limits"]
        s["unpriced"] = sorted(set(s["unpriced"]) | set(f["unpriced"]))
        for key in ("comp", "byModel", "edits", "dirs", "outward", "hour", "weekday"):
            for k, v in f[key].items():
                s[key][k] = s[key].get(k, 0) + v
    return sorted(by.values(), key=lambda s: (s["first"], s["sid"]))


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_bounds(tag: str) -> tuple[date, date]:
    y, w = tag.split("-W")
    mon = date.fromisocalendar(int(y), int(w), 1)
    return mon, mon + timedelta(days=6)


def session_week(s: dict) -> str:
    return iso_week(datetime.fromisoformat(s["first"]).date())


def duration_minutes(s: dict) -> float:
    return (datetime.fromisoformat(s["last"]) - datetime.fromisoformat(s["first"])).total_seconds() / 60


def r2(v: float) -> float:
    return round(v + 0.0, 2)


def week_metrics(sessions: list[dict]) -> dict:
    """The `metrics` dict usage_report.py reads, for one week's sessions. Pure."""
    total = sum(s["cost"] for s in sessions)
    turns = sum(s["turns"] for s in sessions)
    typed = sum(s["typed"] for s in sessions)
    comp: collections.Counter = collections.Counter()
    by_model: collections.Counter = collections.Counter()
    read_tokens = 0
    days: set[str] = set()
    hour = [0.0] * 24
    wd = [0.0] * 7
    unpriced: set[str] = set()
    repos: dict[str, float] = collections.defaultdict(float)
    edits: collections.Counter = collections.Counter()
    dirs: collections.Counter = collections.Counter()
    outward: collections.Counter = collections.Counter()
    limits: list[dict] = []
    nowrite_n = productive_n = 0
    nowrite_cost = productive_cost = 0.0
    for s in sessions:
        comp.update(s["comp"])
        by_model.update(s["byModel"])
        read_tokens += s["cacheReadTokens"]
        days.update(s["days"])
        for k, v in s["hour"].items():
            hour[int(k)] += v
        for k, v in s["weekday"].items():
            wd[int(k)] += v
        unpriced.update(s["unpriced"])
        limits += s["limits"]
        if s["edits"]:
            edits.update(s["edits"])
            spread = sum(s["edits"].values())
            for repo, n in s["edits"].items():
                repos[repo] += s["cost"] * n / spread
            dirs.update(s["dirs"])
        else:
            nowrite_n += 1
            nowrite_cost += s["cost"]
            if s["outward"]:
                outward.update(s["outward"])
                productive_n += 1
                productive_cost += s["cost"]
    buckets = []
    for lo, hi, name in BUCKETS:
        grp = [s for s in sessions if lo <= duration_minutes(s) < hi]
        if not grp:
            continue
        b_turns = sum(s["turns"] for s in grp) or 1
        b_cost = sum(s["cost"] for s in grp)
        nocomp = sum(1 for s in grp if not s["compacted"])
        buckets.append({"bucket": name, "sessions": len(grp), "costUSD": r2(b_cost),
                        "turns": b_turns, "costPerTurn": round(b_cost / b_turns, 4),
                        "noCompactPct": round(nocomp / len(grp) * 100, 1)})
    tickets = sorted({t for d in dirs for t in TICKET.findall(d)})
    dedup_limits = sorted({json.dumps(l, sort_keys=True) for l in limits})
    m = {
        "costUSD": r2(total), "sessions": len(sessions), "turns": turns, "typedPrompts": typed,
        "turnsPerTypedPrompt": round(turns / typed, 1) if typed else None,
        "costPerTypedPrompt": round(total / typed, 2) if typed else None,
        "activeDays": len(days),
        "byModel": {k: r2(v) for k, v in sorted(by_model.items(), key=lambda kv: (-kv[1], kv[0]))},
        "composition": {k: r2(comp.get(k, 0.0)) for k in ("cacheRead", "cacheWrite", "output", "input")},
        "cacheReadPct": round(comp.get("cacheRead", 0.0) / total * 100, 1) if total else 0,
        "cacheReadTokens": read_tokens,
        "cacheReadPerTurn": round(read_tokens / turns) if turns else 0,
        "durationBuckets": buckets,
        "noCompactionSessions": sum(1 for s in sessions if not s["compacted"]),
        "byRepo": {k: r2(v) for k, v in sorted(repos.items(), key=lambda kv: (-kv[1], kv[0]))},
        "editsByRepo": dict(sorted(edits.items(), key=lambda kv: (-kv[1], kv[0]))),
        "totalEdits": sum(edits.values()),
        "workingDirs": dict(sorted(dirs.items(), key=lambda kv: (-kv[1], kv[0]))),
        "noWriteSessions": nowrite_n, "noWriteCostUSD": r2(nowrite_cost),
        "noWriteOutward": dict(sorted(outward.items(), key=lambda kv: (-kv[1], kv[0]))),
        "noWriteOutwardTotal": sum(outward.values()),
        "noWriteProductiveSessions": productive_n, "noWriteProductiveCostUSD": r2(productive_cost),
        "investigationOnlySessions": nowrite_n - productive_n,
        "investigationOnlyCostUSD": r2(nowrite_cost - productive_cost),
        "tickets": tickets,
        "rateLimits": [json.loads(l) for l in dedup_limits],
        "costByHour": [r2(v) for v in hour],
        "costByWeekday": [r2(v) for v in wd],
    }
    if unpriced:
        m["unpricedModels"] = sorted(unpriced)
    return m


def render_body(tag: str, lo: date, hi: date, m: dict) -> str:
    """A short plain-text body, so `recall` and `search` can find a week."""
    money = lambda v: f"${v:,.2f}"  # noqa: E731
    lines = [f"Claude Code usage, ISO week {tag} ({lo} .. {hi}).",
             "Estimated from local session logs by scripts/usage_ingest.py — no model call. "
             "List-price estimate, not an invoice.",
             f"SPEND  {money(m['costUSD'])} ({m['sessions']} sessions, {m['activeDays']} active days, "
             f"{m['turns']:,} turns, {m['typedPrompts']} typed prompts)."]
    if m["byModel"]:
        lines.append("MODELS  " + " · ".join(f"{k} {money(v)}" for k, v in m["byModel"].items()))
    if m["byRepo"]:
        lines.append("REPOSITORIES EDITED  " + " · ".join(f"{k} {money(v)}" for k, v in m["byRepo"].items()))
    lines.append(f"NO COMPACTION  {m['noCompactionSessions']} of {m['sessions']} sessions.")
    lines.append(f"RATE LIMIT HITS  {len(m['rateLimits'])}.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# checkpoint
# --------------------------------------------------------------------------- #

def fingerprint(table, tz) -> str:
    return hashlib.sha256(json.dumps([PARSER_VERSION, str(tz), table, DEFAULT_PRICE,
                                      CACHE_WRITE_1H_X], sort_keys=True).encode()).hexdigest()[:16]


def load_checkpoint(path: Path, fp: str) -> dict:
    try:
        cp = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(cp, dict) and cp.get("fingerprint") == fp and isinstance(cp.get("files"), dict):
            return cp
    except (OSError, ValueError):
        pass
    return {"fingerprint": fp, "files": {}}


def save_checkpoint(path: Path, cp: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cp, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def projects_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser()
    env = _env("USAGE_PROJECTS_DIR")
    if env:
        return Path(env).expanduser()
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(cfg).expanduser() if cfg else Path.home() / ".claude") / "projects"


def find_logs(root: Path) -> list[str]:
    return sorted(glob.glob(str(root / "*" / "*.jsonl"))
                  + glob.glob(str(root / "*" / "*" / "subagents" / "*.jsonl")))


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #

def enabled() -> bool:
    """Automatic runs only: off by $MEMORY_LEDGER_NO_USAGE_INGEST or
    `"usage_ingest": false` in settings.json. The CLI always works when asked."""
    if _env_says_off("usage_ingest"):
        return False
    return str(settings().get("usage_ingest", True)).strip().lower() not in ("false", "0", "no", "off")


def collect(root: Path, cp: dict, table, tz, since_monday: date | None) -> tuple[dict, int, int]:
    """Bring the checkpoint up to date with the logs on disk. Returns
    (checkpoint, files parsed, files unchanged)."""
    resolver = RepoResolver()
    parsed = same = 0
    cutoff = None
    if since_monday:
        cutoff = datetime(since_monday.year, since_monday.month, since_monday.day,
                          tzinfo=tz).timestamp()
    for path in find_logs(root):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if cutoff is not None and st.st_mtime < cutoff:
            continue
        sig = [st.st_mtime_ns, st.st_size]
        prev = cp["files"].get(path)
        if prev and prev.get("sig") == sig:
            same += 1
            continue
        summary = parse_file(path, table, tz, resolver)
        cp["files"][path] = {"sig": sig, "summary": summary}
        parsed += 1
    return cp, parsed, same


def weeks_from(cp: dict, since_monday: date | None) -> dict[str, list[dict]]:
    files = [f["summary"] for f in cp["files"].values() if f.get("summary")]
    weeks: dict[str, list[dict]] = collections.defaultdict(list)
    for s in merge_sessions(files):
        if s["turns"] <= 0:
            continue
        tag = session_week(s)
        if since_monday and week_bounds(tag)[0] < since_monday:
            continue
        weeks[tag].append(s)
    return dict(sorted(weeks.items()))


def store_week(con, graph, tag: str, m: dict, force: bool) -> dict:
    lo, hi = week_bounds(tag)
    node_id = f"{NODE_TYPE}_{tag}"
    title = f"Usage {tag} — ${m['costUSD']:,.2f}, {m['sessions']} sessions"
    props = {"kind": "usage", "week": tag, "from": lo.isoformat(), "to": hi.isoformat(),
             "metrics": m, "source": SOURCE}
    row = con.execute("SELECT props FROM nodes WHERE id=?", (node_id,)).fetchone()
    if row is None:
        res = graph.api_create_node(con, NODE_TYPE, title, body=render_body(tag, lo, hi, m),
                                    props=props, node_id=node_id)
        return {"week": tag, "id": node_id, "action": "created" if res.get("ok") else "error",
                "error": res.get("error", "")}
    try:
        cur = json.loads(row["props"] or "{}")
    except ValueError:
        cur = {}
    old = cur.get("metrics") if isinstance(cur.get("metrics"), dict) else {}
    if not force:
        if cur.get("source") and cur.get("source") != SOURCE:
            return {"week": tag, "id": node_id, "action": "kept",
                    "reason": f"written by {cur.get('source')}; --force to replace"}
        if int(old.get("sessions") or 0) > m["sessions"]:
            return {"week": tag, "id": node_id, "action": "kept",
                    "reason": f"holds {old.get('sessions')} sessions, logs now show {m['sessions']} "
                              "(pruned?); --force to replace"}
    if old == m and cur.get("source") == SOURCE:
        return {"week": tag, "id": node_id, "action": "unchanged"}
    res = graph.api_update_node(con, node_id, title=title, body=render_body(tag, lo, hi, m),
                                props_patch=props)
    return {"week": tag, "id": node_id, "action": "updated" if res.get("ok") else "error",
            "error": res.get("error", "")}


def acquire_lock(path: Path) -> bool:
    try:
        if path.exists() and time.time() - path.stat().st_mtime > LOCK_STALE_SECONDS:
            path.unlink()
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except OSError:
        return False


def ingest(root: Path, *, since: str | None = None, dry_run: bool = False, force: bool = False,
           prices: str | None = None, tz_name: str | None = None) -> dict:
    """Bring the brain's usage-week nodes up to date with the logs. Returns a
    summary dict; nothing here prints."""
    table = load_prices(prices)
    tz = get_tz(tz_name)
    since_monday = None
    if since:
        d = date.fromisoformat(since)
        since_monday = d - timedelta(days=d.weekday())
    home = home_dir()
    cp_path = home / CHECKPOINT
    cp = load_checkpoint(cp_path, fingerprint(table, tz))
    if not root.is_dir():
        return {"ok": True, "projects": str(root), "note": "no session logs directory",
                "parsed": 0, "unchanged": 0, "weeks": []}
    cp, parsed, same = collect(root, cp, table, tz, since_monday)
    weeks = weeks_from(cp, since_monday)
    out: dict = {"ok": True, "projects": str(root), "parsed": parsed, "unchanged": same,
                 "dryRun": dry_run, "weeks": []}
    if dry_run:
        for tag, sess in weeks.items():
            m = week_metrics(sess)
            out["weeks"].append({"week": tag, "id": f"{NODE_TYPE}_{tag}", "action": "would-write",
                                 "costUSD": m["costUSD"], "sessions": m["sessions"]})
        return out
    import graph  # noqa: PLC0415  (the engine: only a real write needs it)
    if graph.disabled():
        return {"ok": True, "note": "the ledger is switched off", "parsed": 0, "unchanged": 0, "weeks": []}
    con = graph.connect()
    try:
        for tag, sess in weeks.items():
            m = week_metrics(sess)
            out["weeks"].append({**store_week(con, graph, tag, m, force),
                                 "costUSD": m["costUSD"], "sessions": m["sessions"]})
    finally:
        con.close()
    if all(w["action"] != "error" for w in out["weeks"]):
        cp["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save_checkpoint(cp_path, cp)   # only once the nodes are safe, so a failure re-parses
    return out


# --------------------------------------------------------------------------- #
# the hook's entry: throttled, detached, silent
# --------------------------------------------------------------------------- #

def spawn(force_now: bool = False) -> bool:
    """Start a detached `run` if automatic ingest is on and the last attempt is
    older than SPAWN_EVERY_HOURS. Touches the stamp BEFORE spawning so a burst of
    sessions starts one run, not many. Never raises, never prints, never waits."""
    try:
        if not enabled():
            return False
        home = home_dir()
        if not home.is_dir() or (home / ".disabled").exists():
            return False          # a ledger that is off or not there yet is left alone
        stamp = home / STAMP
        if not force_now and stamp.exists() and time.time() - stamp.stat().st_mtime < SPAWN_EVERY_HOURS * 3600:
            return False
        if not projects_dir(None).is_dir():
            return False
        stamp.touch()
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "run", "--quiet", "--locked"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True)
        return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="ingest the session logs into usage-week nodes")
    r.add_argument("--since", help="only weeks from the ISO week containing this date (YYYY-MM-DD)")
    r.add_argument("--dry-run", action="store_true", help="show what would be written; write nothing")
    r.add_argument("--json", action="store_true")
    r.add_argument("--quiet", action="store_true", help="print nothing (background runs)")
    r.add_argument("--force", action="store_true",
                   help="also replace a week that holds more sessions or was written by another tool")
    r.add_argument("--locked", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--projects-dir", help="Claude Code's projects dir (default ~/.claude/projects)")
    r.add_argument("--prices", help="JSON file of price overrides")
    r.add_argument("--tz", help="IANA timezone for days and weeks (default: the brain's tz, else UTC)")
    sub.add_parser("status", help="the checkpoint, as JSON")
    sub.add_parser("spawn", help="what the hook calls: a throttled, detached run")
    args = ap.parse_args(argv)

    if args.cmd == "spawn":
        spawn()
        return 0
    if args.cmd == "status":
        try:
            cp = json.loads((home_dir() / CHECKPOINT).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cp = {}
        print(json.dumps({"ok": True, "files": len(cp.get("files", {})), "lastRun": cp.get("last_run"),
                          "automatic": enabled()}))
        return 0

    lock = home_dir() / LOCK
    locked = False
    try:
        if args.locked and not args.dry_run:
            if not acquire_lock(lock):
                return 0
            locked = True
        try:
            res = ingest(projects_dir(args.projects_dir), since=args.since, dry_run=args.dry_run,
                         force=args.force, prices=args.prices, tz_name=args.tz)
        except (ValueError, OSError) as e:
            if not args.quiet:
                print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
            return 2
    finally:
        if locked:
            try:
                lock.unlink()
            except OSError:
                pass
    if args.quiet:
        return 0
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    tag = "[dry-run] " if res.get("dryRun") else ""
    print(f"{tag}{res.get('parsed', 0)} log file(s) parsed, {res.get('unchanged', 0)} unchanged"
          + (f" — {res['note']}" if res.get("note") else ""))
    for w in res["weeks"]:
        extra = f"  ({w['reason']})" if w.get("reason") else ""
        print(f"  {w['action']:10s} {w['week']}  ${w['costUSD']:,.2f}  {w['sessions']} sessions{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
