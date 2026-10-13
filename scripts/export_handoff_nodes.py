#!/usr/bin/env python3
"""Export the non-personal work nodes of this brain as a `second-brain-nodes/1` file.

    python3 scripts/export_handoff_nodes.py                       # exports/handoff/nodes-YYYYMMDD.json
    python3 scripts/export_handoff_nodes.py OUT.json --owner "Ana" --for "Bo" --assignee ana

The file is read by scripts/import_nodes.py in another ledger. Read-only on graph.db.

Selected: projects, artifacts, resources, references, decisions, Jira issues, PRs,
and open tasks/subtasks assigned to --assignee (or to nobody), plus the edges among them.
Never selected: the personal and rules-backup realms, captures/conversations/messages,
done or cancelled tasks, tasks of other people, and anything whose title or the start of
whose body matches the personal keywords.
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

FORMAT = "second-brain-nodes/1"
OPEN = ("open", "doing", "waiting", "blocked", "")
KEEP_ALWAYS = ("project", "artifact", "resource", "reference", "decision", "jira-issue", "pr")
SKIP_REALMS = ("personal", "rules-backup")
PERSONAL = re.compile(r"sa[uú]de|terapi|neurodiv|\badhd\b|psic|m[eé]dic|consulta m|home brain|pessoal|personal", re.I)
FIELDS = ("id", "type", "title", "body", "status", "due", "created", "updated", "realm")


def home() -> Path:
    return Path(os.environ.get("SECOND_BRAIN_HOME") or Path(__file__).resolve().parent.parent)


def db_path() -> Path:
    return Path(os.environ.get("SECOND_BRAIN_DB") or home() / "graph.db")


def keep(row: sqlite3.Row, assignee: str) -> bool:
    if PERSONAL.search(row["title"] or "") or PERSONAL.search((row["body"] or "")[:600]):
        return False
    if row["type"] in ("task", "subtask"):
        who = (json.loads(row["props"] or "{}").get("assignee") or "").lower()
        return (row["status"] or "") in OPEN and who in (assignee.lower(), "")
    return row["type"] in KEEP_ALWAYS


def build(path: Path, owner: str, recipient: str, assignee: str) -> dict:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    marks = ",".join("?" * len(SKIP_REALMS))
    rows = db.execute(f"select * from nodes where realm not in ({marks})", SKIP_REALMS).fetchall()
    sel = {r["id"]: r for r in rows if keep(r, assignee)}
    nodes = [{**{k: r[k] for k in FIELDS}, "props": json.loads(r["props"] or "{}")} for r in sel.values()]
    edges = [dict(e) for e in db.execute("select src,dst,rel,props,created from edges")
             if e["src"] in sel and e["dst"] in sel]
    counts: dict = {}
    for n in nodes:
        counts[n["type"]] = counts.get(n["type"], 0) + 1
    repos = sorted({n["props"]["repo"] for n in nodes if n["type"] == "pr" and n["props"].get("repo")})
    return {"format": FORMAT, "owner": owner, "for": recipient,
            "exported": datetime.datetime.now().isoformat(timespec="seconds"),
            "excluded": "personal realm, captures, conversations, messages, done/cancelled tasks, tasks of others",
            "counts": counts, "repos": repos, "nodes": nodes, "edges": edges}


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("out", nargs="?", type=Path,
                    help="where to write (default: exports/handoff/nodes-YYYYMMDD.json under the brain)")
    ap.add_argument("--owner", default=os.environ.get("MEMORY_LEDGER_OWNER", ""), help="name recorded as the sender")
    ap.add_argument("--for", dest="recipient", default="", help="name recorded as the recipient")
    ap.add_argument("--assignee", default="gabriel", help="whose open tasks to include (default: gabriel)")
    args = ap.parse_args(argv)
    out = args.out or home() / "exports" / "handoff" / f"nodes-{datetime.date.today():%Y%m%d}.json"
    bundle = build(db_path(), args.owner, args.recipient, args.assignee)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"file": str(out), "counts": bundle["counts"], "edges": len(bundle["edges"]),
                      "repos": len(bundle["repos"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
