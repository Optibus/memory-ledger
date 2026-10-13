"""The usage ingest (scripts/usage_ingest.py): session logs -> usage-week nodes.

    python -m pytest tests/test_usage_ingest.py

Pins what makes the ingest trustworthy: it parses Claude Code's JSONL logs
deterministically (streamed duplicates counted once, subagents folded into their
session), cuts them into ISO weeks, prices tokens from the one price table,
writes ONE node per week through graph.py's helpers (idempotent, never
deleting, never replacing a fuller week), re-parses only files that changed, and
feeds usage_report.py end to end. The logs are SYNTHETIC, written here into a
throwaway directory: no real usage is read or kept (CONTRIBUTING ground rule 5).
The brain is a throwaway temp home.

The tests of `TestIngestFlow` build on one brain and one set of logs, in the
order they are written (a rerun changes nothing, a grown log updates in place,
and so on); every other test is independent.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

import usage_ingest as ui
import usage_report as ur
from conftest import ROOT, SCRIPTS, UTF8

NO_AUTOSYNC = "MEMORY_LEDGER_NO_AUTOSYNC"
PROJECTS_ENV = "MEMORY_LEDGER_USAGE_PROJECTS_DIR"
OPT_OUT_ENV = "MEMORY_LEDGER_NO_USAGE_INGEST"
W03, W04 = "2026-W03", "2026-W04"
NODE_W03, NODE_W04 = f"usage-week_{W03}", f"usage-week_{W04}"
UNKNOWN_MODEL = "claude-some-future-model"
HAIKU = "claude-haiku-4-5-20251001"
MTOK = 1_000_000
HOOK = ROOT / "hooks" / "usage_ingest.py"
HOOK_WAIT_S = 10


# ---------------------------------------------------------------- fixtures
def rec(kind, ts, **kw):
    return {"type": kind, "timestamp": ts, "sessionId": kw.pop("sid", "s"), **kw}


def user(ts, text, **kw):
    kw.setdefault("isSidechain", False)
    return rec("user", ts, message={"role": "user", "content": text}, **kw)


def tool_result(ts):
    return rec("user", ts, isSidechain=False, message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": "ok"}]})


def asst(ts, mid, model="claude-opus-5", inp=10, out=100, read=1000, write=0, tools=(), text="",
         w1h=0, **kw):
    blocks = [{"type": "text", "text": text}] if text else []
    blocks += [{"type": "tool_use", "id": f"tu_{mid}_{i}", "name": n, "input": i_}
               for i, (n, i_) in enumerate(tools)]
    usage = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": read,
             "cache_creation_input_tokens": write}
    if w1h:
        usage["cache_creation"] = {"ephemeral_1h_input_tokens": w1h}
    return rec("assistant", ts, message={"id": mid, "model": model, "role": "assistant",
                                          "content": blocks, "usage": usage}, **kw)


def write_log(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding=UTF8)


def make_world(base: Path) -> Path:
    """Two repos on disk (one with a linked worktree) and a projects dir of logs."""
    alpha = base / "work" / "alpha"
    (alpha / ".git" / "worktrees" / "alpha-wt-ABC-123").mkdir(parents=True)
    (alpha / "src").mkdir()
    wt = base / "work" / "alpha-wt-ABC-123"
    (wt / "src").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {alpha}/.git/worktrees/alpha-wt-ABC-123\n", encoding=UTF8)
    beta = base / "work" / "beta"
    (beta / ".git").mkdir(parents=True)
    loose = base / "work" / "loose"
    loose.mkdir()
    proj = base / "claude" / "projects" / "proj-a"

    # W03 (2026-01-12 .. 2026-01-18): s1 edits alpha + its worktree, compacts, has a subagent
    w03 = [
        user("2026-01-13T10:00:00Z", "please add a flag", sid="s1"),
        asst("2026-01-13T10:00:05Z", "m1", tools=[("Edit", {"file_path": f"{alpha}/src/a.py"})],
             out=50, sid="s1"),
        # the same API message streamed again: counted once, at its final usage
        asst("2026-01-13T10:00:06Z", "m1", tools=[("Edit", {"file_path": f"{alpha}/src/a.py"})],
             out=200, sid="s1"),
        tool_result("2026-01-13T10:00:07Z"),
        asst("2026-01-13T10:05:00Z", "m2", out=300, read=2000, write=500, w1h=200,
             tools=[("Write", {"file_path": f"{wt}/src/b.py"}),
                    ("Edit", {"file_path": f"{loose}/notes.txt"})], sid="s1"),
        user("2026-01-13T10:10:00Z", "<system-reminder>not typed</system-reminder>", sid="s1"),
        user("2026-01-13T10:20:00Z", "now the tests", sid="s1"),
        rec("system", "2026-01-13T10:30:00Z", subtype="compact_boundary", sid="s1"),
        asst("2026-01-13T11:45:00Z", "m3", model=HAIKU, out=10, read=0, sid="s1"),
        asst("2026-01-13T11:46:00Z", "synth", model="<synthetic>", out=0, read=0, sid="s1"),
    ]
    write_log(proj / "s1.jsonl", w03)
    write_log(proj / "s1" / "subagents" / "agent-1.jsonl", [
        user("2026-01-13T10:01:00Z", "subagent task", sid="s1", isSidechain=True),
        asst("2026-01-13T10:01:30Z", "sub1", model="claude-sonnet-5", out=400, read=0, sid="s1"),
    ])
    # s2: no repo write, one outward action, and a rate-limit refusal
    write_log(proj / "s2.jsonl", [
        user("2026-01-14T23:30:00Z", "review the thing", sid="s2"),
        asst("2026-01-14T23:31:00Z", "m4", sid="s2", tools=[
            ("mcp__claude_ai_Slack__slack_send_message", {}),
            ("mcp__claude_ai_Slack__slack_read_channel", {}),
            ("Bash", {"command": "gh pr comment 12 --body hi"})]),
        rec("assistant", "2026-01-14T23:32:00Z", sid="s2", isApiErrorMessage=True,
            message={"role": "assistant", "content": [
                {"type": "text", "text": "You've hit your 5-hour limit"}]}),
    ])
    # s3: no writes, nothing outward (investigation only); started Sunday night
    write_log(proj / "s3.jsonl", [
        user("2026-01-18T22:00:00Z", "where is the bug", sid="s3"),
        asst("2026-01-18T22:01:00Z", "m5", sid="s3", model=UNKNOWN_MODEL),
    ])
    # W04: one session editing beta
    write_log(proj / "s4.jsonl", [
        user("2026-01-20T09:00:00Z", "do it", sid="s4"),
        asst("2026-01-20T09:00:10Z", "m6", sid="s4", tools=[("Edit", {"file_path": f"{beta}/x.py"})]),
    ])
    (proj / "junk.jsonl").write_text("not json\n{\n", encoding=UTF8)
    return base / "claude" / "projects"


@pytest.fixture
def projects(tmp_path) -> Path:
    """Fresh synthetic logs for one test."""
    return make_world(tmp_path / "world")


@pytest.fixture(scope="class")
def shared_projects(tmp_path_factory) -> Path:
    """Synthetic logs for a class whose tests build on each other."""
    return make_world(tmp_path_factory.mktemp("world"))


@pytest.fixture(scope="module")
def table():
    return ui.load_prices(None)


@pytest.fixture(scope="module")
def tz():
    return ui.get_tz("UTC")


def child_env(home: Path | str, **extra: str) -> dict[str, str]:
    """An environment for a child: this brain, no autosync, nothing of the owner's."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SECOND_BRAIN_", "MEMORY_LEDGER_"))}
    env.update({"SECOND_BRAIN_HOME": str(home), NO_AUTOSYNC: "1", **extra})
    return env


