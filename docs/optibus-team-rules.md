# Working rules — Optibus

Thirteen rules that accumulated in one engineer's Memory Ledger while working
here, exported so the team can use them. Each one exists because something went
wrong (or right) and the correction was worth keeping. Most name the incident
that produced them.

They are written as **team rules, not personal preferences** — read them as
practice you can adopt, argue with, or improve. Nothing here is policy.

If you run the Memory Ledger plugin, the last section loads all thirteen into
your own brain in one paste, and your agent then pulls only the ones that match
what you are doing (`match-rules`) instead of reading this whole page.

---

## Engineering practice

### 1. Before coding, look for an existing branch and PR

Never open a branch for a ticket without checking what already exists for it. Run
both before the first edit:

```
git branch -a --list "*<TICKET>*"      # local + remote branches
gh pr list --search "<TICKET>"         # open/closed PRs; gh pr view <n> for its headRefName
```

If a branch or an open PR exists, the work goes **there** — commit on top, no
force-push unless asked. Create a new branch only when nothing exists, or when
the change is genuinely a separate deliverable. A second branch for the same
ticket splits review and orphans work.

When a PR is already under review, adding scope to it costs a re-review. Check
`gh pr view <n> --json reviewDecision` and say so rather than silently growing
the diff.

*Caught by:* a P2 fix built on a fresh branch (2026-07-31, OS-84566) while a PR
was already open for the same ticket. The commit had to be cherry-picked over and
the branch deleted. Cheap check, expensive miss.

### 2. A comment on your PR is an open loop until answered and signalled

Any question, comment or change request on your PR needs an action, and stays
open until it is closed on the PR itself:

- reviewer **commented** or **requested changes** → after the work is done,
  **re-request their review**. They are not watching. An addressed change-request
  that was never re-requested still reads as blocked.
- reviewer **already approved** → do not re-request. Answer what is outstanding
  and resolve the threads that no longer apply.
- "not going to solve it" is a valid answer, but it has to be said on the thread,
  with a reason and a follow-up ticket.

Silence is not a decision.

### 3. Integrations experiments: source `~/experiments.env` from HOME

The enabler for running any integrations experiment is `~/experiments.env` — the
file in your **home** directory, which is what PyCharm points at.

There is often a stale `~/dev/experiments.env` too. Its `MITHRA_AUTH_TOKEN`
expires and sourcing it produces a misleading 401 from the timeplan/sorting API.
Ignore that copy; if in doubt, compare timestamps and token values.

Run with the Pants-exported venv python, never `pants run` — dynamic
`init_registry` imports break inside the Pants sandbox:

```
set -a && source ~/experiments.env && set +a && \
PYTHONPATH=python \
~/dev/integrations/dist/export/python/virtualenvs/python-default/3.13.4/bin/python \
<path to *_experiment.py>
```

Run from the repo (or a worktree with `PYTHONPATH` pointed at its `python/`
dir). Port-forwards must be up: Mithra `127.0.0.1:8001`, Hermes
`127.0.0.1:8000`, external API `127.0.0.1:8002` (a 403 on `/` means it is up).

### 4. Timetable / sorting-API experiments need the local prefix override

Any experiment that fetches timeplan data through the sorting API — Timetable
(TTR), Duty Cards, V/Line and the rest — needs **two** setup steps. Out of the
box it fails `401 Unauthorized`. That is setup, never a finding: do not report a
401 as an experiment result.

**Step 1 — prefix override (temporary, never commit).** In
`python/integrations/ruais/utils/get_data_from_api.py`:

- `_get_url_prefix` → return `"http://127.0.0.1:8002"` (preferred: covers every
  export making these calls), or
- `_get_timeplan_trips_url` only, for the timeplan-trips call alone.
- For `pdf_public_timetable_report_based_api`, the same change in
  `_get_api_response`.

Port 8002 must be forwarded — `curl http://127.0.0.1:8002/` returning 403 means
it is up.

