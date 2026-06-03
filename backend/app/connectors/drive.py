"""Cliente directo de la Google Drive API (CRUD) con el token OAuth del broker.

Mismo patrón que `sheets.py` (transport: api, sin contenedor MCP): usa la conexión
`google_drive` guardada en McpConnection y refresca el access token con el refresh_token +
GOOGLE_CLIENT_ID/SECRET cuando expira. Código propio y auditable (sin paquetes npm de
terceros corriendo con el token del dueño).

Operaciones: buscar, listar carpeta, leer/exportar contenido, crear carpeta, crear archivo
de texto, actualizar contenido, renombrar, mover, copiar, borrar (papelera) y compartir.
"""
from __future__ import annotations

import json as _json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select

from ..core.crypto import decrypt, encrypt
from ..core.db import SessionLocal
from ..core.models import McpConnection

FILES = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
TOKEN_URL = "https://oauth2.googleapis.com/token"
FOLDER_MIME = "application/vnd.google-apps.folder"

# Tipos de Google (Docs/Sheets/Slides) que no se descargan crudo: se exportan a texto.
_EXPORT = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


class NotConnected(RuntimeError):
    pass


async def _token() -> str:
    """Access token válido para Drive (refresca y persiste si expiró)."""
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(McpConnection).where(McpConnection.provider == "google_drive")
            )
        ).scalar_one_or_none()
        if row is None or not row.access_token:
            raise NotConnected("Google Drive no está conectado (conéctalo en Connectors).")

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
        raise RuntimeError(f"Drive API {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


def _esc(value: str) -> str:
    """Escapa comillas simples para las consultas `q` de la Drive API."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


# ---- Lectura / descubrimiento --------------------------------------------------

async def search(query: str = "", page_size: int = 20) -> list[dict[str, Any]]:
    q = "trashed=false"
    if query:
        q += f" and name contains '{_esc(query)}'"
    data = await _req(
        "GET", FILES,
        params={"q": q, "fields": "files(id,name,mimeType,modifiedTime,size)", "pageSize": page_size},
    )
    return data.get("files", [])


async def list_folder(folder_id: str, page_size: int = 50) -> list[dict[str, Any]]:
    q = f"'{_esc(folder_id)}' in parents and trashed=false"
    data = await _req(
        "GET", FILES,
        params={"q": q, "fields": "files(id,name,mimeType,modifiedTime)", "pageSize": page_size},
    )
    return data.get("files", [])


async def get_file(file_id: str) -> dict[str, Any]:
    return await _req(
        "GET", f"{FILES}/{file_id}",
        params={"fields": "id,name,mimeType,size,modifiedTime,parents,webViewLink"},
    )


async def read_file(file_id: str) -> tuple[str, str]:
    """Devuelve (nombre, contenido de texto). Exporta Docs/Sheets/Slides a texto/CSV."""
    meta = await get_file(file_id)
    mime = meta.get("mimeType", "")
    token = await _token()
    async with httpx.AsyncClient(timeout=45.0) as client:
        if mime in _EXPORT:
            resp = await client.get(
                f"{FILES}/{file_id}/export", params={"mimeType": _EXPORT[mime]},
                headers={"Authorization": f"Bearer {token}"},
            )
        else:
            resp = await client.get(
                f"{FILES}/{file_id}", params={"alt": "media"},
                headers={"Authorization": f"Bearer {token}"},
            )
    if resp.status_code >= 400:
        raise RuntimeError(f"Drive API {resp.status_code}: {resp.text[:300]}")
    return meta.get("name", file_id), resp.text


# ---- Escritura -----------------------------------------------------------------

async def _upload(method: str, url: str, metadata: dict, content: str, mime: str) -> dict[str, Any]:
    """Upload multipart/related (metadatos + contenido en una sola petición)."""
    token = await _token()
    boundary = "cashea-" + os.urandom(8).hex()
    body = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        + _json.dumps(metadata)
        + f"\r\n--{boundary}\r\n"
        f"Content-Type: {mime}; charset=UTF-8\r\n\r\n"
        + content
        + f"\r\n--{boundary}--"
    ).encode("utf-8")
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.request(
            method, url, content=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Drive API {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


async def create_folder(name: str, parent: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
    if parent:
        body["parents"] = [parent]
    return await _req("POST", FILES, params={"fields": "id,name,webViewLink"}, json=body)


async def create_file(
    name: str, content: str = "", mime: str = "text/plain", parent: str | None = None
) -> dict[str, Any]:
    meta: dict[str, Any] = {"name": name}
    if parent:
        meta["parents"] = [parent]
    # Tipos NATIVOS de Google (Doc/Sheet/Slide/folder) se crean con metadata, SIN media
    # (subir media con un mime google-apps da "Invalid MIME type"). Para Sheets, mejor usar
    # la tool sheet_create (devuelve el spreadsheet_id para escribir).
    if mime.startswith("application/vnd.google-apps."):
        meta["mimeType"] = mime
        return await _req("POST", FILES, params={"fields": "id,name,webViewLink"}, json=meta)
    return await _upload(
        "POST", f"{UPLOAD}?uploadType=multipart&fields=id,name,webViewLink", meta, content, mime
    )


async def update_file(file_id: str, content: str, mime: str = "text/plain") -> dict[str, Any]:
    """Reemplaza el contenido (media) de un archivo existente."""
    token = await _token()
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.patch(
            f"{UPLOAD}/{file_id}", params={"uploadType": "media", "fields": "id,name"},
            content=content.encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": f"{mime}; charset=UTF-8"},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Drive API {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


async def rename(file_id: str, name: str) -> dict[str, Any]:
    return await _req("PATCH", f"{FILES}/{file_id}", params={"fields": "id,name"}, json={"name": name})


async def move(file_id: str, new_parent: str) -> dict[str, Any]:
    cur = await get_file(file_id)
    prev = ",".join(cur.get("parents", []))
    return await _req(
        "PATCH", f"{FILES}/{file_id}",
        params={"addParents": new_parent, "removeParents": prev, "fields": "id,name,parents"},
    )


async def copy_file(file_id: str, name: str | None = None) -> dict[str, Any]:
    body = {"name": name} if name else {}
    return await _req("POST", f"{FILES}/{file_id}/copy", params={"fields": "id,name"}, json=body)


async def delete(file_id: str, permanent: bool = False) -> dict[str, Any]:
    if permanent:
        await _req("DELETE", f"{FILES}/{file_id}")
        return {"deleted": file_id, "permanent": True}
    await _req("PATCH", f"{FILES}/{file_id}", json={"trashed": True})
    return {"deleted": file_id, "permanent": False}


async def share(
    file_id: str, email: str | None = None, role: str = "reader", anyone: bool = False
) -> dict[str, Any]:
    body: dict[str, Any] = {"role": role}
    if anyone:
        body["type"] = "anyone"
    else:
        body["type"] = "user"
        body["emailAddress"] = email
    return await _req(
        "POST", f"{FILES}/{file_id}/permissions",
        params={"fields": "id", "sendNotificationEmail": "false"}, json=body,
    )
