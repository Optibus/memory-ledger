"""Contract-authoring tests (CONVENTIONS §5, §5a, §5b).

    python -m pytest tests/test_contracts.py

Runs the real CLI and the real web server against THROWAWAY brains (see
conftest.py), so it never touches the owner's. What it guards is the part that
would fail silently. A form can produce a skill that reads fine and is missing
the clause that makes it checkable; an export can lose the rules a skill is
accountable to and arrive on the other machine accountable to nothing. Both look
like success at the moment they happen, and like a lie months later, which is the
failure §5 exists to prevent.

The tests that build on one brain (authoring -> export -> guards -> bundles) run
in file order and share the module-scoped `brain` and `authored` fixtures; every
other test makes its own home.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

import pytest

from conftest import HOME_VAR, ROOT, SCRIPTS, UTF8

NO_SEED_VAR = "SECOND_BRAIN_NO_SKILL_SEED"
GRAPH_PY = SCRIPTS / "graph.py"
DIST = ROOT / "frontend" / "dist"
DIST_INDEX = DIST / "index.html"

JUDGED = "JUDGED AGAINST"
SKILL_NAME = "sync-by-file"
SKILL_TITLE = "Sync the brain by file"
RULE_TITLE = "Export carries its rules"
BUNDLE_RULE = "rule_user_bundle-rule"
NO_API = "--no-build"            # the API tests do not need the UI built
API_READY_TIMEOUT_S = 60

# A skill's six authored sections, and a rule's three, complete enough to pass the
# write-time check; tests drop or alter one to prove the refusal.
FULL_SKILL = {
    "when_to_use": "A second machine needs what this one learned.",
    "given": "The learned tree, and a destination directory.",
    "deliverables": "**The folder is self-contained.** Rules sit beside skills.",
    "judged_against": "Ingesting the folder alone restores both halves.",
    "guardrails": "**Must not overwrite a rule the other brain holds.**",
    "reply": "One line: how many moved, and where.",
}
FULL_RULE = {
    "the_rule": "An export carries the rules its skills honor.",
    "why": "A skill whose rules stayed behind is accountable to nothing.",
    "judged_against": "The exported folder has a rules/ entry per honored id.",
}

needs_symlink = pytest.mark.skipif(sys.platform == "win32",
                                   reason="creating a symlink needs a privilege on Windows")
needs_dist = pytest.mark.skipif(not DIST_INDEX.is_file(),
                                reason="no frontend/dist/index.html (serve.py builds it on first start)")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


# ----------------------------------------------------------------- fixtures
@pytest.fixture(scope="module", autouse=True)
def _seedless() -> Iterator[None]:
    """A brain from scratch is seeded with the built-in skills; these tests count
    nodes in an empty one. Children inherit the variable through `brain_env`."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(NO_SEED_VAR, "1")
        yield


@pytest.fixture(scope="module")
def graph(_seedless: None) -> ModuleType:
    """The engine, in-process. It resolves its home from the environment at call
    time, so it must only be used once `brain` has pointed that at a temp dir -
    or half the file would quietly test the developer's real brain."""
    import graph as engine  # noqa: PLC0415 - needs the env set first
    return engine


@pytest.fixture(scope="module")
def brain(module_brain_home: Path, graph: ModuleType) -> Path:
    """The shared throwaway brain, initialised."""
    con = graph.connect()
    con.close()
    return module_brain_home


@pytest.fixture
def con(brain: Path, graph: ModuleType) -> Iterator[Any]:
    connection = graph.connect()
    yield connection
    connection.close()


@pytest.fixture
def g(run_script: Callable[..., subprocess.CompletedProcess]) -> Callable[..., dict[str, Any]]:
    """`g("subcommand", ..., home=...)`: a graph.py subcommand, parsed. Without
    `home` it runs on the shared brain."""
    def run(*args: str, home: Path | str | None = None) -> dict[str, Any]:
        p = run_script(GRAPH_PY, *args, home=home)
        try:
            return json.loads(p.stdout)
        except ValueError:
            return {"ok": False, "raw": p.stdout[:300], "stderr": p.stderr[:300]}
    return run


@pytest.fixture
def fresh_home(tmp_path: Path) -> Path:
    """An empty directory to use as a second brain."""
    home = tmp_path / "other-brain"
    home.mkdir()
    return home


class Authored(NamedTuple):
    rule: dict[str, Any]
    skill: dict[str, Any]
    rid: str
    sid: str


@pytest.fixture(scope="module")
def authored(brain: Path, graph: ModuleType) -> Authored:
    """The rule and the skill the later tests export, guard and bundle. Nothing is
    asserted here; `test_authoring_*` do that."""
    con = graph.connect()
    try:
        rule = graph.api_add_rule(con, title=RULE_TITLE,
                                  scenario="export the brain, sync two machines",
                                  sections=FULL_RULE)
        rid = rule["node"]["id"]
        skill = graph.api_add_skill(con, name=SKILL_NAME, title=SKILL_TITLE,
                                    description="Move skills between machines as markdown.",
                                    sections=FULL_SKILL,
                                    decl={"reads": "skill, rule", "writes": "note"},
                                    rules=[rid])
        return Authored(rule, skill, rid, skill["node"]["id"])
    finally:
        con.close()