**Step 2 — a valid Mithra token.** The `Authorization` header is
`MITHRA_AUTH_TOKEN` straight from `~/experiments.env`. An expired token still
401s **after** the prefix override; the tell is 403 with no token versus 401 with
one, body `{"message":"Unknown error","status":401}`. Refresh with
`/jenkins mithra_token <env>` in Slack and paste it into `~/experiments.env`.

*Source:* the CORD Confluence page "Setup environment for Timetable experiments"
(step 1), plus an observed 401 on 2026-07-31 (step 2).

---

## Working with AI tooling

### 5. When AI-generated code fails, find the process gap

Fixing the output and moving on wastes the only useful signal. When code produced
by an agent turns out wrong, diagnose **where the process broke**:

- **Refinement** — the ticket did not carry enough information to build from.
- **Specification** — the instruction given to the agent was ambiguous or
  incomplete.
- **Validation / execution** — the check that should have caught it was missing,
  or was never run.

Then fix that layer, not just the symptom. This is how the tooling improves
instead of repeating the same defect.

The standing position behind it: AI does not remove code ownership. This rule is
the operational form of that.

### 6. Token cost is a hard constraint

Treat token spend as a real budget, not a nicety. Prefer paths that cost no model
call: date views and agendas from a query script, search/recall over a local
index, rule matching instead of reading whole documents, a UI for writes. When a
task genuinely needs a large read, say what it will cost before doing it.

---

## Working with other people

### 7. Help-channel messages: short, direct, only the ask

When writing to `#help-ask-an-infra` or any other help or support channel, ask
the question and nothing else. The reader is a stranger to the problem who wants
to know what to do, not why it exists.

**Shape:** "Hi infra team, we need X permission for Y. Could someone help?" One
or two sentences. The specific thing needed, the specific target, done.

**Do not include:** the backstory of how the problem arose, prior failed
attempts, contradictions to reconcile, architectural opinions, side questions,
who is covering for whom, or a narrative of a meeting. All of that is real
context, and it belongs in your notes, in the ticket, or in a reply **if** they
ask.

**Why:** a long message buries the ask. Whoever triages a help channel scans for
"what do you need from me". If that takes a paragraph to find, the request waits
longer — the opposite of the goal. Length reads as complexity and gets deferred.

Several asks: short bullets, one line each, no explanation per line. If one
genuinely needs context to be actionable, add one short sentence, not a
paragraph. Unrelated questions go in a separate message.

*Caught by:* an infra request (2026-08-04) that explained the whole history, the
404s, three prior attempts, a contradiction with a teammate's earlier claim, a
repo migration, and an architecture question — when it should have been: "Hi
infra, we need R&D team access on these two repos, and me as admin on one of
them. Could someone help?"

### 8. Before logging "waiting on X", verify X was actually told

Unspoken expectations are premeditated resentments. Never record something as
waiting on someone without checking they were actually asked — a review request
that was never sent, a question never posted, a deadline never stated. If they
were not told, the next action is to tell them, not to wait.

*Caught by:* a PR that sat as "waiting on a reviewer" while GitHub had no review
request pending for them at all.

### 9. Close the loop on the source page, not just in your own tracker

When an action item lives on someone else's page, tick it **there**. Your
manager's view of what you delivered is the source page: an item that is done but
left unticked reads as not done, and 1:1 pages often carry an auto-updated task
report that only drops an item when its box is ticked. Applies to Confluence 1:1
pages, Jira, and any shared doc with checkboxes.

**The catch, if you automate it (Confluence):** the Atlassian MCP has no
per-checkbox write. `updateConfluencePage` replaces the **entire** page body, so
ticking one box means re-emitting the whole HTML including every
`data-local-id` and any macro's escaped-JSON `data-parameters`. Retyping that
from a model risks corrupting the page. So do not blind-rewrite someone else's
page body. Either do a byte-exact scripted edit (fetch, targeted string replace,
PUT) with an API token, or hand the person the exact item text and let them
click it. Note that a 1:1 page often lives in the manager's **personal** space,
which you edit as a guest — another reason not to rewrite it wholesale.

