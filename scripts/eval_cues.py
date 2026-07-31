#!/usr/bin/env python3
"""Measure whether the genome actually fires — does the right rule or skill come
back for a realistic phrasing?

`match-rules` and `match-skills` are lexical: FTS5 over title, body and the `cues`
column (`props.scenario` for rules, `props.triggers` / `props.description` for
skills). Whether that works is an empirical question, and cue words are easy to
tune into a number that means nothing. This makes the number honest.

    python3 scripts/eval_cues.py                    # every set in the cases file
    python3 scripts/eval_cues.py --set fresh        # one set
    python3 scripts/eval_cues.py --min-hit3 80      # exit 1 below the threshold
    python3 scripts/eval_cues.py --quiet            # scores only, no misses

CASES ARE NOT IN THIS REPO. They name a person's own rules and skills, so they are
personal data. Resolution order:

    --cases <path>
    $SECOND_BRAIN_EVAL_CASES
    $SECOND_BRAIN_HOME/eval_cases.json   (default: ~/.second_brain/eval_cases.json)
    ./eval-cases.json                    (gitignored)

`eval-cases.example.json` documents the format.

## Reading the result

**hit@3 is the metric that matters.** Several rules come back and the model reads
them all, so rank 2 is a hit in practice. hit@1 is a tie-breaker.

**Tune against one set, report against another.** Cue words tuned to a set you can
see will score near 100% on it and much lower on phrasings you have not thought of.
Measured on this brain, 2026-07-31: two rounds of cue expansion reached 89-100% on
the sets they were written against, and 31% (rules) / 66% (skills) hit@1 on a third
set written afterwards. Keep at least one set you never tune against, and quote
that one.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import graph  # noqa: E402  (the engine — same matcher the skills call)

KINDS = {"rules": ("cmd_match_rules", "rule"), "skills": ("cmd_match_skills", "skill")}


def cases_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("SECOND_BRAIN_EVAL_CASES")
    if env:
        return Path(env).expanduser()
    home = Path(os.environ.get("SECOND_BRAIN_HOME", "~/.second_brain")).expanduser()
    default = home / "eval_cases.json"
    return default if default.exists() else Path("eval-cases.json")


def _sets(data: dict):
    """The real sets, skipping `_`-prefixed documentation keys and anything that
    is not a mapping — the example file carries a `_readme` array."""
    return [(name, body) for name, body in data.items()
            if not name.startswith("_") and isinstance(body, dict)]


def match(kind: str, query: str, limit: int) -> list[str]:
    """Call the real matcher and read back the ids it would hand the model."""
    cmd = getattr(graph, KINDS[kind][0])
    args = argparse.Namespace(query=query, limit=limit, budget=100_000,
                              json=True, full=False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd(args)
    try:
        payload = json.loads(buf.getvalue())
    except json.JSONDecodeError:
        return []
    rows = payload.get("results") or payload.get("rules") or payload.get("skills") or []
    return [r["id"] for r in rows]


def score(kind: str, cases: list, limit: int) -> dict:
    rows = []
    for case in cases:
        query, want = case["query"], case["expect"]
        got = match(kind, query, limit)
        rank = got.index(want) + 1 if want in got else None
        rows.append({"query": query, "expect": want, "rank": rank, "top": got[:3]})
    n = len(rows) or 1
    return {"n": len(rows), "rows": rows,
            "hit1": sum(1 for r in rows if r["rank"] == 1) * 100 // n,
            "hit3": sum(1 for r in rows if r["rank"] and r["rank"] <= 3) * 100 // n}


def check_ids(data: dict) -> list[str]:
    """An expectation naming a node that does not exist is a broken case, not a
    failing matcher — ids get truncated to 48 chars on creation and are easy to
    mistype. Surface that rather than reporting it as a miss."""
    con = graph.connect()
    known = {r["id"] for r in con.execute(
        "SELECT id FROM nodes WHERE type IN ('rule','skill')")}
    bad = []
    for name, sets in _sets(data):
        for kind, cases in sets.items():
            if kind not in KINDS:
                continue
            for c in cases:
                if c["expect"] not in known:
                    bad.append(f"{name}/{kind}: {c['expect']}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", help="path to the cases file (see resolution order)")
    ap.add_argument("--set", dest="only", help="run only this named set")
    ap.add_argument("--limit", type=int, default=8, help="how deep to look (default 8)")
    ap.add_argument("--min-hit3", type=int, help="exit 1 if any set scores below this")
    ap.add_argument("--quiet", action="store_true", help="scores only, no miss detail")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    path = cases_path(args.cases)
    if not path.exists():
        print(f"no cases file at {path}\n"
              f"copy eval-cases.example.json there and fill in your own cases "
              f"(they name your rules/skills, so they stay out of the repo)",
              file=sys.stderr)
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))

    bad = check_ids(data)
    if bad:
        print("cases naming nodes that do not exist:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 2

    out, worst = {}, 100
    for name, sets in _sets(data):
        if args.only and name != args.only:
            continue
        out[name] = {}
        for kind, cases in sets.items():
            if kind not in KINDS:
                continue
            res = score(kind, cases, args.limit)
            out[name][kind] = res
            worst = min(worst, res["hit3"])

    if args.json:
        print(json.dumps({"cases": str(path), "sets": out}, indent=2))
    else:
        print(f"cases: {path}")
        for name, kinds in out.items():
            for kind, res in kinds.items():
                print(f"\n=== {name} / {kind}  ({res['n']} cases)"
                      f"   hit@1 {res['hit1']}%   hit@3 {res['hit3']}%")
                if args.quiet:
                    continue
                for r in res["rows"]:
                    if r["rank"] and r["rank"] <= 3:
                        continue
                    where = f"rank {r['rank']}" if r["rank"] else "ABSENT"
                    print(f"  MISS [{where:>7}] {r['query']!r}")
                    print(f"           want {r['expect']}")
                    print(f"           got  {r['top']}")

    if args.min_hit3 is not None and worst < args.min_hit3:
        print(f"\nFAIL: worst hit@3 {worst}% < --min-hit3 {args.min_hit3}%",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