def run_py(script: Path, *args: str, env: dict[str, str], stdin: str | None = None):
    return subprocess.run([sys.executable, str(script), *args], input=stdin,
                          capture_output=True, text=True, encoding=UTF8, errors="replace",
                          env=env)


def cli(home: Path | str, *args: str, **extra: str):
    return run_py(SCRIPTS / "usage_ingest.py", *args, env=child_env(home, **extra))


def nodes(home: Path) -> dict:
    con = sqlite3.connect(home / "graph.db")
    try:
        rows = con.execute("SELECT id, type, props FROM nodes WHERE type='usage-week' "
                           "ORDER BY id").fetchall()
    finally:
        con.close()
    return {r[0]: json.loads(r[2]) for r in rows}


def action_of(result: dict, week: str) -> dict:
    return next(w for w in result["weeks"] if w["week"] == week)


# ------------------------------------------------------------------- cost math
def cost_parts(model: str, usage: dict, table):
    (parts, known), read = ui.turn_cost(model, usage, table)
    return parts, known, read


def test_opus_prices_every_token_kind(table):
    parts, known, read = cost_parts("claude-opus-5", {
        "input_tokens": MTOK, "output_tokens": MTOK, "cache_read_input_tokens": MTOK,
        "cache_creation_input_tokens": MTOK}, table)
    assert [round(parts[k], 4) for k in ("input", "output", "cacheWrite", "cacheRead")] == [
        5, 25, 6.25, 0.5]
    assert known
    assert read == MTOK


