# B2B: Brain 2 Brain

One brain leaves a message for another, and the other brain picks it up on its
own schedule and acts on it, the way it acts on a Slack mention. Two parts:

- **The relay** (`services/b2b/`): a small FastAPI service that holds messages
  between brains **in memory only**, sealed by the sender so only the recipient
  can read them, and deletes each one the moment it is read.
- **The brain's side** (`scripts/b2b.py`): this brain's key pair (its public key
  is its user id), the **brokers** (relays) it uses, its **peers** (the brains
  it talks to, each linked to a person), sending, and a **poller** that runs
  with the [trigger](trigger.md) watchers and turns each message into a request
  in the same log, so it shows in **Tasks** and runs the action you picked.

```
brain A ── seal to B, sign as A ──► POST /messages ─┐
                                                    │  broker (memory only)
                                                    │  cannot open it
brain B ◄── trigger request ◄── open ◄── b2b poll ◄─┘  GET /messages (deletes on read)
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
A brain calls a relay it uses a **broker**, and can use several.

### Identity: the user id is the public key

Each brain makes an **X25519 key pair** itself. Its **user id is the public
key**, 32 bytes as unpadded base64url (43 characters): it is what others send
to, and it is safe to share. The **private key never leaves the brain**: the
relay never sees it, and holds nothing that opens a message.

The relay keeps in memory only the ids that have proven they hold their key,
the sessions they opened, and the messages waiting for each.

### Logging in: the challenge

```
brain                                   relay
  ── POST /challenge {user_id} ───────────►  seals a random secret to user_id
  ◄── {challenge_id, challenge} ──────────   (ephemeral X25519 + HKDF + AES-GCM)
  opens it with the private key
  ── POST /session {user_id, challenge_id, answer} ─►  compares (constant time)
  ◄── {token, expires_in: 3600} ──────────   the retrieval token, valid 1 hour