**Verify before ticking:** only tick what is unambiguously done. Standing habit
items ("continue doing X") are never tickable. If your own tracker still has the
task open, leave the box and flag it instead of deciding for someone.

### 10. Share what you build

Improving your own performance is not the whole job — the ask at senior level is
to raise the whole team's. Building tools that benefit the team collectively is
the clearest example.

In practice: when something is built, or a useful insight found, tell the team,
offer it, and ask people to try it. Small shared solutions generate
collaborations that would not otherwise happen. After finishing anything
reusable, **the loop is not closed until someone else knows it exists.**

Because this does not happen on its own, make it structural rather than a matter
of remembering: a scheduled post, a standing agenda item, a template.

*Source:* 1:1 coaching, 2026-07-30.

---

## Delivery and ownership

### 11. Quick tasks get done now, not by the deadline

If a task is quick, resolve it immediately instead of letting it run out to the
longer deadline it was given.

Accumulated routine delays behave like **memory leaks** — individually small,
invisible, and collectively the reason a cycle slips. Find what causes them
rather than absorbing them.

The definition of ownership behind this: taking responsibility for a demand at
the speed and urgency that demand requires. Urgency is the impulse to act and
close, not a priority label.

**The caveat that comes with it:** as velocity rises, code quality has to hold.
Speed that costs quality is not the behaviour being asked for.

*Source:* 1:1, 2026-07-30.

### 12. Technical design is not independent of product — align it with the PO

A technical design detached from the product need produces either overkill or the
wrong solution. Understanding the real need is **part of** the design work, not a
preliminary to it.

Align the design with the product owner so the solution meets the business
requirement and the roadmap. Do not define the product alone under the cover of
designing the system. Stay inside company standards for deploy control; the
architecture choices inside those constraints are where the design work shows.

*Source:* 1:1, 2026-07-30, on what senior-level design looks like.

### 13. File failures, praise, successes and improvements as they happen

Whenever something you did failed, was praised, succeeded, or revealed a
behaviour worth reinforcing or cutting, write it down as its own record, filed
under one of four buckets: **failure** (what went wrong), **praise** (what
someone else said was good), **success** (what worked, with evidence),
**improvement** (the behaviour to enforce or cut).

**Content:** what happened, the consequence, and the behaviour behind it.
Attribute praise to who said it and when. Evidence over adjectives — a
self-evaluation needs the specific instance, not a summary of a feeling. Do not
soften a failure into a neutral observation, and do not inflate a success.

**Why:** these feed 1:1s and IDP cycles. The recurring problem is that the record
is gone by the time it is needed — successes get under-claimed, patterns cannot
be evidenced, and the same behaviour gets re-argued from memory. A cycle's worth
of dated records turns "I think I have been slow" into a list. Reading failure
and praise together also stops the set from becoming a deficit list.

**Framing that matters when you write these:** record the mechanism, not the
character. "The ticket did not say which service owned the field" is useful;
"I was careless" is not. An improvement topic that recurs becomes a personal rule
or an IDP goal — link it back to the records it was read off, so the reasoning
survives.

---

## Load these into your own Memory Ledger

With the plugin installed and the brain enabled (`INSTALL.md`), paste this once.
Each rule becomes a `rule` node with cue words, so
`graph.py match-rules "<what you are about to do>"` returns only the ones that
apply — no model call, and none of the cost of reading this page.

