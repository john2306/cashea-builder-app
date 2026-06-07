"""Servidores MCP del Cashea Builder Agent (conector MCP de la API Anthropic).

La lista de servers vive en `mcp_catalog.yaml` (única fuente de verdad, escala a cientos).
`active_mcp_servers()` arma SOLO los `transport: hosted` (los que la nube de Anthropic
puede alcanzar), refrescando el token OAuth si expiró. Los `self_hosted` se usan desde el
engine determinístico (ver `mcp_client.py`), no desde el conector del agente.
"""
import os
from datetime import datetime, timezone

from sqlalchemy import func, select

from . import oauth as mcp_oauth
from ..core.crypto import decrypt, encrypt
from ..core.db import SessionLocal
from .catalog import catalog_list, load_catalog
from .connstore import current_user_email
from ..core.models import McpConnection

MCP_BETA = "mcp-client-2025-11-20"

# Compat: registry plano {key: (url, label, token_env)} derivado del catálogo.
# La URL es la efectiva (hosted pública o self_hosted interna).
MCP_REGISTRY: dict[str, tuple[str, str, str]] = {
    s.key: (s.resolved_url(), s.label, s.token_env) for s in catalog_list()
}


def _expiry(token: dict) -> datetime | None:
    from datetime import timedelta

    if token.get("expires_in"):
        return datetime.now(timezone.utc) + timedelta(seconds=int(token["expires_in"]))
    return None


async def active_mcp_servers() -> list[dict[str, str]]:
    """Servers para el CONECTOR del agente: solo `hosted` con token válido."""
    catalog = load_catalog()
    hosted = {k: s for k, s in catalog.items() if s.transport == "hosted"}
    servers: list[dict[str, str]] = []
    seen: set[str] = set()

    email = (current_user_email() or "").strip().lower()
    if not email:
        return []  # sin usuario vigente → ningún conector (aislamiento por-usuario)

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(McpConnection).where(
                    func.lower(McpConnection.user_email) == email
                )
            )
        ).scalars().all()
        now = datetime.now(timezone.utc)
        from . import state as connector_state

        for row in rows:
            spec = hosted.get(row.provider)
            if spec is None:
                continue  # no es hosted -> no va al conector del agente
            if not connector_state.is_enabled(row.provider):
                continue  # deshabilitado por el admin (Manager)
            token = decrypt(row.access_token)
            if row.expires_at and row.expires_at <= now:
                # Token expirado: refresca; si falla, OMITE el server (no rompe el chat).
                if row.refresh_token:
                    try:
                        # Clientes OAuth estándar (Google/BigQuery) requieren client_secret
                        # para refrescar; lo tomamos del provider del broker.
                        client_secret = None
                        if spec.oauth.get("provider_id"):
                            from ..connectors import PROVIDERS

                            prov = PROVIDERS.get(spec.oauth["provider_id"])
                            if prov:
                                client_secret = os.environ.get(prov.client_secret_env)
                        tok = await mcp_oauth.refresh(
                            row.token_endpoint,
                            decrypt(row.refresh_token),
                            row.client_id,
                            row.resource,
                            client_secret,
                        )
                        token = tok.get("access_token", token)
                        row.access_token = encrypt(token)
                        if tok.get("refresh_token"):
                            row.refresh_token = encrypt(tok["refresh_token"])
                        row.expires_at = _expiry(tok)
                        await session.commit()
                    except Exception:  # noqa: BLE001
                        token = None
                else:
                    token = None
            if token:
                servers.append(
                    {"type": "url", "name": row.provider, "url": spec.resolved_url(),
                     "authorization_token": token}
                )
                seen.add(row.provider)

    # Fallback: token pegado a mano por variable de entorno.
    for spec in hosted.values():
        if spec.key in seen or not spec.token_env:
            continue
        token = os.environ.get(spec.token_env)
        if token:
            servers.append(
                {"type": "url", "name": spec.key, "url": spec.resolved_url(),
                 "authorization_token": token}
            )
    return servers