@pytest.mark.parametrize("model, usage, part, dollars", [
    ("claude-sonnet-5", {"input_tokens": 2 * MTOK}, "input", 6.0),
    (HAIKU, {"output_tokens": MTOK}, "output", 5.0),
    ("claude-opus-4-1-20250805", {"input_tokens": MTOK}, "input", 15.0),
    ("claude-opus-5", {"cache_creation_input_tokens": MTOK,
                       "cache_creation": {"ephemeral_1h_input_tokens": MTOK}}, "cacheWrite", 10.0),
], ids=["sonnet-input", "dated-haiku-matches-its-family", "older-opus-own-rate",
        "one-hour-cache-write-2x"])
def test_a_model_is_priced_at_its_family_rate(table, model, usage, part, dollars):
    parts, _, _ = cost_parts(model, usage, table)
    assert round(parts[part], 4) == dollars


def test_an_unknown_model_is_flagged_not_hidden(table):
    assert cost_parts(UNKNOWN_MODEL, {"input_tokens": 1}, table)[1] is False


def test_a_price_override_file_wins(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"some-future": [1, 2, 3, 4]}), encoding=UTF8)
    parts, known, _ = cost_parts(UNKNOWN_MODEL, {"input_tokens": MTOK}, ui.load_prices(str(f)))
    assert known
    assert round(parts["input"], 4) == 1.0


def test_a_malformed_price_override_is_refused(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"x": [1, 2]}), encoding=UTF8)
    with pytest.raises(ValueError):
        ui.load_prices(str(f))


# ------------------------------------------------------------------- parsing
def parse(projects: Path, *rel: str, table, tz):
    return ui.parse_file(str(projects.joinpath("proj-a", *rel)), table, tz)