```bash
G="python3 $HOME/dev/second-brain/scripts/graph.py"     # adjust to your clone

# once, on a new brain: create the home, the hub rules hang off, and the base layer
mkdir -p ~/.second_brain
$G init
$G add-area --title Convention --domain convention --realm shared
$G ingest-conventions

$G add-rule --realm optibus --section team --title "Before coding, look for an existing branch and PR" \
  --scenario "start development, implement, fix, new branch, worktree, ticket, existing PR, check first, pick up the ticket, create a branch, is there already a branch, duplicate work, start the ticket" \
  --body "Run 'git branch -a --list \"*<TICKET>*\"' and 'gh pr list --search \"<TICKET>\"' before the first edit. If a branch or open PR exists the work goes there. A second branch for the same ticket splits review and orphans work. Adding scope to a PR already under review costs a re-review - check 'gh pr view <n> --json reviewDecision' and say so."

$G add-rule --realm optibus --section team --title "A comment on your PR is an open loop until answered and signalled" \
  --scenario "pull request, PR, review, reviewer, comment, change request, approved, thread, re-request, requested changes, unresolved, resolve the thread, needs my reply, waiting on my answer" \
  --body "Commented or requested changes: after the work is done, re-request their review - they are not watching. Already approved: do not re-request; answer what is outstanding and resolve stale threads. 'Not going to solve it' must be said on the thread with a reason and a follow-up ticket. Silence is not a decision."

$G add-rule --realm optibus --section team --title "Integrations experiments: source ~/experiments.env from HOME" \
  --scenario "run experiment, integrations, export, payload, experiments.env, venv, reproduce, baseline, local run, script fails, environment variables, credentials, pants" \
  --body "Use ~/experiments.env (HOME), not the stale ~/dev/experiments.env whose MITHRA_AUTH_TOKEN is expired and produces a misleading 401. Run with the Pants-exported venv python, never 'pants run' (dynamic init_registry imports break in the sandbox): set -a && source ~/experiments.env && set +a && PYTHONPATH=python <dist/export venv python> <path to *_experiment.py>. Port-forwards up: Mithra 127.0.0.1:8001, Hermes 127.0.0.1:8000, external API 127.0.0.1:8002 (403 on / means up)."

$G add-rule --realm optibus --section team --title "Timetable/sorting-API experiments need the local prefix override" \
  --scenario "TTR, timetable, timeplan, trips, sort, duty cards, vline, pdf public timetable, 401, unauthorized, prefix, 8002, sorting api, timetable export, local port, points to prod" \
  --body "Two setup steps, or it fails 401 - that is setup, never a finding. 1) In python/integrations/ruais/utils/get_data_from_api.py make _get_url_prefix return http://127.0.0.1:8002 (or just _get_timeplan_trips_url; for pdf_public_timetable_report_based_api, _get_api_response). Never commit it. Port 8002 forwarded: curl http://127.0.0.1:8002/ returning 403 means up. 2) A valid MITHRA_AUTH_TOKEN from ~/experiments.env - an expired one still 401s after the override. Refresh with '/jenkins mithra_token <env>' in Slack."

$G add-rule --realm optibus --section team --title "When AI-generated code fails, find the process gap" \
  --scenario "ai code failed, wrong output, rerun, generated code, agent wrote, retry, debug, hallucinated, keeps failing, regenerate, bad output" \
  --body "Diagnose where the process broke: REFINEMENT (the ticket did not carry enough information), SPECIFICATION (the instruction to the agent was ambiguous), or VALIDATION/EXECUTION (the check that should have caught it was missing or not run). Fix that layer, not the symptom. AI does not remove code ownership."

$G add-rule --realm optibus --section team --title "Token cost is a hard constraint" \
  --scenario "token, cost, budget, expensive, recall, query, performance, how much does it cost, cheaper, save tokens, context window, off-model, without a model call" \
  --body "Prefer paths that cost no model call: brain_query.py for date views, graph.py search/recall/open-loops, match-rules instead of reading whole documents, the web UI for writes. When a task genuinely needs a large read, say what it will cost before doing it."

$G add-rule --realm optibus --section team --title "Help-channel messages: short, direct, only the ask" \
  --scenario "write to infra, help channel, ask another team, request access, permission request, help-ask-an-infra, support request, escalate to another team, slack message to a team" \
  --body "Ask the question and nothing else. Shape: 'Hi infra team, we need X permission for Y. Could someone help?' Do not include the backstory, prior failed attempts, contradictions, architectural opinions or side questions - that belongs in the ticket, or in a reply if they ask. A long message buries the ask and gets deferred. Several asks: one-line bullets. Unrelated questions: a separate message."

$G add-rule --realm optibus --section team --title "Before logging 'waiting on X', verify X was actually told" \
  --scenario "waiting on, blocked by, follow up, chase, delegate, hand-off, expectation, no response, has not replied, nudge them, park it, ball is in their court, depends on someone" \
  --body "Unspoken expectations are premeditated resentments. Never record something as waiting on someone without checking they were actually asked - a review request never sent, a question never posted, a deadline never stated. If they were not told, the next action is to tell them, not to wait."

$G add-rule --realm optibus --section team --title "Close the loop on the source page, not just in your own tracker" \
  --scenario "confluence, action item, done, tick, checkbox, 1:1 notes, meeting page, close loop, source of truth, mark done, jira, comment on the ticket, but did you update" \
  --body "Tick the item where it lives - the manager's view of what you delivered is the source page, and 1:1 task reports only drop an item when its box is ticked. Confluence catch: updateConfluencePage replaces the ENTIRE body, so ticking one box means re-emitting every data-local-id and macro parameter. Do not blind-rewrite someone else's page from a model: do a byte-exact scripted edit, or hand over the exact item text to click. 1:1 pages often live in the manager's personal space. Only tick what is unambiguously done."

$G add-rule --realm optibus --section team --title "Share what you build" \
  --scenario "built a tool, finished something, shipped, share, tell the team, promote, demo, announce, impact, visibility, reusable, would help the team, nobody else uses it" \
  --body "Improving your own performance is not the whole job - the senior ask is raising the whole team's. When something is built or a useful insight found, tell the team, offer it, ask people to try it. The loop is not closed until someone else knows it exists. Make it structural (a scheduled post, a standing agenda item, a template) rather than a matter of remembering."

$G add-rule --realm optibus --section team --title "Quick tasks get done now, not by the deadline" \
  --scenario "small task, quick fix, deadline, due date, urgency, routine delay, do it later, next week, not urgent, plenty of time, been meaning to, low priority" \
  --body "Resolve quick tasks immediately instead of letting them run to the longer deadline they were given. Accumulated routine delays are memory leaks - individually small, collectively why a cycle slips. Ownership is taking responsibility for a demand at the speed that demand requires. Caveat: as velocity rises, code quality must hold."

$G add-rule --realm optibus --section team --title "Technical design is not independent of product - align it with the PO" \
  --scenario "design doc, HLD, architecture, solution, technical proposal, build feature, scope, requirements, what is the approach, over-engineering, overkill, is this what they wanted, product owner, PO, scope creep" \
  --body "A design detached from the product need produces overkill or the wrong solution. Understanding the real need is part of the design work, not a preliminary to it. Align with the product owner so the solution meets the business requirement and the roadmap; do not define the product alone under cover of designing the system. Stay inside company standards for deploy control - the architecture choices inside them are where the design shows."

$G add-rule --realm optibus --section team --title "File failures, praise, successes and improvements as they happen" \
  --scenario "failure, mistake, went wrong, praise, compliment, thanks, success, worked, shipped, improvement, feedback, 1:1, IDP, retro, self-eval, performance review, what did I achieve" \
  --body "File each as its own record under one of four buckets: failure, praise, success, improvement. State what happened, the consequence, and the behaviour behind it; attribute praise to who said it and when; evidence over adjectives. Do not soften a failure or inflate a success. These feed 1:1s and IDP cycles - without them successes get under-claimed and patterns cannot be evidenced. Record the MECHANISM, not the character. A recurring improvement topic becomes a rule or an IDP goal."
```

Check it landed — thirteen rules with `"section": "team"`, and a cue query that
returns the right one:

```
python3 scripts/graph.py list --type rule --all
python3 scripts/graph.py match-rules "open a branch for a ticket"
```

`match-rules` is the point of loading them: it costs no model call and returns
only the rules whose cue words match what you are doing, so your agent never
reads the other twelve.
