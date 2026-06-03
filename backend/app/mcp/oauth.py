"""OAuth 2.1 para servidores MCP: discovery de metadata + Dynamic Client Registration
(RFC 7591) + PKCE (S256). Usado por el flujo "Conectar Notion (MCP)".
"""
import base64
import hashlib
import secrets
from urllib.parse import urlparse

import httpx


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def pkce() -> tuple[str, str]:
    verifier = _b64(secrets.token_bytes(32))
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


async def discover(server_url: str) -> dict | None:
    """Descubre los endpoints OAuth del servidor MCP (authorization/token/registration)."""
    parsed = urlparse(server_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as c:
        as_url = origin
        try:
            r = await c.get(f"{origin}/.well-known/oauth-protected-resource")
            if r.status_code == 200:
                servers = r.json().get("authorization_servers") or []
                if servers:
                    as_url = servers[0]
        except Exception:  # noqa: BLE001
            pass
        for base in dict.fromkeys([as_url, origin]):
            for path in (
                "/.well-known/oauth-authorization-server",
                "/.well-known/openid-configuration",
            ):
                try:
                    rr = await c.get(base.rstrip("/") + path)
                    if rr.status_code == 200 and "authorization_endpoint" in rr.json():
                        return rr.json()
                except Exception:  # noqa: BLE001
                    continue
    return None


async def register_client(registration_endpoint: str, redirect_uri: str) -> tuple[str, str | None]:
    body = {
        "client_name": "Cashea Builder",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.post(registration_endpoint, json=body)
    r.raise_for_status()
    data = r.json()
    return data["client_id"], data.get("client_secret")


async def _post_token(token_endpoint: str, data: dict, client_secret: str | None) -> dict:
    auth = None
    if client_secret:
        auth = (data["client_id"], client_secret)
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(token_endpoint, data=data, headers={"Accept": "application/json"}, auth=auth)
    r.raise_for_status()
    return r.json()


async def exchange_code(
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    code_verifier: str,
    resource: str,
    client_secret: str | None = None,
) -> dict:
    return await _post_token(
        token_endpoint,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
            "resource": resource,
        },
        client_secret,
    )


async def refresh(
    token_endpoint: str,
    refresh_token: str,
    client_id: str,
    resource: str,
    client_secret: str | None = None,
) -> dict:
    return await _post_token(
        token_endpoint,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "resource": resource,
        },
        client_secret,
    )
