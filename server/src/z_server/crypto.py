"""Secrets handling: opaque tokens (stored hashed), field encryption (AES-GCM), audit signing (Ed25519),
password hashing (argon2id). Keys are derived from Z_SECRET_KEY with HKDF and a purpose label."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_hasher = PasswordHasher()  # argon2id with library defaults (RFC 9106 recommended profile)


def new_token(prefix: str, nbytes: int = 32) -> str:
    """Opaque bearer token, e.g. `zs_…` (CLI session), `zt_…` (API token), `zw_…` (web session)."""
    return f"{prefix}_{secrets.token_urlsafe(nbytes)}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def derive_key(secret: str, purpose: str, length: int = 32) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=length, salt=b"z-platform-v1", info=purpose.encode())
    return hkdf.derive(secret.encode())


class Sealer:
    """Authenticated encryption for values stored in the database (refresh tokens, TOTP seeds, LLM keys)."""

    def __init__(self, secret: str, purpose: str = "db-fields") -> None:
        self._aead = AESGCM(derive_key(secret, purpose))

    def seal(self, plaintext: str, associated: str = "") -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aead.encrypt(nonce, plaintext.encode(), associated.encode())

    def open(self, blob: bytes, associated: str = "") -> str:
        return self._aead.decrypt(blob[:12], blob[12:], associated.encode()).decode()


class AuditSigner:
    """Signs audit checkpoints. The public key can be published so third parties verify exports."""

    def __init__(self, secret: str) -> None:
        self._key = Ed25519PrivateKey.from_private_bytes(derive_key(secret, "audit-signing"))
        raw = self._key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public_key_b64 = base64.b64encode(raw).decode()
        self.key_id = hashlib.sha256(raw).hexdigest()[:16]

    def sign(self, message: str) -> str:
        return base64.b64encode(self._key.sign(message.encode())).decode()

    def verify(self, message: str, signature: str) -> bool:
        public: Ed25519PublicKey = self._key.public_key()
        try:
            public.verify(base64.b64decode(signature), message.encode())
        except Exception:  # noqa: BLE001 — any failure means "not valid"
            return False
        return True


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge
