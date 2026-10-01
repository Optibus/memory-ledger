# B2B relay (Brain 2 Brain)

An in-memory relay for messages between brains. Python, FastAPI.

```bash
python3 services/b2b/serve_b2b.py [--host 127.0.0.1] [--port 8780]
```

- `POST /messages` with no credentials registers you and returns a `user_id`
  and a `token` (shown once).
- `POST /messages` with `{user_id, token, to, text}` sends `text` to `to`,
  sealed so only `to` can read it.
- `GET /messages` with the `X-B2B-User-Id` and `X-B2B-Token` headers returns your
  messages and deletes them from the relay.

Nothing is stored on disk: a restart loses every account and message.

The design, the crypto, and how a brain uses it: [docs/b2b.md](../../docs/b2b.md).
