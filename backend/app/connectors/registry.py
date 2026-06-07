"""Registro de conectores OAuth2.0 del Cashea Builder.

Cada conector define los endpoints OAuth del proveedor, los scopes, las variables de
entorno con las credenciales del cliente (CLIENT_ID/SECRET) y las palabras clave para
detectarlo en la conversación.

Las credenciales se registran por proveedor en el entorno (no se hardcodean). Un conector
está "configurado" solo si su CLIENT_ID y CLIENT_SECRET están presentes.
"""
import os
from dataclasses import dataclass, field
from urllib.parse import urlencode


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    authorize_url: str
    token_url: str
    scopes: list[str]
    client_id_env: str
    client_secret_env: str
    keywords: list[str]
    extra_auth_params: dict[str, str] = field(default_factory=dict)
    scope_separator: str = " "
    user_scopes: list[str] = field(default_factory=list)  # Slack: user token (por persona)
    token_auth: str = "body"  # "body" | "basic" (HTTP Basic con client_id:secret)
    token_body: str = "form"  # "form" | "json"


_GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
_GOOGLE_EXTRA = {"access_type": "offline", "prompt": "consent"}

PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in [
        Provider(
            # MCP hosted de Gmail (gmailmcp.googleapis.com). Doc: gmail.readonly + gmail.compose
            # (leer + redactar/enviar borradores). IMPORTANTE: igual que Calendar, este MCP
            # hosted SOLO funciona con cuentas de Google WORKSPACE; con @gmail.com personal da
            # "The caller does not have permission" (limitación de tipo de cuenta, no de scopes).
            "gmail", "Gmail", _GOOGLE_AUTH, _GOOGLE_TOKEN,
            [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.compose",
                "openid", "email",
            ],
            "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
            ["gmail", "correo electr", "email", "imap", "bandeja de entrada"],
            _GOOGLE_EXTRA,
        ),
        Provider(
            # Scope COMPLETO de Drive: leer + crear/editar/borrar/mover/subir archivos y
            # carpetas (CRUD). Antes era drive.readonly (solo búsqueda/lectura).
            "google-drive", "Google Drive", _GOOGLE_AUTH, _GOOGLE_TOKEN,
            ["https://www.googleapis.com/auth/drive", "openid", "email"],
            "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
            ["google drive", " drive"],
            _GOOGLE_EXTRA,
        ),
        Provider(
            # Scope COMPLETO de BigQuery: leer + escribir + DDL (crear/borrar datasets y
            # tablas, INSERT/UPDATE/DELETE, jobs). 'cloud-platform' añade acceso al proyecto
            # para listar/escanear (INFORMATION_SCHEMA) sin tropezar con permisos.
            "bigquery", "BigQuery", _GOOGLE_AUTH, _GOOGLE_TOKEN,
            [
                "https://www.googleapis.com/auth/bigquery",
                "https://www.googleapis.com/auth/cloud-platform",
                "openid", "email",
            ],
            "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
            ["bigquery", "big query"],
            _GOOGLE_EXTRA,
        ),
        Provider(
            # Cloud Storage MCP (hosted por Google). El server exige IAM/OAuth (no API keys).
            # read_write cubre listar/leer/escribir objetos y crear buckets; cloud-platform
            # da contexto de proyecto para list_buckets. Para solo lectura, cambiar a
            # devstorage.read_only.
            "cloud-storage", "Cloud Storage", _GOOGLE_AUTH, _GOOGLE_TOKEN,
            [
                "https://www.googleapis.com/auth/devstorage.read_write",
                "https://www.googleapis.com/auth/cloud-platform",
                "openid", "email",
            ],
            "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
            ["cloud storage", "gcs", "bucket", "google storage", "almacenamiento"],
            _GOOGLE_EXTRA,
        ),
        Provider(
            # Google Docs vía API directa (crear/leer/editar). Scope `documents` (read+write).
            "google-docs", "Google Docs", _GOOGLE_AUTH, _GOOGLE_TOKEN,
            [
                "https://www.googleapis.com/auth/documents",
                "openid", "email",
            ],
            "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
            ["google docs", "google doc", "documento google", "docs"],
            _GOOGLE_EXTRA,
        ),
        Provider(
            # CRUD de Google Sheets vía API directa (read + write).
            "google-sheets", "Google Sheets", _GOOGLE_AUTH, _GOOGLE_TOKEN,
            [
                "https://www.googleapis.com/auth/spreadsheets",   # leer + escribir
                "https://www.googleapis.com/auth/drive.readonly",  # buscar archivos por nombre
                "openid", "email",
            ],
            "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
            ["google sheet", "google sheets", "planilla google", "hoja de cálculo"],
            _GOOGLE_EXTRA,
        ),
        Provider(
            # El MCP hosted de Google Calendar expone tools de lectura Y escritura
            # (create/update/delete/respond_event). `calendar.events` cubre leer+escribir
            # eventos; calendarlist.readonly para list_calendars; freebusy para suggest_time.
            # IMPORTANTE: este MCP hosted SOLO funciona con cuentas de Google WORKSPACE
            # (organizacionales). Con cuentas personales @gmail.com Google responde
            # "The caller does not have permission" — es una limitación de tipo de cuenta,
            # no de scopes. (cf. github.com/google-gemini/gemini-cli/discussions/26017)
            "google-calendar", "Google Calendar", _GOOGLE_AUTH, _GOOGLE_TOKEN,
            [
                "https://www.googleapis.com/auth/calendar.events",
                "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
                "https://www.googleapis.com/auth/calendar.events.freebusy",
                "openid", "email",
            ],
            "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
            ["google calendar", "calendario", "calendar", "agenda", "eventos"],
            _GOOGLE_EXTRA,
        ),
        # Notion ya no usa este broker: pasó a su MCP hosteado (mcp.notion.com), que se conecta
        # por OAuth DCR + PKCE auto-descubierto (igual que Intercom) — ver mcp/catalog.yaml.
        Provider(
            "slack", "Slack",
            "https://slack.com/oauth/v2/authorize",
            "https://slack.com/api/oauth.v2.access",
            [],  # sin bot scopes: usamos user token (por persona)
            "SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET",
            ["slack"], {}, scope_separator=",",
            user_scopes=[
                "channels:read", "channels:history", "groups:read",
                "chat:write", "users:read",
            ],
        ),
        Provider(
            "miro", "Miro",
            "https://miro.com/oauth/authorize",
            "https://api.miro.com/v1/oauth/token",
            [], "MIRO_CLIENT_ID", "MIRO_CLIENT_SECRET",
            ["miro"], {},
        ),
    ]
}


def is_configured(provider: Provider) -> bool:
    return bool(
        os.environ.get(provider.client_id_env) and os.environ.get(provider.client_secret_env)
    )


def build_authorize_url(provider: Provider, redirect_uri: str, state: str) -> str:
    params: dict[str, str] = {
        "client_id": os.environ.get(provider.client_id_env, ""),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    if provider.scopes:
        params["scope"] = provider.scope_separator.join(provider.scopes)
    if provider.user_scopes:
        params["user_scope"] = provider.scope_separator.join(provider.user_scopes)
    params.update(provider.extra_auth_params)
    return f"{provider.authorize_url}?{urlencode(params)}"


def detect_providers(text: str) -> list[str]:
    low = text.lower()
    return [pid for pid, p in PROVIDERS.items() if any(k in low for k in p.keywords)]
