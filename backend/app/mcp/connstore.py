"""Acceso a las conexiones de conector POR USUARIO + identidad vigente.

Una conexión pertenece a un usuario, identificado por su **email** (la identidad estable en
todo el sistema: PK de `users`, `owner_email` de las apps, `shared_emails`). En vez de hilar el
email por cada función, usamos un **contextvar** con el email vigente, fijado en los puntos de
entrada:
  - Run del agente del Builder → el email del usuario que chatea.
  - connector-proxy / dashboards (apps desplegadas) → el email del DUEÑO de la app.

Los conectores (`_token`) y el runtime MCP leen `current_user_email()` sin cambiar firmas.
"""
from __future__ import annotations

import contextvars

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.models import McpConnection

_current_user_email: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user_email", default=None
)

# App vigente (la app desplegada que está llamando al connector-proxy). Lo usa el conector
# `postgres` (DB por-app) para resolver el schema/rol de ESA app. Lo fija app_connector_proxy.
_current_app_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_app_id", default=None
)


def current_user_email() -> str | None:
    return _current_user_email.get()


def current_app_id() -> str | None:
    return _current_app_id.get()


class use_app:
    """Context manager: fija el app_id vigente dentro del bloque (para el conector postgres)."""

    def __init__(self, app_id: str | None) -> None:
        self.app_id = (app_id or "").strip() or None

    def __enter__(self) -> "use_app":
        self._token = _current_app_id.set(self.app_id)
        return self

    def __exit__(self, *exc) -> None:
        _current_app_id.reset(self._token)


# Alias retrocompatible (algunos módulos lo importan así); ahora devuelve el email vigente.
def current_user_sub() -> str | None:  # noqa: D401
    return _current_user_email.get()


class use_user:
    """Context manager: fija el email vigente dentro del bloque (sincrónico; los awaits
    internos lo ven porque corren en la misma tarea). Para apps (dueño) y agente."""

    def __init__(self, user_email: str | None) -> None:
        self.user_email = (user_email or "").strip().lower() or None

    def __enter__(self) -> "use_user":
        self._token = _current_user_email.set(self.user_email)
        return self

    def __exit__(self, *exc) -> None:
        _current_user_email.reset(self._token)


def set_user(user_email: str | None) -> None:
    """Fija el usuario vigente (para tareas de fondo del agente)."""
    _current_user_email.set((user_email or "").strip().lower() or None)


async def get_conn(
    session: AsyncSession, provider: str, user_email: str | None = None
) -> McpConnection | None:
    """La conexión `provider` del usuario (arg explícito o el vigente del contextvar), por EMAIL.
    Sin usuario → None (aislado: no devuelve conexiones de otros)."""
    email = (user_email if user_email is not None else current_user_email()) or ""
    email = email.strip().lower()
    if not email:
        return None
    return (
        await session.execute(
            select(McpConnection).where(
                McpConnection.provider == provider,
                func.lower(McpConnection.user_email) == email,
            )
        )
    ).scalars().first()
