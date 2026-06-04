"""Pool de contenedores MCP por usuario (OAuth self-hosted).

Para los MCP `self_hosted` con `auth: oauth`, el token es por persona, así que el server
stdio se levanta en un contenedor dedicado por usuario con SU token inyectado en el entorno.
Reusa docker.sock (ya montado) y la red `cashea-web` (donde vive el backend), así el backend
alcanza el contenedor por nombre DNS. El contenedor queda corriendo (restart unless-stopped)
y se reusa entre llamadas; cae solo si se desconecta/borra.

Escala a cientos: el comando stdio y el puerto salen del catálogo; el env del token también.
"""
import asyncio
import hashlib
import os
import time

import docker

from .catalog import McpServer

NETWORK = os.environ.get("TRAEFIK_NETWORK", "cashea-web")
GATEWAY_IMAGE = os.environ.get("MCP_GATEWAY_IMAGE", "node:20-alpine")
# Reaper: contenedores ociosos > IDLE_TTL se eliminan (se recrean on-demand).
IDLE_TTL = int(os.environ.get("MCP_IDLE_TTL", "1800"))  # 30 min
REAP_INTERVAL = int(os.environ.get("MCP_REAP_INTERVAL", "300"))  # 5 min

# Última vez que se usó cada contenedor (nombre -> epoch). En memoria; el reprovision
# de arranque lo resetea a "ahora" para no reapear lo recién recreado.
_last_used: dict[str, float] = {}


def container_name(provider: str, user: str) -> str:
    h = hashlib.sha1(user.encode()).hexdigest()[:10]
    return f"mcp-{provider.replace('_', '-')}-{h}"


def _ensure_sync(spec: McpServer, env: dict[str, str], user: str) -> str:
    client = docker.from_env(version="auto")
    name = container_name(spec.key, user)
    try:
        c = client.containers.get(name)
        if c.status != "running":
            c.start()
    except docker.errors.NotFound:
        client.containers.run(
            GATEWAY_IMAGE,
            name=name,
            detach=True,
            network=NETWORK,
            command=[
                "npx", "-y", "supergateway",
                "--stdio", spec.stdio_command,
                "--outputTransport", "streamableHttp",
                "--streamableHttpPath", "/mcp",
                "--port", str(spec.port),
            ],
            environment=dict(env),
            restart_policy={"Name": "unless-stopped"},
            labels={"cashea.mcp": "1", "cashea.mcp.provider": spec.key, "cashea.mcp.user": user},
        )
    return f"http://{name}:{spec.port}/mcp"


async def ensure_server(spec: McpServer, env: dict[str, str], user: str = "builder") -> str:
    """Garantiza el contenedor MCP del usuario y devuelve su URL interna (/mcp)."""
    url = await asyncio.to_thread(_ensure_sync, spec, env, user)
    _last_used[container_name(spec.key, user)] = time.time()
    return url


def _remove_sync(provider: str, user: str) -> None:
    client = docker.from_env(version="auto")
    try:
        client.containers.get(container_name(provider, user)).remove(force=True)
    except docker.errors.NotFound:
        pass


async def remove_server(provider: str, user: str = "builder") -> None:
    _last_used.pop(container_name(provider, user), None)
    await asyncio.to_thread(_remove_sync, provider, user)


# ---- Reaprovisionamiento (arranque) y reaper de ociosos --------------------

async def reprovision() -> None:
    """Recrea los contenedores MCP por usuario desde las conexiones OAuth guardadas.

    Útil tras reiniciar el backend o si los contenedores fueron eliminados
    (p.ej. docker compose down). Idempotente: si el contenedor ya existe, no hace nada.
    """
    import json as _json

    from sqlalchemy import select

    from ..core.crypto import decrypt
    from ..core.db import SessionLocal
    from .catalog import load_catalog
    from ..core.models import McpConnection

    catalog = load_catalog()
    async with SessionLocal() as session:
        rows = (await session.execute(select(McpConnection))).scalars().all()
        # Un contenedor por (user_sub, provider): aislamiento por usuario.
        creds = [(r.user_sub, r.provider, r.env_json) for r in rows if r.user_sub]
    for user_sub, provider, env_json in creds:
        spec = catalog.get(provider)
        if spec is None or spec.transport != "self_hosted" or spec.auth != "oauth" or not env_json:
            continue
        try:
            env = {k: str(v) for k, v in _json.loads(decrypt(env_json)).items()}
            await ensure_server(spec, env, user_sub)
        except Exception:  # noqa: BLE001
            continue


def _reap_sync() -> int:
    client = docker.from_env(version="auto")
    now = time.time()
    reaped = 0
    for c in client.containers.list(all=True, filters={"label": "cashea.mcp=1"}):
        last = _last_used.get(c.name)
        if last is not None and now - last > IDLE_TTL:
            try:
                c.remove(force=True)
                _last_used.pop(c.name, None)
                reaped += 1
            except docker.errors.APIError:
                pass
    return reaped


async def _reaper_loop() -> None:
    while True:
        await asyncio.sleep(REAP_INTERVAL)
        try:
            await asyncio.to_thread(_reap_sync)
        except Exception:  # noqa: BLE001
            pass


_reaper_task: asyncio.Task | None = None


def start_reaper() -> None:
    global _reaper_task
    if _reaper_task is None or _reaper_task.done():
        _reaper_task = asyncio.create_task(_reaper_loop())