class Api:
    """The brain's web API over HTTP: JSON in, JSON out, errors included."""

    def __init__(self, url: str) -> None:
        self.url = url

    def fetch(self, path: str, body: Any = None, timeout: float = 30) -> tuple[int, str, bytes]:
        req = urllib.request.Request(
            self.url + path, data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.headers.get_content_type(), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get_content_type(), e.read()

    def call(self, path: str, body: Any = None) -> dict[str, Any]:
        return json.loads(self.fetch(path, body)[2])


@pytest.fixture
def api(serve_process: Callable[..., Any]) -> Callable[..., Api]:
    """`api(*flags)`: the real server over a fresh brain, answering `/api/meta`."""
    def start(*flags: str) -> Api:
        client = Api(serve_process(None, *flags).url)
        deadline = time.time() + API_READY_TIMEOUT_S
        while time.time() < deadline:
            try:
                if client.call("/api/meta").get("ok"):
                    break
            except (OSError, ValueError):
                time.sleep(0.2)
        return client
    return start


# --------------------------------------------------------------- the shapes
def test_render_and_parse_a_skill(graph: ModuleType) -> None:
    sections = {"when_to_use": "Friday.", "given": "The graph.",
                "deliverables": "**A digest exists.**",
                "judged_against": "The note names every closed task.",
                "guardrails": "**Must not invent progress.**",
                "reply": "One line."}
    body = graph.build_skill_contract(
        sections, {"requires": "recall", "reads": "task, note", "writes": "note"},
        [{"id": "rule_user_x", "title": "Some rule"}], "friday digest")

    assert not [h for h in graph.SKILL_CONTRACT_SECTIONS if h not in body], \
        "every §5.1 heading is present"
    back = graph.parse_skill_contract(body)
    assert all(back.get(k, "").strip() == v for k, v in sections.items()), \
        {k: back.get(k) for k in sections if back.get(k, "").strip() != sections[k]}
    depends = graph._parse_depends_on(body)
    assert depends.get("requires") == ["recall"] and depends.get("reads") == ["task", "note"], \
        "the Depends on block re-reads as declared"
    assert "- **Optional (ordered):** none." in body, "an undeclared field says none rather than nothing"
    assert "rule_user_x" in body, "the honored rule id is in the prose as well as the edge"


def test_render_and_parse_a_rule(graph: ModuleType) -> None:
    rb = graph.build_rule_contract({"the_rule": "Do the thing.", "judged_against": "The thing was done."})
    assert "EXCEPTIONS\n\nnone" in rb, "a rule with no stated exception says none explicitly"
    assert "GOVERNS" not in rb, "GOVERNS is never written into the body (§5b.2)"
    assert graph.parse_rule_contract(rb).get("the_rule") == "Do the thing."
    assert graph.missing_rule_sections({"the_rule": "x"}) == [JUDGED], "a missing required section is named"


# ------------------------------------------------------ authoring, refused
def test_a_rule_with_no_way_to_check_it_is_refused(con: Any, graph: ModuleType) -> None:
    bad = graph.api_add_rule(con, title="Half a rule", sections={"the_rule": "Do the thing."})
    assert not bad["ok"] and JUDGED in bad["error"]


def test_a_skill_missing_deliverables_is_refused(con: Any, graph: ModuleType) -> None:
    bad = graph.api_add_skill(con, name="half", title="Half", sections={"when_to_use": "Sometimes."})
    assert not bad["ok"] and "Deliverables" in bad["error"]


def test_a_skill_cannot_honor_a_rule_this_brain_does_not_hold(con: Any, graph: ModuleType) -> None:
    bad = graph.api_add_skill(con, name="ghost", title="Ghost", sections=FULL_SKILL,
                              rules=["rule_user_nope"])
    assert not bad["ok"] and "rule_user_nope" in bad["error"]
    assert not con.execute("SELECT 1 FROM nodes WHERE id='skill_ghost'").fetchone(), \
        "and the refused skill was not written"


# ----------------------------------------------------------------- authoring
def test_authoring_writes_the_rule(con: Any, graph: ModuleType, authored: Authored) -> None:
    assert authored.rule["ok"], authored.rule.get("error", "")
    assert authored.rule["node"]["props"]["scenario"].startswith("export the brain"), \
        "its cue is indexed, not its prose (§5b.2)"
    assert con.execute("SELECT 1 FROM nodes WHERE id=?", (graph.AREA_CONVENTION,)).fetchone() is not None, \
        "the genome hub is created rather than demanded"
    assert authored.rid in [x["id"] for x in graph.rules_for(con, "sync two machines")], \
        "match-rules returns it for its cue"
    again = graph.api_add_rule(con, title=RULE_TITLE, sections=FULL_RULE)
    assert not again["ok"], "a second rule with the same title is refused, not silently merged"


def test_authoring_writes_the_skill_and_its_tree_row(con: Any, graph: ModuleType, authored: Authored) -> None:
    assert authored.skill["ok"], authored.skill.get("error", "")
    assert [x["id"] for x in graph.honored_rules(con, authored.sid)] == [authored.rid], \
        "the honored rule is an edge, not only prose"
    assert not [h for h in graph.SKILL_CONTRACT_SECTIONS if h not in authored.skill["node"]["body"]], \
        "its contract passes the same audit the CLI runs"

    tree = next(t for t in graph.skill_trees(con) if t["kind"] == "learned")
    row = next((x for x in tree["skills"] if x["id"] == authored.sid), None)
    assert row is not None, "it appears in the user tree with no extra sync run"
    assert row["reads"] == ["rule", "skill"] and row["writes"] == ["note"], \
        "its graph surface is projected onto node-type nodes"
    assert any(x["id"] == authored.rid and x["why"] == "declared" for x in row["rules"]), \
        "the tree reports the rule as declared, not merely matched"


# ------------------------------------------------------------- the file route
def test_a_skill_and_its_rules_survive_a_trip_through_markdown(
        g: Callable[..., dict[str, Any]], graph: ModuleType, brain: Path, fresh_home: Path,
        authored: Authored) -> None:
    rid, sid = authored.rid, authored.sid
    dest = brain / "outbox"
    res = g("export-skills", "--dir", str(dest))
    assert res["ok"] and res["skills"] >= 1, str(res)[:120]
    assert (dest / "rules" / f"{rid}.md").exists(), "the honored rule travelled with it"

    got = g("ingest-skills", "--dir", str(dest), "--kind", "learned", home=fresh_home)
    assert got["ok"], str(got)[:140]
    assert any(x["id"] == rid and x["action"] == "created" for x in got["rules"]), \
        "the rule was created there, not silently dropped"
    assert not [x for x in got["ingested"] if x.get("rules_not_found")], \
        "no skill arrived pointing at a rule the far side lacks"

    far = g("get-node", "--id", sid, home=fresh_home)
    assert far["ok"] and far["node"]["title"] == SKILL_TITLE, \
        "the title survives, not flattened to the slug"
    assert not [h for h in graph.SKILL_CONTRACT_SECTIONS if h not in far["node"]["body"]], \
        "the contract arrived whole"

    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys, json; sys.path.insert(0, %r); import graph; "
         "print(json.dumps([r['id'] for r in graph.honored_rules(graph.connect(), %r)]))"
         % (str(SCRIPTS), sid)],
        capture_output=True, text=True, encoding=UTF8, errors="replace",
        env={**os.environ, HOME_VAR: str(fresh_home)})
    assert json.loads(probe.stdout) == [rid], "the honors edge was rebuilt from the file"

    audit = g("audit-contracts", "--kind", "learned", home=fresh_home)
    assert audit["ok"], json.dumps(audit.get("gaps"))[:200]

    again = g("ingest-skills", "--dir", str(dest), "--kind", "learned", home=fresh_home)
    assert all(x["action"] == "kept" for x in again["rules"]), \
        "re-ingesting keeps the far side's own rule, never overwrites it"


