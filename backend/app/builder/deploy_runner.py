"""Pipeline de deploy (reusable por la API y por el worker de Celery).

Se ejecuta como tarea de Celery (no en el proceso de la API) para: no bloquear la API,
sobrevivir a reinicios/--reload, y ser cancelable/retriable. Publica el progreso por Redis
pub/sub (canal deploy:{app_id}) que consume el endpoint SSE.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import traceback

logger = logging.getLogger("cashea.deploy")

from sqlalchemy import select

from ..agent import runner
from .codegen import generate_app_files
from ..core.config import settings
from ..core.db import SessionLocal, engine
from .deploy import build_and_run
from ..core.models import AppProject, Message


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _transcript(rows: list[Message]) -> str:
    lines = [
        f"{m.role.upper()}: {_message_text(m.content).strip()}"
        for m in rows
        if _message_text(m.content).strip()
    ]
    return "\n\n".join(lines)[:12000]


async def publish_deploy(app_id: str, event: dict) -> None:
    """Publica un evento de progreso a Redis (canal deploy:{app_id}) para el stream SSE."""
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url)
        await r.publish(f"deploy:{app_id}", json.dumps(event))
        await r.aclose()
    except Exception:  # noqa: BLE001
        pass


async def run_deploy(
    app_id: str, slug: str, force_full: bool = False, user_email: str = ""
) -> None:
    """Genera, construye (QA) y despliega la app. Actualiza estado y publica progreso.

    `force_full`: si True, ignora cache/edición incremental y regenera de cero (escotilla
    "reconstruir desde cero" para cuando el código incremental acumuló drift).
    `user_email`: para la bitácora de eventos (deploy.done / deploy.error).
    """
    url, state, err = None, "error", ""
    new_artifacts: dict | None = None
    commit_spec: dict | None = None
    commit_message = "deploy"
    # El pool del engine async queda atado al loop de la tarea anterior (cada tarea Celery
    # usa su propio asyncio.run). Lo disponemos al ENTRAR para empezar con conexiones frescas
    # en ESTE loop, pase lo que pase con la tarea previa (si no, el 2º deploy falla al instante).
    try:
        await engine.dispose()
    except Exception:  # noqa: BLE001
        pass
    try:
        async with SessionLocal() as session:
            app_project = await session.get(AppProject, app_id)
            title = app_project.title if app_project else slug
            prev_artifacts = app_project.build_artifacts if app_project else None
            pending_edits = list(app_project.pending_edits or []) if app_project else []
            rows = (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == app_project.conversation_id)
                    .order_by(Message.seq)
                )
            ).scalars().all()

        if app_project and app_project.app_spec:
            from .app_builder import AppSpec, generate_code, qa_and_fix
            from .deploy import (
                _check_js, apply_static, container_status, run_celery_stack, run_containers,
            )

            async def _stage(text: str) -> None:
                async with SessionLocal() as s2:
                    ap = await s2.get(AppProject, app_id)
                    if ap is not None:
                        ap.deploy_stage = text
                        await s2.commit()
                await publish_deploy(app_id, {"type": "stage", "text": text})

            spec_obj = AppSpec.model_validate(app_project.app_spec)
            deployed_url = app_project.url
            spec_hash = hashlib.sha256(
                spec_obj.model_dump_json().encode()
            ).hexdigest()
            prev = prev_artifacts if isinstance(prev_artifacts, dict) else None

            # Decide solo: REUSE (spec igual, sin edits) / INCREMENTAL (edita el código actual
            # con diff mínimo) / FULL (genera de cero, o force_full). La edición incremental deja
            # main.py intacto si el cambio es solo-UI -> dispara el camino rápido de abajo.
            gen = await generate_code(
                spec_obj, on_stage=_stage, prev=prev, spec_hash=spec_hash,
                edits=pending_edits, force_full=force_full,
            )
            new_artifacts = {
                "spec_hash": spec_hash,
                "main_py": gen["main_py"],
                "static_files": gen.get("static_files") or {},
                "backend_reqs": gen.get("backend_reqs", ""),
            }
            commit_spec = app_project.app_spec
            commit_message = (
                "; ".join(pending_edits)[:200] if pending_edits else f"deploy: {spec_obj.name}"
            )

            # CAMINO RÁPIDO: si el backend (main.py + deps) NO cambió respecto a lo desplegado
            # y la app está viva, solo cambió la UI -> hot-swap de estáticos SIN reconstruir.
            backend_same = (
                isinstance(prev_artifacts, dict)
                and prev_artifacts.get("main_py") == gen["main_py"]
                and (prev_artifacts.get("backend_reqs") or "") == (gen.get("backend_reqs") or "")
            )
            running = await asyncio.to_thread(container_status, slug) == "running"
            fast = False
            if backend_same and running:
                js_err = _check_js(new_artifacts["static_files"].get("static/app.js", ""))
                if js_err is None:
                    await _stage("Aplicando cambios de UI (sin reconstruir)…")
                    fast = await asyncio.to_thread(
                        apply_static, slug, new_artifacts["static_files"]
                    )
                    if fast:
                        url = deployed_url

            if not fast:
                qa = await qa_and_fix(slug, app_id, gen, on_stage=_stage)
                new_artifacts["main_py"] = qa["main_py"]
                new_artifacts["static_files"] = qa.get("static_files") or {}
                new_artifacts["backend_reqs"] = qa.get("backend_reqs", "")
                broker = f"redis://app-{slug}-redis:6379/0" if spec_obj.jobs else None
                await _stage("Desplegando…")
                url = await asyncio.to_thread(run_containers, slug, app_id, broker)
                if spec_obj.jobs:
                    await _stage("Programando tareas (Celery beat)…")
                    await asyncio.to_thread(run_celery_stack, slug, app_id)
        elif app_project and app_project.dashboard:
            from .deploy import DASHBOARD_APP_CSS, DASHBOARD_APP_JS, DASHBOARD_MAIN_PY

            url = await asyncio.to_thread(
                build_and_run, slug, app_id, DASHBOARD_MAIN_PY,
                {"static/app.js": DASHBOARD_APP_JS, "static/app.css": DASHBOARD_APP_CSS},
            )
        else:
            transcript = _transcript(rows)
            files = await generate_app_files(title, transcript)
            url = await asyncio.to_thread(
                build_and_run, slug, app_id, files["main_py"],
                {"static/app.js": files["app_js"]},
            )
        state = "deployed"
    except Exception as exc:  # noqa: BLE001
        url, state, err = None, "error", str(exc)[:300]
        logger.error("DEPLOY FALLÓ (%s):\n%s", slug, traceback.format_exc())
    finally:
        # Reset del cliente Anthropic (aiohttp queda atado al loop de este asyncio.run).
        try:
            await runner.aclose()
        except Exception:  # noqa: BLE001
            pass

    async with SessionLocal() as session:
        ap = await session.get(AppProject, app_id)
        if ap is not None:
            ap.deploy_state, ap.deploy_stage = state, None
            if url:
                ap.url = url
            if state == "deployed" and new_artifacts is not None:
                ap.build_artifacts = new_artifacts
            if state == "deployed":
                # Los cambios pendientes ya se aplicaron al código desplegado.
                ap.pending_edits = []
            await session.commit()
    # Versionado local (git): un commit por deploy OK -> trazabilidad + rollback.
    if state == "deployed" and new_artifacts is not None and commit_spec is not None:
        try:
            from .app_repo import commit_version

            await asyncio.to_thread(
                commit_version, app_id, new_artifacts, commit_spec, commit_message
            )
        except Exception:  # noqa: BLE001
            pass

    await publish_deploy(
        app_id,
        {"type": "done", "state": state, "url": url}
        if state == "deployed"
        else {"type": "error", "message": err or "Falló el despliegue"},
    )
    # Bitácora: resultado del despliegue (con el usuario que lo disparó).
    try:
        from ..core.events import log_event

        if state == "deployed":
            await log_event(
                "deploy.done", status="ok", user_email=user_email or None, app_id=app_id,
                message=f"Despliegue OK ({slug}) → {url or ''}".strip(),
            )
        else:
            await log_event(
                "deploy.error", status="error", user_email=user_email or None, app_id=app_id,
                message=f"Falló el despliegue ({slug}): {err}"[:500],
            )
    except Exception:  # noqa: BLE001
        pass
    try:
        from .deploy import prune_dangling

        await asyncio.to_thread(prune_dangling)
    except Exception:  # noqa: BLE001
        pass

    # Cada tarea Celery corre en su propio asyncio.run() (event loop nuevo). El pool de
    # conexiones del engine async queda atado al loop de la PRIMERA tarea; en la siguiente
    # esas conexiones pertenecen a un loop ya cerrado y fallan al instante. Lo disponemos al
    # final para que la próxima tarea cree conexiones frescas en su propio loop.
    try:
        await engine.dispose()
    except Exception:  # noqa: BLE001
        pass
