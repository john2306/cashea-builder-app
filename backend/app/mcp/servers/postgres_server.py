"""MCP server self-hosted (in-process) de la base de datos POR APP.

Cada app tiene su propio schema + rol en `apps-postgres` (ver core/appdb.py). Este server se
conecta a esa DB **como el ROL de la app** (search_path a su schema), así el SQL arbitrario
queda contenido a su propio schema. La app llama por el connector-proxy; nunca ve la credencial.

El `app_id` vigente lo provee el contextvar `current_app_id` (lo fija app_connector_proxy).
"""
from __future__ import annotations

from typing import Any

import asyncpg

from ...core.appdb import app_dsn, schema_name
from ...core.crypto import decrypt
from ...core.db import SessionLocal
from ...core.models import AppProject
from ..connstore import current_app_id
from ._base import ToolServer, req

PROVIDER = "postgres"
LABEL = "PostgreSQL (app database)"


class NotProvisioned(RuntimeError):
    """La app no tiene una base de datos propia aprovisionada."""


async def _connect() -> asyncpg.Connection:
    """Conexión a apps-postgres como el ROL de la app vigente (search_path a su schema)."""
    app_id = current_app_id()
    if not app_id:
        raise NotProvisioned("No hay app en contexto para resolver la base de datos.")
    async with SessionLocal() as session:
        ap = await session.get(AppProject, app_id)
    pw_enc = getattr(ap, "db_password", None) if ap else None
    if not pw_enc:
        raise NotProvisioned(
            "Esta app no tiene una base de datos propia. Pedila en el builder (data source: postgres)."
        )
    return await asyncpg.connect(app_dsn(app_id, decrypt(pw_enc)))


_RETURNS_ROWS = ("select", "with", "show", "explain", "values", "table", "returning")


async def _execute_sql(a: dict[str, Any]) -> dict[str, Any]:
    sql = req(a, "sql")
    conn = await _connect()
    try:
        low = sql.strip().rstrip(";").lower()
        if low.startswith(_RETURNS_ROWS) or " returning " in low:
            rows = await conn.fetch(sql)
            return {"rows": [dict(r) for r in rows], "rowcount": len(rows)}
        status = await conn.execute(sql)  # p.ej. "INSERT 0 3", "CREATE TABLE"
        return {"status": status}
    finally:
        await conn.close()


async def _list_tables(a: dict[str, Any]) -> dict[str, Any]:
    conn = await _connect()
    try:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = $1 ORDER BY tablename",
            schema_name(current_app_id() or ""),
        )
        return {"tables": [r["tablename"] for r in rows]}
    finally:
        await conn.close()


async def _describe_table(a: dict[str, Any]) -> dict[str, Any]:
    table = req(a, "table")
    conn = await _connect()
    try:
        rows = await conn.fetch(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = $2 ORDER BY ordinal_position",
            schema_name(current_app_id() or ""), table,
        )
        return {"columns": [dict(r) for r in rows]}
    finally:
        await conn.close()


TOOLS: list[dict[str, Any]] = [
    {
        "name": "execute_sql",
        "description": "Run a SQL statement on THIS app's private database (its own schema). "
        "SELECT/WITH/RETURNING return {rows, rowcount}; otherwise {status}. Use plain table "
        "names (search_path is set to the app schema); supports CREATE TABLE/INSERT/UPDATE/DELETE.",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "The SQL to execute."}},
            "required": ["sql"],
        },
        "handler": _execute_sql,
    },
    {
        "name": "list_tables",
        "description": "List the tables in this app's schema. Returns {tables: [...]}.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": _list_tables,
    },
    {
        "name": "describe_table",
        "description": "Describe a table's columns. Returns {columns: [{column_name, data_type, "
        "is_nullable}]}.",
        "input_schema": {
            "type": "object",
            "properties": {"table": {"type": "string", "description": "Table name."}},
            "required": ["table"],
        },
        "handler": _describe_table,
    },
]

SERVER = ToolServer(PROVIDER, LABEL, TOOLS, NotProvisioned)

if __name__ == "__main__":  # pragma: no cover
    import asyncio

    asyncio.run(SERVER.serve_stdio())
