"""Bitácora de eventos (auditoría) para la sección Logs del rol admin.

`log_event(...)` inserta un registro en `event_logs`. Es BEST-EFFORT: si falla (DB caída,
etc.) no rompe el flujo principal — solo loguea un warning. Se usa tanto desde la API
(backend) como desde el worker de Celery (deploy).
"""
from __future__ import annotations

import logging
from typing import Any

from .db import SessionLocal
from .models import EventLog

logger = logging.getLogger("cashea.events")


async def log_event(
    event_type: str,
    *,
    status: str = "ok",
    user_email: str | None = None,
    app_id: str | None = None,
    provider: str | None = None,
    message: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Registra un evento en la bitácora (auditoría). Nunca propaga errores."""
    try:
        async with SessionLocal() as session:
            session.add(
                EventLog(
                    event_type=event_type,
                    status=status,
                    user_email=user_email,
                    app_id=app_id,
                    provider=provider,
                    message=(message or None),
                    meta=meta,
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("no se pudo registrar el evento %s", event_type)
