#!/usr/bin/env python3
"""Weekly recall-quality report: is injected context getting more useful?

    python3 scripts/recall_weekly.py                 # last 7 days, writes report + history line
    python3 scripts/recall_weekly.py --days 7 --dry-run   # print the markdown, write nothing

Runs `graph.py recall-report` for the window, appends one JSON line to a history
file OUTSIDE the repo (it holds real note titles), reads the previous line and
writes a markdown report with the verdict first, the metric deltas, the
never-matched notes, the token cost per used injection, the next changes to
try, and the commits that landed in the window (so a change can be linked to
its effect next week).

Verdict rule, on `rate_used` (prompts whose answer matched an injected note by
words / prompts with recall injected):
  - fewer than MIN_INJECTIONS injections in the window: "too little data";
  - no previous line, or the previous line covers a window of another length:
    "baseline";
  - delta >= +FLAT_BAND: better; <= -FLAT_BAND: worse; otherwise flat.

A run on the same day as the last history line replaces that line, so a retry
or a manual run next to the scheduled one never becomes its own "last week".

Stdlib only, like the rest of `scripts/`. Every file names its encoding.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRAPH_PY = HERE / "graph.py"
REPO = HERE.parent
UTF8 = "utf-8"

DEFAULT_HISTORY = Path("~/.claude/handovers/recall-history.jsonl")
DEFAULT_OUT_DIR = Path("~/.claude/handovers")

MIN_INJECTIONS = 50       # below this the window says "too little data", no verdict
FLAT_BAND = 0.03          # rate_used must move 3 points before it is better/worse
DEMOTE_INJECTED = 3       # a note injected this often and never used is a demotion candidate
BRIEF_LOW_RATE = 0.3      # brief sessions using at least one note, below this: trim the brief
BRIEF_MIN_SESSIONS = 10   # the brief rate means little under this many sessions
TOKENS_HIGH = 2000        # tokens spent per used injection, above this: cut what is injected

# metric key -> (label, how to show it, higher is better?)
METRICS = [
    ("prompts", "Prompts", "int", None),
    ("injected", "Prompts with recall injected", "int", None),
    ("used", "Prompts whose answer used it", "int", True),
    ("rate_used", "Rate used (prompt recall)", "pct", True),
    ("brief_sessions", "Brief: sessions logged", "int", None),
    ("brief_rate", "Brief: rate used", "pct", True),
    ("tokens_per_used_injection", "Tokens per used injection", "num", False),
    ("never_matched_total", "Notes injected, never used", "int", False),
]


# ---------------------------------------------------------------- data in

def run_report(days: int) -> dict:
    """The recall report for the window, from graph.py (read of the real brain)."""
    p = subprocess.run([sys.executable, str(GRAPH_PY), "recall-report", "--days", str(days)],
                       capture_output=True, text=True, encoding=UTF8, errors="replace", check=True)
    return json.loads(p.stdout)


def metrics(report: dict) -> dict:
    """The numbers the history keeps, flat, with the same keys every week."""
    src = report.get("sources") or {}
    brief = src.get("brief") or {}
    pr = src.get("prompt_recall") or {}
    return {
        "prompts": int(report.get("prompts") or 0),
        "injected": int(report.get("injected") or 0),
        # the count behind rate_used: matched by words (an id-only match is not counted)
        "used": int(report.get("used_by_words") or 0),
        "rate_used": float(report.get("rate_used") or 0.0),
        "tokens_est": int(pr.get("tokens_est") or 0) + int(brief.get("tokens_est") or 0),
        "tokens_per_used_injection": float(report.get("tokens_per_used_injection") or 0.0),
        "brief_sessions": int(brief.get("sessions") or 0),
        "brief_rate": float(brief.get("rate_used") or 0.0),
        "never_matched_total": int(report.get("never_matched_total")
                                   if report.get("never_matched_total") is not None
                                   else len(report.get("never_matched") or [])),
    }


def history_entry(report: dict, days: int, now: datetime, head: str) -> dict:
    return {
        "date": now.date().isoformat(),
        "run_at": now.isoformat(timespec="seconds"),
        "window_from": (now - timedelta(days=days)).date().isoformat(),
        "window_to": now.date().isoformat(),
        "days": days,
        "git_head": head,
        "metrics": metrics(report),
        "never_matched": [{"id": n.get("id"), "injected": int(n.get("injected") or 0),
                           "title": n.get("title") or ""}
                          for n in (report.get("never_matched") or [])],
    }


def read_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding=UTF8).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a damaged line never stops the weekly run
    return rows


def previous(history: list[dict], today: str) -> dict | None:
    """The last entry from another day: a same-day line is a rerun, not last week."""
    for row in reversed(history):
        if row.get("date") != today:
            return row
    return None


def append_history(path: Path, entry: dict) -> None:
    """Append the line; a line from the same day is replaced instead."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [r for r in read_history(path) if r.get("date") != entry["date"]]
    if len(rows) != len(read_history(path)):
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                        encoding=UTF8)
    with path.open("a", encoding=UTF8) as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- logic