class TestParsing:
    def test_a_streamed_message_is_one_turn_and_synthetic_is_skipped(self, projects, table, tz):
        assert parse(projects, "s1.jsonl", table=table, tz=tz)["turns"] == 3

    def test_typed_prompts_exclude_system_reminders_and_tool_results(self, projects, table, tz):
        assert parse(projects, "s1.jsonl", table=table, tz=tz)["typed"] == 2

    def test_the_last_streamed_usage_wins(self, projects, table, tz):
        s1 = parse(projects, "s1.jsonl", table=table, tz=tz)
        assert s1["comp"]["output"] == pytest.approx((200 + 300) * 25 / 1e6 + 10 * 5 / 1e6)

    def test_a_compact_boundary_marks_the_session_compacted(self, projects, table, tz):
        assert parse(projects, "s1.jsonl", table=table, tz=tz)["compacted"] is True

    def test_edits_resolve_to_the_repository_and_a_worktree_folds_onto_it(self, projects, table, tz):
        s1 = parse(projects, "s1.jsonl", table=table, tz=tz)
        assert s1["edits"] == {"alpha": 2}
        assert "loose" not in s1["edits"]

    def test_the_worktree_directory_is_kept_as_a_working_directory(self, projects, table, tz):
        dirs = parse(projects, "s1.jsonl", table=table, tz=tz)["dirs"]
        assert "alpha-wt-ABC-123" in dirs
        assert "alpha" in dirs

    def test_the_model_split_carries_dated_ids_and_drops_synthetic(self, projects, table, tz):
        by_model = parse(projects, "s1.jsonl", table=table, tz=tz)["byModel"]
        assert HAIKU in by_model
        assert "<synthetic>" not in by_model

    def test_a_subagent_log_belongs_to_its_parent_and_types_nothing(self, projects, table, tz):
        sub = parse(projects, "s1", "subagents", "agent-1.jsonl", table=table, tz=tz)
        assert sub["sid"] == "s1"
        assert sub["sub"]
        assert sub["typed"] == 0

    def test_outward_actions_count_mcp_writes_and_gh_writes_not_mcp_reads(self, projects, table, tz):
        s2 = parse(projects, "s2.jsonl", table=table, tz=tz)
        assert s2["outward"] == {"Slack writes (MCP)": 1, "GitHub PR and issue writes": 1}

    def test_a_limit_message_is_a_rate_limit_hit_with_its_window(self, projects, table, tz):
        s2 = parse(projects, "s2.jsonl", table=table, tz=tz)
        assert len(s2["limits"]) == 1
        assert s2["limits"][0]["window"] == "five_hour"

    def test_an_unreadable_or_empty_log_is_none_never_an_error(self, projects, table, tz, tmp_path):
        assert parse(projects, "junk.jsonl", table=table, tz=tz) is None
        assert ui.parse_file(str(tmp_path / "missing.jsonl"), table, tz) is None

    def test_an_unknown_model_is_named_as_unpriced(self, projects, table, tz):
        assert parse(projects, "s3.jsonl", table=table, tz=tz)["unpriced"] == [UNKNOWN_MODEL]


# ------------------------------------------------------------------- weeks
@pytest.fixture
def collected(projects, table, tz):
    cp, parsed, same = ui.collect(projects, {"fingerprint": "x", "files": {}}, table, tz, None)
    return cp, parsed, same, ui.weeks_from(cp, None)


