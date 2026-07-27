# Installing second-brain

second-brain is a Claude Code plugin. Installing it makes its skills, the `/brain`
command, and the always-on capture hooks available **globally** (every project,
every session). It works in Claude Code and the Claude app.

## Prerequisites

- **Claude Code** (recent version — plugin marketplaces + `Stop`/`UserPromptSubmit` hooks).
- **Python 3** on your `PATH` (the engine and hooks are stdlib-only — no packages).
- **git** (for the brain's own history and, optionally, installing from GitHub).

Nothing else. The brain has zero third-party dependencies.

## 1. Install the plugin (global)

The plugin ships its own marketplace manifest (`.claude-plugin/marketplace.json`),
so you add the repo as a marketplace, then install from it.

**From a local clone** (what you'll do on your own machines):

```
git clone git@github.com:gabriel-nori-optibus/second-brain.git ~/dev/second-brain
```
then, inside Claude Code:
```
/plugin marketplace add ~/dev/second-brain
/plugin install second-brain@second-brain
```

**From GitHub directly** (no manual clone):
```
/plugin marketplace add gabriel-nori-optibus/second-brain
/plugin install second-brain@second-brain
```

**Dev mode** (try it for one session without installing):
```
claude --plugin-dir ~/dev/second-brain
```

Restart the session (or run `/reload-plugins`) after installing.

### Runs on every session (user-level)

Installing via `/plugin install` enables the plugin at **user level**, so it's
active in **all projects and all sessions** — the hooks are not scoped to any repo:

- The capture hooks (`UserPromptSubmit`, `Stop`) are **matcher-less** → they fire
  on every prompt and every completed turn, in every session.
- The session brief (`SessionStart`) fires on `startup`, `resume`, `clear`, and
  `compact`, so your open loops resurface however a session begins.
- Hook commands use `${CLAUDE_PLUGIN_ROOT}` (absolute) and the global brain home
  (`~/.second_brain`), so they behave identically regardless of the session's `cwd`.

Nothing extra is needed to "make it global" — user-level enable is the switch.
(Sub-agents don't double-capture: they have no `UserPromptSubmit`, and `Stop` is
the main agent's turn, not `SubagentStop`.)

## 2. Enable the brain

The brain is **enabled when its home directory exists**. Skills and hooks no-op
silently until then, so create it once:

```
mkdir -p ~/.second_brain
python3 ~/dev/second-brain/scripts/graph.py init
```

- Home dir: `~/.second_brain` (override with `$SECOND_BRAIN_HOME`; exact DB file with
  `$SECOND_BRAIN_DB`).
- Set your timezone once so date views bucket correctly, e.g. in your shell rc:
  `export SECOND_BRAIN_TZ="America/Sao_Paulo"`.
- `$SECOND_BRAIN_*` is the **only** config prefix, and `~/.second_brain` the only
  default home. Since v0.1.1 there is no fallback to the pre-rebrand `WORK_BRAIN_*`
  vars or `~/.work_brain` home — if you still have an old home dir, move it to
  `~/.second_brain` yourself; it will not be picked up.

To **disable** the brain anywhere, unset/rename that dir — the hooks detect it and
go quiet.

### Optional: shared neo4j master

By default everything runs on the local SQLite mirror (offline, instant). To share
one brain across machines, point it at a neo4j server — create `~/.second_brain/neo4j.json`:

```json
{ "url": "http://your-neo4j-host:7474", "db": "neo4j", "user": "neo4j", "password": "…" }
```
(or the env vars `SECOND_BRAIN_NEO4J_URL / _DB / _USER / _PASSWORD`). Writes go to
SQLite immediately and sync to neo4j through an offline outbox; reads prefer the
master when it's reachable. **Keep this file out of the repo — it holds a secret.**

## 3. What turns on automatically (no model call)

Once enabled, hooks run in **pure Python/SQL — no model call**:

- **SessionStart** → injects a brief of your open loops (`open_loops_brief.py`).
- **UserPromptSubmit** → `capture_prompt.py` stores each prompt as a `capture` node
  and auto-creates `task` nodes for explicit to-dos ("remind me…", "todo:", "follow
  up…"), linked back to the turn.
- **Stop** → `capture_answer.py` stores an extractive summary of the answer, linked
  to its question.

So every interaction is remembered without a single model call. Two of the hooks do
add a short line to the conversation — 197 chars per prompt and an ~850-char
open-loops brief per session start (`Stop` adds nothing) — which measured **0.01% of
a real session**. Recall itself runs outside the conversation and costs nothing:

```
python3 scripts/brain_query.py agenda          # today/tomorrow/week/closed/range
python3 scripts/graph.py search "<terms>"      # full-text over the graph
python3 scripts/graph.py open-loops            # everything still awaiting action
```

### Map + dashboard (optional UI)

```
SECOND_BRAIN_TZ="America/Sao_Paulo" python3 viz/serve.py       # http://127.0.0.1:8770
SECOND_BRAIN_TZ="America/Sao_Paulo" python3 viz/serve.py --no-write   # read-only
```
One page, two tabs: **Map** (live graph) and **Dashboard** (the query views). The
dashboard also **edits** the brain — create tasks with auto-recognised links,
complete them, change status/assignee/due, rename, and add/remove relations — all
token-free (no model). Add `--host 0.0.0.0` to reach it from your phone on the LAN
/ a tunnel; because the write API is **unauthenticated**, pair that with
`--no-write` unless you trust every device on the network.

## 4. Verify

```
/plugin              # second-brain shows as installed + enabled
/hooks               # SessionStart, UserPromptSubmit, Stop are listed
claude plugin list   # from the CLI
```
Then send any message and check it landed:
```
python3 scripts/graph.py list --type capture --all --limit 5
```

## Uninstall

```
/plugin uninstall second-brain@second-brain
```
Your brain data in `~/.second_brain` is left untouched (delete that dir to remove it).