# ------------------------------------------------------------- the web routes
def test_the_browser_writes_the_same_node_the_shell_does(api: Callable[..., Api]) -> None:
    web = api(NO_API)
    meta = web.call("/api/meta")
    assert [s["field"] for s in meta["skill_sections"]][:2] == ["when_to_use", "given"], \
        "the page is told the shape rather than hardcoding it"
    assert {s["field"] for s in meta["skill_sections"] if s["generated"]} == {"rules_honored", "depends_on"}, \
        "the generated sections are flagged as generated"

    bad = web.call("/api/create-rule", {"title": "Half", "sections": {"the_rule": "Do it."}})
    assert not bad["ok"] and JUDGED in bad["error"], \
        "the server refuses an incomplete rule even if the form did not"

    res = web.call("/api/create-skill", {
        "name": "inbox-sweep", "title": "Inbox sweep",
        "description": "Clear the inbox into the graph.",
        "sections": FULL_SKILL, "reads": "email, task", "writes": "task",
        "new_rules": [{"title": "Never archive an unread ask",
                       "scenario": "archive an email, clear the inbox",
                       "sections": FULL_RULE}]})
    assert res["ok"] and len(res.get("created_rules", [])) == 1, \
        "a skill and its inline rule are created in one call"

    fail = web.call("/api/create-skill", {
        "name": "aborted", "title": "Aborted", "sections": FULL_SKILL,
        "new_rules": [{"title": "Never archive an unread ask", "sections": FULL_RULE}]})
    assert not fail["ok"], "a failing inline rule aborts the skill rather than half-writing it"
    assert not web.call("/api/node?id=skill_aborted")["ok"], \
        "and the skill it would have made does not exist"

    rels = {(e["dir"], e["rel"]) for e in web.call("/api/node?id=skill_inbox-sweep")["edges"]}
    assert {("out", "honors"), ("out", "part-of"), ("out", "reads")} <= rels, \
        "the expander can see one level of its relations"

    exp = web.call("/api/export-skills", {"kind": "learned"})
    assert exp["ok"] and exp["skills"] == 1, "the page can export the tree to markdown"
    ing = web.call("/api/ingest-skills", {"dir": exp["dir"]})
    assert ing["ok"] and ing["count"] >= 1, "and read it back"


# ----------------------------------------------------------- the edges of it
# Everything below is a path that only runs when something is wrong or unusual.
# They are the paths worth testing precisely because nobody exercises them by
# hand: a refusal that stopped refusing, or an error branch that raises its own
# error, is invisible until the day it matters.
@pytest.mark.parametrize("kwargs, why", [
    ({"title": "  "}, "a rule needs a title"),
    ({"title": "Odd source", "source": "whatever", "sections": FULL_RULE},
     "a rule's source must be one the engine ranks"),
])
def test_a_rule_that_breaks_a_guard_is_refused(con: Any, graph: ModuleType, kwargs: dict, why: str) -> None:
    assert not graph.api_add_rule(con, **kwargs)["ok"], why


def test_a_skill_needs_a_name_and_a_title(con: Any, graph: ModuleType) -> None:
    assert not graph.api_add_skill(con, name="", title="", sections=FULL_SKILL)["ok"]


def test_a_skill_id_is_not_silently_reused(con: Any, graph: ModuleType, authored: Authored) -> None:
    dup = graph.api_add_skill(con, name=SKILL_NAME, title="Again", sections=FULL_SKILL)
    assert not dup["ok"] and "already exists" in dup["error"]


def test_a_non_negotiable_rule_is_overridden_not_deleted(con: Any, graph: ModuleType) -> None:
    nn = graph.api_add_rule(con, title="No emoji, ever", non_negotiable=True,
                            scenario="write anything outward",
                            sections={**FULL_RULE, "the_rule": "No emoji."})
    nn_id = nn["node"]["id"]
    assert nn["ok"] and nn["node"]["props"]["non_negotiable"], "a non-negotiable rule is written as one"
    assert nn_id in [r["id"] for r in graph.rules_for(con, "something else")], \
        "and it comes back for a cue that does not mention it"

    over = graph.api_add_rule(con, title="Emoji are fine actually", scenario="write anything outward",
                              overrides=nn_id, sections=FULL_RULE)
    assert over["ok"], over.get("error", "")
    old = graph._node_dict(con.execute("SELECT * FROM nodes WHERE id=?", (nn_id,)).fetchone())
    assert old["status"] == "superseded", "and retires the rule it replaces rather than deleting it"
    assert nn_id not in [r["id"] for r in graph.rules_for(con, "write outward")], \
        "the superseded rule stops being returned"
    assert f"overrides: {nn_id}" in graph.rule_markdown(con, over["node"]["id"]), \
        ("what it replaced rides in the exported file, so the far side retires the same rule "
         "rather than keeping both sides of the contradiction")

    # A built-in names its rules in prose and has no honors edge until someone
    # adds one. Dropping that fallback would blank the rules on every shipped
    # skill at once, which is exactly the kind of regression a test earns.
    prose = graph.api_create_node(
        con, "skill", "Prose only", f"## Rules honored\n\nAccountable for: `{nn_id}`.",
        {"kind": "plugin"}, "active", "", "shared", "skill_prose-only")
    found = graph.honored_rules(con, "skill_prose-only")
    assert prose["ok"] and [r["id"] for r in found] == [nn_id], "a rule named only in prose is still found"
    assert found[0]["linked"] is False, "and is marked as not carrying an edge"


def test_a_failed_write_aborts_instead_of_half_finishing(con: Any, graph: ModuleType,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph, "api_create_node", lambda *a, **k: {"ok": False, "error": "disk on fire"})
    r = graph.api_add_rule(con, title="Doomed rule", sections=FULL_RULE)
    assert not r["ok"] and r["error"] == "disk on fire", "a rule that could not be stored reports the storage error"
    sk = graph.api_add_skill(con, name="doomed", title="Doomed", sections=FULL_SKILL)
    assert not sk["ok"] and sk["error"] == "disk on fire", "so does a skill, before it links anything to it"
    assert not con.execute("SELECT 1 FROM edges WHERE src='skill_doomed'").fetchone(), \
        "and no edge was left pointing at the skill that never existed"


def test_the_node_type_catalog(con: Any, graph: ModuleType, authored: Authored) -> None:
    types = graph.node_type_catalog(con)
    by = {t["type"]: t for t in types}
    assert set(graph.NODE_TYPES) <= set(by), "every declarable type is offered"
    assert SKILL_TITLE in by["rule"]["read_by"], \
        "a type says who reads it, by name, so the coupling is visible before declaring it"
    assert by["rule"]["nodes"] >= 1, "and how many of it this brain holds"
    assert all(t["declarable"] or t["nodes"] for t in types), \
        "a type outside the vocabulary is still offered, marked"


def test_a_missing_node_exports_as_nothing(con: Any, graph: ModuleType) -> None:
    assert graph.skill_markdown(con, "skill_nope") == ("", [])
    assert graph.rule_markdown(con, "rule_nope") == ""


def test_a_pre_contract_skill_still_exports(con: Any, graph: ModuleType) -> None:
    """A skill learned before contracts existed has a plain body. Exporting it
    must still produce a file the far side can ingest, or the oldest skills are
    the ones that cannot travel."""
    graph.api_create_node(con, "skill", "Old habit", "Just some steps.",
                          {"kind": "learned", "triggers": "when it is old"},
                          "active", "", "shared", "skill_old-habit")
    md, rules = graph.skill_markdown(con, "skill_old-habit")
    assert "## When to use" in md and "Just some steps." in md, \
        "it exports with what it does have under headings"
    assert "## Judged against" not in md, \
        "and the sections it never had stay missing, so the audit can still see it"
    assert rules == [], "with nothing invented for its rules"


