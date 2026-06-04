import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import and_, case, delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .mcp import oauth as mcp_oauth
from .agent import runner
from .mcp.registry import MCP_REGISTRY
from .auth import decode_jwt, encode_jwt
from .builder.codegen import generate_app_files
from .core.config import settings
from .connectors import PROVIDERS, build_authorize_url, detect_providers, is_configured
from .core.crypto import decrypt, encrypt
from .core.db import SessionLocal, get_session, init_db
from .core.events import log_event
from .builder.deploy import build_and_run, public_url_parts, teardown_app
from .core.models import (
    AppProject,
    Connection,
    Conversation,
    EventLog,
    McpConnection,
    Message,
    User,
)
from .core.schemas import (
    AppListPage,
    AppProjectCreate,
    AppProjectDetail,
    AppProjectOut,
    AppProjectUpdate,
    ConnectionOut,
    ConnectorInfo,
    ConversationDetail,
    ConversationOut,
    MessageOut,
)

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")
# Estados OAuth pendientes: state -> (app_id, user_sub, provider, created_ts)
_oauth_states: dict[str, tuple[str, str, str, float]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Recrea contenedores MCP por usuario (tras reinicio) y arranca el reaper de ociosos.
    try:
        from .mcp import pool as mcp_pool

        await mcp_pool.reprovision()
        mcp_pool.start_reaper()
    except Exception:  # noqa: BLE001
        pass
    yield
    # Cierra el cliente aiohttp de Anthropic limpiamente.
    await runner.aclose()


app = FastAPI(title="Automatización por lenguaje natural", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # Las apps desplegadas (<slug>.localhost:5173) llaman al broker/gateway.
    allow_origin_regex=r"http://[a-z0-9-]+\.localhost:5173",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def current_user(authorization: str = Header(default="")) -> dict:
    """Extrae el usuario final (Google sub) desde el JWT de sesión (Bearer)."""
    token = authorization[7:] if authorization[:7].lower() == "bearer " else ""
    user = decode_jwt(token, settings.session_secret) if token else None
    if not user or not user.get("sub"):
        raise HTTPException(status_code=401, detail="Sesión requerida")
    return user


def _is_public_api(path: str) -> bool:
    """Rutas /api que NO exigen sesión del builder:
    - gateway de apps desplegadas (X-App-Secret) y datos de dashboard del visor;
    - flujos OAuth (navegación/popup y callbacks, que no pueden llevar el header de sesión)."""
    if path == "/api/health" or path == "/api/config":
        return True
    if "/access" in path or "/owner-token/" in path or path.startswith("/api/dashboards/"):
        return True
    if path.endswith("/llm"):  # proxy LLM de la app desplegada (auth por X-App-Secret)
        return True
    if path.startswith("/api/apps/") and "/mcp/" in path:  # connector proxy (X-App-Secret)
        return True
    if "/me/connections" in path:  # conexiones por-visor de la app desplegada
        return True
    if path == "/api/connectors/callback":
        return True
    if "/connectors/" in path and path.endswith("/authorize"):
        return True
    if path.startswith("/api/mcp/") and (path.endswith("/connect") or path.endswith("/callback")):
        return True
    return False


def _req_email(request: Request) -> str | None:
    """Email del usuario de la sesión (lo deja el middleware en request.state.user)."""
    user = getattr(request.state, "user", None)
    return user.get("email") if isinstance(user, dict) else None


@app.middleware("http")
async def _require_session(request, call_next):
    """Gate del builder: exige sesión Google (JWT) en /api/* salvo las rutas públicas.
    Acepta el token por `Authorization: Bearer` o `?token=` (para SSE/EventSource)."""
    path = request.url.path
    if request.method == "OPTIONS" or not path.startswith("/api/") or _is_public_api(path):
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth[:7].lower() == "bearer " else request.query_params.get("token", "")
    user = decode_jwt(token, settings.session_secret) if token else None
    if not user or not user.get("sub"):
        return JSONResponse(status_code=401, content={"detail": "Sesión requerida"})
    request.state.user = user
    return await call_next(request)


SLUG_MAX = 21  # límite de caracteres del subdominio


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:SLUG_MAX].strip("-") or "app"


async def _unique_slug(
    session: AsyncSession, base: str, exclude_id: str | None = None
) -> str:
    slug = base
    n = 2
    while True:
        existing = (
            await session.execute(select(AppProject).where(AppProject.slug == slug))
        ).scalar_one_or_none()
        if existing is None or existing.id == exclude_id:
            return slug
        slug = f"{base}-{n}"
        n += 1


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(parts)
    return ""


def _transcript(rows: list[Message]) -> str:
    lines = []
    for m in rows:
        text = _message_text(m.content).strip()
        if text:
            lines.append(f"{m.role.upper()}: {text}")
    return "\n\n".join(lines)[:12000]


# Deploys en curso: app_id -> id de la tarea Celery (para poder cancelar/revocar).
_deploy_tasks: dict[str, str] = {}


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "model": settings.anthropic_model}


@app.get("/api/conversations", response_model=list[ConversationOut])
async def list_conversations(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(select(Conversation).order_by(Conversation.created_at.desc()))
    ).scalars().all()
    return rows


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str, session: AsyncSession = Depends(get_session)
):
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    rows = (
        await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.seq)  # orden de inserción estable
        )
    ).scalars().all()
    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        messages=[MessageOut.model_validate(m) for m in rows],
    )


