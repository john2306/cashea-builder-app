"""Acceso a las conexiones de conector POR USUARIO + identidad vigente.

Una conexión pertenece a un `user_sub` (Google sub). En vez de hilar `user_sub` por cada
función, usamos un **contextvar** con el usuario vigente, que se fija en los pocos puntos de
entrada:
  - Run del agente del Builder → el usuario que chatea.
  - owner-token / connector-proxy / dashboards (apps desplegadas) → el DUEÑO de la app.

Los conectores (`_token`) y el runtime MCP leen `current_user_sub()` sin cambiar sus firmas.
"""
from __future__ import annotations

import contextvars

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.models import McpConnection, User

_current_user_sub: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user_sub", default=None
)


def current_user_sub() -> str | None:
    return _current_user_sub.get()


class use_user:
    """Context manager: fija el `user_sub` vigente dentro del bloque (sincrónico; los awaits
    internos lo ven porque corren en la misma tarea). Reusable para apps (dueño) y agente."""

    def __init__(self, user_sub: str | None) -> None:
        self.user_sub = user_sub

    def __enter__(self) -> "use_user":
        self._token = _current_user_sub.set(self.user_sub)
        return self

    def __exit__(self, *exc) -> None:
        _current_user_sub.reset(self._token)


def set_user(user_sub: str | None) -> None:
    """Fija el usuario vigente sin context manager (para tareas de fondo del agente)."""
    _current_user_sub.set(user_sub)


async def get_conn(
    session: AsyncSession, provider: str, user_sub: str | None = None
) -> McpConnection | None:
    """La conexión `provider` del usuario (arg explícito o el vigente del contextvar), o None.
    Sin usuario → None (aislado: no devuelve conexiones de otros)."""
    sub = user_sub if user_sub is not None else current_user_sub()
    if not sub:
        return None
    return (
        await session.execute(
            select(McpConnection).where(
                McpConnection.provider == provider,
                McpConnection.user_sub == sub,
            )
        )
    ).scalar_one_or_none()


async def sub_for_email(session: AsyncSession, email: str | None) -> str | None:
    """Resuelve el `user_sub` (Google sub) del dueño a partir de su correo (tabla users)."""
    norm = (email or "").strip().lower()
    if not norm:
        return None
    u = await session.get(User, norm)
    return u.sub if u else None