def test_rendering_edge_cases(graph: ModuleType) -> None:
    assert "EXCEPTIONS" in graph.build_rule_contract({"the_rule": "x", "judged_against": "y"}), \
        "a rule with no exceptions still renders none"
    assert "No rule is named here yet" in graph.render_rules_honored([], "a cue"), \
        "a contract with no honored rules says so rather than lying"
    assert graph.render_rules_honored([], "cue", "Also see the team handbook.").endswith(
        "Also see the team handbook."), "and extra prose the author wrote is kept below the generated part"
    assert (graph.parse_skill_contract("## Guardrails — must not\n\na").get("guardrails")
            == graph.parse_skill_contract("## Guardrails - must not\n\na").get("guardrails")
            == "a"), "the corpus's em dash and the generated hyphen are the same heading"
    assert graph.parse_skill_contract("## Guardrails\n\na").get("guardrails") == "a", \
        "and the bare stem is too"
    assert JUDGED not in graph.build_rule_contract({"the_rule": "x"}), \
        "a required section left empty is omitted, not written as a bare heading"
    assert graph._fm_value("a\nb  c") == "a b c" and graph._fm_value(None) == "", \
        "an empty frontmatter value folds rather than breaking the line"


def test_the_same_writes_through_the_cli(g: Callable[..., dict[str, Any]], fresh_home: Path) -> None:
    assert g("ingest-conventions", home=fresh_home)["ok"], "ingest-conventions creates the genome hub it needs"
    r = g("add-rule", "--title", "Say it in one line", "--scenario", "reply to anything",
          "--the-rule", "The ask leads the reply.",
          "--judged-against", "The first sentence contains the ask.", home=fresh_home)
    assert r["ok"] and JUDGED in r["node"]["body"], str(r)[:120]

    bad = g("add-rule", "--title", "Half", "--the-rule", "x", "--strict", home=fresh_home)
    assert not bad["ok"], "--strict refuses the half of it that cannot be checked"
    assert g("add-rule", "--title", "Half", "--the-rule", "x", home=fresh_home)["ok"], \
        "without --strict the gap is left for audit-contracts"
    gaps = g("audit-contracts", "--source", "user", home=fresh_home)["gaps"]["rules"]
    assert any(x["id"].endswith("half") and JUDGED in x["missing"] for x in gaps), \
        f"audit-contracts then reports exactly that rule, and what it is missing: {gaps}"

    assert not g("sync-skill-tree", home=fresh_home)["ok"], "sync-skill-tree refuses a brain with no skills in it"
    assert g("ingest-skills", home=fresh_home)["ok"] and g("sync-skill-tree", home=fresh_home)["ok"], \
        "and runs once there are"


@pytest.fixture
def damaged_inbox(fresh_home: Path) -> Path:
    """A folder as it arrives when it was not copied whole: a skill naming a rule
    that is not there, a rule with no title, and a dependency nobody holds."""
    box = fresh_home / "inbox" / "orphan"
    box.mkdir(parents=True)
    (box / "SKILL.md").write_text(
        "---\nname: orphan\ntitle: Orphan\nkind: learned\n"
        "rules: rule_user_never-travelled\n---\n\n"
        "## When to use\n\nnever\n\n## Depends on\n\n"
        "- **Requires:** `nothing-here`\n\n## Deliverables\n\nnone\n", encoding=UTF8)
    (fresh_home / "inbox" / "rules").mkdir()
    (fresh_home / "inbox" / "rules" / "untitled.md").write_text(
        "---\nid: rule_user_untitled\n---\n\nTHE RULE\n\nnothing\n", encoding=UTF8)
    return fresh_home / "inbox"


def test_what_arrives_when_the_folder_is_not_intact(g: Callable[..., dict[str, Any]], fresh_home: Path,
                                                    damaged_inbox: Path) -> None:
    got = g("ingest-skills", "--dir", str(damaged_inbox), home=fresh_home)
    assert got["ingested"][0].get("rules_not_found") == ["rule_user_never-travelled"], \
        "a rule the skill names but that did not travel is reported"
    assert got["rules"][0]["action"].startswith("skipped"), \
        "a rule file with no title is skipped, not written as a blank rule"
    assert got["tree"]["unresolved"], "an unresolvable dependency is named rather than dropped in silence"
    assert not g("ingest-skills", "--dir", str(damaged_inbox), "--no-rules", home=fresh_home)["rules"], \
        "skipping the rules folder is a choice the flag makes"


def test_export_flags_and_filters(g: Callable[..., dict[str, Any]], fresh_home: Path,
                                  damaged_inbox: Path) -> None:
    g("ingest-skills", "--dir", str(damaged_inbox), home=fresh_home)
    exp = g("export-skills", "--dir", str(fresh_home / "out"), "--kind", "all", "--no-rules", home=fresh_home)
    assert exp["ok"] and exp["rules"] == 0, "--no-rules exports the skills alone"

    # The built-ins already ship as files, so the default export is the learned
    # tree only. Getting that filter wrong would dump the whole plugin corpus
    # into someone's brain folder on every export.
    g("ingest-skills", home=fresh_home)
    every = g("export-skills", "--dir", str(fresh_home / "all"), "--kind", "all", home=fresh_home)
    mine = g("export-skills", "--dir", str(fresh_home / "mine"), home=fresh_home)
    assert 1 <= mine["skills"] < every["skills"], \
        f'the default export leaves the built-ins behind: {mine["skills"]} of {every["skills"]}'

    walked = g("ingest-skills", "--dir", str(damaged_inbox), "--all-md", home=fresh_home)["ingested"]
    assert all(not r["path"].replace("\\", "/").endswith("rules/untitled.md") for r in walked), \
        "--all-md walks past the rules folder rather than reading rules as skills"


def test_a_graph_command_the_server_runs_fails_loudly_never_raises(brain: Path) -> None:
    """The out-of-process commands the write routes run. The in-process safety
    net (a handler that raises, a client that hangs up) is in test_api_faults.py,
    which needs the server's own environment."""
    from brain_api import commands  # noqa: PLC0415

    assert not commands.graph_cmd(["no-such-command"])["ok"], "a graph command that fails is reported, not raised"
    assert "timed out" in commands.graph_cmd(["export-skills"], timeout=0.001)["error"], \
        "a graph command that hangs is bounded"
    assert not commands.graph_cmd(["--help"])["ok"], "output that is not JSON is reported as itself"
    assert commands.staged("../../graph") is None, "a staged token cannot name a file outside the staging area"
    assert commands.bundle_path("../graph.db") is None, "a bundle name cannot either"


