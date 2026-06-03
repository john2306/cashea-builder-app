"""Cliente directo de la Gmail API con el token OAuth del broker.

Mismo patrón que `sheets.py`/`drive.py`/`docs.py` (transport: api, sin contenedor MCP).
Funciona con cuentas personales @gmail.com (la API por-usuario no exige Workspace, a
diferencia del MCP hosted). Código propio y auditable.

Operaciones: buscar/listar correos, leer un correo, enviar y crear borrador.
Scopes: gmail.readonly (leer) + gmail.compose (enviar/borradores).
"""
from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy import select

from ..core.crypto import decrypt, encrypt
from ..core.db import SessionLocal
from ..core.models import McpConnection

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class NotConnected(RuntimeError):
    pass


async def _token() -> str:
    """Access token válido para Gmail (refresca y persiste si expiró)."""
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(McpConnection).where(McpConnection.provider == "gmail")
            )
        ).scalar_one_or_none()
        if row is None or not row.access_token:
            raise NotConnected("Gmail no está conectado (conéctalo en Connectors).")

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


async def _req(method: str, url: str, *, params=None, json=None) -> dict[str, Any]:
    token = await _token()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            method, url, params=params, json=json,
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Gmail API {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_part(data: str) -> str:
    return base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="replace")


def _extract_body(payload: dict) -> str:
    """Devuelve el texto plano del mensaje (recorre las partes MIME)."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return _decode_part(payload["body"]["data"])
    for part in payload.get("parts", []) or []:
        text = _extract_body(part)
        if text:
            return text
    # Fallback: body directo si no hubo text/plain.
    if payload.get("body", {}).get("data"):
        return _decode_part(payload["body"]["data"])
    return ""


# ---- Lectura -------------------------------------------------------------------

async def search(query: str = "", max_results: int = 10) -> list[dict[str, str]]:
    """Lista correos (con asunto/remitente/fecha) que matchean la query de Gmail."""
    data = await _req(
        "GET", f"{GMAIL}/messages", params={"q": query, "maxResults": max_results}
    )
    out: list[dict[str, str]] = []
    for m in data.get("messages", []) or []:
        meta = await _req(
            "GET", f"{GMAIL}/messages/{m['id']}",
            params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
        )
        headers = meta.get("payload", {}).get("headers", [])
        out.append({
            "id": m["id"],
            "from": _header(headers, "From"),
            "subject": _header(headers, "Subject"),
            "date": _header(headers, "Date"),
            "snippet": meta.get("snippet", ""),
        })
    return out


async def read_message(message_id: str) -> dict[str, str]:
    msg = await _req("GET", f"{GMAIL}/messages/{message_id}", params={"format": "full"})
    headers = msg.get("payload", {}).get("headers", [])
    return {
        "from": _header(headers, "From"),
        "to": _header(headers, "To"),
        "subject": _header(headers, "Subject"),
        "date": _header(headers, "Date"),
        "body": _extract_body(msg.get("payload", {})),
    }


# ---- Escritura -----------------------------------------------------------------

def _build_raw(to: str, subject: str, body: str) -> str:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


async def send(to: str, subject: str, body: str) -> dict[str, Any]:
    return await _req("POST", f"{GMAIL}/messages/send", json={"raw": _build_raw(to, subject, body)})


async def create_draft(to: str, subject: str, body: str) -> dict[str, Any]:
    return await _req(
        "POST", f"{GMAIL}/drafts", json={"message": {"raw": _build_raw(to, subject, body)}}
    )
