#!/usr/bin/env python3
"""memory-ledger usage report — "Where the Quota Went", rendered from the graph.

A deterministic HTML report over the brain's `usage-week` nodes: one node per
ISO week of Claude Code usage, whose `props` carry `week`, `from`, `to` and a
`metrics` dict (cost, sessions, turns, typed prompts, model split, cost
composition, session-length buckets, repositories edited, zero-write output,
rate-limit hits). Something outside the plugin writes those nodes — a weekly
ingest of the person's own usage — and this reads them.

**No model call, and no clock.** `render(weeks)` is a pure function: the same
weeks give the same page, byte for byte. Every number on the page is read or
computed from the nodes; every heading that states a finding is a template
filled from them; anything the nodes do not carry (the daily shape, per-session
detail, what the prompts were about) is simply not on the page. Charts are
static SVG drawn here, so the page needs no script to show them and prints as
it looks. A small script adds hover tooltips when it is allowed to run.

Stdlib only, like the rest of `scripts/`. The database is opened READ-ONLY.

Usage:
  usage_report.py render [--weeks N] [--from YYYY-Www --to YYYY-Www] [--out file.html]
  usage_report.py weeks                     # the stored weeks, as JSON

DB location follows graph.py ($SECOND_BRAIN_DB / $SECOND_BRAIN_HOME).
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _switch import _env, home_dir  # noqa: E402

NODE_TYPE = "usage-week"
WEEK_TAG = re.compile(r"^\d{4}-W\d{2}$")

# The session-length buckets the metrics use, in order, with the lower bound in
# minutes and how a sentence names them. A bucket this list does not know is
# kept, after these, under its own name.
BUCKETS = [
    ("<30m", 0, "under 30 minutes"),
    ("30m-2h", 30, "30 minutes to two hours"),
    ("2-4h", 120, "two to four hours"),
    ("4-8h", 240, "four to eight hours"),
    ("8h+", 480, "eight hours or more"),
]
LONG_MINUTES = 240  # "long session" for the premium below: four hours or more

SERIES = ["var(--s1)", "var(--s2)", "var(--s4)", "var(--s3)", "var(--muted)"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
NUMBER_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten"]
WINDOWS = {"five_hour": "five-hour", "seven_day": "seven-day",
           "seven_day_opus": "seven-day Opus", "seven_day_sonnet": "seven-day Sonnet"}


# --------------------------------------------------------------------------- #
# reading the nodes
# --------------------------------------------------------------------------- #

def default_db() -> Path:
    """The same file graph.py uses, resolved through the same module
    (`_switch`), without importing the engine's connect() — which creates the
    schema, a write this read must never make."""
    env = _env("DB")
    return Path(env).expanduser() if env else home_dir() / "graph.db"


def load_weeks(db: str | Path | None = None) -> list[dict]:
    """Every usage-week node, oldest first. Opens the file read-only; a missing
    file or a brain with no such nodes is an empty list, never an error."""
    path = Path(db) if db else default_db()
    if not path.is_file():
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT id, props FROM nodes WHERE type=?", (NODE_TYPE,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return parse_rows(rows)


def parse_rows(rows) -> list[dict]:
    """(id, props-json) rows -> the week dicts render() takes, oldest first."""
    weeks = []
    for node_id, raw in rows:
        try:
            props = json.loads(raw or "{}")
        except ValueError:
            continue
        tag = str(props.get("week") or "")
        metrics = props.get("metrics")
        if not WEEK_TAG.match(tag) or not isinstance(metrics, dict):
            continue
        weeks.append({"id": node_id, "week": tag, "from": str(props.get("from") or ""),
                      "to": str(props.get("to") or ""), "source": str(props.get("source") or ""),
                      "metrics": metrics})
    weeks.sort(key=lambda w: (w["week"], w["id"]))
    return weeks


def select_weeks(weeks: list[dict], last: int | None = None,
                 start: str | None = None, end: str | None = None) -> list[dict]:
    """The window: weeks between `start` and `end` (ISO week tags, inclusive),
    then the newest `last` of those. No bounds is every stored week."""
    for tag in (start, end):
        if tag and not WEEK_TAG.match(tag):
            raise ValueError(f"not an ISO week tag (YYYY-Www): {tag}")
    out = [w for w in weeks
           if (not start or w["week"] >= start) and (not end or w["week"] <= end)]
    if last is not None:
        if last < 1:
            raise ValueError("--weeks must be at least 1")
        out = out[-last:]
    return out


def available(weeks: list[dict]) -> list[dict]:
    """The stored weeks, slim — what a picker needs."""
    return [{"week": w["week"], "from": w["from"], "to": w["to"],
             "costUSD": round(_num(w["metrics"].get("costUSD")), 2),
             "sessions": int(_num(w["metrics"].get("sessions")))} for w in weeks]


# --------------------------------------------------------------------------- #
# aggregation — sum the weeks, recompute every rate from the sums
# --------------------------------------------------------------------------- #

def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _div(a: float, b: float) -> float:
    return a / b if b else 0.0


def model_name(model_id: str) -> str:
    """`claude-opus-5` -> `Opus 5`, `claude-haiku-4-5-20251001` -> `Haiku 4.5`."""
    if not model_id or model_id.startswith("<"):
        return model_id
    s = re.sub(r"-\d{8}$", "", model_id)
    s = s[len("claude-"):] if s.startswith("claude-") else s
    words = [p for p in s.split("-") if p and not p.isdigit()]
    nums = [p for p in s.split("-") if p.isdigit()]
    if not words:
        return model_id
    return " ".join(w.capitalize() for w in words) + (" " + ".".join(nums) if nums else "")


def _add(acc: dict, key: str, v: float) -> None:
    acc[key] = acc.get(key, 0.0) + v


def _ranked(d: dict) -> list[tuple[str, float]]:
    return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))


def _parse_at(s: str):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _stops(hits: list[dict]) -> list[dict]:
    """Rate-limit hits -> stops. A parent session and its subagents are refused
    seconds apart; hits in one session and window within a minute are one stop."""
    clean = sorted((h for h in hits if isinstance(h, dict)),
                   key=lambda h: (str(h.get("at", "")), str(h.get("session", ""))))
    stops: list[dict] = []
    for h in clean:
        at = _parse_at(str(h.get("at", "")))
        prev = stops[-1] if stops else None
        if (prev and at and prev["_at"] and prev["session"] == str(h.get("session", ""))
                and prev["window"] == str(h.get("window", ""))
                and (at - prev["_last"]).total_seconds() <= 60):
            prev["hits"] += 1
            prev["_last"] = at
            continue
        stops.append({"at": str(h.get("at", "")), "_at": at, "_last": at,
                      "session": str(h.get("session", "")), "window": str(h.get("window", "")),
                      "overage": str(h.get("overage", "")), "reason": str(h.get("reason", "")),
                      "hits": 1})
    for s in stops:
        s.pop("_last")
    return stops


def aggregate(weeks: list[dict]) -> dict:
    """The selected weeks as one window. Pure; sorted wherever order matters."""
    a: dict = {"weeks": [], "cost": 0.0, "sessions": 0, "turns": 0, "typed": 0,
               "active": 0, "calendar": 0, "models": {}, "unpriced": {}, "comp": {},
               "cacheReadTokens": 0, "buckets": {}, "bucketOrder": [], "noCompact": 0,
               "repos": {}, "edits": {}, "totalEdits": 0, "dirs": {}, "noWriteSessions": 0,
               "noWriteCost": 0.0, "outward": {}, "outwardTotal": 0, "productiveSessions": 0,
               "productiveCost": 0.0, "investigationSessions": 0, "investigationCost": 0.0,
               "tickets": set(), "hits": [], "sources": set()}
    for w in weeks:
        m = w["metrics"]
        cost = _num(m.get("costUSD"))
        turns = int(_num(m.get("turns")))
        typed = int(_num(m.get("typedPrompts")))
        sessions = int(_num(m.get("sessions")))
        active = int(_num(m.get("activeDays")))
        days = 0
        try:
            days = (date.fromisoformat(w["to"]) - date.fromisoformat(w["from"])).days + 1
        except ValueError:
            days = 7
        wk_models: dict = {}
        for mid, v in (m.get("byModel") or {}).items():
            name = model_name(str(mid))
            if str(mid).startswith("<"):
                _add(a["unpriced"], name, _num(v))
                continue
            _add(wk_models, name, _num(v))
            _add(a["models"], name, _num(v))
        a["weeks"].append({"week": w["week"], "from": w["from"], "to": w["to"], "cost": cost,
                           "sessions": sessions, "turns": turns, "typed": typed,
                           "active": active, "models": wk_models,
                           "rate": _div(cost, turns), "perTyped": _div(cost, typed),
                           "fanout": _div(turns, typed)})
        a["cost"] += cost
        a["sessions"] += sessions
        a["turns"] += turns
        a["typed"] += typed
        a["active"] += active
        a["calendar"] += days
        for k, v in (m.get("composition") or {}).items():
            _add(a["comp"], str(k), _num(v))
        a["cacheReadTokens"] += int(_num(m.get("cacheReadTokens")))
        for b in m.get("durationBuckets") or []:
            if not isinstance(b, dict) or not b.get("bucket"):
                continue
            name = str(b["bucket"])
            if name not in a["buckets"]:
                a["buckets"][name] = {"sessions": 0, "cost": 0.0, "turns": 0, "noCompact": 0}
                a["bucketOrder"].append(name)
            acc = a["buckets"][name]
            n = int(_num(b.get("sessions")))
            acc["sessions"] += n
            acc["cost"] += _num(b.get("costUSD"))
            acc["turns"] += int(_num(b.get("turns")))
            acc["noCompact"] += int(round(_num(b.get("noCompactPct")) * n / 100))
        a["noCompact"] += int(_num(m.get("noCompactionSessions")))
        for k, v in (m.get("byRepo") or {}).items():
            _add(a["repos"], str(k), _num(v))
        for k, v in (m.get("editsByRepo") or {}).items():
            _add(a["edits"], str(k), _num(v))
        a["totalEdits"] += int(_num(m.get("totalEdits")))
        for k, v in (m.get("workingDirs") or {}).items():
            _add(a["dirs"], str(k), _num(v))
        a["noWriteSessions"] += int(_num(m.get("noWriteSessions")))
        a["noWriteCost"] += _num(m.get("noWriteCostUSD"))
        for k, v in (m.get("noWriteOutward") or {}).items():
            _add(a["outward"], str(k), _num(v))
        a["outwardTotal"] += int(_num(m.get("noWriteOutwardTotal")))
        a["productiveSessions"] += int(_num(m.get("noWriteProductiveSessions")))
        a["productiveCost"] += _num(m.get("noWriteProductiveCostUSD"))
        a["investigationSessions"] += int(_num(m.get("investigationOnlySessions")))
        a["investigationCost"] += _num(m.get("investigationOnlyCostUSD"))
        a["tickets"].update(str(t) for t in (m.get("tickets") or []))
        a["hits"].extend(m.get("rateLimits") or [])
        if w.get("source"):
            a["sources"].add(w["source"])

    known = [b[0] for b in BUCKETS]
    a["bucketOrder"] = ([b for b in known if b in a["buckets"]]
                        + [b for b in a["bucketOrder"] if b not in known])
    for acc in a["buckets"].values():
        acc["rate"] = _div(acc["cost"], acc["turns"])
        acc["ncPct"] = _div(acc["noCompact"] * 100, acc["sessions"])
    a["tickets"] = sorted(a["tickets"])
    a["sources"] = sorted(a["sources"])
    a["stops"] = _stops(a["hits"])
    a["modelsRanked"] = [kv for kv in _ranked(a["models"]) if kv[1] > 0]
    a["reposRanked"] = [kv for kv in _ranked(a["repos"]) if kv[1] > 0]
    a["outwardRanked"] = [kv for kv in _ranked(a["outward"]) if kv[1] > 0]
    a["dirsRanked"] = [kv for kv in _ranked(a["dirs"]) if kv[1] > 0]
    return a


# --------------------------------------------------------------------------- #
# formatting — fixed, locale-free
# --------------------------------------------------------------------------- #

def esc(s) -> str:
    return html.escape(str(s), quote=True)


def usd(v: float) -> str:
    return f"${v:,.2f}"


def usd0(v: float) -> str:
    return f"${v:,.0f}"


def pct(v: float, d: int = 1) -> str:
    return f"{v:.{d}f}%"


def wshare(v: float) -> str:
    """A share of the window: one decimal, two when it would round to 0.0."""
    return pct(v, 2 if 0 < v < 0.05 else 1)


def num(v: float) -> str:
    return f"{int(round(v)):,}"


def rate(v: float) -> str:
    return f"${v:.3f}"


def words(n: int) -> str:
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else f"{n:,}"


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def month_day(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{MONTHS[d.month - 1]} {d.day:02d}"


def bucket_label(name: str) -> str:
    return name.replace("-", "–")


def bucket_phrase(name: str) -> str:
    for key, _lo, phrase in BUCKETS:
        if key == name:
            return phrase
    return name


def bucket_minutes(name: str) -> int | None:
    for key, lo, _phrase in BUCKETS:
        if key == name:
            return lo
    return None


def window_label(w: str) -> str:
    return WINDOWS.get(w, w.replace("_", " ") or "unnamed")


def n(x: float) -> str:
    """An SVG coordinate: two decimals at most, no trailing zeros, no -0."""
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def nice_axis(maxv: float, ticks: int = 4) -> tuple[float, list[float]]:
    """A round top and evenly spaced round ticks from 0 to it."""
    if maxv <= 0:
        return 1.0, [0.0, 1.0]
    raw = maxv / ticks
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if raw <= m * mag * (1 + 1e-9))
    count = max(1, math.ceil(maxv / step - 1e-9))
    return step * count, [step * i for i in range(count + 1)]


def tick_text(v: float, step: float, prefix: str = "", suffix: str = "") -> str:
    decimals = 0 if step >= 1 else min(4, max(0, -math.floor(math.log10(step)) +
                                             (1 if round(step / 10 ** math.floor(math.log10(step)), 6) == 2.5 else 0)))
    body = f"{v:,.{decimals}f}"
    return f"{prefix}{body}{suffix}"


# --------------------------------------------------------------------------- #
# SVG — static, so the page shows (and prints) its charts without a script
# --------------------------------------------------------------------------- #

MONO = 'font-family:&quot;JetBrains Mono&quot;,monospace'


def label(x, y, s, anchor="start", size=11, color="var(--muted)", weight=400, extra="") -> str:
    return (f'<text x="{n(x)}" y="{n(y)}" text-anchor="{anchor}" font-size="{n(size)}" '
            f'fill="{color}" style="{MONO}" font-weight="{weight}" '
            f'font-variant-numeric="tabular-nums"{extra}>{esc(s)}</text>')


def line(x1, y1, x2, y2, stroke="var(--rule)", width=1, extra="") -> str:
    return (f'<line x1="{n(x1)}" x2="{n(x2)}" y1="{n(y1)}" y2="{n(y2)}" '
            f'stroke="{stroke}" stroke-width="{n(width)}"{extra}/>')


def rect(x, y, w, h, fill, rx=2, tip: list[str] | None = None, extra="") -> str:
    attrs = (f'x="{n(x)}" y="{n(y)}" width="{n(max(0, w))}" height="{n(max(0, h))}" '
             f'fill="{fill}" rx="{n(rx)}"{extra}')
    if tip:
        return f'<rect class="hit" tabindex="0" {attrs}><title>{esc(chr(10).join(tip))}</title></rect>'
    return f"<rect {attrs}/>"


def svg(chart_id: str, w: int, h: float, aria: str, body: list[str]) -> str:
    return (f'<svg id="{chart_id}" role="img" aria-label="{esc(aria)}" '
            f'viewBox="0 0 {w} {n(h)}" preserveAspectRatio="xMidYMid meet">\n'
            + "\n".join(body) + "\n</svg>")


def hbars(chart_id: str, rows: list[dict], *, left: int, right: int, row_h: int = 38,
          top: int = 24, axis_suffix: str = "", axis_prefix: str = "", foot: str = "",
          aria: str, legend: list[tuple[str, str]] | None = None) -> str:
    """Horizontal bars: rows of {label, value, fill, text, tip, note?, bold?}."""
    W = 1040
    pw = W - left - right
    t = top + (22 if legend else 0)
    maxv = max((r["value"] for r in rows), default=0)
    axis_top, ticks = nice_axis(maxv)
    body = []
    if legend:
        lx = left
        for name, fill in legend:
            body.append(rect(lx, t - 22, 10, 10, fill))
            body.append(label(lx + 15, t - 13, name, "start", 10.5, "var(--ink-2)", 500))
            lx += len(name) * 6.6 + 34
    for i, r in enumerate(rows):
        y = t + i * row_h
        w = max(3, r["value"] / axis_top * pw)
        weight = 700 if r.get("bold") else 500
        body.append(label(left - 12, y + 18, r["label"], "end", 12.5, "var(--ink)", weight))
        if r.get("note"):
            body.append(label(left - 12, y + 31, r["note"], "end", 10))
        body.append(rect(left, y + 4, w, 22, r["fill"], tip=r["tip"]))
        body.append(label(left + w + 10, y + 20, r["text"], "start", 11.5, "var(--ink)",
                          700 if r.get("bold") else 600))
    by = t + len(rows) * row_h + 4
    body.append(line(left, by, left + pw, by))
    step = ticks[1] - ticks[0] if len(ticks) > 1 else 1
    for v in ticks:
        body.append(label(left + v / axis_top * pw, by + 17, tick_text(v, step, axis_prefix, axis_suffix),
                          "middle", 10))
    if foot:
        body.append(label(left, by + 35, foot, "start", 10))
    return svg(chart_id, W, by + 46, aria, body)


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #

CSS = """\
:root{
  --ground:#F2F3F0; --surface:#FCFCFB; --surface-2:#E9EAE6; --sunk:#E4E6E1;
  --ink:#141A1B; --ink-2:#3C4647; --muted:#697374; --rule:#D8DAD5; --rule-soft:#E6E8E3;
  --accent:#04697A; --accent-soft:#D6EAEE;
  --s1:#008C9E; --s2:#D25A14; --s3:#6257C4; --s4:#8C9622;
  --good:#2E7148; --warn:#9A6B08; --crit:#A8281F;
  --crit-soft:#F6E2E0; --warn-soft:#F5EBD6; --good-soft:#DFEDE4;
  --shadow:0 1px 2px rgba(20,26,27,.05), 0 8px 24px -14px rgba(20,26,27,.18);

  --display:"Archivo","Helvetica Neue",Arial,sans-serif;
  --body:"Source Serif 4",Georgia,"Times New Roman",serif;
  --mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;

  --col:min(100%, 68ch);
  --wide:min(100%, 1040px);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --ground:#121617; --surface:#1A1F21; --surface-2:#232829; --sunk:#171C1D;
    --ink:#EAECE9; --ink-2:#C3C9C8; --muted:#8C9697; --rule:#2E3436; --rule-soft:#252B2C;
    --accent:#45BECD; --accent-soft:#16333A;
    --s1:#0E9BAD; --s2:#DB6B33; --s3:#8278D4; --s4:#8B9524;
    --good:#5FB183; --warn:#D3A23C; --crit:#E1796E;
    --crit-soft:#33211F; --warn-soft:#2E2718; --good-soft:#1B2C22;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -14px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"]{
  --ground:#121617; --surface:#1A1F21; --surface-2:#232829; --sunk:#171C1D;
  --ink:#EAECE9; --ink-2:#C3C9C8; --muted:#8C9697; --rule:#2E3436; --rule-soft:#252B2C;
  --accent:#45BECD; --accent-soft:#16333A;
  --s1:#0E9BAD; --s2:#DB6B33; --s3:#8278D4; --s4:#8B9524;
  --good:#5FB183; --warn:#D3A23C; --crit:#E1796E;
  --crit-soft:#33211F; --warn-soft:#2E2718; --good-soft:#1B2C22;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -14px rgba(0,0,0,.7);
}

