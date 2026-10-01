"""B2B (Brain 2 Brain): an in-memory relay that lets one brain leave a message
for another (services/b2b/README.md, docs/b2b.md).

Three things happen here, all on `/messages`:

* `POST /messages` with no `user_id` and no `token` registers a new user and
  returns both, once. The relay keeps neither the token nor anything that
  decrypts with it on its own.
* `POST /messages` with `user_id`, `token`, `to` and `text` leaves `text` for
  the user `to`, sealed so only `to` can read it.
* `GET /messages` with the `X-B2B-User-Id` and `X-B2B-Token` headers returns
  every message waiting for that user, decrypted, and deletes them from the
  relay. A message is read once.

Everything lives in this process's memory. There is no database: when the
relay stops, every user and every message waiting on it is gone, by design.

How "only they can read it" holds. Each user gets an X25519 key pair at
registration. The public key is kept as it is. The private key is kept only
wrapped (AES-GCM) under a key derived with scrypt from the user's token, which
the relay never stores. A message is sealed to the recipient's public key with
an ephemeral X25519 exchange, HKDF and AES-GCM, so sending needs nothing of the
recipient's, and opening needs the recipient's token. The sender is sealed
inside the message too: what sits in memory is ciphertext and a timestamp.

    python3 services/b2b/serve_b2b.py [--host 127.0.0.1] [--port 8780]
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from fastapi import Body, FastAPI, Header
from fastapi.responses import JSONResponse

VERSION = "1.0.0"

# Caps that keep one process's memory bounded. Over them is refused, never
# truncated: a message cut short is a different message.
TEXT_MAX = 16_000
QUEUE_MAX = 500
USERS_MAX = 10_000
ID_MAX = 100

# scrypt's cost. Paid on every authenticated call, so it is tuned to tens of
# milliseconds, not to a password's seconds: the token is 256 random bits, and
# the work only has to make a stolen memory dump useless, not a weak guess hard.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
HKDF_INFO = b"b2b/v1/message"
WRAP_AAD = b"b2b/v1/private-key:"


def now_iso() -> str:
    """The current UTC time, ISO 8601 to the second."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- crypto
def derive(token: str, salt: bytes) -> tuple[bytes, bytes]:
    """The key that unwraps a user's private key, and the verifier that proves
    the token without it, both from one scrypt run over the token."""
    out = Scrypt(salt=salt, length=64, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(
        token.encode("utf-8"))
    return out[:32], out[32:]


def seal(public: X25519PublicKey, plaintext: bytes, aad: bytes) -> dict[str, bytes]:
    """Encrypt `plaintext` so only the holder of `public`'s private key opens it."""
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes_raw()
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=HKDF_INFO + eph_pub + public.public_bytes_raw()).derive(eph.exchange(public))
    nonce = os.urandom(12)
    return {"eph": eph_pub, "nonce": nonce, "ct": AESGCM(key).encrypt(nonce, plaintext, aad)}


def open_sealed(private: X25519PrivateKey, box: dict[str, bytes], aad: bytes) -> bytes:
    """The inverse of `seal`. Raises `InvalidTag` when the box is not for this key."""
    eph = X25519PublicKey.from_public_bytes(box["eph"])
    own = private.public_key().public_bytes_raw()
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=HKDF_INFO + box["eph"] + own).derive(private.exchange(eph))
    return AESGCM(key).decrypt(box["nonce"], box["ct"], aad)


# ---------------------------------------------------------------- store
@dataclass
class User:
    """One registered user: what proves their token, their public key, their
    private key wrapped under that token, and the messages waiting for them."""

    user_id: str
    salt: bytes
    verifier: bytes
    public: bytes
    wrapped: bytes
    nonce: bytes
    created_at: str
    inbox: list[dict[str, Any]] = field(default_factory=list)


class Unauthorized(Exception):
    """The user id and token do not match a registered user."""


