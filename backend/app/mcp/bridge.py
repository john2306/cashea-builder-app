"""Puente MCP self-hosted -> herramientas nativas del agente.

Los MCP `self_hosted` viven en contenedores locales que la nube de Anthropic NO puede
alcanzar (por eso no van al conector `mcp_servers`). En su lugar, el BACKEND lista sus
tools y se las ofrece al modelo como tools normales; cuando el modelo las llama, el
backend las ejecuta contra el contenedor (`mcp_client.call_tool`) y devuelve el resultado.

Escala a cientos: se recorre el catálogo, se incluyen solo los self-hosted con credenciales
y alcanzables. El nombre se namespacea `<provider>__<tool>` para enrutar de vuelta.
"""
import os
import time
from typing import Any

from sqlalchemy import select

from . import client as mcp_client
from ..core.db import SessionLocal
from .catalog import load_catalog
from ..core.models import McpConnection

# Cache del listado puenteado (evita listar tools de los contenedores en cada mensaje).
_CACHE_TTL = 30.0  # segundos
_cache: tuple[float, list[dict[str, Any]], dict[str, tuple[str, str]]] | None = None


def invalidate() -> None:
    """Limpia el cache (llamar al conectar/desconectar un MCP)."""
    global _cache
    _cache = None


async def _credentials_ready(spec) -> bool:
    if spec.auth == "env":
        return bool(spec.env) and all(os.environ.get(v) for v in spec.env)
    if spec.auth == "oauth":
        # Listo si el usuario ya conectó por OAuth (hay fila con env resuelto).
        async with SessionLocal() as session:
            row = (
                await session.execute(
                    select(McpConnection).where(McpConnection.provider == spec.key)
                )
            ).scalar_one_or_none()
        return bool(row and row.env_json)
    return True


async def bridged_tools() -> tuple[list[dict[str, Any]], dict[str, tuple[str, str]]]:
    """Devuelve (schemas de tools para la API, mapa nombre->(provider, tool original))."""
    global _cache
    if _cache and (time.time() - _cache[0]) < _CACHE_TTL:
        return _cache[1], _cache[2]

    schemas: list[dict[str, Any]] = []
    routes: dict[str, tuple[str, str]] = {}
    for spec in load_catalog().values():
        if spec.transport != "self_hosted" or not await _credentials_ready(spec):
            continue
        try:
            # quick: no bloquear el turno del agente si el contenedor aún no está listo.
            tools = await mcp_client.list_tools(spec.key, quick=True)
        except Exception:  # noqa: BLE001
            continue  # contenedor caído/no listo -> se omite (no rompe el chat)
        for t in tools:
            full = f"{spec.key}__{t['name']}"[:64]
            schemas.append(
                {
                    "name": full,
                    "description": f"[{spec.label}] {t['description']}"[:1024],
                    "input_schema": t["input_schema"],
                }
            )
            routes[full] = (spec.key, t["name"])
    _cache = (time.time(), schemas, routes)
    return schemas, routes
