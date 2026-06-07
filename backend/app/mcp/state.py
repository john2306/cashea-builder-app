"""Habilitación de conectores/MCP por la plataforma (admin, sección Manager).

Default: HABILITADO (sin fila en `connector_state`). Mantenemos en memoria el set DESHABILITADO
(rápido para enforcement sync) y lo refrescamos al inicio y tras cada toggle.
"""
from __future__ import annotations

from sqlalchemy import select

from ..core.db import SessionLocal
from ..core.models import ConnectorState

_disabled: set[str] = set()


def _key(provider: str | None) -> str:
    return (provider or "").replace("-", "_")


async def refresh() -> None:
    global _disabled
    try:
        async with SessionLocal() as s:
            rows = (
                await s.execute(select(ConnectorState).where(ConnectorState.enabled.is_(False)))
            ).scalars().all()
        _disabled = {r.provider for r in rows}
    except Exception:  # noqa: BLE001 — DB no lista: conserva el set actual
        pass


def is_enabled(provider: str | None) -> bool:
    return _key(provider) not in _disabled


def disabled_providers() -> set[str]:
    return set(_disabled)


async def set_enabled(provider: str, enabled: bool) -> None:
    key = _key(provider)
    async with SessionLocal() as s:
        row = await s.get(ConnectorState, key)
        if row is None:
            s.add(ConnectorState(provider=key, enabled=enabled))
        else:
            row.enabled = enabled
        await s.commit()
    await refresh()
