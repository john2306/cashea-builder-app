"""Endpoint WebSocket: recibe mensajes en lenguaje natural y transmite la respuesta
del agente (tokens, razonamiento, uso de herramientas y progreso) en tiempo real.

Protocolo (cliente -> servidor):
    {"type": "user_message", "content": "...", "conversation_id": "opcional"}

Protocolo (servidor -> cliente): eventos con "type" entre
    conversation | status | token | thinking | tool_use | tool_progress |
    tool_result | message_done | error
"""
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from .agent.runner import run_agent
from .core.db import SessionLocal
from .core.models import AppProject, Conversation, Message

router = APIRouter()
logger = logging.getLogger("cashea.ws")


def _friendly_error(exc: Exception) -> str:
    """Traduce errores crudos (API/MCP) a un mensaje claro para el chat (sin JSON técnico)."""
    msg = str(exc)
    low = msg.lower()
    if "communicating with mcp server" in low or "error while communicating" in low:
        return (
            "No pude comunicarme con un servidor MCP conectado. Suele pasar si la API del "
            "servicio está deshabilitada en tu proyecto de Google Cloud o si la conexión "
            "expiró. Revisá la sección **Connectors** (reconectá el servicio) y verificá que "
            "su API esté habilitada en GCP; luego volvé a intentar."
        )
    if "mcp_tool_use" in low and "mcp_tool_result" in low:
        return "Se interrumpió una llamada a una herramienta. Reenviá tu mensaje, por favor."
    if "overloaded" in low or "529" in low:
        return "El modelo está sobrecargado por un momento. Probá de nuevo en unos segundos."
    if "rate limit" in low or "429" in low:
        return "Alcanzamos el límite de uso momentáneamente. Esperá unos segundos y reintentá."
    return f"Ocurrió un error procesando tu mensaje. Detalle: {msg[:200]}"


def _build_user_content(text: str, attachments: list[dict[str, Any]]) -> Any:
    """Construye el contenido del turno de usuario.

    Sin adjuntos -> string simple. Con adjuntos -> lista de content blocks de Claude:
    texto inline para archivos de texto/código, bloques image/document (base64) para
    imágenes y PDF. El texto del usuario va al final.
    """
    if not attachments:
        return text

    blocks: list[dict[str, Any]] = []
    for att in attachments:
        kind = att.get("kind")
        name = att.get("name", "archivo")
        if kind == "text":
            blocks.append(
                {
                    "type": "text",
                    "text": f"--- Archivo adjunto: {name} ---\n{att.get('text', '')}",
                }
            )
        elif kind == "image":
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": att.get("media_type", "image/png"),
                        "data": att.get("data", ""),
                    },
                }
            )
        elif kind == "document":
            blocks.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": att.get("media_type", "application/pdf"),
                        "data": att.get("data", ""),
                    },
                }
            )
        elif kind == "table":
            # Planilla (CSV/XLSX): se ingiere a pandas y se inyecta el PERFIL (no los datos
            # crudos). El agente la analiza con profile_dataset/analyze_dataset.
            import base64

            from .analysis import datasets

            try:
                raw = base64.b64decode(att.get("data", ""))
                profiles = datasets.ingest(name, raw, att.get("format", "csv"))
                blocks.append(
                    {
                        "type": "text",
                        "text": (
                            f"--- Planilla cargada: {name} ---\n"
                            "Analízala con las tools profile_dataset / analyze_dataset "
                            "(usa el table_id de abajo).\n\n" + "\n\n".join(profiles)
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                blocks.append(
                    {"type": "text", "text": f"No se pudo leer la planilla {name}: {exc}"}
                )

    if text:
        blocks.append({"type": "text", "text": text})
    return blocks


async def _load_history(session, conversation_id: str) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.seq)  # orden de inserción estable (created_at empata)
        )
    ).scalars().all()
    return [{"role": m.role, "content": m.content} for m in rows]


async def _ensure_app_project(session, conversation: Conversation) -> AppProject:
    row = (
        await session.execute(
            select(AppProject).where(AppProject.conversation_id == conversation.id)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row

    app_project = AppProject(
        conversation_id=conversation.id,
        title=conversation.title or "Nueva app",
    )
    session.add(app_project)
    await session.commit()
    await session.refresh(app_project)
    return app_project


@router.websocket("/ws/agent")
async def agent_socket(websocket: WebSocket) -> None:
    # Sesión por query param (el WS no puede mandar Authorization). Cierra si es inválida.
    from .auth import decode_jwt
    from .core.config import settings

    token = websocket.query_params.get("token", "")
    session = decode_jwt(token, settings.session_secret)
    if not session:
        await websocket.close(code=1008)  # policy violation
        return
    user_email = session.get("email")
    await websocket.accept()

    # Si el cliente se desconecta (recarga/navegación), dejamos de emitir en silencio
    # en vez de crashear el handler ("Cannot call send once a close message has been sent").
    state = {"disconnected": False}

    async def emit(event: dict[str, Any]) -> None:
        if state["disconnected"]:
            return
        try:
            await websocket.send_json(event)
        except Exception:  # noqa: BLE001  (cliente se fue / socket cerrado)
            state["disconnected"] = True

    try:
        while True:
            payload = await websocket.receive_json()
            if payload.get("type") != "user_message":
                continue

            user_text: str = (payload.get("content") or "").strip()
            attachments: list[dict[str, Any]] = payload.get("attachments") or []
            if not user_text and not attachments:
                continue
            conversation_id: str | None = payload.get("conversation_id")
            model: str | None = payload.get("model")

            title_seed = user_text or (
                attachments[0].get("name", "Adjuntos") if attachments else "Nueva conversación"
            )
            user_content = _build_user_content(user_text, attachments)

            async with SessionLocal() as session:
                # Crea la conversación si es nueva.
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

                app_project = await _ensure_app_project(session, conv)
                await emit({"type": "conversation", "conversation_id": conversation_id})
                await emit({"type": "app", "app_id": app_project.id})

                # Historial + nuevo turno del usuario.
                messages = await _load_history(session, conversation_id)
                messages.append({"role": "user", "content": user_content})

                # Persistimos el turno del usuario.
                session.add(
                    Message(
                        conversation_id=conversation_id, role="user", content=user_content
                    )
                )
                await session.commit()

                # Ejecutamos el agente; run_agent transmite eventos por `emit`.
                try:
                    new_messages = await run_agent(
                        messages, emit, model=model, app_id=app_project.id,
                        user_email=user_email,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("run_agent falló: %s", exc)
                    await emit({"type": "error", "message": _friendly_error(exc)})
                    if state["disconnected"]:
                        return
                    continue

                # Persistimos los turnos generados (asistente + tool_results).
                for msg in new_messages:
                    session.add(
                        Message(
                            conversation_id=conversation_id,
                            role=msg["role"],
                            content=msg["content"],
                        )
                    )
                await session.commit()

    except WebSocketDisconnect:
        return
