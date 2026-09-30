#!/usr/bin/env python3
"""The usage ingest (scripts/usage_ingest.py): session logs -> usage-week nodes.

    python3 tests/test_usage_ingest.py

Pins what makes the ingest trustworthy: it parses Claude Code's JSONL logs
deterministically (streamed duplicates counted once, subagents folded into their
session), cuts them into ISO weeks, prices tokens from the one price table,
writes ONE node per week through graph.py's helpers (idempotent, never
deleting, never replacing a fuller week), re-parses only files that changed, and
feeds usage_report.py end to end. The logs are SYNTHETIC, written here into a
throwaway directory — no real usage is read or kept (CONTRIBUTING ground rule 5).
The brain is a throwaway temp home. Stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
HOME = tempfile.mkdtemp(prefix="ml-usage-ingest-")
for k in list(os.environ):
    if k.startswith(("SECOND_BRAIN_", "MEMORY_LEDGER_")):
        del os.environ[k]
os.environ["SECOND_BRAIN_HOME"] = HOME
os.environ["MEMORY_LEDGER_NO_AUTOSYNC"] = "1"
sys.path.insert(0, str(SCRIPTS))
import usage_ingest as ui  # noqa: E402
import usage_report as ur  # noqa: E402

FAILS: list[str] = []


def ck(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


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
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def make_world(base: Path) -> Path:
    """Two repos on disk (one with a linked worktree) and a projects dir of logs."""
    alpha = base / "work" / "alpha"
    (alpha / ".git" / "worktrees" / "alpha-wt-ABC-123").mkdir(parents=True)
    (alpha / "src").mkdir()
    wt = base / "work" / "alpha-wt-ABC-123"
    (wt / "src").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {alpha}/.git/worktrees/alpha-wt-ABC-123\n")
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
        asst("2026-01-13T11:45:00Z", "m3", model="claude-haiku-4-5-20251001", out=10, read=0, sid="s1"),
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
        asst("2026-01-18T22:01:00Z", "m5", sid="s3", model="claude-some-future-model"),
    ])
    # W04: one session editing beta
    write_log(proj / "s4.jsonl", [
        user("2026-01-20T09:00:00Z", "do it", sid="s4"),
        asst("2026-01-20T09:00:10Z", "m6", sid="s4", tools=[("Edit", {"file_path": f"{beta}/x.py"})]),
    ])
    (proj / "junk.jsonl").write_text("not json\n{\n", encoding="utf-8")
    return base / "claude" / "projects"


def nodes():
    import sqlite3
    con = sqlite3.connect(Path(HOME) / "graph.db")
    rows = con.execute("SELECT id, type, props FROM nodes WHERE type='usage-week' ORDER BY id").fetchall()
    con.close()
    return {r[0]: json.loads(r[2]) for r in rows}


def cli(*args, env=None):
    e = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, str(SCRIPTS / "usage_ingest.py"), *args],
                          capture_output=True, text=True, env=e)


# ------------------------------------------------------------------- tests
def test_cost_math():
    print("\n== cost math: one price table, per model family ==")
    table = ui.load_prices(None)
    (parts, known), read = ui.turn_cost("claude-opus-5", {
        "input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
        "cache_creation_input_tokens": 1_000_000}, table)
    ck("opus: input 5, output 25, cache write 6.25, cache read 0.5 per MTok",
       [round(parts[k], 4) for k in ("input", "output", "cacheWrite", "cacheRead")] == [5, 25, 6.25, 0.5]
       and known and read == 1_000_000)
    (p, _), _ = ui.turn_cost("claude-sonnet-5", {"input_tokens": 2_000_000}, table)
    ck("sonnet input is 3 per MTok", round(p["input"], 4) == 6.0)
    (p, _), _ = ui.turn_cost("claude-haiku-4-5-20251001", {"output_tokens": 1_000_000}, table)
    ck("a dated haiku id matches its family: output 5", round(p["output"], 4) == 5.0)
    (p, _), _ = ui.turn_cost("claude-opus-4-1-20250805", {"input_tokens": 1_000_000}, table)
    ck("an older opus is priced at its own (higher) rate", round(p["input"], 4) == 15.0)
    (p, _), _ = ui.turn_cost("claude-opus-5", {"cache_creation_input_tokens": 1_000_000,
                                                "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000}}, table)
    ck("a one-hour cache write bills at 2x input", round(p["cacheWrite"], 4) == 10.0)
    (_, known), _ = ui.turn_cost("claude-some-future-model", {"input_tokens": 1}, table)
    ck("an unknown model is flagged, not hidden", known is False)
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "p.json"
        f.write_text(json.dumps({"some-future": [1, 2, 3, 4]}))
        t2 = ui.load_prices(str(f))
        (p, known), _ = ui.turn_cost("claude-some-future-model", {"input_tokens": 1_000_000}, t2)
        ck("an override file wins", known and round(p["input"], 4) == 1.0)
        f.write_text(json.dumps({"x": [1, 2]}))
        try:
            ui.load_prices(str(f))
            ck("a malformed override is refused", False)
        except ValueError:
            ck("a malformed override is refused", True)


def test_parsing(projects: Path):
    print("\n== parsing one log ==")
    table, tz = ui.load_prices(None), ui.get_tz("UTC")
    s1 = ui.parse_file(str(projects / "proj-a" / "s1.jsonl"), table, tz)
    ck("a streamed message is one turn: m1, m2, m3 (synthetic skipped)", s1["turns"] == 3, str(s1["turns"]))
    ck("typed prompts exclude system-reminders and tool results", s1["typed"] == 2, str(s1["typed"]))
    ck("the last streamed usage wins (m1 output 200, not 50)",
       abs(s1["comp"]["output"] - (200 + 300) * 25 / 1e6 - 10 * 5 / 1e6) < 1e-9, str(s1["comp"]))
    ck("a compact_boundary marks the session compacted", s1["compacted"] is True)
    ck("edits resolve to the repository; a linked worktree folds onto it",
       s1["edits"] == {"alpha": 2}, str(s1["edits"]))
    ck("the worktree directory is kept as the working directory",
       "alpha-wt-ABC-123" in s1["dirs"] and "alpha" in s1["dirs"], str(s1["dirs"]))
    ck("a file outside any repo is not a repo edit", "loose" not in s1["edits"])
    ck("model split carries dated ids as they are", "claude-haiku-4-5-20251001" in s1["byModel"]
       and "<synthetic>" not in s1["byModel"])
    sub = ui.parse_file(str(projects / "proj-a" / "s1" / "subagents" / "agent-1.jsonl"), table, tz)
    ck("a subagent log belongs to its parent session and types nothing",
       sub["sid"] == "s1" and sub["sub"] and sub["typed"] == 0)
    s2 = ui.parse_file(str(projects / "proj-a" / "s2.jsonl"), table, tz)
    ck("outward actions: MCP writes and gh writes counted, MCP reads not",
       s2["outward"] == {"Slack writes (MCP)": 1, "GitHub PR and issue writes": 1}, str(s2["outward"]))
    ck("a limit message is a rate-limit hit with its window",
       len(s2["limits"]) == 1 and s2["limits"][0]["window"] == "five_hour", str(s2["limits"]))
    ck("an unreadable or empty log is None, never an error",
       ui.parse_file(str(projects / "proj-a" / "junk.jsonl"), table, tz) is None
       and ui.parse_file("/nope/x.jsonl", table, tz) is None)
    ck("an unknown model is named as unpriced",
       ui.parse_file(str(projects / "proj-a" / "s3.jsonl"), table, tz)["unpriced"]
       == ["claude-some-future-model"])


def test_weeks(projects: Path):
    print("\n== week bucketing and the metrics shape ==")
    table, tz = ui.load_prices(None), ui.get_tz("UTC")
    cp, parsed, same = ui.collect(projects, {"fingerprint": "x", "files": {}}, table, tz, None)
    ck("every log is parsed once (4 sessions' files + subagent + junk)", parsed == 6 and same == 0, str(parsed))
    weeks = ui.weeks_from(cp, None)
    ck("sessions fall in their ISO week by start: W03 has s1,s2,s3; W04 has s4",
       list(weeks) == ["2026-W03", "2026-W04"]
       and [len(v) for v in weeks.values()] == [3, 1], str({k: len(v) for k, v in weeks.items()}))
    lo, hi = ui.week_bounds("2026-W03")
    ck("week bounds are Monday..Sunday", (lo.isoformat(), hi.isoformat()) == ("2026-01-12", "2026-01-18"))
    m = ui.week_metrics(weeks["2026-W03"])
    needed = ["costUSD", "sessions", "turns", "typedPrompts", "activeDays", "byModel", "composition",
              "cacheReadTokens", "durationBuckets", "noCompactionSessions", "byRepo", "editsByRepo",
              "totalEdits", "workingDirs", "noWriteSessions", "noWriteCostUSD", "noWriteOutward",
              "noWriteOutwardTotal", "noWriteProductiveSessions", "noWriteProductiveCostUSD",
              "investigationOnlySessions", "investigationOnlyCostUSD", "tickets", "rateLimits"]
    ck("every key usage_report.py reads is present", all(k in m for k in needed),
       str([k for k in needed if k not in m]))
    ck("sessions, typed prompts, active days", (m["sessions"], m["typedPrompts"], m["activeDays"]) == (3, 4, 3))
    ck("composition sums to the cost (to a cent)",
       abs(sum(m["composition"].values()) - m["costUSD"]) < 0.02)
    ck("cost is credited to the repository actually edited", list(m["byRepo"]) == ["alpha"]
       and m["totalEdits"] >= 2)
    ck("two sessions wrote no repo file; one did something outward",
       m["noWriteSessions"] == 2 and m["noWriteProductiveSessions"] == 1 and m["investigationOnlySessions"] == 1
       and m["noWriteOutwardTotal"] == 2)
    ck("a ticket key is read off the worktree directory name", m["tickets"] == ["ABC-123"], str(m["tickets"]))
    ck("the compacted session is not counted as never-compacted", m["noCompactionSessions"] == 2)
    names = [b["bucket"] for b in m["durationBuckets"]]
    ck("session-length buckets: s1 is 105 min (30m-2h), s2 2 min, s3 1 min (<30m)",
       names == ["<30m", "30m-2h"] and m["durationBuckets"][0]["sessions"] == 2, str(m["durationBuckets"]))
    ck("the rate-limit hit is carried", len(m["rateLimits"]) == 1)
    ck("the unpriced model is listed", m["unpricedModels"] == ["claude-some-future-model"])
    ck("cost by hour and weekday each sum to the cost",
       abs(sum(m["costByHour"]) - m["costUSD"]) < 0.05 and abs(sum(m["costByWeekday"]) - m["costUSD"]) < 0.05
       and len(m["costByHour"]) == 24 and len(m["costByWeekday"]) == 7)
    ck("metrics are a pure function of the sessions", ui.week_metrics(weeks["2026-W03"]) == m)
    tz_ny = ui.get_tz("America/New_York")
    cp2, _, _ = ui.collect(projects, {"fingerprint": "y", "files": {}}, table, tz_ny, None)
    ck("a timezone moves a late-night session to the week it was lived in",
       "2026-W03" in ui.weeks_from(cp2, None) and ui.fingerprint(table, tz) != ui.fingerprint(table, tz_ny))


def test_write_and_idempotency(projects: Path):
    print("\n== writing nodes: idempotent, never deleting ==")
    r = ui.ingest(projects)
    ck("first run creates one node per week", [w["action"] for w in r["weeks"]] == ["created", "created"], str(r["weeks"]))
    n1 = nodes()
    ck("stable ids, the shape the report reads", sorted(n1) == ["usage-week_2026-W03", "usage-week_2026-W04"]
       and n1["usage-week_2026-W03"]["week"] == "2026-W03" and n1["usage-week_2026-W03"]["from"] == "2026-01-12"
       and n1["usage-week_2026-W03"]["source"] == "usage_ingest.py")
    r2 = ui.ingest(projects)
    ck("a rerun on the same logs changes nothing and parses nothing",
       [w["action"] for w in r2["weeks"]] == ["unchanged", "unchanged"] and r2["parsed"] == 0, str(r2))
    ck("and adds no node", nodes() == n1)
    # the week's logs grow: the node updates in place
    p = projects / "proj-a" / "s4.jsonl"
    with open(p, "a") as fh:
        fh.write(json.dumps(asst("2026-01-20T09:30:00Z", "m7", sid="s4")) + "\n")
    os.utime(p, (time.time() + 5, time.time() + 5))
    r3 = ui.ingest(projects)
    ck("only the changed file is re-parsed", r3["parsed"] == 1, str(r3["parsed"]))
    n3 = nodes()
    ck("the week updates in place, one node per week", [w["action"] for w in r3["weeks"]] == ["unchanged", "updated"]
       and len(n3) == 2 and n3["usage-week_2026-W04"]["metrics"]["turns"] == 2)
    # the dry run writes nothing, the checkpoint included
    cp_before = (Path(HOME) / ui.CHECKPOINT).read_bytes()
    with open(p, "a") as fh:
        fh.write(json.dumps(asst("2026-01-20T09:40:00Z", "m8", sid="s4")) + "\n")
    os.utime(p, (time.time() + 10, time.time() + 10))
    r4 = ui.ingest(projects, dry_run=True)
    ck("--dry-run reports but writes neither nodes nor checkpoint",
       any(w["action"] == "would-write" for w in r4["weeks"]) and nodes() == n3
       and (Path(HOME) / ui.CHECKPOINT).read_bytes() == cp_before)
    # never replaced by less data
    shutil_dir = projects / "proj-a"
    hidden = shutil_dir / "s3.jsonl"
    saved = hidden.read_text()
    hidden.unlink()
    (Path(HOME) / ui.CHECKPOINT).unlink()   # logs pruned AND checkpoint lost
    r5 = ui.ingest(projects)
    kept = [w for w in r5["weeks"] if w["week"] == "2026-W03"][0]
    ck("a week is never overwritten by fewer sessions than it holds", kept["action"] == "kept"
       and nodes()["usage-week_2026-W03"]["metrics"]["sessions"] == 3, str(kept))
    forced = ui.ingest(projects, force=True)
    ck("--force replaces it", [w for w in forced["weeks"] if w["week"] == "2026-W03"][0]["action"] == "updated"
       and nodes()["usage-week_2026-W03"]["metrics"]["sessions"] == 2)
    hidden.write_text(saved)
    ui.ingest(projects)
    # another tool's node is left alone (a separate throwaway brain)
    with tempfile.TemporaryDirectory() as home:
        env = {**os.environ, "SECOND_BRAIN_HOME": home, "MEMORY_LEDGER_NO_AUTOSYNC": "1"}
        theirs = {"week": "2026-W03", "from": "2026-01-12", "to": "2026-01-18", "source": "other-tool",
                  "metrics": {"costUSD": 1.0, "sessions": 1}}
        subprocess.run([sys.executable, str(SCRIPTS / "graph.py"), "add-node", "--type", "usage-week",
                        "--id", "usage-week_2026-W03", "--title", "theirs", "--props", json.dumps(theirs)],
                       capture_output=True, text=True, env=env, check=True)
        res = json.loads(cli("run", "--projects-dir", str(projects), "--json", env=env).stdout)
        w03 = [w for w in res["weeks"] if w["week"] == "2026-W03"][0]
        ck("a node written by another tool is kept, with the reason",
           w03["action"] == "kept" and "other-tool" in w03["reason"], str(w03))
        res = json.loads(cli("run", "--projects-dir", str(projects), "--json", "--force", env=env).stdout)
        ck("--force replaces it", [w for w in res["weeks"] if w["week"] == "2026-W03"][0]["action"] == "updated")


def test_checkpoint(projects: Path):
    print("\n== the checkpoint ==")
    cp_path = Path(HOME) / ui.CHECKPOINT
    cp = json.loads(cp_path.read_text())
    ck("it records each log's mtime+size and summary", len(cp["files"]) >= 6
       and all("sig" in f for f in cp["files"].values()))
    ck("it holds no prompt or answer text", "please add a flag" not in cp_path.read_text()
       and "hit your 5-hour limit" not in cp_path.read_text())
    table, tz = ui.load_prices(None), ui.get_tz("UTC")
    ck("a changed price table invalidates it (so every session is re-priced)",
       ui.load_checkpoint(cp_path, "other-fingerprint")["files"] == {})
    r = ui.ingest(projects, since="2026-01-19")
    ck("--since snaps to its ISO week and writes only from there", [w["week"] for w in r["weeks"]] == ["2026-W04"])
    st = json.loads(cli("status").stdout)
    ck("status reports the checkpoint", st["ok"] and st["files"] >= 6 and st["lastRun"])


def test_cli_and_hook(projects: Path):
    print("\n== the CLI, the lock and the hook ==")
    with tempfile.TemporaryDirectory() as home:
        env = {"SECOND_BRAIN_HOME": home, "MEMORY_LEDGER_NO_AUTOSYNC": "1"}
        p = cli("run", "--projects-dir", str(projects), "--json", env=env)
        res = json.loads(p.stdout or "{}")
        ck("run --json ingests and says what it did", p.returncode == 0 and len(res.get("weeks", [])) == 2, p.stderr[:200])
        p = cli("run", "--projects-dir", str(projects), "--dry-run", env=env)
        ck("run --dry-run prints the plan", p.returncode == 0 and "unchanged" in p.stdout or "would-write" in p.stdout)
        p = cli("run", "--projects-dir", str(projects), "--quiet", env=env)
        ck("--quiet prints nothing", p.returncode == 0 and p.stdout == "" and p.stderr == "")
        p = cli("run", "--projects-dir", str(projects), "--prices", "/nope.json", env=env)
        ck("a missing prices file exits non-zero with the reason", p.returncode == 2 and "error" in p.stderr)
        p = cli("run", "--projects-dir", "/does/not/exist", "--json", env=env)
        ck("no logs directory is a note, not a failure", p.returncode == 0 and "no session logs" in p.stdout)
        (Path(home) / ui.LOCK).write_text("999")
        p = cli("run", "--projects-dir", str(projects), "--quiet", "--locked", env=env)
        ck("a held lock makes a background run stand down", p.returncode == 0 and (Path(home) / ui.LOCK).exists())
        (Path(home) / ui.LOCK).unlink()

    def hook(home, extra=None, stdin='{"session_id":"t"}'):
        env = {**os.environ, "SECOND_BRAIN_HOME": home, "HOME": home, "MEMORY_LEDGER_NO_AUTOSYNC": "1",
               **(extra or {})}
        return subprocess.run([sys.executable, str(ROOT / "hooks" / "usage_ingest.py")], input=stdin,
                              capture_output=True, text=True, env=env)

    with tempfile.TemporaryDirectory() as home:
        (Path(home) / ".claude" / "projects").mkdir(parents=True)
        t0 = time.time()
        p = hook(home, {"MEMORY_LEDGER_USAGE_PROJECTS_DIR": str(projects)})
        ck("the hook exits 0 and prints nothing", p.returncode == 0 and p.stdout == "" and p.stderr == "", p.stderr[:200])
        ck("and returns fast (it only forks)", time.time() - t0 < 3, f"{time.time() - t0:.2f}s")
        stamp = Path(home) / ui.STAMP
        ck("it leaves a stamp so the next start does not spawn again", stamp.exists())
        for _ in range(100):
            if (Path(home) / "graph.db").exists() and (Path(home) / ui.CHECKPOINT).exists():
                break
            time.sleep(0.1)
        ck("the detached run wrote the checkpoint", (Path(home) / ui.CHECKPOINT).exists())
        old = (Path(home) / ui.CHECKPOINT).stat().st_mtime_ns
        hook(home, {"MEMORY_LEDGER_USAGE_PROJECTS_DIR": str(projects)})
        time.sleep(0.5)
        ck("a second start inside the window spawns nothing", (Path(home) / ui.CHECKPOINT).stat().st_mtime_ns == old)
    with tempfile.TemporaryDirectory() as home:
        p = hook(home, {"MEMORY_LEDGER_NO_USAGE_INGEST": "1", "MEMORY_LEDGER_USAGE_PROJECTS_DIR": str(projects)})
        ck("the opt-out env var stops it: no stamp, no output", p.returncode == 0 and p.stdout == ""
           and not (Path(home) / ui.STAMP).exists())
    with tempfile.TemporaryDirectory() as home:
        (Path(home) / "settings.json").write_text('{"usage_ingest": false}')
        hook(home, {"MEMORY_LEDGER_USAGE_PROJECTS_DIR": str(projects)})
        ck("so does settings.json", not (Path(home) / ui.STAMP).exists())
    with tempfile.TemporaryDirectory() as home:
        p = hook(home + "/not-created", {"MEMORY_LEDGER_USAGE_PROJECTS_DIR": str(projects)}, stdin="garbage")
        ck("a brain that does not exist is not created by the hook, and garbage on stdin is fine",
           p.returncode == 0 and not Path(home + "/not-created").exists())
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    ck("it is bound to SessionStart startup", any(
        "usage_ingest.py" in h["command"] for blk in hooks["hooks"]["SessionStart"]
        if blk["matcher"] == "startup" for h in blk["hooks"]))


def test_end_to_end(projects: Path):
    print("\n== ingest -> render ==")
    weeks = ur.load_weeks()
    tags = [w["week"] for w in weeks]
    ck("the report reads what the ingest wrote", "2026-W03" in tags and "2026-W04" in tags)
    page = ur.render([w for w in weeks if w["week"] in ("2026-W03", "2026-W04")])
    ck("it draws the page, repositories and all", page.startswith("<!doctype html>")
       and "alpha" in page and "beta" in page and "ABC-123" in page)
    ck("the habits are there: session length, compaction, cache read, no-write output",
       'id="ch-usage"' in page and "Never compacted" in page and "Cache read" in page and "outward action" in page)
    ck("cost by hour and weekday are drawn", 'id="ch-hour"' in page and 'id="ch-weekday"' in page)
    ck("rate-limit hit shows as a hard stop", "five-hour" in page)
    ck("deterministic", page == ur.render([w for w in ur.load_weeks() if w["week"] in ("2026-W03", "2026-W04")]))
    env = {**os.environ, "SECOND_BRAIN_HOME": HOME, "MEMORY_LEDGER_USAGE_PROJECTS_DIR": str(projects)}
    p = subprocess.run([sys.executable, str(SCRIPTS / "usage_report.py"), "render", "--ingest"],
                       capture_output=True, text=True, env=env)
    ck("render --ingest ingests first, then renders", p.returncode == 0 and "Where the Quota Went" in p.stdout, p.stderr[:200])
    with tempfile.TemporaryDirectory() as home:
        env2 = {**env, "SECOND_BRAIN_HOME": home}
        p = subprocess.run([sys.executable, str(SCRIPTS / "usage_report.py"), "render", "--ingest"],
                           capture_output=True, text=True, env=env2)
        ck("on a fresh brain render --ingest fills it from the logs", p.returncode == 0 and "alpha" in p.stdout, p.stderr[:200])
        p = subprocess.run([sys.executable, str(SCRIPTS / "usage_report.py"), "--db", "x.db", "render", "--ingest"],
                           capture_output=True, text=True, env=env2)
        ck("--ingest with --db is refused", p.returncode == 2)
    ck("an older node without the hour series still renders (no rhythm section)",
       'id="rhythm"' not in ur.render([{"id": "x", "week": "2026-W01", "from": "2025-12-29", "to": "2026-01-04",
                                        "source": "", "metrics": {"costUSD": 1.0, "sessions": 1, "turns": 2,
                                                                  "byModel": {"claude-opus-5": 1.0}}}]))


def main():
    base = Path(tempfile.mkdtemp(prefix="ml-usage-logs-"))
    projects = make_world(base)
    test_cost_math()
    test_parsing(projects)
    test_weeks(projects)
    test_write_and_idempotency(projects)
    test_checkpoint(projects)
    test_cli_and_hook(projects)
    test_end_to_end(projects)
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
