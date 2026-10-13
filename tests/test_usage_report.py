#!/usr/bin/env python3
"""The usage report (scripts/usage_report.py): deterministic, true to its data.

    python -m pytest tests/test_usage_report.py

The report is a pure function of the `usage-week` nodes, so this suite pins the
three things that make it trustworthy: the same weeks render the same bytes; the
numbers on the page are the numbers in the nodes (summed, and rates recomputed
from the sums); and a brain with no weeks gets an empty state, not a crash.
It also drives the CLI against a throwaway graph.db, opened read-only.

The fixture is invented — no real usage leaves a home directory (CONTRIBUTING
ground rule 5).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

import usage_report as ur

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "usage_report.py"
WEEK_TAGS = ["2026-W01", "2026-W02", "2026-W03"]


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


@pytest.fixture(scope="module")
def page() -> str:
    return ur.render(WEEKS)


@pytest.fixture(scope="module")
def agg() -> dict:
    return ur.aggregate(WEEKS)


# ---------------------------------------- the same weeks render the same bytes
class TestDeterminism:
    def test_rendering_twice_is_byte_identical(self, page):
        assert page == ur.render([dict(w) for w in WEEKS])

    def test_row_order_in_the_database_does_not_change_the_page(self, page):
        shuffled = ur.parse_rows([(w["id"], json.dumps({**w, "metrics": w["metrics"]}))
                                  for w in reversed(WEEKS)])
        assert ur.render(shuffled) == page

    def test_it_is_a_whole_html_document(self, page):
        assert page.startswith("<!doctype html>")
        assert page.rstrip().endswith("</html>")

    @pytest.mark.parametrize("year", ["2027", "2024"])
    def test_no_clock_in_it_no_year_that_is_not_in_the_data(self, page, year):
        assert year not in page


# ------------------------------- the numbers on the page are the numbers in the nodes
class TestAggregate:
    def test_cost_is_summed(self, agg):
        assert round(agg["cost"], 2) == 600.0

    def test_sessions_and_turns_are_summed(self, agg):
        assert (agg["sessions"], agg["turns"]) == (40, 4000)

    def test_dated_model_variants_fold_under_one_name(self, agg):
        assert round(agg["models"]["Haiku 4.5"], 2) == 50.0
        assert "Opus 5" in agg["models"]

    def test_an_unpriced_model_is_kept_apart(self, agg):
        assert "<synthetic>" in agg["unpriced"]
        assert "<synthetic>" not in agg["models"]

    def test_a_buckets_rate_is_recomputed_from_summed_cost_and_turns(self, agg):
        b = agg["buckets"]["4-8h"]
        assert (b["cost"], b["turns"]) == (150.0, 900)
        assert abs(b["rate"] - 150 / 900) < 1e-12

    def test_never_compacted_is_rebuilt_as_a_count(self, agg):
        assert agg["buckets"]["<30m"]["noCompact"] == 9

    def test_two_refusals_a_second_apart_are_one_stop(self, agg):
        assert len(agg["stops"]) == 1
        assert agg["stops"][0]["hits"] == 2

    def test_repos_are_ranked_by_cost(self, agg):
        assert [r for r, _ in agg["reposRanked"]] == ["alpha", "gamma", "beta"]


PAGE_NEEDLES = [
    ("$600.00", "the total"),
    ("$600 of list price across 15 working days", "the templated usage heading"),
    ("Opus 5 carries 72% of the spend", "the templated model heading"),
    ("The week of Jan 05 cost the most, at $300", "the templated weekly heading"),
    ("21 of 40 sessions never compacted", "the templated compaction heading"),
    ("A five-hour window with overage rejected", "the templated rate-limit heading"),
    ("130 typed prompts drove 4,000 turns", "the templated assertivity heading"),
    ("$0.150/turn", "the 30m-2h rate on the weekly bar"),
    ("ABC-1", "a ticket key from the data"),
    ("45 outward actions", "the zero-write output, summed"),
    ("53.3%", "the cache-read share, recomputed: 320 of 600"),
]


class TestPage:
    @pytest.mark.parametrize("needle, what", PAGE_NEEDLES, ids=[w for _, w in PAGE_NEEDLES])
    def test_the_page_says(self, page, needle, what):
        assert needle in page

    def test_with_no_rate_limit_hit_the_section_says_so_not_an_invented_stop(self):
        no_stop = ur.render(WEEKS[:1])
        assert "No hard stop in 1 week" in no_stop
        assert "Rate limit hit" not in no_stop

    def test_no_invented_narrative_no_commit_log_section(self, page):
        assert 'id="actions"' not in page


# ------------------------------------------------------------------ the window
class TestSelection:
    @staticmethod
    def tags(*args) -> list[str]:
        return [w["week"] for w in ur.select_weeks(WEEKS, *args)]

    def test_no_bounds_is_every_week(self):
        assert self.tags() == WEEK_TAGS

    def test_weeks_n_is_the_newest_n(self):
        assert self.tags(2) == WEEK_TAGS[1:]

    def test_from_to_is_inclusive(self):
        assert self.tags(None, "2026-W02", "2026-W02") == ["2026-W02"]

    def test_a_malformed_tag_is_refused(self):
        with pytest.raises(ValueError):
            ur.select_weeks(WEEKS, None, "2026-1")

    def test_one_week_renders_with_its_own_wording(self):
        assert "One week, $100" in ur.render(WEEKS[:1])


# ------------------------------------- no weeks is an empty state, not an error
class TestEmpty:
    def test_the_empty_page_says_so(self):
        page = ur.render([])
        assert "No usage weeks in the brain" in page and "usage-week" in page

    def test_and_draws_no_chart(self):
        assert "<svg" not in ur.render([])

    def test_an_out_of_range_selection_names_the_range(self):
        assert "2030-W01" in ur.render([], "2030-W01 \u2192 2030-W02")

    def test_a_missing_database_is_no_weeks(self, tmp_path):
        assert ur.load_weeks(tmp_path / "nope.db") == []


# ---------------------------------------- the CLI, against a throwaway graph.db
class TestCli:
    @pytest.fixture
    def db(self, tmp_path) -> Path:
        path = tmp_path / "graph.db"
        make_db(path, WEEKS)
        return path

    @pytest.fixture
    def cli(self, db, run_script):
        def run(*args):
            return run_script(SCRIPT, "--db", db, *args)
        return run

    def test_render_out_writes_the_page_and_it_is_what_render_returns(self, cli, db, tmp_path):
        out = tmp_path / "r.html"
        p = cli("render", "--out", str(out))
        res = json.loads(p.stdout or "{}")
        assert p.returncode == 0 and out.is_file(), p.stderr[:200]
        assert res.get("selected") == WEEK_TAGS
        assert out.read_text(encoding="utf-8") == ur.render(ur.load_weeks(db))

    def test_stdout_and_out_agree_byte_for_byte(self, cli, tmp_path):
        out = tmp_path / "r.html"
        cli("render", "--out", str(out))
        assert cli("render").stdout == out.read_text(encoding="utf-8")

    @pytest.mark.parametrize("args, needle", [
        (("--weeks", "1"), "One week, $200"),
        (("--from", "2026-W02", "--to", "2026-W02"), "One week, $300"),
    ], ids=["newest-only", "range"])
    def test_selection_flags(self, cli, args, needle):
        assert needle in cli("render", *args).stdout

    def test_a_bad_tag_exits_non_zero_with_the_reason(self, cli):
        p = cli("render", "--from", "bad")
        assert p.returncode == 2 and "ISO week" in p.stderr

    def test_weeks_lists_what_is_stored(self, cli):
        assert [w["week"] for w in json.loads(cli("weeks").stdout)["weeks"]] == WEEK_TAGS

    def test_the_database_is_never_written(self, cli, db):
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        cli("render")
        cli("weeks")
        assert hashlib.sha256(db.read_bytes()).hexdigest() == before
        assert not Path(str(db) + "-journal").exists()

    def test_with_no_db_it_reads_the_brain_homes_graph_db(self, brain_home, run_script):
        make_db(brain_home / "graph.db", WEEKS)
        p = run_script(SCRIPT, "render", home=brain_home,
                       env={"MEMORY_LEDGER_HOME": str(brain_home)})
        assert "$600.00" in p.stdout, p.stderr[:200]


def test_usage_summary_windows():
    hours = {"2026-10-02T14": 1.0, "2026-10-02T10": 2.0, "2026-10-02T08": 4.0,
             "2026-10-01T23": 8.0, "2026-09-30T09": 16.0, "2025-12-31T09": 32.0}
    weeks = [{"week": "2026-W40", "from": "2026-09-28", "metrics": {"costUSD": 63.0, "costByAbsHour": hours}},
             {"week": "2026-W35", "from": "2026-08-24", "metrics": {"costUSD": 100.0}}]
    s = ur.usage_summary(weeks, datetime(2026, 10, 2, 14, 30))
    assert s["fiveHours"] == 3.0     # hours 14..10 inclusive
    assert s["today"] == 7.0
    assert s["week"] == 31.0         # Monday 2026-09-28 on
    assert s["month"] == 15.0
    assert s["year"] == 131.0        # the hourless week counts whole, on its first day
    assert s["total"] == 163.0 and s["coarseWeeks"] == 1
