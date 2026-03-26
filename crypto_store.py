import os
import hashlib
import hmac

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC = b"KTP1"     # versioned header
NONCE_LEN = 12      # AESGCM standard nonce length


def _master_key_bytes() -> bytes:
    """
    Master key from env var. Must be set in deployment.
    Use a long random string (>= 32 chars) at minimum.
    """
    s = os.environ.get("RESULTS_ENC_KEY", "")
    if not s:
        raise RuntimeError("RESULTS_ENC_KEY env var is not set")
    return s.encode("utf-8")


def _user_key(email: str) -> bytes:
    """
    Deterministically derive a per-user 32-byte key from the master key + email.
    """
    mk = _master_key_bytes()
    msg = (email or "").strip().lower().encode("utf-8")
    return hmac.new(mk, msg, hashlib.sha256).digest()  # 32 bytes


def encrypt_for_user(email: str, plaintext: bytes) -> bytes:
    key = _user_key(email)
    nonce = os.urandom(NONCE_LEN)
    aes = AESGCM(key)
    ct = aes.encrypt(nonce, plaintext, associated_data=None)
    return MAGIC + nonce + ct


def decrypt_for_user(email: str, blob: bytes) -> bytes:
    if not blob or len(blob) < len(MAGIC) + NONCE_LEN + 16:
        raise ValueError("Encrypted blob too small")

    if blob[: len(MAGIC)] != MAGIC:
        raise ValueError("Invalid encrypted blob header")

    nonce = blob[len(MAGIC) : len(MAGIC) + NONCE_LEN]
    ct = blob[len(MAGIC) + NONCE_LEN :]

    key = _user_key(email)
    aes = AESGCM(key)
    return aes.decrypt(nonce, ct, associated_data=None)