def test_the_web_routes_given_nonsense(api: Callable[..., Api]) -> None:
    web = api(NO_API)
    assert not web.call("/api/create-rule", {})["ok"], "a rule with no title at all"
    assert not web.call("/api/create-rule", {"title": "x", "source": "made-up"})["ok"], \
        "a rule whose source is invented"
    assert not web.call("/api/create-rule", {"title": "x", "sections": "oops"})["ok"], \
        "sections sent as something other than an object is not a crash"
    assert not web.call("/api/create-skill", {})["ok"], "a skill with no title"

    res = web.call("/api/create-skill", {"name": "list-decls", "title": "List decls", "sections": FULL_SKILL,
                                         "reads": ["task", "note"], "writes": "note"})
    assert res["ok"] and res["node"]["props"]["reads"] == "task, note", \
        "declarations arrive as a list or a string, either way"
    assert web.call("/api/create-skill", {"name": "skip-junk", "title": "Skip junk", "sections": FULL_SKILL,
                                          "new_rules": ["nonsense"]})["ok"], \
        "a new_rules entry that is not an object is ignored, not fatal"
    assert not web.call("/api/create-skill", {"name": "no-go", "title": "No go", "sections": FULL_SKILL,
                                              "new_rules": [{}]})["ok"], \
        "a new_rules entry with no title aborts the skill"
    assert not web.call("/api/node?id=skill_no-go")["ok"], "and that skill was not written"
    assert not web.call("/api/export-skills", {"kind": "nonsense"})["ok"], \
        "an export to a bad kind is refused by the command, reported by the page"
    assert any(t["type"] == "task" for t in web.call("/api/skills")["node_types"]), \
        "the node-type catalog reaches the page"


# ------------------------------------------------------------ where home is
@pytest.fixture
def adopt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., tuple[Path, str, str | None, list[str]]]:
    """`adopt(setup, env=None)`: one adoption case in its own fake $HOME. Returns
    (resolved home, action, link target or None, stray legacy homes)."""
    import _switch  # noqa: PLC0415

    def run(setup: Callable[[Path], object], env: dict[str, str] | None = None):
        fake = tmp_path / "home-case"
        fake.mkdir()
        for var in ("MEMORY_LEDGER_HOME", HOME_VAR):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HOME", str(fake))
        monkeypatch.setenv("USERPROFILE", str(fake))
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        setup(fake)
        action = _switch.adopt_canonical_home()["action"]
        home = _switch.home_dir()
        link = os.readlink(str(home)) if home.is_symlink() else None
        return home, action, link, [p.name for p in _switch.legacy_homes()]
    return run


# `~/.memory-ledger` is the canonical home and `~/.second_brain` is where the
# data actually is on any machine that predates the rename. The bridge is a
# symlink, so the thing that must never happen is a second, empty brain
# appearing while the real one sits unreachable beside it.
def test_a_fresh_machine_gets_a_real_directory_at_the_canonical_name(adopt) -> None:
    home, action, link, _ = adopt(lambda h: None)
    assert action == "created" and home.name == ".memory-ledger" and link is None, action


@needs_symlink
def test_a_pre_rename_machine_is_adopted_by_symlink(adopt) -> None:
    home, action, link, strays = adopt(lambda h: (h / ".second_brain").mkdir())
    assert action == "linked" and home.name == ".memory-ledger" and link == ".second_brain", \
        f"adopted by symlink, not by moving anything: {action} -> {link}"
    assert strays == [], "and the adopted legacy dir is not then reported as a stray brain"
    # Relative, so the link survives $HOME itself moving - a migrated account,
    # a remounted volume, a launchd agent with a different HOME.
    assert link is not None and not link.startswith("/"), \
        "the link is relative, so it survives the home directory moving"


@needs_symlink
@pytest.mark.parametrize("name", [".second-brain", ".memory_ledger"])
def test_a_brain_at_another_legacy_name_is_found_and_adopted(adopt, name: str) -> None:
    _, action, link, _ = adopt(lambda h: (h / name).mkdir())
    assert action == "linked" and link == name, f"{action} -> {link}"


@needs_symlink
def test_adoption_is_idempotent(adopt) -> None:
    _, action, link, _ = adopt(lambda h: ((h / ".second_brain").mkdir(),
                                          (h / ".memory-ledger").symlink_to(".second_brain")))
    assert action == "already" and link == ".second_brain", action


def test_two_real_brains_the_canonical_one_wins_and_the_other_is_reported(adopt) -> None:
    home, action, _, strays = adopt(lambda h: ((h / ".second_brain").mkdir(), (h / ".memory-ledger").mkdir()))
    assert action == "already" and home.name == ".memory-ledger" and strays == [".second_brain"], \
        f"never silently merged or deleted: {action} strays={strays}"


@needs_symlink
def test_a_dangling_home_is_reported_not_quietly_recreated_over(adopt) -> None:
    _, action, _, _ = adopt(lambda h: (h / ".memory-ledger").symlink_to(".gone"))
    assert action == "broken-link", action


def test_an_explicit_home_outranks_adoption_entirely(adopt, tmp_path: Path) -> None:
    explicit = tmp_path / "explicit-brain"
    home, action, _, _ = adopt(lambda h: (h / ".second_brain").mkdir(),
                               env={"MEMORY_LEDGER_HOME": str(explicit)})
    assert action == "skipped" and home.name == "explicit-brain", action


@needs_symlink
def test_data_written_before_the_rename_survives_adoption(
        g: Callable[..., dict[str, Any]], brain_env: Callable[..., dict[str, str]], tmp_path: Path) -> None:
    """The end of the story: a brain written before the rename is still the brain
    being read after it, through the new name, with no second copy."""
    fake = tmp_path / "home-e2e"
    legacy = fake / ".second_brain"
    legacy.mkdir(parents=True)
    g("init", home=legacy)
    g("add-node", "--type", "note", "--title", "written before the rename", home=legacy)

    env = brain_env(None, **{HOME_VAR: None, "MEMORY_LEDGER_HOME": None,
                             "HOME": str(fake), "USERPROFILE": str(fake)})
    subprocess.run([sys.executable, str(ROOT / "hooks" / "bootstrap.py")], input="{}",
                   capture_output=True, text=True, encoding=UTF8, errors="replace", env=env)
    dbs = [str(p) for p in fake.rglob("graph.db")]
    assert len(dbs) == 1 and ".second_brain" in dbs[0], \
        f"bootstrap created exactly one database, the one already there: {dbs}"

    p = subprocess.run([sys.executable, str(GRAPH_PY), "stats"], capture_output=True, text=True,
                       encoding=UTF8, errors="replace", env=env)
    nodes = json.loads(p.stdout).get("nodes") if p.returncode == 0 else None
    assert nodes == 1, f"the pre-rename node is still there, read through the new name: nodes={nodes}"


# ------------------------------------------------------------ the built UI
def _asset_refs() -> list[str]:
    if not DIST_INDEX.is_file():
        return []
    return re.findall(r'(?:src|href)="\.?/?(assets/[^"]+)"', DIST_INDEX.read_text(encoding=UTF8))