class TestWeeks:
    def test_every_log_is_parsed_once(self, collected):
        _, parsed, same, _ = collected
        assert (parsed, same) == (6, 0)     # 4 sessions' files + the subagent + the junk

    def test_sessions_fall_in_their_iso_week_by_start(self, collected):
        weeks = collected[3]
        assert list(weeks) == [W03, W04]
        assert [len(v) for v in weeks.values()] == [3, 1]

    def test_week_bounds_are_monday_to_sunday(self):
        lo, hi = ui.week_bounds(W03)
        assert (lo.isoformat(), hi.isoformat()) == ("2026-01-12", "2026-01-18")

    @pytest.fixture
    def metrics(self, collected):
        return ui.week_metrics(collected[3][W03])

    def test_every_key_the_report_reads_is_present(self, metrics):
        needed = ["costUSD", "sessions", "turns", "typedPrompts", "activeDays", "byModel",
                  "composition", "cacheReadTokens", "durationBuckets", "noCompactionSessions",
                  "byRepo", "editsByRepo", "totalEdits", "workingDirs", "noWriteSessions",
                  "noWriteCostUSD", "noWriteOutward", "noWriteOutwardTotal",
                  "noWriteProductiveSessions", "noWriteProductiveCostUSD",
                  "investigationOnlySessions", "investigationOnlyCostUSD", "tickets",
                  "rateLimits"]
        assert [k for k in needed if k not in metrics] == []

    def test_sessions_typed_prompts_and_active_days(self, metrics):
        assert (metrics["sessions"], metrics["typedPrompts"], metrics["activeDays"]) == (3, 4, 3)

    def test_composition_sums_to_the_cost_to_a_cent(self, metrics):
        assert sum(metrics["composition"].values()) == pytest.approx(metrics["costUSD"], abs=0.02)

    def test_cost_is_credited_to_the_repository_actually_edited(self, metrics):
        assert list(metrics["byRepo"]) == ["alpha"]
        assert metrics["totalEdits"] >= 2

    def test_no_write_sessions_and_outward_actions(self, metrics):
        assert metrics["noWriteSessions"] == 2
        assert metrics["noWriteProductiveSessions"] == 1
        assert metrics["investigationOnlySessions"] == 1
        assert metrics["noWriteOutwardTotal"] == 2

    def test_a_ticket_key_is_read_off_the_worktree_directory_name(self, metrics):
        assert metrics["tickets"] == ["ABC-123"]

    def test_the_compacted_session_is_not_counted_as_never_compacted(self, metrics):
        assert metrics["noCompactionSessions"] == 2

    def test_session_length_buckets(self, metrics):
        # s1 is 105 min (30m-2h), s2 is 2 min and s3 1 min (<30m)
        assert [b["bucket"] for b in metrics["durationBuckets"]] == ["<30m", "30m-2h"]
        assert metrics["durationBuckets"][0]["sessions"] == 2

    def test_the_rate_limit_hit_and_the_unpriced_model_are_carried(self, metrics):
        assert len(metrics["rateLimits"]) == 1
        assert metrics["unpricedModels"] == [UNKNOWN_MODEL]

    def test_cost_by_hour_and_weekday_each_sum_to_the_cost(self, metrics):
        assert sum(metrics["costByHour"]) == pytest.approx(metrics["costUSD"], abs=0.05)
        assert sum(metrics["costByWeekday"]) == pytest.approx(metrics["costUSD"], abs=0.05)
        assert len(metrics["costByHour"]) == 24
        assert len(metrics["costByWeekday"]) == 7

    def test_metrics_are_a_pure_function_of_the_sessions(self, collected, metrics):
        assert ui.week_metrics(collected[3][W03]) == metrics

    def test_a_timezone_moves_a_late_night_session_to_the_week_it_was_lived_in(
            self, projects, table, tz):
        tz_ny = ui.get_tz("America/New_York")
        cp, _, _ = ui.collect(projects, {"fingerprint": "y", "files": {}}, table, tz_ny, None)
        assert W03 in ui.weeks_from(cp, None)
        assert ui.fingerprint(table, tz) != ui.fingerprint(table, tz_ny)