@app.get("/api/apps", response_model=AppListPage)
async def list_apps(
    request: Request,
    q: str = "",
    owner: str = "",
    scope: str = "",  # member: ""/all | mine | shared (mías / compartidas conmigo / ambas)
    sort: str = "new",
    limit: int = 30,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """Lista paginada (escala a miles de apps): búsqueda/orden/filtro server-side.
    Admin → todas (filtro opcional `?owner=`); member → solo propias + compartidas.
    Setea `my_role` por app (admin|owner|editor|viewer) para que la UI gobierne las acciones."""
    email = (_req_email(request) or "").strip().lower()
    admin = await _is_admin_db(session, email)
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    conds = []
    if q.strip():
        conds.append(AppProject.title.ilike(f"%{q.strip()}%"))
    if admin:
        if owner.strip():
            conds.append(func.lower(AppProject.owner_email) == owner.strip().lower())
    else:
        # propias O en la allowlist (shared_emails es JSON → contención con @> en jsonb).
        # OJO: hay que castear AMBOS lados a jsonb (el bind llega como varchar).
        shared = text("app_projects.shared_emails::jsonb @> CAST(:em AS jsonb)").bindparams(
            em=json.dumps([email])
        )
        own = func.lower(AppProject.owner_email) == email
        if scope == "mine":
            conds.append(own)
        elif scope == "shared":  # compartidas conmigo (no soy el dueño)
            conds.append(and_(shared, func.lower(AppProject.owner_email) != email))
        else:  # ambas
            conds.append(or_(own, shared))

    base = select(AppProject)
    if conds:
        base = base.where(*conds)

    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar() or 0

    if sort == "live":
        ordered = base.order_by(
            case((AppProject.deploy_state == "deployed", 0), else_=1),
            AppProject.updated_at.desc(),
        )
    else:
        order_col = {
            "old": AppProject.updated_at.asc(),
            "az": AppProject.title.asc(),
            "za": AppProject.title.desc(),
        }.get(sort, AppProject.updated_at.desc())
        ordered = base.order_by(order_col)

    rows = (
        await session.execute(ordered.limit(limit).offset(offset))
    ).scalars().all()

    for a in rows:
        if admin:
            a.my_role = "admin"
        elif email == (a.owner_email or "").strip().lower():
            a.my_role = "owner"
        else:
            editors = [e.strip().lower() for e in (a.editor_emails or [])]
            a.my_role = "editor" if email in editors else "viewer"

    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@app.get("/api/config")
async def public_config():
    """Config que la UI necesita en runtime. Hoy: dominio/esquema de las apps desplegadas
    (para que el modal de deploy muestre `.app.izideploy.com` en prod y no el localhost)."""
    return {"apps": public_url_parts()}


@app.post("/api/apps", response_model=AppProjectDetail)
async def create_app(
    payload: AppProjectCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    title = payload.title.strip() or "Nueva app"
    conversation = Conversation(title=title[:60])
    session.add(conversation)
    await session.flush()

    # El creador es el dueño: se auto-comparte la app con su correo y no se podrá quitar.
    owner = (_req_email(request) or "").strip().lower() or None
    app_project = AppProject(
        conversation_id=conversation.id,
        title=title[:255],
        owner_email=owner,
        shared_emails=[owner] if owner else [],
    )
    session.add(app_project)
    await session.flush()
    app_project.slug = await _unique_slug(session, _slugify(title))
    await session.commit()
    await session.refresh(app_project)
    await log_event(
        "app.create", user_email=_req_email(request), app_id=app_project.id,
        message=f"App creada: {app_project.title}",
    )
    return app_project


@app.get("/api/apps/{app_id}", response_model=AppProjectDetail)
async def get_app(
    app_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    ap = await _app_for_request(session, request, app_id)
    ap.my_role = await _app_role(session, request, ap) or "viewer"
    return ap


@app.get("/api/apps/{app_id}/spec")
async def get_app_spec(
    app_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    """Devuelve la AppSpec (para el paso de revisión previo al deploy). null si no hay."""
    app_project = await _app_for_request(session, request, app_id)
    return {"spec": app_project.app_spec or None}


@app.put("/api/apps/{app_id}/spec")
async def update_app_spec(
    app_id: str, body: dict, request: Request, session: AsyncSession = Depends(get_session)
):
    """Guarda la AppSpec editada por el usuario (paso de revisión). Valida la spec y rechaza
    `location` placeholder/vacío (mismo guardrail que define_app)."""
    app_project = await _app_require_edit(session, request, app_id)
    from .agent.runner import _placeholder_locations
    from .builder.app_builder import AppSpec

    try:
        spec = AppSpec.model_validate(body.get("spec") if "spec" in body else body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Spec inválida: {exc}")
    bad = _placeholder_locations(spec)
    if bad:
        raise HTTPException(
            status_code=400,
            detail="Estas entidades tienen un location placeholder o vacío (usá id/ruta real): "
            + "; ".join(bad),
        )
    app_project.app_spec = spec.model_dump()
    await session.commit()
    await log_event(
        "app.update", user_email=_req_email(request), app_id=app_id,
        message=f"Spec editada: {spec.name}",
    )
    return {"spec": app_project.app_spec}


@app.patch("/api/apps/{app_id}", response_model=AppProjectDetail)
async def update_app(
    app_id: str,
    payload: AppProjectUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    app_project = await _app_require_edit(session, request, app_id)

    changed = [k for k, v in payload.model_dump(exclude_none=True).items()]

    if payload.title is not None:
        title = payload.title.strip()
        if title:
            app_project.title = title[:255]
    if payload.icon is not None:
        app_project.icon = (payload.icon or "")[:16] or None
    if payload.color is not None:
        app_project.color = (payload.color or "")[:16] or None
    if payload.status is not None:
        app_project.status = payload.status
    if payload.flow is not None:
        app_project.flow = payload.flow.model_dump()
    if payload.integrations is not None:
        app_project.integrations = payload.integrations.model_dump()

    await session.commit()
    await session.refresh(app_project)
    await log_event(
        "app.update", user_email=_req_email(request), app_id=app_id,
        message=f"App actualizada ({', '.join(changed) or 'sin cambios'})",
        meta={"fields": changed},
    )
    app_project.my_role = await _app_role(session, request, app_project) or "viewer"
    return app_project


@app.delete("/api/apps/{app_id}", status_code=204)
async def delete_app(
    app_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    app_project = await _app_require_owner(session, request, app_id)
    slug = app_project.slug
    title = app_project.title
    # DELETE a nivel Core sobre la conversación: el ON DELETE CASCADE de la BD
    # arrastra la app y los mensajes. Evita el lazy-loading de relaciones en async.
    await session.execute(
        delete(Conversation).where(Conversation.id == app_project.conversation_id)
    )
    await session.commit()
    await log_event(
        "app.delete", user_email=_req_email(request), app_id=app_id,
        message=f"App eliminada: {title}",
    )

    # Teardown de Docker: contenedor + imagen de la app (best-effort, no bloquea el borrado).
    if slug:
        try:
            await asyncio.to_thread(teardown_app, slug)
        except Exception:  # noqa: BLE001
            pass


async def _slug_taken(session, slug: str, exclude_id: str) -> bool:
    row = (
        await session.execute(select(AppProject).where(AppProject.slug == slug))
    ).scalar_one_or_none()
    return row is not None and row.id != exclude_id


@app.get("/api/apps/{app_id}/subdomain-check")
async def subdomain_check(
    app_id: str, request: Request, slug: str = "",
    session: AsyncSession = Depends(get_session),
):
    """Normaliza un subdominio propuesto e indica si está disponible (no usado por otra app)."""
    await _app_require_edit(session, request, app_id)
    normalized = _slugify(slug)
    available = bool(normalized) and not await _slug_taken(session, normalized, app_id)
    return {"slug": normalized, "available": available}


@app.post("/api/apps/{app_id}/deploy", response_model=AppProjectDetail)
async def deploy_app(
    app_id: str,
    request: Request,
    payload: dict | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Inicia (o re-ejecuta) el despliegue. Acepta `slug` (subdominio) opcional: debe ser
    único; si ya está en uso por otra app devuelve 409. Si no se envía, se conserva el
    actual o se genera del título."""
    app_project = await _app_require_edit(session, request, app_id)
    if app_project.deploy_state == "deploying":
        raise HTTPException(status_code=409, detail="La app ya se está desplegando.")

    requested = (payload or {}).get("slug")
    old_slug = app_project.slug
    if requested:
        normalized = _slugify(requested)
        if not normalized:
            raise HTTPException(status_code=400, detail="Subdominio inválido.")
        if await _slug_taken(session, normalized, app_project.id):
            raise HTTPException(
                status_code=409,
                detail=f"El subdominio '{normalized}' ya está en uso. Elegí otro.",
            )
        app_project.slug = normalized
    elif not app_project.slug:
        app_project.slug = await _unique_slug(
            session, _slugify(app_project.title), exclude_id=app_project.id
        )

    # Si cambió el subdominio y había un despliegue anterior, lo bajamos.
    if old_slug and old_slug != app_project.slug:
        try:
            await asyncio.to_thread(teardown_app, old_slug)
        except Exception:  # noqa: BLE001
            pass

    # Garantía de owner: si la app no tenía dueño (legacy) o aún no se auto-compartió,
    # el que despliega queda como dueño y con acceso permanente.
    email = _req_email(request)
    deployer = (email or "").strip().lower() or None
    if deployer:
        if not app_project.owner_email:
            app_project.owner_email = deployer
        shared = list(app_project.shared_emails or [])
        owner = (app_project.owner_email or "").strip().lower()
        if owner and owner not in shared:
            app_project.shared_emails = sorted({*shared, owner})

    app_project.deploy_state = "deploying"
    app_project.deploy_stage = "Iniciando…"
    await session.commit()
    await session.refresh(app_project)

    # Despliegue en el WORKER de Celery (no bloquea la API ni muere si la API reinicia).
    from .tasks.jobs import run_deploy_task

    force_full = bool((payload or {}).get("rebuild"))
    await log_event(
        "deploy.start", status="info", user_email=email, app_id=app_project.id,
        message=f"Despliegue iniciado ({app_project.slug})"
        + (" · reconstrucción total" if force_full else ""),
    )
    result = run_deploy_task.delay(app_project.id, app_project.slug, force_full, email or "")
    _deploy_tasks[app_project.id] = result.id
    app_project.my_role = await _app_role(session, request, app_project) or "viewer"
    return app_project


@app.post("/api/apps/{app_id}/deploy/cancel", status_code=200)
async def cancel_deploy(
    app_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    """Cancela un despliegue en curso (revoca la tarea Celery y deja la app en 'idle')."""
    await _app_require_edit(session, request, app_id)
    task_id = _deploy_tasks.pop(app_id, None)
    if task_id:
        try:
            from .tasks.celery_app import celery_app

            celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
        except Exception:  # noqa: BLE001
            pass
    ap = await session.get(AppProject, app_id)
    if ap is not None and ap.deploy_state == "deploying":
        ap.deploy_state, ap.deploy_stage = "idle", None
        await session.commit()
    try:
        from .builder.deploy_runner import publish_deploy

        await publish_deploy(app_id, {"type": "cancelled"})
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


@app.get("/api/apps/{app_id}/versions")
async def app_versions(
    app_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    """Historial de versiones desplegadas (commits del repo git local de la app)."""
    await _app_for_request(session, request, app_id)
    from .builder.app_repo import list_versions

    return await asyncio.to_thread(list_versions, app_id)


@app.post("/api/apps/{app_id}/rollback", response_model=AppProjectDetail)
async def app_rollback(
    app_id: str,
    request: Request,
    payload: dict = Body(...),
    session: AsyncSession = Depends(get_session),
):
    """Restaura una versión anterior (sha del historial) y la re-despliega (sin LLM)."""
    sha = (payload or {}).get("sha")
    if not sha:
        raise HTTPException(status_code=400, detail="Falta 'sha'.")
    app_project = await _app_require_edit(session, request, app_id)
    if app_project.deploy_state == "deploying":
        raise HTTPException(status_code=409, detail="La app ya se está desplegando.")
    if not app_project.slug:
        raise HTTPException(status_code=400, detail="La app no tiene subdominio desplegado.")

    from .builder.app_builder import AppSpec
    from .builder.app_repo import read_version

    ver = await asyncio.to_thread(read_version, app_id, sha)
    if not ver or not ver.get("spec"):
        raise HTTPException(status_code=404, detail="Versión no encontrada.")

    spec_obj = AppSpec.model_validate(ver["spec"])
    spec_hash = hashlib.sha256(spec_obj.model_dump_json().encode()).hexdigest()
    # Restauramos spec + código; el spec_hash coincide -> el deploy REUSA (sin LLM).
    app_project.app_spec = spec_obj.model_dump()
    app_project.build_artifacts = {
        "spec_hash": spec_hash,
        "main_py": ver["main_py"],
        "static_files": ver.get("static_files") or {},
        "backend_reqs": ver.get("backend_reqs", ""),
    }
    app_project.pending_edits = []
    app_project.deploy_state = "deploying"
    app_project.deploy_stage = "Restaurando versión…"
    await session.commit()
    await session.refresh(app_project)

    from .tasks.jobs import run_deploy_task

    email = _req_email(request)
    await log_event(
        "deploy.rollback", status="info", user_email=email, app_id=app_id,
        message=f"Restaurando versión {str(sha)[:12]} ({app_project.slug})",
        meta={"sha": sha},
    )
    result = run_deploy_task.delay(app_project.id, app_project.slug, False, email or "")
    _deploy_tasks[app_project.id] = result.id
    app_project.my_role = await _app_role(session, request, app_project) or "viewer"
    return app_project


@app.get("/api/apps/{app_id}/deploy/stream")
async def deploy_stream(
    app_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    """Stream SSE del progreso del deploy (Redis pub/sub, canal deploy:{app_id}).

    Emite primero el estado actual, luego cada evento; cierra al recibir done/error/cancelled.
    """
    import redis.asyncio as aioredis

    ap = await _app_for_request(session, request, app_id)
    current_stage = ap.deploy_stage if ap else None
    current_state = ap.deploy_state if ap else "idle"

    async def gen():
        # Estado actual (para que el cliente que se conecta tarde vea dónde va).
        if current_state == "deploying":
            yield f"data: {json.dumps({'type': 'stage', 'text': current_stage or 'Desplegando…'})}\n\n"
        elif current_state in ("deployed", "error", "idle"):
            # Nada en curso: informá el estado y cerrá.
            yield f"data: {json.dumps({'type': current_state})}\n\n"
            return

        r = aioredis.from_url(settings.redis_url)
        pubsub = r.pubsub()
        await pubsub.subscribe(f"deploy:{app_id}")
        try:
            async for msg in pubsub.listen():
                if msg["type"] != "message":
                    continue
                data = msg["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                yield f"data: {data}\n\n"
                evt = json.loads(data)
                if evt.get("type") in ("done", "error", "cancelled"):
                    break
        finally:
            await pubsub.unsubscribe(f"deploy:{app_id}")
            await pubsub.aclose()
            await r.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ===================== Enterprise: acceso (allowlist) + credenciales del dueño =====

def app_secret(app_id: str) -> str:
    """Secreto determinístico por-app (no se almacena). Lo usa la app desplegada para
    pedirle al builder la lista de acceso y los tokens del dueño."""
    return hmac.new(
        settings.session_secret.encode(), app_id.encode(), hashlib.sha256
    ).hexdigest()


def _require_app_secret(app_id: str, x_app_secret: str) -> None:
    if not x_app_secret or not hmac.compare_digest(x_app_secret, app_secret(app_id)):
        raise HTTPException(status_code=403, detail="Secreto de app inválido")


@app.get("/api/apps/{app_id}/shares")
async def get_shares(
    app_id: str, request: Request, session: AsyncSession = Depends(get_session)
):
    """Lista de compartidos con su rol (view|edit). El dueño viaja aparte (no-eliminable)."""
    ap = await _app_for_request(session, request, app_id)
    owner = (ap.owner_email or "").strip().lower() or None
    editors = {e.strip().lower() for e in (ap.editor_emails or [])}
    shares = [
        {"email": e, "role": "edit" if e in editors else "view"}
        for e in sorted(set(ap.shared_emails or []))
        if e != owner
    ]
    # `can_manage` indica si el solicitante puede editar la lista (solo owner/admin).
    can_manage = (await _app_role(session, request, ap)) in ("admin", "owner")
    return {"owner": owner, "shares": shares, "can_manage": can_manage}


@app.put("/api/apps/{app_id}/shares")
async def set_shares(
    app_id: str, body: dict, request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Reemplaza la lista de compartidos. Solo owner/admin. Cada item: {email, role:view|edit}.
    El dueño SIEMPRE conserva acceso (se reinyecta) y no es 'editor' (ya tiene control total)."""
    ap = await _app_require_owner(session, request, app_id)
    owner = (ap.owner_email or "").strip().lower() or None
    emails: set[str] = set()
    editors: set[str] = set()
    for item in body.get("shares") or []:
        e = (item.get("email") or "").strip().lower()
        if not e:
            continue
        emails.add(e)
        if (item.get("role") or "view").lower() == "edit":
            editors.add(e)
    if owner:
        emails.add(owner)
        editors.discard(owner)
    ap.shared_emails = sorted(emails)
    ap.editor_emails = sorted(editors)
    await session.commit()
    shares = [
        {"email": e, "role": "edit" if e in editors else "view"}
        for e in sorted(emails)
        if e != owner
    ]
    return {"owner": owner, "shares": shares, "can_manage": True}


@app.get("/api/apps/{app_id}/access")
async def app_access(
    app_id: str, email: str = "", x_app_secret: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
):
    """La app desplegada chequea acá (en runtime) si un correo tiene acceso (allowlist)."""
    _require_app_secret(app_id, x_app_secret)
    ap = await session.get(AppProject, app_id)
    if ap is None:
        return {"allowed": False}
    e = email.strip().lower()
    owner = (ap.owner_email or "").strip().lower()
    # El dueño siempre tiene acceso; el resto, si está en la allowlist.
    allowed = bool(e and (e == owner or e in (ap.shared_emails or [])))
    return {"allowed": allowed}


@app.get("/api/apps/{app_id}/owner-token/{provider}")
async def owner_token(
    app_id: str, provider: str, x_app_secret: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
):
    """Vende el token del DUEÑO para un conector (las apps usan las credenciales del dueño,
    no las del visor). Autorizado por el secreto por-app."""
    _require_app_secret(app_id, x_app_secret)
    from .mcp.connstore import get_conn, sub_for_email, use_user

    # Normaliza el provider a la CLAVE del catálogo (acepta guion o guion bajo):
    # google-docs == google_docs, google-sheets == google_sheets, etc.
    key = provider.replace("-", "_")

    # La app usa las credenciales del DUEÑO → resolvemos su user_sub.
    ap = await session.get(AppProject, app_id)
    owner_sub = await sub_for_email(session, ap.owner_email if ap else None)

    # google_sheets: reutiliza el refresh ya implementado (con el usuario = dueño).
    if key == "google_sheets":
        from .connectors import sheets as sheets_api

        try:
            with use_user(owner_sub):
                return {"access_token": await sheets_api._token(), "token_type": "Bearer"}
        except sheets_api.NotConnected:
            raise HTTPException(status_code=409, detail="El dueño no conectó Google Sheets.")

    row = await get_conn(session, key, owner_sub)
    if row is None:
        raise HTTPException(status_code=409, detail=f"El dueño no conectó {provider}.")

    if key == "slack":
        env = json.loads(decrypt(row.env_json)) if row.env_json else {}
        return {"access_token": env.get("SLACK_BOT_TOKEN", ""), "token_type": "Bearer"}

    # OAuth (Google docs/drive/calendar/gmail/bigquery, Notion): refresca si expiró usando los
    # datos guardados en la conexión + GOOGLE_CLIENT_SECRET. Notion no expira (no entra acá).
    token = decrypt(row.access_token) if row.access_token else ""
    now = datetime.now(timezone.utc)
    if row.expires_at and row.expires_at <= now and row.refresh_token:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    row.token_endpoint or "https://oauth2.googleapis.com/token",
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": decrypt(row.refresh_token),
                        "client_id": row.client_id or os.environ.get("GOOGLE_CLIENT_ID", ""),
                        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
                    },
                )
            tok = resp.json()
            if tok.get("access_token"):
                token = tok["access_token"]
                row.access_token = encrypt(token)
                row.expires_at = _expiry(tok)
                await session.commit()
        except Exception:  # noqa: BLE001
            pass
    return {"access_token": token, "token_type": "Bearer"}


# ===================== Agente: run desacoplado (POST + SSE) =====================

@app.post("/api/agent/run")
async def agent_run(body: dict, request: Request):
    """Inicia un run del agente en background (publica eventos a Redis) y devuelve los ids.
    El cliente abre luego el SSE `/api/agent/runs/{run_id}/stream` para ver el progreso.
    Desacoplado del transporte: el run sobrevive a desconexiones del navegador."""
    from .agent.run_service import start_run

    content = (body.get("content") or "").strip()
    attachments = body.get("attachments") or []
    if not content and not attachments:
        raise HTTPException(status_code=400, detail="Mensaje vacío.")
    user = getattr(request.state, "user", None) or {}
    return await start_run(
        content=content,
        attachments=attachments,
        conversation_id=body.get("conversation_id"),
        model=body.get("model"),
        user_email=_req_email(request),
        user_sub=user.get("sub") if isinstance(user, dict) else None,
    )


@app.get("/api/agent/active-run")
async def agent_active_run(conversation_id: str):
    """Devuelve el run_id activo de una conversación (si sigue corriendo), para reanudar el
    stream tras recargar la página."""
    from .agent.run_service import get_active_run

    return {"run_id": await get_active_run(conversation_id)}


@app.post("/api/agent/runs/{run_id}/cancel")
async def agent_run_cancel(run_id: str):
    """Cancela un run en curso (corta loops). Emite terminales al stream para cerrar el SSE."""
    from .agent.run_service import cancel_run

    return {"cancelled": await cancel_run(run_id)}


@app.get("/api/agent/runs/{run_id}/stream")
async def agent_run_stream(run_id: str, request: Request):
    """SSE de los eventos del run. Reanuda desde `Last-Event-ID` (replay del Redis Stream)."""
    from .agent.run_service import stream_events

    last_id = request.headers.get("last-event-id") or request.query_params.get("last_event_id", "")
    return StreamingResponse(
        stream_events(run_id, last_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ===================== Connector proxy (MCP) para apps desplegadas =====================

@app.post("/api/apps/{app_id}/mcp/{provider}/{tool}")
async def app_connector_proxy(
    app_id: str, provider: str, tool: str,
    body: dict | None = Body(default=None),
    x_app_secret: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
):
    """Ejecuta una tool de un conector MCP (Notion, etc.) con la conexión del DUEÑO. La app
    manda {"arguments": {...}} y la plataforma habla con el MCP server con el token del dueño
    (nunca expuesto a la app). Auth por X-App-Secret + auditoría en Logs."""
    from .mcp.proxy import ConnectorError, call_owner_tool
    from .mcp.connstore import sub_for_email, use_user

    _require_app_secret(app_id, x_app_secret)
    ap = await session.get(AppProject, app_id)
    owner_sub = await sub_for_email(session, ap.owner_email if ap else None)
    args = (body or {}).get("arguments")
    if args is None:
        args = {k: v for k, v in (body or {}).items() if k != "arguments"}
    try:
        with use_user(owner_sub):  # la app corre con la conexión del dueño
            res = await call_owner_tool(provider, tool, args)
    except ConnectorError as exc:
        await log_event("connector.call", status="error", app_id=app_id, provider=provider,
                        message=f"{provider}:{tool} — {exc}"[:240])
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        await log_event("connector.call", status="error", app_id=app_id, provider=provider,
                        message=f"{provider}:{tool} — error")
        raise HTTPException(status_code=502, detail=f"Error del conector: {exc}")
    await log_event(
        "connector.call", status="ok" if res.get("ok") else "error",
        app_id=app_id, provider=provider, message=f"{provider}:{tool}",
    )
    return res


# ===================== Proxy LLM para apps desplegadas =====================

@app.post("/api/apps/{app_id}/llm")
async def app_llm(
    app_id: str,
    body: dict,
    x_app_secret: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
):
    """Proxy LLM: la app desplegada manda model+messages y la plataforma llama al proveedor
    con SUS claves (nunca expuestas a la app). Autorizado por el secreto por-app, con tope
    diario anti-abuso y auditoría en Logs (sin guardar el prompt)."""
    from .llm import DEFAULT_MODEL, LLMError, complete

    _require_app_secret(app_id, x_app_secret)

    cap = settings.llm_daily_call_cap
    if cap > 0:
        since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        used = await session.scalar(
            select(func.count()).select_from(EventLog).where(
                EventLog.event_type == "llm.call",
                EventLog.app_id == app_id,
                EventLog.status == "ok",
                EventLog.created_at >= since,
            )
        )
        if (used or 0) >= cap:
            await log_event(
                "llm.call", status="error", app_id=app_id,
                message=f"Cuota diaria excedida ({cap})",
            )
            raise HTTPException(status_code=429, detail="Cuota diaria de LLM excedida para esta app.")

    model = body.get("model") or DEFAULT_MODEL
    try:
        result = await complete(
            model=model,
            messages=body.get("messages") or [],
            system=body.get("system"),
            max_tokens=int(body.get("max_tokens") or 1024),
            temperature=body.get("temperature"),
        )
    except LLMError as exc:
        await log_event("llm.call", status="error", app_id=app_id, message=f"{model}: {exc}"[:280])
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        await log_event("llm.call", status="error", app_id=app_id, message=f"{model}: error de proveedor")
        raise HTTPException(status_code=502, detail=f"Error del proveedor LLM: {exc}")

    await log_event(
        "llm.call", app_id=app_id, provider=result["provider"], message=result["model"],
        meta={
            "input_tokens": result["usage"]["input_tokens"],
            "output_tokens": result["usage"]["output_tokens"],
        },
    )
    return result


# ===================== Dashboard de Google Sheet =====================

@app.get("/api/dashboards/{app_id}/data")
async def dashboard_data(
    app_id: str,
    user: dict = Depends(current_user),  # cualquier usuario con sesión (SSO) puede ver
    session: AsyncSession = Depends(get_session),
):
    """Sirve los datos de la Sheet configurada (leída con el token del dueño) + la config.

    El dashboard desplegado (SSO) llama acá; los datos son de la Sheet fija configurada.
    """
    app_project = await session.get(AppProject, app_id)
    cfg = app_project.dashboard if app_project else None
    if not cfg:
        raise HTTPException(status_code=404, detail="Este dashboard no está configurado.")

    from .connectors import sheets as sheets_api
    from .mcp.connstore import sub_for_email, use_user

    owner_sub = await sub_for_email(session, app_project.owner_email if app_project else None)
    rng = cfg.get("range") or ""
    try:
        with use_user(owner_sub):  # se lee con la Sheet del DUEÑO
            if not rng:
                meta = await sheets_api.metadata(cfg["spreadsheet_id"])
                rng = meta["sheets"][0]["title"] if meta["sheets"] else "Sheet1"
            rows = await sheets_api.read_range(cfg["spreadsheet_id"], rng)
    except sheets_api.NotConnected:
        raise HTTPException(status_code=409, detail="Google Sheets no está conectado en el builder.")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"No se pudo leer la Sheet: {exc}")

    headers = rows[0] if rows else []
    data = rows[1:] if len(rows) > 1 else []
    return {"title": cfg.get("title", app_project.title), "headers": headers, "rows": data,
            "config": cfg}


# ===================== Conectores (OAuth2.0) =====================

@app.get("/api/connectors", response_model=list[ConnectorInfo])
async def list_connectors():
    return [
        ConnectorInfo(id=p.id, label=p.label, configured=is_configured(p))
        for p in PROVIDERS.values()
    ]


@app.get("/api/apps/{app_id}/me/connections", response_model=list[ConnectionOut])
async def my_connections(
    app_id: str,
    user: dict = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(Connection).where(
                Connection.app_id == app_id, Connection.user_sub == user["sub"]
            )
        )
    ).scalars().all()
    return rows


@app.delete("/api/apps/{app_id}/me/connections/{provider}", status_code=204)
async def delete_my_connection(
    app_id: str,
    provider: str,
    user: dict = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await session.execute(
        delete(Connection).where(
            Connection.app_id == app_id,
            Connection.user_sub == user["sub"],
            Connection.provider == provider,
        )
    )
    await session.commit()


@app.get("/api/apps/{app_id}/connectors/{provider}/authorize")
async def connector_authorize(
    app_id: str,
    provider: str,
    session_token: str = Query("", alias="session"),
    session: AsyncSession = Depends(get_session),
):
    # La sesión del usuario final viaja por query (es navegación de popup, no XHR).
    user = decode_jwt(session_token, settings.session_secret) if session_token else None
    if not user or not user.get("sub"):
        raise HTTPException(status_code=401, detail="Sesión requerida")

    prov = PROVIDERS.get(provider)
    if prov is None:
        raise HTTPException(status_code=404, detail="Conector desconocido")
    if not is_configured(prov):
        raise HTTPException(
            status_code=400,
            detail=f"{prov.label} no está configurado (faltan {prov.client_id_env}/"
            f"{prov.client_secret_env} en el entorno).",
        )
    if await session.get(AppProject, app_id) is None:
        raise HTTPException(status_code=404, detail="App no encontrada")

    now = time.time()
    for k in [k for k, v in _oauth_states.items() if now - v[3] > 600]:
        _oauth_states.pop(k, None)
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = (app_id, user["sub"], provider, now)

    redirect_uri = f"{PUBLIC_BASE_URL}/api/connectors/callback"
    return RedirectResponse(build_authorize_url(prov, redirect_uri, state))


def _extract_account(provider_id: str, token: dict) -> str | None:
    if provider_id == "slack":
        return (token.get("team") or {}).get("name") or (
            token.get("authed_user") or {}
        ).get("id")
    if provider_id == "notion":
        return token.get("workspace_name")
    return token.get("email")


async def _post_token(prov, payload: dict) -> dict:
    cid = os.environ.get(prov.client_id_env, "")
    secret = os.environ.get(prov.client_secret_env, "")
    auth = None
    if prov.token_auth == "basic":
        auth = (cid, secret)
    else:
        payload = {**payload, "client_id": cid, "client_secret": secret}
    data = payload if prov.token_body == "form" else None
    json_body = payload if prov.token_body == "json" else None
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            prov.token_url,
            data=data,
            json=json_body,
            headers={"Accept": "application/json"},
            auth=auth,
        )
    resp.raise_for_status()
    return resp.json()


def _expiry(token: dict) -> datetime | None:
    if token.get("expires_in"):
        return datetime.now(timezone.utc) + timedelta(seconds=int(token["expires_in"]))
    return None


def _popup_html(message: str, ok: bool) -> HTMLResponse:
    color = "#138a57" if ok else "#d94343"
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>Conector</title>
<style>body{{font-family:system-ui;display:grid;place-items:center;height:100vh;margin:0;
background:#f7f8fb;color:#171a21}}.c{{text-align:center}}.d{{color:{color};font-weight:700}}</style>
</head><body><div class="c"><p class="d">{message}</p><p>Puedes cerrar esta ventana.</p></div>
<script>try{{window.opener&&window.opener.postMessage("connector:refresh","*")}}catch(e){{}}
setTimeout(function(){{window.close()}},1200)</script></body></html>"""
    )


@app.get("/api/connectors/callback")
async def connector_callback(
    state: str = "", code: str = "", error: str = "",
    session: AsyncSession = Depends(get_session),
):
    if error:
        return _popup_html(f"Autorización cancelada: {error}", ok=False)
    entry = _oauth_states.pop(state, None)
    if entry is None:
        return _popup_html("Estado OAuth inválido o expirado.", ok=False)
    app_id, user_sub, provider, _ = entry
    prov = PROVIDERS.get(provider)
    if prov is None:
        return _popup_html("Conector desconocido.", ok=False)

    try:
        redirect_uri = f"{PUBLIC_BASE_URL}/api/connectors/callback"
        token = await _post_token(
            prov,
            {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        )
    except Exception as exc:  # noqa: BLE001
        return _popup_html(f"Error al intercambiar el código: {exc}", ok=False)

    access_token = token.get("access_token")
    if not access_token:
        return _popup_html("El proveedor no devolvió access_token.", ok=False)

    existing = (
        await session.execute(
            select(Connection).where(
                Connection.app_id == app_id,
                Connection.user_sub == user_sub,
                Connection.provider == provider,
            )
        )
    ).scalar_one_or_none()
    fields = dict(
        account=_extract_account(provider, token),
        access_token=encrypt(access_token),
        refresh_token=encrypt(token.get("refresh_token")),
        token_type=token.get("token_type"),
        scope=token.get("scope"),
        expires_at=_expiry(token),
    )
    if existing is None:
        session.add(Connection(app_id=app_id, user_sub=user_sub, provider=provider, **fields))
    else:
        for k, v in fields.items():
            setattr(existing, k, v)
    await session.commit()
    return _popup_html(f"{prov.label} conectado correctamente.", ok=True)


@app.get("/api/apps/{app_id}/me/connections/{provider}/token")
async def connection_token(
    app_id: str,
    provider: str,
    user: dict = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Broker: devuelve un access token válido del usuario (refresca si expiró).

    Es lo que el backend de la app desplegada llama para usar el proveedor con los
    permisos exactos de la cuenta del usuario (ni más ni menos)."""
    conn = (
        await session.execute(
            select(Connection).where(
                Connection.app_id == app_id,
                Connection.user_sub == user["sub"],
                Connection.provider == provider,
            )
        )
    ).scalar_one_or_none()
    if conn is None:
        raise HTTPException(status_code=404, detail="El usuario no ha conectado ese servicio")

    access_token = decrypt(conn.access_token)
    now = datetime.now(timezone.utc)
    if conn.expires_at and conn.expires_at <= now and conn.refresh_token:
        prov = PROVIDERS.get(provider)
        try:
            token = await _post_token(
                prov,
                {"grant_type": "refresh_token", "refresh_token": decrypt(conn.refresh_token)},
            )
            access_token = token.get("access_token", access_token)
            conn.access_token = encrypt(access_token)
            if token.get("refresh_token"):
                conn.refresh_token = encrypt(token["refresh_token"])
            conn.expires_at = _expiry(token) or conn.expires_at
            await session.commit()
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=502, detail="No se pudo refrescar el token")

    return {
        "provider": provider,
        "access_token": access_token,
        "token_type": conn.token_type,
        "expires_at": conn.expires_at.isoformat() if conn.expires_at else None,
    }


# ===================== Gateway de login (Google SSO compartido) =====================

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
# state -> (return_to, created_ts)
_login_states: dict[str, tuple[str, float]] = {}


def _require_admin(request: Request) -> None:
    user = getattr(request.state, "user", None)
    email = user.get("email") if isinstance(user, dict) else None
    if not settings.is_admin(email):
        raise HTTPException(status_code=403, detail="Solo administradores")


async def _is_admin_db(session: AsyncSession, email: str | None) -> bool:
    """Admin = correo en ADMIN_EMAILS (env, bootstrap) o `users.role == 'admin'` (DB).
    La DB es la fuente de verdad en runtime: una degradación aplica de inmediato (no espera
    a que expire el JWT)."""
    if settings.is_admin(email):
        return True
    norm = (email or "").strip().lower()
    if not norm:
        return False
    u = await session.get(User, norm)
    return bool(u and u.role == "admin")


async def _require_admin_db(request: Request, session: AsyncSession) -> str:
    """403 si el usuario no es admin (chequeo contra DB). Devuelve el email normalizado."""
    email = (_req_email(request) or "").strip().lower()
    if not await _is_admin_db(session, email):
        raise HTTPException(status_code=403, detail="Solo administradores")
    return email


async def _app_role(
    session: AsyncSession, request: Request, ap: AppProject
) -> str | None:
    """Rol del solicitante sobre la app: admin | owner | editor | viewer, o None si sin acceso."""
    email = (_req_email(request) or "").strip().lower()
    if await _is_admin_db(session, email):
        return "admin"
    if not email:
        return None
    if email == (ap.owner_email or "").strip().lower():
        return "owner"
    shared = [e.strip().lower() for e in (ap.shared_emails or [])]
    if email not in shared:
        return None
    editors = [e.strip().lower() for e in (ap.editor_emails or [])]
    return "editor" if email in editors else "viewer"


async def _app_for_request(
    session: AsyncSession, request: Request, app_id: str
) -> AppProject:
    """Acceso de LECTURA: admin / owner / editor / viewer (cualquiera con acceso). 404/403 si no."""
    ap = await session.get(AppProject, app_id)
    if ap is None:
        raise HTTPException(status_code=404, detail="App no encontrada")
    if await _app_role(session, request, ap) is None:
        raise HTTPException(status_code=403, detail="Sin acceso a esta app")
    return ap


async def _app_require_edit(
    session: AsyncSession, request: Request, app_id: str
) -> AppProject:
    """Permiso de EDICIÓN (desplegar/editar): admin / owner / editor. Viewers → 403."""
    ap = await session.get(AppProject, app_id)
    if ap is None:
        raise HTTPException(status_code=404, detail="App no encontrada")
    role = await _app_role(session, request, ap)
    if role in ("admin", "owner", "editor"):
        return ap
    if role is None:
        raise HTTPException(status_code=403, detail="Sin acceso a esta app")
    raise HTTPException(status_code=403, detail="Necesitas permiso de edición sobre esta app")


async def _app_require_owner(
    session: AsyncSession, request: Request, app_id: str
) -> AppProject:
    """Acciones reservadas (eliminar / gestionar compartidos): solo admin u owner."""
    ap = await session.get(AppProject, app_id)
    if ap is None:
        raise HTTPException(status_code=404, detail="App no encontrada")
    role = await _app_role(session, request, ap)
    if role in ("admin", "owner"):
        return ap
    if role is None:
        raise HTTPException(status_code=403, detail="Sin acceso a esta app")
    raise HTTPException(
        status_code=403, detail="Solo el dueño o un admin pueden hacer esto"
    )


def _parse_dt(s: str, end: bool = False):
    try:
        if len(s) == 10:  # YYYY-MM-DD -> abarca todo el día
            s = s + ("T23:59:59" if end else "T00:00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


@app.get("/api/logs")
async def list_logs(
    request: Request,
    type: str = "",
    status: str = "",
    user: str = "",
    app_id: str = "",
    q: str = "",
    date_from: str = Query("", alias="from"),
    date_to: str = Query("", alias="to"),
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """Bitácora de eventos (rol admin). Filtros: tipo, estado, usuario, app, texto y rango."""
    await _require_admin_db(request, session)
    conds = []
    if type:
        conds.append(EventLog.event_type == type)
    if status:
        conds.append(EventLog.status == status)
    if user:
        conds.append(EventLog.user_email == user)
    if app_id:
        conds.append(EventLog.app_id == app_id)
    if q:
        conds.append(EventLog.message.ilike(f"%{q}%"))
    df = _parse_dt(date_from) if date_from else None
    dt_ = _parse_dt(date_to, end=True) if date_to else None
    if df:
        conds.append(EventLog.created_at >= df)
    if dt_:
        conds.append(EventLog.created_at <= dt_)

    base = select(EventLog).where(*conds) if conds else select(EventLog)
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar() or 0
    rows = (
        await session.execute(
            base.order_by(EventLog.created_at.desc())
            .limit(min(max(limit, 1), 500))
            .offset(max(offset, 0))
        )
    ).scalars().all()
    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "type": r.event_type,
                "status": r.status,
                "user": r.user_email,
                "app_id": r.app_id,
                "provider": r.provider,
                "message": r.message,
                "meta": r.meta,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@app.get("/api/logs/facets")
async def log_facets(request: Request, session: AsyncSession = Depends(get_session)):
    """Valores distintos para poblar los filtros del panel de Logs."""
    await _require_admin_db(request, session)

    async def distinct(col):
        return [v for v in (await session.execute(select(col).distinct())).scalars().all() if v]

    return {
        "types": sorted(await distinct(EventLog.event_type)),
        "statuses": sorted(await distinct(EventLog.status)),
        "users": sorted(await distinct(EventLog.user_email)),
    }


# ===================== Sesión actual + gestión de usuarios (admin) =====================

@app.get("/api/me")
async def whoami(request: Request, session: AsyncSession = Depends(get_session)):
    """Datos de la sesión con el rol FRESCO desde DB (el front refresca la nav sin re-login)."""
    user = getattr(request.state, "user", None) or {}
    email = (_req_email(request) or "").strip().lower()
    is_admin = await _is_admin_db(session, email)
    return {
        "email": user.get("email"),
        "name": user.get("name"),
        "picture": user.get("picture"),
        "is_admin": is_admin,
        "role": "admin" if is_admin else "member",
    }


@app.get("/api/users")
async def list_users(
    request: Request,
    q: str = "",
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """Lista paginada de usuarios con su rol y nº de apps (solo admin). Escala a 10k+."""
    await _require_admin_db(request, session)
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    base = select(User)
    if q.strip():
        like = f"%{q.strip()}%"
        base = base.where(or_(User.email.ilike(like), User.name.ilike(like)))

    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar() or 0
    admins = (
        await session.execute(
            select(func.count()).select_from(User).where(User.role == "admin")
        )
    ).scalar() or 0
    users = (
        await session.execute(base.order_by(User.email).limit(limit).offset(offset))
    ).scalars().all()

    # Conteo de apps por dueño SOLO de los usuarios de esta página (no escanea todo).
    emails = [u.email for u in users]
    counts: dict[str, int] = {}
    if emails:
        counts = dict(
            (
                await session.execute(
                    select(AppProject.owner_email, func.count())
                    .where(AppProject.owner_email.in_(emails))
                    .group_by(AppProject.owner_email)
                )
            ).all()
        )
    env_admins = set(settings.admin_email_list)
    return {
        "items": [
            {
                "email": u.email,
                "name": u.name,
                "picture": u.picture,
                "role": u.role,
                "is_env_admin": u.email in env_admins,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                "apps_count": int(counts.get(u.email, 0) or 0),
            }
            for u in users
        ],
        "total": total,
        "admins": admins,
        "limit": limit,
        "offset": offset,
    }


@app.put("/api/users/{email}/role")
async def set_user_role(
    email: str, body: dict, request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Cambia el rol de un usuario (admin|member). Lo único que se bloquea es degradar a un
    admin permanente (correo en ADMIN_EMAILS): es el salvavidas que evita quedarse sin admins,
    porque esos correos siempre vuelven a ser admin al iniciar sesión (bootstrap). Por eso SÍ
    se permite que un admin (incluido uno mismo) baje a member: la recuperación está garantizada."""
    me = await _require_admin_db(request, session)
    role = (body or {}).get("role", "").strip().lower()
    if role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="Rol inválido (admin|member).")
    target = email.strip().lower()
    u = await session.get(User, target)
    if u is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    if role == "member" and settings.admin_email_list and target in settings.admin_email_list:
        raise HTTPException(
            status_code=400, detail="Ese correo es admin permanente (ADMIN_EMAILS)."
        )
    u.role = role
    await session.commit()
    await log_event(
        "user.role", status="info", user_email=me,
        message=f"Rol de {target} → {role}", meta={"target": target, "role": role},
    )
    return {"email": u.email, "role": u.role}


@app.get("/auth/google/login")
async def google_login(return_to: str = Query("/", alias="return_to")):
    cid = os.environ.get("GOOGLE_CLIENT_ID")
    secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not (cid and secret):
        raise HTTPException(
            status_code=400,
            detail="Login con Google no configurado (faltan GOOGLE_CLIENT_ID/SECRET).",
        )
    now = time.time()
    for k in [k for k, v in _login_states.items() if now - v[1] > 600]:
        _login_states.pop(k, None)
    state = secrets.token_urlsafe(24)
    _login_states[state] = (return_to, now)
    params = {
        "client_id": cid,
        "redirect_uri": f"{settings.public_base_url}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@app.get("/auth/google/callback")
async def google_callback(
    state: str = "", code: str = "", error: str = "",
    session: AsyncSession = Depends(get_session),
):
    if error:
        return HTMLResponse(f"<p>Login cancelado: {error}</p>", status_code=400)
    entry = _login_states.pop(state, None)
    if entry is None:
        return HTMLResponse("<p>Estado de login inválido o expirado.</p>", status_code=400)
    return_to, _ = entry

    cid = os.environ.get("GOOGLE_CLIENT_ID", "")
    secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    redirect_uri = f"{settings.public_base_url}/auth/google/callback"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            tok = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": cid,
                    "client_secret": secret,
                },
            )
            tok.raise_for_status()
            access = tok.json().get("access_token")
            ui = await client.get(
                GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access}"}
            )
            ui.raise_for_status()
            info = ui.json()
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(f"<p>Error de login: {exc}</p>", status_code=400)

    email = info.get("email")
    # Upsert del usuario (alta en primer login + actualización de perfil/last_login). Los correos
    # en ADMIN_EMAILS (env) son admin permanente: se fuerzan a admin acá (bootstrap, no degradables).
    norm_email = (email or "").strip().lower()
    is_admin = False
    if norm_email:
        u = await session.get(User, norm_email)
        env_admin = settings.is_admin(email)
        if u is None:
            u = User(email=norm_email, role="admin" if env_admin else "member")
            session.add(u)
        u.sub = info.get("sub")
        u.name = info.get("name")
        u.picture = info.get("picture")
        u.last_login_at = datetime.now(timezone.utc)
        if env_admin:
            u.role = "admin"
        await session.commit()
        is_admin = u.role == "admin"
    session_jwt = encode_jwt(
        {
            "sub": info.get("sub"),
            "email": email,
            "name": info.get("name"),
            "picture": info.get("picture"),
            "is_admin": is_admin,
        },
        settings.session_secret,
    )
    await log_event(
        "auth.login", status="info", user_email=email,
        message=f"Inicio de sesión: {info.get('name') or email}",
    )
    return RedirectResponse(f"{return_to}#token={session_jwt}")


# ===================== Conexión MCP del agente (OAuth 2.1 + DCR + PKCE) =====================

# state -> dict(provider, code_verifier, client_id, client_secret, token_endpoint, redirect_uri, resource, ts)
_mcp_states: dict[str, dict] = {}


@app.get("/api/mcp/connections")
async def mcp_connections(
    request: Request, session: AsyncSession = Depends(get_session)
):
    from .mcp.catalog import catalog_list

    # Estado de conexión SOLO del usuario actual (conectores por-usuario).
    user = getattr(request.state, "user", None) or {}
    sub = user.get("sub") if isinstance(user, dict) else None
    oauth_connected = (
        set(
            (
                await session.execute(
                    select(McpConnection.provider).where(McpConnection.user_sub == sub)
                )
            ).scalars().all()
        )
        if sub
        else set()
    )
    out = []
    for s in catalog_list():
        if s.auth == "oauth":
            connected = s.key in oauth_connected
        elif s.auth == "env":
            # self_hosted: "listo" si sus credenciales están presentes en el entorno.
            connected = bool(s.env) and all(os.environ.get(v) for v in s.env)
        else:
            connected = True
        out.append(
            {
                "provider": s.key,
                "label": s.label,
                "brand": s.brand,
                "transport": s.transport,
                "auth": s.auth,
                "connected": connected,
                "needs_env": list(s.env) if s.auth == "env" else [],
                "workspace_only": s.workspace_only,
            }
        )
    return out


@app.get("/api/mcp/{provider}/connect")
async def mcp_connect(
    provider: str, token: str = "", session: AsyncSession = Depends(get_session)
):
    from .mcp.catalog import load_catalog

    # Popup de OAuth (navegación, sin header): el token viaja por query para identificar al
    # usuario que conecta. La conexión es POR-USUARIO → guardamos su sub/email en el state.
    claims = (decode_jwt(token, settings.session_secret) or {}) if token else {}
    connect_email = claims.get("email")
    connect_sub = claims.get("sub")
    if not connect_sub:
        raise HTTPException(status_code=401, detail="Sesión inválida para conectar.")
    spec = load_catalog().get(provider)

    # OAuth estándar (broker): self-hosted con auth oauth (Slack, etc.) -> user token.
    if spec is not None and spec.auth == "oauth" and spec.oauth.get("provider_id"):
        prov = PROVIDERS.get(spec.oauth["provider_id"])
        if prov is None or not is_configured(prov):
            raise HTTPException(
                status_code=400,
                detail=f"{spec.label} no está configurado (faltan {provider.upper()}_CLIENT_ID/SECRET).",
            )
        now = time.time()
        for k in [k for k, v in _mcp_states.items() if now - v["ts"] > 600]:
            _mcp_states.pop(k, None)
        state = secrets.token_urlsafe(24)
        _mcp_states[state] = {
            "provider": provider, "std_oauth": True, "ts": now,
            "user_email": connect_email, "user_sub": connect_sub,
        }
        redirect_uri = f"{settings.public_base_url}/api/mcp/oauth/callback"
        return RedirectResponse(build_authorize_url(prov, redirect_uri, state))

    reg = MCP_REGISTRY.get(provider)
    if reg is None:
        raise HTTPException(status_code=404, detail="MCP desconocido")
    server_url = reg[0]
    if not server_url:
        env_var = f"{provider.upper()}_MCP_URL"
        raise HTTPException(
            status_code=400,
            detail=f"Configura la URL del MCP de {reg[1]} en la variable {env_var}.",
        )

    meta = await mcp_oauth.discover(server_url)
    if not meta or not meta.get("authorization_endpoint"):
        raise HTTPException(status_code=400, detail="No se pudo descubrir el OAuth del MCP")

    redirect_uri = f"{settings.public_base_url}/api/mcp/callback"
    try:
        client_id, client_secret = await mcp_oauth.register_client(
            meta["registration_endpoint"], redirect_uri
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Registro de cliente MCP falló: {exc}")

    verifier, challenge = mcp_oauth.pkce()
    now = time.time()
    for k in [k for k, v in _mcp_states.items() if now - v["ts"] > 600]:
        _mcp_states.pop(k, None)
    state = secrets.token_urlsafe(24)
    _mcp_states[state] = {
        "provider": provider,
        "code_verifier": verifier,
        "client_id": client_id,
        "client_secret": client_secret,
        "token_endpoint": meta["token_endpoint"],
        "redirect_uri": redirect_uri,
        "resource": server_url,
        "ts": now,
        "user_email": connect_email,
        "user_sub": connect_sub,
    }
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": server_url,
    }
    return RedirectResponse(f"{meta['authorization_endpoint']}?{urlencode(params)}")


def _dig(data: dict, path: str):
    """Resuelve un dot-path (p.ej. 'authed_user.access_token') sobre un dict anidado."""
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _build_mcp_env(spec, token: dict) -> dict[str, str]:
    """Construye el env del server stdio desde la respuesta del token OAuth.

    - env_map (Slack): mapea dot-paths del token -> env vars.
    - creds_format=gdrive (Google): arma el archivo de credenciales del server de Google.
    """
    fmt = spec.oauth.get("creds_format")
    if fmt == "gdrive":
        access = token.get("access_token")
        if not access:
            raise ValueError("Google no devolvió access_token")
        expires_in = int(token.get("expires_in", 3600))
        creds = {
            "access_token": access,
            "refresh_token": token.get("refresh_token"),
            "scope": token.get("scope"),
            "token_type": token.get("token_type", "Bearer"),
            "expiry_date": int((time.time() + expires_in) * 1000),
        }
        return {"GDRIVE_CREDS_JSON": json.dumps(creds)}

    env_map: dict[str, str] = spec.oauth.get("env_map", {})
    env = {k: _dig(token, path) for k, path in env_map.items()}
    if not all(env.values()):
        raise ValueError(f"faltan campos del token ({', '.join(env_map.values())})")
    return {k: str(v) for k, v in env.items()}


@app.get("/api/mcp/oauth/callback")
async def mcp_oauth_callback(
    state: str = "", code: str = "", error: str = "",
    session: AsyncSession = Depends(get_session),
):
    """Callback OAuth estándar (broker) para MCP self-hosted (Slack, etc.)."""
    from .mcp.catalog import load_catalog

    if error:
        return _popup_html(f"Autorización MCP cancelada: {error}", ok=False)
    st = _mcp_states.pop(state, None)
    if st is None or not st.get("std_oauth"):
        return _popup_html("Estado MCP inválido o expirado.", ok=False)

    spec = load_catalog().get(st["provider"])
    prov = PROVIDERS.get(spec.oauth["provider_id"]) if spec else None
    if spec is None or prov is None:
        return _popup_html("MCP desconocido.", ok=False)

    try:
        redirect_uri = f"{settings.public_base_url}/api/mcp/oauth/callback"
        token = await _post_token(
            prov,
            {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        )
    except Exception as exc:  # noqa: BLE001
        return _popup_html(f"Error al intercambiar el código MCP: {exc}", ok=False)

    access = token.get("access_token")
    if not access:
        return _popup_html(f"OAuth de {spec.label}: no devolvió access_token.", ok=False)

    # Solo los self_hosted necesitan un contenedor (con env del token). hosted (URL pública
    # como BigQuery) y api (Sheets) no: solo guardamos el token.
    needs_container = spec.transport == "self_hosted"
    env: dict[str, str] = {}
    if needs_container:
        try:
            env = _build_mcp_env(spec, token)
        except Exception as exc:  # noqa: BLE001
            return _popup_html(f"OAuth de {spec.label}: {exc}", ok=False)

    st_sub = st.get("user_sub")
    existing = (
        await session.execute(
            select(McpConnection).where(
                McpConnection.provider == st["provider"],
                McpConnection.user_sub == st_sub,
            )
        )
    ).scalar_one_or_none()
    fields = dict(
        user_sub=st_sub,
        user_email=st.get("user_email"),
        access_token=encrypt(access),
        # Guardamos refresh + cliente para refrescar (Sheets API directa; conector hosted).
        refresh_token=encrypt(token["refresh_token"]) if token.get("refresh_token") else
        (existing.refresh_token if existing else None),
        client_id=os.environ.get(prov.client_id_env),
        token_endpoint=prov.token_url,
        env_json=encrypt(json.dumps(env)) if needs_container else None,
        resource=prov.token_url,
        expires_at=_expiry(token),
    )
    if existing is None:
        session.add(McpConnection(provider=st["provider"], **fields))
    else:
        for k, v in fields.items():
            setattr(existing, k, v)
    await session.commit()
    await log_event(
        "mcp.connect", user_email=st.get("user_email"), provider=st["provider"],
        message=f"MCP conectado: {st['provider']}",
    )

    if needs_container:
        # Pre-calienta el contenedor del usuario para que el agente lo tenga listo.
        try:
            from .mcp.pool import ensure_server

            await ensure_server(spec, {k: str(v) for k, v in env.items()}, st.get("user_sub"))
        except Exception:  # noqa: BLE001
            pass
    # Invalida el cache de tools puenteadas para que el agente vea el cambio enseguida.
    from .mcp import bridge as mcp_bridge

    mcp_bridge.invalidate()
    return _popup_html(f"{spec.label} conectado por OAuth.", ok=True)


@app.get("/api/mcp/callback")
async def mcp_callback(
    state: str = "", code: str = "", error: str = "",
    session: AsyncSession = Depends(get_session),
):
    if error:
        return _popup_html(f"Autorización MCP cancelada: {error}", ok=False)
    st = _mcp_states.pop(state, None)
    if st is None:
        return _popup_html("Estado MCP inválido o expirado.", ok=False)

    try:
        token = await mcp_oauth.exchange_code(
            st["token_endpoint"], code, st["redirect_uri"], st["client_id"],
            st["code_verifier"], st["resource"], st.get("client_secret"),
        )
    except Exception as exc:  # noqa: BLE001
        return _popup_html(f"Error al intercambiar el código MCP: {exc}", ok=False)

    access_token = token.get("access_token")
    if not access_token:
        return _popup_html("El MCP no devolvió access_token.", ok=False)

    st_sub = st.get("user_sub")
    existing = (
        await session.execute(
            select(McpConnection).where(
                McpConnection.provider == st["provider"],
                McpConnection.user_sub == st_sub,
            )
        )
    ).scalar_one_or_none()
    fields = dict(
        user_sub=st_sub,
        user_email=st.get("user_email"),
        access_token=encrypt(access_token),
        refresh_token=encrypt(token.get("refresh_token")),
        client_id=st["client_id"],
        token_endpoint=st["token_endpoint"],
        resource=st["resource"],
        expires_at=_expiry(token),
    )
    if existing is None:
        session.add(McpConnection(provider=st["provider"], **fields))
    else:
        for k, v in fields.items():
            setattr(existing, k, v)
    await session.commit()
    await log_event(
        "mcp.connect", user_email=st.get("user_email"), provider=st["provider"],
        message=f"MCP conectado: {st['provider']}",
    )
    label = MCP_REGISTRY.get(st["provider"], ("", st["provider"], ""))[1]
    return _popup_html(f"{label} (MCP) conectado al agente.", ok=True)


@app.delete("/api/mcp/{provider}", status_code=204)
async def mcp_disconnect(
    provider: str, request: Request, session: AsyncSession = Depends(get_session)
):
    # Conector POR-USUARIO: cada quien desconecta SOLO el suyo (sin gate admin).
    user = getattr(request.state, "user", None) or {}
    sub = user.get("sub") if isinstance(user, dict) else None
    if not sub:
        raise HTTPException(status_code=401, detail="Sesión requerida.")
    await session.execute(
        delete(McpConnection).where(
            McpConnection.provider == provider, McpConnection.user_sub == sub
        )
    )
    await session.commit()
    await log_event(
        "mcp.disconnect", user_email=_req_email(request), provider=provider,
        message=f"MCP desconectado: {provider}",
    )
    # Reapea el contenedor MCP de ESTE usuario (si era self-hosted oauth).
    try:
        from .mcp.pool import remove_server

        await remove_server(provider, sub)
    except Exception:  # noqa: BLE001
        pass
    from .mcp import bridge as mcp_bridge

    mcp_bridge.invalidate()
