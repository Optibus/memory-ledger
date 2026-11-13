"""The weekly recall report (scripts/recall_weekly.py): deltas, verdict, guard rails.

    python -m pytest tests/test_recall_weekly.py

Pure functions are tested on invented reports; the end-to-end run swaps the
graph.py call for a fake and writes to a throwaway history and output folder,
so no real brain is read and nothing lands in a home directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import recall_weekly as rw

UTF8 = "utf-8"


def report(injected=100, used=28, prompts=500, brief_sessions=0, brief_rate=0.0,
           tokens=0, tpu=0.0, never=None, never_total=None):
    never = never or []
    return {
        "ok": True, "days": 7, "prompts": prompts, "injected": injected, "used": used,
        "used_by_words": used, "rate_used": round(used / injected, 3) if injected else 0.0,
        "never_matched": never,
        "never_matched_total": len(never) if never_total is None else never_total,
        "sources": {"prompt_recall": {"injections": injected, "tokens_est": tokens},
                    "brief": {"sessions": brief_sessions, "rate_used": brief_rate,
                              "tokens_est": 0}},
        "tokens_per_used_injection": tpu,
    }


def entry(**kw):
    return {"date": kw.pop("date", "2026-11-06"), "days": kw.pop("days", 7),
            "metrics": rw.metrics(report(**kw)), "never_matched": []}


def this_week(**kw):
    return entry(date=kw.pop("date", "2026-11-13"), **kw)


# ---------------------------------------------------------------- deltas

def test_deltas_without_previous_week_are_none():
    d = rw.deltas(None, rw.metrics(report()))
    assert d["rate_used"] == {"prev": None, "cur": 0.28, "delta": None}
    assert set(d) == {k for k, *_ in rw.METRICS}


def test_deltas_subtract_last_week():
    d = rw.deltas(entry(used=28), rw.metrics(report(used=35, tpu=120.0, never_total=4)))
    assert d["rate_used"]["delta"] == pytest.approx(0.07)
    assert d["used"]["delta"] == 7
    assert d["tokens_per_used_injection"] == {"prev": 0.0, "cur": 120.0, "delta": 120.0}
    assert d["never_matched_total"]["delta"] == 4


def test_metrics_fall_back_to_the_capped_list_when_total_is_missing():
    rep = report(never=[{"id": "a", "injected": 3, "title": "A"}])
    del rep["never_matched_total"]
    assert rw.metrics(rep)["never_matched_total"] == 1


# ---------------------------------------------------------------- verdict

def test_too_little_data_beats_every_other_verdict():
    code, reason = rw.verdict(entry(used=10), this_week(injected=49, used=40))
    assert code == "too-little-data" and "49" in reason


def test_first_report_is_the_baseline():
    assert rw.verdict(None, this_week())[0] == "baseline"


def test_baseline_again_when_last_week_was_too_thin():
    assert rw.verdict(entry(injected=20, used=5), this_week())[0] == "baseline"


def test_baseline_when_the_windows_differ_in_length():
    code, reason = rw.verdict(entry(days=14), this_week(used=40))
    assert code == "baseline" and "not comparable" in reason


def test_a_gap_between_reports_is_said_in_the_reason():
    code, reason = rw.verdict(entry(date="2026-10-01"), this_week(used=40))
    assert code == "better" and "43 days since the last report" in reason
    assert "days since" not in rw.verdict(entry(), this_week(used=40))[1]


@pytest.mark.parametrize("used, code", [(31, "better"), (30, "flat"), (26, "flat"), (25, "worse")])
def test_verdict_uses_a_three_point_band_on_rate_used(used, code):
    assert rw.verdict(entry(used=28), this_week(used=used))[0] == code


def test_used_is_the_count_behind_rate_used():
    rep = report(used=28)
    rep["used"] = 30  # two id-only matches are in `used` but not in rate_used
    m = rw.metrics(rep)
    assert m["used"] == 28 and m["used"] / m["injected"] == pytest.approx(m["rate_used"])


# ---------------------------------------------------------------- next steps

def test_demotion_candidates_come_first_and_name_ids():
    never = [{"id": "n1", "injected": 5, "title": "x"}, {"id": "n2", "injected": 2, "title": "y"}]
    steps = rw.next_steps(rw.metrics(report(never=never)), never, report(never=never))
    assert steps[0][0].startswith("Demote the 1 note")
    assert "n1" in steps[0][1] and "n2" not in steps[0][1]


def test_low_brief_rate_and_high_token_cost_are_suggested():
    rep = report(brief_sessions=12, brief_rate=0.1, tpu=5000.0)
    steps = rw.next_steps(rw.metrics(rep), [], rep)
    texts = " ".join(s[0] for s in steps)
    assert "Trim the session brief" in texts and "Cut what each injection carries" in texts
    assert len(steps) <= 3


def test_brief_rate_is_ignored_under_ten_sessions():
    rep = report(brief_sessions=3, brief_rate=0.0)
    assert all("brief" not in s[0] for s in rw.next_steps(rw.metrics(rep), [], rep))


def test_nothing_to_change_is_still_a_step():
    rep = report()
    assert rw.next_steps(rw.metrics(rep), [], rep) == [
        ("Change nothing; re-check next week",
         "no note is a clear repeat offender and the rates hold")]


def test_repeat_offenders_count_earlier_reports():
    hist = [{"never_matched": [{"id": "a"}]}, {"never_matched": [{"id": "a"}, {"id": "b"}]}]
    out = rw.repeat_offenders(hist, [{"id": "a", "injected": 3, "title": "A"},
                                     {"id": "c", "injected": 3, "title": "C"}])
    assert [(n["id"], n["weeks_before"]) for n in out] == [("a", 2), ("c", 0)]


# ---------------------------------------------------------------- render + run

def test_render_puts_the_verdict_first_and_keeps_every_section():
    cur = {"date": "2026-11-13", "window_from": "2026-11-06", "window_to": "2026-11-13",
           "days": 7, "git_head": "abc1234", "metrics": rw.metrics(report(used=35)),
           "never_matched": [{"id": "n1", "injected": 4, "title": "Old task"}]}
    md = rw.render(cur, entry(used=28), [entry()], report(used=35), ["abc1234 2026-11-10 feat: x"],
                   "2026-11-06")
    lines = md.splitlines()
    assert lines[0] == "# Recall weekly — 2026-11-13"
    assert lines[2].startswith("**🟢 Better than last week**")
    assert "| Rate used (prompt recall) | 28.0% | 35.0% | +7.0 pts ✅ |" in md
    for h in ("## Metrics", "## Never-matched notes", "## Next steps",
              "## Changes since last report", "**Best next change:**"):
        assert h in md
    assert "Old task (`n1`)" in md and "`abc1234 2026-11-10 feat: x`" in md


def test_render_says_too_little_data_instead_of_a_verdict():
    cur = {"date": "2026-11-13", "window_from": "2026-11-06", "window_to": "2026-11-13",
           "days": 7, "git_head": "", "metrics": rw.metrics(report(injected=12, used=9)),
           "never_matched": []}
    md = rw.render(cur, entry(), [entry()], report(injected=12, used=9), [], "2026-11-06")
    assert "⚪ Too little data" in md and "Better" not in md and "Worse" not in md


def test_main_writes_report_and_appends_history(tmp_path, monkeypatch):
    reps = iter([report(used=28), report(used=35)])
    monkeypatch.setattr(rw, "run_report", lambda days: next(reps))
    monkeypatch.setattr(rw, "git_log", lambda repo, since, until: ["deadbee 2026-11-10 fix: y"])
    monkeypatch.setattr(rw, "git_head", lambda repo: "deadbee")
    hist, out = tmp_path / "h" / "recall-history.jsonl", tmp_path / "out"
    argv = ["--history", str(hist), "--out-dir", str(out), "--repo", str(tmp_path)]

    assert rw.main(argv) == 0
    rows = [json.loads(ln) for ln in hist.read_text(encoding=UTF8).splitlines()]
    assert len(rows) == 1 and rows[0]["metrics"]["used"] == 28 and rows[0]["git_head"] == "deadbee"
    first = next(out.glob("recall-weekly-*.md")).read_text(encoding=UTF8)
    assert "⚪ Baseline" in first and "| — | 28.0% | — |" in first

    # a rerun the same day replaces the line and is still the baseline
    assert rw.main(argv) == 0
    rows = [json.loads(ln) for ln in hist.read_text(encoding=UTF8).splitlines()]
    assert len(rows) == 1 and rows[0]["metrics"]["used"] == 35
    assert "⚪ Baseline" in next(out.glob("recall-weekly-*.md")).read_text(encoding=UTF8)

    # a line from an earlier day is last week; the git log starts at its run time
    rows[0]["date"], rows[0]["run_at"] = "2026-11-06", "2026-11-06T09:00:00+00:00"
    hist.write_text(json.dumps(rows[0]) + "\n", encoding=UTF8)
    seen = {}
    monkeypatch.setattr(rw, "git_log", lambda repo, since, until: seen.setdefault("since", since) and [])
    monkeypatch.setattr(rw, "run_report", lambda days: report(used=45))
    assert rw.main(argv) == 0
    rows = [json.loads(ln) for ln in hist.read_text(encoding=UTF8).splitlines()]
    assert [r["metrics"]["used"] for r in rows] == [35, 45]
    assert seen["since"] == "2026-11-06T09:00:00+00:00"
    second = next(out.glob("recall-weekly-*.md")).read_text(encoding=UTF8)
    assert "🟢 Better than last week" in second and "commits since 2026-11-06" in second


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rw, "run_report", lambda days: report())
    hist = tmp_path / "recall-history.jsonl"
    assert rw.main(["--history", str(hist), "--out-dir", str(tmp_path / "o"),
                    "--repo", str(tmp_path), "--dry-run"]) == 0
    assert "# Recall weekly" in capsys.readouterr().out
    assert not hist.exists() and not (tmp_path / "o").exists()


def test_previous_skips_todays_line():
    hist = [entry(date="2026-11-06"), entry(date="2026-11-13")]
    assert rw.previous(hist, "2026-11-13")["date"] == "2026-11-06"
    assert rw.previous([entry(date="2026-11-13")], "2026-11-13") is None


def test_damaged_history_line_is_skipped(tmp_path):
    p = tmp_path / "h.jsonl"
    p.write_text('{"date": "2026-11-06", "metrics": {}}\nnot json\n', encoding=UTF8)
    assert [r["date"] for r in rw.read_history(p)] == ["2026-11-06"]
