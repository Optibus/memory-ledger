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
git clone git@github.com:Optibus/second-brain.git ~/dev/second-brain
```
then, inside Claude Code:
```
/plugin marketplace add ~/dev/second-brain
/plugin install second-brain@second-brain
```

**From GitHub directly** (no manual clone):
```
/plugin marketplace add Optibus/second-brain
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

### Optional: mirror one brain across machines

**Nothing here is required.** Out of the box the brain is one local SQLite file —
offline, instant, and connected to nothing.

If you want the same brain on more than one machine, point it at a graph server you
control by creating `~/.second_brain/neo4j.json`:

```json
{ "url": "https://<your-graph-host>:<port>", "db": "neo4j", "user": "…", "password": "…" }
```
(or the env vars `SECOND_BRAIN_NEO4J_URL / _DB / _USER / _PASSWORD`). Writes land in
SQLite immediately and mirror through an offline outbox, so an unreachable mirror
never blocks or loses a write.

Two things worth getting right if you do this:
- **Use TLS.** The credentials and your whole graph cross the network on every write.
- **Keep the config file out of any repo — it holds a secret.** Restrict it with
  `chmod 600`, and never paste the credentials into a command that gets saved (a
  shell history or a tool-permission allowlist counts as saved).

## 3. What turns on automatically (no model call)

Once enabled, hooks run in **pure Python/SQL — no model call**:

- **SessionStart** → injects a brief of your open loops (`open_loops_brief.py`),
  and **once a day** kicks off consolidation in the background: closed days'
  captures are folded into digest notes (originals kept). Detached, so it never
  delays the session; disable with `SECOND_BRAIN_NO_CONSOLIDATE=1`. It runs here
  rather than as a scheduled cloud routine because the brain is a local file — a
  cloud routine cannot read it. If you want it to run even on days you never open
  Claude Code, add a launchd agent calling
  `graph.py consolidate --older-than 1`.
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
