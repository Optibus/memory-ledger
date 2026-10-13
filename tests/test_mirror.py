#!/usr/bin/env python3
"""The neo4j mirror is a COMPLETE copy (docs/neo4j.md#what-the-master-holds).

    python3 tests/test_mirror.py

Stdlib only, no server. The master is replaced at its two seams: `_neo4j_call`
records what would be sent, and the restore is fed the master's state exactly as
neo4j returns it (properties, nulls dropped) — built from what was pushed.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = tempfile.mkdtemp(prefix="second-brain-mirror-")
os.environ["SECOND_BRAIN_HOME"] = HOME
os.environ["SECOND_BRAIN_NO_AUTOSYNC"] = "1"
for k in list(os.environ):
    if k.startswith(("SECOND_BRAIN_NEO4J", "MEMORY_LEDGER_NEO4J", "SECOND_BRAIN_DB",
                     "MEMORY_LEDGER_DB", "MEMORY_LEDGER_HOME")):
        del os.environ[k]
sys.path.insert(0, str(ROOT / "scripts"))

import graph  # noqa: E402
import trigger  # noqa: E402
import brain_sync  # noqa: E402

FAILS: list[str] = []
SENT: list[list] = []


def ck(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def fake_call(statements, timeout=30):
    SENT.append(statements)
    return True, None


graph._neo4j_call = fake_call


def queued(con):
    return {(r["kind"], r["ref"]) for r in con.execute("SELECT kind, ref FROM outbox")}


def sent_params(key):
    return [s["parameters"][key] for batch in SENT for s in batch
            if key in s.get("parameters", {})]


def seed(con):
    ts = "2026-09-29T10:00:00+00:00"
    con.execute("INSERT INTO nodes(id,type,title,body,props,created,updated) VALUES"
                "('n_a','note','A','body a',?,?,?)",
                (json.dumps({"status": "inner", "mixed": [1, "a"], "deep": {"k": 1}}), ts, ts))
    con.execute("INSERT INTO nodes(id,type,title,props,created,updated) VALUES"
                "('n_b','note','B','{}',?,?)", (ts, ts))
    con.execute("INSERT INTO edges(src,dst,rel,props,created) VALUES"
                "('n_a','n_b','mentions','{\"w\": 2}',?)", (ts,))
    con.execute("INSERT INTO coactivation(a,b,n,last) VALUES('n_a','n_b',3,?)", (ts,))
    con.execute("INSERT INTO trigger_checkpoint(source,cursor,updated) VALUES('slack','c1',?)",
                (ts,))
    con.commit()


def test_triggers(con):
    print("== every write is queued, whoever makes it ==")
    q = queued(con)
    ck("a direct-SQL node insert", ("node", "n_a") in q)
    ck("an edge", ("edge", "n_a\x1fn_b\x1fmentions") in q)
    ck("a co-activation row", ("row", 'coactivation\x1f["n_a","n_b"]') in q)
    ck("a trigger-engine row", ("row", 'trigger_checkpoint\x1f["slack"]') in q)
    con.execute("DELETE FROM outbox")
    con.execute("DELETE FROM nodes WHERE id='n_b'")
    q = queued(con)
    ck("a delete, and the edge its cascade removed",
       ("node", "n_b") in q and ("edge", "n_a\x1fn_b\x1fmentions") in q, q)


def test_state_wins(con):
    print("== a flush pushes current state, not the queued kind ==")
    ts = "2026-09-29T10:00:00+00:00"
    con.execute("INSERT INTO nodes(id,type,title,props,created,updated) VALUES"
                "('n_b','note','B again','{}',?,?)", (ts, ts))
    con.execute("INSERT INTO edges(src,dst,rel,props,created) VALUES"
                "('n_a','n_b','mentions','{\"w\": 2}',?)", (ts,))
    con.commit()
    SENT.clear()
    res = graph._flush(con)
    ids = [n["id"] for batch in sent_params("nodes") for n in batch]
    dels = [i for batch in sent_params("ids") for i in batch]
    ck("deleted then recreated is an upsert", "n_b" in ids and "n_b" not in dels, res)
    ck("the queue is drained", res.get("pending") == 0, res)


def test_payload(con):
    print("== the payload loses nothing ==")
    p = graph._node_payload(con, "n_a")
    props = json.loads(p["_props"])
    ck("a prop named like a column survives in _props",
       props.get("status") == "inner" and p["status"] == "")
    ck("a mixed list is sent as JSON, not as a list neo4j would refuse",
       p["mixed"] == json.dumps([1, "a"]), p.get("mixed"))
    e = graph._edge_payload(con, "n_a", "n_b", "mentions")
    ck("an edge keeps its props and created",
       json.loads(e["p"]["_props"]) == {"w": 2} and e["p"]["created"])


def test_restore(con):
    print("== a restore rebuilds the same brain ==")
    Path(HOME, "scripts").mkdir(exist_ok=True)
    Path(HOME, "scripts", "mine.py").write_text("print('mine')\n", encoding="utf-8")
    Path(HOME, "neo4j.json").write_text('{"password": "secret"}', encoding="utf-8")
    files = brain_sync.local_files(Path(HOME))
    paths = {f["path"] for f in files}
    ck("user scripts are mirrored", "scripts/mine.py" in paths, paths)
    ck("secrets never are", "neo4j.json" not in paths, paths)
    # the master's state, shaped as neo4j returns it: properties, nulls dropped
    master = {
        "nodes": [brain_sync._drop_nulls(n) for n in brain_sync.local_nodes(con)],
        "edges": [[e["src"], e["dst"], brain_sync._drop_nulls(e["p"])]
                  for e in brain_sync.local_edges(con)],
        "rows": [[r["table"], r["key"], r["data"]] for r in brain_sync.local_rows(con)],
        "files": [[f["path"], f["content"], f["sha"]] for f in files],
    }
    live = graph.db_path()
    target = Path(HOME) / "restored.db"
    counts = brain_sync.build_restore(target, master)
    ck("the restored file passes its integrity check", counts["integrity"] == "ok", counts)
    import sqlite3
    a, b = sqlite3.connect(str(live)), sqlite3.connect(str(target))
    for name, sql in [
            ("nodes", "SELECT id,type,title,body,props,status,due,realm,created,updated FROM nodes"),
            ("edges", "SELECT src,dst,rel,props,created FROM edges"),
            ("coactivation", "SELECT * FROM coactivation"),
            ("trigger_checkpoint", "SELECT * FROM trigger_checkpoint")]:
        x, y = set(a.execute(sql).fetchall()), set(b.execute(sql).fetchall())
        ck(f"{name} come back exactly", x == y, (x ^ y))
    ck("and the restore does not queue itself for the master",
       b.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0)
    for prefix in ("MEMORY_LEDGER_", "SECOND_BRAIN_"):
        os.environ.pop(prefix + "DB", None)
    fresh = Path(tempfile.mkdtemp(prefix="second-brain-mirror-home-"))
    written = brain_sync.restore_files(fresh, master["files"], overwrite=False)
    ck("home files are written back", (fresh / "scripts" / "mine.py").read_text(encoding="utf-8")
       == "print('mine')\n", written)


def fake_master(changes: dict):
    """A master that answers each pull query with canned rows."""
    def query(statement, params=None, timeout=60):
        if "SyncClock" in statement:
            return [[changes.get("clock", 10)]], None
        for key, marker in (("files", "MirrorFile"), ("tombs", "Tombstone"),
                            ("rows", "MirrorRow"), ("edges", "[x:REL]"),
                            ("nodes", "(x:Node)")):
            if marker in statement:
                return changes.get(key, []), None
        return [], None
    graph._neo4j_query = query


def test_pull(con):
    print("== a pull brings other machines' changes down ==")
    con.execute("DELETE FROM outbox")
    con.commit()
    theirs = {"id": "n_theirs", "type": "note", "title": "From B", "body": "",
              "status": "", "due": "", "realm": "default", "_props": '{"x": 1}',
              "created": "2026-09-29T11:00:00+00:00", "updated": "2026-09-29T11:00:00+00:00"}
    stale_a = {"id": "n_a", "type": "note", "title": "OLD A", "_props": "{}",
               "created": "2026-01-01T00:00:00+00:00", "updated": "2026-01-01T00:00:00+00:00"}
    fake_master({"clock": 10,
                 "nodes": [[theirs], [stale_a]],
                 "edges": [["n_theirs", "n_a", {"rel": "about", "created": "t",
                                                "_props": '{"w": 5}'}]],
                 "rows": [["trigger_gate", '["from-b"]',
                           json.dumps({"scope": "from-b", "enabled": 1, "at": "t"})]]})
    res = brain_sync.pull_changes(con, Path(HOME))
    ck("a new node arrives", con.execute(
        "SELECT props FROM nodes WHERE id='n_theirs'").fetchone()[0] == '{"x": 1}', res)
    ck("an older master copy does not overwrite a newer local one",
       con.execute("SELECT title FROM nodes WHERE id='n_a'").fetchone()[0] == "A")
    ck("its edge and row arrive",
       con.execute("SELECT props FROM edges WHERE src='n_theirs'").fetchone()[0] == '{"w": 5}'
       and con.execute("SELECT enabled FROM trigger_gate WHERE scope='from-b'").fetchone())
    ck("nothing pulled is queued to go back up",
       con.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0)
    ck("the pull remembers how far it got",
       graph._sync_get(con, "pull_seq") == "10")

    print("== deletes come down, but never over a local edit ==")
    con.execute("UPDATE nodes SET title='edited here' WHERE id='n_a'")
    con.commit()
    fake_master({"clock": 12, "tombs": [["node", "n_theirs"], ["node", "n_a"]]})
    brain_sync.pull_changes(con, Path(HOME))
    ck("a node deleted elsewhere is deleted here, with its edges",
       not con.execute("SELECT 1 FROM nodes WHERE id='n_theirs'").fetchone()
       and not con.execute("SELECT 1 FROM edges WHERE src='n_theirs'").fetchone())
    ck("a node still being edited here survives the delete",
       con.execute("SELECT title FROM nodes WHERE id='n_a'").fetchone()[0] == "edited here")

    print("== a file changed on both sides keeps both ==")
    f = Path(HOME, "scripts", "both.py")
    f.write_text("base\n", encoding="utf-8")
    graph._sync_set(con, "file:scripts/both.py", brain_sync._sha("base\n"))
    f.write_text("local edit\n", encoding="utf-8")
    fake_master({"clock": 13, "files": [["scripts/both.py", "their edit\n",
                                         brain_sync._sha("their edit\n"), "machine-b"]]})
    res = brain_sync.pull_changes(con, Path(HOME))
    side = Path(HOME, "scripts", "both.py.conflict-machine-b")
    ck("the local file stays", f.read_text(encoding="utf-8") == "local edit\n")
    ck("the other copy is written beside it", side.exists()
       and side.read_text(encoding="utf-8") == "their edit\n", res.get("conflicts"))
    ck("and a conflict copy is never mirrored itself",
       "scripts/both.py.conflict-machine-b" not in
       {x["path"] for x in brain_sync.local_files(Path(HOME))})
    g = Path(HOME, "scripts", "untouched.py")
    g.write_text("v1\n", encoding="utf-8")
    graph._sync_set(con, "file:scripts/untouched.py", brain_sync._sha("v1\n"))
    fake_master({"clock": 14, "files": [["scripts/untouched.py", "v2\n",
                                         brain_sync._sha("v2\n"), "machine-b"]]})
    brain_sync.pull_changes(con, Path(HOME))
    ck("a file changed only elsewhere is updated here", g.read_text(encoding="utf-8") == "v2\n")


def main():
    try:
        con = trigger.db()
        seed(con)
        test_triggers(con)
        test_state_wins(con)
        test_payload(con)
        test_restore(con)
        test_pull(con)
    finally:
        import shutil
        shutil.rmtree(HOME, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
