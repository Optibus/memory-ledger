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

A conversation is a Slack thread (`slack:<channel>:<thread_ts>`), a Jira issue
(`jira:<KEY>`), or a **whole Slack channel**. Threads and issues are opened by
your own mention and live in `trigger_gate`. Whole channels are a list you keep
by hand, in `trigger_channel`: an open channel runs everyone's mentions in every
thread. Both are in `graph.db`, so they survive restarts.

**The channel list is history, not just state.** Closing a channel keeps its row,
marked with who closed it and when, and reopening it restores that same row. In
the UI it is the **Open channels** list in the Slack panel, folded until you
click it, with **Close** on each row, a field to open one by id or by pasting a
link, and a **Closed** list to reopen from.

```bash
python3 scripts/trigger.py channels                                  # the list
python3 scripts/trigger.py channels --add https://…/archives/C0123ABCD --name ops
python3 scripts/trigger.py channels --remove C0123ABCD
python3 scripts/trigger.py enable  --scope jira:PROJ-42              # a thread or issue
```

## Saying no — two notices

| Notice | When | Sent by |
|---|---|---|
| **not enabled here** (`refusals.not_enabled`) | someone mentions the brain in a conversation you have not opened | the engine, before any session. Once per conversation, so a colleague hammering the bot does not fill the thread. The request is `refused` |
| **against a rule** (`refusals.policy`) | the session reads the request and finds it breaks one of your rules or guardrails | the session, through `trigger.py decline`, which records the rule and reason and prints the notice. `{reason}` is a one-line why, safe to show. The request is `declined` |

**Both go out as you, never as the bot**, and both end with the signature:

- **With a Slack user token** (`xoxp-…`), the engine posts the not-enabled notice
  itself.
- **Without one** (it is optional), it goes out through **your Slack MCP**, in a
  short headless session whose only job is to post that one message. You still
  post it, and it costs a model call.
- **On Jira**, it is a comment made with the Jira token.

**Every notice is in the sender's language.** The message that mentioned the
brain decides, not the channel or the person: someone writing in Hebrew gets
Hebrew, and the same person writing in English the next day gets English.

- With a user token, and on Jira, a small tool-less model (`claude.notice_model`,
  default `haiku`) rewrites the template first.
- Without a user token, the MCP session does it on its way.
- Measured on 2026-09-23 with `haiku`: about 6 s and $0.004 per notice. Hebrew
  and Italian were translated, and an English message left the English template
  alone. Names, @mentions and links stay as written.
- `translate_notices: false` sends the templates exactly as written, and then a
  refusal with a user token costs no model call at all.
- Notice and translation calls run with the brain's capture **off**
  (`MEMORY_LEDGER_OFF=1`). They are plumbing, not conversations. The `respond`
  session itself is captured like any other.
- **The signature is never translated.** It is the loop guard, and a translated
  one would not be recognised.

**Can't reach the channel? The requester gets a DM.** When posting in the thread
fails because you cannot reach that channel (you are not in it, or it is not
found, archived or restricted), the notice goes to the person who mentioned the
brain as a DM instead, with a link to their message. It is logged as
`refusal-notice-dm`. The `respond` skill does the same with its answers
(`reply-dm`). Any other failure, a rate limit say, is logged as a failure rather
than turned into a DM. Jira has no DM, so a comment that cannot be posted is
logged as failed.

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
- **Catch-up reads every channel the bot is in**, not only the open ones. Your
  own mention counts anywhere, and someone else's in a closed conversation still
  has to be logged and refused.

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
tells the session to use the [`respond`](../skills/respond/SKILL.md) skill, to
answer in the language of the message, to DM the requester when it cannot post
in place, and how to decline a request that breaks a rule.
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
| `trigger_request` | one row per mention noticed: `id` (UUID), `source`, `event_id`, `scope`, requester id and name, permalink, text, `received_at`, `status` (`queued` → `running` → `done` / `declined` / `failed` / `timeout` / `interrupted`, or `refused`), exit code, result, cost |
| `trigger_action` | append-only: every action taken for a request — `kind` (`reply`, `reply-dm`, `declined`, `refusal-notice`, `refusal-notice-dm`, `jira-transition`, …), `target`, `detail`, `at` |
| `trigger_channel` | the Slack channels open to everyone, with who opened and closed each and when. Closed ones are kept |
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

