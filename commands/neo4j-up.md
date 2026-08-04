---
description: Start the brain's own neo4j in Docker (data in ~/.second_brain/neo4j, restart unless-stopped) and make it the default backend — then backfill the existing brain into it.
argument-hint: "[--switch] [--keep-config] [--no-sync] [--dry-run] [--http-port N]"
---

# /neo4j-up

Bring up a **local neo4j for the brain** and wire the plugin to it. One
token-free script does the whole job — your part is to run it, read its output,
and handle the two cases where it deliberately stops and asks.

What `up` does (idempotent — safe to re-run):

1. store at **`~/.second_brain/neo4j/{data,logs}`** — a bind mount inside the
   brain home, so the graph is a directory you can back up beside `graph.db`;
2. `neo4j:5` with **`--restart unless-stopped`** — survives reboots, stays down
   when deliberately stopped;
3. bound to **127.0.0.1 only** (the HTTP endpoint is cleartext and the brain is
   personal data);
4. password generated once into `~/.second_brain/neo4j.json` (chmod 600) —
   **never printed, never echoed** (see step 4 below);
5. neo4j becomes the **default read backend** (`backend` setting, written to
   `~/.second_brain/settings.json`);
6. the existing brain is **backfilled** into it (`brain_sync.py`) and the outbox
   drained, so the new master is not an empty graph.

## Steps

1. **Show the current state first** — this command can repoint where the brain
   writes, so never run it blind:
   ```
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/neo4j_local.py" status
   ```
   Read it out in two lines: is there a container, and is a master already
   configured (and is it this container or a different host)?

2. **Run it.** Pass through whatever the person asked for (`$ARGUMENTS`):
   ```
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/neo4j_local.py" up $ARGUMENTS
   ```
   Add `--dry-run` first if they want to see the plan. First run pulls ~600 MB
   and neo4j's first boot builds the store — allow a couple of minutes.

3. **Handle the two deliberate stops** (exit code 3, nothing changed):
   - **"already pointed at a neo4j master: `<url>`"** — the brain has a mirror
     somewhere else. **Ask before repointing it**; do not add `--switch`
     yourself. Offer both: `--switch` (repoint; the old `neo4j.json` is backed
     up first) or `--keep-config` (run the container locally, leave the config
     alone).
   - **"container exists but no password on file"** — neo4j bakes the password
     into the store on first boot. Either they supply it (`--password '<pw>'`)
     or they start over by removing the container and the store dir. Read the
     script's message back; it names both commands. Removing the store deletes
     only the neo4j copy — the SQLite brain is untouched and the backfill refills
     it.

4. **Never echo the password.** Do not read `~/.second_brain/neo4j.json`, do not
   `docker inspect` the container's env into the transcript, and do not put a
   `--password` value in a command you print back. Every prompt and answer in
   this session is captured into the graph (CONVENTIONS §0), so a printed
   password becomes a stored password. Say "stored in `neo4j.json`, chmod 600".

5. **Report in the focus contract** — where the store is, that restart is
   `unless-stopped`, node/edge counts pushed, and **one next action**. Useful
   follow-ups:
   - `python3 "$CLAUDE_PLUGIN_ROOT/scripts/graph.py" neo4j-status`
   - the browser at <http://127.0.0.1:7474> (user `neo4j`, password from the
     config file — let them open it themselves)
   - `python3 "$CLAUDE_PLUGIN_ROOT/scripts/brain_query.py" agenda` now reads
     through the `backend` setting (`auto` by default).

## Stopping it

```
python3 "$CLAUDE_PLUGIN_ROOT/scripts/neo4j_local.py" down            # stays stopped
python3 "$CLAUDE_PLUGIN_ROOT/scripts/neo4j_local.py" down --remove   # drop the container, keep the graph
```
`down` leaves the config pointed at it: with `backend: auto` reads fall back to
the SQLite mirror and writes queue in the outbox, draining on the next `up`.
This is the intended pattern — **turn neo4j on for an afternoon of graph
questions and turn it off again** (docs/neo4j.md), because a JVM costs ~850 MiB
resident against 26 MiB for SQLite.

## Notes
- SQLite is always written first and is never behind; neo4j is the master for
  *traversal queries*, not a dependency. Nothing here is required.
- Flags: `--http-port/--bolt-port` (port already taken), `--image`,
  `--heap/--pagecache` (default 512m/256m), `--name`, `--bind` (only widen it
  behind TLS — see docs/neo4j.md "Security"), `--default-backend`.
- The `backend` setting governs `brain_query.py`, the hooks and any cron. The map
  and dashboard always prefer the master when it answers, whatever it says.
