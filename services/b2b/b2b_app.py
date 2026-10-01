"""B2B (Brain 2 Brain): an in-memory relay that lets one brain leave a message
for another (services/b2b/README.md, docs/b2b.md).

A user's id **is** their public key: an X25519 public key, 32 bytes, as
unpadded base64url (43 characters). The brain makes the key pair itself and the
private key never leaves it. The relay keeps no accounts: it does not know or
care whether an id ever logged in. In memory, it holds only the retrieval
tokens it handed out (as digests, for an hour) and the sealed messages waiting
to be read.

* `POST /challenge {user_id}` returns a random secret sealed to that id. Only
  the holder of the private key can open it.
* `POST /session {user_id, challenge_id, answer}` checks the opened secret and
  returns a **retrieval token**, valid for one hour. Nothing else about the id
  is kept.
* `POST /messages {from, to, envelope}` leaves a message for `to`. No token: the
  envelope was sealed by the sender, to `to`'s key, and carries proof of who
  sealed it, which only the recipient can check. The relay never sees a message
  in clear and holds nothing that opens one.
* `GET /messages` (bearer) returns every envelope waiting for the caller and
  deletes them from the relay. A message is read once, and an emptied mailbox
  is forgotten.

Everything lives in this process's memory. There is no database: when the
relay stops, every session and message on it is gone, by design.

    python3 services/b2b/serve_b2b.py [--host 127.0.0.1] [--port 8780]
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import Body, FastAPI, Header
from fastapi.responses import JSONResponse

VERSION = "3.0.0"

# Caps that keep one process's memory bounded. Over them is refused, never
# truncated: a message cut short is a different message.
ENVELOPE_MAX = 40_000           # characters of base64 ciphertext: ~16,000 of text, sealed
QUEUE_MAX = 500
MAILBOXES_MAX = 10_000          # recipients with unread messages
CHALLENGES_PER_USER = 5
CHALLENGE_TTL_S = 120
SESSION_TTL_S = 3600
SESSIONS_PER_USER = 20

ID_RE = re.compile(r"[A-Za-z0-9_-]{43}")
MSG_ID_RE = re.compile(r"[0-9a-f]{32}")
CHALLENGE_INFO = b"b2b/v2/challenge"


def now_iso(ts: float | None = None) -> str:
    """A UTC time, ISO 8601 to the second; now when `ts` is not given."""
    return datetime.fromtimestamp(ts if ts is not None else time.time(),
                                  timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- crypto
def b64(raw: bytes) -> str:
    """Unpadded base64url, the form every key and box travels in."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def unb64(text: str) -> bytes:
    """The inverse of `b64`. Raises `ValueError` on anything that is not."""
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, TypeError) as e:
        raise ValueError(str(e)) from None


def public_key(user_id: str) -> X25519PublicKey:
    """The public key a user id is. Raises `ValueError` when it is not one."""
    if not isinstance(user_id, str) or not ID_RE.fullmatch(user_id):
        raise ValueError("not a user id")
    return X25519PublicKey.from_public_bytes(unb64(user_id))


def seal_challenge(user_id: str, challenge_id: str, secret: bytes) -> dict[str, str]:
    """`secret` sealed to the id's key: ephemeral X25519, HKDF-SHA256, AES-GCM,
    bound to the challenge id. Only the private key opens it."""
    pub = public_key(user_id)
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes_raw()
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=CHALLENGE_INFO + eph_pub + pub.public_bytes_raw()).derive(eph.exchange(pub))
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, secret, challenge_id.encode())
    return {"eph": b64(eph_pub), "nonce": b64(nonce), "ct": b64(ct)}


def _digest(value: str) -> bytes:
    """What the relay keeps of a secret it hands out: never the secret."""
    return hashlib.sha256(value.encode()).digest()


# ---------------------------------------------------------------- store
class Unauthorized(Exception):
    """No valid retrieval token, or a challenge answered wrong."""


