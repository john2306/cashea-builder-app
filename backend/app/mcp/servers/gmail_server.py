"""MCP server self-hosted de Gmail (basado en la REST API).

Envuelve `connectors/gmail.py` (token del dueño, refresh; buscar/leer/enviar/borrador).
Scopes: gmail.readonly + gmail.compose.
"""
from __future__ import annotations

from typing import Any

from ...connectors import gmail as gmail_api
from ._base import ToolServer, req

PROVIDER = "gmail"
LABEL = "Gmail"


async def _search(a):
    return {"messages": await gmail_api.search(a.get("query", ""), int(a.get("max_results", 10)))}


async def _read_message(a):
    return await gmail_api.read_message(req(a, "message_id"))


async def _send(a):
    return await gmail_api.send(req(a, "to"), req(a, "subject"), req(a, "body"))


async def _create_draft(a):
    return await gmail_api.create_draft(req(a, "to"), req(a, "subject"), req(a, "body"))


_COMPOSE = {
    "type": "object",
    "properties": {
        "to": {"type": "string", "description": "Recipient email address."},
        "subject": {"type": "string", "description": "Email subject."},
        "body": {"type": "string", "description": "Plain-text body."},
    },
    "required": ["to", "subject", "body"],
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search",
        "description": "Search messages with a Gmail query (e.g. 'from:bob is:unread'). "
        "Returns [{id, from, subject, date, snippet}].",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query."},
                "max_results": {"type": "integer", "description": "Max messages (default 10)."},
            },
        },
        "handler": _search,
    },
    {
        "name": "read_message",
        "description": "Read a message's headers and plain-text body. "
        "Returns {from, to, subject, date, body}.",
        "input_schema": {
            "type": "object",
            "properties": {"message_id": {"type": "string", "description": "The message ID."}},
            "required": ["message_id"],
        },
        "handler": _read_message,
    },
    {
        "name": "send",
        "description": "Send an email from the owner's account.",
        "input_schema": _COMPOSE,
        "handler": _send,
    },
    {
        "name": "create_draft",
        "description": "Create a draft email (does not send).",
        "input_schema": _COMPOSE,
        "handler": _create_draft,
    },
]

SERVER = ToolServer(PROVIDER, LABEL, TOOLS, gmail_api.NotConnected)

if __name__ == "__main__":  # pragma: no cover
    import asyncio

    asyncio.run(SERVER.serve_stdio())
