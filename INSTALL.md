# Installing Memory Ledger

Memory Ledger is a Claude Code plugin. Installing it makes its skills, the
`/ledger` command, and the always-on capture hooks available **globally** (every
project, every session). It works in Claude Code and the Claude app.

> **One name now.** The product, the repo, the marketplace and the plugin id are
> all **`memory-ledger`**, and the command is `/ledger`. They were `second-brain`
> up to 0.6.0. `/brain` was the old command name and is gone.
>
> **The brain keeps its own name.** Your memory lives in `~/.second_brain` and is
> addressed by `$SECOND_BRAIN_HOME` / `$SECOND_BRAIN_DB`. None of that moved, and
> none of it should: the brain is your data, not the plugin. Renaming the plugin
> does not touch a single node.
>
> Already running the old plugin? See
> [Upgrading from second-brain](#upgrading-from-second-brain).

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
git clone git@github.com:Optibus/memory-ledger.git ~/dev/memory-ledger
```
then, inside Claude Code:
```
/plugin marketplace add ~/dev/memory-ledger
/plugin install memory-ledger@memory-ledger
```

**From GitHub directly** (no manual clone):
```
/plugin marketplace add Optibus/memory-ledger
/plugin install memory-ledger@memory-ledger
```

**Dev mode** (try it for one session without installing):
```
claude --plugin-dir ~/dev/memory-ledger
```

Restart the session (or run `/reload-plugins`) after installing.

**From a zip** (no GitHub access needed — see [below](#no-repo-access-install-from-a-zip)).

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
python3 ~/dev/memory-ledger/scripts/graph.py init
```

- Home dir: `~/.second_brain` (override with `$SECOND_BRAIN_HOME`; exact DB file with
  `$SECOND_BRAIN_DB`).
- Set your timezone once so date views bucket correctly — either in the UI
  (**Settings › Time zone**, which writes `~/.second_brain/settings.json` and is
  read by the CLI, the hooks and any cron too), or in your shell rc:
  `export SECOND_BRAIN_TZ="America/Sao_Paulo"`. The env var wins over the file.
- `$SECOND_BRAIN_*` is the **only** config prefix, and `~/.second_brain` the only
  default home. Since v0.1.1 there is no fallback to the pre-rebrand `WORK_BRAIN_*`
  vars or `~/.work_brain` home — if you still have an old home dir, move it to
  `~/.second_brain` yourself; it will not be picked up.

To **disable** the brain anywhere, unset/rename that dir — the hooks detect it and
go quiet.

### Optional: a local neo4j, in one command

Also not required. SQLite is the default and is enough for one person's brain;
neo4j buys **query shape** (variable-depth paths, shortest path, centrality), not
speed — measured, it costs ~850 MiB resident against 26 MiB. Read
[docs/neo4j.md](docs/neo4j.md) before turning it on.

```
python3 ~/dev/memory-ledger/scripts/neo4j_local.py up     # or /neo4j-up in Claude Code
```

Idempotent, and it does the whole job: a `neo4j:5` container with
**`--restart unless-stopped`**, the store bind-mounted at
`~/.second_brain/neo4j/{data,logs}`, bound to `127.0.0.1` only, a password
generated into `~/.second_brain/neo4j.json` (chmod 600, never printed), the
`backend` setting flipped to `auto`, and the existing brain backfilled into it.
Needs Docker; nothing else.

```
python3 ~/dev/memory-ledger/scripts/neo4j_local.py status
python3 ~/dev/memory-ledger/scripts/neo4j_local.py down      # stops, and stays stopped
```

If a master is **already** configured, `up` refuses to repoint it until you pass
`--switch` (the old config is backed up first) or `--keep-config`.

The `backend` setting it writes is where `brain_query.py` reads from when no
`--backend` flag is given: `auto` (neo4j when reachable, else the local mirror),
`neo4j` (master only), or `sqlite`. Change it any time in the UI's
**Settings › Database connection**.

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
- **PreToolUse / PostToolUse** (matcher `Task`) → `capture_agent.py` puts each
  subagent in the **agent tree** as it spawns and stamps its outcome
  (`completed` / `failed`) as it returns, so what a subagent considered does not
  vanish when its branch ends (CONVENTIONS §2k). Other tools are ignored.
- **PreCompact** → `close_conversation.py compact` records one `summarized`
  context event: the one moment Claude Code says the window changed wholesale.
- **SessionEnd** → `close_conversation.py end` closes the conversation and stamps
  an outcome on every agent still open, so no branch is left reading like a crash.

So every interaction is remembered without a single model call. Two of the hooks do
add a short line to the conversation — 209 chars per prompt and a ~1.0 KB
open-loops brief per session start (everything else adds nothing) — which measured
**under 0.05% of a working day**. Recall itself runs outside the conversation
and costs nothing:

```
python3 scripts/brain_query.py agenda          # today/tomorrow/week/closed/range
python3 scripts/graph.py search "<terms>"      # full-text over the graph
python3 scripts/graph.py open-loops            # everything still awaiting action
python3 scripts/graph.py tree --session <sid>  # the agent tree, with its ideas
python3 scripts/graph.py recheck               # what became possible again (§2k)
```

### Map + dashboard (optional UI)

```
python3 viz/serve.py                 # http://127.0.0.1:8770
python3 viz/serve.py --no-write      # read-only
```
One page, three sections behind the hamburger menu (`m`): **Map** (live graph),
**Dashboard** (the query views), and **Settings** (timezone, default realm,
database connection, prune). The dashboard also **edits** the brain — create tasks
with auto-recognised links, complete them, change status/assignee/due, rename, and
add/remove relations — all token-free (no model). Set the timezone once in
Settings rather than per command. Add `--host 0.0.0.0` to reach it from your phone
on the LAN / a tunnel; because the write API is **unauthenticated**, pair that with
`--no-write` unless you trust every device on the network.

## 4. Verify

```
/plugin              # second-brain shows as installed + enabled
/hooks               # SessionStart, UserPromptSubmit, Stop, PreToolUse,
                     # PostToolUse, PreCompact, SessionEnd are listed
claude plugin list   # from the CLI
```
Then send any message and check it landed:
```
python3 scripts/graph.py list --type capture --all --limit 5
python3 scripts/graph.py list --type conversation --all --limit 3   # the tree's root
```

Run the traceability tests any time (a throwaway brain in a temp dir — it never
touches `~/.second_brain`):
```
python3 tests/test_traceability.py
```

## No repo access? Install from a zip

`/plugin marketplace add Optibus/memory-ledger` needs the person to be able to read
the GitHub repo. Until that access exists, hand them a zip — Claude Code installs a
plugin from a **directory**, so unzipping *is* the install.

Build one:

```
python3 scripts/bundle.py                  # dist/memory-ledger-<version>.zip
python3 scripts/bundle.py --list           # what would go in
python3 scripts/bundle.py --out ~/Desktop
python3 scripts/bundle.py --include ~/.second_brain/exports/<your-doc>.md
```

It packs **git-tracked files only** — so `.gitignore` decides what is excluded and
no brain data, eval cases or generated deck can ride along — under a single
top-level `second-brain/` directory, and generates two files into the archive:
`INSTALL-FROM-ZIP.md` (the recipient's whole procedure) and `BUNDLE.txt` (version,
commit, file list). Anything untracked is reported as a warning and left out, so
`git add` new files before building.

**`--include` for anything company-specific.** The plugin is agnostic — it holds no
company's rules, runbooks or names. Material like that belongs outside the repo (in
your brain home, which is never committed) and rides along at bundle time:
`--include <path>` puts the file at the archive root and lists it under *attached*,
so the recipient can tell the plugin from what you attached to it. Repeatable.

