"""Connector proxy para apps desplegadas: ejecuta una tool MCP con la conexión del DUEÑO.

Las apps NO reciben tokens ni hablan con el servicio directo (el token de un MCP NO sirve
para la API REST del servicio — p.ej. el token de mcp.notion.com no vale para api.notion.com).
En su lugar llaman a la plataforma con su X-App-Secret y ESTE módulo ejecuta la tool:
  - hosted      -> JSON-RPC MCP contra la URL pública (mcp.notion.com, etc.) con el token del
                   dueño (refrescado por active_mcp_servers).
  - self_hosted -> contenedor interno del usuario (mcp.client.call_tool).

Mismo principio de seguridad que el LLM proxy: las credenciales nunca salen de la plataforma.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from .catalog import load_catalog
from .registry import active_mcp_servers
from .servers import get_local_server


class ConnectorError(RuntimeError):
    pass


def _parse_mcp(r: httpx.Response) -> dict[str, Any]:
    """Parsea la respuesta MCP, sea JSON o SSE (text/event-stream)."""
    ct = r.headers.get("content-type", "")
    if "text/event-stream" in ct:
        out: dict[str, Any] = {}
        for line in r.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    out = json.loads(line[5:].strip())
                except Exception:  # noqa: BLE001
                    pass
        return out
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return {}


async def _hosted_call(url: str, token: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(timeout=90.0) as c:
        init = await c.post(url, headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "cashea-app", "version": "1"}},
        })
        sid = init.headers.get("mcp-session-id")
        if sid:
            headers["mcp-session-id"] = sid
        try:  # notificación de inicialización (algunos servers la requieren)
            await c.post(url, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:  # noqa: BLE001
            pass
        r = await c.post(url, headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}},
        })
    data = _parse_mcp(r)
    err = data.get("error")
    result = data.get("result", {}) if isinstance(data.get("result"), dict) else {}
    content = result.get("content", []) or []
    text = "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    is_error = bool(result.get("isError")) or err is not None
    return {
        "ok": not is_error,
        "text": text or (json.dumps(err, ensure_ascii=False) if err else ""),
        "result": result or ({"error": err} if err else {}),
    }


def _is_unknown_tool(text: str) -> bool:
    """Detecta el error de 'tool inexistente' (JSON-RPC -32602 / method not found)."""
    t = (text or "").lower()
    return (
        "not found in the list of tools" in t
        or "method not found" in t
        or "-32602" in t
        or "unknown tool" in t
    )


async def _available_tools(provider: str) -> list[str]:
    """Lista las tools del MCP (nombre + args requeridos) para auto-documentar errores de tool."""
    try:
        from . import client as mcp_client

        out: list[str] = []
        for t in await mcp_client.list_tools(provider, quick=True):
            req = (t.get("input_schema") or {}).get("required") or []
            out.append(f"{t['name']}({', '.join(req)})")
        return out
    except Exception:  # noqa: BLE001 — best-effort; no rompe el flujo de error
        return []


async def _enrich_unknown_tool(res: dict[str, Any], provider: str, tool: str, label: str) -> dict[str, Any]:
    """Si la tool no existe, agrega al error la lista de tools disponibles (con sus args)."""
    if res.get("ok") or not _is_unknown_tool(res.get("text", "")):
        return res
    names = await _available_tools(provider)
    if names:
        res["text"] = (
            f"Tool '{tool}' no existe en {label}. Tools disponibles: {', '.join(names)}."
        )
        res.setdefault("result", {})
        if isinstance(res["result"], dict):
            res["result"]["available_tools"] = names
    return res


async def call_owner_tool(provider: str, tool: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Ejecuta `tool` del conector MCP `provider` con la conexión del dueño. Devuelve
    {ok, text, result}."""
    arguments = arguments or {}

    from . import state as connector_state

    if not connector_state.is_enabled(provider):
        raise ConnectorError(f"El conector '{provider}' está deshabilitado por el administrador.")

    # MCP server self-hosted propio (in-process, reusa connectors/*): el camino que reemplaza a
    # owner-token. El dueño vigente ya viene en el contextvar (lo fija app_connector_proxy).
    local = get_local_server(provider)
    if local is not None:
        res = await local.dispatch(tool, arguments)
        # "dueño no conectó" -> ConnectorError (el proxy lo mapea a 409, igual que los hosted).
        if not res.get("ok") and (res.get("result") or {}).get("error") == "not_connected":
            raise ConnectorError(res.get("text") or f"{local.label} no está conectado.")
        return res

    spec = load_catalog().get(provider)
    if spec is None:
        raise ConnectorError(f"Conector '{provider}' desconocido.")

    if spec.transport == "self_hosted":
        from . import client as mcp_client

        res = await mcp_client.call_tool(provider, tool, arguments)
        text = res.get("text") or json.dumps(res.get("structured") or {}, ensure_ascii=False)
        out = {"ok": not res.get("is_error"), "text": text or "(sin contenido)", "result": res}
        return await _enrich_unknown_tool(out, provider, tool, spec.label)

    if spec.transport == "hosted":
        servers = await active_mcp_servers()
        entry = next((s for s in servers if s.get("name") == provider), None)
        if not entry:
            raise ConnectorError(f"{spec.label} no está conectado (conéctalo en Connectors).")
        out = await _hosted_call(entry["url"], entry["authorization_token"], tool, arguments)
        return await _enrich_unknown_tool(out, provider, tool, spec.label)

    raise ConnectorError(
        f"'{provider}' no tiene un MCP server local ni hosted/self-hosted configurado en el catálogo."
    )
