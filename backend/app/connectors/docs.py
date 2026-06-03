"""Cliente directo de la Google Docs API con el token OAuth del broker.

Mismo patrón que `sheets.py`/`drive.py` (transport: api, sin contenedor MCP): usa la
conexión `google_docs` guardada en McpConnection y refresca el access token cuando expira.
Código propio y auditable.

Operaciones: crear documento, leer texto, anexar/insertar texto y reemplazar texto.
La edición usa documents.batchUpdate (insertText / replaceAllText).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select

from ..core.crypto import decrypt, encrypt
from ..core.db import SessionLocal
from ..core.models import McpConnection

DOCS = "https://docs.googleapis.com/v1/documents"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class NotConnected(RuntimeError):
    pass


async def _token() -> str:
    """Access token válido para Docs (refresca y persiste si expiró)."""
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(McpConnection).where(McpConnection.provider == "google_docs")
            )
        ).scalar_one_or_none()
        if row is None or not row.access_token:
            raise NotConnected("Google Docs no está conectado (conéctalo en Connectors).")

        now = datetime.now(timezone.utc)
        if row.expires_at and row.expires_at <= now and row.refresh_token:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    row.token_endpoint or TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": decrypt(row.refresh_token),
                        "client_id": row.client_id or os.environ.get("GOOGLE_CLIENT_ID", ""),
                        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
                    },
                )
            resp.raise_for_status()
            tok = resp.json()
            access = tok.get("access_token")
            if access:
                row.access_token = encrypt(access)
                if tok.get("expires_in"):
                    row.expires_at = now + timedelta(seconds=int(tok["expires_in"]))
                await session.commit()
                return access
        return decrypt(row.access_token)


async def _req(method: str, url: str, *, json=None) -> dict[str, Any]:
    token = await _token()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            method, url, json=json, headers={"Authorization": f"Bearer {token}"}
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Docs API {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


def _extract_text(doc: dict[str, Any]) -> str:
    """Aplana el cuerpo estructurado del documento a texto plano."""
    out: list[str] = []
    for el in doc.get("body", {}).get("content", []):
        para = el.get("paragraph")
        if not para:
            continue
        for pe in para.get("elements", []):
            run = pe.get("textRun")
            if run and run.get("content"):
                out.append(run["content"])
    return "".join(out)


def _end_index(doc: dict[str, Any]) -> int:
    """Índice de inserción al final del cuerpo (antes del salto de línea final)."""
    content = doc.get("body", {}).get("content", [])
    return (content[-1].get("endIndex", 2) - 1) if content else 1


# ---- Operaciones ---------------------------------------------------------------

async def create(title: str) -> dict[str, Any]:
    doc = await _req("POST", DOCS, json={"title": title})
    return {"documentId": doc.get("documentId"), "title": doc.get("title")}


async def get(document_id: str) -> dict[str, Any]:
    return await _req("GET", f"{DOCS}/{document_id}")


async def read_text(document_id: str) -> tuple[str, str]:
    doc = await get(document_id)
    return doc.get("title", document_id), _extract_text(doc)


async def _batch_update(document_id: str, requests: list[dict]) -> dict[str, Any]:
    return await _req("POST", f"{DOCS}/{document_id}:batchUpdate", json={"requests": requests})


async def insert_text(document_id: str, text: str, index: int | None = None) -> dict[str, Any]:
    if index is None:
        index = _end_index(await get(document_id))
    return await _batch_update(
        document_id, [{"insertText": {"location": {"index": index}, "text": text}}]
    )


async def append_text(document_id: str, text: str) -> dict[str, Any]:
    return await insert_text(document_id, text, index=None)


async def replace_text(document_id: str, find: str, replace: str) -> dict[str, Any]:
    return await _batch_update(
        document_id,
        [{"replaceAllText": {"containsText": {"text": find, "matchCase": True}, "replaceText": replace}}],
    )