ASSET_REFS = _asset_refs()
SCRIPT_REFS = [r for r in ASSET_REFS if r.endswith(".js")]


@needs_dist
def test_the_built_ui_is_coherent() -> None:
    """A build on disk, when there is one, is coherent and parses.

    `frontend/dist` is not in git: `scripts/serve.py` builds it on first start.
    So a checkout may have no bundle at all, and that is SKIPPED rather than
    failed - the suite must pass with npm off PATH. But a bundle that IS there
    gets served, and a half-written one is the worst kind of failure here: the
    API answers, the server starts, and the person gets a blank page."""
    assert ASSET_REFS, "its entry point references built assets"
    missing = [r for r in ASSET_REFS if not (DIST / r).is_file()]
    assert not missing, f"every one of them is on disk - a half-written build is a blank page: {missing}"
    assert 'id="root"' in DIST_INDEX.read_text(encoding=UTF8), "the app mounts on the root the bundle expects"


# The syntax check is skipped when node is not installed; it is a guard, not a
# dependency (CI installs node and builds first, so the guard cannot pass
# vacuously there).
@needs_dist
@needs_node
def test_the_bundle_is_a_single_script() -> None:
    assert len(SCRIPT_REFS) == 1, f"one syntax error is visible at once: {SCRIPT_REFS}"


@needs_dist
@needs_node
@pytest.mark.parametrize("rel", SCRIPT_REFS)
def test_the_bundle_parses(rel: str, tmp_path: Path) -> None:
    # .mjs so `node --check` parses it as the ES module the build emits.
    src = tmp_path / "bundle.mjs"
    src.write_text((DIST / rel).read_text(encoding=UTF8), encoding=UTF8)
    pr = subprocess.run([shutil.which("node"), "--check", str(src)], capture_output=True, text=True,
                        encoding=UTF8, errors="replace")
    why = next((ln for ln in (pr.stderr or "").splitlines() if "Error" in ln), "").strip()
    assert pr.returncode == 0, f"{rel} does not parse - a dead bundle takes the whole page with it: {why}"


# ---------------------------------------------------- the UI build decision
class UiTree:
    """A throwaway frontend directory that `serve.ui_stale()` is pointed at."""

    def __init__(self, work: Path) -> None:
        self.work = work
        self.src = work / "src"
        self.dist = work / "dist"
        self.index = self.dist / "index.html"
        self.app = self.src / "App.tsx"
        self.package = work / "package.json"
        self.src.mkdir(parents=True)
        self.dist.mkdir()
        self.package.write_text("{}", encoding=UTF8)
        self.app.write_text("x", encoding=UTF8)
        self.built_at = time.time() + 10      # a build "after" the sources

    def build(self) -> None:
        self.index.write_text("<html>", encoding=UTF8)
        os.utime(self.index, (self.built_at, self.built_at))

    def touch(self, path: Path, when: float) -> None:
        os.utime(path, (when, when))


@pytest.fixture
def ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, UiTree]:
    import serve  # noqa: PLC0415

    tree = UiTree(tmp_path / "frontend")
    monkeypatch.setattr(serve, "FRONTEND", tree.work)
    monkeypatch.setattr(serve, "DIST", tree.dist)
    monkeypatch.setattr(serve, "INDEX", tree.index)
    return serve, tree


# The server decides when to BUILD the UI, and that is what is tested. The bundle
# is not in git, so `scripts/serve.py` builds it when it is missing, and whoever
# changes `frontend/src/` needs the page to follow, so it rebuilds it when the
# source is newer. Never rebuilding serves a page that predates its source;
# always rebuilding puts an npm run in front of every start - including when
# files are written together, where every one lands with the same mtime and
# nothing was edited at all. So this exercises `ui_stale()` against a real
# directory rather than running npm.
def test_no_bundle_at_all_is_stale(ui) -> None:
    serve, _ = ui
    assert serve.ui_stale() == "no bundle yet"


def test_a_fresh_bundle_is_not_rebuilt(ui) -> None:
    serve, tree = ui
    tree.build()
    assert serve.ui_stale() == ""


def test_a_source_change_is_a_rebuild(ui) -> None:
    serve, tree = ui
    tree.build()
    tree.touch(tree.app, tree.built_at + 10)
    assert serve.ui_stale().startswith("App.tsx")


def test_a_config_change_is_a_rebuild_too(ui) -> None:
    """A config edit changes the output without touching a single component."""
    serve, tree = ui
    tree.build()
    cfg = tree.work / "vite.config.ts"
    cfg.write_text("export default {}", encoding=UTF8)
    tree.touch(cfg, tree.built_at + 10)
    assert serve.ui_stale().startswith("vite.config.ts")


def test_a_test_edit_is_not_a_rebuild(ui) -> None:
    """A test file never reaches the bundle, so editing one must not trigger a
    build on every start while someone is writing tests."""
    serve, tree = ui
    tree.build()
    spec = tree.src / "App.test.tsx"
    spec.write_text("test", encoding=UTF8)
    tree.touch(spec, tree.built_at + 10)
    assert serve.ui_stale() == ""


def test_files_with_one_shared_mtime_are_not_a_rebuild(ui) -> None:
    """Files written together are not an edit. A copy or a restore lands them
    within moments of each other, in any order - without the grace window that
    would compile a bundle that already matches its source."""
    serve, tree = ui
    tree.build()
    for path in (tree.index, tree.app, tree.package):
        tree.touch(path, tree.built_at + 30)
    assert serve.ui_stale() == ""


def test_rebuild_ignores_how_current_the_bundle_looks_and_keeps_it_on_disk(ui) -> None:
    serve, tree = ui
    tree.build()
    assert "rebuild" in serve.ensure_ui(auto=False, force=True), \
        "--rebuild does not care that the bundle looks current"
    # ...but never by DELETING the bundle to force the issue: on a machine that
    # cannot rebuild, that trades a stale page for no page.
    assert tree.index.is_file(), "and it leaves the existing bundle on disk"


def test_no_build_refuses_to_build_and_explains_itself(ui) -> None:
    serve, tree = ui
    tree.build()
    tree.touch(tree.app, tree.built_at + 60)
    assert "no-build" in serve.ensure_ui(auto=False)


# The bundle is not in git, so the fallback page usually means no Node.js to
# build it with - and it says so, with the fix, rather than 404ing.
@pytest.mark.parametrize("needle", [
    "{reason}",                                 # names the reason it was given
    "first start",                              # the UI is built on first start...
    "Node.js",                                  # ...and needs Node.js
    "npm install &amp;&amp; npm run build",     # the command that builds it by hand
    "/api/docs",                                # points at the API, which is unaffected
])
def test_the_fallback_page_says(needle: str) -> None:
    import serve  # noqa: PLC0415
    assert needle in serve.MISSING_UI


