"""Cliente MCP directo para el engine y el puente del agente.

Resuelve cómo llegar a cada MCP server según el catálogo:
  - hosted + oauth        -> URL pública + token del usuario (broker, refrescado).
  - self_hosted + env     -> contenedor estático (docker-compose.mcp.yml), URL interna.
  - self_hosted + oauth   -> contenedor POR USUARIO (mcp_pool) con su token inyectado.
Transporte: Streamable HTTP (SDK `mcp`).
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sqlalchemy import select

from . import oauth as mcp_oauth
from ..core.crypto import decrypt, encrypt
from ..core.db import SessionLocal
from .catalog import load_catalog
from .connstore import current_user_sub, get_conn
from ..core.models import McpConnection


async def _valid_token(session, row: McpConnection) -> str:
    """Devuelve un access token válido, refrescándolo si expiró (y persistiéndolo)."""
    token = decrypt(row.access_token)
    now = datetime.now(timezone.utc)
    if row.expires_at and row.expires_at <= now and row.refresh_token:
        tok = await mcp_oauth.refresh(
            row.token_endpoint, decrypt(row.refresh_token), row.client_id, row.resource
        )
        token = tok.get("access_token", token)
        row.access_token = encrypt(token)
        if tok.get("refresh_token"):
            row.refresh_token = encrypt(tok["refresh_token"])
        if tok.get("expires_in"):
            row.expires_at = now + timedelta(seconds=int(tok["expires_in"]))
        await session.commit()
    return token


async def _connection_creds(provider: str) -> tuple[str, dict[str, str]]:
    """(token hosted-oauth, env self_hosted-oauth) de la conexión guardada."""
    async with SessionLocal() as session:
        row = await get_conn(session, provider)
        if row is None:
            raise RuntimeError(f"'{provider}' no está conectado por MCP")
        token = await _valid_token(session, row) if row.access_token else ""
        env = json.loads(decrypt(row.env_json)) if row.env_json else {}
    return token, env


async def _connect_params(
    provider: str, user: str | None = None
) -> tuple[str, dict[str, str], bool]:
    """Devuelve (url, headers, es_pool) para conectar al MCP del provider.
    El contenedor self-hosted se keyea por usuario vigente (aislamiento por-usuario)."""
    pool_user = user or current_user_sub() or "builder"
    spec = load_catalog().get(provider)
    if spec is None:
        raise RuntimeError(f"MCP '{provider}' desconocido")

    if spec.transport == "self_hosted" and spec.auth == "oauth":
        # Contenedor por usuario con el token OAuth inyectado en el entorno.
        from .pool import ensure_server

        _token, env = await _connection_creds(provider)
        if not env:
            raise RuntimeError(f"'{provider}' conectado sin credenciales resueltas")
        url = await ensure_server(spec, env, pool_user)
        return url, {}, True

    if spec.auth == "oauth":  # hosted oauth (Notion): token por usuario en header
        token, _env = await _connection_creds(provider)
        return spec.resolved_url(), {"Authorization": f"Bearer {token}"}, False

    # self_hosted env o hosted sin auth: URL del catálogo, sin header.
    return spec.resolved_url(), {}, False


async def list_tools(
    provider: str, user: str | None = None, quick: bool = False
) -> list[dict[str, Any]]:
    """Lista las herramientas del MCP server (para puentearlas al agente).

    `quick`: pocos reintentos (no bloquear el turno del agente si el pool aún arranca).
    """
    url, headers, is_pool = await _connect_params(provider, user)
    attempts = (3 if quick else 22) if is_pool else 1
    last: Exception | None = None
    for i in range(attempts):
        try:
            async with streamablehttp_client(url, headers=headers or None) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    res = await s.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema or {"type": "object"},
                }
                for t in res.tools
            ]
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i < attempts - 1:
                await asyncio.sleep(4)
    raise last  # type: ignore[misc]


async def call_tool(
    provider: str, tool: str, args: dict[str, Any] | None, user: str | None = None
) -> dict[str, Any]:
    url, headers, is_pool = await _connect_params(provider, user)
    attempts = 22 if is_pool else 1
    last: Exception | None = None
    for i in range(attempts):
        try:
            async with streamablehttp_client(url, headers=headers or None) as (read, write, _):
                async with ClientSession(read, write) as session_mcp:
                    await session_mcp.initialize()
                    result = await session_mcp.call_tool(tool, args or {})
            text = "".join(
                getattr(c, "text", "")
                for c in (result.content or [])
                if getattr(c, "type", None) == "text"
            )
            return {
                "is_error": bool(getattr(result, "isError", False)),
                "text": text[:4000],
                "structured": getattr(result, "structuredContent", None),
            }
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i < attempts - 1:
                await asyncio.sleep(4)
    raise last  # type: ignore[misc]
