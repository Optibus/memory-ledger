# Roadmap

What the brain does today is in the [README](../README.md) and the pages next to
this one. This page is where it is going. Each item is already named in the page
that owns it; this is the one place they sit together, so a contributor can pick
one without reading every page. Nothing here is a promise of a date.

## Under way

- **Retire SQLite, neo4j as the only store.** neo4j already holds everything
  SQLite holds, every write reaches it, `sync` and `--pull` feed SQLite from it,
  and `--verify` proves they agree. What is left: the engine's reads and writes
  are SQL (`graph.py`), full-text search is FTS5, and the side tables travel as
  `:MirrorRow` JSON rather than labels of their own. See
  [the road off SQLite](neo4j.md#the-road-off-sqlite).
- **One engine on every OS.** Windows and Linux are in the CI matrix and
  `scripts/setup.py` sets a machine up on any of them. Portability fixes keep
  arriving from people running it on those systems; a path, an encoding or a
  process-tree assumption is a bug here, not a platform limit. See
  [INSTALL.md](../INSTALL.md) and ground rule 6 in [CONTRIBUTING](../CONTRIBUTING.md).
- **Handing a brain over.** `export_handoff_nodes.py` writes the non-personal
  work nodes to a file and `import_nodes.py` brings them into another ledger,
  idempotently and as a dry run first ([INSTALL.md](../INSTALL.md)).
- **Location names.** The brain accepts `~/.second_brain` and
  `~/.memory-ledger` today through a symlink; the engine accepting either
  directly, with no link, is still open.

## Next

- **A Jira bot user**, so a mention does not have to be of you
  ([triggers](trigger.md#not-done-yet)).
- **GitHub webhooks** instead of polling, where a public endpoint exists, and
  team review requests that count for you.
- **Slack: DMs to the bot and catch-up.** Today only `app_mention` in channels
  is heard, and channel events and your DMs are live only.
- **Allowed and blocked lists for Jira and GitHub**; only Slack has them.
- **A full Requests view** in the web UI. Settings lists the newest five;
  `trigger.py requests` and `show` have the rest.
- **Custom triggers beyond v1**: webhooks, branching, loops, parallel blocks,
  languages other than Python in a script block, and switching a failing trigger
  off by itself ([custom triggers](trigger.md#custom-triggers)).
- **B2B rate limiting** on the relay, which today lets anyone who can reach it
  leave mail for any id ([B2B](b2b.md)).

## Deliberately not planned

Considered and left out, with the reason, in
[CONTRIBUTING](../CONTRIBUTING.md#things-deliberately-not-done): vector search
(it would need a model call), a daemon, deleting old data, and cloud sync.

## Proposing something

Open an issue or a pull request that names the item here you are changing, or adds
one. A change that ships behavior ships its docs in the same commit, and that
includes moving an item from this page to the page that now describes it.
