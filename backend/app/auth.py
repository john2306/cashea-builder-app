"""Sesiones firmadas (JWT HS256) del gateway de login. Solo stdlib para que el mismo
algoritmo pueda copiarse a las apps generadas sin dependencias extra."""
import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def encode_jwt(payload: dict[str, Any], secret: str, ttl_seconds: int = 28800) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl_seconds
    seg = f"{_b64e(json.dumps(header).encode())}.{_b64e(json.dumps(body).encode())}"
    sig = hmac.new(secret.encode(), seg.encode(), hashlib.sha256).digest()
    return f"{seg}.{_b64e(sig)}"


def decode_jwt(token: str, secret: str) -> dict[str, Any] | None:
    try:
        header_seg, payload_seg, sig_seg = token.split(".")
        seg = f"{header_seg}.{payload_seg}"
        expected = _b64e(hmac.new(secret.encode(), seg.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, sig_seg):
            return None
        payload = json.loads(_b64d(payload_seg))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:  # noqa: BLE001
        return None
