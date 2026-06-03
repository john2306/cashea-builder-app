"""Genera `docker-compose.mcp.yml` desde `catalog.yaml`.

Cada server `self_hosted` -> un servicio que envuelve el MCP stdio con `supergateway`
exponiéndolo como Streamable HTTP en http://mcp-<key>:<port>/mcp (alcanzable por el
backend en la red interna). Escala a cientos: agregás al catálogo y regenerás.

Uso:
  docker exec cashea-builder-app-backend-1 python -m app.mcp.gen > docker-compose.mcp.yml
  docker compose -f docker-compose.yml -f docker-compose.mcp.yml up -d
"""
import sys

import yaml

from .catalog import catalog_list


def build() -> dict:
    services: dict[str, dict] = {}
    for s in catalog_list():
        # oauth self-hosted se maneja por-usuario en mcp_pool (no contenedor estático).
        if s.transport != "self_hosted" or s.auth == "oauth":
            continue
        services[s.host] = {
            "image": "node:20-alpine",
            "command": [
                "npx", "-y", "supergateway",
                "--stdio", s.stdio_command,
                "--outputTransport", "streamableHttp",
                "--streamableHttpPath", "/mcp",
                "--port", str(s.port),
            ],
            "environment": {var: f"${{{var}:-}}" for var in s.env},
            "networks": ["default"],
            "restart": "unless-stopped",
        }
    return {"services": services, "networks": {"default": {}}}


def main() -> None:
    doc = build()
    header = (
        "# GENERADO por `python -m app.mcp.gen` desde catalog.yaml — NO editar a mano.\n"
        "# Regenerar tras cambiar el catálogo. Levantar:\n"
        "#   docker compose -f docker-compose.yml -f docker-compose.mcp.yml up -d\n"
    )
    sys.stdout.write(header)
    yaml.safe_dump(doc, sys.stdout, sort_keys=False, default_flow_style=False)


if __name__ == "__main__":
    main()