def test_the_fallback_page_no_longer_suggests_restoring_from_git() -> None:
    import serve  # noqa: PLC0415
    assert "git checkout" not in serve.MISSING_UI


# ------------------------------------------------------------- API documents
# The API describes itself, over the same port it answers on. The document is
# generated from the routes by the running process, so there is no second copy to
# drift. What is left to check is that it is served, and that the SPA fallback
# does not swallow the API around it. (--no-build: this is about the API, and
# waiting on an npm run would make a stdlib-only suite depend on a JS toolchain.)
def test_the_server_serves_a_document_of_its_own_routes(api) -> None:
    served = api(NO_API).call("/api/openapi.json")
    assert str(served.get("openapi", "")).startswith("3.") and "/api/meta" in served["paths"], \
        str(served.get("openapi"))


def test_the_server_has_a_browsable_ui_over_it(api) -> None:
    _, _, page = api(NO_API).fetch("/api/docs")
    assert "swagger" in page.decode(UTF8).lower(), page[:60]


def test_an_unknown_api_route_is_a_404_not_the_app(api) -> None:
    """The SPA fallback must not swallow a genuinely unknown API route: a client
    asking for an endpoint that does not exist has to hear so."""
    code, _, _ = api(NO_API).fetch("/api/no-such-endpoint")
    assert code == 404, str(code)


def test_a_deep_link_is_served_html_never_a_404(api) -> None:
    """Any non-API path is HTML, so a deep link reaches the app - or, when it has
    not been built, the page that says why (a 503). Never a 404, which would read
    as "that section does not exist"."""
    code, ctype, _ = api(NO_API).fetch("/dashboard")
    assert ctype == "text/html" and code != 404, f"{code} {ctype}"


# --------------------------------------------------------- bundles and deletion
# A bundle is a SELECTION moved as one file, and the whole thing turns on the
# import plan: the order the items can be read back in. An export that writes a
# skill before the rule it honors, or before the skill it requires, produces a zip
# that imports without complaint and arrives wired to nothing - the same
# silent-lie failure §5 exists to prevent, one layer out.
@pytest.fixture(scope="module")
def bundle(brain: Path, graph: ModuleType) -> dict[str, Any]:
    """base <- mid <- top, so a correct plan has to order three levels, and the
    rule ahead of all of them. Returns the build result and the zip's path."""
    con = graph.connect()
    try:
        graph.api_add_rule(con, title="Bundle rule", scenario="bundling", sections=FULL_RULE,
                           node_id=BUNDLE_RULE)
        for name, title, req in (("bun-base", "Bundle base", ""), ("bun-mid", "Bundle mid", "bun-base"),
                                 ("bun-top", "Bundle top", "bun-mid")):
            graph.api_add_skill(con, name=name, title=title, sections=FULL_SKILL,
                                decl={"requires": [req]} if req else {},
                                rules=[BUNDLE_RULE] if name == "bun-top" else [])
        zpath = brain / "out" / "sel.zip"
        res = graph.build_bundle(con, ["skill_bun-top", "skill_bun-mid", "skill_bun-base"], [], zpath)
        return {"res": res, "zpath": zpath}
    finally:
        con.close()


def test_the_export_writes_a_zip_with_one_directory_per_kind(graph: ModuleType, bundle: dict[str, Any]) -> None:
    assert bundle["res"]["ok"] and bundle["zpath"].exists(), str(bundle["res"])[:120]
    names = set(zipfile.ZipFile(bundle["zpath"]).namelist())
    assert graph.BUNDLE_PLAN in names, "the plan"
    assert any(n.startswith("skills/") for n in names) and any(n.startswith("rules/") for n in names), \
        "one directory per kind"
    assert f"rules/{BUNDLE_RULE}.md" in names, "a rule a skill honors travels even though it was not picked"


def test_the_plan_orders_rules_before_skills_and_requirements_first(bundle: dict[str, Any]) -> None:
    order = bundle["res"]["plan"]["order"]
    pos = {p: i for i, p in enumerate(order)}
    rules_at = [i for p, i in pos.items() if p.startswith("rules/")]
    skills_at = [i for p, i in pos.items() if p.startswith("skills/")]
    assert max(rules_at) < min(skills_at), f"every rule is ordered before every skill: {order}"
    assert pos["skills/bun-base/SKILL.md"] < pos["skills/bun-mid/SKILL.md"] < pos["skills/bun-top/SKILL.md"], \
        f"and a skill comes after the skill it requires: {order}"


def test_a_required_skill_that_did_not_travel_is_named(con: Any, graph: ModuleType, brain: Path,
                                                       bundle: dict[str, Any]) -> None:
    part = graph.build_bundle(con, ["skill_bun-top"], [], brain / "out" / "part.zip")
    assert part["missing_skills"] == ["skill_bun-mid"], "named, not silently dropped"


def test_a_bundle_lands_wired_in_a_brain_that_never_saw_it(g: Callable[..., dict[str, Any]], fresh_home: Path,
                                                           bundle: dict[str, Any]) -> None:
    zpath = str(bundle["zpath"])
    imp = g("import-bundle", "--zip", zpath, home=fresh_home)
    acts = {a["id"]: a["action"] for a in imp.get("applied", [])}
    assert set(acts.values()) == {"created"} and len(acts) == 4, f"an untouched brain creates everything: {acts}"
    rels = {(e["rel"], e["dst"]) for e in g("get-node", "--id", "skill_bun-top", home=fresh_home).get("edges", [])}
    assert ("honors", BUNDLE_RULE) in rels, "the skill lands accountable to the rule that travelled with it"
    assert ("requires", "skill_bun-mid") in rels, "and requiring the skill below it"

    # --- the three answers to "this one is already here"
    g("update-node", "--id", "skill_bun-mid", "--title", "Edited locally", home=fresh_home)
    again = g("import-bundle", "--zip", zpath, "--select", "skill_bun-mid:ignore",
              "--select", "skill_bun-top:replace", home=fresh_home)
    acts = {a["id"]: a["action"] for a in again.get("applied", [])}
    assert acts.get("skill_bun-mid") == "ignored", "ignore leaves this brain's copy alone"
    assert acts.get("skill_bun-top") == "replaced", "replace rebuilds it from the file"
    assert acts.get("skill_bun-base") == "ignored", f"and an item nobody mentioned is not touched either: {acts}"
    title = g("get-node", "--id", "skill_bun-mid", home=fresh_home)["node"]["title"]
    assert title == "Edited locally", "so the local edit survived an import that did not ask about it"

    upd = g("import-bundle", "--zip", zpath, "--select", "skill_bun-mid:update", home=fresh_home)
    acts = {a["id"]: a["action"] for a in upd.get("applied", [])}
    assert acts.get("skill_bun-mid") == "updated", f"update refreshes it from the incoming file: {acts}"
    assert g("get-node", "--id", "skill_bun-mid", home=fresh_home)["node"]["title"] == "Bundle mid", \
        "and the title it was exported with is back"


