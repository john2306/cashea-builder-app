"""Tareas largas ejecutadas por los workers de Celery.

Cada tarea publica su progreso en un canal de Redis (pub/sub). El proceso de FastAPI
está suscrito a ese canal y reenvía los eventos al navegador por SSE en tiempo real.

El `channel` lo genera quien dispara la tarea (el runner del agente), de modo que el
suscriptor ya está escuchando antes de que el worker empiece a publicar (sin race condition).
"""
import json
import time
from typing import Any

import redis

from ..core.config import settings
from .celery_app import celery_app

# Cliente Redis síncrono: estamos en un worker de Celery, no en código async.
_redis = redis.from_url(settings.redis_url)


def _publish(channel: str, payload: dict[str, Any]) -> None:
    _redis.publish(channel, json.dumps(payload))


@celery_app.task(name="run_batch_job", bind=True)
def run_batch_job(self, tool_input: dict[str, Any], channel: str) -> str:
    job_name = tool_input.get("job_name", "trabajo")
    steps = int(tool_input.get("steps", 5))
    steps = max(1, min(steps, 20))

    try:
        for i in range(steps):
            # Aquí iría el trabajo real (consulta a DB, llamada a API, ETL, etc.).
            time.sleep(1)
            _publish(
                channel,
                {
                    "event": "progress",
                    "progress": round((i + 1) / steps, 3),
                    "message": f"Procesando '{job_name}': paso {i + 1}/{steps}",
                },
            )

        result = (
            f"Proceso '{job_name}' completado correctamente: "
            f"{steps} paso(s) ejecutado(s)."
        )
        _publish(channel, {"event": "done", "result": result})
        return result
    except Exception as exc:  # noqa: BLE001
        _publish(channel, {"event": "error", "message": str(exc)})
        raise


@celery_app.task(name="deploy.run")
def run_deploy_task(
    app_id: str, slug: str, force_full: bool = False, user_email: str = "",
    restore_sha: str = "",
) -> str:
    """Ejecuta el pipeline de deploy (generación desde spec + QA) en el worker.

    Corre la lógica async en un loop propio. Aislado de la API: no la bloquea ni muere
    si la API reinicia. `force_full`: ignora cache/incremental y regenera de cero.
    `user_email`: para la bitácora (el worker no tiene la sesión del request).
    `restore_sha`: rollback a esa versión (no crea commit nuevo; marca esa como desplegada).
    """
    import asyncio

    from ..builder.deploy_runner import run_deploy

    asyncio.run(run_deploy(
        app_id, slug, force_full=force_full, user_email=user_email, restore_sha=restore_sha,
    ))
    return "ok"
