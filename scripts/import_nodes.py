#!/usr/bin/env python3
"""Import a `second-brain-nodes/1` file (made by export_handoff_nodes.py) into this brain.

    python3 scripts/import_nodes.py FILE                 # dry run: what would happen, change nothing
    python3 scripts/import_nodes.py FILE --apply         # write it
    python3 scripts/import_nodes.py FILE --apply --update   # also refresh nodes already here

What it does with each node:
  - a node whose id is not here is added, with its original id, so a second run
    (and the edges) line up;
  - a node whose id is already here is left alone, unless --update, which
    refreshes title/body/status/due/props but never touches realm;
  - every imported node gets props.imported_from (the file's owner) and
    props.imported_at, so it is always possible to tell what came from the file;
  - realm is forced to --realm (default "handoff"), never the sender's realm, so
    the import does not land in one of your own sections;
  - an edge is added only when both ends exist here; the rest are counted.

Safety: a dry run is the default; --apply snapshots the database first (the same
backup consolidate takes, restorable with scripts/restore.py); nothing is pushed
to neo4j (the changes are queued and go out on the next sync); a file in another
format is refused before anything is read into the graph.
"""
import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("SECOND_BRAIN_NO_AUTOSYNC", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import graph  # noqa: E402

FORMAT = "second-brain-nodes/1"
REQUIRED = ("id", "type", "title")


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        raise SystemExit(f"refused: format is {data.get('format')!r}, expected {FORMAT!r}")
    for n in data.get("nodes", []):
        missing = [k for k in REQUIRED if not n.get(k)]
        if missing:
            raise SystemExit(f"refused: node without {missing}: {str(n)[:120]}")
    return data


def run(con, data: dict, *, apply: bool, update: bool, realm: str) -> dict:
    sender = data.get("owner", "")
    stamp = graph.now()
    res = {"file_nodes": len(data["nodes"]), "file_edges": len(data.get("edges", [])),
           "added": 0, "updated": 0, "skipped_existing": 0,
           "edges_added": 0, "edges_existing": 0, "edges_dangling": 0, "by_type": {}}
    present = {r[0] for r in con.execute("select id from nodes")}
    for n in data["nodes"]:
        props = {**(n.get("props") or {}), "imported_from": sender, "imported_at": stamp}
        exists = n["id"] in present
        if exists and not update:
            res["skipped_existing"] += 1
            continue
        key = "updated" if exists else "added"
        res[key] += 1
        res["by_type"][n["type"]] = res["by_type"].get(n["type"], 0) + 1
        if not apply:
            present.add(n["id"])
            continue
        if exists:
            graph.api_update_node(con, n["id"], title=n["title"], body=n.get("body") or "",
                                  status=n.get("status") or "", due=n.get("due") or "",
                                  props_patch=props)
        else:
            graph.api_create_node(con, n["type"], n["title"], n.get("body") or "", props,
                                  n.get("status") or "", n.get("due") or "", realm, n["id"])
            # keep the sender's timestamps, so age and staleness stay truthful
            if n.get("created"):
                con.execute("update nodes set created=?, updated=? where id=?",
                            (n["created"], n.get("updated") or n["created"], n["id"]))
                con.commit()
        present.add(n["id"])
    have = {(r[0], r[1], r[2]) for r in con.execute("select src,dst,rel from edges")}
    for e in data.get("edges", []):
        if e["src"] not in present or e["dst"] not in present:
            res["edges_dangling"] += 1
        elif (e["src"], e["dst"], e["rel"]) in have:
            res["edges_existing"] += 1
        else:
            res["edges_added"] += 1
            if apply:
                graph.api_link(con, e["src"], e["dst"], e["rel"], json.loads(e.get("props") or "{}")
                               if isinstance(e.get("props"), str) else (e.get("props") or {}))
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file")
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--update", action="store_true", help="refresh nodes already present")
    ap.add_argument("--realm", default="handoff", help="realm for imported nodes (default: handoff)")
    a = ap.parse_args()
    data = load(Path(a.file).expanduser())
    con = graph.connect()
    backup = graph.backup_db("pre-import-nodes") if a.apply else None
    res = run(con, data, apply=a.apply, update=a.update, realm=a.realm)
    print(json.dumps({"ok": True, "applied": a.apply, "db": str(graph.db_path()),
                      "backup": backup, "from": data.get("owner"), **res}, indent=1))


if __name__ == "__main__":
    main()
