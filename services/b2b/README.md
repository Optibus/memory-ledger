# B2B relay (Brain 2 Brain)

An in-memory relay for messages between brains. Python, FastAPI.

```bash
python3 services/b2b/serve_b2b.py [--host 127.0.0.1] [--port 8780]
```

A user id is an X25519 public key; the private key stays with its brain.

- `POST /challenge {user_id}` returns a secret sealed to that key.
- `POST /session {user_id, challenge_id, answer}` with the opened secret returns
  a retrieval token, valid for one hour.
- `POST /messages {to, envelope}` (`Authorization: Bearer <token>`) leaves an
  envelope the sender sealed to `to`. The relay cannot open it.
- `GET /messages` (bearer) returns your envelopes and deletes them from the relay.

Nothing is stored on disk: a restart loses every id, session and message.

The design, the crypto, and how a brain uses it: [docs/b2b.md](../../docs/b2b.md).
