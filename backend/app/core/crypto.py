"""Cifrado en reposo de los tokens OAuth (Fernet / AES autenticado).

La clave se deriva de TOKEN_ENCRYPTION_KEY (o, si no está, de SESSION_SECRET) con
SHA-256, así funciona out-of-the-box pero permite una clave dedicada en producción.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def _fernet() -> Fernet:
    raw = (settings.token_encryption_key or settings.session_secret).encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def encrypt(value: str | None) -> str | None:
    if value is None:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        # Tolera valores en texto plano previos al cifrado (dev/migración).
        return value
