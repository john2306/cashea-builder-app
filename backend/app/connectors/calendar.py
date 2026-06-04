"""Cliente directo de la Google Calendar API con el token OAuth del broker.

Mismo patrón que `sheets.py`/`drive.py`/`docs.py` (transport: api, sin contenedor MCP).
Funciona con cuentas personales @gmail.com (la API por-usuario no exige Workspace, a
diferencia del MCP hosted). Código propio y auditable.

Operaciones: listar calendarios, listar/buscar eventos, crear, actualizar y borrar eventos.
Scopes: calendar.events (read+write) + calendar.calendarlist.readonly + events.freebusy.
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

CAL = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class NotConnected(RuntimeError):
    pass


async def _token() -> str:
    """Access token válido para Calendar (refresca y persiste si expiró)."""
    async with SessionLocal() as session:
        row = await get_conn(session, "google_calendar")
        if row is None or not row.access_token:
            raise NotConnected("Google Calendar no está conectado (conéctalo en Connectors).")

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
        raise RuntimeError(f"Calendar API {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


# ---- Lectura -------------------------------------------------------------------

async def list_calendars() -> list[dict[str, str]]:
    data = await _req("GET", f"{CAL}/users/me/calendarList", params={"fields": "items(id,summary,primary)"})
    return data.get("items", [])


async def list_events(
    calendar_id: str = "primary",
    time_min: str | None = None,
    time_max: str | None = None,
    query: str | None = None,
    max_results: int = 20,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": max_results,
    }
    # Por defecto, desde ahora hacia adelante.
    params["timeMin"] = time_min or datetime.now(timezone.utc).isoformat()
    if time_max:
        params["timeMax"] = time_max
    if query:
        params["q"] = query
    data = await _req("GET", f"{CAL}/calendars/{calendar_id}/events", params=params)
    out = []
    for e in data.get("items", []):
        out.append({
            "id": e.get("id"),
            "summary": e.get("summary", "(sin título)"),
            "start": (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date"),
            "end": (e.get("end") or {}).get("dateTime") or (e.get("end") or {}).get("date"),
            "location": e.get("location", ""),
        })
    return out


# ---- Escritura -----------------------------------------------------------------

def _time_field(value: str) -> dict[str, str]:
    """Acepta fecha (YYYY-MM-DD → all-day) o datetime ISO (con hora)."""
    if len(value) == 10 and value.count("-") == 2:
        return {"date": value}
    return {"dateTime": value}


async def create_event(
    summary: str,
    start: str,
    end: str,
    calendar_id: str = "primary",
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "summary": summary,
        "start": _time_field(start),
        "end": _time_field(end),
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]
    return await _req("POST", f"{CAL}/calendars/{calendar_id}/events", json=body)


async def update_event(
    event_id: str, calendar_id: str = "primary", **fields: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if fields.get("summary"):
        body["summary"] = fields["summary"]
    if fields.get("description"):
        body["description"] = fields["description"]
    if fields.get("location"):
        body["location"] = fields["location"]
    if fields.get("start"):
        body["start"] = _time_field(fields["start"])
    if fields.get("end"):
        body["end"] = _time_field(fields["end"])
    return await _req("PATCH", f"{CAL}/calendars/{calendar_id}/events/{event_id}", json=body)


async def delete_event(event_id: str, calendar_id: str = "primary") -> dict[str, Any]:
    await _req("DELETE", f"{CAL}/calendars/{calendar_id}/events/{event_id}")
    return {"deleted": event_id}
