# Contributing

The brain is small on purpose: **stdlib only, no dependencies, nothing to
install.** `python3 scripts/graph.py` runs on a clean machine, and so do the
hooks. Please keep that true — a dependency here is a dependency in everyone's
shell startup path.

There are three fenced-off exceptions, and none is on that path.

The web server's API (`scripts/brain_api/`) is a FastAPI app. It earns the
dependency by being the one part of the brain nothing else imports: `serve.py`
stays stdlib-only, builds a venv for the server on first run from
`scripts/requirements-server.txt`, and re-enters it on every start — so
`python3 scripts/serve.py` is still the only command a user runs. See
[the API](docs/api.md#the-servers-environment).

The other is `frontend/`, which is a React app with a build. The bundle is
not in git: `scripts/serve.py` builds it on first start, and rebuilds it when the
source has moved ahead of it, so there is still no install step to perform by
hand — but **npm is required for the UI**. It is not required for the API: with
no node on the machine the server starts, every endpoint answers, and only the
page is missing, with a 503 that says why. See [the frontend](docs/frontend.md).

The third is the B2B relay in `services/b2b/`, a separate service other brains
talk to over HTTP (FastAPI, uvicorn, cryptography). Nothing in `scripts/` or
`hooks/` imports it: `scripts/b2b.py` reaches it with `urllib`. The brain seals
and opens messages itself with `scripts/b2b_crypto.py`, the one engine module
that needs `cryptography` (from `requirements-server.txt`). Its launcher,
`serve_b2b.py`, is stdlib and builds its own venv from
`services/b2b/requirements.txt`, like `serve.py`. See [B2B](docs/b2b.md).

## Ground rules

1. **Standard library only** in `scripts/` and `hooks/`, except
   `scripts/brain_api/`. If you need a third-party package, that is a strong
   signal the feature belongs in a skill (where the model already runs) rather
   than in the engine. `brain_api/` and `frontend/` are the exceptions and are
   fenced off: nothing in either may be imported by the engine, the CLI or a
   hook, and a new server dependency goes in `scripts/requirements-server.txt`,
   pinned.
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
6. **Name the encoding, always.** Everything the brain reads and writes is
   UTF-8, and Python's default is the locale's (cp1252 on most Windows
   machines), so every `open()`, `read_text()` and `write_text()` in text mode
   passes `encoding="utf-8"`, and every subprocess in text mode goes through
   `scripts/_textio.py` (`run_text()`, `popen_text()`) or passes
   `encoding="utf-8", errors="replace"` itself. A child process gets
   `_textio.child_env()`, which sets `PYTHONUTF8=1` and
   `PYTHONIOENCODING=utf-8`. `tests/test_encoding.py` fails on any text-mode
   call without an encoding.

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
| `scripts/_platform.py` | what differs per OS: `resolve_bin()` (PATH, `claude.cmd`), the interpreter for a script by its kind, `kill_tree()` and `pid_alive()` |
| `scripts/_textio.py` | UTF-8 text I/O for subprocesses: `run_text()`, `popen_text()`, `child_env()` |
| `scripts/trigger.py` | the Slack/Jira trigger engine — gate, audit log, checkpoints, Jira poll, `claude -p` dispatch ([triggers](docs/trigger.md)) |
| `scripts/b2b.py` | B2B, this brain's side: its key pair, the brokers, peers (`b2b_peer`, linked to people), send, and the poll that turns each message into a trigger request ([B2B](docs/b2b.md)) |
| `scripts/b2b_crypto.py` | B2B's sealing and opening (X25519, HKDF, AES-GCM); the one engine module that needs `cryptography`, from the server's venv |
| `services/b2b/` | the B2B relay: an in-memory FastAPI service, with its own stdlib launcher and pinned `requirements.txt` |
| `scripts/usage_ingest.py` | parses Claude Code's local session logs into one `usage-week` node per ISO week, token-free and incremental (checkpoint under the brain home); the price table lives here |
| `scripts/usage_report.py` | "Where the Quota Went" — renders those nodes as a deterministic HTML report |
| `scripts/restore.py` | restore a `--with-brain` zip or a `.db` snapshot, checked first, never over a brain without `--replace` |
| `scripts/brain_sync.py` | full push into neo4j, `--verify` against it, `--pull` restore from it |
| `hooks/` | the always-on hooks + `_capture.py` shared helpers; `usage_ingest.py` only forks a throttled, detached `scripts/usage_ingest.py run` |
| `hooks/hooks.json` | which hook binds to which Claude Code event |
| `scripts/serve.py` | the one command: builds the server's venv, the UI, and runs the app |
| `scripts/brain_api/` | the FastAPI app — reads, writes, request models, the SPA fallback, and the trigger watchers (`trigger_runtime.py`, the one user of `slack_sdk`) |
| `scripts/requirements-server.txt` | the server's pinned dependencies, and only the server's |
| `frontend/src/` | the React SPA — map, dashboard, skills, settings |
| `frontend/dist/` | its build output. Not in git; `serve.py` produces it |
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

See [the API guide](docs/api.md#extending-it). A request model in
`scripts/brain_api/models.py`, a route in `reads.py` or `writes.py`, text
length-capped with `clip(value, max_len)`, and the route pinned in
`tests/test_api.py`.

### A skill

`skills/<name>/SKILL.md` with frontmatter (`name`, `description`) — the
description is what the model matches on, so write it as trigger conditions, not
as a summary. Then:

```bash
python3 scripts/graph.py ingest-skills
```

## Testing

Stdlib-only suites, no install step and no runner to learn:

```bash
python3 tests/test_api.py             # the HTTP API, pinned from the outside
python3 tests/test_api_faults.py      # a handler that raises, a client that hangs up
python3 tests/test_contracts.py       # contract shapes, bundles, the web routes
python3 tests/test_traceability.py    # the tree, and hooks that must not block
python3 tests/test_trigger.py         # the trigger engine, against fake Slack/Jira/claude
python3 tests/test_usage_report.py    # the usage report: deterministic, true to its nodes
python3 tests/test_usage_ingest.py    # session logs -> usage-week nodes (synthetic logs), idempotency, hook, end to end
python3 tests/test_agents.py         # the Agents tab: turns, context, connectors, against fake claude/Ollama
python3 tests/test_voice.py          # voice: language, cleaning, the segmenter, narration, the dictation socket (fake transcriber)
python3 tests/test_learned_rules.py   # the learned-rules detector: off by default, proposes, never applies
cd frontend && npm test               # the UI: unit + integration
```

The Python suites build THROWAWAY brains in temp dirs, so a run can never touch
the one in your home. `test_contracts.py` also starts the real
`scripts/serve.py` and drives it over HTTP, checks that a bundle on disk (if
there is one) is coherent and parses (`node --check`, skipped when node is
absent), and that the server decides correctly when to build it. It never runs npm, so the Python
suite stays stdlib-only.

`test_api.py` is the one worth understanding before you add an endpoint. It
never imports the server: it starts `serve.py` against a throwaway brain and
pins every route over a socket — the envelope, the status codes, every refusal
and its wording, the SPA fallback, `--no-write`. It was written against the old
stdlib server and carried across the move to FastAPI unchanged, which is what a
behaviour spec is for. It also holds the route inventory: a route the served
OpenAPI document lists that the suite has never heard of, or the reverse, fails
it. `test_api_faults.py` is the only suite that needs the server in process (to
make a route raise on demand), so it re-execs itself under the server's venv.

The servers these suites start use the same venv `serve.py` builds, so the first
run on a machine needs the network once.

The frontend suites are split by what they cover: unit (components, hooks, and
the pure logic in `src/lib`) and integration (a whole section driven against a
mock server, asserting on the writes that reach the wire). See
[the frontend](docs/frontend.md#testing).

**CI** (`.github/workflows/ci.yml`) runs all of them on every pull request and
every push to `main`, alongside byte-compilation, the plugin and hook manifests,
and `scripts/check-contracts.sh`. It builds the frontend (a type error fails the
run) and checks `frontend/dist` has not been committed by accident — it is a
generated artifact, and a copy in git drifts from its source silently. The OpenAPI document is not committed at all: it is
generated by the running server, so there is no copy to go stale. The §4 docs-ship-with-behavior check runs on pull
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