*{box-sizing:border-box}
body{
  margin:0; background:var(--ground); color:var(--ink);
  font-family:var(--body); font-size:16.5px; line-height:1.62;
  -webkit-font-smoothing:antialiased;
}
.wrap{ width:100%; max-width:1120px; margin:0 auto; padding-inline:20px; padding-block:0 72px; }

/* ---------- masthead ---------- */
.mast{ padding-block:44px 30px; border-bottom:1px solid var(--rule); }
.kicker{
  font-family:var(--mono); font-size:11px; font-weight:500; letter-spacing:.14em;
  text-transform:uppercase; color:var(--muted); display:flex; flex-wrap:wrap; gap:6px 18px;
}
h1{
  font-family:var(--display); font-weight:800; font-size:clamp(38px,7vw,68px);
  line-height:.98; letter-spacing:-.028em; margin:16px 0 0; text-wrap:balance; max-width:16ch;
}
.dek{ font-size:19px; color:var(--ink-2); max-width:62ch; margin:16px 0 0; }

/* ---------- headline instrument ---------- */
.gauge{
  margin-top:30px; display:grid; gap:1px; background:var(--rule);
  grid-template-columns:repeat(auto-fit,minmax(178px,1fr));
  border:1px solid var(--rule); border-radius:3px; overflow:hidden;
}
.gauge div{ background:var(--surface); padding:16px 18px 18px; }
.gauge dt{ font-family:var(--mono); font-size:10.5px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted); }
.gauge dd{ margin:7px 0 0; font-family:var(--display); font-weight:700; font-size:clamp(20px,2.1vw,30px); overflow-wrap:anywhere; letter-spacing:-.02em; font-variant-numeric:tabular-nums; line-height:1; }
.gauge dd small{ font-family:var(--mono); font-weight:400; font-size:12px; color:var(--muted); display:block; margin-top:7px; letter-spacing:0; }
.gauge div.lead{ background:var(--ink); color:var(--ground); }
.gauge div.lead dt, .gauge div.lead dd small{ color:color-mix(in srgb, var(--ground) 62%, transparent); }

