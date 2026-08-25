"""Encrypt LLM provider API keys at rest with Fernet (symmetric, authenticated).

Not a substitute for a real secrets manager (Vault/KMS) in a larger
deployment, but a meaningful minimum for a self-contained app: a DB dump or
backup doesn't leak provider keys in plaintext. Key comes from `FERNET_KEY`
(never hard-coded outside the dev default in config.py).
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from backend.config import get_settings

_fernet = Fernet(get_settings().fernet_key.encode())


def encrypt_secret(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str | None:
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return None
