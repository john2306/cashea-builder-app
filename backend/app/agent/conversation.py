"""Helpers compartidos de conversación del builder (usados por el run desacoplado y por
el WS legacy): construcción del turno de usuario (con adjuntos), carga de historial,
alta de la app del proyecto y traducción de errores a mensajes amables."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..core.models import AppProject, Conversation, Message


def friendly_error(exc: Exception) -> str:
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


def build_user_content(text: str, attachments: list[dict[str, Any]]) -> Any:
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
                {"type": "text", "text": f"--- Archivo adjunto: {name} ---\n{att.get('text', '')}"}
            )
        elif kind == "image":
            blocks.append(
                {"type": "image", "source": {
                    "type": "base64", "media_type": att.get("media_type", "image/png"),
                    "data": att.get("data", "")}}
            )
        elif kind == "document":
            blocks.append(
                {"type": "document", "source": {
                    "type": "base64", "media_type": att.get("media_type", "application/pdf"),
                    "data": att.get("data", "")}}
            )
        elif kind == "table":
            import base64

            from ..analysis import datasets

            try:
                raw = base64.b64decode(att.get("data", ""))
                profiles = datasets.ingest(name, raw, att.get("format", "csv"))
                blocks.append(
                    {"type": "text", "text": (
                        f"--- Planilla cargada: {name} ---\n"
                        "Analízala con las tools profile_dataset / analyze_dataset "
                        "(usa el table_id de abajo).\n\n" + "\n\n".join(profiles)
                    )}
                )
            except Exception as exc:  # noqa: BLE001
                blocks.append({"type": "text", "text": f"No se pudo leer la planilla {name}: {exc}"})

    if text:
        blocks.append({"type": "text", "text": text})
    return blocks


async def load_history(session, conversation_id: str) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.seq)
        )
    ).scalars().all()
    return [{"role": m.role, "content": m.content} for m in rows]


async def ensure_app_project(session, conversation: Conversation) -> AppProject:
    row = (
        await session.execute(
            select(AppProject).where(AppProject.conversation_id == conversation.id)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    app_project = AppProject(conversation_id=conversation.id, title=conversation.title or "Nueva app")
    session.add(app_project)
    await session.commit()
    await session.refresh(app_project)
    return app_project
