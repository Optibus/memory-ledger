# Contributing

The brain is small on purpose: **stdlib only, no dependencies, nothing to
install.** `python3 scripts/graph.py` runs on a clean machine, and so does
`python3 scripts/serve.py`. Please keep that true — a dependency here is a
dependency in everyone's shell startup path.

The one exception is `frontend/`, which is a React app with a build. It earns its
place by not costing anyone anything at runtime: the compiled bundle is
**committed**, so a person who installed the plugin and has no node still gets
the UI from `serve.py`. The toolchain is a contributor's problem, never a user's.
See [the frontend](docs/frontend.md).

## Ground rules

1. **Standard library only** in `scripts/` and `hooks/`. If you need a
   third-party package, that is a strong signal the feature belongs in a skill
   (where the model already runs) rather than in the engine. `frontend/` is the
   exception and is fenced off: nothing in it may be needed to RUN the brain.
2. **The hooks must never call a model.** Capture and recall being token-free is
   the property the whole design rests on. Anything that needs judgement goes in
   `skills/`, which the user invokes deliberately.
3. **Never delete user data.** `complete` archives; `del-node` exists but nothing
   should call it automatically. Decay changes *rank*, never existence.
4. **Budget every read that could be large.** Return `chars`, `budget`, `of`,
   `truncated` and a `hint` naming the drill-in command. See
   [queries](docs/queries.md#budgets).
5. **The brain home is private.** `~/.second_brain/` is outside the repo and
   `.gitignore`d. No test, fixture or doc may write real personal data into the
   repo.

## Getting set up

```bash
git clone git@github.com:Optibus/memory-ledger.git ~/dev/memory-ledger
cd ~/dev/memory-ledger
python3 scripts/graph.py stats
```

`connect()` creates the home dir and schema on first use (the same auto-init
`hooks/bootstrap.py` runs on every `SessionStart`), so there is no separate
`init` step to run by hand — any command against a fresh `~/.second_brain`
just works.

Work against a scratch brain so you never corrupt your real one:

```bash
export SECOND_BRAIN_HOME=/tmp/brain-dev
python3 scripts/graph.py stats
```

Every path respects `SECOND_BRAIN_HOME`, so this is fully isolated.

Try it in one session without installing:

```bash
claude --plugin-dir ~/dev/memory-ledger
```

## Layout

| Path | What lives there |
|---|---|
| `scripts/graph.py` | the engine — schema, commands, recall, consolidation (~2.6k lines) |
| `scripts/brain_query.py` | read-only date views, no writes |
| `scripts/brain_sync.py` | one-shot backfill into neo4j |
| `hooks/` | the three always-on hooks + `_capture.py` shared helpers |
| `hooks/hooks.json` | which hook binds to which Claude Code event |
| `scripts/serve.py` | the local HTTP server: the JSON API, and the built UI |
| `scripts/openapi.py` | the API description, served at `/api/docs` and generated into `docs/` |
| `frontend/src/` | the React SPA — map, dashboard, skills, settings |
| `frontend/dist/` | its committed build, so `serve.py` needs no node |
| `skills/<name>/SKILL.md` | model-driven skills (these may spend tokens) |
| `commands/` | `/ledger`, `/plan-today` |
| `CONVENTIONS.md` | the operating rules every skill reads first |
| `docs/` | developer documentation |

## Adding things

### A node type

1. Add it to `NODE_TYPES` in `graph.py`.
2. Decide whether it belongs to a domain area (`AREA_REL` / `part-of`).
3. Update the vocabulary table in `CONVENTIONS.md` §3 — skills read that, not the
   source.
4. `/api/meta` picks it up automatically; the UI does not hardcode types.

### An edge relation

Add it to `EDGE_RELS`. Keep the name a lowercase verb phrase (`blocked-by`,
`relates-to`, `part-of`) — the vocabulary is read by both the model and the UI.

### A command

1. A `cmd_*` function taking `args`, ending in `out({...})`.
2. Register it in the argparse block at the bottom.
3. If it writes, go through the helpers so `_after_write()` runs and the neo4j
   outbox stays correct. Writing raw SQL silently desyncs neo4j.
4. If it can return a lot, budget it.

### An HTTP endpoint

See [the API guide](docs/api.md#extending-it). Validate and length-cap with
`self._str(data, key, max_len)`.

### A skill

`skills/<name>/SKILL.md` with frontmatter (`name`, `description`) — the
description is what the model matches on, so write it as trigger conditions, not
as a summary. Then:

```bash
python3 scripts/graph.py ingest-skills
```

## Testing

Two stdlib-only suites, no install step and no runner to learn:

```bash
python3 tests/test_contracts.py       # contract shapes, bundles, the web routes
python3 tests/test_traceability.py    # the tree, and hooks that must not block
python3 tests/test_openapi.py         # the API doc and the router cannot drift
cd frontend && npm test               # the UI: unit + integration
```

The Python suites build THROWAWAY brains in temp dirs, so a run can never touch
the one in your home. `test_contracts.py` also starts the real
`scripts/serve.py` and drives it over HTTP, checks the committed bundle is
present and parses (`node --check`, skipped when node is absent), and asserts the
running server serves the same OpenAPI document it was built from.

`test_openapi.py` is the one worth understanding before you add an endpoint. It
reads serve.py's ACTUAL routes out of its source — the `path ==` chain and the
`do_POST` dispatch dict — and fails if the spec in `scripts/openapi.py` holds an
endpoint the router does not, or the other way round. A spec beside a router is a
second description of the same surface, and the second description is the one
that rots.

The frontend suites are split by what they cover: unit (components, hooks, and
the pure logic in `src/lib`) and integration (a whole section driven against a
mock server, asserting on the writes that reach the wire). See
[the frontend](docs/frontend.md#testing).

**CI** (`.github/workflows/ci.yml`) runs all of them on every pull request and
every push to `main`, alongside byte-compilation, the plugin and hook manifests,
and `scripts/check-contracts.sh`. It also rebuilds `frontend/dist` and
regenerates `docs/openapi.json`, failing if either moved — both are generated
artifacts that are committed, and a stale one is a lie the repo tells quietly. The §4 docs-ship-with-behavior check runs on pull
requests only, because a direct push may legitimately have taken the
`--no-verify` bypass that check documents.

Beyond the suites, verify by hand against a scratch brain:

```bash
export SECOND_BRAIN_HOME=/tmp/brain-test
python3 scripts/graph.py add-node --type task --title "probe" --status open
python3 scripts/graph.py recall "probe"
python3 scripts/graph.py open-loops
python3 scripts/graph.py stats
```

`python3 scripts/doctor.py` is the quick sanity check — it reports whether the
ledger is enabled, where the DB lives, and whether hooks/settings look wired up
correctly, using the same paths and env vars the engine itself reads.

Check the hooks still emit valid JSON and nothing else — a stray `print` in a hook
corrupts the protocol and breaks every session:

```bash
echo '{"session_id":"t","cwd":"'"$PWD"'","prompt":"probe"}' \
  | python3 hooks/capture_prompt.py | python3 -m json.tool
```

**Contributions that add tests are especially welcome.** The thinnest coverage
is the ranking maths in `_strength` / `_hebbian` and the timezone handling in
`_parse_dt` — both are subtle and both have bitten us. Add a check beside the
existing ones and `main()` will pick it up.

## Performance

If you touch a read path, re-measure. Recipes and current numbers are in
[docs/performance.md](docs/performance.md). Two properties worth preserving:

- **Spreading activation is flat** in graph size (bounded by seeds × degree ×
  depth, with an activation floor). Do not introduce an unbounded sweep.
- **The FTS seed is the expensive part** and scales with matches. Do not widen the
  cue construction without checking the cost.

## Commits and PRs

- Conventional prefixes (`feat:`, `fix:`, `docs:`, `refactor:`).
- Say *why* in the body, not just what — the diff already shows what.
- One logical change per commit.
- Update `CONVENTIONS.md` in the same commit when you change behaviour skills
  depend on. Skills read it at runtime; a stale convention is a live bug.
- `presentation/` is generated and gitignored — rebuild it with
  `presentation/build/*.js`, do not commit 6 MB of HTML.

## Things deliberately not done

Before proposing these, know they were considered:

- **Vector/semantic search** — would mean an embedding model call, breaking the
  token-free property. The lexical + structural combination is the trade.
- **A daemon** — the CLI's 60 ms startup is nearly all interpreter boot. A daemon
  would fix that and add a process to babysit. Not worth it yet.
- **Deleting old data** — decay handles relevance; deletion loses history.
- **Cloud sync** — the local file is a feature. [neo4j](docs/neo4j.md) is the
  supported way to centralise if you need it.
