"""The neo4j mirror is a COMPLETE copy (docs/neo4j.md#what-the-master-holds).

    python -m pytest tests/test_mirror.py

No server. The master is replaced at its two seams: `_neo4j_call` records what
would be sent, and the restore is fed the master's state exactly as neo4j
returns it (properties, nulls dropped), built from what was pushed.

The classes share one brain and run in file order: triggers queue, a flush
drains, a restore rebuilds, a pull brings changes down.
"""

from __future__ import annotations

import os
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

import brain_sync
import graph
import trigger

UTF8 = "utf-8"
TS = "2026-09-29T10:00:00+00:00"
SEP = "\x1f"
EDGE_AB = f"n_a{SEP}n_b{SEP}mentions"
MINE_PY = "print('mine')\n"
DB_VARS = ("MEMORY_LEDGER_DB", "SECOND_BRAIN_DB")


def queued(con) -> set[tuple[str, str]]:
    return {(r["kind"], r["ref"]) for r in con.execute("SELECT kind, ref FROM outbox")}


@pytest.fixture(scope="module")
def mirror(module_brain_home: Path) -> Iterator[SimpleNamespace]:
    """A seeded brain, with the master replaced by a recorder (`sent`) and, per
    test, by `fake_master(...)` for pulls."""
    sent: list[list] = []

    def fake_call(statements, timeout=30):
        sent.append(statements)
        return True, None

    def sent_params(key):
        return [s["parameters"][key] for batch in sent for s in batch
                if key in s.get("parameters", {})]

    def fake_master(changes: dict) -> None:
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

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(graph, "_neo4j_call", fake_call)
        # fake_master() replaces this one; patching it to itself makes the module's end restore it
        mp.setattr(graph, "_neo4j_query", graph._neo4j_query)
        con = trigger.db()
        con.execute("INSERT INTO nodes(id,type,title,body,props,created,updated) VALUES"
                    "('n_a','note','A','body a',?,?,?)",
                    (json.dumps({"status": "inner", "mixed": [1, "a"], "deep": {"k": 1}}), TS, TS))
        con.execute("INSERT INTO nodes(id,type,title,props,created,updated) VALUES"
                    "('n_b','note','B','{}',?,?)", (TS, TS))
        con.execute("INSERT INTO edges(src,dst,rel,props,created) VALUES"
                    "('n_a','n_b','mentions','{\"w\": 2}',?)", (TS,))
        con.execute("INSERT INTO coactivation(a,b,n,last) VALUES('n_a','n_b',3,?)", (TS,))
        con.execute("INSERT INTO trigger_checkpoint(source,cursor,updated) VALUES('slack','c1',?)", (TS,))
        con.commit()
        yield SimpleNamespace(con=con, home=module_brain_home, sent=sent,
                              sent_params=sent_params, fake_master=fake_master)
        con.close()


# --------------------------------------------- every write is queued, whoever makes it
class TestQueue:
    @pytest.mark.parametrize("what, item", [
        ("a direct-SQL node insert", ("node", "n_a")),
        ("an edge", ("edge", EDGE_AB)),
        ("a co-activation row", ("row", f'coactivation{SEP}["n_a","n_b"]')),
        ("a trigger-engine row", ("row", f'trigger_checkpoint{SEP}["slack"]')),
    ], ids=["node", "edge", "coactivation", "trigger-row"])
    def test_it_is_queued(self, mirror, what, item):
        assert item in queued(mirror.con)

    def test_a_delete_and_the_edge_its_cascade_removed_are_queued(self, mirror):
        mirror.con.execute("DELETE FROM outbox")
        mirror.con.execute("DELETE FROM nodes WHERE id='n_b'")
        q = queued(mirror.con)
        assert ("node", "n_b") in q and ("edge", EDGE_AB) in q, q


# ------------------------------------- a flush pushes current state, not the queued kind
class TestFlush:
    @pytest.fixture(scope="class")
    def flushed(self, mirror):
        con = mirror.con
        con.execute("INSERT INTO nodes(id,type,title,props,created,updated) VALUES"
                    "('n_b','note','B again','{}',?,?)", (TS, TS))
        con.execute("INSERT INTO edges(src,dst,rel,props,created) VALUES"
                    "('n_a','n_b','mentions','{\"w\": 2}',?)", (TS,))
        con.commit()
        mirror.sent.clear()
        return graph._flush(con)

    def test_deleted_then_recreated_is_an_upsert(self, mirror, flushed):
        ids = [n["id"] for batch in mirror.sent_params("nodes") for n in batch]
        dels = [i for batch in mirror.sent_params("ids") for i in batch]
        assert "n_b" in ids and "n_b" not in dels, flushed

    def test_the_queue_is_drained(self, flushed):
        assert flushed.get("pending") == 0, flushed