class Store:
    """Open challenges, retrieval tokens and waiting messages, in memory,
    behind one lock. No accounts: an id exists here only while it holds a
    token or has mail."""

    def __init__(self) -> None:
        """An empty relay."""
        # recipient id -> sealed envelopes waiting for it, oldest first
        self._mailboxes: dict[str, list[dict[str, Any]]] = {}
        # challenge_id -> (user_id, digest of the secret, expires at)
        self._challenges: dict[str, tuple[str, bytes, float]] = {}
        # digest of the token -> (user_id, expires at)
        self._sessions: dict[bytes, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def _sweep(self, now: float) -> None:
        """Forget expired challenges and sessions. Called under the lock."""
        self._challenges = {k: v for k, v in self._challenges.items() if v[2] > now}
        self._sessions = {k: v for k, v in self._sessions.items() if v[1] > now}

    def challenge(self, user_id: str) -> dict[str, Any]:
        """A fresh secret sealed to `user_id`. Raises `ValueError` for an id
        that is not a public key, `OverflowError` when it has too many open."""
        public_key(user_id)
        secret = secrets.token_urlsafe(32)
        cid = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._sweep(now)
            if sum(1 for v in self._challenges.values() if v[0] == user_id) \
                    >= CHALLENGES_PER_USER:
                raise OverflowError("too many open challenges for this id; answer one "
                                    f"or wait {CHALLENGE_TTL_S}s")
            self._challenges[cid] = (user_id, _digest(secret), now + CHALLENGE_TTL_S)
        return {"challenge_id": cid, "challenge": seal_challenge(user_id, cid, secret.encode()),
                "expires_at": now_iso(now + CHALLENGE_TTL_S)}

    def session(self, user_id: str, challenge_id: str, answer: str) -> dict[str, Any]:
        """Check an answered challenge and open a session. The challenge is
        spent either way: a wrong answer cannot be retried against it."""
        now = time.time()
        with self._lock:
            self._sweep(now)
            got = self._challenges.pop(challenge_id, None)
        if got is None or got[0] != user_id \
                or not hmac.compare_digest(got[1], _digest(answer)):
            raise Unauthorized()
        token = secrets.token_urlsafe(32)
        expires = now + SESSION_TTL_S
        with self._lock:
            mine = sorted((v[1], k) for k, v in self._sessions.items() if v[0] == user_id)
            for _, k in mine[:max(0, len(mine) - SESSIONS_PER_USER + 1)]:
                del self._sessions[k]
            self._sessions[_digest(token)] = (user_id, expires)
        return {"token": token, "expires_at": now_iso(expires), "expires_in": SESSION_TTL_S}

    def whoami(self, token: str) -> str:
        """The id a retrieval token belongs to, or `Unauthorized`."""
        now = time.time()
        with self._lock:
            got = self._sessions.get(_digest(token or ""))
        if got is None or got[1] <= now:
            raise Unauthorized()
        return got[0]

    def send(self, sender: str, to: str, envelope: dict[str, Any]) -> dict[str, str]:
        """Leave a sealed envelope for `to`, whether or not `to` ever logged in.
        Raises `ValueError` (a malformed envelope, or `from` not matching it)
        or `OverflowError` (their mailbox, or the relay, is full)."""
        env = _clean_envelope(envelope)
        if env["from"] != sender:
            raise ValueError("from must match envelope.from")
        sent_at = now_iso()
        with self._lock:
            box = self._mailboxes.get(to)
            if box is None:
                if len(self._mailboxes) >= MAILBOXES_MAX:
                    raise OverflowError("the relay is full")
                box = self._mailboxes[to] = []
            if len(box) >= QUEUE_MAX:
                raise OverflowError(to)
            if any(m["id"] == env["id"] for m in box):
                raise ValueError("a message with this id is already waiting")
            box.append({"id": env["id"], "from": sender, "at": sent_at, "envelope": env})
        return {"id": env["id"], "to": to, "sent_at": sent_at}

    def fetch(self, token: str) -> list[dict[str, Any]]:
        """Every envelope waiting for the caller, oldest first, and deleted
        from the relay. Only the ones returned are deleted, so a message that
        arrives during the call waits for the next one."""
        user_id = self.whoami(token)
        with self._lock:
            return self._mailboxes.pop(user_id, [])


def _clean_envelope(env: Any) -> dict[str, str]:
    """The envelope's fields, checked for shape only: the relay cannot open
    it, and does not try."""
    if not isinstance(env, dict):
        raise ValueError("envelope must be an object")
    out: dict[str, str] = {}
    for k in ("v", "id", "from", "eph", "nonce", "ct"):
        v = env.get(k)
        if k == "v":
            if v != 2:
                raise ValueError("envelope.v must be 2")
            continue
        if not isinstance(v, str) or not v:
            raise ValueError(f"envelope.{k} is missing")
        out[k] = v
    if not MSG_ID_RE.fullmatch(out["id"]):
        raise ValueError("envelope.id must be 32 hex characters")
    if not ID_RE.fullmatch(out["from"]) or not ID_RE.fullmatch(out["eph"]):
        raise ValueError("envelope.from and envelope.eph must be public keys")
    if len(out["ct"]) > ENVELOPE_MAX:
        raise OverflowError("envelope is too large")
    try:
        unb64(out["nonce"])
        unb64(out["ct"])
    except ValueError:
        raise ValueError("envelope.nonce and envelope.ct must be base64url") from None
    return {"v": 2, **out}  # type: ignore[dict-item]


# ---------------------------------------------------------------- http
STORE = Store()
app = FastAPI(title="B2B — Brain 2 Brain", version=VERSION,
              description="An in-memory relay for messages between brains. A user id is "
                          "an X25519 public key; a challenge proves the private key and "
                          "opens a one-hour session to read your mail. Anyone can send "
                          "to an id: messages arrive sealed by the sender and are "
                          "deleted once read. No accounts, and nothing is written to disk.")


def _err(status: int, error: str) -> JSONResponse:
    """The refusal envelope every route uses."""
    return JSONResponse({"ok": False, "error": error}, status_code=status)


def _text(value: Any, cap: int = 200) -> str:
    """A trimmed string field, or "" when it is missing or not text."""
    return value.strip()[:cap] if isinstance(value, str) else ""


def _bearer(authorization: str) -> str:
    """The token in an `Authorization: Bearer` header, or ""."""
    scheme, _, tok = (authorization or "").partition(" ")
    return tok.strip() if scheme.lower() == "bearer" else ""


@app.get("/health", summary="Is the relay up")
def health() -> dict[str, Any]:
    """Liveness only: it says nothing about who is registered."""
    return {"ok": True, "service": "b2b", "version": VERSION}


@app.post("/challenge", summary="Get a challenge sealed to your id", response_model=None)
def post_challenge(data: Any = Body(default=None)) -> JSONResponse | dict[str, Any]:
    """`{user_id}`: a secret sealed to that public key, to open with the
    private key and send back to `/session` within two minutes."""
    data = data if isinstance(data, dict) else {}
    user_id = _text(data.get("user_id"))
    if not user_id:
        return _err(400, "missing user_id: your X25519 public key, base64url")
    try:
        return {"ok": True, **STORE.challenge(user_id)}
    except ValueError:
        return _err(400, "user_id is not an X25519 public key (43 base64url characters)")
    except OverflowError as e:
        return _err(429, str(e))


@app.post("/session", summary="Answer a challenge, get a retrieval token",
          response_model=None)
def post_session(data: Any = Body(default=None)) -> JSONResponse | dict[str, Any]:
    """`{user_id, challenge_id, answer}`: the opened secret. Right, and the
    relay returns a retrieval token valid for an hour."""
    data = data if isinstance(data, dict) else {}
    user_id, cid = _text(data.get("user_id")), _text(data.get("challenge_id"))
    answer = _text(data.get("answer"))
    if not (user_id and cid and answer):
        return _err(400, "send user_id, challenge_id and answer")
    try:
        return JSONResponse({"ok": True, "user_id": user_id,
                             **STORE.session(user_id, cid, answer)}, status_code=201)
    except Unauthorized:
        return _err(401, "wrong, expired or already used challenge")


@app.post("/messages", summary="Send a sealed message", response_model=None)
def post_messages(data: Any = Body(default=None)) -> JSONResponse | dict[str, Any]:
    """`{from, to, envelope}`. The envelope is sealed by the sender to `to`'s
    key and proves who sealed it; the relay only checks its shape."""
    data = data if isinstance(data, dict) else {}
    sender, to = _text(data.get("from")), _text(data.get("to"))
    if not ID_RE.fullmatch(sender) or not ID_RE.fullmatch(to):
        return _err(400, "from and to must be user ids (X25519 public keys)")
    try:
        res = STORE.send(sender, to, data.get("envelope"))
    except OverflowError as e:
        if "large" in str(e):
            return _err(413, str(e))
        if "full" in str(e):
            return _err(503, str(e))
        return _err(429, f"{to} has {QUEUE_MAX} unread messages")
    except ValueError as e:
        return _err(400, str(e))
    return JSONResponse({"ok": True, **res}, status_code=202)


@app.get("/messages", summary="Read and delete my messages", response_model=None)
def get_messages(authorization: str = Header(default="")) -> JSONResponse | dict[str, Any]:
    """Every envelope waiting for the caller, oldest first. Each is deleted
    from the relay as it is returned: the caller is now the only copy."""
    tok = _bearer(authorization)
    if not tok:
        return _err(401, "send Authorization: Bearer <retrieval token>")
    try:
        messages = STORE.fetch(tok)
    except Unauthorized:
        return _err(401, "no valid retrieval token: answer a new challenge")
    return {"ok": True, "messages": messages}