/* ---------- sections ---------- */
section{ padding-block:46px 8px; border-top:1px solid var(--rule-soft); }
section:first-of-type{ border-top:none; }
.eyebrow{
  font-family:var(--mono); font-size:11px; letter-spacing:.13em; text-transform:uppercase;
  color:var(--accent); margin:0 0 10px; display:flex; gap:10px; align-items:baseline; flex-wrap:wrap;
}
.eyebrow .src{ color:var(--muted); letter-spacing:.06em; text-transform:none; }
h2{
  font-family:var(--display); font-weight:700; font-size:clamp(25px,3.6vw,34px);
  letter-spacing:-.021em; line-height:1.1; margin:0 0 14px; text-wrap:balance; max-width:22ch;
}
h3{ font-family:var(--display); font-weight:600; font-size:17px; letter-spacing:-.01em; margin:30px 0 8px; }
p{ margin:0 0 15px; max-width:var(--col); }
p.lead-p{ font-size:18.5px; color:var(--ink-2); }
strong{ font-weight:600; }
.num{ font-family:var(--mono); font-weight:500; font-variant-numeric:tabular-nums; font-size:.93em; }
ul{ max-width:var(--col); padding-left:1.1em; margin:0 0 15px; }
li{ margin-bottom:7px; }
a{ color:var(--accent); }

/* ---------- verdict bar ---------- */
.verdict{
  display:flex; gap:13px; align-items:flex-start; max-width:var(--col);
  border-left:3px solid var(--accent); background:var(--surface);
  padding:14px 16px; margin:0 0 22px; border-radius:0 3px 3px 0;
}
.verdict.alarm{ border-left-color:var(--crit); background:var(--crit-soft); }
.verdict.caution{ border-left-color:var(--warn); background:var(--warn-soft); }
.verdict.ok{ border-left-color:var(--good); background:var(--good-soft); }
.verdict p{ margin:0; font-size:16px; }
.verdict .tag{
  font-family:var(--mono); font-size:10px; letter-spacing:.11em; text-transform:uppercase;
  color:var(--muted); flex:0 0 auto; padding-top:4px;
}

/* ---------- figures ---------- */
figure{ margin:24px 0 8px; width:var(--wide); }
figure svg{ display:block; width:100%; height:auto; overflow:visible; }
figcaption{
  font-family:var(--mono); font-size:11.5px; color:var(--muted); line-height:1.55;
  margin-top:12px; max-width:78ch;
}
.legend{ display:flex; flex-wrap:wrap; gap:6px 18px; margin:0 0 12px; }
.legend span{ display:inline-flex; align-items:center; gap:7px; font-family:var(--mono); font-size:11.5px; color:var(--ink-2); }
.legend i{ width:11px; height:11px; border-radius:2px; display:inline-block; flex:0 0 auto; }

/* ---------- tables ---------- */
.scroll{ width:var(--wide); overflow-x:auto; margin:22px 0 8px; border:1px solid var(--rule); border-radius:3px; background:var(--surface); }
table{ border-collapse:collapse; width:100%; font-family:var(--mono); font-size:12.5px; min-width:560px; }
th,td{ text-align:left; padding:9px 14px; border-bottom:1px solid var(--rule-soft); white-space:nowrap; }
th{ font-size:10px; letter-spacing:.11em; text-transform:uppercase; color:var(--muted); font-weight:500; background:var(--sunk); position:sticky; top:0; }
th.n, td.n{ text-align:right; font-variant-numeric:tabular-nums; }
tbody tr:last-child td{ border-bottom:none; }
tbody tr:hover td{ background:var(--surface-2); }
td .bar{ display:block; height:3px; border-radius:2px; background:var(--s1); margin-top:5px; margin-left:auto; }
.chip{ font-size:10px; letter-spacing:.06em; text-transform:uppercase; padding:2px 7px; border-radius:2px; background:var(--surface-2); color:var(--ink-2); }
.chip.hot{ background:var(--crit-soft); color:var(--crit); }
.chip.mid{ background:var(--warn-soft); color:var(--warn); }

/* ---------- footer ---------- */
footer{ margin-top:56px; padding-top:26px; border-top:1px solid var(--rule); }
footer h3{ margin-top:0; }
footer p, footer li{ font-size:14.5px; color:var(--muted); max-width:72ch; }
footer code{ font-family:var(--mono); font-size:12px; color:var(--ink-2); }

/* ---------- tooltip ---------- */
#tip{
  position:fixed; z-index:50; pointer-events:none; opacity:0; transition:opacity .09s;
  background:var(--ink); color:var(--ground); font-family:var(--mono); font-size:11.5px;
  line-height:1.5; padding:7px 10px; border-radius:3px; box-shadow:var(--shadow); max-width:260px;
}
#tip b{ font-weight:700; }
.hit{ cursor:default; }
.hit:focus-visible{ outline:2px solid var(--accent); outline-offset:2px; }

@media (max-width:560px){
  .gauge dd{ font-size:25px; }
}
@media (prefers-reduced-motion:reduce){ *{ transition:none !important; animation:none !important; } }

