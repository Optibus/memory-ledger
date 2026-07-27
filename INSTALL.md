# Installing work-brain

work-brain is a Claude Code plugin. Installing it makes its skills, the `/brain`
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
git clone git@github.com:gabriel-nori-optibus/work-brain.git ~/dev/work-brain
```
then, inside Claude Code:
```
/plugin marketplace add ~/dev/work-brain
/plugin install work-brain@work-brain
```

**From GitHub directly** (no manual clone):
```
/plugin marketplace add gabriel-nori-optibus/work-brain
/plugin install work-brain@work-brain
```

**Dev mode** (try it for one session without installing):
```
claude --plugin-dir ~/dev/work-brain
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
  (`~/.work_brain`), so they behave identically regardless of the session's `cwd`.

Nothing extra is needed to "make it global" — user-level enable is the switch.
(Sub-agents don't double-capture: they have no `UserPromptSubmit`, and `Stop` is
the main agent's turn, not `SubagentStop`.)

## 2. Enable the brain

The brain is **enabled when its home directory exists**. Skills and hooks no-op
silently until then, so create it once:

```
mkdir -p ~/.work_brain
python3 ~/dev/work-brain/scripts/graph.py init
```

- Home dir: `~/.work_brain` (override with `$WORK_BRAIN_HOME`; exact DB file with
  `$WORK_BRAIN_DB`).
- Set your timezone once so date views bucket correctly, e.g. in your shell rc:
  `export WORK_BRAIN_TZ="America/Sao_Paulo"`.

To **disable** the brain anywhere, unset/rename that dir — the hooks detect it and
go quiet.

### Optional: shared neo4j master

By default everything runs on the local SQLite mirror (offline, instant). To share
one brain across machines, point it at a neo4j server — create `~/.work_brain/neo4j.json`:

```json
{ "url": "http://your-neo4j-host:7474", "db": "neo4j", "user": "neo4j", "password": "…" }
```
(or the env vars `WORK_BRAIN_NEO4J_URL / _DB / _USER / _PASSWORD`). Writes go to
SQLite immediately and sync to neo4j through an offline outbox; reads prefer the
master when it's reachable. **Keep this file out of the repo — it holds a secret.**

## 3. What turns on automatically (token-free)

Once enabled, hooks run in **pure Python/SQL — no model, no tokens**:

- **SessionStart** → injects a brief of your open loops (`open_loops_brief.py`).
- **UserPromptSubmit** → `capture_prompt.py` stores each prompt as a `capture` node
  and auto-creates `task` nodes for explicit to-dos ("remind me…", "todo:", "follow
  up…"), linked back to the turn.
- **Stop** → `capture_answer.py` stores an extractive summary of the answer, linked
  to its question.

So every interaction is remembered without spending tokens, and you recall it the
same way — free:

```
python3 scripts/brain_query.py agenda          # today/tomorrow/week/closed/range
python3 scripts/graph.py search "<terms>"      # full-text over the graph
python3 scripts/graph.py open-loops            # everything still awaiting action
```

### Map + dashboard (optional UI)

```
WORK_BRAIN_TZ="America/Sao_Paulo" python3 viz/serve.py       # http://127.0.0.1:8770
```
One page, two tabs: **Map** (live graph) and **Dashboard** (the query views). Add
`--host 0.0.0.0` to reach it from your phone on the LAN / a tunnel. Read-only, no tokens.

## 4. Verify

```
/plugin              # work-brain shows as installed + enabled
/hooks               # SessionStart, UserPromptSubmit, Stop are listed
claude plugin list   # from the CLI
```
Then send any message and check it landed:
```
python3 scripts/graph.py list --type capture --all --limit 5
```

## Uninstall

```
/plugin uninstall work-brain@work-brain
```
Your brain data in `~/.work_brain` is left untouched (delete that dir to remove it).
