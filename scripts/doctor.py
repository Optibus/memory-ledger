#!/usr/bin/env python3
"""memory-ledger health check.

Zero-touch install removed the one moment (`graph.py init`, run by hand) that
used to prove the ledger was wired up. Now the home dir, DB and schema are
created silently on first hook fire, which is convenient but also means there
is no longer an obvious signal that anything is actually happening: a broken
hook, a stray `$MEMORY_LEDGER_OFF=1` left in an rc file, or a `.disabled`
marker nobody remembers writing can all sit invisibly for weeks. `doctor` is
the answer to "is it capturing?" — it inspects the same paths, env vars and
settings the engine itself reads (via `graph.py`, imported directly, never
re-implemented) and reports what it finds in plain language.

Defensive by construction: every section is wrapped so a single unreadable
file (a corrupt settings.json, a missing hooks.json, a locked DB) reports its
own failure and the rest of the report still runs. `doctor` must never
traceback — a health-check tool that crashes is the least trustworthy kind.

Usage:
    python3 doctor.py           # human-readable report
    python3 doctor.py --json    # same data, machine-readable

Exit code: 0 when healthy. Non-zero when something is ACTIONABLY wrong — a
missing DB, a hook pointing at a file that does not exist, an unreadable
settings file. Being disabled is a choice, not a fault, and never affects the
exit code by itself.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graph as G  # noqa: E402  (path must be set up first)


def _section(name, fn):
    """Run one report section; never let it take the whole report down."""
    try:
        return fn()
    except Exception as e:  # a section that cannot be read must not hide the rest
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def check_status():
    off_env = G._env("OFF").strip().lower() in G._OFF_TRUTHY
    home = G.home_dir()
    marker = home / G.DISABLE_MARKER
    marker_set = marker.exists()
    is_disabled = off_env or marker_set
    if off_env:
        cause = "env var ($MEMORY_LEDGER_OFF or $SECOND_BRAIN_OFF)"
    elif marker_set:
        cause = f"marker file ({marker})"
    else:
        cause = None
    return {"ok": True, "enabled": not is_disabled, "disabled": is_disabled,
            "cause": cause, "env_off": off_env, "marker_present": marker_set,
            "marker_path": str(marker)}


def check_paths():
    home = G.home_dir()
    db = G.db_path()
    home_exists = home.exists()
    db_exists = db.exists()
    db_size = db.stat().st_size if db_exists else 0
    # How home is reached matters more than where it is. A symlink means this
    # machine predates the rename and was adopted; two real directories mean
    # two separate brains, and only a human can say which one is the brain.
    link = None
    if home.is_symlink():
        try:
            link = os.readlink(str(home))
        except OSError:
            link = "?"
    strays = [str(x) for x in G.legacy_homes()]
    return {"ok": db_exists, "home": str(home), "home_exists": home_exists,
            "home_link": link, "legacy_homes": strays,
            "db": str(db), "db_exists": db_exists, "db_size_bytes": db_size,
            "error": None if db_exists else "no graph.db — nothing has ever "
                     "connected here (or MEMORY_LEDGER_HOME/DB points at the "
                     "wrong place)"}


def _source_of(name, settings_dict):
    """Where a config value would come from, in the engine's own precedence
    order: explicit env var, then settings.json, then built-in default."""
    if G._env(name):
        return "env"
    if settings_dict.get(name.lower()):
        return "settings.json"
    return "default"


def check_config():
    s = G.settings()
    tz = G.setting("tz", "") or "UTC (implicit)"
    realm = G.DEFAULT_REALM
    return {"ok": True,
            "tz": tz, "tz_source": _source_of("TZ", s),
            "realm": realm, "realm_source": _source_of("REALM", s),
            # Who "me" is. A brain answering --mine with someone else's name is
            # not broken in any way a path check would notice, which is exactly
            # why it belongs in the report.
            "owner": G.owner(), "owner_source": _source_of("OWNER", s),
            # The switches. `autosync` off means writes queue for the mirror and
            # never push — silent, correct, and the first thing to check when the
            # master looks stale.
            "autosync": G.flag("autosync"),
            "autosync_source": G.flag_source("autosync"),
            "consolidate": G.flag("consolidate"),
            "consolidate_source": G.flag_source("consolidate"),
            "settings_path": str(G.settings_path()),
            "settings_readable": G.settings_path().exists() and bool(s) or not G.settings_path().exists()}


def check_content():
    if not G.db_path().exists():
        return {"ok": False, "error": "no DB — see paths section"}
    con = G.connect()
    try:
        ntot = con.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"]
        by_type = {r["type"]: r["c"] for r in con.execute(
            "SELECT type, COUNT(*) c FROM nodes GROUP BY type "
            "ORDER BY c DESC LIMIT 10").fetchall()}
        open_loops = 0
        try:
            for r in con.execute("SELECT * FROM nodes").fetchall():
                d = G._node_dict(r)
                if G._is_open_loop(d):
                    open_loops += 1
        except Exception:
            open_loops = -1  # unknown, not fatal
        last_capture_row = con.execute(
            "SELECT created FROM nodes WHERE type='capture' "
            "ORDER BY created DESC LIMIT 1").fetchone()
        last_capture = last_capture_row["created"] if last_capture_row else None
        stale_captures = True
        age_days = None
        if last_capture:
            try:
                dt = datetime.fromisoformat(last_capture)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - dt).days
                stale_captures = age_days > 2
            except Exception:
                pass
        warning = None
        if last_capture is None:
            warning = "zero capture nodes ever recorded — the hooks may not be firing"
        elif stale_captures:
            warning = f"newest capture is {age_days} day(s) old (>2) — capture may have stopped"
        return {"ok": True, "total_nodes": ntot, "by_type": by_type,
                "open_loops": open_loops, "last_capture": last_capture,
                "last_capture_age_days": age_days, "warning": warning}
    finally:
        con.close()


def check_hooks():
    hooks_json = Path(__file__).resolve().parent.parent / "hooks" / "hooks.json"
    if not hooks_json.exists():
        return {"ok": False, "error": f"hooks.json not found at {hooks_json}"}
    try:
        cfg = json.loads(hooks_json.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"hooks.json unreadable: {e}"}
    events = {}
    all_ok = True
    hooks_dir = hooks_json.parent
    for event, entries in (cfg.get("hooks") or {}).items():
        # One script is typically registered once per matcher (SessionStart has
        # four: startup/resume/clear/compact). Listing each registration on its
        # own line prints the same name four times with nothing to tell them
        # apart, so collapse by script and carry the matchers along instead.
        by_script = {}
        for entry in entries:
            matcher = entry.get("matcher") or "*"
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                # pull the script path out of `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/x.py"`
                path_str = None
                if "/hooks/" in cmd:
                    tail = cmd.split("/hooks/", 1)[1]
                    fname = tail.split('"')[0].split("'")[0].strip()
                    path_str = fname
                exists = None
                if path_str:
                    exists = (hooks_dir / path_str).exists()
                    if not exists:
                        all_ok = False
                rec = by_script.setdefault(
                    path_str or cmd,
                    {"command": cmd, "script": path_str, "exists": exists, "matchers": []})
                rec["matchers"].append(matcher)
        events[event] = list(by_script.values())
    return {"ok": all_ok, "hooks_json": str(hooks_json), "events": events}


def check_mirror():
    cfg = G._neo4j_cfg()
    configured = bool(cfg.get("url") and cfg.get("password"))
    result = {"ok": True, "configured": configured}
    if not configured:
        return result
    if not G.db_path().exists():
        result["pending_outbox"] = None
        return result
    con = G.connect()
    try:
        pending = con.execute("SELECT COUNT(*) c FROM outbox").fetchone()["c"]
        result["pending_outbox"] = pending
    finally:
        con.close()
    return result


def run() -> dict:
    status = _section("status", check_status)
    paths = _section("paths", check_paths)
    config = _section("config", check_config)
    content = _section("content", check_content)
    hooks = _section("hooks", check_hooks)
    mirror = _section("mirror", check_mirror)

    # Healthy unless something ACTIONABLE is broken. Disabled is a choice.
    problems = []
    if paths.get("ok") is False:
        problems.append(paths.get("error") or "path check failed")
    if hooks.get("ok") is False:
        problems.append("one or more hooks reference a missing script")
    if config.get("ok") is False:
        problems.append(config.get("error") or "config unreadable")
    healthy = not problems

    return {
        "ok": healthy,
        "problems": problems,
        "status": status,
        "paths": paths,
        "config": config,
        "content": content,
        "hooks": hooks,
        "mirror": mirror,
    }


def render_human(report: dict) -> str:
    lines = []
    ok = "✓"
    bad = "✗"
    warn = "!"

    st = report["status"]
    lines.append("memory-ledger doctor")
    lines.append("=" * 21)
    lines.append("")
    lines.append("Status:")
    if st.get("ok"):
        if st["enabled"]:
            lines.append(f"  {ok} enabled")
        else:
            lines.append(f"  {warn} disabled  (cause: {st['cause']})")
    else:
        lines.append(f"  {bad} could not determine status: {st.get('error')}")

    p = report["paths"]
    lines.append("")
    lines.append("Paths:")
    if p.get("ok") is not None:
        lines.append(f"  home: {p.get('home')}  ({'exists' if p.get('home_exists') else 'MISSING'})")
        if p.get("home_link"):
            lines.append(f"        → {p['home_link']}  (adopted from the pre-rename name)")
        if p.get("db_exists"):
            lines.append(f"  db:   {p.get('db')}  (exists, {p.get('db_size_bytes')} bytes)")
        else:
            lines.append(f"  db:   {p.get('db')}  {bad} MISSING — {p.get('error')}")
        # Not a warning by itself -- someone may keep an old brain on purpose --
        # but it is the one thing that silently explains "my notes vanished".
        for stray in p.get("legacy_homes") or []:
            lines.append(f"  {warn} another brain at {stray}, not the one in use")

    c = report["config"]
    lines.append("")
    lines.append("Config:")
    if "error" in c and c.get("ok") is False:
        lines.append(f"  {bad} {c['error']}")
    else:
        lines.append(f"  tz:    {c.get('tz')}  (from {c.get('tz_source')})")
        lines.append(f"  realm: {c.get('realm')}  (from {c.get('realm_source')})")
        lines.append(f"  owner: {c.get('owner')}  (from {c.get('owner_source')})")
        for key in ("autosync", "consolidate"):
            state = "on" if c.get(key) else "off"
            lines.append(f"  {key + ':':13}{state}  (from {c.get(key + '_source')})")

    ct = report["content"]
    lines.append("")
    lines.append("Content:")
    if ct.get("ok") is False:
        lines.append(f"  {bad} {ct.get('error')}")
    else:
        lines.append(f"  total nodes: {ct.get('total_nodes')}")
        for t, n in (ct.get("by_type") or {}).items():
            lines.append(f"    {t}: {n}")
        lines.append(f"  open loops: {ct.get('open_loops')}")
        lc = ct.get("last_capture")
        lines.append(f"  last capture: {lc or 'never'}"
                      + (f"  ({ct.get('last_capture_age_days')}d ago)" if lc else ""))
        if ct.get("warning"):
            lines.append(f"  {warn} {ct['warning']}")

    h = report["hooks"]
    lines.append("")
    lines.append("Hook registration:")
    if h.get("ok") is False and "error" in h:
        lines.append(f"  {bad} {h['error']}")
    else:
        for event, scripts in (h.get("events") or {}).items():
            for s in scripts:
                mark = ok if s["exists"] else (bad if s["exists"] is False else "?")
                matchers = [m for m in s.get("matchers", []) if m and m != "*"]
                where = f"  ({', '.join(matchers)})" if matchers else ""
                lines.append(f"  {mark} {event}: {s['script']}{where}")

    m = report["mirror"]
    lines.append("")
    lines.append("Mirror (neo4j):")
    if not m.get("configured"):
        lines.append("  not configured")
    else:
        pend = m.get("pending_outbox")
        lines.append(f"  configured  (pending outbox: {pend if pend is not None else 'unknown'})")

    lines.append("")
    if report["ok"]:
        lines.append(f"{ok} healthy")
    else:
        lines.append(f"{bad} problems found:")
        for pr in report["problems"]:
            lines.append(f"  - {pr}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="doctor", description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    try:
        report = run()
    except Exception as e:  # doctor itself must never traceback
        report = {"ok": False, "problems": [f"doctor crashed: {type(e).__name__}: {e}"]}

    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        print(render_human(report))

    sys.exit(0 if report.get("ok") else 1)


if __name__ == "__main__":
    main()
