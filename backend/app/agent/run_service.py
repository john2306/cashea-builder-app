"""Ejecución del agente DESACOPLADA del transporte.

En vez de correr el agente dentro de una conexión WebSocket (que se cae en runs largos y
mata el stream), el run corre en una tarea de fondo y publica cada evento a un Redis Stream.
El cliente lo consume por SSE y puede reanudar tras una desconexión usando Last-Event-ID
(replay del stream). El run NO depende del cliente: sigue aunque el navegador se desconecte.

Claves Redis:
  agent:run:{run_id}      -> Redis Stream con los eventos (replay + live).
  agent:conv:{cid}:run    -> run_id activo de la conversación (para reanudar al recargar).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterator

import redis.asyncio as aioredis

from ..core.config import settings
from ..core.db import SessionLocal
from ..core.models import Conversation, Message
from .conversation import build_user_content, ensure_app_project, friendly_error, load_history
from .runner import run_agent

logger = logging.getLogger("cashea.agent.run")

# Tareas en curso por run_id (evita que el GC las recolecte y permite saber si siguen vivas).
_tasks: dict[str, asyncio.Task] = {}

STREAM_TTL = 3600  # segundos que sobrevive el stream tras terminar (para reconexiones tardías)


def _key(run_id: str) -> str:
    return f"agent:run:{run_id}"


def _conv_key(conversation_id: str) -> str:
    return f"agent:conv:{conversation_id}:run"


def _redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def start_run(
    *,
    content: str,
    attachments: list[dict[str, Any]],
    conversation_id: str | None,
    model: str | None,
    user_email: str | None,
) -> dict[str, str]:
    """Crea/asegura conversación+app, persiste el turno del usuario y dispara el run en
    background. Devuelve {run_id, conversation_id, app_id} para que el cliente abra el SSE."""
    run_id = uuid.uuid4().hex
    title_seed = (content or (attachments[0].get("name") if attachments else "Nueva conversación") or "Nueva conversación")

    async with SessionLocal() as session:
        if not conversation_id:
            conv = Conversation(title=title_seed[:60])
            session.add(conv)
            await session.commit()
            conversation_id = conv.id
        else:
            conv = await session.get(Conversation, conversation_id)
            if conv is None:
                conv = Conversation(id=conversation_id, title=title_seed[:60])
                session.add(conv)
                await session.commit()
        app_project = await ensure_app_project(session, conv)
        app_id = app_project.id

        user_content = build_user_content(content, attachments)
        messages = await load_history(session, conversation_id)
        messages.append({"role": "user", "content": user_content})
        session.add(Message(conversation_id=conversation_id, role="user", content=user_content))
        await session.commit()

    r = _redis()
    await r.set(_conv_key(conversation_id), run_id, ex=7200)

    async def emit(event: dict[str, Any]) -> None:
        try:
            await r.xadd(_key(run_id), {"data": json.dumps(event)})
        except Exception:  # noqa: BLE001
            pass

    # Eventos iniciales para que el cliente vea conversación/app aunque abra el SSE tarde.
    await emit({"type": "conversation", "conversation_id": conversation_id})
    await emit({"type": "app", "app_id": app_id})

    task = asyncio.create_task(
        _run(run_id, conversation_id, app_id, messages, model, user_email, r, emit)
    )
    _tasks[run_id] = task
    task.add_done_callback(lambda _t: _tasks.pop(run_id, None))

    return {"run_id": run_id, "conversation_id": conversation_id, "app_id": app_id}


async def _run(run_id, conversation_id, app_id, messages, model, user_email, r, emit) -> None:
    try:
        new_messages = await run_agent(
            messages, emit, model=model, app_id=app_id, user_email=user_email
        )
        async with SessionLocal() as session:
            for msg in new_messages:
                session.add(
                    Message(conversation_id=conversation_id, role=msg["role"], content=msg["content"])
                )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_agent (run %s) falló: %s", run_id, exc)
        await emit({"type": "error", "message": friendly_error(exc)})
    finally:
        # Marcadores terminales: `status idle` (por si run_agent no llegó a emitirlo) y `end`
        # (cierre explícito que usa el SSE para terminar y que el cliente cierre el EventSource).
        await emit({"type": "status", "state": "idle"})
        await emit({"type": "end"})
        try:
            await r.expire(_key(run_id), STREAM_TTL)
        finally:
            await r.aclose()


async def cancel_run(run_id: str) -> bool:
    """Cancela el run en curso y emite los marcadores terminales al stream (para que el cliente
    cierre el SSE y se rompa cualquier loop). Idempotente."""
    task = _tasks.get(run_id)
    # Emitimos los terminales desde acá (cliente Redis propio, no cancelado) por si el `finally`
    # de la tarea no llega a completarse al recibir CancelledError.
    r = _redis()
    try:
        for ev in (
            {"type": "error", "message": "⏹️ Ejecución cancelada por el usuario."},
            {"type": "status", "state": "idle"},
            {"type": "end"},
        ):
            await r.xadd(_key(run_id), {"data": json.dumps(ev)})
        await r.expire(_key(run_id), STREAM_TTL)
    finally:
        await r.aclose()
    if task and not task.done():
        task.cancel()
        return True
    return False


async def get_active_run(conversation_id: str) -> str | None:
    """run_id de la conversación SOLO si la tarea sigue viva en este proceso (para reanudar
    al recargar sin re-renderizar runs ya terminados)."""
    r = _redis()
    try:
        run_id = await r.get(_conv_key(conversation_id))
    finally:
        await r.aclose()
    if run_id and run_id in _tasks and not _tasks[run_id].done():
        return run_id
    return None


async def stream_events(run_id: str, last_id: str) -> AsyncIterator[str]:
    """Generador SSE: replay desde `last_id` (o desde el inicio) + eventos en vivo del stream,
    con heartbeat. Termina al ver el evento `end`."""
    r = _redis()
    key = _key(run_id)
    last = last_id or "0"
    try:
        while True:
            resp = await r.xread({key: last}, block=20000, count=100)
            if not resp:
                yield ": ping\n\n"  # heartbeat (comentario SSE; mantiene viva la conexión)
                continue
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    last = entry_id
                    data = fields.get("data", "{}")
                    yield f"id: {entry_id}\ndata: {data}\n\n"
                    try:
                        if json.loads(data).get("type") == "end":
                            return
                    except Exception:  # noqa: BLE001
                        pass
    finally:
        await r.aclose()