# ----------------------------------------------------- the payload loses nothing
class TestPayload:
    def test_a_prop_named_like_a_column_survives_in_props(self, mirror):
        p = graph._node_payload(mirror.con, "n_a")
        assert json.loads(p["_props"]).get("status") == "inner"
        assert p["status"] == ""

    def test_a_mixed_list_is_sent_as_json_not_a_list_neo4j_would_refuse(self, mirror):
        p = graph._node_payload(mirror.con, "n_a")
        assert p["mixed"] == json.dumps([1, "a"])

    def test_an_edge_keeps_its_props_and_created(self, mirror):
        e = graph._edge_payload(mirror.con, "n_a", "n_b", "mentions")
        assert json.loads(e["p"]["_props"]) == {"w": 2}
        assert e["p"]["created"]


# ------------------------------------------------ a restore rebuilds the same brain
class TestRestore:
    @pytest.fixture(scope="class")
    def restored(self, mirror) -> SimpleNamespace:
        home = mirror.home
        (home / "scripts").mkdir(exist_ok=True)
        (home / "scripts" / "mine.py").write_text(MINE_PY, encoding=UTF8)
        (home / "neo4j.json").write_text('{"password": "secret"}', encoding=UTF8)
        files = brain_sync.local_files(home)
        con = mirror.con
        # the master's state, shaped as neo4j returns it: properties, nulls dropped
        master = {
            "nodes": [brain_sync._drop_nulls(n) for n in brain_sync.local_nodes(con)],
            "edges": [[e["src"], e["dst"], brain_sync._drop_nulls(e["p"])]
                      for e in brain_sync.local_edges(con)],
            "rows": [[r["table"], r["key"], r["data"]] for r in brain_sync.local_rows(con)],
            "files": [[f["path"], f["content"], f["sha"]] for f in files],
        }
        target = home / "restored.db"
        live_path = graph.db_path()
        before = {v: os.environ.get(v) for v in DB_VARS}
        counts = brain_sync.build_restore(target, master)
        assert {v: os.environ.get(v) for v in DB_VARS} == before
        live = sqlite3.connect(str(live_path))
        copy = sqlite3.connect(str(target))
        yield SimpleNamespace(paths={f["path"] for f in files}, master=master, counts=counts,
                              live=live, copy=copy)
        live.close()
        copy.close()

    def test_user_scripts_are_mirrored(self, restored):
        assert "scripts/mine.py" in restored.paths, restored.paths

    def test_secrets_never_are(self, restored):
        assert "neo4j.json" not in restored.paths, restored.paths

    def test_the_restored_file_passes_its_integrity_check(self, restored):
        assert restored.counts["integrity"] == "ok", restored.counts

    @pytest.mark.parametrize("name, sql", [
        ("nodes", "SELECT id,type,title,body,props,status,due,realm,created,updated FROM nodes"),
        ("edges", "SELECT src,dst,rel,props,created FROM edges"),
        ("coactivation", "SELECT * FROM coactivation"),
        ("trigger_checkpoint", "SELECT * FROM trigger_checkpoint"),
    ])
    def test_a_table_comes_back_exactly(self, restored, name, sql):
        x, y = set(restored.live.execute(sql).fetchall()), set(restored.copy.execute(sql).fetchall())
        assert x == y, x ^ y

    def test_the_restore_does_not_queue_itself_for_the_master(self, restored):
        assert restored.copy.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0

    def test_home_files_are_written_back(self, restored, tmp_path_factory):
        fresh = tmp_path_factory.mktemp("mirror-home")
        written = brain_sync.restore_files(fresh, restored.master["files"], overwrite=False)
        assert (fresh / "scripts" / "mine.py").read_text(encoding=UTF8) == MINE_PY, written


