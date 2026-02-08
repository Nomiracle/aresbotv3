"""API key encryption utilities using AES-256-GCM."""
import base64
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_encryption_key: Optional[bytes] = None


def init_encryption(key_hex: str) -> None:
    """Initialize encryption with a hex-encoded 32-byte key."""
    global _encryption_key
    if len(key_hex) != 64:
        raise ValueError("Encryption key must be 64 hex characters (32 bytes)")
    _encryption_key = bytes.fromhex(key_hex)


def encrypt_api_secret(plaintext: str) -> str:
    """Encrypt an API secret using AES-256-GCM.

    Returns a base64-encoded string containing: nonce (12 bytes) + ciphertext
    """
    if _encryption_key is None:
        raise RuntimeError("Encryption not initialized. Call init_encryption first.")

    nonce = os.urandom(12)
    aesgcm = AESGCM(_encryption_key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

    return base64.b64encode(nonce + ciphertext).decode("utf-8")


def decrypt_api_secret(encrypted: str) -> str:
    """Decrypt an API secret.

    Expects a base64-encoded string containing: nonce (12 bytes) + ciphertext
    """
    if _encryption_key is None:
        raise RuntimeError("Encryption not initialized. Call init_encryption first.")

    data = base64.b64decode(encrypted)
    nonce = data[:12]
    ciphertext = data[12:]

    aesgcm = AESGCM(_encryption_key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)

    return plaintext.decode("utf-8")