The recipient runs:

```
unzip memory-ledger-<version>.zip -d ~/dev
```
then, in Claude Code:
```
/plugin marketplace add ~/dev/memory-ledger
/plugin install memory-ledger@memory-ledger
```
`mkdir -p ~/.second_brain && python3 ~/dev/memory-ledger/scripts/graph.py init`, and
they are running. No admin, no marketplace, no repo permission. When repo access
lands they can clone over it and get updates with `git pull`; `~/.second_brain` is
outside the repo, so the brain survives the swap.

The bundle carries no memory of yours. To share **rules** rather than code, export
the ones you want as a Markdown doc kept **outside this repo** (e.g.
`~/.second_brain/exports/<team>-rules.md`) with a paste block of `graph.py add-rule`
calls at the end, then attach it with `--include`. The recipient's agent then pulls
only the rules that match what they are doing (`match-rules`) instead of reading the
whole document.

## Upgrading from second-brain

The plugin id changed in **0.7.0**, so Claude Code treats it as a different
plugin. Nothing migrates automatically, and the old one keeps working until you
remove it, which means both can be installed at once and their skills will
collide. Remove the old one first:

```
/plugin uninstall second-brain@second-brain
/plugin marketplace remove second-brain
```
then install the new one as in step 1.

**What changes for you:** skills and commands are invoked under the new prefix.
`/second-brain:plan-today` becomes `/memory-ledger:plan-today`, and the same for
every other skill. `/ledger` is unchanged.

**What does not change:** your brain. Every node, edge, rule and learned skill
lives in `~/.second_brain/graph.db`, outside the plugin, and is untouched by the
rename. Do not delete that directory, and do not rename it: `$SECOND_BRAIN_HOME`,
`$SECOND_BRAIN_DB` and every path in the docs still point there on purpose.

**If you cloned the repo**, the remote moved too. Repoint it rather than
re-cloning, so your local history and branches survive:

```
git remote set-url origin git@github.com:Optibus/memory-ledger.git
```
GitHub redirects the old URL, so a stale remote keeps working silently and you
will not notice it is wrong. The local directory name does not matter: the plugin
resolves itself from `$CLAUDE_PLUGIN_ROOT`, never from what the folder is called.

## Uninstall

```
/plugin uninstall memory-ledger@memory-ledger
```
Your brain data in `~/.second_brain` is left untouched (delete that dir to remove it).
A local neo4j container is separate: `python3 scripts/neo4j_local.py down --remove`,
and delete `~/.second_brain/neo4j` to drop its copy of the graph.
