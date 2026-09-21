#!/usr/bin/env python3
"""Contract-authoring tests (CONVENTIONS §5, §5a, §5b).

Stdlib only, like the rest. Runs the real CLI and the real web server against
THROWAWAY brains in temp dirs, so it never touches ~/.second_brain:

    python3 tests/test_contracts.py

What it guards is the part that would fail silently. A form can produce a skill
that reads fine and is missing the clause that makes it checkable; an export can
lose the rules a skill is accountable to and arrive on the other machine
accountable to nothing. Both look like success at the moment they happen, and
like a lie months later, which is the failure §5 exists to prevent.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "viz"))

BRAIN = tempfile.mkdtemp(prefix="second-brain-contracts-")
# In-process checks import the engine directly, and it resolves its home from the
# environment at call time — so this has to be set before the first connect(),
# or half the file would quietly test the developer's real brain.
os.environ["SECOND_BRAIN_HOME"] = BRAIN
os.environ["SECOND_BRAIN_NO_AUTOSYNC"] = "1"
FAILS = []


def env_for(home):
    return {**os.environ, "SECOND_BRAIN_HOME": home, "SECOND_BRAIN_NO_AUTOSYNC": "1"}


def g(*a, home=BRAIN):
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "graph.py"), *a],
                       capture_output=True, text=True, env=env_for(home))
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"ok": False, "raw": p.stdout[:300], "stderr": p.stderr[:300]}


def ck(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


# --------------------------------------------------------------- the shapes
def test_render_and_parse():
    print("\n== a contract renders, and parses back to what went in ==")
    import graph

    sections = {"when_to_use": "Friday.", "given": "The graph.",
                "deliverables": "**A digest exists.**",
                "judged_against": "The note names every closed task.",
                "guardrails": "**Must not invent progress.**",
                "reply": "One line."}
    body = graph.build_skill_contract(
        sections, {"requires": "recall", "reads": "task, note", "writes": "note"},
        [{"id": "rule_user_x", "title": "Some rule"}], "friday digest")
    ck("every §5.1 heading is present",
       not [h for h in graph.SKILL_CONTRACT_SECTIONS if h not in body])
    back = graph.parse_skill_contract(body)
    ck("the sections survive a round trip",
       all(back.get(k, "").strip() == v for k, v in sections.items()),
       str({k: back.get(k) for k in sections if back.get(k, "").strip() != sections[k]}))
    ck("the Depends on block re-reads as declared",
       graph._parse_depends_on(body).get("requires") == ["recall"] and
       graph._parse_depends_on(body).get("reads") == ["task", "note"])
    ck("an undeclared field says none rather than nothing",
       "- **Optional (ordered):** none." in body)
    ck("the honored rule id is in the prose as well as the edge",
       "rule_user_x" in body)

    rb = graph.build_rule_contract({"the_rule": "Do the thing.",
                                    "judged_against": "The thing was done."})
    ck("a rule with no stated exception says none explicitly",
       "EXCEPTIONS\n\nnone" in rb)
    ck("GOVERNS is never written into the body (§5b.2)", "GOVERNS" not in rb)
    ck("the rule sections parse back",
       graph.parse_rule_contract(rb).get("the_rule") == "Do the thing.")
    ck("a missing required section is named",
       graph.missing_rule_sections({"the_rule": "x"}) == ["JUDGED AGAINST"])


# ------------------------------------------------------- authoring, refused
def test_refusals():
    print("\n== an incomplete contract is refused at write time ==")
    import graph
    con = graph.connect()

    bad = graph.api_add_rule(con, title="Half a rule",
                             sections={"the_rule": "Do the thing."})
    ck("a rule with no way to check it is refused",
       not bad["ok"] and "JUDGED AGAINST" in bad["error"], bad.get("error", ""))
    bad = graph.api_add_skill(con, name="half", title="Half",
                              sections={"when_to_use": "Sometimes."})
    ck("a skill missing deliverables is refused",
       not bad["ok"] and "Deliverables" in bad["error"], bad.get("error", ""))
    bad = graph.api_add_skill(con, name="ghost", title="Ghost",
                              sections=FULL_SKILL, rules=["rule_user_nope"])
    ck("a skill cannot honor a rule this brain does not hold",
       not bad["ok"] and "rule_user_nope" in bad["error"], bad.get("error", ""))
    ck("and the refused skill was not written",
       not con.execute("SELECT 1 FROM nodes WHERE id='skill_ghost'").fetchone())


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


def test_authoring():
    print("\n== authoring writes the node, the edges and the tree ==")
    import graph
    con = graph.connect()

    r = graph.api_add_rule(con, title="Export carries its rules",
                           scenario="export the brain, sync two machines",
                           sections=FULL_RULE)
    ck("the rule is written", r["ok"], r.get("error", ""))
    rid = r["node"]["id"]
    ck("its cue is indexed, not its prose (§5b.2)",
       r["node"]["props"]["scenario"].startswith("export the brain"))
    ck("the genome hub is created rather than demanded",
       con.execute("SELECT 1 FROM nodes WHERE id=?",
                   (graph.AREA_CONVENTION,)).fetchone() is not None)
    ck("match-rules returns it for its cue",
       rid in [x["id"] for x in graph.rules_for(con, "sync two machines")])
    ck("a second rule with the same title is refused, not silently merged",
       not graph.api_add_rule(con, title="Export carries its rules",
                              sections=FULL_RULE)["ok"])

    s = graph.api_add_skill(con, name="sync-by-file", title="Sync the brain by file",
                            description="Move skills between machines as markdown.",
                            sections=FULL_SKILL,
                            decl={"reads": "skill, rule", "writes": "note"},
                            rules=[rid])
    ck("the skill is written", s["ok"], s.get("error", ""))
    sid = s["node"]["id"]
    ck("the honored rule is an edge, not only prose",
       [x["id"] for x in graph.honored_rules(con, sid)] == [rid])
    ck("its contract passes the same audit the CLI runs",
       not [h for h in graph.SKILL_CONTRACT_SECTIONS if h not in s["node"]["body"]])

    tree = [t for t in graph.skill_trees(con) if t["kind"] == "learned"][0]
    row = next((x for x in tree["skills"] if x["id"] == sid), None)
    ck("it appears in the user tree with no extra sync run", row is not None)
    ck("its graph surface is projected onto node-type nodes",
       row and row["reads"] == ["rule", "skill"] and row["writes"] == ["note"],
       str(row and (row["reads"], row["writes"])))
    ck("the tree reports the rule as declared, not merely matched",
       row and any(x["id"] == rid and x["why"] == "declared" for x in row["rules"]))
    return sid, rid


# ------------------------------------------------------------- the file route
def test_round_trip(sid, rid):
    print("\n== a skill and its rules survive a trip through markdown ==")
    dest = Path(BRAIN) / "outbox"
    res = g("export-skills", "--dir", str(dest))
    ck("the export reports what it wrote", res["ok"] and res["skills"] >= 1, str(res)[:120])
    ck("the honored rule travelled with it",
       (dest / "rules" / f"{rid}.md").exists())

    other = tempfile.mkdtemp(prefix="second-brain-contracts-b-")
    got = g("ingest-skills", "--dir", str(dest), "--kind", "learned", home=other)
    ck("the far side ingests it", got["ok"], str(got)[:140])
    ck("the rule was created there, not silently dropped",
       any(x["id"] == rid and x["action"] == "created" for x in got["rules"]))
    ck("no skill arrived pointing at a rule the far side lacks",
       not [x for x in got["ingested"] if x.get("rules_not_found")])

    import graph
    far = json.loads(subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "graph.py"), "get-node", "--id", sid],
        capture_output=True, text=True, env=env_for(other)).stdout)
    ck("the title survives, not flattened to the slug",
       far["ok"] and far["node"]["title"] == "Sync the brain by file",
       far.get("node", {}).get("title", ""))
    ck("the contract arrived whole",
       far["ok"] and not [h for h in graph.SKILL_CONTRACT_SECTIONS
                          if h not in far["node"]["body"]])
    edges = json.loads(subprocess.run(
        [sys.executable, "-c",
         "import sys;sys.path.insert(0,%r);import graph;import json;"
         "print(json.dumps([r['id'] for r in graph.honored_rules(graph.connect(),%r)]))"
         % (str(ROOT / "scripts"), sid)],
        capture_output=True, text=True, env=env_for(other)).stdout)
    ck("the honors edge was rebuilt from the file", edges == [rid], str(edges))

    audit = g("audit-contracts", "--kind", "learned", home=other)
    ck("everything that arrived passes audit-contracts",
       audit["ok"], json.dumps(audit.get("gaps"))[:200])

    again = g("ingest-skills", "--dir", str(dest), "--kind", "learned", home=other)
    ck("re-ingesting keeps the far side's own rule, never overwrites it",
       all(x["action"] == "kept" for x in again["rules"]),
       str([x["action"] for x in again["rules"]]))
    shutil.rmtree(other, ignore_errors=True)


# ------------------------------------------------------------- the web routes
def test_web():
    print("\n== the browser writes the same node the shell does ==")
    home = tempfile.mkdtemp(prefix="second-brain-contracts-w-")
    port = 8799
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "viz" / "serve.py"), "--port", str(port),
         "--no-open", "--no-reload"],
        env=env_for(home), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def call(path, body=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    try:
        for _ in range(60):
            try:
                if call("/api/meta").get("ok"):
                    break
            except Exception:
                time.sleep(0.2)
        meta = call("/api/meta")
        ck("the page is told the shape rather than hardcoding it",
           [s["field"] for s in meta["skill_sections"]][:2] == ["when_to_use", "given"])
        ck("the generated sections are flagged as generated",
           {s["field"] for s in meta["skill_sections"] if s["generated"]} ==
           {"rules_honored", "depends_on"})

        bad = call("/api/create-rule",
                   {"title": "Half", "sections": {"the_rule": "Do it."}})
        ck("the server refuses an incomplete rule even if the form did not",
           not bad["ok"] and "JUDGED AGAINST" in bad["error"])

        res = call("/api/create-skill", {
            "name": "inbox-sweep", "title": "Inbox sweep",
            "description": "Clear the inbox into the graph.",
            "sections": FULL_SKILL, "reads": "email, task", "writes": "task",
            "new_rules": [{"title": "Never archive an unread ask",
                           "scenario": "archive an email, clear the inbox",
                           "sections": FULL_RULE}]})
        ck("a skill and its inline rule are created in one call",
           res["ok"] and len(res.get("created_rules", [])) == 1, str(res)[:140])

        fail = call("/api/create-skill", {
            "name": "aborted", "title": "Aborted", "sections": FULL_SKILL,
            "new_rules": [{"title": "Never archive an unread ask",
                           "sections": FULL_RULE}]})
        ck("a failing inline rule aborts the skill rather than half-writing it",
           not fail["ok"])
        ck("and the skill it would have made does not exist",
           not call("/api/node?id=skill_aborted")["ok"])

        det = call("/api/node?id=skill_inbox-sweep")
        rels = {(e["dir"], e["rel"]) for e in det["edges"]}
        ck("the expander can see one level of its relations",
           ("out", "honors") in rels and ("out", "part-of") in rels and
           ("out", "reads") in rels, str(sorted(rels)))

        exp = call("/api/export-skills", {"kind": "learned"})
        ck("the page can export the tree to markdown", exp["ok"] and exp["skills"] == 1)
        ing = call("/api/ingest-skills", {"dir": exp["dir"]})
        ck("and read it back", ing["ok"] and ing["count"] >= 1, str(ing)[:120])
    finally:
        # SIGINT, not SIGTERM: the server catches KeyboardInterrupt and shuts
        # down the way Ctrl-C does, which is both the real exit path and the one
        # that lets a coverage run under this suite flush its data.
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(home, ignore_errors=True)


# ----------------------------------------------------------- the edges of it
# Everything below is a path that only runs when something is wrong or unusual.
# They are the paths worth testing precisely because nobody exercises them by
# hand: a refusal that stopped refusing, or an error branch that raises its own
# error, is invisible until the day it matters.
def test_guards():
    print("\n== the refusals and the odd shapes ==")
    import graph
    con = graph.connect()

    ck("a rule needs a title", not graph.api_add_rule(con, title="  ")["ok"])
    ck("a rule's source must be one the engine ranks",
       not graph.api_add_rule(con, title="Odd source", source="whatever",
                              sections=FULL_RULE)["ok"])
    ck("a skill needs a name and a title",
       not graph.api_add_skill(con, name="", title="", sections=FULL_SKILL)["ok"])
    dup = graph.api_add_skill(con, name="sync-by-file", title="Again",
                              sections=FULL_SKILL)
    ck("a skill id is not silently reused",
       not dup["ok"] and "already exists" in dup["error"], dup.get("error", ""))

    nn = graph.api_add_rule(con, title="No emoji, ever", non_negotiable=True,
                            scenario="write anything outward",
                            sections={**FULL_RULE, "the_rule": "No emoji."})
    ck("a non-negotiable rule is written as one",
       nn["ok"] and nn["node"]["props"]["non_negotiable"])
    ck("and it comes back for a cue that does not mention it",
       nn["node"]["id"] in [r["id"] for r in graph.rules_for(con, "something else")])

    over = graph.api_add_rule(con, title="Emoji are fine actually",
                              scenario="write anything outward",
                              overrides=nn["node"]["id"], sections=FULL_RULE)
    ck("an override is written", over["ok"], over.get("error", ""))
    ck("and retires the rule it replaces rather than deleting it",
       graph._node_dict(con.execute("SELECT * FROM nodes WHERE id=?",
                                    (nn["node"]["id"],)).fetchone())["status"]
       == "superseded")
    ck("the superseded rule stops being returned",
       nn["node"]["id"] not in [r["id"] for r in graph.rules_for(con, "write outward")])
    ck("and what it replaced rides in the exported file, so the far side retires "
       "the same rule rather than keeping both sides of the contradiction",
       f"overrides: {nn['node']['id']}" in graph.rule_markdown(con, over["node"]["id"]))

    # A built-in names its rules in prose and has no honors edge until someone
    # adds one. Dropping that fallback would blank the rules on every shipped
    # skill at once, which is exactly the kind of regression a test earns.
    prose = graph.api_create_node(
        con, "skill", "Prose only",
        f"## Rules honored\n\nAccountable for: `{nn['node']['id']}`.",
        {"kind": "plugin"}, "active", "", "shared", "skill_prose-only")
    ck("a rule named only in prose is still found",
       [r["id"] for r in graph.honored_rules(con, "skill_prose-only")]
       == [nn["node"]["id"]] and prose["ok"])
    ck("and is marked as not carrying an edge",
       graph.honored_rules(con, "skill_prose-only")[0]["linked"] is False)


def test_storage_failure():
    print("\n== a failed write aborts instead of half-finishing ==")
    import graph
    con = graph.connect()
    orig = graph.api_create_node
    graph.api_create_node = lambda *a, **k: {"ok": False, "error": "disk on fire"}
    try:
        r = graph.api_add_rule(con, title="Doomed rule", sections=FULL_RULE)
        ck("a rule that could not be stored reports the storage error",
           not r["ok"] and r["error"] == "disk on fire", str(r))
        sk = graph.api_add_skill(con, name="doomed", title="Doomed",
                                 sections=FULL_SKILL)
        ck("so does a skill, before it links anything to it",
           not sk["ok"] and sk["error"] == "disk on fire", str(sk))
    finally:
        graph.api_create_node = orig
    ck("and no edge was left pointing at the skill that never existed",
       not con.execute("SELECT 1 FROM edges WHERE src='skill_doomed'").fetchone())


def test_catalogs_and_odd_bodies():
    print("\n== the catalogs, and bodies that are not contracts ==")
    import graph
    con = graph.connect()

    types = graph.node_type_catalog(con)
    by = {t["type"]: t for t in types}
    ck("every declarable type is offered", set(graph.NODE_TYPES) <= set(by))
    ck("a type says who reads it, by name, so the coupling is visible before "
       "declaring it",
       "Sync the brain by file" in by["rule"]["read_by"], str(by["rule"]["read_by"]))
    ck("and how many of it this brain holds", by["rule"]["nodes"] >= 1)
    ck("a type outside the vocabulary is still offered, marked",
       all(t["declarable"] or t["nodes"] for t in types))

    ck("a skill that does not exist exports as nothing",
       graph.skill_markdown(con, "skill_nope") == ("", []))
    ck("a rule that does not exist exports as nothing",
       graph.rule_markdown(con, "rule_nope") == "")

    # A skill learned before contracts existed has a plain body. Exporting it
    # must still produce a file the far side can ingest, or the oldest skills
    # are the ones that cannot travel.
    graph.api_create_node(con, "skill", "Old habit", "Just some steps.",
                          {"kind": "learned", "triggers": "when it is old"},
                          "active", "", "shared", "skill_old-habit")
    md, rules = graph.skill_markdown(con, "skill_old-habit")
    ck("a pre-contract skill still exports, with what it does have under headings",
       "## When to use" in md and "Just some steps." in md, md[:90])
    ck("and the sections it never had stay missing, so the audit can still see it",
       "## Judged against" not in md)
    ck("with nothing invented for its rules", rules == [])

    ck("a rule with no exceptions still renders none",
       "EXCEPTIONS" in graph.build_rule_contract({"the_rule": "x",
                                                  "judged_against": "y"}))
    ck("a contract with no honored rules says so rather than lying",
       "No rule is named here yet" in graph.render_rules_honored([], "a cue"))
    ck("and extra prose the author wrote is kept below the generated part",
       graph.render_rules_honored([], "cue", "Also see the team handbook.")
       .endswith("Also see the team handbook."))
    ck("the corpus's em dash and the generated hyphen are the same heading",
       graph.parse_skill_contract("## Guardrails — must not\n\na").get("guardrails")
       == graph.parse_skill_contract("## Guardrails - must not\n\na").get("guardrails")
       == "a")
    ck("and the bare stem is too",
       graph.parse_skill_contract("## Guardrails\n\na").get("guardrails") == "a")
    ck("a required section left empty is omitted, not written as a bare heading",
       "JUDGED AGAINST" not in graph.build_rule_contract({"the_rule": "x"}))
    ck("an empty frontmatter value folds rather than breaking the line",
       graph._fm_value("a\nb  c") == "a b c" and graph._fm_value(None) == "")


def test_cli_surface():
    print("\n== the same writes, through the CLI ==")
    home = tempfile.mkdtemp(prefix="second-brain-contracts-c-")
    ck("ingest-conventions creates the genome hub it needs",
       g("ingest-conventions", home=home)["ok"])
    r = g("add-rule", "--title", "Say it in one line",
          "--scenario", "reply to anything",
          "--the-rule", "The ask leads the reply.",
          "--judged-against", "The first sentence contains the ask.",
          home=home)
    ck("add-rule builds the contract from the section flags",
       r["ok"] and "JUDGED AGAINST" in r["node"]["body"], str(r)[:120])
    bad = g("add-rule", "--title", "Half", "--the-rule", "x", "--strict", home=home)
    ck("--strict refuses the half of it that cannot be checked", not bad["ok"])
    ck("without --strict the gap is left for audit-contracts",
       g("add-rule", "--title", "Half", "--the-rule", "x", home=home)["ok"])
    gaps = g("audit-contracts", "--source", "user", home=home)["gaps"]["rules"]
    ck("audit-contracts then reports exactly that rule, and what it is missing",
       any(x["id"].endswith("half") and "JUDGED AGAINST" in x["missing"]
           for x in gaps), str(gaps))
    ck("sync-skill-tree refuses a brain with no skills in it",
       not g("sync-skill-tree", home=home)["ok"])
    ck("and runs once there are",
       g("ingest-skills", home=home)["ok"] and g("sync-skill-tree", home=home)["ok"])
    shutil.rmtree(home, ignore_errors=True)


def test_travel_damage():
    print("\n== what arrives when the folder is not intact ==")
    home = tempfile.mkdtemp(prefix="second-brain-contracts-d-")
    box = Path(home) / "inbox" / "orphan"
    box.mkdir(parents=True)
    (box / "SKILL.md").write_text(
        "---\nname: orphan\ntitle: Orphan\nkind: learned\n"
        "rules: rule_user_never-travelled\n---\n\n"
        "## When to use\n\nnever\n\n## Depends on\n\n"
        "- **Requires:** `nothing-here`\n\n## Deliverables\n\nnone\n",
        encoding="utf-8")
    (Path(home) / "inbox" / "rules").mkdir()
    (Path(home) / "inbox" / "rules" / "untitled.md").write_text(
        "---\nid: rule_user_untitled\n---\n\nTHE RULE\n\nnothing\n", encoding="utf-8")
    got = g("ingest-skills", "--dir", str(Path(home) / "inbox"), home=home)
    ck("a rule the skill names but that did not travel is reported",
       got["ingested"][0].get("rules_not_found") == ["rule_user_never-travelled"],
       str(got["ingested"][0]))
    ck("a rule file with no title is skipped, not written as a blank rule",
       got["rules"][0]["action"].startswith("skipped"), str(got["rules"][0]))
    ck("an unresolvable dependency is named rather than dropped in silence",
       got["tree"]["unresolved"], str(got["tree"]["unresolved"]))
    ck("skipping the rules folder is a choice the flag makes",
       not g("ingest-skills", "--dir", str(Path(home) / "inbox"),
             "--no-rules", home=home)["rules"])
    exp = g("export-skills", "--dir", str(Path(home) / "out"), "--kind", "all",
            "--no-rules", home=home)
    ck("--no-rules exports the skills alone", exp["ok"] and exp["rules"] == 0)

    # The built-ins already ship as files, so the default export is the learned
    # tree only. Getting that filter wrong would dump the whole plugin corpus
    # into someone's brain folder on every export.
    g("ingest-skills", home=home)
    every = g("export-skills", "--dir", str(Path(home) / "all"), "--kind", "all",
              home=home)
    mine = g("export-skills", "--dir", str(Path(home) / "mine"), home=home)
    ck("the default export leaves the built-ins behind",
       mine["skills"] < every["skills"] and mine["skills"] >= 1,
       f'{mine["skills"]} of {every["skills"]}')
    ck("--all-md walks past the rules folder rather than reading rules as skills",
       all(not r["path"].endswith("rules/untitled.md")
           for r in g("ingest-skills", "--dir", str(Path(home) / "inbox"),
                      "--all-md", home=home)["ingested"]))
    shutil.rmtree(home, ignore_errors=True)


def test_server_error_paths():
    print("\n== the server survives the client, and its own bugs ==")
    import graph
    import serve

    calls = {}

    class FakeSelf:
        def _send(self, code, body, ctype):
            calls["code"] = code
            if calls.get("dead"):
                raise BrokenPipeError(32, "broken pipe")

    f = FakeSelf()
    serve.Handler._fail(f, RuntimeError("boom"))
    ck("an ordinary handler bug still reaches the client", calls.get("code") == 500)
    calls.clear()
    serve.Handler._fail(f, ConnectionResetError(54, "reset"))
    ck("a client that hung up is not reported back to itself, since it is gone",
       "code" not in calls)
    calls["dead"] = True
    serve.Handler._fail(f, RuntimeError("boom"))
    ck("and reporting a bug down a dead socket does not raise a second error",
       calls.get("code") == 500)

    quiet = serve.QuietServer.__new__(serve.QuietServer)
    try:
        raise ConnectionResetError(54, "reset")
    except ConnectionResetError:
        quiet.handle_error(None, ("127.0.0.1", 1))
    ck("a client hanging up never reaches the traceback printer", True)
    loud, serve.ThreadingHTTPServer.handle_error = {}, \
        lambda *a, **k: loud.setdefault("printed", True)
    try:
        raise RuntimeError("a real bug")
    except RuntimeError:
        quiet.handle_error(None, ("127.0.0.1", 1))
    ck("but a real bug is still printed, loudly", loud.get("printed"))

    ck("a graph command that fails is reported, not raised",
       not serve.Handler._graph_cmd(["no-such-command"])["ok"])
    ck("a graph command that hangs is bounded",
       "timed out" in serve.Handler._graph_cmd(["export-skills"], timeout=0.001)["error"])
    ck("output that is not JSON is reported as itself",
       not serve.Handler._graph_cmd(["--help"])["ok"])

    # An in-process server, so a handler can be made to fail on demand. The
    # subprocess test above proves the real entry point works; this one proves
    # the safety net under it does.
    srv = serve.QuietServer(("127.0.0.1", 0), serve.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    orig = serve.brain_query.node_detail
    try:
        serve.brain_query.node_detail = lambda _id: (_ for _ in ()).throw(
            RuntimeError("engine exploded"))
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/node?id=x", timeout=5) as r:
            body = json.loads(r.read())
        ck("a 500 is never what happens", False, "should have raised")
    except urllib.error.HTTPError as e:
        ck("a handler bug comes back as a 500 with the reason",
           e.code == 500 and "engine exploded" in json.loads(e.read())["error"])
    finally:
        serve.brain_query.node_detail = orig

    orig_link = serve.graph.api_link
    try:
        serve.graph.api_link = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("write exploded"))
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/link",
            data=json.dumps({"src": "a", "dst": "b", "rel": "relates-to"}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
            ck("a write bug comes back as a 500", False, "should have raised")
        except urllib.error.HTTPError as e:
            ck("a write bug comes back as a 500", e.code == 500)
    finally:
        serve.graph.api_link = orig_link

    # The real-world case from the log: the map polls, the person reloads, and
    # python raises out of the socket write. It must not print a traceback and
    # must not take the server with it.
    import socket as sk
    import struct
    for path in ("/api/graph", "/api/agenda", "/"):
        c = sk.create_connection(("127.0.0.1", port))
        c.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        time.sleep(0.03)
        c.setsockopt(sk.SOL_SOCKET, sk.SO_LINGER, struct.pack("ii", 1, 0))
        c.close()
    time.sleep(0.5)
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/meta", timeout=5) as r:
        ck("three aborted requests later the server is still answering",
           json.loads(r.read())["ok"])
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/agenda", timeout=5) as r:
        ck("a dashboard view answers", json.loads(r.read())["ok"])
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/range?from=2026-01-01&to=oops", timeout=5)
        ck("a bad query is a 400, not a crash", False, "should have raised")
    except urllib.error.HTTPError as e:
        ck("a bad query is a 400, not a crash", e.code == 400)
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/no-such-view", timeout=5)
        ck("an unknown view is a 404", False, "should have raised")
    except urllib.error.HTTPError as e:
        ck("an unknown view is a 404", e.code == 404)
    srv.shutdown()
    srv.server_close()


def test_web_odd_shapes():
    print("\n== the web routes, given nonsense ==")
    home = tempfile.mkdtemp(prefix="second-brain-contracts-x-")
    import serve
    srv = serve.QuietServer(("127.0.0.1", 0), serve.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def call(path, body=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    try:
        ck("a rule with no title at all", not call("/api/create-rule", {})["ok"])
        ck("a rule whose source is invented",
           not call("/api/create-rule", {"title": "x", "source": "made-up"})["ok"])
        ck("sections sent as something other than an object is not a crash",
           not call("/api/create-rule", {"title": "x", "sections": "oops"})["ok"])
        ck("a skill with no title", not call("/api/create-skill", {})["ok"])
        res = call("/api/create-skill", {
            "name": "list-decls", "title": "List decls", "sections": FULL_SKILL,
            "reads": ["task", "note"], "writes": "note"})
        ck("declarations arrive as a list or a string, either way",
           res["ok"] and res["node"]["props"]["reads"] == "task, note",
           str(res.get("node", {}).get("props", {}).get("reads")))
        ck("a new_rules entry that is not an object is ignored, not fatal",
           call("/api/create-skill", {"name": "skip-junk", "title": "Skip junk",
                                      "sections": FULL_SKILL,
                                      "new_rules": ["nonsense"]})["ok"])
        ck("a new_rules entry with no title aborts the skill",
           not call("/api/create-skill", {"name": "no-go", "title": "No go",
                                          "sections": FULL_SKILL,
                                          "new_rules": [{}]})["ok"])
        ck("and that skill was not written",
           not call("/api/node?id=skill_no-go")["ok"])
        ck("an export to a bad kind is refused by the command, reported by the page",
           not call("/api/export-skills", {"kind": "nonsense"})["ok"])
        ck("the node-type catalog reaches the page",
           any(t["type"] == "task" for t in call("/api/skills")["node_types"]))
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(home, ignore_errors=True)


def main():
    g("init")
    test_render_and_parse()
    test_refusals()
    sid, rid = test_authoring()
    test_round_trip(sid, rid)
    test_guards()
    test_storage_failure()
    test_catalogs_and_odd_bodies()
    test_cli_surface()
    test_travel_damage()
    test_server_error_paths()
    test_web()
    test_web_odd_shapes()
    shutil.rmtree(BRAIN, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
