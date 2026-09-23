# Triggers — summoning the brain from Slack and Jira

Mention the brain in a Slack thread or a Jira comment and it answers there, as
you, without anyone opening Claude Code and pasting a link. Everything runs on
your own machine. Nothing is exposed to the internet, and no bot ever speaks.

This page is the design and the reference. Setup is in
[Setting it up](#setting-it-up); the moving parts are in
[How it works](#how-it-works).

## The shape

```
Slack (Socket Mode) ──┐                                 ┌─► your MCPs (Slack, Jira, Gmail, Drive)
  app_mention events  │                                 │   authenticated as YOU
                      ├─► trigger engine ─► claude -p ──┤
Jira (HTTP polling) ──┘   (inside serve.py)  headless,  └─► the `respond` skill:
  comments w/ marker      │                  one per        rules → act → reply → sign
                          │                  mention
                          ▼
                  graph.db: request log, actions, gates, checkpoints
```

Four decisions carry the whole design:

1. **The bot is an ear, never a mouth.** The Slack app has no `chat:write`; the
   Jira poller reads with your token and writes nothing on its own. Whatever gets
   said is said by a headless Claude Code session through **your** MCP servers,
   so it lands under your name, signed so nobody mistakes it for you typing.
2. **There is no trigger MCP.** The watcher only notices the mention and runs
   `claude -p --mcp-config <yours>`. The MCPs you already authenticated do the
   work, so no integration's auth is solved twice.
3. **One mention, one stateless session.** No shared session between mentions:
   each one is its own `claude -p` with its own context and its own audit row.
   Concurrency is a thread pool, capped.
4. **Every mention is logged before anything happens.** A request id (UUID) is
   minted the moment a mention is noticed, and every action taken for it is
   appended under that id. "I never asked for that" has an answer.

## Who may summon it — the gate

A conversation is **closed until you open it**.

| Who mentions | Where | What happens |
|---|---|---|
| you (the owner) | anywhere | runs, and that conversation is **enabled** from then on |
| someone else | an enabled conversation | runs |
| someone else | not enabled | **refused**: nothing runs, and one short notice says so |

A conversation is a Slack thread (`slack:<channel>:<thread_ts>`), a whole Slack
channel (`slack:<channel>`, only by hand), or a Jira issue (`jira:<KEY>`). The
state lives in `graph.db` (`trigger_gate`), so it survives restarts and is the
same for the Slack and the Jira side.

The refusal notice is posted **with your user token directly**, not by a model:
refusing costs no tokens, and it is sent once per conversation rather than once
per mention, so a colleague hammering the bot does not fill the thread. Without a
user token configured the refusal is logged and nothing is posted.

Open or close a conversation by hand:

```bash
python3 scripts/trigger.py enable  --scope slack:C0123ABCD          # the whole channel
python3 scripts/trigger.py disable --scope jira:PROJ-42
```

## Loops

The brain answers as you, and on Jira the mention *is* your own account. So its
own answer would look like a new mention unless something stops it. Two things do:

- **Anything carrying the signature is ignored.** The signature is the one line
  the brain always adds and a person never types.
- **Every event is seen once.** `trigger_request` is unique on
  `(source, event_id)`; a mention delivered live and again by catch-up, or seen by
  two overlapping polls, runs once.

## How it works

### Slack — Socket Mode

A Slack app in **Socket Mode** opens an outbound websocket, so there is no public
endpoint. It subscribes to `app_mention` and listens while the server runs.

- **Scopes (bot):** `app_mentions:read`, `channels:history`, `groups:history`,
  and `channels:read` + `groups:read` for catch-up to list where it is;
  `users:read` is optional and only turns a requester's id into a name. **No
  `chat:write`.** An app-level token with `connections:write` opens the socket.
- **Live:** each `app_mention` is acked, then handed to the engine.
- **Catch-up:** on every (re)connect, for each channel the bot is in (or only
  `slack.channels`), `conversations.history` reaching back
  `thread_lookback_days` (default 7) before the checkpoint, and
  `conversations.replies` for any thread whose latest reply is newer than it.
  Mentions inside threads are the common case, and a reply in a week-old thread
  does not show up in `history` from the checkpoint alone. Messages that do not
  contain `<@bot>` are skipped.
- **The checkpoint** (`trigger_checkpoint`) follows the clock while the socket
  is up, trailing it by two minutes, since then every mention arrives live. It
  only ever moves forward. A first run has no checkpoint and starts from now: it
  does not answer a backlog nobody expected it to.

This half needs `slack_sdk` for the websocket, so it lives in the server
(`scripts/brain_api/trigger_runtime.py`) with the server's other dependencies.
Catch-up and the refusal notice are plain HTTPS and stay in the stdlib engine.

### Jira — polling

There is no Jira bot yet (creating one is heavier; it is the obvious next step).
So the mention is **of you**, plus a **text marker** that says "this one is for
the brain" — e.g. `@Your Name @brain can you move this to review?`.

- Every `poll_seconds` (default 60) the poller searches
  `updated >= -<N>m` (relative, so the Jira profile's timezone cannot shift it),
  narrowed by your own `jql` if you set one, and reads each issue's newest
  comments back to the checkpoint. They are handled oldest first, as they were
  written, so your comment that opens an issue counts before a colleague's reply
  that follows it in the same pass. Only new comments count; editing an old one
  to add the marker does not.
- A comment is a mention when it contains the marker (compared without case or
  accents, so `@cérebro` matches `@Cerebro`) **and** either mentions your account
  or is yours.
- The checkpoint is the time the last successful poll started; the next poll
  reaches back to it plus a margin. Missed a day with the laptop shut? The first
  poll reaches back to the checkpoint, capped at `max_catchup_hours`.

It is plain HTTPS with an API token, so it is stdlib and can also run without the
server: `python3 scripts/trigger.py poll-jira` does one pass (useful from cron).

### The headless session

Each allowed mention runs:

```bash
claude -p "<the request>" --output-format json --dangerously-skip-permissions \
       [--mcp-config=<your config>] [--strict-mcp-config] [extra args]
```

`--dangerously-skip-permissions` is what makes it run end to end with no one at a
prompt. The prompt carries the three things the session cannot work without:

- **the text** of the mention,
- **the permalink** to that exact message or comment — the session reads the
  thread or issue from there with its own MCP, instead of being handed a channel,
- **the source** (`slack` or `jira`), which decides the reply convention: a
  thread reply, or a comment on the issue.

It also carries the request id, in the prompt and as `$MEMORY_LEDGER_REQUEST_ID`,
and tells the session to use the [`respond`](../skills/respond/SKILL.md) skill.
That skill is the contract: read the rules that apply, do what was asked, log
every outward action against the request id, reply in place, end with the
signature.

The dispatcher records the exit code, the final text and the cost on the
request. A session that outlives `timeout_s` is killed and marked `timeout`; a
server restart that orphans one marks it `interrupted` on the next start.

### The audit log

Two tables in `graph.db`, and one node per request:

| Where | What |
|---|---|
| `trigger_request` | one row per mention noticed: `id` (UUID), `source`, `event_id`, `scope`, requester id and name, permalink, text, `received_at`, `status` (`queued` → `running` → `done` / `failed` / `timeout` / `interrupted`, or `refused`), exit code, result, cost |
| `trigger_action` | append-only: every action taken for a request — `kind` (`reply`, `jira-transition`, `slack-message`, …), `target`, `detail`, `at` |
| a `request` node | the same request in the graph, so `recall "who asked me to …"` finds it, and it mirrors to neo4j like any other node |

Refused mentions are logged too. A mention that was never logged never ran.

Read it:

```bash
python3 scripts/trigger.py status               # watchers, config, counts
python3 scripts/trigger.py requests --limit 20
python3 scripts/trigger.py show --id <request-id>
```

or `GET /api/trigger` and `GET /api/trigger-request?id=` on a running server.

## Setting it up

All of it is **per person** and lives in the brain home, never in this repo:
`<home>/trigger.json`, written mode 600 because it may hold tokens.

```json
{
  "enabled": true,
  "signature": "_sent by {owner}'s second brain_",
  "claude": {
    "bin": "claude",
    "mcp_config": "~/.second_brain/trigger-mcp.json",
    "strict_mcp_config": false,
    "timeout_s": 900,
    "max_concurrent": 3,
    "extra_args": []
  },
  "slack": {
    "enabled": true,
    "owner_user_id": "U00000000",
    "channels": []
  },
  "jira": {
    "enabled": true,
    "base_url": "https://your-site.atlassian.net",
    "email": "you@example.com",
    "owner_account_id": "",
    "marker": "@brain",
    "poll_seconds": 60,
    "jql": "",
    "max_catchup_hours": 72
  }
}
```

Secrets are read from the environment first, then from the same file:

| Secret | Env var | Key in the file |
|---|---|---|
| Slack app-level token (`xapp-…`, opens the socket) | `MEMORY_LEDGER_SLACK_APP_TOKEN` | `slack.app_token` |
| Slack bot token (`xoxb-…`, reads only) | `MEMORY_LEDGER_SLACK_BOT_TOKEN` | `slack.bot_token` |
| Slack user token (`xoxp-…`, only for the refusal notice) | `MEMORY_LEDGER_SLACK_USER_TOKEN` | `slack.user_token` |
| Jira API token | `MEMORY_LEDGER_JIRA_TOKEN` | `jira.token` |

- `signature` — `{owner}` is the brain's `owner` setting. Keep it distinctive: it
  is also the loop guard.
- `slack.channels` — limit catch-up to these channel ids; empty means every
  channel the bot is in.
- `jira.owner_account_id` — left empty, it is looked up once (`/myself`).
- `jira.jql` — ANDed onto the search, e.g. `project in (PROJ, OPS)`.
- `claude.mcp_config` — the MCP servers the session may use. Empty means the
  ones Claude Code would load anyway.

Then:

1. Create the Slack app (Socket Mode on, the scopes above, `app_mention` event
   subscription), install it to the workspace — that may need a workspace admin.
2. Invite it to the channels where it should listen.
3. `python3 scripts/trigger.py check` — validates the file, the tokens, and that
   `claude` runs, without starting anything.
4. `python3 scripts/serve.py` — the watchers start with the server.
   `--no-trigger` keeps them off; `--no-write` does too, since a read-only server
   must not be acting on your behalf.

## Risks, and what holds them

- **A mention is untrusted input to a session that skips permissions.** In an
  enabled conversation, anyone there can ask. The gate keeps it to conversations
  you opened, the `respond` skill treats the message as a *request* rather than
  as instructions that outrank the brain's rules, and personal material never
  crosses to an outward surface without your say-so
  (`rule_user_nothing-personal-goes-out-without-explic` in a brain that has it).
  Open channels deliberately, not by habit.
- **It acts while you are away.** That is the point; the audit log is what makes
  it answerable afterwards.
- **Tokens on disk.** Prefer the env vars; the file is mode 600 either way.

## Not done yet

- A dedicated Jira bot user, so a mention does not have to be of you.
- A Requests view in the web UI over `GET /api/trigger`.
- DMs to the bot (`message.im`) — today only `app_mention` in channels.