# ------------------------------------------- a pull brings other machines' changes down
class TestPull:
    @pytest.fixture(scope="class")
    def pulled(self, mirror) -> SimpleNamespace:
        con = mirror.con
        con.execute("DELETE FROM outbox")
        con.commit()
        theirs = {"id": "n_theirs", "type": "note", "title": "From B", "body": "",
                  "status": "", "due": "", "realm": "default", "_props": '{"x": 1}',
                  "created": "2026-09-29T11:00:00+00:00", "updated": "2026-09-29T11:00:00+00:00"}
        stale_a = {"id": "n_a", "type": "note", "title": "OLD A", "_props": "{}",
                   "created": "2026-01-01T00:00:00+00:00", "updated": "2026-01-01T00:00:00+00:00"}
        mirror.fake_master({
            "clock": 10, "nodes": [[theirs], [stale_a]],
            "edges": [["n_theirs", "n_a", {"rel": "about", "created": "t", "_props": '{"w": 5}'}]],
            "rows": [["trigger_gate", '["from-b"]',
                      json.dumps({"scope": "from-b", "enabled": 1, "at": "t"})]]})
        return brain_sync.pull_changes(con, mirror.home)

    def test_a_new_node_arrives(self, mirror, pulled):
        assert mirror.con.execute("SELECT props FROM nodes WHERE id='n_theirs'").fetchone()[0] == '{"x": 1}', pulled

    def test_an_older_master_copy_does_not_overwrite_a_newer_local_one(self, mirror, pulled):
        assert mirror.con.execute("SELECT title FROM nodes WHERE id='n_a'").fetchone()[0] == "A"

    def test_its_edge_and_row_arrive(self, mirror, pulled):
        con = mirror.con
        assert con.execute("SELECT props FROM edges WHERE src='n_theirs'").fetchone()[0] == '{"w": 5}'
        assert con.execute("SELECT enabled FROM trigger_gate WHERE scope='from-b'").fetchone()

    def test_nothing_pulled_is_queued_to_go_back_up(self, mirror, pulled):
        assert mirror.con.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0

    def test_the_pull_remembers_how_far_it_got(self, mirror, pulled):
        assert graph._sync_get(mirror.con, "pull_seq") == "10"

    # ---- deletes come down, but never over a local edit
    def test_deletes_come_down_but_never_over_a_local_edit(self, mirror, pulled):
        con = mirror.con
        con.execute("UPDATE nodes SET title='edited here' WHERE id='n_a'")
        con.commit()
        mirror.fake_master({"clock": 12, "tombs": [["node", "n_theirs"], ["node", "n_a"]]})
        brain_sync.pull_changes(con, mirror.home)
        # deleted elsewhere is deleted here, with its edges
        assert not con.execute("SELECT 1 FROM nodes WHERE id='n_theirs'").fetchone()
        assert not con.execute("SELECT 1 FROM edges WHERE src='n_theirs'").fetchone()
        # still being edited here survives the delete
        assert con.execute("SELECT title FROM nodes WHERE id='n_a'").fetchone()[0] == "edited here"

    # ---- a file changed on both sides keeps both
    def test_a_file_changed_on_both_sides_keeps_both(self, mirror, pulled):
        con, home = mirror.con, mirror.home
        (home / "scripts").mkdir(exist_ok=True)
        f = home / "scripts" / "both.py"
        f.write_text("base\n", encoding=UTF8)
        graph._sync_set(con, "file:scripts/both.py", brain_sync._sha("base\n"))
        f.write_text("local edit\n", encoding=UTF8)
        mirror.fake_master({"clock": 13, "files": [["scripts/both.py", "their edit\n",
                                                    brain_sync._sha("their edit\n"), "machine-b"]]})
        res = brain_sync.pull_changes(con, home)
        side = home / "scripts" / "both.py.conflict-machine-b"
        assert f.read_text(encoding=UTF8) == "local edit\n"
        assert side.exists() and side.read_text(encoding=UTF8) == "their edit\n", res.get("conflicts")
        # a conflict copy is never mirrored itself
        assert "scripts/both.py.conflict-machine-b" not in {x["path"] for x in brain_sync.local_files(home)}

    def test_a_file_changed_only_elsewhere_is_updated_here(self, mirror, pulled):
        con, home = mirror.con, mirror.home
        (home / "scripts").mkdir(exist_ok=True)
        g = home / "scripts" / "untouched.py"
        g.write_text("v1\n", encoding=UTF8)
        graph._sync_set(con, "file:scripts/untouched.py", brain_sync._sha("v1\n"))
        mirror.fake_master({"clock": 14, "files": [["scripts/untouched.py", "v2\n",
                                                    brain_sync._sha("v2\n"), "machine-b"]]})
        brain_sync.pull_changes(con, home)
        assert g.read_text(encoding=UTF8) == "v2\n"
