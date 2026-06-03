"""Cliente directo de la Notion API (REST) con el token OAuth del broker.

Mismo patrón que sheets/drive/docs/gmail (transport: api, sin MCP): usa la conexión `notion`
(OAuth público de api.notion.com) guardada en McpConnection. El token OAuth de Notion es de
larga duración (no expira / sin refresh), así que solo se lee. Código propio y auditable —
adiós a los schemas caprichosos del MCP.

Operaciones: buscar, crear página (con contenido markdown simple), leer página, anexar bloques.
"""
from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import select

from ..core.crypto import decrypt
from ..core.db import SessionLocal
from ..core.models import McpConnection

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotConnected(RuntimeError):
    pass


async def _token() -> str:
    async with SessionLocal() as session:
        row = (
            await session.execute(select(McpConnection).where(McpConnection.provider == "notion"))
        ).scalar_one_or_none()
        if row is None or not row.access_token:
            raise NotConnected("Notion no está conectado (conéctalo en Connectors).")
        return decrypt(row.access_token)


async def _req(method: str, url: str, *, json=None, params=None) -> dict[str, Any]:
    token = await _token()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            method, url, json=json, params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Notion API {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


def _text_to_blocks(content: str) -> list[dict[str, Any]]:
    """Convierte texto/markdown simple en bloques de párrafo de Notion (1 por línea no vacía)."""
    blocks: list[dict[str, Any]] = []
    for line in (content or "").split("\n"):
        line = line.rstrip()
        if not line:
            continue
        blocks.append({
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": line[:1900]}}]},
        })
    return blocks[:100]  # Notion limita los children por request


# ---- Operaciones ---------------------------------------------------------------

async def search(query: str = "", only: str = "") -> list[dict[str, str]]:
    """Busca páginas/DBs accesibles por la integración. `only`: 'page' | 'database' | ''."""
    body: dict[str, Any] = {"page_size": 20}
    if query:
        body["query"] = query
    if only:
        body["filter"] = {"value": only, "property": "object"}
    data = await _req("POST", f"{API}/search", json=body)
    out = []
    for r in data.get("results", []):
        title = ""
        props = r.get("properties", {})
        # título de page (propiedad title) o de database (array title)
        for p in props.values():
            if isinstance(p, dict) and p.get("type") == "title":
                title = "".join(t.get("plain_text", "") for t in p.get("title", []))
                break
        if not title and r.get("object") == "database":
            title = "".join(t.get("plain_text", "") for t in r.get("title", []))
        out.append({"id": r.get("id"), "object": r.get("object"), "title": title or "(sin título)",
                    "url": r.get("url", "")})
    return out


async def create_page(parent_id: str, title: str, content: str = "",
                      parent_type: str = "page_id") -> dict[str, Any]:
    """Crea una página bajo un parent (page_id por defecto, o database_id). Devuelve id + url."""
    body: dict[str, Any] = {
        "parent": {parent_type: parent_id},
        "properties": {"title": {"title": [{"type": "text", "text": {"content": title}}]}},
    }
    blocks = _text_to_blocks(content)
    if blocks:
        body["children"] = blocks
    data = await _req("POST", f"{API}/pages", json=body)
    return {"id": data.get("id"), "url": data.get("url"), "title": title}


async def get_page(page_id: str) -> dict[str, Any]:
    return await _req("GET", f"{API}/pages/{page_id}")


async def append_blocks(page_id: str, content: str) -> dict[str, Any]:
    blocks = _text_to_blocks(content)
    return await _req("PATCH", f"{API}/blocks/{page_id}/children", json={"children": blocks})