def deltas(prev: dict | None, cur: dict) -> dict:
    """metric -> {"prev", "cur", "delta"}; prev values None when there is no last week."""
    out = {}
    pm = (prev or {}).get("metrics") or {}
    for key, _label, _fmt, _better in METRICS:
        c = cur.get(key, 0)
        p = pm.get(key)
        out[key] = {"prev": p, "cur": c, "delta": None if p is None else round(c - p, 3)}
    return out


def verdict(prev: dict | None, entry: dict, min_injections: int = MIN_INJECTIONS,
            band: float = FLAT_BAND) -> tuple[str, str]:
    """(code, one-line reason). code in: too-little-data, baseline, better, flat, worse.

    `prev` and `entry` are history entries (date, days, metrics)."""
    cur = dict(entry.get("metrics") or {}, date=entry.get("date"), days=entry.get("days"))
    inj = cur.get("injected", 0)
    if inj < min_injections:
        return "too-little-data", f"only {inj} injections in the window (need {min_injections})"
    pm = (prev or {}).get("metrics") or {}
    if "rate_used" not in pm:
        return "baseline", "first report; deltas start next week"
    if pm.get("injected", 0) < min_injections:
        return "baseline", "last week had too little data; deltas start next week"
    if prev.get("days") is not None and cur.get("days") is not None and prev["days"] != cur["days"]:
        return "baseline", (f"last report covered {prev['days']} days, this one {cur['days']}; "
                            "not comparable")
    gap = ""
    try:
        age = (date.fromisoformat(cur["date"]) - date.fromisoformat(prev["date"])).days
        if cur.get("days") and age > 2 * cur["days"]:
            gap = f"; {age} days since the last report, so the windows are not adjacent"
    except (KeyError, TypeError, ValueError):
        pass
    d = round(cur["rate_used"] - pm["rate_used"], 4)  # rates are 3-dp; keep 0.31-0.28 at the band
    pts = f"{d * 100:+.1f} points"
    if d >= band:
        return "better", f"rate used {pts} vs last report{gap}"
    if d <= -band:
        return "worse", f"rate used {pts} vs last report{gap}"
    return "flat", f"rate used {pts} vs last report (within ±{band * 100:.0f} points){gap}"


VERDICT_LINE = {
    "better": "🟢 Better than last week",
    "flat": "🟡 Flat vs last week",
    "worse": "🔴 Worse than last week",
    "baseline": "⚪ Baseline",
    "too-little-data": "⚪ Too little data",
}


def repeat_offenders(history: list[dict], never: list[dict]) -> list[dict]:
    """The never-matched notes, with how many earlier reports also listed them."""
    seen: dict[str, int] = {}
    for row in history:
        for n in row.get("never_matched") or []:
            seen[n.get("id")] = seen.get(n.get("id"), 0) + 1
    return [dict(n, weeks_before=seen.get(n.get("id"), 0)) for n in never]


def next_steps(cur: dict, never: list[dict], report: dict) -> list[tuple[str, str]]:
    """1-3 concrete changes, best first: (change, why)."""
    steps: list[tuple[str, str]] = []
    demote = [n for n in never if int(n.get("injected") or 0) >= DEMOTE_INJECTED]
    if demote:
        steps.append((
            f"Demote the {len(demote)} note(s) injected {DEMOTE_INJECTED}+ times and never used "
            "(lower their recall weight or close them)",
            f"they cost tokens on every prompt and the answer never drew on them; "
            f"ids: {', '.join(n['id'] for n in demote[:5])}"))
    bs, br = cur.get("brief_sessions", 0), cur.get("brief_rate", 0.0)
    if bs >= BRIEF_MIN_SESSIONS and br < BRIEF_LOW_RATE:
        steps.append((
            "Trim the session brief (fewer open loops, shorter lines)",
            f"only {br:.0%} of {bs} sessions used anything from it"))
    tpu = cur.get("tokens_per_used_injection", 0.0)
    if tpu > TOKENS_HIGH:
        steps.append((
            "Cut what each injection carries (shorter bodies, fewer notes per prompt)",
            f"{tpu:,.0f} tokens per used injection, above {TOKENS_HIGH:,}"))
    if cur.get("injected", 0) and cur.get("rate_used", 0.0) < 0.2 and len(steps) < 3:
        steps.append((
            "Raise the recall threshold so fewer, closer notes are injected",
            f"rate used is {cur['rate_used']:.0%}; most injections go unused"))
    if not steps:
        steps.append(("Change nothing; re-check next week",
                      "no note is a clear repeat offender and the rates hold"))
    return steps[:3]


