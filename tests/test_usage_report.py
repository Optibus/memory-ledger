#!/usr/bin/env python3
"""The usage report (scripts/usage_report.py): deterministic, true to its data.

    python3 tests/test_usage_report.py

The report is a pure function of the `usage-week` nodes, so this suite pins the
three things that make it trustworthy: the same weeks render the same bytes; the
numbers on the page are the numbers in the nodes (summed, and rates recomputed
from the sums); and a brain with no weeks gets an empty state, not a crash.
It also drives the CLI against a throwaway graph.db, opened read-only.

The fixture is invented — no real usage leaves a home directory (CONTRIBUTING
ground rule 5). Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "usage_report.py"
sys.path.insert(0, str(ROOT / "scripts"))
import usage_report as ur  # noqa: E402

FAILS: list[str] = []


def ck(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def week(tag, frm, to, **m):
    base = {
        "costUSD": 100.0, "sessions": 10, "turns": 1000, "typedPrompts": 40, "activeDays": 5,
        "byModel": {"claude-opus-5": 90.0, "claude-sonnet-5": 10.0},
        "composition": {"cacheRead": 60.0, "cacheWrite": 20.0, "output": 19.9, "input": 0.1},
        "cacheReadTokens": 100_000_000,
        "durationBuckets": [
            {"bucket": "<30m", "sessions": 6, "costUSD": 20.0, "turns": 400, "noCompactPct": 50.0},
            {"bucket": "30m-2h", "sessions": 2, "costUSD": 30.0, "turns": 300, "noCompactPct": 100.0},
            {"bucket": "4-8h", "sessions": 2, "costUSD": 50.0, "turns": 300, "noCompactPct": 100.0},
        ],
        "noCompactionSessions": 7,
        "byRepo": {"alpha": 60.0, "beta": 20.0},
        "editsByRepo": {"alpha": 30, "beta": 10}, "totalEdits": 40,
        "workingDirs": {"alpha": 20, "alpha-wt-feature": 10, "beta": 10},
        "noWriteSessions": 4, "noWriteCostUSD": 20.0,
        "noWriteOutward": {"Brain writes (tasks, notes, decisions, rules)": 12, "Slack messages sent": 3},
        "noWriteOutwardTotal": 15, "noWriteProductiveSessions": 3, "noWriteProductiveCostUSD": 15.0,
        "investigationOnlySessions": 1, "investigationOnlyCostUSD": 5.0,
        "tickets": ["ABC-1"], "rateLimits": [],
    }
    base.update(m)
    return {"id": f"usage-week_{tag}", "week": tag, "from": frm, "to": to,
            "source": "fixture", "metrics": base}


WEEKS = [
    week("2026-W01", "2025-12-29", "2026-01-04"),
    week("2026-W02", "2026-01-05", "2026-01-11", costUSD=300.0, sessions=20, turns=2000,
         typedPrompts=50, byModel={"claude-opus-5": 250.0, "claude-haiku-4-5-20251001": 50.0,
                                   "<synthetic>": 0},
         composition={"cacheRead": 200.0, "cacheWrite": 50.0, "output": 49.8, "input": 0.2},
         byRepo={"alpha": 100.0, "gamma": 150.0}, editsByRepo={"alpha": 10, "gamma": 40},
         totalEdits=50, noWriteCostUSD=50.0,
         rateLimits=[{"at": "2026-01-07T10:00:00.500Z", "session": "s1", "window": "five_hour",
                      "overage": "rejected", "reason": "zero_credit"},
                     {"at": "2026-01-07T10:00:01.900Z", "session": "s1", "window": "five_hour",
                      "overage": "rejected", "reason": "zero_credit"}]),
    week("2026-W03", "2026-01-12", "2026-01-18", costUSD=200.0),
]


def make_db(path: Path, weeks) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE nodes (id TEXT PRIMARY KEY, type TEXT, title TEXT, props TEXT)")
    for w in weeks:
        props = {"kind": "usage", "week": w["week"], "from": w["from"], "to": w["to"],
                 "metrics": w["metrics"], "source": w["source"]}
        con.execute("INSERT INTO nodes VALUES (?,?,?,?)",
                    (w["id"], "usage-week", w["week"], json.dumps(props)))
    con.execute("INSERT INTO nodes VALUES ('t1','task','not a week','{}')")
    con.commit()
    con.close()


def test_determinism():
    print("\n== the same weeks render the same bytes ==")
    a, b = ur.render(WEEKS), ur.render([dict(w) for w in WEEKS])
    ck("rendering twice is byte-identical", a == b)
    shuffled = ur.parse_rows([(w["id"], json.dumps({**w, "metrics": w["metrics"]}))
                              for w in reversed(WEEKS)])
    ck("row order in the database does not change the page", ur.render(shuffled) == a)
    ck("it is a whole HTML document", a.startswith("<!doctype html>") and a.rstrip().endswith("</html>"))
    ck("with no clock in it: no year that is not in the data",
       "2027" not in a and "2024" not in a)


def test_numbers():
    print("\n== the numbers on the page are the numbers in the nodes ==")
    agg = ur.aggregate(WEEKS)
    ck("cost is summed", round(agg["cost"], 2) == 600.0, str(agg["cost"]))
    ck("sessions and turns are summed", agg["sessions"] == 40 and agg["turns"] == 4000)
    ck("dated model variants fold under one name",
       round(agg["models"]["Haiku 4.5"], 2) == 50.0 and "Opus 5" in agg["models"])
    ck("an unpriced model is kept apart", "<synthetic>" in agg["unpriced"]
       and "<synthetic>" not in agg["models"])
    b = agg["buckets"]["4-8h"]
    ck("a bucket's rate is recomputed from summed cost and turns",
       b["cost"] == 150.0 and b["turns"] == 900 and abs(b["rate"] - 150 / 900) < 1e-12)
    ck("never-compacted is rebuilt as a count", agg["buckets"]["<30m"]["noCompact"] == 9)
    ck("two refusals a second apart are one stop", len(agg["stops"]) == 1
       and agg["stops"][0]["hits"] == 2)
    ck("repos are ranked by cost", [r for r, _ in agg["reposRanked"]] == ["alpha", "gamma", "beta"])

    page = ur.render(WEEKS)
    for needle, what in [
        ("$600.00", "the total"),
        ("$600 of list price across 15 working days", "the templated usage heading"),
        ("Opus 5 carries 72% of the spend", "the templated model heading"),
        ("The week of Jan 05 cost the most, at $300", "the templated weekly heading"),
        ("21 of 40 sessions never compacted", "the templated compaction heading"),
        ("A five-hour window with overage rejected", "the templated rate-limit heading"),
        ("130 typed prompts drove 4,000 turns", "the templated assertivity heading"),
        ("$0.150/turn", "the 30m–2h rate on the weekly bar"),
        ("ABC-1", "a ticket key from the data"),
        ("45 outward actions", "the zero-write output, summed"),
    ]:
        ck(f"{what}: {needle}", needle in page)
    ck("the cache-read share is recomputed: 320 of 600 is 53.3%", "53.3%" in page)
    no_stop = ur.render(WEEKS[:1])
    ck("with no rate-limit hit the section says so, not an invented stop",
       "No hard stop in 1 week" in no_stop and "Rate limit hit" not in no_stop)
    ck("no invented narrative: no commit log section", 'id="actions"' not in page)


def test_selection():
    print("\n== the window ==")
    ck("no bounds is every week", [w["week"] for w in ur.select_weeks(WEEKS)] ==
       ["2026-W01", "2026-W02", "2026-W03"])
    ck("--weeks N is the newest N", [w["week"] for w in ur.select_weeks(WEEKS, 2)] ==
       ["2026-W02", "2026-W03"])
    ck("from/to is inclusive", [w["week"] for w in ur.select_weeks(WEEKS, None, "2026-W02", "2026-W02")] ==
       ["2026-W02"])
    try:
        ur.select_weeks(WEEKS, None, "2026-1")
        ck("a malformed tag is refused", False)
    except ValueError:
        ck("a malformed tag is refused", True)
    one = ur.render(WEEKS[:1])
    ck("one week renders, with its own wording", "One week, $100" in one)


def test_empty():
    print("\n== no weeks is an empty state, not an error ==")
    page = ur.render([])
    ck("the empty page says so", "No usage weeks in the brain" in page and "usage-week" in page)
    ck("and draws no chart", "<svg" not in page)
    ck("an out-of-range selection names the range",
       "2030-W01" in ur.render([], "2030-W01 → 2030-W02"))
    with tempfile.TemporaryDirectory() as d:
        ck("a missing database is no weeks", ur.load_weeks(Path(d) / "nope.db") == [])


def test_cli():
    print("\n== the CLI, against a throwaway graph.db ==")
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "graph.db"
        make_db(db, WEEKS)
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        out = Path(d) / "r.html"

        def run(*args):
            return subprocess.run([sys.executable, str(SCRIPT), "--db", str(db), *args],
                                  capture_output=True, text=True)

        p = run("render", "--out", str(out))
        res = json.loads(p.stdout or "{}")
        ck("render --out writes the page", p.returncode == 0 and out.is_file()
           and res.get("selected") == ["2026-W01", "2026-W02", "2026-W03"], p.stderr[:200])
        ck("and it is the page render() returns",
           out.read_text(encoding="utf-8") == ur.render(ur.load_weeks(db)))
        p2 = run("render")
        ck("stdout and --out agree byte for byte", p2.stdout == out.read_text(encoding="utf-8"))
        p3 = run("render", "--weeks", "1")
        ck("--weeks 1 renders only the newest", "One week, $200" in p3.stdout)
        p4 = run("render", "--from", "2026-W02", "--to", "2026-W02")
        ck("--from/--to renders that range", "One week, $300" in p4.stdout)
        p5 = run("render", "--from", "bad")
        ck("a bad tag exits non-zero with the reason", p5.returncode == 2 and "ISO week" in p5.stderr)
        p6 = run("weeks")
        ck("weeks lists what is stored", [w["week"] for w in json.loads(p6.stdout)["weeks"]] ==
           ["2026-W01", "2026-W02", "2026-W03"])
        ck("the database was never written", hashlib.sha256(db.read_bytes()).hexdigest() == before
           and not Path(str(db) + "-journal").exists())

        env = {**os.environ, "SECOND_BRAIN_HOME": d, "MEMORY_LEDGER_HOME": d}
        env.pop("SECOND_BRAIN_DB", None)
        env.pop("MEMORY_LEDGER_DB", None)
        p7 = subprocess.run([sys.executable, str(SCRIPT), "render"], capture_output=True, text=True, env=env)
        ck("with no --db it reads the brain home's graph.db", "$600.00" in p7.stdout, p7.stderr[:200])


def main():
    test_determinism()
    test_numbers()
    test_selection()
    test_empty()
    test_cli()
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
