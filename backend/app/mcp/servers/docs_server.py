"""MCP server self-hosted de Google Docs (basado en la REST API).

Envuelve `connectors/docs.py` (token del dueño, refresh, crear/leer/editar documentos).
"""
from __future__ import annotations

from typing import Any

from ...connectors import docs as docs_api
from ._base import ToolServer, req

PROVIDER = "google_docs"
LABEL = "Google Docs"

_DOC_ID = {"type": "string", "description": "The document ID (from its URL)."}


async def _create(a):
    return await docs_api.create(req(a, "title"))


async def _read_text(a):
    title, text = await docs_api.read_text(req(a, "document_id"))
    return {"title": title, "text": text}


async def _insert_text(a):
    idx = a.get("index")
    return await docs_api.insert_text(
        req(a, "document_id"), req(a, "text"), int(idx) if idx is not None else None
    )


async def _append_text(a):
    return await docs_api.append_text(req(a, "document_id"), req(a, "text"))


async def _replace_text(a):
    return await docs_api.replace_text(req(a, "document_id"), req(a, "find"), req(a, "replace"))


TOOLS: list[dict[str, Any]] = [
    {
        "name": "create",
        "description": "Create a new document. Returns {documentId, title}.",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string", "description": "Title of the new document."}},
            "required": ["title"],
        },
        "handler": _create,
    },
    {
        "name": "read_text",
        "description": "Read a document's plain text. Returns {title, text}.",
        "input_schema": {
            "type": "object",
            "properties": {"document_id": _DOC_ID},
            "required": ["document_id"],
        },
        "handler": _read_text,
    },
    {
        "name": "insert_text",
        "description": "Insert text at an index (default: end of the document).",
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": _DOC_ID,
                "text": {"type": "string", "description": "Text to insert."},
                "index": {"type": "integer", "description": "1-based insert index (optional)."},
            },
            "required": ["document_id", "text"],
        },
        "handler": _insert_text,
    },
    {
        "name": "append_text",
        "description": "Append text at the end of the document.",
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": _DOC_ID,
                "text": {"type": "string", "description": "Text to append."},
            },
            "required": ["document_id", "text"],
        },
        "handler": _append_text,
    },
    {
        "name": "replace_text",
        "description": "Replace all occurrences of a string (case-sensitive) with another.",
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": _DOC_ID,
                "find": {"type": "string", "description": "Text to find."},
                "replace": {"type": "string", "description": "Replacement text."},
            },
            "required": ["document_id", "find", "replace"],
        },
        "handler": _replace_text,
    },
]

SERVER = ToolServer(PROVIDER, LABEL, TOOLS, docs_api.NotConnected)

if __name__ == "__main__":  # pragma: no cover
    import asyncio

    asyncio.run(SERVER.serve_stdio())