def git_log(repo: Path, since: str, until: str) -> list[str]:
    try:
        p = subprocess.run(
            ["git", "-C", str(repo), "log", f"--since={since}", f"--until={until} 23:59:59",
             "--no-merges", "--date=short", "--format=%h %ad %s"],
            capture_output=True, text=True, encoding=UTF8, errors="replace", check=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    return [ln for ln in p.stdout.splitlines() if ln.strip()]


def git_head(repo: Path) -> str:
    try:
        p = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, encoding=UTF8, errors="replace",
                           check=True)
        return p.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


# ---------------------------------------------------------------- render

def _fmt(v, fmt: str) -> str:
    if v is None:
        return "—"
    if fmt == "pct":
        return f"{v * 100:.1f}%"
    if fmt == "int":
        return f"{int(v):,}"
    return f"{v:,.1f}"


def _fmt_delta(v, fmt: str, better) -> str:
    if v is None:
        return "—"
    if fmt == "pct":
        s = f"{v * 100:+.1f} pts"
    elif fmt == "int":
        s = f"{int(v):+,}"
    else:
        s = f"{v:+,.1f}"
    if better is None or v == 0:
        return s
    good = (v > 0) == better
    return f"{s} {'✅' if good else '❌'}"


def render(entry: dict, prev: dict | None, history: list[dict], report: dict,
           commits: list[str], since: str) -> str:
    cur = entry["metrics"]
    code, reason = verdict(prev, entry)
    d = deltas(prev, cur)
    never = repeat_offenders(history, entry["never_matched"])
    steps = next_steps(cur, never, report)
    prev_date = prev.get("date") if prev else None

    L = [f"# Recall weekly — {entry['date']}", "",
         f"**{VERDICT_LINE[code]}** — {reason}.", "",
         f"Window: {entry['window_from']} → {entry['window_to']} ({entry['days']} days). "
         f"Previous report: {prev_date or 'none'}. Repo at `{entry['git_head'] or '?'}`.", "",
         "## Metrics", "",
         "| Metric | Last week | This week | Delta |", "|---|---:|---:|---:|"]
    for key, label, fmt, better in METRICS:
        r = d[key]
        L.append(f"| {label} | {_fmt(r['prev'], fmt)} | {_fmt(r['cur'], fmt)} | "
                 f"{_fmt_delta(r['delta'], fmt, better)} |")
    L += ["", f"Token cost per used injection: **{cur['tokens_per_used_injection']:,.1f}** "
              f"(from {cur['tokens_est']:,} tokens injected). Zero means the window had no "
              "token logging yet.", ""]

    L += ["## Never-matched notes (top 10)", ""]
    if never:
        L += ["| Injected | Seen before | Note |", "|---:|---:|---|"]
        for n in never:
            flag = " 🔁" if n["weeks_before"] else ""
            L.append(f"| {n['injected']} | {n['weeks_before']}{flag} | "
                     f"{n['title'] or n['id']} (`{n['id']}`) |")
    else:
        L.append("None: every injected note was used at least once.")
    L.append("")

    L += ["## Next steps", "", "| # | Change to try | Why |", "|---|---|---|"]
    for i, (change, why) in enumerate(steps, 1):
        L.append(f"| {i} | {change} | {why} |")
    L += ["", f"**Best next change:** {steps[0][0]}. Implement it on its own branch so the "
              "next report shows its effect.", ""]

    L += [f"## Changes since last report (commits since {since[:10]})", ""]
    L += [f"- `{c}`" for c in commits] or ["- none"]
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--history", default=str(DEFAULT_HISTORY),
                    help="JSONL history file, kept OUTSIDE the repo (holds note titles)")
    ap.add_argument("--out-dir", dest="out_dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--repo", default=str(REPO), help="git repo the change log is read from")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="print the markdown; write neither report nor history")
    args = ap.parse_args(argv)

    history_path = Path(os.path.expanduser(args.history))
    out_dir = Path(os.path.expanduser(args.out_dir))
    repo = Path(os.path.expanduser(args.repo))
    now = datetime.now(timezone.utc)

    report = run_report(args.days)
    history = read_history(history_path)
    entry = history_entry(report, args.days, now, git_head(repo))
    prev = previous(history, entry["date"])
    since = (prev.get("run_at") or prev["date"]) if prev else entry["window_from"]
    commits = git_log(repo, since, entry["window_to"])
    md = render(entry, prev, history, report, commits, since)

    if args.dry_run:
        sys.stdout.write(md)
        return 0
    append_history(history_path, entry)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"recall-weekly-{entry['date']}.md"
    out.write_text(md, encoding=UTF8)
    print(json.dumps({"ok": True, "report": str(out), "history": str(history_path),
                      "verdict": verdict(prev, entry)[0]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
