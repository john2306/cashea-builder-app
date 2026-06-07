"""Infra compartida de los MCP servers self-hosted in-process.

Cada provider define solo sus tools (nombre, descripción, schema, handler) y crea un
`ToolServer`. La base aporta:
  - dispatch(tool, args, owner_email=None) -> {ok, text, result}  (contrato de call_owner_tool)
  - list_tools() -> [{name, description, input_schema}]            (para el puente del agente)
  - build_server() / serve_stdio()                                 (hosting standalone, p.ej. Cloud Run)

El token del dueño lo resuelve cada handler vía connectors/* (que leen el `use_user` vigente).
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


def req(args: dict[str, Any], key: str) -> Any:
    """Argumento requerido (no vacío) o ValueError -> {error: bad_arguments}."""
    v = args.get(key)
    if v is None or v == "":
        raise ValueError(f"Missing required argument '{key}'.")
    return v


class ToolServer:
    """Un MCP server self-hosted in-process para un provider."""

    def __init__(
        self,
        provider: str,
        label: str,
        tools: list[dict[str, Any]],
        not_connected: type[Exception],
    ) -> None:
        self.provider = provider
        self.label = label
        self.tools = tools
        self._handlers: dict[str, Handler] = {t["name"]: t["handler"] for t in tools}
        self._not_connected = not_connected

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
            for t in self.tools
        ]

    async def dispatch(
        self, tool: str, arguments: dict[str, Any] | None, owner_email: str | None = None
    ) -> dict[str, Any]:
        """Ejecuta `tool` y devuelve {ok, text, result}.

        Si `owner_email` se pasa, fija el dueño vigente (modo standalone); si no, usa el
        contextvar ya fijado por quien llama (modo in-process del connector-proxy).
        """
        fn = self._handlers.get(tool)
        if fn is None:
            avail = ", ".join(self._handlers)
            return {
                "ok": False,
                "text": f"Unknown tool '{tool}'. Available: {avail}.",
                "result": {"error": "unknown_tool", "tool": tool},
            }

        arguments = arguments or {}
        try:
            if owner_email:
                from ..connstore import use_user

                with use_user(owner_email):
                    result = await fn(arguments)
            else:
                result = await fn(arguments)
        except self._not_connected as exc:
            return {"ok": False, "text": str(exc), "result": {"error": "not_connected"}}
        except ValueError as exc:  # argumento faltante / inválido
            return {"ok": False, "text": str(exc), "result": {"error": "bad_arguments"}}
        except Exception as exc:  # noqa: BLE001 — error del API/red
            return {
                "ok": False,
                "text": f"{type(exc).__name__}: {exc}"[:500],
                "result": {"error": "tool_error"},
            }

        return {
            "ok": True,
            "text": json.dumps(result, ensure_ascii=False, default=str),
            "result": result,
        }

    # --- Hosting standalone (su propio Cloud Run / contenedor) -------------- #
    def build_server(self):  # pragma: no cover - ruta de hosting standalone
        from mcp.server import Server
        import mcp.types as types

        server = Server(self.provider.replace("_", "-"))

        @server.list_tools()
        async def _list() -> list["types.Tool"]:
            return [
                types.Tool(
                    name=t["name"], description=t["description"], inputSchema=t["input_schema"]
                )
                for t in self.tools
            ]

        @server.call_tool()
        async def _call(name: str, arguments: dict[str, Any]) -> list["types.TextContent"]:
            owner = (arguments or {}).pop("__owner_email", None)
            res = await self.dispatch(name, arguments, owner_email=owner)
            return [types.TextContent(type="text", text=res["text"])]

        return server

    async def serve_stdio(self) -> None:  # pragma: no cover
        from mcp.server.stdio import stdio_server

        server = self.build_server()
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
