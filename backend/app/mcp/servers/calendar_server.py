"""MCP server self-hosted de Google Calendar (basado en la REST API).

Envuelve `connectors/calendar.py` (token del dueño, refresh; calendarios y eventos CRUD).
"""
from __future__ import annotations

from typing import Any

from ...connectors import calendar as cal_api
from ._base import ToolServer, req

PROVIDER = "google_calendar"
LABEL = "Google Calendar"

_CAL_ID = {"type": "string", "description": "Calendar ID (default 'primary')."}
_EVENT_ID = {"type": "string", "description": "The event ID."}


async def _list_calendars(a):
    return {"calendars": await cal_api.list_calendars()}


async def _list_events(a):
    return {
        "events": await cal_api.list_events(
            a.get("calendar_id", "primary"),
            a.get("time_min"),
            a.get("time_max"),
            a.get("query"),
            int(a.get("max_results", 20)),
        )
    }


async def _create_event(a):
    return await cal_api.create_event(
        req(a, "summary"),
        req(a, "start"),
        req(a, "end"),
        a.get("calendar_id", "primary"),
        a.get("description"),
        a.get("location"),
        a.get("attendees"),
    )


async def _update_event(a):
    return await cal_api.update_event(
        req(a, "event_id"),
        a.get("calendar_id", "primary"),
        summary=a.get("summary"),
        description=a.get("description"),
        location=a.get("location"),
        start=a.get("start"),
        end=a.get("end"),
    )


async def _delete_event(a):
    return await cal_api.delete_event(req(a, "event_id"), a.get("calendar_id", "primary"))


_TIME = "Date 'YYYY-MM-DD' (all-day) or ISO datetime (with time)."

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_calendars",
        "description": "List the owner's calendars. Returns [{id, summary, primary}].",
        "input_schema": {"type": "object", "properties": {}},
        "handler": _list_calendars,
    },
    {
        "name": "list_events",
        "description": "List/search events (defaults to upcoming). "
        "Returns [{id, summary, start, end, location}].",
        "input_schema": {
            "type": "object",
            "properties": {
                "calendar_id": _CAL_ID,
                "time_min": {"type": "string", "description": "ISO lower bound (default: now)."},
                "time_max": {"type": "string", "description": "ISO upper bound (optional)."},
                "query": {"type": "string", "description": "Free-text search (optional)."},
                "max_results": {"type": "integer", "description": "Max events (default 20)."},
            },
        },
        "handler": _list_events,
    },
    {
        "name": "create_event",
        "description": "Create an event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Event title."},
                "start": {"type": "string", "description": _TIME},
                "end": {"type": "string", "description": _TIME},
                "calendar_id": _CAL_ID,
                "description": {"type": "string", "description": "Event description."},
                "location": {"type": "string", "description": "Event location."},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Attendee email addresses.",
                },
            },
            "required": ["summary", "start", "end"],
        },
        "handler": _create_event,
    },
    {
        "name": "update_event",
        "description": "Update fields of an existing event (only provided fields change).",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": _EVENT_ID,
                "calendar_id": _CAL_ID,
                "summary": {"type": "string", "description": "New title."},
                "description": {"type": "string", "description": "New description."},
                "location": {"type": "string", "description": "New location."},
                "start": {"type": "string", "description": _TIME},
                "end": {"type": "string", "description": _TIME},
            },
            "required": ["event_id"],
        },
        "handler": _update_event,
    },
    {
        "name": "delete_event",
        "description": "Delete an event. Returns {deleted: event_id}.",
        "input_schema": {
            "type": "object",
            "properties": {"event_id": _EVENT_ID, "calendar_id": _CAL_ID},
            "required": ["event_id"],
        },
        "handler": _delete_event,
    },
]

SERVER = ToolServer(PROVIDER, LABEL, TOOLS, cal_api.NotConnected)

if __name__ == "__main__":  # pragma: no cover
    import asyncio

    asyncio.run(SERVER.serve_stdio())