# ------------------------------------------------------------------- the flow
@pytest.mark.usefixtures("module_brain_home")
class TestIngestFlow:
    """Writing nodes, the checkpoint, and the report, on one brain, in order."""

    def test_first_run_creates_one_node_per_week_with_stable_ids(
            self, shared_projects, module_brain_home):
        r = ui.ingest(shared_projects)
        assert [w["action"] for w in r["weeks"]] == ["created", "created"]
        n1 = nodes(module_brain_home)
        assert sorted(n1) == [NODE_W03, NODE_W04]
        assert n1[NODE_W03]["week"] == W03
        assert n1[NODE_W03]["from"] == "2026-01-12"
        assert n1[NODE_W03]["source"] == "usage_ingest.py"

    def test_a_rerun_changes_nothing_and_parses_nothing(self, shared_projects, module_brain_home):
        before = nodes(module_brain_home)
        r = ui.ingest(shared_projects)
        assert [w["action"] for w in r["weeks"]] == ["unchanged", "unchanged"]
        assert r["parsed"] == 0
        assert nodes(module_brain_home) == before

    def test_a_week_whose_logs_grow_updates_in_place(self, shared_projects, module_brain_home):
        p = shared_projects / "proj-a" / "s4.jsonl"
        with open(p, "a", encoding=UTF8) as fh:
            fh.write(json.dumps(asst("2026-01-20T09:30:00Z", "m7", sid="s4")) + "\n")
        os.utime(p, (time.time() + 5, time.time() + 5))
        r = ui.ingest(shared_projects)
        assert r["parsed"] == 1
        n = nodes(module_brain_home)
        assert [w["action"] for w in r["weeks"]] == ["unchanged", "updated"]
        assert len(n) == 2
        assert n[NODE_W04]["metrics"]["turns"] == 2

    def test_dry_run_writes_neither_nodes_nor_checkpoint(self, shared_projects, module_brain_home):
        before = nodes(module_brain_home)
        checkpoint = module_brain_home / ui.CHECKPOINT
        cp_before = checkpoint.read_bytes()
        p = shared_projects / "proj-a" / "s4.jsonl"
        with open(p, "a", encoding=UTF8) as fh:
            fh.write(json.dumps(asst("2026-01-20T09:40:00Z", "m8", sid="s4")) + "\n")
        os.utime(p, (time.time() + 10, time.time() + 10))
        r = ui.ingest(shared_projects, dry_run=True)
        assert any(w["action"] == "would-write" for w in r["weeks"])
        assert nodes(module_brain_home) == before
        assert checkpoint.read_bytes() == cp_before

    def test_a_week_is_never_overwritten_by_fewer_sessions_than_it_holds(
            self, shared_projects, module_brain_home):
        hidden = shared_projects / "proj-a" / "s3.jsonl"
        TestIngestFlow.saved_s3 = hidden.read_text(encoding=UTF8)
        hidden.unlink()
        (module_brain_home / ui.CHECKPOINT).unlink()    # logs pruned AND checkpoint lost
        r = ui.ingest(shared_projects)
        assert action_of(r, W03)["action"] == "kept"
        assert nodes(module_brain_home)[NODE_W03]["metrics"]["sessions"] == 3

    def test_force_replaces_it(self, shared_projects, module_brain_home):
        forced = ui.ingest(shared_projects, force=True)
        assert action_of(forced, W03)["action"] == "updated"
        assert nodes(module_brain_home)[NODE_W03]["metrics"]["sessions"] == 2
        (shared_projects / "proj-a" / "s3.jsonl").write_text(TestIngestFlow.saved_s3,
                                                             encoding=UTF8)
        ui.ingest(shared_projects)

    def test_the_checkpoint_records_each_logs_signature_and_summary(
            self, shared_projects, module_brain_home):
        cp = json.loads((module_brain_home / ui.CHECKPOINT).read_text(encoding=UTF8))
        assert len(cp["files"]) >= 6
        assert all("sig" in f for f in cp["files"].values())

    def test_the_checkpoint_holds_no_prompt_or_answer_text(self, module_brain_home):
        text = (module_brain_home / ui.CHECKPOINT).read_text(encoding=UTF8)
        assert "please add a flag" not in text
        assert "hit your 5-hour limit" not in text

    def test_a_changed_price_table_invalidates_it(self, module_brain_home):
        assert ui.load_checkpoint(module_brain_home / ui.CHECKPOINT, "other-fingerprint")[
            "files"] == {}

    def test_since_snaps_to_its_iso_week_and_writes_only_from_there(self, shared_projects):
        r = ui.ingest(shared_projects, since="2026-01-19")
        assert [w["week"] for w in r["weeks"]] == [W04]

    def test_status_reports_the_checkpoint(self, module_brain_home):
        st = json.loads(cli(module_brain_home, "status").stdout)
        assert st["ok"]
        assert st["files"] >= 6
        assert st["lastRun"]

    def test_the_report_reads_what_the_ingest_wrote(self, module_brain_home):
        tags = [w["week"] for w in ur.load_weeks()]
        assert W03 in tags
        assert W04 in tags

    @pytest.fixture
    def page(self):
        return ur.render([w for w in ur.load_weeks() if w["week"] in (W03, W04)])

    def test_it_draws_the_page_repositories_and_all(self, page):
        assert page.startswith("<!doctype html>")
        for needle in ("alpha", "beta", "ABC-123"):
            assert needle in page

    def test_the_habits_are_there(self, page):
        for needle in ('id="ch-usage"', "Never compacted", "Cache read", "outward action"):
            assert needle in page

    def test_cost_by_hour_and_weekday_are_drawn(self, page):
        assert 'id="ch-hour"' in page
        assert 'id="ch-weekday"' in page

    def test_a_rate_limit_hit_shows_as_a_hard_stop(self, page):
        assert "five-hour" in page

    def test_the_page_is_deterministic(self, page):
        assert page == ur.render([w for w in ur.load_weeks() if w["week"] in (W03, W04)])

    def test_render_ingest_ingests_first_then_renders(self, shared_projects, module_brain_home):
        p = run_py(SCRIPTS / "usage_report.py", "render", "--ingest",
                   env=child_env(module_brain_home, **{PROJECTS_ENV: str(shared_projects)}))
        assert p.returncode == 0, p.stderr[:200]
        assert "Where the Quota Went" in p.stdout


