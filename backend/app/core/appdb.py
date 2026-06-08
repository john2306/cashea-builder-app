"""Bases de datos POR APP (monolito): un schema + un rol acotado en `apps-postgres`.

Cada app que pide "su propia base de datos" recibe un SCHEMA y un ROLE dedicados, con
privilegios SOLO sobre ese schema (search_path fijo + revokes sobre `public`). La plataforma
(rol admin) provisiona/borra; la app interactúa vía el connector-proxy → `postgres_server`
(in-process), que se conecta como el ROL de la app. El connection string NUNCA llega a la app.

El schema y el rol se derivan del `app_id` (uuid): `app_<hex sin guiones>` — identificador SQL
válido sin comillas. El password lo genera la plataforma (urlsafe, sin comillas) y se guarda
cifrado en `AppProject.db_password`.
"""
from __future__ import annotations

import re
import secrets
from urllib.parse import quote, urlsplit

import asyncpg

from .config import settings

_PW_RE = re.compile(r"^[A-Za-z0-9_-]+$")  # token_urlsafe → seguro para inlinear en DDL
_IDENT_RE = re.compile(r"^app_[a-z0-9]+$")


def _ident(app_id: str) -> str:
    """Identificador SQL (schema = rol) derivado del app_id. Seguro sin comillas."""
    name = "app_" + re.sub(r"[^a-z0-9]", "", (app_id or "").lower())
    if not _IDENT_RE.fullmatch(name):
        raise ValueError(f"app_id inválido para identificador SQL: {app_id!r}")
    return name[:63]  # límite de Postgres


def schema_name(app_id: str) -> str:
    return _ident(app_id)


def role_name(app_id: str) -> str:
    return _ident(app_id)


def new_password() -> str:
    return secrets.token_urlsafe(24)


def app_dsn(app_id: str, password: str) -> str:
    """DSN del ROL de la app contra apps-postgres (lo usa postgres_server, NO la app)."""
    parts = urlsplit(settings.apps_database_admin_url)
    host = parts.hostname or "apps-postgres"
    port = parts.port or 5432
    db = (parts.path or "/appsdata").lstrip("/")
    return f"postgresql://{role_name(app_id)}:{quote(password)}@{host}:{port}/{db}"


async def provision(app_id: str, password: str) -> None:
    """Crea/asegura el schema + rol acotado de la app (IDEMPOTENTE).

    El rol: LOGIN, password fijo, `search_path` a su schema, dueño de su schema, SIN acceso a
    `public` ni a otros schemas. Arbitrario SQL del rol queda contenido por estos grants.
    """
    if not _PW_RE.fullmatch(password or ""):
        raise ValueError("password con caracteres no permitidos para DDL")
    schema = schema_name(app_id)
    role = role_name(app_id)
    # timeout acotado: si apps-postgres no responde, fallar rápido (NO colgar el deploy).
    conn = await asyncpg.connect(settings.apps_database_admin_url, timeout=10)
    try:
        # Rol (idempotente): crear si no existe; fijar password + search_path siempre.
        await conn.execute(
            f"DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
            f"CREATE ROLE {role} LOGIN PASSWORD '{password}'; END IF; END $$;"
        )
        await conn.execute(f"ALTER ROLE {role} WITH LOGIN PASSWORD '{password}'")
        await conn.execute(f"ALTER ROLE {role} SET search_path = {schema}")
        # Schema propio del rol.
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema} AUTHORIZATION {role}")
        await conn.execute(f"GRANT USAGE, CREATE ON SCHEMA {schema} TO {role}")
        # Aislamiento: nada en public, y que no pueda crear ahí.
        await conn.execute(f"REVOKE ALL ON SCHEMA public FROM {role}")
    finally:
        await conn.close()


async def deprovision(app_id: str) -> None:
    """Borra el schema (CASCADE) y el rol de la app. Best-effort, idempotente."""
    schema = schema_name(app_id)
    role = role_name(app_id)
    conn = await asyncpg.connect(settings.apps_database_admin_url, timeout=10)
    try:
        await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        # DROP OWNED limpia privilegios/objetos remanentes del rol antes de borrarlo.
        await conn.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
            f"DROP OWNED BY {role}; DROP ROLE {role}; END IF; END $$;"
        )
    finally:
        await conn.close()
