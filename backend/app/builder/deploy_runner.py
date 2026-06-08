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

from ..agent import runner
from ..core.config import settings
from ..core.db import SessionLocal, engine
from ..core.models import AppProject, Message


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
    app_id: str, slug: str, force_full: bool = False, user_email: str = "",
    restore_sha: str = "",
) -> None:
    """Genera, construye (QA) y despliega la app. Actualiza estado y publica progreso.

    `force_full`: si True, ignora cache/edición incremental y regenera de cero (escotilla
    "reconstruir desde cero" para cuando el código incremental acumuló drift).
    `user_email`: para la bitácora de eventos (deploy.done / deploy.error).
    `restore_sha`: si viene (rollback), NO se crea un commit nuevo — se marca esa versión como
    la desplegada (`deployed_sha`), así el historial conserva su orden y se resalta en su lugar.
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
            prev_artifacts = app_project.build_artifacts if app_project else None
            pending_edits = list(app_project.pending_edits or []) if app_project else []

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

            # DB PROPIA de la app: si la spec la pide (data_source "postgres"), aprovisiona el
            # schema + rol acotado en apps-postgres (idempotente) y guarda el password (cifrado)
            # la primera vez. La app la usará por el connector-proxy (provider "postgres").
            if "postgres" in (spec_obj.data_sources or []):
                from ..core.appdb import new_password, provision as provision_db
                from ..core.crypto import decrypt, encrypt

                try:
                    async with SessionLocal() as s3:
                        ap2 = await s3.get(AppProject, app_id)
                        pw = decrypt(ap2.db_password) if (ap2 and ap2.db_password) else new_password()
                        # Tope duro: si apps-postgres no responde, NUNCA colgar el deploy.
                        await asyncio.wait_for(provision_db(app_id, pw), timeout=30)
                        if ap2 and not ap2.db_password:
                            ap2.db_password = encrypt(pw)
                            await s3.commit()
                    await _stage("Base de datos de la app lista (schema propio)…")
                except Exception as exc:  # noqa: BLE001 — no abortar el deploy por esto
                    await _stage(f"Aviso: no se pudo aprovisionar la DB de la app: {exc}")

            # Decide solo: REUSE (spec igual, sin edits) / INCREMENTAL (edita el código actual
            # con diff mínimo) / FULL (genera de cero, o force_full). La edición incremental deja
            # main.py intacto si el cambio es solo-UI -> dispara el camino rápido de abajo.
            # Contexto del DUEÑO durante la generación: permite introspectar en vivo las tools de
            # los MCP hosted/self-hosted (BigQuery, Slack, Notion…) con SU conexión, e inyectar el
            # catálogo real (nombres+args) en el prompt del builder. Mismo dueño que en runtime.
            from ..mcp.connstore import use_user

            owner_email = (app_project.owner_email or user_email or "").strip().lower()
            with use_user(owner_email):
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
        else:
            # Único camino de generación: la app debe estar DEFINIDA (spec) por el agente.
            raise RuntimeError(
                "This app has no spec yet. Define it first in the builder (the agent's define_app) "
                "before deploying."
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

    # Sha desplegado: en deploy normal se crea un commit (git) y se usa su sha; en un RESTORE se
    # reusa el sha restaurado (sin commit nuevo) para no alterar el orden del historial.
    new_sha: str | None = (restore_sha or "").strip() or None
    if state == "deployed" and not restore_sha and new_artifacts is not None and commit_spec is not None:
        try:
            from .app_repo import commit_version

            new_sha = await asyncio.to_thread(
                commit_version, app_id, new_artifacts, commit_spec, commit_message
            ) or None
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
                if new_sha:
                    ap.deployed_sha = new_sha  # versión actualmente live (para resaltar en el historial)
                # Marca en el CHAT: un mensaje "system" deja constancia del deploy en ese punto.
                if ap.conversation_id:
                    label = (
                        f"Restored version {restore_sha[:7]}" if restore_sha else "Deployed"
                    )
                    session.add(Message(
                        conversation_id=ap.conversation_id,
                        role="system",
                        content={"type": "deploy", "url": url, "sha": new_sha, "label": label},
                    ))
            await session.commit()

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
