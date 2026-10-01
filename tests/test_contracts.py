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
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

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
        [sys.executable, str(ROOT / "scripts" / "serve.py"), "--port", str(port),
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
        for _ in range(600):
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
    """The out-of-process commands the write routes run. The in-process safety
    net (a handler that raises, a client that hangs up) is in test_api_faults.py,
    which needs the server's own environment."""
    print("\n== a graph command the server runs fails loudly, never raises ==")
    from brain_api import commands

    ck("a graph command that fails is reported, not raised",
       not commands.graph_cmd(["no-such-command"])["ok"])
    ck("a graph command that hangs is bounded",
       "timed out" in commands.graph_cmd(["export-skills"], timeout=0.001)["error"])
    ck("output that is not JSON is reported as itself",
       not commands.graph_cmd(["--help"])["ok"])
    ck("a staged token cannot name a file outside the staging area",
       commands.staged("../../graph") is None)
    ck("a bundle name cannot either", commands.bundle_path("../graph.db") is None)


def test_web_odd_shapes():
    print("\n== the web routes, given nonsense ==")
    home = tempfile.mkdtemp(prefix="second-brain-contracts-x-")
    port = 8797
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "serve.py"), "--port", str(port),
         "--no-open", "--no-reload", "--no-build"],
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
        for _ in range(600):
            try:
                if call("/api/meta").get("ok"):
                    break
            except Exception:
                time.sleep(0.2)
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
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(home, ignore_errors=True)


# ------------------------------------------------------- where home is
def test_home_adoption():
    """`~/.memory-ledger` is the canonical home and `~/.second_brain` is where
    the data actually is on any machine that predates the rename. The bridge is
    a symlink, so the thing that must never happen is a second, empty brain
    appearing while the real one sits unreachable beside it."""
    print("\n== the canonical home adopts the legacy one without moving data ==")
    import _switch

    def run(setup, env=None):
        """One case, in its own fake $HOME. Returns (resolved home, action,
        link target or None, stray legacy homes)."""
        fake = tempfile.mkdtemp(prefix="home-case-")
        saved = {k: os.environ.get(k) for k in
                 ("HOME", "MEMORY_LEDGER_HOME", "SECOND_BRAIN_HOME")}
        for k in ("MEMORY_LEDGER_HOME", "SECOND_BRAIN_HOME"):
            os.environ.pop(k, None)
        os.environ["HOME"] = fake
        if env:
            os.environ.update(env)
        try:
            setup(Path(fake))
            action = _switch.adopt_canonical_home()["action"]
            home = _switch.home_dir()
            link = os.readlink(str(home)) if home.is_symlink() else None
            return home, action, link, [p.name for p in _switch.legacy_homes()]
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(fake, ignore_errors=True)

    home, action, link, _ = run(lambda h: None)
    ck("a fresh machine gets a real directory at the canonical name",
       action == "created" and home.name == ".memory-ledger" and link is None, action)

    home, action, link, strays = run(lambda h: (h / ".second_brain").mkdir())
    ck("a pre-rename machine is adopted by symlink, not by moving anything",
       action == "linked" and home.name == ".memory-ledger" and link == ".second_brain",
       f"{action} -> {link}")
    ck("and the adopted legacy dir is not then reported as a stray brain",
       strays == [], str(strays))

    # Relative, so the link survives $HOME itself moving — a migrated account,
    # a remounted volume, a launchd agent with a different HOME.
    ck("the link is relative, so it survives the home directory moving",
       link is not None and not link.startswith("/"), str(link))

    for name in (".second-brain", ".memory_ledger"):
        home, action, link, _ = run(lambda h, n=name: (h / n).mkdir())
        ck(f"a brain at {name} is found and adopted, not ignored",
           action == "linked" and link == name, f"{action} -> {link}")

    home, action, link, _ = run(lambda h: ((h / ".second_brain").mkdir(),
                                           (h / ".memory-ledger").symlink_to(".second_brain")))
    ck("adoption is idempotent — a second session changes nothing",
       action == "already" and link == ".second_brain", action)

    home, action, link, strays = run(lambda h: ((h / ".second_brain").mkdir(),
                                                (h / ".memory-ledger").mkdir()))
    ck("two real brains: the canonical one wins and the other is reported, "
       "never silently merged or deleted",
       action == "already" and home.name == ".memory-ledger" and strays == [".second_brain"],
       f"{action} strays={strays}")

    home, action, link, _ = run(lambda h: (h / ".memory-ledger").symlink_to(".gone"))
    ck("a dangling home is reported, not quietly recreated over",
       action == "broken-link", action)

    home, action, link, _ = run(lambda h: (h / ".second_brain").mkdir(),
                                env={"MEMORY_LEDGER_HOME": "/tmp/explicit-brain"})
    ck("an explicit $MEMORY_LEDGER_HOME outranks adoption entirely",
       action == "skipped" and home.name == "explicit-brain", action)


