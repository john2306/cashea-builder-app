"""Carga el catálogo de MCP servers (`mcp_catalog.yaml`) — única fuente de verdad.

Escala a cientos de servers sin tocar código: el catálogo declara cada server y
de aquí derivan el registry del agente, el cliente determinístico y la UI.

Dos planos de alcance (clave para entender `transport`):
  - hosted      : URL pública. El conector del agente (API Anthropic) la llama desde
                  la nube de Anthropic -> debe ser pública (p.ej. mcp.notion.com).
  - self_hosted : contenedor local (supergateway + server stdio). Solo lo alcanza el
                  engine determinístico vía DNS interno de Docker (http://mcp-<key>:port/mcp).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

CATALOG_PATH = Path(__file__).with_name("catalog.yaml")


@dataclass(frozen=True)
class McpServer:
    key: str
    label: str
    brand: str = "#888888"
    transport: str = "hosted"  # hosted | self_hosted
    auth: str = "none"  # oauth | env | none
    url: str = ""  # hosted: URL pública
    token_env: str = ""  # hosted: fallback de token por env
    package: str = ""  # self_hosted: paquete npm stdio
    command: str = ""  # self_hosted: comando stdio completo (override)
    port: int = 8000  # self_hosted: puerto HTTP interno
    env: tuple[str, ...] = field(default_factory=tuple)
    oauth: dict = field(default_factory=dict)  # auth=oauth: provider_id, env_map, user_token
    agent_hint: str = ""  # descripción breve de capacidades (la ve el agente en su contexto)
    workspace_only: bool = False  # MCP que solo funciona con cuentas Google Workspace

    @property
    def host(self) -> str:
        """Nombre DNS interno del contenedor (sin guion bajo: inválido en hostnames)."""
        return "mcp-" + self.key.replace("_", "-")

    @property
    def internal_url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"

    @property
    def stdio_command(self) -> str:
        return self.command or f"npx -y {self.package}"

    def resolved_url(self) -> str:
        """URL efectiva para llamar al server: env override > catálogo > interno."""
        if self.transport == "hosted":
            return os.environ.get(f"{self.key.upper()}_MCP_URL", self.url)
        return os.environ.get(f"{self.key.upper()}_MCP_URL", self.internal_url)


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, McpServer]:
    if not CATALOG_PATH.exists():
        return {}
    data = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}
    out: dict[str, McpServer] = {}
    for raw in data.get("servers", []):
        key = raw["key"]
        out[key] = McpServer(
            key=key,
            label=raw.get("label", key),
            brand=raw.get("brand", "#888888"),
            transport=raw.get("transport", "hosted"),
            auth=raw.get("auth", "none"),
            url=raw.get("url", ""),
            token_env=raw.get("token_env", ""),
            package=raw.get("package", ""),
            command=raw.get("command", ""),
            port=int(raw.get("port", 8000)),
            env=tuple(raw.get("env", []) or []),
            oauth=raw.get("oauth", {}) or {},
            agent_hint=raw.get("agent_hint", ""),
            workspace_only=bool(raw.get("workspace_only", False)),
        )
    return out


def catalog_list() -> list[McpServer]:
    return list(load_catalog().values())
