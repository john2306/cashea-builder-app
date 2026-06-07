"""MCP server self-hosted de Google Drive (basado en la REST API).

Envuelve `connectors/drive.py` (token del dueño, refresh, CRUD de archivos/carpetas).
"""
from __future__ import annotations

from typing import Any

from ...connectors import drive as drive_api
from ._base import ToolServer, req

PROVIDER = "google_drive"
LABEL = "Google Drive"

_FILE_ID = {"type": "string", "description": "The Drive file or folder ID."}


async def _search(a):
    return {"files": await drive_api.search(a.get("query", ""), int(a.get("page_size", 20)))}


async def _list_folder(a):
    return {"files": await drive_api.list_folder(req(a, "folder_id"), int(a.get("page_size", 50)))}


async def _get_file(a):
    return await drive_api.get_file(req(a, "file_id"))


async def _read_file(a):
    name, content = await drive_api.read_file(req(a, "file_id"))
    return {"name": name, "content": content}


async def _create_folder(a):
    return await drive_api.create_folder(req(a, "name"), a.get("parent"))


async def _create_file(a):
    return await drive_api.create_file(
        req(a, "name"), a.get("content", ""), a.get("mime", "text/plain"), a.get("parent")
    )


async def _update_file(a):
    return await drive_api.update_file(req(a, "file_id"), req(a, "content"), a.get("mime", "text/plain"))


async def _rename(a):
    return await drive_api.rename(req(a, "file_id"), req(a, "name"))


async def _move(a):
    return await drive_api.move(req(a, "file_id"), req(a, "new_parent"))


async def _copy_file(a):
    return await drive_api.copy_file(req(a, "file_id"), a.get("name"))


async def _delete(a):
    return await drive_api.delete(req(a, "file_id"), bool(a.get("permanent", False)))


async def _share(a):
    return await drive_api.share(
        req(a, "file_id"), a.get("email"), a.get("role", "reader"), bool(a.get("anyone", False))
    )


TOOLS: list[dict[str, Any]] = [
    {
        "name": "search",
        "description": "Search the owner's Drive by name fragment. Returns "
        "[{id, name, mimeType, modifiedTime, size}].",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name fragment to match."},
                "page_size": {"type": "integer", "description": "Max results (default 20)."},
            },
        },
        "handler": _search,
    },
    {
        "name": "list_folder",
        "description": "List the children of a folder. Returns [{id, name, mimeType, modifiedTime}].",
        "input_schema": {
            "type": "object",
            "properties": {
                "folder_id": {"type": "string", "description": "The folder ID."},
                "page_size": {"type": "integer", "description": "Max results (default 50)."},
            },
            "required": ["folder_id"],
        },
        "handler": _list_folder,
    },
    {
        "name": "get_file",
        "description": "Get file metadata (id, name, mimeType, size, modifiedTime, parents, link).",
        "input_schema": {
            "type": "object",
            "properties": {"file_id": _FILE_ID},
            "required": ["file_id"],
        },
        "handler": _get_file,
    },
    {
        "name": "read_file",
        "description": "Read a file's text content (Docs/Sheets/Slides are exported to text/CSV). "
        "Returns {name, content}.",
        "input_schema": {
            "type": "object",
            "properties": {"file_id": _FILE_ID},
            "required": ["file_id"],
        },
        "handler": _read_file,
    },
    {
        "name": "create_folder",
        "description": "Create a folder (optionally inside a parent). Returns {id, name, webViewLink}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Folder name."},
                "parent": {"type": "string", "description": "Optional parent folder ID."},
            },
            "required": ["name"],
        },
        "handler": _create_folder,
    },
    {
        "name": "create_file",
        "description": "Create a text file (default text/plain). Returns {id, name, webViewLink}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "File name."},
                "content": {"type": "string", "description": "Text content."},
                "mime": {"type": "string", "description": "MIME type (default text/plain)."},
                "parent": {"type": "string", "description": "Optional parent folder ID."},
            },
            "required": ["name"],
        },
        "handler": _create_file,
    },
    {
        "name": "update_file",
        "description": "Replace the content (media) of an existing file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": _FILE_ID,
                "content": {"type": "string", "description": "New text content."},
                "mime": {"type": "string", "description": "MIME type (default text/plain)."},
            },
            "required": ["file_id", "content"],
        },
        "handler": _update_file,
    },
    {
        "name": "rename",
        "description": "Rename a file or folder.",
        "input_schema": {
            "type": "object",
            "properties": {"file_id": _FILE_ID, "name": {"type": "string", "description": "New name."}},
            "required": ["file_id", "name"],
        },
        "handler": _rename,
    },
    {
        "name": "move",
        "description": "Move a file to another folder.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": _FILE_ID,
                "new_parent": {"type": "string", "description": "Destination folder ID."},
            },
            "required": ["file_id", "new_parent"],
        },
        "handler": _move,
    },
    {
        "name": "copy_file",
        "description": "Copy a file (optionally with a new name).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": _FILE_ID,
                "name": {"type": "string", "description": "Optional name for the copy."},
            },
            "required": ["file_id"],
        },
        "handler": _copy_file,
    },
    {
        "name": "delete",
        "description": "Delete a file. Default moves it to trash; set permanent=true to delete forever.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": _FILE_ID,
                "permanent": {"type": "boolean", "description": "Permanent delete (default false)."},
            },
            "required": ["file_id"],
        },
        "handler": _delete,
    },
    {
        "name": "share",
        "description": "Share a file: with a user email (role reader/writer/commenter) or with "
        "anyone (anyone=true).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": _FILE_ID,
                "email": {"type": "string", "description": "User email to share with."},
                "role": {"type": "string", "description": "reader | writer | commenter (default reader)."},
                "anyone": {"type": "boolean", "description": "Share with anyone with the link."},
            },
            "required": ["file_id"],
        },
        "handler": _share,
    },
]

SERVER = ToolServer(PROVIDER, LABEL, TOOLS, drive_api.NotConnected)

if __name__ == "__main__":  # pragma: no cover
    import asyncio

    asyncio.run(SERVER.serve_stdio())