/* ---------- print: "Save as PDF" keeps the look, light, whole ---------- */
@media print{
  @page{ size:A4; margin:14mm 12mm; }
  :root:not([data-print]), :root[data-theme]{
    --ground:#FFFFFF; --surface:#FCFCFB; --surface-2:#E9EAE6; --sunk:#E4E6E1;
    --ink:#141A1B; --ink-2:#3C4647; --muted:#697374; --rule:#D8DAD5; --rule-soft:#E6E8E3;
    --accent:#04697A; --accent-soft:#D6EAEE;
    --s1:#008C9E; --s2:#D25A14; --s3:#6257C4; --s4:#8C9622;
    --good:#2E7148; --warn:#9A6B08; --crit:#A8281F;
    --crit-soft:#F6E2E0; --warn-soft:#F5EBD6; --good-soft:#DFEDE4;
    --shadow:none; --col:100%; --wide:100%;
  }
  html, body{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }
  body{ font-size:10.5pt; line-height:1.5; }
  .wrap{ max-width:none; padding:0; }
  .mast{ padding-block:0 18px; }
  h1{ font-size:40pt; }
  section{ padding-block:22px 4px; }
  h2, h3, .eyebrow{ break-after:avoid; page-break-after:avoid; }
  figure, .verdict, .gauge, .scroll, tr, footer ul li{ break-inside:avoid; page-break-inside:avoid; }
  .scroll{ overflow:visible; }
  table{ min-width:0; }
  th{ position:static; }
  tbody tr:hover td{ background:none; }
  #tip{ display:none; }
}
"""

SCRIPT = """\
(function(){
"use strict";
var tip=document.getElementById("tip");
if(!tip||!document.querySelectorAll)return;
function show(node,lines,e){
  tip.textContent="";
  lines.forEach(function(s,i){
    if(i)tip.appendChild(document.createElement("br"));
    if(i===0){var b=document.createElement("b");b.textContent=s;tip.appendChild(b);}
    else tip.appendChild(document.createTextNode(s));
  });
  tip.style.opacity="1";
  var x=(e.clientX||0)+14,y=(e.clientY||0)+14;
  if(e.type==="focus"){var r=node.getBoundingClientRect();x=r.left+r.width/2;y=r.top-8;}
  var w=tip.offsetWidth,h=tip.offsetHeight;
  if(x+w>window.innerWidth-8)x=window.innerWidth-w-8;
  if(y+h>window.innerHeight-8)y=y-h-28;
  tip.style.left=Math.max(8,x)+"px";tip.style.top=Math.max(8,y)+"px";
}
function hide(){tip.style.opacity="0";}
Array.prototype.forEach.call(document.querySelectorAll("svg .hit"),function(node){
  var t=node.querySelector("title");
  if(!t)return;
  var lines=t.textContent.split("\\n");
  node.removeChild(t);
  var on=function(e){show(node,lines,e);};
  node.addEventListener("mouseenter",on);node.addEventListener("mousemove",on);
  node.addEventListener("focus",on);
  node.addEventListener("mouseleave",hide);node.addEventListener("blur",hide);
});
})();
"""

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">\n'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700;800'
         '&amp;family=JetBrains+Mono:wght@400;500;700&amp;family=Source+Serif+4:opsz,wght@8..60,400;8..60,600'
         '&amp;display=swap">')


def page(title: str, body: str) -> str:
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{esc(title)}</title>\n{FONTS}\n<style>\n{CSS}</style>\n</head>\n<body>\n"
            f"<div class=\"wrap\">\n{body}\n</div>\n\n"
            "<div id=\"tip\" role=\"status\" aria-live=\"polite\"></div>\n\n"
            f"<script>\n{SCRIPT}</script>\n</body>\n</html>\n")


def verdict(tag: str, text: str, tone: str = "") -> str:
    cls = f"verdict {tone}".strip()
    return f'<div class="{cls}">\n  <span class="tag">{esc(tag)}</span>\n  <p>{text}</p>\n</div>'


def section(sid: str, eyebrow: str, src: str, heading: str, parts: list[str]) -> str:
    return (f'<section id="{sid}">\n  <p class="eyebrow">{esc(eyebrow)} <span class="src">{esc(src)}</span></p>\n'
            f"  <h2>{heading}</h2>\n\n" + "\n\n".join(p for p in parts if p) + "\n</section>")


def figure(chart: str, caption: str, legend: list[tuple[str, str]] | None = None) -> str:
    leg = ""
    if legend:
        leg = ('<div class="legend">' + "".join(
            f'<span><i style="background:{fill}"></i>{esc(name)}</span>' for name, fill in legend)
            + "</div>\n")
    return f"<figure>\n{leg}{chart}\n<figcaption>{caption}</figcaption>\n</figure>"


def table(headers: list[tuple[str, bool]], rows: list[list[str]]) -> str:
    """headers: (text, numeric). Cells are already-escaped HTML."""
    head = "".join(f'<th class="n">{esc(h)}</th>' if numeric else f"<th>{esc(h)}</th>"
                   for h, numeric in headers)
    body = []
    for r in rows:
        cells = "".join(f'<td class="n">{c}</td>' if headers[i][1] else f"<td>{c}</td>"
                        for i, c in enumerate(r))
        body.append(f"<tr>{cells}</tr>")
    return ('<div class="scroll">\n<table>\n<thead><tr>' + head + "</tr></thead>\n<tbody>\n"
            + "\n".join(body) + "\n</tbody>\n</table>\n</div>")


def share_cell(p: float) -> str:
    return f'{wshare(p)}<span class="bar" style="width:{n(max(2, min(100, p)))}%"></span>'


def N(s) -> str:
    """A number inline in prose."""
    return f'<span class="num">{esc(s)}</span>'


def trim_float(v: float, d: int = 1) -> str:
    return f"{v:.{d}f}"


# --------------------------------------------------------------------------- #
# sections
# --------------------------------------------------------------------------- #

def _masthead(a: dict) -> str:
    first, last = a["weeks"][0], a["weeks"][-1]
    nw = len(a["weeks"])
    comp = a["comp"]
    read_pct = _div(comp.get("cacheRead", 0) * 100, a["cost"])
    repos = len(a["reposRanked"])
    kick = [f"usage-week nodes&nbsp;·&nbsp;{plural(nw, 'week')}",
            f"{esc(first['from'])} → {esc(last['to'])}",
            f"{plural(a['sessions'], 'session')}&nbsp;·&nbsp;{plural(repos, 'repo')}"]
    stops = len(a["stops"])
    stop_clause = (f", and the work hit a hard rate-limit stop {words(stops)} "
                   f"time{'s' if stops != 1 else ''}" if stops
                   else ", and no rate limit stopped the work")
    dek = (f"{words(nw).capitalize() if nw <= 10 else num(nw)} week{'s' if nw != 1 else ''} of Claude Code, "
           f"{esc(first['from'])} to {esc(last['to'])}, read from the brain's weekly usage records. "
           f"{usd(a['cost'])} at list price across {plural(a['sessions'], 'session')}; "
           f"{pct(read_pct)} of it is re-reading cached context{stop_clause}.")

    gauges = [("lead", "List-price burn", usd(a["cost"]),
               f"{plural(a['active'], 'active day')} · {usd(_div(a['cost'], a['active']))}/day"),
              ("", "Cache read", pct(read_pct), f"{usd(comp.get('cacheRead', 0))} of the total")]
    if a["modelsRanked"]:
        top, cost = a["modelsRanked"][0]
        gauges.append(("", f"{top} share", pct(_div(cost * 100, a["cost"])), f"{usd(cost)} of spend"))
    if a["typed"]:
        gauges.append(("", "Turns per prompt", f"{_div(a['turns'], a['typed']):.1f}×",
                       f"{num(a['turns'])} turns · {num(a['typed'])} typed"))
    if stops:
        s = a["stops"][-1]
        gauges.append(("", "Hard stops", str(stops),
                       f"{window_label(s['window'])} cap, {s['at'][:10]}"))
    else:
        gauges.append(("", "Hard stops", "0", "no rate-limit hit recorded"))
    gauges.append(("", "Never compacted", f"{num(a['noCompact'])} of {num(a['sessions'])}",
                   f"{pct(_div(a['noCompact'] * 100, a['sessions']))} of sessions"))
    if a["reposRanked"]:
        repo, cost = a["reposRanked"][0]
        gauges.append(("", "Top repo", esc(repo),
                       f"{usd(cost)} · {pct(_div(cost * 100, a['cost']))} · "
                       f"{num(a['edits'].get(repo, 0))} edits"))
    if a["noWriteSessions"]:
        gauges.append(("", "No-code output", num(a["outwardTotal"]),
                       f"outward actions · {plural(a['productiveSessions'], 'session')}"))
    dl = "\n".join(
        f'    <div{" class=" + chr(34) + cls + chr(34) if cls else ""}><dt>{esc(dt)}</dt>'
        f"<dd>{dd}<small>{esc(small)}</small></dd></div>" for cls, dt, dd, small in gauges)
    return ('<header class="mast">\n  <div class="kicker">\n'
            + "\n".join(f"    <span>{k}</span>" for k in kick)
            + "\n  </div>\n  <h1>Where the Quota Went</h1>\n"
            f'  <p class="dek">{dek}</p>\n\n  <dl class="gauge">\n{dl}\n  </dl>\n</header>')


def _usage(a: dict) -> str:
    weeks = a["weeks"]
    parts = [verdict("Read as",
                     "These are raw per-token model rates, not a subscription invoice. Treat them as "
                     "<strong>relative weight and quota burn</strong> — the number that matters is which "
                     "activity carries which share, not the dollar sign.")]
    costs = sorted(w["cost"] for w in weeks)
    mid = len(costs) // 2
    median = costs[mid] if len(costs) % 2 else (costs[mid - 1] + costs[mid]) / 2
    peak = max(weeks, key=lambda w: (w["cost"], w["week"]))
    if len(weeks) > 1:
        parts.append(
            f'<p class="lead-p">The weekly shape: a median week costs {N(usd(median))}, but the week of '
            f"{esc(peak['from'])} alone cost {N(usd(peak['cost']))} — "
            f"{_div(peak['cost'], median):.1f}× the median, in {plural(peak['active'], 'active day')}. "
            f"{words(a['active']).capitalize() if a['active'] <= 10 else num(a['active'])} of "
            f"{num(a['calendar'])} calendar days carried any usage at all.</p>")
    else:
        parts.append(
            f'<p class="lead-p">One week, {esc(peak["from"])} to {esc(peak["to"])}: '
            f"{N(usd(peak['cost']))} in {plural(peak['active'], 'active day')}.</p>")

    models = [name for name, _ in a["modelsRanked"]]
    colors = {name: SERIES[i % len(SERIES)] for i, name in enumerate(models)}
    W, H, L, R, T, B = 1040, 300, 58, 14, 26, 54
    pw, ph = W - L - R, H - T - B
    top, ticks = nice_axis(max((w["cost"] for w in weeks), default=0))
    step = ticks[1] - ticks[0]
    body = []
    for v in ticks:
        y = T + ph - v / top * ph
        body.append(line(L, y, W - R, y))
        body.append(label(L - 9, y + 4, tick_text(v, step, "$"), "end", 10.5))
    band = pw / len(weeks)
    bw = min(96, band * 0.62)
    rotate = len(weeks) > 12
    for i, w in enumerate(weeks):
        cx = L + band * i + band / 2
        acc = 0.0
        tip = [f"{w['week']} · week of {w['from']}", f"Total {usd(w['cost'])}"]
        tip += [f"{m} {usd(w['models'][m])}" for m in models if w["models"].get(m, 0) > 0]
        for m in models:
            v = w["models"].get(m, 0)
            if v <= 0:
                continue
            h = v / top * ph
            y = T + ph - acc - h
            gap = 2 if acc > 0 else 0
            body.append(rect(cx - bw / 2, y + gap, bw, max(1, h - gap), colors[m], tip=tip))
            acc += h
        if not rotate:
            body.append(label(cx, T + ph - acc - 8, usd(w["cost"]), "middle", 11, "var(--ink)", 700))
        lx, ly = cx, H - B + 20
        extra = f' transform="rotate(-42 {n(lx)} {n(ly)})"' if rotate else ""
        body.append(label(lx, ly, w["week"], "middle", 10.5, "var(--ink)" if not rotate else "var(--muted)",
                          500, extra))
        if not rotate:
            body.append(label(lx, ly + 14, month_day(w["from"]), "middle", 10))
    body.append(line(L, T + ph, W - R, T + ph, "var(--ink-2)", 1.5))
    chart = svg("ch-usage", W, H, f"Weekly list-price cost by model across {len(weeks)} weeks. "
                f"Peak {usd(peak['cost'])} in {peak['week']}.", body)
    parts.append(figure(chart, "Cost per ISO week, stacked by model. The nodes are weekly, so the week is "
                        "the finest grain this report can show; a day-level shape is not in the data.",
                        [(m, colors[m]) for m in models]))

    # --- repositories
    if a["reposRanked"] or a["noWriteSessions"]:
        parts.append("<h3>Which repository the work was actually in</h3>")
        parts.append(verdict(
            "Read as",
            "Each session's cost is credited to the <strong>repositories it actually modified</strong>, "
            "as recorded in the weekly nodes — split by edit count when a session touched more than one. "
            "A working directory says where the terminal was pointed; the edits say what was built."))
        rows = [(repo, cost, False) for repo, cost in a["reposRanked"]]
        if a["noWriteSessions"]:
            rows.append(("No file writes", a["noWriteCost"], True))
        rows.sort(key=lambda r: (-r[1], r[0]))
        chart_rows = []
        for name, cost, blank in rows:
            share = _div(cost * 100, a["cost"])
            edits = int(a["edits"].get(name, 0)) if not blank else None
            tip = [name, f"{usd(cost)} · {wshare(share)} of spend"]
            tip.append(f"{num(edits)} file edits" if edits is not None
                       else f"{plural(a['noWriteSessions'], 'session')} that edited no repo file")
            chart_rows.append({"label": name, "value": share, "fill": "var(--muted)" if blank else "var(--s1)",
                               "text": f"{usd(cost)}  ·  {wshare(share)}" + (f"   {num(edits)} edits" if edits is not None else ""),
                               "tip": tip})
        chart = hbars("ch-repo", chart_rows, left=176, right=196, top=12, axis_suffix="%",
                      foot=f"SHARE OF THE {usd0(a['cost'])} WINDOW, BY REPOSITORY MODIFIED",
                      legend=[("Repository changed", "var(--s1)"), ("No code written", "var(--muted)")],
                      aria="Cost by repository actually changed: " + ", ".join(
                          f"{r['label']} {r['text'].split('  ·  ')[0]}" for r in chart_rows) + ".")
        parts.append(figure(chart, "Spend attributed to the repository that was actually modified. "
                            + (f"{N('No file writes')} is the {plural(a['noWriteSessions'], 'session')} "
                               "that changed no code at all, held separate rather than forced into a repo."
                               if a["noWriteSessions"] else "")))
        parts.append(table([("Repository", False), ("Cost", True), ("Share", True), ("Edits", True)],
                           [[esc(name), usd(cost), share_cell(_div(cost * 100, a["cost"])),
                             "—" if blank else num(a["edits"].get(name, 0))] for name, cost, blank in rows]))
        if len(a["reposRanked"]) >= 2:
            (r1, c1), (r2, c2) = a["reposRanked"][0], a["reposRanked"][1]
            parts.append(verdict(
                "The centre",
                f"<strong>{N(r1)} is the centre of gravity at {N(usd(c1))} — "
                f"{pct(_div(c1 * 100, a['cost']))} and {num(a['edits'].get(r1, 0))} edits</strong>, "
                f"with {N(r2)} second at {N(usd(c2))} and {num(a['edits'].get(r2, 0))} edits."))
        elif a["reposRanked"]:
            r1, c1 = a["reposRanked"][0]
            parts.append(verdict("The centre", f"<strong>Every edit landed in {N(r1)}</strong>, "
                                 f"credited {N(usd(c1))} — {pct(_div(c1 * 100, a['cost']))}."))

    # --- zero-write sessions
    if a["noWriteSessions"]:
        parts.append("<h3>“No file writes” is not “no work”</h3>")
        parts.append(verdict(
            "Do not read", f"That {N(usd(a['noWriteCost']))} band reads as idleness and isn't. Reviewing "
            "someone else's PR, moving a ticket, publishing a document, booking the week and filling the "
            "brain <strong>all leave the local checkout untouched</strong>. None of them show up as an edit.",
            "alarm"))
        if a["outwardTotal"]:
            parts.append(
                f'<p class="lead-p">Counting what those sessions actually <em>did</em> — every call that changed '
                f"something outside the repo — turns the blank band into <strong>{num(a['outwardTotal'])} outward "
                f"actions across {num(a['productiveSessions'])} of the {num(a['noWriteSessions'])} sessions, "
                f"carrying {N(usd(a['productiveCost']))} of the {N(usd(a['noWriteCost']))}</strong>.</p>")
            rows = [{"label": k, "value": v, "fill": "var(--s1)", "text": plural(int(v), "action"),
                     "tip": [k, plural(int(v), "action")]} for k, v in a["outwardRanked"]]
            chart = hbars("ch-nowrite", rows, left=420, right=110, row_h=38,
                          foot=f"OUTWARD ACTIONS FROM THE {num(a['noWriteSessions'])} SESSIONS THAT EDITED NO "
                               f"REPO FILE  ·  {num(a['outwardTotal'])} TOTAL",
                          aria="Outward actions from the zero-write sessions: " + ", ".join(
                              f"{int(v)} {k}" for k, v in a["outwardRanked"]) + ".")
            parts.append(figure(chart, "What the zero-write sessions produced, by kind of outward action."))
            trows = [[esc(k), num(v)] for k, v in a["outwardRanked"]]
            trows.append(["<strong>Total</strong>", f"<strong>{num(a['outwardTotal'])}</strong>"])
            parts.append(table([("What came out of it", False), ("Actions", True)], trows))
        if a["investigationSessions"]:
            parts.append(
                f"<p>That leaves <strong>{plural(a['investigationSessions'], 'session')} and "
                f"{N(usd(a['investigationCost']))} — {pct(_div(a['investigationCost'] * 100, a['cost']))} of the "
                "window</strong> with no recorded output of any kind: reading a codebase, tracing a bug, working "
                "out whether a ticket is real. Their product is a decision, and a decision leaves no file.</p>")
    if a["tickets"]:
        keys = ", ".join(N(t) for t in a["tickets"])
        parts.append(f"<p>The worktree names carry the ticket, too — {plural(len(a['tickets']), 'ticket key')} "
                     f"{'is' if len(a['tickets']) == 1 else 'are'} readable straight off the directory list "
                     f"({keys}) without opening a single transcript.</p>")
    if a["dirsRanked"]:
        parts.append("<h3>Working directory, for completeness</h3>")
        parts.append("<p>Where the edits were made, before each directory is folded onto its repository.</p>")
        total = sum(v for _, v in a["dirsRanked"])
        parts.append(table([("Working directory", False), ("Edits", True), ("Share of edits", True)],
                           [[esc(d), num(v), share_cell(_div(v * 100, total))] for d, v in a["dirsRanked"]]))
    return section("usage", "Usage", "costUSD · byModel · byRepo · noWrite*",
                   f"{usd0(a['cost'])} of list price across {plural(a['active'], 'working day')}", parts)


def _model(a: dict) -> str:
    ranked = a["modelsRanked"]
    if not ranked:
        return ""
    top, top_cost = ranked[0]
    share = _div(top_cost * 100, a["cost"])
    if len(ranked) == 1:
        heading = f"{esc(top)} carries all of the spend"
        lead = f"There is no model mix: every priced turn in the window ran on {esc(top)}."
    else:
        heading = f"{esc(top)} carries {pct(share, 0)} of the spend"
        rest = ", ".join(f"{esc(m)} {N(usd(c))}" for m, c in ranked[1:])
        mix = "There is effectively no model mix. " if share >= 90 else ""
        lead = (f"{mix}{esc(top)} costs {N(usd(top_cost))} of the {N(usd(a['cost']))}; "
                f"the rest is {rest}.")
    rows = []
    for i, (m, c) in enumerate(ranked):
        s = _div(c * 100, a["cost"])
        rows.append({"label": m, "value": s, "fill": SERIES[i % len(SERIES)],
                     "text": f"{usd(c)}  ·  {wshare(s)}", "tip": [m, f"{usd(c)} · {wshare(s)} of spend"]})
    chart = hbars("ch-model", rows, left=120, right=190, row_h=44, axis_suffix="%",
                  foot="SHARE OF LIST-PRICE SPEND, BY MODEL",
                  aria="Cost by model: " + ", ".join(f"{r['label']} {r['text']}" for r in rows) + ".")
    parts = [f'<p class="lead-p">{lead}</p>',
             figure(chart, "Cost share per model, with dated variants of one model folded together.")]
    if share >= 50 and len(ranked) > 1:
        parts.append(verdict("Headroom", f"{pct(share)} of spend on one model means exploration, file reading "
                             "and test running bill at the same rate as the judgment calls. Routing the routine "
                             "turns to a cheaper model is the lever this split points at.", "caution"))
    return section("model", "Most used model", "byModel", heading, parts)


def _weekly(a: dict) -> str:
    weeks = a["weeks"]
    peak = max(weeks, key=lambda w: (w["cost"], w["week"]))
    idx = weeks.index(peak)
    if len(weeks) == 1:
        heading = f"One week, {usd0(peak['cost'])}"
    elif idx >= 2 and peak["cost"] > weeks[idx - 1]["cost"] + weeks[idx - 2]["cost"]:
        heading = f"The week of {esc(month_day(peak['from']))} cost more than the two before it"
    else:
        heading = f"The week of {esc(month_day(peak['from']))} cost the most, at {usd0(peak['cost'])}"
    first, last = weeks[0], weeks[-1]
    lead = (f"The week of {esc(month_day(peak['from']))} hit {N(usd(peak['cost']))} in "
            f"<strong>{words(peak['active'])} active day{'s' if peak['active'] != 1 else ''}</strong>, at "
            f"{N(rate(peak['rate']))} per turn.")
    if len(weeks) > 1:
        change = _div((last["cost"] - first["cost"]) * 100, first["cost"])
        lead += (f" Across the window burn went from {N(usd(first['cost']))} ({esc(first['week'])}) to "
                 f"{N(usd(last['cost']))} ({esc(last['week'])}), "
                 f"{'up' if change >= 0 else 'down'} {pct(abs(change), 0)}.")
    W, H, L, R, T, B = 1040, 290, 58, 18, 44, 58
    pw, ph = W - L - R, H - T - B
    top, ticks = nice_axis(max(w["cost"] for w in weeks))
    step = ticks[1] - ticks[0]
    body = []
    for v in ticks:
        y = T + ph - v / top * ph
        body.append(line(L, y, W - R, y))
        body.append(label(L - 9, y + 4, tick_text(v, step, "$"), "end", 10.5))
    band = pw / len(weeks)
    bw = min(132, band * 0.5)
    small = len(weeks) > 12
    for i, w in enumerate(weeks):
        cx = L + band * i + band / 2
        h = w["cost"] / top * ph
        y = T + ph - h
        body.append(rect(cx - bw / 2, y, bw, h, "var(--s1)", 3,
                         tip=[f"{w['week']} · week of {w['from']}", usd(w["cost"]),
                              f"{plural(w['sessions'], 'session')} · {num(w['turns'])} turns",
                              f"{plural(w['active'], 'active day')} · {rate(w['rate'])}/turn"]))
        if not small:
            body.append(label(cx, y - 24, usd(w["cost"]), "middle", 15, "var(--ink)", 700))
            body.append(label(cx, y - 10, f"{rate(w['rate'])}/turn", "middle", 10.5))
            body.append(label(cx, T + ph + 20, month_day(w["from"]), "middle", 12, "var(--ink)", 500))
            body.append(label(cx, T + ph + 35, f"{plural(w['active'], 'active day')}", "middle", 10))
        else:
            ly = T + ph + 18
            body.append(label(cx, ly, w["week"], "middle", 10, "var(--muted)", 400,
                              f' transform="rotate(-42 {n(cx)} {n(ly)})"'))
    body.append(line(L, T + ph, W - R, T + ph, "var(--ink-2)", 1.5))
    chart = svg("ch-week", W, H, "Weekly cost: " + ", ".join(
        f"{w['week']} {usd(w['cost'])}" for w in weeks) + ".", body)
    parts = [f'<p class="lead-p">{lead}</p>',
             figure(chart, "Cost per ISO week, with cost-per-turn printed above each bar. The rate is "
                    "recomputed from the week's own cost and turns, so weeks of different size compare.")]
    parts.append(table([("Week", False), ("Week of", False), ("Cost", True), ("Sessions", True), ("Turns", True),
                        ("Active days", True), ("$/turn", True)],
                       [[esc(w["week"]), esc(month_day(w["from"])), usd(w["cost"]), num(w["sessions"]),
                         num(w["turns"]), num(w["active"]), rate(w["rate"])] for w in weeks]))
    priced = [w for w in weeks if w["turns"]]
    if len(priced) > 1:
        cheap = min(priced, key=lambda w: (w["rate"], w["week"]))
        busiest = max(weeks, key=lambda w: (w["sessions"], w["week"]))
        if cheap is busiest:
            parts.append(f"<p>Note the divergence between total and rate. The cheapest week per turn "
                         f"({N(rate(cheap['rate']))}, week of {esc(month_day(cheap['from']))}) is also the week "
                         f"with the <em>most</em> sessions — {num(cheap['sessions'])}. Sessions are not what "
                         "costs money. Session <em>length</em> is.</p>")
        else:
            dear = max(priced, key=lambda w: (w["rate"], w["week"]))
            parts.append(f"<p>Per turn, the window ranged from {N(rate(cheap['rate']))} (week of "
                         f"{esc(month_day(cheap['from']))}) to {N(rate(dear['rate']))} (week of "
                         f"{esc(month_day(dear['from']))}). The session-length curve below is where that "
                         "difference comes from.</p>")
    return section("weekly", "Weekly usage", "one usage-week node per bar", heading, parts)


def _compaction(a: dict) -> str:
    order = [b for b in a["bucketOrder"] if a["buckets"][b]["sessions"]]
    if not order:
        return ""
    B = a["buckets"]
    rates = [B[b]["rate"] for b in order]
    peak_i = max(range(len(rates)), key=lambda i: (rates[i], -i))
    rising = peak_i > 0 and rates[peak_i] > rates[0]
    heading = (f"{num(a['noCompact'])} of {num(a['sessions'])} sessions never compacted"
               + (", and the price shows up per turn" if rising else ""))
    listing = ", ".join(f"{N(rate(B[b]['rate']))} {bucket_phrase(b)}" for b in order)
    if len(order) > 1 and all(rates[i] < rates[i + 1] for i in range(len(rates) - 1)):
        lead = f"Cost per turn climbs monotonically with session length: {listing}."
    elif rising and len(order) > 1:
        lead = (f"Cost per turn rises with session length up to the {bucket_label(order[peak_i])} bucket, "
                f"where it peaks: {listing}.")
    else:
        lead = f"Cost per turn by session length: {listing}."
    past = [b for b in order if (bucket_minutes(b) or 0) >= 30]
    if past:
        n_past = sum(B[b]["sessions"] for b in past)
        nc_past = sum(B[b]["noCompact"] for b in past)
        if n_past and nc_past == n_past:
            lead += (f" Every session past 30 minutes in this window — {num(n_past)} of them — is a session "
                     f"where {N('/compact')} never ran.")
        elif n_past:
            lead += (f" Past 30 minutes, {num(nc_past)} of {num(n_past)} sessions never ran "
                     f"{N('/compact')}.")

    W, L, R, T, row_h = 1040, 96, 132, 26, 52
    pw = W - L - R
    max_rate, ticks = nice_axis(max(rates))
    max_tot = max(B[b]["cost"] for b in order) or 1
    step = ticks[1] - ticks[0]
    grid_bottom = T + len(order) * row_h + 4
    body = [line(L + v / max_rate * pw, T - 8, L + v / max_rate * pw, grid_bottom, extra=' opacity="0.6"')
            for v in ticks]
    for i, b in enumerate(order):
        d = B[b]
        y = T + i * row_h
        hgt = 6 + round(d["cost"] / max_tot * 22)
        w = max(3, d["rate"] / max_rate * pw)
        body.append(label(L - 12, y + hgt / 2 + 4, bucket_label(b), "end", 12.5, "var(--ink)", 500))
        body.append(rect(L, y, w, hgt, "var(--s1)",
                         tip=[f"{bucket_label(b)} · {plural(d['sessions'], 'session')}",
                              f"{rate(d['rate'])} per turn",
                              f"{usd(d['cost'])} total · {num(d['turns'])} turns",
                              f"{pct(d['ncPct'])} never compacted"]))
        body.append(label(L + w + 10, y + hgt / 2 + 4, f"{rate(d['rate'])}/turn", "start", 12, "var(--ink)", 700))
        body.append(label(L - 12, y + hgt / 2 + 19, f"{pct(d['ncPct'], 0)} no compact", "end", 10))
        body.append(label(L + 2, y + hgt + 16, f"{usd(d['cost'])} total · {plural(d['sessions'], 'session')}",
                          "start", 10.5))
    by = T + len(order) * row_h + 4
    body.append(line(L, by, W - R, by))
    for v in ticks:
        x = L + v / max_rate * pw
        body.append(label(x, by + 16, tick_text(v, step, "$"), "middle", 10))
    body.append(label(L, by + 34, "COST PER TURN  ·  BAR THICKNESS = TOTAL SPEND IN BUCKET", "start", 10))
    chart = svg("ch-dur", W, by + 46, "Cost per turn by session duration bucket: " + ", ".join(
        f"{rate(B[b]['rate'])} for {bucket_phrase(b)}" for b in order) + ".", body)
    parts = [f'<p class="lead-p">{lead}</p>',
             figure(chart, "Cost per turn by session duration. Bar thickness is proportional to the bucket's "
                    "total spend, so the thickest bar is where the money is. The percentage under each label is "
                    "the share of that bucket's sessions that never compacted.")]
    dear = max(order, key=lambda b: (B[b]["cost"], b))
    parts.append(f"<p>In absolute terms the {N(bucket_label(dear))} bucket carries the most: "
                 f"{N(usd(B[dear]['cost']))} across {plural(B[dear]['sessions'], 'session')} — "
                 f"{pct(_div(B[dear]['cost'] * 100, a['cost']))} of the window.</p>")
    parts.append(table([("Duration", False), ("Sessions", True), ("Total", True), ("Turns", True),
                        ("$/turn", True), ("No compact", True)],
                       [[esc(bucket_label(b)), num(B[b]["sessions"]), usd(B[b]["cost"]), num(B[b]["turns"]),
                         rate(B[b]["rate"]), pct(B[b]["ncPct"])] for b in order]))
    return section("compaction", "Context cleaning vs session duration", "durationBuckets · noCompactionSessions",
                   heading, parts)


COMP_KEYS = [("cacheRead", "Cache read"), ("cacheWrite", "Cache write"), ("output", "Output"),
             ("input", "Input")]


def _recall(a: dict) -> str:
    comp = a["comp"]
    total = a["cost"]
    if not comp or not total:
        return ""
    read = comp.get("cacheRead", 0)
    read_pct = _div(read * 100, total)
    parts = [f'<p class="lead-p">Every turn re-sends the conversation so far. The cache makes that cheap per '
             f"token and expensive in aggregate.</p>",
             f"<h3>Model-context recall — {usd(read)}</h3>"]
    if a["cacheReadTokens"]:
        parts.append(f"<p><strong>{num(a['cacheReadTokens'])} cache-read tokens</strong> over "
                     f"{plural(len(a['weeks']), 'week')}. That is {N(num(_div(a['cacheReadTokens'], a['turns'])))} "
                     f"tokens re-read on <em>every single turn</em>, averaged across all {num(a['turns'])} of them. "
                     f"Cache reads bill at 0.1× the input rate and still land at {pct(read_pct)} of everything.</p>")
    W, H, T, bar_h = 1040, 176, 40, 58
    cols = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"]
    body = []
    x = 0.0
    segs = [(k, name, comp.get(k, 0)) for k, name in COMP_KEYS if comp.get(k, 0) > 0]
    for i, (k, name, v) in enumerate(segs):
        w = v / total * W
        gap = 2 if i > 0 else 0
        rw = max(2, w - gap)
        p = _div(v * 100, total)
        tip = [name, f"{usd(v)} · {pct(p, 2 if p < 1 else 1)}"]
        if k == "cacheRead" and a["cacheReadTokens"]:
            tip.append(f"{num(a['cacheReadTokens'])} tokens")
        body.append(rect(x + gap, T, rw, bar_h, cols[i % 4], 3 if i in (0, len(segs) - 1) else 0, tip=tip))
        text = f"{usd(v)}  ·  {pct(p, 2 if p < 1 else 1)}"
        if w > 150:
            body.append(label(x + gap + 14, T + 26, name, "start", 12.5, "var(--surface)", 700))
            body.append(label(x + gap + 14, T + 44, text, "start", 11.5, "var(--surface)", 500))
        else:
            cx = x + gap + rw / 2
            last = i == len(segs) - 1
            lx, anchor = (W - 4, "end") if last else (cx, "middle")
            body.append(line(cx, T - 6, cx, T, "var(--muted)"))
            body.append(label(lx, T - 22, name, anchor, 11.5, "var(--ink)", 700))
            body.append(label(lx, T - 9, text.replace("  ·  ", " · "), anchor, 10.5))
        x += w
    body.append(label(0, T + bar_h + 20, "$0", "start", 10.5))
    body.append(label(W, T + bar_h + 20, usd(total), "end", 10.5))
    body.append(label(W / 2, T + bar_h + 20, f"TOTAL LIST-PRICE BURN, {plural(len(a['weeks']), 'WEEK')}",
                      "middle", 10))
    chart = svg("ch-comp", W, H, "Cost composition: " + ", ".join(
        f"{name} {usd(v)} or {pct(_div(v * 100, total))}" for _, name, v in segs) + ".", body)
    fresh = comp.get("input", 0)
    parts.append(figure(chart, f"Where every dollar went, by token class. Fresh (uncached) input is "
                        f"{N(usd(fresh))} of {usd(total)}; the rest of the bill is context handling and output.",
                        [(name, cols[i % 4]) for i, (_, name, _v) in enumerate(segs)]))
    return section("recall", "Recall cost", "composition · cacheReadTokens",
                   f"Reading back what we already knew is {pct(read_pct, 0)} of the bill", parts)


def _write(a: dict) -> str:
    comp = a["comp"]
    total = a["cost"]
    if not comp or not total:
        return ""
    cw, out, fresh = comp.get("cacheWrite", 0), comp.get("output", 0), comp.get("input", 0)
    both = cw + out
    parts = [f'<p class="lead-p">Committing context to cache ({N(usd(cw))}) plus generating output '
             f"({N(usd(out))}) is <strong>{usd(both)}, or {pct(_div(both * 100, total))}</strong> of the window.</p>",
             table([("Write path", False), ("Cost", True), ("Share of window", True)],
                   [["Cache write (context → cache)", usd(cw), pct(_div(cw * 100, total))],
                    ["Model output (generation)", usd(out), pct(_div(out * 100, total))],
                    ["Fresh input (uncached)", usd(fresh), pct(_div(fresh * 100, total), 2)]])]
    if cw:
        parts.append(f"<p>For every dollar spent writing context into the cache, "
                     f"{N(usd(_div(comp.get('cacheRead', 0), cw)))} is spent reading it back — which is what makes "
                     "long sessions expensive, and why the leverage is on the read side, not the write side.</p>")
    return section("write", "Write cost", "composition.cacheWrite + output",
                   f"Writing costs {usd0(both)} to the model", parts)


def _causes(a: dict) -> str:
    B = a["buckets"]
    total = a["cost"]
    causes = []
    base = B.get("<30m")
    long_b = [b for b in a["bucketOrder"] if (bucket_minutes(b) or 0) >= LONG_MINUTES and B[b]["sessions"]]
    if base and base["turns"] and long_b:
        lc = sum(B[b]["cost"] for b in long_b)
        lt = sum(B[b]["turns"] for b in long_b)
        ls = sum(B[b]["sessions"] for b in long_b)
        at_base = lt * base["rate"]
        premium = lc - at_base
        if premium > 0:
            causes.append({
                "key": "Long-context premium", "value": premium,
                "note": f"{ls} sessions of 4h+, above the <30m turn rate",
                "h3": f"Long sessions without compaction — ~{usd0(premium)} of premium",
                "p": (f"{words(ls).capitalize() if ls <= 10 else num(ls)} session{'s' if ls != 1 else ''} ran four "
                      f"hours or longer, costing {N(usd(lc))} over {num(lt)} turns. At the cost-per-turn of "
                      f"sub-30-minute sessions they would have cost {N(usd(at_base))}. The <strong>{usd(premium)} "
                      f"difference — {pct(_div(premium * 100, total))} of the entire window</strong> — is the price "
                      "of carrying accumulated context through every subsequent turn.")})
    read = a["comp"].get("cacheRead", 0)
    if read > 0:
        causes.append({
            "key": "Cache reads", "value": read, "note": "re-reading context already sent",
            "h3": f"Re-reading context — {usd0(read)}",
            "p": (f"{pct(_div(read * 100, total))} of spend is cache reads: the model re-reading what it was already "
                  "given. The cheaper each token, the easier this is to miss — and it scales with how long a session "
                  "has been running, not with how much it is asked to do.")})
    if a["modelsRanked"] and len(a["modelsRanked"]) >= 1:
        top, c = a["modelsRanked"][0]
        s = _div(c * 100, total)
        if s >= 50:
            causes.append({
                "key": f"Spend on {top}", "value": c, "note": f"{pct(s)} of spend on one model",
                "h3": f"Everything runs on {esc(top)} — {pct(s)} of spend",
                "p": (f"{pct(s)} of spend on one model, with no routing by task visible in the split. Exploration, "
                      "file reading, test running and code review bill at the same rate as architecture "
                      "decisions.")})
    if not causes:
        return ""
    causes.sort(key=lambda c: (-c["value"], c["key"]))
    cols = ["var(--s2)", "var(--s3)", "var(--s1)"]
    W, L, R, T, row_h = 1040, 310, 126, 26, 56
    pw = W - L - R
    body = [rect(L, T - 8, pw, row_h * len(causes) - 6, "var(--sunk)")]
    for i, c in enumerate(causes):
        y = T + i * row_h
        w = max(3, c["value"] / total * pw)
        p = _div(c["value"] * 100, total)
        body.append(label(L - 12, y + 16, c["key"], "end", 12.5, "var(--ink)", 500))
        body.append(label(L - 12, y + 31, c["note"], "end", 10))
        body.append(rect(L, y + 2, w, 22, cols[i % 3],
                         tip=[c["key"], usd(c["value"]), f"{pct(p)} of the {usd(total)} window", c["note"]]))
        body.append(label(L + w + 10, y + 18, f"{usd(c['value'])}  ·  {pct(p)}", "start", 12, "var(--ink)", 700))
    by = T + len(causes) * row_h - 4
    body.append(line(L, by, L + pw, by))
    body.append(label(L, by + 17, "$0", "start", 10))
    body.append(label(L + pw, by + 17, f"{usd(total)}  (whole window)", "end", 10))
    chart = svg("ch-cause", W, by + 28, "Spend attributable to each cause: " + ", ".join(
        f"{c['key']} {usd(c['value'])}" for c in causes) + ".", body)
    parts = [figure(chart, f"Spend carried by each cause, against the {usd(total)} total. These overlap — the same "
                    "long session appears in more than one bar — so they do not sum to a saving. Each is a bound "
                    "on one lever.")]
    for i, c in enumerate(causes, 1):
        parts.append(f"<h3>{i} · {c['h3']}</h3>")
        parts.append(f"<p>{c['p']}</p>")
    heading = (f"{words(len(causes)).capitalize()} cause{'s' if len(causes) != 1 else ''}, in order of how much "
               f"{'they carry' if len(causes) != 1 else 'it carries'}")
    return section("cause", "What's causing high usage", "durationBuckets · composition · byModel", heading, parts)


def _ceiling(a: dict) -> str:
    stops = a["stops"]
    parts = []
    if stops:
        s = stops[-1]
        windows = sorted({window_label(x["window"]) for x in stops})
        wname = window_label(s["window"])
        overage = s["overage"] or "unknown"
        heading = f"A {esc(wname)} window with overage {esc(overage)}"
        when = s["at"][:16].replace("T", " ")
        fields = [f"{N('window: ' + s['window'])}" if s["window"] else "",
                  f"{N('overage: ' + s['overage'])}" if s["overage"] else "",
                  f"{N('reason: ' + s['reason'])}" if s["reason"] else ""]
        text = (f"<strong>Rate limit hit</strong> in session {N(s['session'])} — "
                + ", ".join(f for f in fields if f)
                + (f". {words(s['hits']).capitalize()} refusals landed within a minute of each other, counted as "
                   "one stop." if s["hits"] > 1 else "."))
        parts.append(verdict(when + " UTC", text, "alarm"))
        stop_weeks = sorted({x["at"][:10] for x in stops})
        parts.append(f'<p class="lead-p">The work stopped {words(len(stops))} time{"s" if len(stops) != 1 else ""} '
                     f"on a rate limit in this window ({', '.join(N(d) for d in stop_weeks)}). The window that "
                     f"fired {'was' if len(windows) == 1 else 'were'} the <strong>{' and '.join(windows)}</strong> "
                     f"{'one' if len(windows) == 1 else 'ones'}. When it fills, work stops mid-task; it does not "
                     "degrade or queue.</p>")
        items = [f"<li><strong>The {esc(wname)} window</strong> — the one that fired most recently. It binds on "
                 "burst intensity, not on total volume.</li>"]
        if s["overage"]:
            items.append(f"<li><strong>Overage {esc(s['overage'])}</strong>"
                         + (f" ({N(s['reason'])})" if s["reason"] else "")
                         + (" — there is no paid-through path past the window; only waiting works.</li>"
                            if s["overage"] == "rejected" else ".</li>"))
        items.append(f"<li><strong>Working days, not quota</strong> — {num(a['active'])} of {num(a['calendar'])} "
                     "calendar days had any usage at all, so the totals here reflect a working week, not a ceiling "
                     "approached smoothly.</li>")
        parts.append(f"<h3>{words(len(items)).capitalize()} constraints, distinct from each other</h3>")
        parts.append("<ul>\n" + "\n".join(items) + "\n</ul>")
        rows = [[esc(x["at"][:16].replace("T", " ")), esc(x["session"]), esc(window_label(x["window"])),
                 esc(x["overage"] or "—"), esc(x["reason"] or "—"), num(x["hits"])] for x in stops]
        parts.append(table([("When (UTC)", False), ("Session", False), ("Window", False), ("Overage", False),
                            ("Reason", False), ("Refusals", True)], rows))
    else:
        heading = f"No hard stop in {plural(len(a['weeks']), 'week')}"
        parts.append(verdict("Clear", f"No rate-limit refusal is recorded in the window. {num(a['active'])} of "
                             f"{num(a['calendar'])} calendar days had usage; whatever the ceiling is, this window "
                             "did not reach it.", "ok"))
    return section("ceiling", "What's preventing higher usage", "rateLimits", heading, parts)


def _assertivity(a: dict) -> str:
    if not a["typed"]:
        return ""
    fan = _div(a["turns"], a["typed"])
    per = _div(a["cost"], a["typed"])
    parts = [f'<p class="lead-p">One human instruction produces <strong>{fan:.1f} agent turns</strong> and costs '
             f"{N(usd(per))}. The count of turns is not the count of prompts: tool results and subagent messages are "
             "turns too, and only the typed ones are a person asking for something.</p>"]
    pts = [w for w in a["weeks"] if w["typed"]]
    if len(pts) > 1:
        W, H, L, R, T, B = 1040, 330, 64, 26, 26, 56
        pw, ph = W - L - R, H - T - B
        xmax, xt = nice_axis(max(w["fanout"] for w in pts) * 1.12)
        ymax, yt = nice_axis(max(w["perTyped"] for w in pts) * 1.12)
        body = []

        def X(v):
            return L + v / xmax * pw

        def Y(v):
            return T + ph - v / ymax * ph
        ys = yt[1] - yt[0]
        xs = xt[1] - xt[0]
        for v in yt:
            body.append(line(L, Y(v), L + pw, Y(v)))
            body.append(label(L - 9, Y(v) + 4, tick_text(v, ys, "$"), "end", 10.5))
        for v in xt:
            body.append(line(X(v), T, X(v), T + ph, extra=' opacity="0.55"'))
            body.append(label(X(v), T + ph + 20, tick_text(v, xs, "", "×"), "middle", 10.5))
        cmax = max(w["cost"] for w in pts) or 1
        placed: list[tuple[float, float, float, float]] = []
        for w in pts:
            r = 6 + math.sqrt(w["cost"] / cmax) * 14
            cx, cy = X(w["fanout"]), Y(w["perTyped"])
            tip = [w["week"], f"{usd(w['cost'])} week", f"{w['fanout']:.1f} turns per typed prompt",
                   f"{usd(w['perTyped'])} per typed prompt"]
            body.append(f'<circle class="hit" tabindex="0" cx="{n(cx)}" cy="{n(cy)}" '
                        f'r="{n(r)}" fill="var(--s3)" opacity="0.78" stroke="var(--surface)" stroke-width="2">'
                        f'<title>{esc(chr(10).join(tip))}</title></circle>')
            if len(pts) <= 12:
                right = cx > L + pw * 0.8
                lw = len(w["week"]) * 6.6
                lx = cx - (r + 6) if right else cx + r + 6
                x0 = lx - lw if right else lx
                ly = cy + 4
                # Nudge a label down until it clears the ones already placed.
                while any(x0 < bx + bw and bx < x0 + lw and ly - 11 < by_ + 13 and by_ - 11 < ly + 2
                          for bx, by_, bw, _ in placed):
                    ly += 14
                placed.append((x0, ly, lw, 0))
                body.append(label(lx, ly, w["week"], "end" if right else "start", 10.5, "var(--ink)", 500))
        body.append(line(L, T + ph, L + pw, T + ph, "var(--ink-2)", 1.5))
        body.append(label(L + pw, T + ph + 38, "AGENT TURNS PER TYPED PROMPT →", "end", 10))
        body.append(label(L - 9, T - 8, "COST PER TYPED PROMPT", "start", 10))
        body.append(label(L + pw, T + ph + 52, "dot size = total week cost", "end", 10))
        chart = svg("ch-assert", W, H, "Cost per typed prompt against turns per typed prompt, one dot per week.", body)
        parts.append(figure(chart, "Each dot is one week. Horizontal: how many agent turns one typed prompt produced. "
                            "Vertical: what that prompt cost. Fan-out inside long sessions is what bills."))
        lo = min(pts, key=lambda w: (w["perTyped"], w["week"]))
        hi = max(pts, key=lambda w: (w["perTyped"], w["week"]))
        parts.append(f"<p>The cost per typed prompt ranged from {N(usd(lo['perTyped']))} ({esc(lo['week'])}, "
                     f"{lo['fanout']:.1f} turns each) to {N(usd(hi['perTyped']))} ({esc(hi['week'])}, "
                     f"{hi['fanout']:.1f} turns each).</p>")
    parts.append(table([("Week", False), ("Typed", True), ("Turns", True), ("Turns / typed", True),
                        ("$ / typed", True)],
                       [[esc(w["week"]), num(w["typed"]), num(w["turns"]), f"{w['fanout']:.1f}×",
                         usd(w["perTyped"])] for w in pts]))
    return section("assertivity", "Prompt assertivity", f"typedPrompts · n = {num(a['typed'])}",
                   f"{num(a['typed'])} typed prompts drove {num(a['turns'])} turns", parts)


def _also(a: dict) -> str:
    items = []
    if a["typed"]:
        items.append(("“Prompts” in the tooling means turns",
                      f"The raw count is {N(num(a['turns']))} prompts. Only {N(num(a['typed']))} were typed by a "
                      "person; the rest are tool results and subagent messages counted as user-role turns. Any "
                      f"per-prompt figure quoted from the raw count is understated by "
                      f"<strong>{_div(a['turns'], a['typed']):.1f}×</strong>. Every per-prompt number in this "
                      "report uses the typed count."))
    if a["unpriced"]:
        names = ", ".join(N(k) for k in sorted(a["unpriced"]))
        cost = sum(a["unpriced"].values())
        items.append(("A model with no published price",
                      f"The usage names {names}, which carries no published pricing. It is shown at "
                      f"{N(usd(cost))} and left out of the model split — worth knowing before quoting a total "
                      "from a window where it appears."))
    if a["dirsRanked"] and a["totalEdits"]:
        d, v = a["dirsRanked"][0]
        items.append(("One working directory took the most edits",
                      f"{N(d)} alone took {num(v)} of {num(a['totalEdits'])} file edits "
                      f"({pct(_div(v * 100, a['totalEdits']))})."))
    if not items:
        return ""
    heading = (f"{words(len(items)).capitalize()} thing{'s' if len(items) != 1 else ''} worth knowing that "
               "nobody asked about")
    parts = []
    for h, p in items:
        parts.append(f"<h3>{h}</h3>\n<p>{p}</p>")
    return section("also", "Also in the data", "usage-week metrics", heading, parts)


def _footer(a: dict) -> str:
    ids = [w["week"] for w in a["weeks"]]
    src = ", ".join(f"<code>{esc(s)}</code>" for s in a["sources"]) or "whatever ingests them"
    method = (f"Generated by <code>scripts/usage_report.py</code> from {plural(len(ids), 'usage-week node')} in the "
              f"brain graph (<code>{esc(ids[0])}</code> … <code>{esc(ids[-1])}</code>), with no model call: the same "
              f"weeks always render the same page, byte for byte. Each node carries one ISO week of metrics, written "
              f"by {src}. Weeks are summed; every rate (cost per turn, per typed prompt, cache read per turn) is "
              "recomputed from the summed totals rather than averaged across weeks, and the never-compacted share "
              "of a duration bucket is rebuilt as a session count from each week's percentage. The nodes are "
              "weekly, so the week is the finest grain on this page.")
    caveats = ["All dollar figures are <strong>published list price</strong> — raw per-token model rates, not what "
               "a subscription charges. Read them as relative weight and quota burn."]
    if a["reposRanked"]:
        caveats.append("Repository attribution splits a session that edited several repositories by edit count; a "
                       "session that edited none is held apart as “no file writes”.")
    caveats.append("Cause figures are bounds under a counterfactual rate, not realised savings, and they overlap "
                   "each other.")
    if a["stops"]:
        caveats.append("Rate-limit refusals in one session and window within a minute of each other count as one "
                       "stop.")
    caveats.append("Never-compacted shares are rebuilt from percentages rounded to one decimal, so a bucket's "
                   "count can be off by one in a large week.")
    return ("<footer>\n  <h3>Method</h3>\n  <p>" + method + "</p>\n  <h3>Caveats worth carrying</h3>\n  <ul>\n"
            + "\n".join(f"    <li>{c}</li>" for c in caveats) + "\n  </ul>\n</footer>")


def _empty(selected_range: str) -> str:
    where = f" in the selected range ({esc(selected_range)})" if selected_range else ""
    body = ('<header class="mast">\n  <div class="kicker">\n    <span>usage-week nodes&nbsp;·&nbsp;none'
            f'{" in range" if selected_range else " stored"}</span>\n  </div>\n'
            "  <h1>Where the Quota Went</h1>\n"
            f'  <p class="dek">There is no usage to report{where} yet.</p>\n</header>\n\n'
            '<section id="empty">\n  <p class="eyebrow">Nothing to draw <span class="src">usage-week</span></p>\n'
            "  <h2>No usage weeks in the brain</h2>\n\n"
            + verdict("How it fills",
                      "This report is drawn from nodes of type <strong>usage-week</strong> — one per ISO week, "
                      "whose props carry <code>week</code>, <code>from</code>, <code>to</code> and a "
                      "<code>metrics</code> object. Whatever writes them (a weekly ingest of your Claude Code "
                      "usage) lives outside the plugin; once a week is stored, this page fills in on the next "
                      "render.")
            + "\n</section>")
    return page("Where the Quota Went", body)


# --------------------------------------------------------------------------- #
# the entry point
# --------------------------------------------------------------------------- #

def render(weeks: list[dict], selected_range: str = "") -> str:
    """The report for these weeks, as one self-contained HTML string. Pure."""
    if not weeks:
        return _empty(selected_range)
    a = aggregate(weeks)
    body = "\n\n".join(p for p in [
        _masthead(a), _usage(a), _model(a), _weekly(a), _compaction(a), _recall(a), _write(a),
        _causes(a), _ceiling(a), _assertivity(a), _also(a), _footer(a)] if p)
    return page("Where the Quota Went", body)


def report(db: str | Path | None = None, last: int | None = None,
           start: str | None = None, end: str | None = None) -> dict:
    """Read, select and render — what the CLI and the API both call."""
    stored = load_weeks(db)
    chosen = select_weeks(stored, last, start, end)
    rng = ""
    if not chosen and stored and (start or end or last):
        rng = f"{start or stored[0]['week']} → {end or stored[-1]['week']}"
    return {"ok": True, "weeks": available(stored), "selected": [w["week"] for w in chosen],
            "html": render(chosen, rng)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", help="the graph database (default: the brain's graph.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="render the report as HTML")
    r.add_argument("--weeks", type=int, help="only the newest N stored weeks (after --from/--to)")
    r.add_argument("--from", dest="start", help="first ISO week, inclusive (YYYY-Www)")
    r.add_argument("--to", dest="end", help="last ISO week, inclusive (YYYY-Www)")
    r.add_argument("--out", help="write here instead of stdout")
    sub.add_parser("weeks", help="list the stored weeks as JSON")
    args = ap.parse_args(argv)

    if args.cmd == "weeks":
        print(json.dumps({"ok": True, "weeks": available(load_weeks(args.db))}, indent=2))
        return 0
    try:
        res = report(args.db, args.weeks, args.start, args.end)
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        return 2
    if args.out:
        Path(args.out).write_text(res["html"], encoding="utf-8")
        print(json.dumps({"ok": True, "out": args.out, "selected": res["selected"],
                          "bytes": len(res["html"].encode("utf-8"))}))
    else:
        sys.stdout.write(res["html"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
