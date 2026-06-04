"""Cliente directo de la Google Sheets API (CRUD) con el token OAuth del broker.

Usa la conexión `google_sheets` (transport: api) guardada en McpConnection. Refresca el
access token con el refresh_token + GOOGLE_CLIENT_ID/SECRET cuando expira. Soporta:
leer, actualizar, agregar, limpiar rango, borrar filas por criterio y borrar pestaña.
También busca planillas por nombre (Drive API).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select

from ..core.crypto import decrypt, encrypt
from ..core.db import SessionLocal
from ..mcp.connstore import get_conn

SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE = "https://www.googleapis.com/drive/v3/files"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class NotConnected(RuntimeError):
    pass


async def _token() -> str:
    """Access token válido para Sheets (refresca y persiste si expiró)."""
    async with SessionLocal() as session:
        row = await get_conn(session, "google_sheets")
        if row is None or not row.access_token:
            raise NotConnected("Google Sheets no está conectado (conéctalo en Connectors).")

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
        raise RuntimeError(f"Sheets API {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


# ---- Descubrimiento -------------------------------------------------------

async def find_spreadsheets(query: str) -> list[dict[str, str]]:
    q = (
        f"mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
        + (f" and name contains '{query}'" if query else "")
    )
    data = await _req("GET", DRIVE, params={"q": q, "fields": "files(id,name)", "pageSize": 20})
    return data.get("files", [])


async def create_spreadsheet(title: str, headers: list[str] | None = None) -> dict[str, Any]:
    """Crea una nueva Google Sheet (Sheets API) y, opcional, escribe la fila de encabezados.
    Devuelve spreadsheet_id + url."""
    data = await _req("POST", SHEETS, json={"properties": {"title": title}})
    sid = data.get("spreadsheetId")
    if headers and sid:
        await update_range(sid, "A1", [headers])
    return {"spreadsheet_id": sid, "title": title, "url": data.get("spreadsheetUrl")}


async def metadata(spreadsheet_id: str) -> dict[str, Any]:
    data = await _req(
        "GET", f"{SHEETS}/{spreadsheet_id}",
        params={"fields": "properties.title,sheets.properties(sheetId,title,gridProperties)"},
    )
    return {
        "title": data.get("properties", {}).get("title"),
        "sheets": [
            {
                "sheetId": s["properties"]["sheetId"],
                "title": s["properties"]["title"],
                "rows": s["properties"].get("gridProperties", {}).get("rowCount"),
                "cols": s["properties"].get("gridProperties", {}).get("columnCount"),
            }
            for s in data.get("sheets", [])
        ],
    }


# ---- CRUD de valores ------------------------------------------------------

async def read_range(spreadsheet_id: str, a1_range: str) -> list[list[Any]]:
    data = await _req("GET", f"{SHEETS}/{spreadsheet_id}/values/{a1_range}")
    return data.get("values", [])


async def update_range(spreadsheet_id: str, a1_range: str, values: list[list[Any]]) -> dict:
    return await _req(
        "PUT", f"{SHEETS}/{spreadsheet_id}/values/{a1_range}",
        params={"valueInputOption": "USER_ENTERED"},
        json={"values": values},
    )


async def append_rows(spreadsheet_id: str, a1_range: str, values: list[list[Any]]) -> dict:
    return await _req(
        "POST", f"{SHEETS}/{spreadsheet_id}/values/{a1_range}:append",
        params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
        json={"values": values},
    )


async def clear_range(spreadsheet_id: str, a1_range: str) -> dict:
    return await _req("POST", f"{SHEETS}/{spreadsheet_id}/values/{a1_range}:clear")


async def _sheet_id(spreadsheet_id: str, sheet_name: str) -> int:
    for s in (await metadata(spreadsheet_id))["sheets"]:
        if s["title"] == sheet_name:
            return s["sheetId"]
    raise RuntimeError(f"No existe la pestaña '{sheet_name}'.")


async def delete_rows_where(
    spreadsheet_id: str, sheet_name: str, column: str, equals: str
) -> dict[str, Any]:
    """Borra filas donde la columna (por encabezado) == valor. Fila 1 = encabezados."""
    rows = await read_range(spreadsheet_id, sheet_name)
    if not rows:
        return {"deleted": 0}
    header = rows[0]
    if column not in header:
        raise RuntimeError(f"Columna '{column}' no está en {header}")
    col_idx = header.index(column)
    # Índices de fila (0-based en la grilla) a borrar; saltamos el encabezado (fila 0).
    targets = [
        i for i, r in enumerate(rows)
        if i > 0 and col_idx < len(r) and str(r[col_idx]) == str(equals)
    ]
    if not targets:
        return {"deleted": 0}
    sid = await _sheet_id(spreadsheet_id, sheet_name)
    # Borramos de abajo hacia arriba para no correr los índices.
    requests = [
        {
            "deleteDimension": {
                "range": {"sheetId": sid, "dimension": "ROWS", "startIndex": i, "endIndex": i + 1}
            }
        }
        for i in sorted(targets, reverse=True)
    ]
    await _req("POST", f"{SHEETS}/{spreadsheet_id}:batchUpdate", json={"requests": requests})
    return {"deleted": len(targets)}


async def delete_tab(spreadsheet_id: str, sheet_name: str) -> dict:
    sid = await _sheet_id(spreadsheet_id, sheet_name)
    return await _req(
        "POST", f"{SHEETS}/{spreadsheet_id}:batchUpdate",
        json={"requests": [{"deleteSheet": {"sheetId": sid}}]},
    )
