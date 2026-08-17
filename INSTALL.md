# Installing Memory Ledger

Memory Ledger is a Claude Code plugin. Installing it makes its skills, the
`/ledger` command, and the always-on capture hooks available **globally** (every
project, every session). It works in Claude Code and the Claude app.

> **Upgrading from `second-brain`?** As of v0.5.0 the plugin id, the
> marketplace name, and the repo are all **`memory-ledger`** — the old
> `second-brain` id is gone. Existing installs don't migrate automatically:
> run `/plugin uninstall second-brain@second-brain`, remove the old
> marketplace, then follow the install steps below. Your data is untouched —
> it still lives in `~/.second_brain` (see the env-var section for why that
> path didn't change).

## Prerequisites

- **Claude Code** (recent version — plugin marketplaces + `Stop`/`UserPromptSubmit` hooks).
- **Python 3** on your `PATH` (the engine and hooks are stdlib-only — no packages).
- **git** (for the brain's own history and, optionally, installing from GitHub).

Nothing else. The brain has zero third-party dependencies.

## 1. Install the plugin (global)

The plugin ships its own marketplace manifest (`.claude-plugin/marketplace.json`),
so you add the repo as a marketplace, then install from it.

**From GitHub directly** (no manual clone — this is now the normal path):
```
/plugin marketplace add Optibus/memory-ledger
/plugin install memory-ledger@memory-ledger
```
`Optibus/memory-ledger` is a **private** repo, so this shells out to `git
clone` over HTTPS using your own git credentials — Claude Code doesn't
prompt for a login. Make sure `git` can already clone it before you run
`/plugin marketplace add`:
```
gh auth setup-git     # wires gh's token into git's credential helper
```
(or configure any other credential helper that already works for private
Optibus repos). Skip this and the failure you get is an opaque `git clone`
error, not a helpful "auth required" message — if the marketplace add fails,
check git auth first.

Prefer SSH? Use the SSH remote instead:
```
/plugin marketplace add git@github.com:Optibus/memory-ledger.git
/plugin install memory-ledger@memory-ledger
```

**From a local clone** (if you already keep one, or want to hack on it):
```
git clone git@github.com:Optibus/memory-ledger.git ~/dev/memory-ledger
```
then, inside Claude Code:
```
/plugin marketplace add ~/dev/memory-ledger
/plugin install memory-ledger@memory-ledger
```

**Dev mode** (try it for one session without installing):
```
claude --plugin-dir ~/dev/memory-ledger
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

## 2. That's it — no manual init

Older versions required you to `mkdir ~/.second_brain` and run `graph.py
init` by hand before anything worked. That step is gone. A `SessionStart`
hook (`hooks/bootstrap.py`) now runs on every session, before the open-loops
brief, and calls `graph.connect()` — which creates the home dir, the DB file,
and the schema the first time it's missing. It prints nothing, and after that
first run it's a cheap no-op (open a SQLite connection, confirm the schema is
there).

Installing the plugin is enough. Send any message in a fresh session and the
ledger is already capturing it.

The one thing bootstrap deliberately won't do is turn a disabled ledger back
on — see the switch below.

### Optional: set your timezone

Date views (open loops, agenda) bucket by timezone, so set yours once — either
in the UI (**Settings › Time zone**, which writes `~/.second_brain/settings.json`
and is read by the CLI, the hooks, and any cron too), or in your shell rc:
`export MEMORY_LEDGER_TZ="America/Sao_Paulo"`. The env var wins over the file.

### Optional: mirror one brain across machines

**Nothing here is required.** Out of the box the brain is one local SQLite file —
offline, instant, and connected to nothing.

If you want the same brain on more than one machine, point it at a graph server you
control by creating `~/.second_brain/neo4j.json`:

```json
{ "url": "https://<your-graph-host>:<port>", "db": "neo4j", "user": "…", "password": "…" }
```
(or the env vars `MEMORY_LEDGER_NEO4J_URL / _DB / _USER / _PASSWORD`). Writes land in
SQLite immediately and mirror through an offline outbox, so an unreachable mirror
never blocks or loses a write.

Two things worth getting right if you do this:
- **Use TLS.** The credentials and your whole graph cross the network on every write.
- **Keep the config file out of any repo — it holds a secret.** Restrict it with
  `chmod 600`, and never paste the credentials into a command that gets saved (a
  shell history or a tool-permission allowlist counts as saved).

## 3. The on/off switch

Enablement used to just mean "the home dir exists" — deleting `~/.second_brain`
*was* the off switch. Now that bootstrap auto-creates that dir on every
session, its absence can't mean "off" any more (bootstrap would just recreate
it, empty, on the next session start). So being off is now a state you set
explicitly, on purpose, in one of two ways:

- **`/ledger off`** (or `python3 scripts/graph.py disable`) — writes
  `<home>/.disabled`. Machine-wide and persistent: it survives restarts,
  every hook checks for it first, and it stays off until you turn it back on.
  **`/ledger on`** (or `graph.py enable`) removes the marker. Both are
  idempotent.
- **`MEMORY_LEDGER_OFF=1`** — per-process. Scopes to one shell, one CI job, or
  one session, and forces the ledger off for exactly that process regardless
  of the marker file. Note: `graph.py enable` will **warn** (not silently
  succeed) if this is set in your shell, because removing the marker alone
  won't turn capture back on there — you also have to unset the env var
  wherever it's exported (rc file, cron, launchd).

**Deleting `~/.second_brain` still deletes your DATA** — it is not, any more,
how you turn the ledger off. Bootstrap will just recreate an empty home dir
on the next session. Use `/ledger off` if you want the ledger off; only
delete the directory if you actually want to lose what's in it.

## 4. Env vars

`MEMORY_LEDGER_*` is the canonical prefix since v0.5.0. The legacy
`SECOND_BRAIN_*` names all still work as a fallback (checked in that order —
see `_env()` in `scripts/graph.py`) and are not going away, so nothing you
already have exported breaks.

- `$MEMORY_LEDGER_HOME` (was `$SECOND_BRAIN_HOME`) — override the home dir.
- `$MEMORY_LEDGER_DB` (was `$SECOND_BRAIN_DB`) — override the exact DB file.
- `$MEMORY_LEDGER_TZ` (was `$SECOND_BRAIN_TZ`) — timezone for date bucketing;
  the env var wins over `settings.json`.
- `$MEMORY_LEDGER_REALM` (was `$SECOND_BRAIN_REALM`) — default section for new
  nodes (`optibus`, `personal`, ...).

**The home directory itself is still `~/.second_brain`** — that path was
deliberately *not* renamed in this release, so there is no data migration to
do. Don't go looking for `~/.memory_ledger`; it doesn't exist.

## 5. What turns on automatically (no model call)

Once installed, hooks run in **pure Python/SQL — no model call**:

- **SessionStart** → runs `bootstrap.py` (see step 2), then injects a brief of
  your open loops (`open_loops_brief.py`), and **once a day** kicks off
  consolidation in the background: closed days' captures are folded into
  digest notes (originals kept). Detached, so it never delays the session;
  disable with `MEMORY_LEDGER_NO_CONSOLIDATE=1`. It runs here rather than as a
  scheduled cloud routine because the brain is a local file — a cloud routine
  cannot read it. If you want it to run even on days you never open Claude
  Code, add a launchd agent calling `graph.py consolidate --older-than 1`.
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

## 6. Verify

With the manual init step gone, there's no longer an obvious moment that
proves the ledger is wired up — so there's a dedicated health check:

```
/ledger doctor          # or: python3 scripts/doctor.py
```

It reports, in plain language:
- **Status** — enabled or disabled, and if disabled, which switch did it (the
  `.disabled` marker or the `MEMORY_LEDGER_OFF`/`SECOND_BRAIN_OFF` env var).
- **Paths** — the resolved home dir and DB file, and whether each exists.
- **Config** — the effective timezone and realm, and where each came from
  (env var, `settings.json`, or a built-in default).
- **Content** — total node count, a breakdown by type, open-loop count, and
  how stale the newest capture is (a warning if nothing's landed in the last
  2 days, or ever).
- **Hook registration** — every hook in `hooks.json` per event, with its
  matchers, and whether the script file it points at actually exists.
- **Mirror status** — whether a neo4j mirror is configured and how many
  writes are pending in the offline outbox, without ever printing the
  credentials themselves.

Add `--json` for machine-readable output. It exits non-zero only when
something is **actionably** wrong (a missing DB, a hook pointing at a missing
script, unreadable config) — being disabled is a choice, not a fault, and
never affects the exit code by itself.

Complementary, Claude-Code-side checks:
```
/plugin              # memory-ledger shows as installed + enabled
/hooks               # SessionStart, UserPromptSubmit, Stop are listed
claude plugin list   # from the CLI
```

## Uninstall

```
/plugin uninstall memory-ledger@memory-ledger
```
Your brain data in `~/.second_brain` is left untouched (delete that dir to remove it —
but see the on/off switch above if what you actually want is to turn capture off,
not lose your data).
