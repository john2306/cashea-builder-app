"""MCP servers self-hosted propios (in-process), basados en las REST APIs.

Reemplazan el owner-token: el connector-proxy ejecuta sus tools con la conexión del dueño y
devuelve solo el resultado (el token nunca sale hacia la app). Cada server (`_base.ToolServer`)
expone:
  - dispatch(tool, args, owner_email=None) -> {ok, text, result}
  - list_tools() -> [{name, description, input_schema}]
  - build_server() / serve_stdio() -> hosting standalone (su propio Cloud Run después)

Registrar un nuevo provider self-hosted = agregar su módulo a la tupla de abajo.
"""
from __future__ import annotations

from . import (
    calendar_server,
    docs_server,
    drive_server,
    gmail_server,
    postgres_server,
    sheets_server,
)

# provider (clave del catálogo, snake_case) -> ToolServer
_LOCAL = {
    m.PROVIDER: m.SERVER
    for m in (
        sheets_server, drive_server, docs_server, gmail_server, calendar_server, postgres_server,
    )
}


def get_local_server(provider: str | None):
    """El ToolServer local para `provider` (acepta guion o guion bajo), o None."""
    return _LOCAL.get((provider or "").replace("-", "_"))


def local_providers() -> list[str]:
    return list(_LOCAL)