**The easy way is the web UI: Settings → Triggers.** It has a master switch, and
one panel each for Slack, Jira and Claude. Each panel has its own on/off switch,
its fields, a **Save** and a **Check** that proves the stored tokens against the
real service without starting anything. Saving restarts the watchers on the new
file, so a switch flipped there takes effect at once. Tokens are write-only: the
page shows whether one is stored, and whether the environment has taken it over,
never what it is. A blank token field keeps the stored one, and **Remove**
deletes it. Behind the page are `POST /api/trigger-config` and
`POST /api/trigger-check` ([the API](api.md)).

The file itself, for editing by hand or scripting:

```json
{
  "enabled": true,
  "signature": "_sent by {owner}'s second brain_",
  "refusals": {
    "not_enabled": "I'm not enabled in this conversation, so I did nothing. {owner} can enable me by mentioning me here.",
    "policy": "I can't do that: it goes against a rule {owner} has set for me, so I did nothing. {reason}"
  },
  "translate_notices": true,
  "claude": {
    "bin": "claude",
    "notice_model": "haiku",
    "mcp_config": "~/.second_brain/trigger-mcp.json",
    "strict_mcp_config": false,
    "timeout_s": 900,
    "max_concurrent": 3,
    "extra_args": []
  },
  "slack": {
    "enabled": true,
    "owner_user_id": "U00000000",
    "thread_lookback_days": 7
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

Secrets are read from the environment first, then from the same file. A token
in the environment of the process running `serve.py` outranks anything saved
from the page, and the page says so:

| Secret | Env var | Key in the file |
|---|---|---|
| Slack app-level token (`xapp-…`, opens the socket) | `MEMORY_LEDGER_SLACK_APP_TOKEN` | `slack.app_token` |
| Slack bot token (`xoxb-…`, reads only) | `MEMORY_LEDGER_SLACK_BOT_TOKEN` | `slack.bot_token` |
| Slack user token (`xoxp-…`, **optional**: posts refusal notices directly; without it they go through your Slack MCP). Needs `chat:write` and `im:write` for the DM fallback | `MEMORY_LEDGER_SLACK_USER_TOKEN` | `slack.user_token` |
| Jira API token | `MEMORY_LEDGER_JIRA_TOKEN` | `jira.token` |

- `signature` — `{owner}` is the brain's `owner` setting. Keep it distinctive: it
  is also the loop guard.
- `refusals` — the two notices; see [Saying no](#saying-no--two-notices).
- `translate_notices` — send them in the sender's language (default on).
- `claude.notice_model` — the model that translates a notice (default `haiku`).
- Open channels are **not** in this file: they are a list in `graph.db`, edited
  from the UI or `trigger.py channels`.
- `jira.owner_account_id` — left empty, it is looked up once (`/myself`).
- `jira.jql` — ANDed onto the search, e.g. `project in (PROJ, OPS)`.
- `claude.mcp_config` — the MCP servers the session may use. Empty means the
  ones Claude Code would load anyway.

Then:

1. Create the Slack app (Socket Mode on, the scopes above, `app_mention` event
   subscription), install it to the workspace — that may need a workspace admin.
2. Invite it to the channels where it should listen.
3. **Check** each service in Settings → Triggers, or
   `python3 scripts/trigger.py check [--only slack|jira|claude]`. It validates
   the file, calls Slack and Jira with the tokens, and runs `claude --version`,
   without starting anything.
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
- A full Requests view in the web UI. Settings → Triggers lists only the newest
  five; `trigger.py requests` and `show` have the rest.
- DMs to the bot (`message.im`) — today only `app_mention` in channels.