```

A challenge lives two minutes and is spent on the first answer, right or wrong.
The relay keeps a hash of each secret and token, never the value. The first
session an id opens registers it: a message can only be sent to an id that has
logged in to that relay at least once. A brain renews its token by itself when
it is near its end, or when the relay no longer knows it (it restarted).

### Endpoints

| Call | Body / headers | What it does |
|---|---|---|
| `POST /challenge` | `{user_id}` | a secret sealed to that public key: `{challenge_id, challenge: {eph, nonce, ct}, expires_at}`. `400` not a public key, `429` five open already |
| `POST /session` | `{user_id, challenge_id, answer}` | `201 {token, expires_at, expires_in}`; `401` wrong, expired or used |
| `POST /messages` | `Authorization: Bearer <token>`, `{to, envelope}` | leaves a sealed envelope for `to`: `202 {id, to, sent_at}`. `400` malformed, or `envelope.from` is not you; `404` `to` never logged in here; `413` too large; `429` their inbox holds 500 unread |
| `GET /messages` | `Authorization: Bearer <token>` | every envelope waiting for you, oldest first, as `{id, from, at, envelope}`, and **deletes them from the relay** |
| `GET /health` | | liveness; says nothing about who is registered |

Every refusal is `{"ok": false, "error": "..."}`.

### The crypto

A message is sealed **by the sender, before it is sent** (`scripts/b2b_crypto.py`):

- an envelope is `{v: 2, id, from, eph, nonce, ct}`: the sender's id, a fresh
  ephemeral public key, and AES-GCM ciphertext of `{text, sent_at}`;
- its key is HKDF-SHA256 over two X25519 exchanges, **ephemeral → recipient**
  (a fresh key per message) and **sender → recipient** (only the holder of
  `from`'s private key could have made it), bound to both ids, the ephemeral key
  and the message id.

So only the recipient opens it, and opening it proves who sent it, without
trusting the relay. The relay checks only that the envelope is well formed and
that `from` is the id the token belongs to. An envelope that does not open is
dropped by the brain and counted `unreadable`, never believed.

What the relay does see: who sends to whom, when, and how long each message is.
Put it behind TLS (a reverse proxy) before it leaves localhost: the retrieval
token travels in a header.

### What it does not do

- **Persist anything.** A restart loses every id, session and unread message.
  Brains log in again by themselves on their next call; a peer who never comes
  back has to log in once before you can send to them again.
- **Rate-limit registration.** Anyone who can reach it can log in with a fresh
  key. Keep it on a network you control.
- **Recover a key.** Lose `b2b.json`'s private key and that id is gone: make a
  new one and send your peers the new id.

## The brain's side

Configuration is `<home>/b2b.json`, written mode 600. The private key is
write-only everywhere, like the trigger tokens:
`MEMORY_LEDGER_B2B_PRIVATE_KEY` outranks the file. The command line needs
`cryptography`; run from a plain `python3` it re-runs itself under the server's
venv (`scripts/requirements-server.txt`).

```bash
python3 scripts/b2b.py identity --generate                          # the key pair; prints your user id
python3 scripts/b2b.py brokers --add http://relay.example:8780 --name Home
python3 scripts/b2b.py brokers --add https://b2b.team.example --name Team
python3 scripts/b2b.py brokers --check home                         # log in now
python3 scripts/b2b.py config --interval 120 --enable               # poll every 2 minutes
python3 scripts/b2b.py peers --add <their user id> --broker team --person "Ana Souza"
python3 scripts/b2b.py allowed --add <their user id> --name Carla    # let a non-peer send to you
python3 scripts/b2b.py allowed --remove <their user id>              # block them again
python3 scripts/b2b.py allowed                                       # who is allowed, and who was
python3 scripts/b2b.py send --to "Ana Souza" --text "Can you review OS-123 today?"
python3 scripts/b2b.py status                                       # identity, brokers, peers, sent
python3 scripts/b2b.py poll                                         # one pass, by hand
```

The same identity on another machine: `b2b.py identity --import <private key>`.
Share your user id with the brains that should reach you, and the broker you
read from; never the private key.

| Key | Default | |
|---|---|---|
| `enabled` | `false` | poll the brokers |
| `private_key` | | this brain's identity; the user id is derived from it |
| `brokers` | `[]` | `[{id, name, url, enabled}]`, up to 20. Every enabled one is polled |
| `poll_seconds` | `300` | 30 to 86,400 |
| `do` | record it as a task or note, answer nothing | the run command for a received message |
| `action`, `http`, `script` | `claude` | what a message runs, as for a [trigger event](trigger.md#actions) |

The settings page (**Settings → B2B**) does all of this: identity, the broker
list, peers with the person each is, and a send box.

### Peers, and who gets through

Peers live in `graph.db` (`b2b_peer`): a user id, the **broker** that reaches
them (needed when there is more than one), a name, and optionally the
**person** node they are, by id or by a name that resolves to one person; with
no name, the person's is used. `send --to` takes a peer's id, name, or person.
Disconnecting a peer keeps its row, marked removed, and reconnecting restores
it.

**The allowed list** (`b2b_allowed`) says who may send to you, like friends on
a social network. **Only an allowed sender's message runs anything.** A message
from anyone else is logged **`blocked`**, with its text and the reason `b2b:
not on the allowed list`, and goes no further. It shows in Tasks, where
**Reprocess** runs it after all, once you confirm (see
[reprocessing](trigger.md#reprocessing-a-task)).

- Adding a peer also allows it (`peers --add … --no-allow`, or `allow: false`
  on the API, to skip that). Reconnecting one allows it again.
- The two lists are otherwise independent: you can allow a brain you never
  send to, and block a peer you still send to. Disconnecting a peer leaves it
  allowed; block it to stop its messages.
- Blocking keeps the row, marked removed, and allowing it again restores it.
- The list lives in this brain only. The relay still delivers anything sealed
  to you from a brain that has logged in to it; the list decides what runs.
- The first time the list is created, every connected peer goes on it, so
  peers that could send before still can.

### Receiving

The poller starts with the trigger watchers in `serve.py` when `b2b.json` is
enabled and has an account, **and** the trigger master switch (`trigger.json`
`enabled`) is on: a message from another brain runs a session on your behalf,
so it sits behind the same switch. Saving the B2B config restarts it.

Each pass reads every enabled broker, which deletes what it returned, opens
each envelope, and logs each message as a request at once, `source: b2b`,
`scope: b2b:<peer id>`. A broker that fails is reported in `errors` and does not
stop the others. The request
row is the only copy left anywhere. A peer's message is queued and run like any
trigger, with `{trigger_user}` being the peer's name and these extra variables:

| Variable | |
|---|---|
| `{b2b_peer_id}` | the sender's user id (their public key) |
| `{b2b_peer_name}` | the name you gave them |
| `{b2b_person_id}` | the person node they are linked to, if any |
| `{b2b_broker}` | the broker it came through |
| `{b2b_message_id}` | the message's id |
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

`b2b.py send` (or `POST /api/b2b-send`) only sends to a connected peer, by id,
name or person, through that peer's broker. Every sent message is kept in
`graph.db` (`b2b_sent`).

## The API

| Route | |
|---|---|
| `GET /api/b2b` | the config (the user id, never the private key), brokers with their session state, peers, the allowed list, sent messages, received ones by status, and the poller's state |
| `POST /api/b2b-config` | a patch of `b2b.json`; restarts the poller |
| `POST /api/b2b-identity` | `{force?}`: a new key pair |
| `POST /api/b2b-broker` | `{url, name?}` adds; `{id, url?, name?, enabled?}` changes; `{id, remove: true}` drops |
| `POST /api/b2b-broker-check` | `{id}`: log in to it now |
| `POST /api/b2b-peer` | `{peer_id, name?, broker_id?, person_id?, enabled, allow?}`: connect or disconnect a peer; connecting also allows it unless `allow` is false |
| `POST /api/b2b-allowed` | `{user_id, name?, enabled}`: allow a sender, or block it again |
| `POST /api/trigger-reprocess` | `{request_id}`: run a `blocked` (or failed) message again |
| `POST /api/b2b-send` | `{to, text}`: send to a peer |

See [the API](api.md).

## Tests

`python3 tests/test_b2b.py` checks the crypto (only the recipient opens, a
forged sender does not), starts two real relays on free ports and drives one
from the outside (challenge, wrong and spent answers, session, send, read-once),
then runs the brain's side against a throwaway brain with a fake `claude`:
identity, two brokers, peers linked to a person, send, polling both brokers into
the request log, refusals for strangers, tampered envelopes, re-login after a
stale token, a broker that is down, and the prompt a session gets.