# ------------------------------------------------------------------- independent
def test_a_node_written_by_another_tool_is_kept_with_the_reason_until_forced(projects, tmp_path):
    home = tmp_path / "other-home"
    home.mkdir()
    theirs = {"week": W03, "from": "2026-01-12", "to": "2026-01-18", "source": "other-tool",
              "metrics": {"costUSD": 1.0, "sessions": 1}}
    subprocess.run([sys.executable, str(SCRIPTS / "graph.py"), "add-node", "--type",
                    "usage-week", "--id", NODE_W03, "--title", "theirs", "--props",
                    json.dumps(theirs)],
                   capture_output=True, text=True, encoding=UTF8, errors="replace",
                   env=child_env(home), check=True)
    res = json.loads(cli(home, "run", "--projects-dir", str(projects), "--json").stdout)
    w03 = action_of(res, W03)
    assert w03["action"] == "kept"
    assert "other-tool" in w03["reason"]
    res = json.loads(cli(home, "run", "--projects-dir", str(projects), "--json",
                         "--force").stdout)
    assert action_of(res, W03)["action"] == "updated"


class TestCli:
    """The CLI and the lock, each on a brain of its own."""

    @pytest.fixture
    def home(self, tmp_path) -> Path:
        h = tmp_path / "cli-home"
        h.mkdir()
        return h

    def test_run_json_ingests_and_says_what_it_did(self, home, projects):
        p = cli(home, "run", "--projects-dir", str(projects), "--json")
        assert p.returncode == 0, p.stderr[:200]
        assert len(json.loads(p.stdout)["weeks"]) == 2

    def test_run_dry_run_prints_the_plan(self, home, projects):
        p = cli(home, "run", "--projects-dir", str(projects), "--dry-run")
        assert p.returncode == 0
        assert "would-write" in p.stdout
        cli(home, "run", "--projects-dir", str(projects), "--quiet")
        p = cli(home, "run", "--projects-dir", str(projects), "--dry-run")
        assert "unchanged" in p.stdout

    def test_quiet_prints_nothing(self, home, projects):
        p = cli(home, "run", "--projects-dir", str(projects), "--quiet")
        assert p.returncode == 0
        assert p.stdout == ""
        assert p.stderr == ""

    def test_a_missing_prices_file_exits_non_zero_with_the_reason(self, home, projects, tmp_path):
        p = cli(home, "run", "--projects-dir", str(projects), "--prices",
                str(tmp_path / "nope.json"))
        assert p.returncode == 2
        assert "error" in p.stderr

    def test_no_logs_directory_is_a_note_not_a_failure(self, home, tmp_path):
        p = cli(home, "run", "--projects-dir", str(tmp_path / "does" / "not" / "exist"), "--json")
        assert p.returncode == 0
        assert "no session logs" in p.stdout

    def test_a_held_lock_makes_a_background_run_stand_down(self, home, projects):
        lock = home / ui.LOCK
        lock.write_text("999", encoding=UTF8)
        p = cli(home, "run", "--projects-dir", str(projects), "--quiet", "--locked")
        assert p.returncode == 0
        assert lock.exists()


