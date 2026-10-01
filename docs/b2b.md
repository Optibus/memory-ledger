# B2B: Brain 2 Brain

One brain leaves a message for another, and the other brain picks it up on its
own schedule and acts on it, the way it acts on a Slack mention. Two parts:

- **The relay** (`services/b2b/`): a small FastAPI service that holds messages
  between brains **in memory only**, sealed so only the recipient can read them,
  and deletes each one the moment it is read.
- **The brain's side** (`scripts/b2b.py`): this brain's account on the relay,
  its **peers** (the brains it talks to), sending, and a **poller** that runs
  with the [trigger](trigger.md) watchers and turns each message into a request
  in the same log, so it shows in **Tasks** and runs the action you picked.

```
brain A ── b2b.py send ──► POST /messages ─┐
                                           │  relay (memory only)
                                           │  sealed to B's key
brain B ◄── trigger request ◄── b2b poll ◄─┘  GET /messages (deletes on read)
          (Tasks, claude / http / script)
```

## The relay

```bash
python3 services/b2b/serve_b2b.py                    # 127.0.0.1:8780
python3 services/b2b/serve_b2b.py --host 0.0.0.0 --port 8780   # reachable by others
```

Like `scripts/serve.py`, the launcher is stdlib and builds its own venv on first
run from `services/b2b/requirements.txt` (FastAPI, uvicorn, cryptography), under
`~/.cache/memory-ledger/b2b-<hash>`. `$B2B_VENV` points it at your own venv.
It always runs one worker: the store is the process's memory, and a second
worker would be a second relay behind the same port. OpenAPI is at `/docs`.

### Endpoints

| Call | Body / headers | What it does |
|---|---|---|
| `POST /messages` | none, or `{}` | **registers** a user: `201 {user_id, token}`. The token is shown once; the relay cannot recover it |
| `POST /messages` | `{user_id, token, to, text}` | **sends** `text` to the user `to`: `202 {id, to, sent_at}`. `401` wrong token, `404` no such user, `413` over 16,000 characters, `429` their inbox holds 500 unread |
| `GET /messages` | headers `X-B2B-User-Id`, `X-B2B-Token` | **reads** every message waiting for you, oldest first, as `{id, from, text, sent_at}`, and **deletes them from the relay** |
| `GET /health` | | liveness; says nothing about who is registered |

Half a credential (`user_id` without `token`, or the reverse) is `400`. Every
refusal is `{"ok": false, "error": "..."}`.

### What "only they can read it" means

- Each user gets an X25519 key pair at registration. The private key is kept
  only **wrapped** (AES-GCM) under a key derived with scrypt from the user's
  token. The relay keeps a verifier for the token, never the token itself.
- A message is **sealed** to the recipient's public key (ephemeral X25519,
  HKDF-SHA256, AES-GCM). The sender's id is sealed inside it too, so what sits
  in memory is ciphertext and a timestamp.
- Reading needs the recipient's token: it unwraps their private key for the
  length of the call, opens the messages, and deletes them.

So a dump of the relay's memory reads no message without the recipient's
token. It is not end-to-end against the relay's operator: a relay changed to
log tokens as they arrive could read what follows. Run your own relay, or one
you trust, and put it behind TLS (a reverse proxy) before it leaves localhost.

### What it does not do

- **Persist anything.** A restart loses every account and every unread message.
  A brain whose account is gone gets `401` on its next poll: register again and
  send your peers the new id.
- **Rate-limit.** Registration is open to anyone who can reach it. Keep it on a
  network you control.

## The brain's side

Configuration is `<home>/b2b.json`, written mode 600. The token is write-only
everywhere, like the trigger tokens: `MEMORY_LEDGER_B2B_TOKEN` outranks the file.

```bash
python3 scripts/b2b.py register --server http://relay.example:8780  # new account, stored
python3 scripts/b2b.py config --interval 120 --enable               # poll every 2 minutes
python3 scripts/b2b.py peers --add 4314db08...c749c --name Ana       # connect a brain
python3 scripts/b2b.py send --to Ana --text "Can you review OS-123 today?"
python3 scripts/b2b.py status                                        # account, peers, sent
python3 scripts/b2b.py poll                                          # one pass, by hand
```

Already have an account (on another machine, say)? Set it instead of
registering: `b2b.py config --server URL --user-id ID --token T`. Share your
`user_id` with the brains that should reach you, never the token.

| Key | Default | |
|---|---|---|
| `enabled` | `false` | poll the relay |
| `server_url` | `http://127.0.0.1:8780` | the relay |
| `user_id`, `token` | | this brain's account |
| `poll_seconds` | `300` | 30 to 86,400 |
| `do` | record it as a task or note, answer nothing | the run command for a received message |
| `action`, `http`, `script` | `claude` | what a message runs, as for a [trigger event](trigger.md#actions) |

### Peers, and who gets through

Peers live in `graph.db` (`b2b_peer`). **Only a connected peer's message runs
anything.** A message from anyone else is logged `refused`, with its text, and
goes no further. Disconnecting a peer keeps its row, marked removed, and
reconnecting restores it.

### Receiving

The poller starts with the trigger watchers in `serve.py` when `b2b.json` is
enabled and has an account, **and** the trigger master switch (`trigger.json`
`enabled`) is on: a message from another brain runs a session on your behalf,
so it sits behind the same switch. Saving the B2B config restarts it.

Each pass reads the relay, which deletes what it returned, so each message is
logged as a request at once, `source: b2b`, `scope: b2b:<peer id>`. The request
row is the only copy left anywhere. A peer's message is queued and run like any
trigger, with `{trigger_user}` being the peer's name and these extra variables:

| Variable | |
|---|---|
| `{b2b_peer_id}` | the sender's relay user id |
| `{b2b_peer_name}` | the name you gave them |
| `{b2b_message_id}` | the message's id on the relay |
| `{b2b_sent_at}` | when they sent it |

The headless session gets the message as a request from another person, never
as yours: the brain's rules and guardrails hold, as for any mention (the
`respond` skill). It posts nowhere by default. An answer, when the run command
asks for one, is a B2B message back, which the prompt spells out:

```bash
python3 scripts/b2b.py send --to <peer id> --text "..."
```

Sent from a session, it is also logged as a `b2b-send` action on that request.

### Sending

`b2b.py send` (or `POST /api/b2b-send`) only sends to a connected peer, by id or
by name. Every sent message is kept in `graph.db` (`b2b_sent`).

## The API

| Route | |
|---|---|
| `GET /api/b2b` | the config (never the token), peers, sent messages, received ones by status, and the poller's state |
| `POST /api/b2b-config` | a patch of `b2b.json`; restarts the poller |
| `POST /api/b2b-register` | `{server_url?, force?}`: a new account from the relay |
| `POST /api/b2b-peer` | `{peer_id, name?, enabled}`: connect or disconnect a peer |
| `POST /api/b2b-send` | `{to, text}`: send to a peer |

See [the API](api.md).

## Tests

`python3 tests/test_b2b.py` starts the real relay on a free port and drives it
from the outside (register, send, wrong tokens, read-once), then runs the
brain's side against a throwaway brain with a fake `claude`: peers, send, poll
into the request log, refusals for strangers, and the prompt a session gets.