class Store:
    """Every user and every waiting message, in memory, behind one lock."""

    def __init__(self) -> None:
        """An empty relay."""
        self._users: dict[str, User] = {}
        self._lock = threading.Lock()

    def register(self) -> tuple[str, str]:
        """Create a user. Returns `(user_id, token)`; the token is not kept."""
        token = secrets.token_urlsafe(32)
        salt = os.urandom(16)
        kek, verifier = derive(token, salt)
        private = X25519PrivateKey.generate()
        user_id = uuid.uuid4().hex
        nonce = os.urandom(12)
        wrapped = AESGCM(kek).encrypt(nonce, private.private_bytes_raw(),
                                      WRAP_AAD + user_id.encode())
        user = User(user_id=user_id, salt=salt, verifier=verifier,
                    public=private.public_key().public_bytes_raw(),
                    wrapped=wrapped, nonce=nonce, created_at=now_iso())
        with self._lock:
            if len(self._users) >= USERS_MAX:
                raise OverflowError("the relay is full")
            self._users[user_id] = user
        return user_id, token

    def _auth(self, user_id: str, token: str) -> tuple[User, bytes]:
        """The user and their key-encryption key, or `Unauthorized`. The same
        work is done for an unknown id, so timing does not tell which ids exist."""
        with self._lock:
            user = self._users.get(user_id)
        salt = user.salt if user else b"\0" * 16
        kek, verifier = derive(token, salt)
        if user is None or not hmac.compare_digest(verifier, user.verifier):
            raise Unauthorized()
        return user, kek

    def check(self, user_id: str, token: str) -> None:
        """Raise `Unauthorized` unless the token is this user's."""
        self._auth(user_id, token)

    def exists(self, user_id: str) -> bool:
        """Whether a user id is registered."""
        with self._lock:
            return user_id in self._users

    def send(self, sender: str, token: str, to: str, text: str) -> dict[str, str]:
        """Leave `text` for `to`, from the authenticated `sender`. Raises
        `Unauthorized`, `LookupError` (no such recipient) or `OverflowError`
        (their inbox is full)."""
        self._auth(sender, token)
        with self._lock:
            target = self._users.get(to)
        if target is None:
            raise LookupError(to)
        message_id = uuid.uuid4().hex
        sent_at = now_iso()
        plain = json.dumps({"id": message_id, "from": sender, "text": text,
                            "sent_at": sent_at}).encode("utf-8")
        box = seal(X25519PublicKey.from_public_bytes(target.public), plain,
                   message_id.encode())
        with self._lock:
            if len(target.inbox) >= QUEUE_MAX:
                raise OverflowError(to)
            target.inbox.append({"id": message_id, "at": sent_at, "box": box})
        return {"id": message_id, "to": to, "sent_at": sent_at}

    def fetch(self, user_id: str, token: str) -> list[dict[str, str]]:
        """Every message waiting for the user, opened, oldest first, and
        deleted from the relay. Only the ones actually returned are deleted, so
        a message that arrives during the call waits for the next one."""
        user, kek = self._auth(user_id, token)
        private = X25519PrivateKey.from_private_bytes(
            AESGCM(kek).decrypt(user.nonce, user.wrapped, WRAP_AAD + user_id.encode()))
        with self._lock:
            waiting = list(user.inbox)
        out, done = [], set()
        for item in waiting:
            try:
                msg = json.loads(open_sealed(private, item["box"], item["id"].encode()))
            except (InvalidTag, ValueError):
                # Cannot happen for a box this relay sealed; dropped rather than
                # left to fail on every fetch forever.
                done.add(item["id"])
                continue
            out.append({"id": msg["id"], "from": msg["from"], "text": msg["text"],
                        "sent_at": msg["sent_at"]})
            done.add(item["id"])
        with self._lock:
            user.inbox = [m for m in user.inbox if m["id"] not in done]
        return out


# ---------------------------------------------------------------- http
STORE = Store()
app = FastAPI(title="B2B — Brain 2 Brain", version=VERSION,
              description="An in-memory relay for messages between brains. Nothing is "
                          "written to disk: a restart loses every user and message.")


def _err(status: int, error: str) -> JSONResponse:
    """The refusal envelope every route uses."""
    return JSONResponse({"ok": False, "error": error}, status_code=status)


def _text(value: Any, cap: int = ID_MAX) -> str:
    """A trimmed string field, or "" when it is missing or not text."""
    return value.strip()[:cap] if isinstance(value, str) else ""


@app.get("/health", summary="Is the relay up")
def health() -> dict[str, Any]:
    """Liveness only: it says nothing about who is registered."""
    return {"ok": True, "service": "b2b", "version": VERSION}


@app.post("/messages", summary="Register, or send a message", response_model=None)
def post_messages(data: Any = Body(default=None)) -> JSONResponse | dict[str, Any]:
    """With no `user_id` and no `token`: register, and return both (the token
    is shown this once). With both, plus `to` and `text`: send."""
    data = data if isinstance(data, dict) else {}
    user_id, token = _text(data.get("user_id")), _text(data.get("token"))
    if not user_id and not token:
        try:
            uid, tok = STORE.register()
        except OverflowError as e:
            return _err(503, str(e))
        return JSONResponse({"ok": True, "registered": True, "user_id": uid, "token": tok,
                             "note": "Keep the token: it is shown once and the relay cannot "
                                     "recover it. Messages live in memory only."},
                            status_code=201)
    if not (user_id and token):
        return _err(400, "send both user_id and token, or neither to register")
    to = _text(data.get("to"))
    text = data.get("text")
    if not to:
        return _err(400, "missing to: the user id to send the message to")
    if not isinstance(text, str) or not text.strip():
        return _err(400, "missing text")
    if len(text) > TEXT_MAX:
        return _err(413, f"text is over {TEXT_MAX} characters")
    try:
        res = STORE.send(user_id, token, to, text)
    except Unauthorized:
        return _err(401, "user_id and token do not match")
    except LookupError:
        return _err(404, f"no user {to} on this relay")
    except OverflowError:
        return _err(429, f"{to} has {QUEUE_MAX} unread messages; try later")
    return JSONResponse({"ok": True, **res}, status_code=202)


@app.get("/messages", summary="Read and delete my messages", response_model=None)
def get_messages(x_b2b_user_id: str = Header(default=""),
                 x_b2b_token: str = Header(default="")) -> JSONResponse | dict[str, Any]:
    """Every message waiting for the caller, oldest first. Each is deleted from
    the relay as it is returned: the caller is now the only copy."""
    user_id, token = _text(x_b2b_user_id), _text(x_b2b_token)
    if not (user_id and token):
        return _err(401, "send the X-B2B-User-Id and X-B2B-Token headers")
    try:
        messages = STORE.fetch(user_id, token)
    except Unauthorized:
        return _err(401, "user_id and token do not match")
    return {"ok": True, "messages": messages}