class TestHook:
    """The SessionStart hook: it only forks, and it respects every off switch."""

    def hook(self, home: Path | str, projects: Path, stdin: str = '{"session_id":"t"}',
             **extra: str):
        env = child_env(home, HOME=str(home), **{PROJECTS_ENV: str(projects), **extra})
        return run_py(HOOK, env=env, stdin=stdin)

    @pytest.fixture
    def home(self, tmp_path) -> Path:
        h = tmp_path / "hook-home"
        (h / ".claude" / "projects").mkdir(parents=True)
        return h

    def test_it_exits_zero_prints_nothing_and_returns_fast(self, home, projects):
        t0 = time.time()
        p = self.hook(home, projects)
        assert p.returncode == 0
        assert p.stdout == ""
        assert p.stderr == ""
        assert time.time() - t0 < 3

    def test_it_leaves_a_stamp_and_the_detached_run_writes_the_checkpoint(self, home, projects):
        self.hook(home, projects)
        assert (home / ui.STAMP).exists()
        deadline = time.time() + HOOK_WAIT_S
        while time.time() < deadline and not (
                (home / "graph.db").exists() and (home / ui.CHECKPOINT).exists()):
            time.sleep(0.1)
        assert (home / ui.CHECKPOINT).exists()

    def test_a_second_start_inside_the_window_spawns_nothing(self, home, projects):
        self.hook(home, projects)
        deadline = time.time() + HOOK_WAIT_S
        while time.time() < deadline and not (home / ui.CHECKPOINT).exists():
            time.sleep(0.1)
        old = (home / ui.CHECKPOINT).stat().st_mtime_ns
        self.hook(home, projects)
        time.sleep(0.5)
        assert (home / ui.CHECKPOINT).stat().st_mtime_ns == old

    def test_the_opt_out_env_var_stops_it(self, home, projects):
        p = self.hook(home, projects, **{OPT_OUT_ENV: "1"})
        assert p.returncode == 0
        assert p.stdout == ""
        assert not (home / ui.STAMP).exists()

    def test_settings_json_stops_it_too(self, home, projects):
        (home / "settings.json").write_text('{"usage_ingest": false}', encoding=UTF8)
        self.hook(home, projects)
        assert not (home / ui.STAMP).exists()

    def test_a_brain_that_does_not_exist_is_not_created_and_garbage_on_stdin_is_fine(
            self, tmp_path, projects):
        missing = tmp_path / "not-created"
        p = self.hook(missing, projects, stdin="garbage")
        assert p.returncode == 0
        assert not missing.exists()

    def test_it_is_bound_to_session_start_startup(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding=UTF8))
        assert any("usage_ingest.py" in h["command"]
                   for blk in hooks["hooks"]["SessionStart"] if blk["matcher"] == "startup"
                   for h in blk["hooks"])


class TestRenderIngest:
    def env(self, home: Path, projects: Path) -> dict[str, str]:
        return child_env(home, **{PROJECTS_ENV: str(projects)})

    def test_on_a_fresh_brain_render_ingest_fills_it_from_the_logs(self, tmp_path, projects):
        home = tmp_path / "fresh"
        home.mkdir()
        p = run_py(SCRIPTS / "usage_report.py", "render", "--ingest",
                   env=self.env(home, projects))
        assert p.returncode == 0, p.stderr[:200]
        assert "alpha" in p.stdout

    def test_ingest_with_db_is_refused(self, tmp_path, projects):
        home = tmp_path / "fresh"
        home.mkdir()
        p = run_py(SCRIPTS / "usage_report.py", "--db", "x.db", "render", "--ingest",
                   env=self.env(home, projects))
        assert p.returncode == 2

    def test_an_older_node_without_the_hour_series_still_renders(self):
        page = ur.render([{"id": "x", "week": "2026-W01", "from": "2025-12-29",
                           "to": "2026-01-04", "source": "",
                           "metrics": {"costUSD": 1.0, "sessions": 1, "turns": 2,
                                       "byModel": {"claude-opus-5": 1.0}}}])
        assert 'id="rhythm"' not in page