def test_home_adoption_keeps_the_data():
    """The end of the story: a brain written before the rename is still the
    brain being read after it, through the new name, with no second copy."""
    print("\n== data written before the rename survives adoption ==")
    fake = tempfile.mkdtemp(prefix="home-e2e-")
    legacy = os.path.join(fake, ".second_brain")
    os.makedirs(legacy)
    g("init", home=legacy)
    g("add-node", "--type", "note", "--title", "written before the rename", home=legacy)

    saved = {k: os.environ.get(k) for k in
             ("HOME", "MEMORY_LEDGER_HOME", "SECOND_BRAIN_HOME")}
    for k in ("MEMORY_LEDGER_HOME", "SECOND_BRAIN_HOME"):
        os.environ.pop(k, None)
    os.environ["HOME"] = fake
    try:
        env = {**os.environ}
        env.pop("MEMORY_LEDGER_HOME", None)
        env.pop("SECOND_BRAIN_HOME", None)
        subprocess.run([sys.executable, str(ROOT / "hooks" / "bootstrap.py")],
                       input="{}", capture_output=True, text=True, env=env)
        dbs = [str(p) for p in Path(fake).rglob("graph.db")]
        ck("bootstrap created exactly one database, the one already there",
           len(dbs) == 1 and ".second_brain" in dbs[0], str(dbs))
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "graph.py"), "stats"],
                           capture_output=True, text=True, env=env)
        n = json.loads(p.stdout).get("nodes") if p.returncode == 0 else None
        ck("and the pre-rename node is still there, read through the new name",
           n == 1, f"nodes={n}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(fake, ignore_errors=True)


# ------------------------------------------------------- bundles and deletion
# A bundle is a SELECTION moved as one file, and the whole thing turns on the
# import plan: the order the items can be read back in. An export that writes a
# skill before the rule it honors, or before the skill it requires, produces a
# zip that imports without complaint and arrives wired to nothing — the same
# silent-lie failure §5 exists to prevent, one layer out.
def test_built_ui_is_coherent():
    """A build on disk, when there is one, is coherent and parses.

    `frontend/dist` is not in git: `scripts/serve.py` builds it on first start.
    So a checkout may have no bundle at all, and that is SKIPPED rather than
    failed — the suite must pass with npm off PATH. But a bundle that IS there
    gets served, and a half-written one is the worst kind of failure here: the
    API answers, the server starts, and the person gets a blank page.

    The syntax check is skipped when node is not installed; it is a guard, not a
    dependency (CI installs node and builds first, so the guard cannot pass
    vacuously there)."""
    print("\n== a built UI, if present, is coherent ==")
    dist = ROOT / "frontend" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        print("  SKIP  no frontend/dist/index.html (serve.py builds it on first start)")
        return

    html = index.read_text(encoding="utf-8")
    import re
    refs = re.findall(r'(?:src|href)="\.?/?(assets/[^"]+)"', html)
    ck("its entry point references built assets", bool(refs), str(refs))
    missing = [r for r in refs if not (dist / r).is_file()]
    ck("and every one of them is on disk — a half-written build is a blank page",
       not missing, ", ".join(missing))

    ck("the app mounts on the root the bundle expects", 'id="root"' in html, "")

    node = shutil.which("node")
    if not node:
        print("  SKIP  node not installed")
        return
    scripts = [r for r in refs if r.endswith(".js")]
    ck("the bundle is a single script, so one syntax error is visible at once",
       len(scripts) == 1, str(scripts))
    for rel in scripts:
        # .mjs so `node --check` parses it as the ES module the build emits.
        src = Path(tempfile.mkdtemp(prefix="second-brain-js-")) / "bundle.mjs"
        src.write_text((dist / rel).read_text(encoding="utf-8"), encoding="utf-8")
        pr = subprocess.run([node, "--check", str(src)], capture_output=True, text=True)
        why = next((l for l in (pr.stderr or "").splitlines() if "Error" in l), "")
        ck(f"{rel} parses — a dead bundle takes the whole page with it",
           pr.returncode == 0, why.strip())
        shutil.rmtree(src.parent, ignore_errors=True)


def test_ui_build_is_decided_correctly():
    """The server decides when to BUILD the UI, and that is the test.

    The bundle is not in git, so `scripts/serve.py` builds it when it is missing,
    and whoever changes `frontend/src/` needs the page to follow, so it rebuilds
    it when the source is newer. The DECISION is what can go wrong:
    never rebuilding serves a page that predates its source (the failure the
    whole reload mechanism exists to prevent), always rebuilding puts an npm run
    in front of every start — including when files are written together, where
    every one lands with the same mtime and nothing was edited at all.

    So this exercises `ui_stale()` against a real directory rather than running
    npm, which would make the suite need a toolchain it otherwise does not."""
    print("\n== the server knows when the UI needs rebuilding ==")
    import serve

    work = Path(tempfile.mkdtemp(prefix="second-brain-ui-"))
    src = work / "src"
    src.mkdir()
    dist = work / "dist"
    dist.mkdir()
    (work / "package.json").write_text("{}", encoding="utf-8")
    (src / "App.tsx").write_text("x", encoding="utf-8")

    index = dist / "index.html"
    orig_front, orig_dist, orig_index = serve.FRONTEND, serve.DIST, serve.INDEX
    serve.FRONTEND, serve.DIST, serve.INDEX = work, dist, index
    try:
        ck("no bundle at all is stale", serve.ui_stale() == "no bundle yet",
           serve.ui_stale())

        # Build it "after" the sources.
        index.write_text("<html>", encoding="utf-8")
        newer = time.time() + 10
        os.utime(index, (newer, newer))
        ck("a fresh bundle is not rebuilt", serve.ui_stale() == "", serve.ui_stale())

        # A source edit after the build IS a rebuild.
        later = newer + 10
        os.utime(src / "App.tsx", (later, later))
        ck("a source change is a rebuild",
           serve.ui_stale().startswith("App.tsx"), serve.ui_stale())

        # ...and so is a config edit, which changes the output without touching
        # a single component.
        os.utime(src / "App.tsx", (newer - 5, newer - 5))
        cfg = work / "vite.config.ts"
        cfg.write_text("export default {}", encoding="utf-8")
        os.utime(cfg, (later, later))
        ck("a config change is a rebuild too",
           serve.ui_stale().startswith("vite.config.ts"), serve.ui_stale())

        # A test file never reaches the bundle, so editing one must not trigger
        # a build on every start while someone is writing tests.
        os.utime(cfg, (newer - 5, newer - 5))
        spec = src / "App.test.tsx"
        spec.write_text("test", encoding="utf-8")
        os.utime(spec, (later, later))
        ck("a test edit is NOT a rebuild", serve.ui_stale() == "", serve.ui_stale())

        # Files written together are not an edit. A copy or a restore lands them
        # within moments of each other, in any order — without the grace window
        # that would compile a bundle that already matches its source.
        same = newer + 30
        for path in (index, src / "App.tsx", cfg, work / "package.json"):
            os.utime(path, (same, same))
        ck("files with one shared mtime are NOT a rebuild",
           serve.ui_stale() == "", serve.ui_stale())

        # --rebuild builds whatever the mtimes say...
        why = serve.ensure_ui(auto=False, force=True)
        ck("--rebuild does not care that the bundle looks current",
           "rebuild" in why, why)
        # ...but never by DELETING the bundle to force the issue: on a machine
        # that cannot rebuild, that trades a stale page for no page.
        ck("and it leaves the existing bundle on disk", index.is_file(), "")

        # --no-build never runs npm, and says so rather than pretending the
        # bundle was refreshed.
        os.utime(src / "App.tsx", (same + 60, same + 60))
        why = serve.ensure_ui(auto=False)
        ck("--no-build refuses to build and explains itself",
           "no-build" in why, why)
    finally:
        serve.FRONTEND, serve.DIST, serve.INDEX = orig_front, orig_dist, orig_index
        shutil.rmtree(work, ignore_errors=True)

    # The bundle is not in git, so this page usually means no Node.js to build
    # it with — and it says so, with the fix, rather than 404ing.
    ck("the fallback page names the reason it was given",
       "{reason}" in serve.MISSING_UI, "")
    ck("and says the UI is built on first start and needs Node.js",
       "first start" in serve.MISSING_UI and "Node.js" in serve.MISSING_UI, "")
    ck("and gives the command that builds it by hand",
       "npm install &amp;&amp; npm run build" in serve.MISSING_UI, "")
    ck("and no longer suggests restoring it from git",
       "git checkout" not in serve.MISSING_UI, "")
    ck("and points at the API, which is unaffected",
       "/api/docs" in serve.MISSING_UI, "")


def test_api_docs_are_served():
    """The API describes itself, over the same port it answers on.

    The document is generated from the routes by the running process, so there
    is no second copy to drift. What is left to check is that it is served, and
    that the SPA fallback does not swallow the API around it.
    """
    print("\n== the API documents itself ==")
    import urllib.request

    home = tempfile.mkdtemp(prefix="second-brain-contracts-d-")
    port = 8798
    proc = subprocess.Popen(
        # --no-build: this test is about the API, and making it wait on an npm
        # run would make a stdlib-only suite depend on a JavaScript toolchain.
        [sys.executable, str(ROOT / "scripts" / "serve.py"), "--port", str(port),
         "--no-open", "--no-reload", "--no-build"],
        env=env_for(home), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(600):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/meta", timeout=5)
                break
            except Exception:
                time.sleep(0.2)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/openapi.json") as r:
            served = json.loads(r.read())
        ck("the running server serves a document of its own routes",
           str(served.get("openapi", "")).startswith("3.") and "/api/meta" in served["paths"],
           str(served.get("openapi")))

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/docs") as r:
            page = r.read().decode("utf-8")
        ck("and a browsable UI over it", "swagger" in page.lower(), page[:60])

        # The SPA fallback must not swallow a genuinely unknown API route: a
        # client asking for an endpoint that does not exist has to hear so.
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/no-such-endpoint")
            ck("an unknown /api/ route is a 404, not the app", False, "got 200")
        except urllib.error.HTTPError as e:
            ck("an unknown /api/ route is a 404, not the app", e.code == 404, str(e.code))

        # ...while any non-API path is HTML, so a deep link reaches the app —
        # or, when it has not been built, the page that says why. Never a 404,
        # which would read as "that section does not exist".
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/dashboard") as r:
                ctype, code = r.headers.get_content_type(), r.status
        except urllib.error.HTTPError as e:      # 503: no bundle on this machine
            ctype, code = e.headers.get_content_type(), e.code
        ck("a deep link is served HTML, never a 404",
           ctype == "text/html" and code != 404, f"{code} {ctype}")
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(home, ignore_errors=True)


def test_bundle_round_trip():
    print("\n== a selection travels as one zip, and lands wired ==")
    import zipfile
    import graph
    con = graph.connect()

    # base <- mid <- top, so a correct plan has to order three levels, and the
    # rule ahead of all of them
    graph.api_add_rule(con, title="Bundle rule", scenario="bundling",
                       sections=FULL_RULE, node_id="rule_user_bundle-rule")
    for name, title, req in (("bun-base", "Bundle base", ""),
                             ("bun-mid", "Bundle mid", "bun-base"),
                             ("bun-top", "Bundle top", "bun-mid")):
        graph.api_add_skill(con, name=name, title=title, sections=FULL_SKILL,
                            decl={"requires": [req]} if req else {},
                            rules=["rule_user_bundle-rule"] if name == "bun-top" else [])

    zpath = Path(BRAIN) / "out" / "sel.zip"
    res = graph.build_bundle(con, ["skill_bun-top", "skill_bun-mid", "skill_bun-base"],
                             [], zpath)
    ck("the export writes a zip", res["ok"] and zpath.exists(), str(res)[:120])

    names = set(zipfile.ZipFile(zpath).namelist())
    ck("one directory per kind, plus the plan",
       graph.BUNDLE_PLAN in names and
       any(n.startswith("skills/") for n in names) and
       any(n.startswith("rules/") for n in names), str(sorted(names)))

    order = res["plan"]["order"]
    pos = {p: i for i, p in enumerate(order)}
    rules_at = [i for p, i in pos.items() if p.startswith("rules/")]
    skills_at = [i for p, i in pos.items() if p.startswith("skills/")]
    ck("every rule is ordered before every skill",
       max(rules_at) < min(skills_at), str(order))
    ck("and a skill comes after the skill it requires",
       pos["skills/bun-base/SKILL.md"] < pos["skills/bun-mid/SKILL.md"] <
       pos["skills/bun-top/SKILL.md"], str(order))

    ck("a rule a skill honors travels even though it was not picked",
       "rules/rule_user_bundle-rule.md" in names)

    # the gap an export must not hide: a required skill left behind
    part = graph.build_bundle(con, ["skill_bun-top"], [],
                              Path(BRAIN) / "out" / "part.zip")
    ck("a required skill that did not travel is named, not silently dropped",
       part["missing_skills"] == ["skill_bun-mid"], str(part["missing_skills"]))

    # --- land it in a brain that has never seen any of it
    other = tempfile.mkdtemp(prefix="second-brain-bundle-")
    try:
        imp = g("import-bundle", "--zip", str(zpath), home=other)
        acts = {a["id"]: a["action"] for a in imp.get("applied", [])}
        ck("an untouched brain creates everything in the bundle",
           set(acts.values()) == {"created"} and len(acts) == 4, str(acts))
        node = g("get-node", "--id", "skill_bun-top", home=other)
        rels = {(e["rel"], e["dst"]) for e in node.get("edges", [])}
        ck("the skill lands accountable to the rule that travelled with it",
           ("honors", "rule_user_bundle-rule") in rels, str(sorted(rels)))
        ck("and requiring the skill below it",
           ("requires", "skill_bun-mid") in rels, str(sorted(rels)))

        # --- the three answers to "this one is already here"
        g("update-node", "--id", "skill_bun-mid", "--title", "Edited locally",
          home=other)
        again = g("import-bundle", "--zip", str(zpath),
                  "--select", "skill_bun-mid:ignore",
                  "--select", "skill_bun-top:replace", home=other)
        acts = {a["id"]: a["action"] for a in again.get("applied", [])}
        ck("ignore leaves this brain's copy alone", acts.get("skill_bun-mid") == "ignored")
        ck("replace rebuilds it from the file", acts.get("skill_bun-top") == "replaced")
        ck("and an item nobody mentioned is not touched either",
           acts.get("skill_bun-base") == "ignored", str(acts))
        title = g("get-node", "--id", "skill_bun-mid", home=other)["node"]["title"]
        ck("so the local edit survived an import that did not ask about it",
           title == "Edited locally", title)

        upd = g("import-bundle", "--zip", str(zpath),
                "--select", "skill_bun-mid:update", home=other)
        acts = {a["id"]: a["action"] for a in upd.get("applied", [])}
        ck("update refreshes it from the incoming file",
           acts.get("skill_bun-mid") == "updated", str(acts))
        ck("and the title it was exported with is back",
           g("get-node", "--id", "skill_bun-mid",
             home=other)["node"]["title"] == "Bundle mid")
    finally:
        shutil.rmtree(other, ignore_errors=True)


def test_bundle_refusals():
    print("\n== a bundle is an untrusted file, and a contract is not history ==")
    import graph
    con = graph.connect()

    ck("nothing selected is refused rather than writing an empty zip",
       not graph.build_bundle(con, [], [], Path(BRAIN) / "out" / "empty.zip")["ok"])
    ck("a skill that is not in this brain is refused",
       not graph.build_bundle(con, ["skill_never-existed"], [],
                              Path(BRAIN) / "out" / "no.zip")["ok"])

    junk = Path(BRAIN) / "out" / "junk.zip"
    junk.parent.mkdir(parents=True, exist_ok=True)
    junk.write_bytes(b"this is not a zip at all")
    ck("a file that is not a zip is reported, not raised",
       not graph.inspect_bundle(con, junk)["ok"])

    import zipfile
    plainzip = Path(BRAIN) / "out" / "plain.zip"
    with zipfile.ZipFile(plainzip, "w") as z:
        z.writestr("skills/x/SKILL.md", "---\nname: x\n---\n\n# X\n")
    got = graph.inspect_bundle(con, plainzip)
    ck("a zip with no import plan is not a bundle",
       not got["ok"] and graph.BUNDLE_PLAN in got["error"], str(got)[:120])

    # a member the plan does not list is never read, so a zip carrying
    # ../../something cannot reach the filesystem through the importer
    sneaky = Path(BRAIN) / "out" / "sneaky.zip"
    with zipfile.ZipFile(sneaky, "w") as z:
        z.writestr(graph.BUNDLE_PLAN, json.dumps({
            "format": graph.BUNDLE_FORMAT, "version": 1,
            "order": ["../../escaped.md"],
            "items": {"../../escaped.md": {"kind": "rule", "id": "rule_user_escaped",
                                           "title": "Escaped"}}}))
        z.writestr("../../escaped.md", "---\ntitle: Escaped\n---\n\nTHE RULE\nno.\n")
    other = tempfile.mkdtemp(prefix="second-brain-sneaky-")
    try:
        res = graph.import_bundle(graph.connect(), sneaky, {"rule_user_escaped": "update"})
        ck("a member outside the bundle's own layout is not written anywhere",
           res["ok"] and not res["applied"], str(res)[:160])
        ck("and no file escaped the archive",
           not Path(BRAIN).parent.joinpath("escaped.md").exists())
    finally:
        shutil.rmtree(other, ignore_errors=True)

    # the OTHER name a bundle controls: a skill is written to disk before it is
    # read back, and the plan says what to call the folder
    walker = Path(BRAIN) / "out" / "walker.zip"
    with zipfile.ZipFile(walker, "w") as z:
        z.writestr(graph.BUNDLE_PLAN, json.dumps({
            "format": graph.BUNDLE_FORMAT, "version": 1,
            "order": ["skills/ok/SKILL.md"],
            "items": {"skills/ok/SKILL.md": {
                "kind": "skill", "id": "skill_walker", "title": "Walker",
                "name": "../../../../../../tmp/walked"}}}))
        z.writestr("skills/ok/SKILL.md",
                   "---\nname: walker\ntitle: Walker\n---\n\n# Walker\n\n## When to use\nnever\n")
    graph.import_bundle(graph.connect(), walker, {"skill_walker": "update"})
    ck("a folder name out of the plan cannot choose where the file lands",
       not Path("/tmp/walked/SKILL.md").exists() and
       (graph.learned_skills_dir() / "tmp-walked" / "SKILL.md").exists(),
       str(sorted(p.name for p in graph.learned_skills_dir().iterdir())))

    # deletion: a contract is not work, so it really goes — but never silently
    graph.api_add_rule(con, title="Doomed rule", sections=FULL_RULE,
                       node_id="rule_user_doomed")
    graph.api_add_skill(con, name="doomed", title="Doomed", sections=FULL_SKILL,
                        rules=["rule_user_doomed"])
    res = graph.delete_contracts(con, ["skill_doomed", "rule_user_doomed", "nope"])
    ck("selected contracts are deleted", len(res["deleted"]) == 2, str(res)[:140])
    ck("an id that is not here is reported rather than counted",
       res["not_found"] == ["nope"], str(res["not_found"]))
    left = con.execute("SELECT count(*) FROM edges WHERE src=? OR dst=?",
                       ("skill_doomed", "skill_doomed")).fetchone()[0]
    ck("and every edge on them went with them", left == 0, str(left))

    ck("a task is refused: archive-never-delete still holds for work",
       not graph.delete_contracts(con, ["hub_convention"])["deleted"])

    graph.api_add_skill(con, name="shipped", title="Shipped", sections=FULL_SKILL,
                        kind="plugin")
    res = graph.delete_contracts(con, ["skill_shipped"])
    ck("a built-in is refused, because its file would re-ingest it",
       not res["deleted"] and res["refused"][0]["id"] == "skill_shipped", str(res)[:140])
    ck("and goes only when the caller says so anyway",
       len(graph.delete_contracts(con, ["skill_shipped"], force=True)["deleted"]) == 1)


def test_bundle_web():
    print("\n== the browser moves a bundle the same way the shell does ==")
    import base64
    home = tempfile.mkdtemp(prefix="second-brain-bundle-w-")
    port = 8798
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "serve.py"), "--port", str(port),
         "--no-open", "--no-reload"],
        env=env_for(home), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def call(path, body=None, raw=False):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read() if raw else json.loads(r.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    try:
        for _ in range(600):
            try:
                if call("/api/meta").get("ok"):
                    break
            except Exception:
                time.sleep(0.2)
        call("/api/create-skill", {"name": "web-bun", "title": "Web bundle",
                                   "description": "d", "sections": FULL_SKILL})
        exp = call("/api/export-bundle", {"skills": ["skill_web-bun"], "rules": []})
        ck("the page can bundle a selection", exp["ok"] and exp["skills"] == 1,
           str(exp)[:140])
        blob = call(exp["download"], raw=True)
        ck("and download it as a real zip", blob[:2] == b"PK", str(blob[:8]))

        ck("a name pointing out of the bundle directory serves nothing",
           not call("/api/bundle?name=../graph.db")["ok"])

        got = call("/api/upload-bundle",
                   {"filename": "web.zip", "data": base64.b64encode(blob).decode()})
        ck("an uploaded bundle is inspected before anything is written",
           got["ok"] and got["items"][0]["exists"], str(got)[:140])
        ck("and the inspection wrote nothing", not call("/api/skills")["trees"][0]["skills"])

        imp = call("/api/import-bundle",
                   {"token": got["token"], "select": {"skill_web-bun": "replace"}})
        ck("the page imports what was ticked",
           imp["ok"] and imp["applied"][0]["action"] == "replaced", str(imp)[:140])
        ck("a staged upload is not reusable once it landed",
           not call("/api/import-bundle",
                    {"token": got["token"], "select": {"skill_web-bun": "update"}})["ok"])
        ck("garbage in place of base64 is refused",
           not call("/api/upload-bundle", {"data": "not base64 at all!!"})["ok"])

        d = call("/api/delete-contracts", {"ids": ["skill_web-bun"]})
        ck("and deletes what was ticked", d["ok"] and d["count"] == 1, str(d)[:140])
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(home, ignore_errors=True)


def main():
    g("init")
    test_home_adoption()
    test_home_adoption_keeps_the_data()
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
    test_built_ui_is_coherent()
    test_ui_build_is_decided_correctly()
    test_api_docs_are_served()
    test_bundle_round_trip()
    test_bundle_refusals()
    test_bundle_web()
    shutil.rmtree(BRAIN, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
