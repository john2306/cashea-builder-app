"""MCP server self-hosted de Google Sheets (basado en la REST API).

Expone las operaciones de Sheets como tools MCP reutilizando `connectors/sheets.py` (token del
dueño, refresh, CRUD). Ver `_base.ToolServer` para el contrato in-process / standalone.
"""
from __future__ import annotations

from typing import Any

from ...connectors import sheets as sheets_api
from ._base import ToolServer, req

PROVIDER = "google_sheets"
LABEL = "Google Sheets"


def _range(args: dict[str, Any]) -> str:
    # Aceptamos `range` (canónico) o `a1_range` por compatibilidad.
    v = args.get("range") or args.get("a1_range")
    if not v:
        raise ValueError("Missing required argument 'range' (e.g. 'Sheet1!A1:C10').")
    return v


async def _find_spreadsheets(a):
    return {"files": await sheets_api.find_spreadsheets(a.get("query", ""))}


async def _create_spreadsheet(a):
    return await sheets_api.create_spreadsheet(req(a, "title"), a.get("headers"))


async def _get_metadata(a):
    return await sheets_api.metadata(req(a, "spreadsheet_id"))


async def _read_range(a):
    return {"values": await sheets_api.read_range(req(a, "spreadsheet_id"), _range(a))}


async def _update_range(a):
    return await sheets_api.update_range(req(a, "spreadsheet_id"), _range(a), req(a, "values"))


async def _append_rows(a):
    return await sheets_api.append_rows(req(a, "spreadsheet_id"), _range(a), req(a, "values"))


async def _clear_range(a):
    return await sheets_api.clear_range(req(a, "spreadsheet_id"), _range(a))


async def _delete_rows_where(a):
    return await sheets_api.delete_rows_where(
        req(a, "spreadsheet_id"), req(a, "sheet_name"), req(a, "column"), req(a, "equals")
    )


async def _delete_tab(a):
    return await sheets_api.delete_tab(req(a, "spreadsheet_id"), req(a, "sheet_name"))


_SPREADSHEET_ID = {"type": "string", "description": "The spreadsheet ID (from its URL)."}
_RANGE = {"type": "string", "description": "A1 notation, e.g. 'Sheet1!A1:C10' or 'Sheet1'."}
_VALUES = {
    "type": "array",
    "description": "Rows to write; a list of rows, each row a list of cell values.",
    "items": {"type": "array", "items": {}},
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "find_spreadsheets",
        "description": "Search the owner's Drive for spreadsheets by name. Returns [{id, name}].",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Name fragment to match."}},
        },
        "handler": _find_spreadsheets,
    },
    {
        "name": "create_spreadsheet",
        "description": "Create a new spreadsheet (optionally write a header row). "
        "Returns {spreadsheet_id, title, url}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Title of the new spreadsheet."},
                "headers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional header row written to A1.",
                },
            },
            "required": ["title"],
        },
        "handler": _create_spreadsheet,
    },
    {
        "name": "get_metadata",
        "description": "Get the spreadsheet title and its tabs (sheetId, title, rows, cols).",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _SPREADSHEET_ID},
            "required": ["spreadsheet_id"],
        },
        "handler": _get_metadata,
    },
    {
        "name": "read_range",
        "description": "Read a range. Returns {values: [[...]]} (empty if no data).",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _SPREADSHEET_ID, "range": _RANGE},
            "required": ["spreadsheet_id", "range"],
        },
        "handler": _read_range,
    },
    {
        "name": "update_range",
        "description": "Overwrite a range with the given values (USER_ENTERED).",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _SPREADSHEET_ID, "range": _RANGE, "values": _VALUES},
            "required": ["spreadsheet_id", "range", "values"],
        },
        "handler": _update_range,
    },
    {
        "name": "append_rows",
        "description": "Append rows after the last row of the range (INSERT_ROWS).",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _SPREADSHEET_ID, "range": _RANGE, "values": _VALUES},
            "required": ["spreadsheet_id", "range", "values"],
        },
        "handler": _append_rows,
    },
    {
        "name": "clear_range",
        "description": "Clear the values of a range (keeps formatting).",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": _SPREADSHEET_ID, "range": _RANGE},
            "required": ["spreadsheet_id", "range"],
        },
        "handler": _clear_range,
    },
    {
        "name": "delete_rows_where",
        "description": "Delete rows in a tab where a column (by header name) equals a value. "
        "Row 1 is treated as headers. Returns {deleted: N}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": _SPREADSHEET_ID,
                "sheet_name": {"type": "string", "description": "Tab name."},
                "column": {"type": "string", "description": "Header name to match."},
                "equals": {"type": "string", "description": "Value that triggers deletion."},
            },
            "required": ["spreadsheet_id", "sheet_name", "column", "equals"],
        },
        "handler": _delete_rows_where,
    },
    {
        "name": "delete_tab",
        "description": "Delete an entire tab (sheet) from the spreadsheet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": _SPREADSHEET_ID,
                "sheet_name": {"type": "string", "description": "Tab name to delete."},
            },
            "required": ["spreadsheet_id", "sheet_name"],
        },
        "handler": _delete_tab,
    },
]

SERVER = ToolServer(PROVIDER, LABEL, TOOLS, sheets_api.NotConnected)

if __name__ == "__main__":  # pragma: no cover
    import asyncio

    asyncio.run(SERVER.serve_stdio())