# A bundle is an untrusted file, and a contract is not history.
def test_a_bundle_with_nothing_to_carry_is_refused(con: Any, graph: ModuleType, brain: Path) -> None:
    assert not graph.build_bundle(con, [], [], brain / "out" / "empty.zip")["ok"], \
        "nothing selected is refused rather than writing an empty zip"
    assert not graph.build_bundle(con, ["skill_never-existed"], [], brain / "out" / "no.zip")["ok"], \
        "a skill that is not in this brain is refused"


def test_a_file_that_is_not_a_bundle_is_reported_not_raised(con: Any, graph: ModuleType, tmp_path: Path) -> None:
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"this is not a zip at all")
    assert not graph.inspect_bundle(con, junk)["ok"], "a file that is not a zip"

    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("skills/x/SKILL.md", "---\nname: x\n---\n\n# X\n")
    got = graph.inspect_bundle(con, plain)
    assert not got["ok"] and graph.BUNDLE_PLAN in got["error"], f"a zip with no import plan: {str(got)[:120]}"


def test_a_member_outside_the_bundles_layout_is_never_written(con: Any, graph: ModuleType, tmp_path: Path) -> None:
    """A member the plan does not list is never read, so a zip carrying
    ../../something cannot reach the filesystem through the importer."""
    sneaky = tmp_path / "sneaky.zip"
    with zipfile.ZipFile(sneaky, "w") as z:
        z.writestr(graph.BUNDLE_PLAN, json.dumps({
            "format": graph.BUNDLE_FORMAT, "version": 1, "order": ["../../escaped.md"],
            "items": {"../../escaped.md": {"kind": "rule", "id": "rule_user_escaped", "title": "Escaped"}}}))
        z.writestr("../../escaped.md", "---\ntitle: Escaped\n---\n\nTHE RULE\nno.\n")
    res = graph.import_bundle(con, sneaky, {"rule_user_escaped": "update"})
    assert res["ok"] and not res["applied"], str(res)[:160]
    assert not tmp_path.parent.joinpath("escaped.md").exists(), "and no file escaped the archive"


def test_a_folder_name_out_of_the_plan_cannot_choose_where_the_file_lands(con: Any, graph: ModuleType,
                                                                          tmp_path: Path) -> None:
    """The OTHER name a bundle controls: a skill is written to disk before it is
    read back, and the plan says what to call the folder."""
    walker = tmp_path / "walker.zip"
    with zipfile.ZipFile(walker, "w") as z:
        z.writestr(graph.BUNDLE_PLAN, json.dumps({
            "format": graph.BUNDLE_FORMAT, "version": 1, "order": ["skills/ok/SKILL.md"],
            "items": {"skills/ok/SKILL.md": {"kind": "skill", "id": "skill_walker", "title": "Walker",
                                             "name": "../../../../../../tmp/walked"}}}))
        z.writestr("skills/ok/SKILL.md", "---\nname: walker\ntitle: Walker\n---\n\n# Walker\n\n## When to use\nnever\n")
    graph.import_bundle(con, walker, {"skill_walker": "update"})
    landed = graph.learned_skills_dir()
    assert not Path("/tmp/walked/SKILL.md").exists(), "the file did not land where the name pointed"
    assert (landed / "tmp-walked" / "SKILL.md").exists(), sorted(p.name for p in landed.iterdir())


def test_deletion_is_never_silent(con: Any, graph: ModuleType) -> None:
    """A contract is not work, so it really goes - but never silently."""
    graph.api_add_rule(con, title="Doomed rule", sections=FULL_RULE, node_id="rule_user_doomed")
    graph.api_add_skill(con, name="doomed", title="Doomed", sections=FULL_SKILL, rules=["rule_user_doomed"])
    res = graph.delete_contracts(con, ["skill_doomed", "rule_user_doomed", "nope"])
    assert len(res["deleted"]) == 2, f"selected contracts are deleted: {str(res)[:140]}"
    assert res["not_found"] == ["nope"], "an id that is not here is reported rather than counted"
    left = con.execute("SELECT count(*) FROM edges WHERE src=? OR dst=?",
                       ("skill_doomed", "skill_doomed")).fetchone()[0]
    assert left == 0, "and every edge on them went with them"
    assert not graph.delete_contracts(con, ["hub_convention"])["deleted"], \
        "a task is refused: archive-never-delete still holds for work"


def test_a_built_in_is_deleted_only_when_forced(con: Any, graph: ModuleType) -> None:
    graph.api_add_skill(con, name="shipped", title="Shipped", sections=FULL_SKILL, kind="plugin")
    res = graph.delete_contracts(con, ["skill_shipped"])
    assert not res["deleted"] and res["refused"][0]["id"] == "skill_shipped", \
        f"a built-in is refused, because its file would re-ingest it: {str(res)[:140]}"
    assert len(graph.delete_contracts(con, ["skill_shipped"], force=True)["deleted"]) == 1, \
        "and goes only when the caller says so anyway"


def test_the_browser_moves_a_bundle_the_same_way_the_shell_does(api: Callable[..., Api]) -> None:
    web = api()
    web.call("/api/create-skill", {"name": "web-bun", "title": "Web bundle", "description": "d",
                                   "sections": FULL_SKILL})
    exp = web.call("/api/export-bundle", {"skills": ["skill_web-bun"], "rules": []})
    assert exp["ok"] and exp["skills"] == 1, f"the page can bundle a selection: {str(exp)[:140]}"
    code, _, blob = web.fetch(exp["download"])
    assert blob[:2] == b"PK", f"and download it as a real zip: {code} {blob[:8]!r}"

    assert not web.call("/api/bundle?name=../graph.db")["ok"], \
        "a name pointing out of the bundle directory serves nothing"

    got = web.call("/api/upload-bundle", {"filename": "web.zip", "data": base64.b64encode(blob).decode()})
    assert got["ok"] and got["items"][0]["exists"], \
        f"an uploaded bundle is inspected before anything is written: {str(got)[:140]}"
    assert not web.call("/api/skills")["trees"][0]["skills"], "and the inspection wrote nothing"

    imp = web.call("/api/import-bundle", {"token": got["token"], "select": {"skill_web-bun": "replace"}})
    assert imp["ok"] and imp["applied"][0]["action"] == "replaced", \
        f"the page imports what was ticked: {str(imp)[:140]}"
    assert not web.call("/api/import-bundle",
                        {"token": got["token"], "select": {"skill_web-bun": "update"}})["ok"], \
        "a staged upload is not reusable once it landed"
    assert not web.call("/api/upload-bundle", {"data": "not base64 at all!!"})["ok"], \
        "garbage in place of base64 is refused"

    d = web.call("/api/delete-contracts", {"ids": ["skill_web-bun"]})
    assert d["ok"] and d["count"] == 1, f"and deletes what was ticked: {str(d)[:140]}"
