"""Loop agéntico manual con streaming.

Ejecuta el bucle tool-use de Claude, transmite tokens y eventos al cliente mediante
un callback `emit` (que publica los eventos a un Redis Stream para el SSE), y coordina las
herramientas largas con los workers de Celery a través de Redis pub/sub.

Decisiones tomadas de la guía del SDK:
  - Modelo claude-opus-4-8
  - thinking adaptativo con display "summarized" (para mostrar progreso de razonamiento)
  - output_config.effort = high
  - prompt caching: breakpoint en el system prompt (cachea tools + system, el prefijo
    estable más grande) y otro en el último turno (caché incremental de la conversación)
"""
import asyncio
import copy
import json
import re
import uuid
from typing import Any, Awaitable, Callable

import redis.asyncio as aioredis
from anthropic import AsyncAnthropic, DefaultAioHttpClient

from ..core.config import settings
from ..core.db import SessionLocal
from ..core.models import AppProject
from ..mcp.registry import MCP_BETA, active_mcp_servers
from ..mcp.bridge import bridged_tools
from .prompts import SYSTEM_PROMPT
from .tools import INLINE_EXECUTORS, LONG_RUNNING_TOOLS, TOOL_SCHEMAS

Emit = Callable[[dict[str, Any]], Awaitable[None]]

# Cliente async con backend aiohttp (full async, recomendado para alta concurrencia).
# Se construye de forma perezosa dentro del event loop (aiohttp requiere un loop activo).
_client: AsyncAnthropic | None = None


def get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            http_client=DefaultAioHttpClient(),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None

# El system prompt se manda como bloque con cache_control: cachea tools + system juntos.
SYSTEM_BLOCKS = [
    {
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }
]


async def _capabilities_context() -> str:
    """Estado VIVO de conexiones para el agente (va como bloque de system NO cacheado,
    después del breakpoint, para no invalidar el prefijo cacheado al cambiar conexiones).

    Se deriva del CATÁLOGO de MCP (única fuente de verdad): cualquier server nuevo
    aparece solo, con su estado de conexión real y su `agent_hint`."""
    from sqlalchemy import select

    from ..core.models import McpConnection
    from ..mcp.catalog import catalog_list
    from ..mcp.connstore import current_user_sub

    sub = current_user_sub()
    async with SessionLocal() as session:
        connected = (
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

    def mark(p: str) -> str:
        return "✅ conectado" if p in connected else "⚠️ NO conectado"

    lines = [
        "Estado de conexiones AHORA (úsalo para decidir; si algo está NO conectado y lo "
        "necesitas, pídele al usuario que lo conecte en la sección 'Connectors'):"
    ]
    for s in catalog_list():
        hint = f" ({s.agent_hint})" if s.agent_hint else ""
        lines.append(f"- {s.label}: {mark(s.key)}{hint}")
    return "\n".join(lines)

# Parámetros de modelo que dependen de la versión de la API. Se envían vía extra_body
# para NO depender de la firma del SDK instalado: algunas versiones de `anthropic`
# todavía no aceptan `output_config` (ni `thinking` adaptativo) como keyword de stream().
# extra_body se reenvía tal cual al cuerpo JSON de la petición.
MODEL_EXTRA_BODY: dict[str, Any] = {
    "thinking": {"type": "adaptive", "display": "summarized"},
    "output_config": {"effort": settings.anthropic_effort},
}

MAX_ITERATIONS = 12  # cota de seguridad para el bucle agéntico
MAX_RETRIES = 3  # reintentos por llamada (backoff exponencial) ante errores transitorios
RETRY_BASE_DELAY = 0.6  # segundos; espera = RETRY_BASE_DELAY * 2**intento

# Modelos que la UI puede seleccionar. Si llega uno fuera de la lista, usamos el
# predeterminado de settings (evita inyectar strings de modelo arbitrarios).
AVAILABLE_MODELS: set[str] = {
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
}


def resolve_model(model: str | None) -> str:
    if model and model in AVAILABLE_MODELS:
        return model
    return settings.anthropic_model


def _mcp_result_text(block: Any) -> str:
    """Texto/diagnóstico de un mcp_tool_result, robusto a la forma del contenido (objeto/dict/
    str). Si no hay partes de texto (p.ej. un error estructurado de Notion), serializa el
    contenido para NO perder el detalle del error (así el agente corrige en vez de reintentar
    a ciegas, y se ve en el chat/Logs)."""
    content = getattr(block, "content", None)
    if isinstance(content, str):
        return content
    items = content if isinstance(content, list) else []
    texts: list[str] = []
    for c in items:
        d = c if isinstance(c, dict) else (c.model_dump() if hasattr(c, "model_dump") else {})
        if d.get("type") == "text" and d.get("text"):
            texts.append(d["text"])
    if texts:
        return "".join(texts)
    if items:
        try:
            return json.dumps(
                [c if isinstance(c, dict) else (c.model_dump() if hasattr(c, "model_dump") else str(c)) for c in items],
                ensure_ascii=False,
            )[:1500]
        except Exception:  # noqa: BLE001
            return str(items)[:1500]
    return ""


def _web_search_text(block: Any) -> str:
    """Resume el resultado de la web search nativa (lista de {title, url} o un error)."""
    content = getattr(block, "content", None)
    if isinstance(content, dict) or getattr(content, "type", None) == "web_search_tool_result_error":
        code = content.get("error_code") if isinstance(content, dict) else getattr(content, "error_code", "")
        return f"(búsqueda web falló: {code or 'error'})"
    items = content if isinstance(content, list) else []
    lines: list[str] = []
    for r in items[:8]:
        d = r if isinstance(r, dict) else (r.model_dump() if hasattr(r, "model_dump") else {})
        title = d.get("title") or d.get("url") or ""
        url = d.get("url", "")
        if title:
            lines.append(f"- {title}\n  {url}" if url else f"- {title}")
    return (f"{len(items)} resultado(s):\n" + "\n".join(lines)) if lines else "(sin resultados)"


def _tool_result(tool_use_id: str, content: str, is_error: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }
    if is_error:
        block["is_error"] = True
    return block


_THINKING_TYPES = {"thinking", "redacted_thinking"}
# Solo las tool_use de CLIENTE necesitan un `tool_result` en el siguiente mensaje de
# usuario. Las server-side (mcp_tool_use / server_tool_use) traen su resultado INLINE
# (mcp_tool_result) en el mismo turno del asistente -> NO se emparejan ni sintetizan.
_TOOL_USE_TYPES = {"tool_use"}


def _strip_orphan_server_tools(
    content: list[dict[str, Any]], keep_dangling_use: bool = False
) -> list[dict[str, Any]]:
    """Sanea bloques server-side (mcp_tool_use ↔ mcp_tool_result) dentro de un turno.

    La API exige que cada `mcp_tool_result` tenga su `mcp_tool_use` ANTES, en el mismo turno.
    Al fusionar turnos consecutivos (pause_turn) se puede descartar el `mcp_tool_use` y dejar
    el `mcp_tool_result` colgado → 400 ('mcp_tool_result ... must have a corresponding
    mcp_tool_use block before it'). Reglas (orden-aware):
      - `mcp_tool_result` sin un `mcp_tool_use` visto ANTES en este turno -> SIEMPRE se quita
        (incluso en el último mensaje: un resultado huérfano nunca es válido).
      - `mcp_tool_use` sin su `mcp_tool_result` -> se quita, salvo `keep_dangling_use`
        (último turno = pause_turn EN CURSO que debe reenviarse para reanudar)."""
    res_ids = {b.get("tool_use_id") for b in content if b.get("type") == "mcp_tool_result"}
    seen_use: set = set()
    out: list[dict[str, Any]] = []
    for b in content:
        t = b.get("type")
        if t == "mcp_tool_use":
            if b.get("id") in res_ids or keep_dangling_use:
                seen_use.add(b.get("id"))
                out.append(b)
            continue  # uso colgado en turno viejo -> descartar
        if t == "mcp_tool_result":
            if b.get("tool_use_id") in seen_use:
                out.append(b)
            continue  # resultado sin su uso antes -> descartar
        out.append(b)
    return out


def _merge_consecutive(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Garantiza alternancia user/assistant (string -> bloque de texto).

    - user+user: concatena (p.ej. reintentos duplicados).
    - assistant+assistant (p.ej. pause_turn): conserva SOLO el último turno. No se puede
      anteponer contenido al último asistente sin "modificar" sus bloques de razonamiento
      (la API lo rechaza), así que descartamos los turnos intermedios.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}] if content.strip() else []
        else:
            content = [dict(b) for b in content]
        if not content:
            continue
        if out and out[-1]["role"] == m["role"]:
            if m["role"] == "user":
                out[-1]["content"].extend(content)
            else:
                out[-1]["content"] = content  # asistente: quedarse con el último turno
        else:
            out.append({"role": m["role"], "content": content})
    return out


def _normalize_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanea el historial antes de mandarlo a la API (no muta el original).

    La API exige alternancia user/assistant, que cada `tool_use` tenga su `tool_result`
    en el SIGUIENTE mensaje (y viceversa), y firmas válidas de razonamiento. El historial
    persistido puede romper esto (pause_turn deja dos turnos de asistente seguidos;
    reintentos duplican el turno de usuario; runs interrumpidos dejan tool_use sin
    respuesta). Pasos:
      1) fusiona consecutivos del mismo rol;
      2) descarta bloques `thinking` salvo en el ciclo de tool-use activo (último
         asistente seguido de tool_result) — evita firmas inválidas al fusionar;
      3) empareja: sintetiza `tool_result` faltantes y descarta los huérfanos;
      4) re-fusiona y limpia vacíos.
    """
    merged = _merge_consecutive(messages)

    # (2) Razonamiento: la API exige que los bloques `thinking` del ÚLTIMO turno del
    # asistente viajen VERBATIM (no se pueden reordenar ni modificar). En turnos viejos
    # sí se pueden descartar (como hace context editing). Por eso: NO reordenamos nada y
    # quitamos `thinking` de todos los asistentes salvo el último.
    last_ast = max(
        (i for i, m in enumerate(merged) if m["role"] == "assistant"), default=-1
    )
    for i, m in enumerate(merged):
        if m["role"] == "assistant" and i != last_ast:
            m["content"] = [b for b in m["content"] if b.get("type") not in _THINKING_TYPES]

    # (2c) Sanea bloques server-side huérfanos en TODOS los asistentes. Un `mcp_tool_result`
    # sin su `mcp_tool_use` se quita siempre (incluso en el último mensaje). Solo en el ÚLTIMO
    # turno preservamos un `mcp_tool_use` colgado (pause_turn EN CURSO que se reenvía para
    # reanudar); en turnos viejos ese uso colgado se descarta.
    last_idx = len(merged) - 1
    for i, m in enumerate(merged):
        if m["role"] == "assistant":
            m["content"] = _strip_orphan_server_tools(
                m["content"], keep_dangling_use=(i == last_idx)
            )

    # (3) Emparejar tool_use -> tool_result.
    for i in range(len(merged)):
        m = merged[i]
        if m["role"] != "assistant":
            continue
        use_ids = [b.get("id") for b in m["content"] if b.get("type") in _TOOL_USE_TYPES]
        if not use_ids:
            continue
        nxt = merged[i + 1] if i + 1 < len(merged) else None
        if nxt is None or nxt["role"] != "user":
            nxt = {"role": "user", "content": []}
            merged.append(nxt) if i + 1 >= len(merged) else merged.insert(i + 1, nxt)
        existing = {b.get("tool_use_id") for b in nxt["content"] if b.get("type") == "tool_result"}
        synth = [
            {
                "type": "tool_result",
                "tool_use_id": uid,
                "content": "(resultado no disponible)",
                "is_error": True,
            }
            for uid in use_ids
            if uid not in existing
        ]
        nxt["content"] = synth + nxt["content"]  # tool_result al inicio del turno user

    # (3b) Descarta tool_result sin su tool_use en el asistente previo.
    for i, m in enumerate(merged):
        if m["role"] != "user":
            continue
        prev = merged[i - 1] if i > 0 else None
        valid: set[str] = set()
        if prev and prev["role"] == "assistant":
            valid = {b.get("id") for b in prev["content"] if b.get("type") in _TOOL_USE_TYPES}
        m["content"] = [
            b
            for b in m["content"]
            if b.get("type") != "tool_result" or b.get("tool_use_id") in valid
        ]

    # (4) Re-fusiona (por si quitar vacíos juntó roles) y limpia.
    return _merge_consecutive([m for m in merged if m["content"]])


def _with_cache_breakpoint(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Devuelve una copia de `messages` con un breakpoint de caché en el último bloque.

    No muta la lista original (no queremos persistir cache_control en el historial).
    """
    if not messages:
        return messages
    msgs = copy.deepcopy(messages)
    last = msgs[-1]
    content = last["content"]
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
        last["content"] = content
    if isinstance(content, list) and content:
        # cache_control solo aplica a tipos de bloque cacheables.
        content[-1]["cache_control"] = {"type": "ephemeral"}
    return msgs


async def _save_dashboard(app_id: str | None, config: dict[str, Any]) -> tuple[str, bool]:
    """Guarda la config de dashboard de Google Sheet en la app actual."""
    if not config.get("spreadsheet_id"):
        return "Falta spreadsheet_id en el dashboard.", True
    if app_id:
        async with SessionLocal() as session:
            app_project = await session.get(AppProject, app_id)
            if app_project is not None:
                app_project.dashboard = config
                await session.commit()
    n_k = len(config.get("kpis", []))
    n_c = len(config.get("charts", []))
    return (
        f"Dashboard '{config.get('title', 'Sheet')}' guardado: {n_k} KPIs, {n_c} gráficos. "
        "Desplegalo con el botón Desplegar.",
        False,
    )


# Fuentes que REQUIEREN un location real (id/ruta) para funcionar en runtime.
_NEEDS_LOCATION = {"google_sheets", "bigquery"}
# Tokens típicos de placeholder / id inventado (case-insensitive). Los ids reales no los traen.
_PLACEHOLDER_RE = re.compile(
    r"(pendiente|placeholder|\btodo\b|xxx|reemplaz|cambiar|ejemplo|example|tbd|"
    r"your[_-]|tu[_-]|spreadsheet_id|dataset_id|project_id|table_id|<|>|\.\.\.)",
    re.I,
)


def _placeholder_locations(spec: Any) -> list[str]:
    """Entidades (de fuentes que requieren location) con `location` vacío o tipo placeholder."""
    bad: list[str] = []
    for e in getattr(spec, "entities", []) or []:
        src = (getattr(e, "source", "") or "").lower()
        if src not in _NEEDS_LOCATION:
            continue
        loc = (getattr(e, "location", "") or "").strip()
        if not loc or _PLACEHOLDER_RE.search(loc):
            bad.append(f"{getattr(e, 'name', '?')} ({src}) → location={loc or 'vacío'!r}")
    return bad


async def _save_app_spec(app_id: str | None, spec_input: dict[str, Any]) -> tuple[str, bool]:
    """Valida la AppSpec compilada por el arquitecto y la guarda en la app."""
    from ..builder.app_builder import AppSpec

    try:
        spec = AppSpec.model_validate(spec_input)
    except Exception as exc:  # noqa: BLE001
        return f"La spec de la app no es válida: {exc}", True
    # GUARDRAIL DURO: no guardamos specs con location placeholder/vacío (romperían en runtime,
    # 404 del servicio → 502). Aplica a TODA app y a cada fuente que requiere id real.
    bad = _placeholder_locations(spec)
    if bad:
        return (
            "No puedo guardar la app: estas entidades tienen un `location` PLACEHOLDER o vacío. "
            "Usá el id/ruta REAL (creá la Google Sheet con `sheet_create`, indicá la tabla real de "
            "BigQuery, o pedíle el dato al usuario) y volvé a llamar `define_app`:\n- "
            + "\n- ".join(bad),
            True,
        )
    if app_id:
        async with SessionLocal() as session:
            app_project = await session.get(AppProject, app_id)
            if app_project is not None:
                app_project.app_spec = spec.model_dump()
                await session.commit()
    return (
        f"App '{spec.name}' especificada: {len(spec.entities)} entidad(es), "
        f"{len(spec.screens)} pantalla(s). El equipo de devs la construye al Desplegar.",
        False,
    )


async def _save_pending_edit(app_id: str | None, tool_input: dict[str, Any]) -> tuple[str, bool]:
    """Encola un cambio incremental (texto libre) sobre la app ya construida. Se aplica en
    el próximo Desplegar como edición de diff mínimo sobre el código actual."""
    instruction = (tool_input.get("instruction") or "").strip()
    if not instruction:
        return "Decime qué cambio querés aplicar.", True
    if not app_id:
        return "No hay una app activa para editar.", True
    async with SessionLocal() as session:
        app_project = await session.get(AppProject, app_id)
        if app_project is None:
            return "No encontré la app.", True
        if not (app_project.app_spec or app_project.build_artifacts):
            return (
                "Esta app todavía no está definida. Usá define_app para la definición inicial.",
                True,
            )
        edits = list(app_project.pending_edits or [])
        edits.append(instruction)
        app_project.pending_edits = edits
        await session.commit()
    return (
        f"Cambio anotado: «{instruction}». Se aplicará al Desplegar (edición incremental; "
        "si es solo visual, será un refresh instantáneo).",
        False,
    )


async def _execute_inline_tool(name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
    try:
        result = await INLINE_EXECUTORS[name](tool_input)
        return result, False
    except Exception as exc:  # noqa: BLE001
        return f"Error al ejecutar '{name}': {exc}", True


async def _execute_bridge_tool(
    provider: str, tool: str, tool_input: dict[str, Any]
) -> tuple[str, bool]:
    """Ejecuta una tool de un MCP self-hosted contra su contenedor local."""
    from ..mcp import client as mcp_client

    try:
        res = await mcp_client.call_tool(provider, tool, tool_input)
        text = res.get("text") or json.dumps(res.get("structured") or {}, ensure_ascii=False)
        return (text or "(sin contenido)"), bool(res.get("is_error"))
    except Exception as exc:  # noqa: BLE001
        return f"Error MCP {provider}.{tool}: {exc}", True


async def _execute_long_tool(
    name: str, tool_input: dict[str, Any], tool_use_id: str, emit: Emit
) -> tuple[str, bool]:
    """Despacha la tarea a Celery y reenvía el progreso al cliente vía Redis pub/sub.

    Generamos el canal nosotros y nos suscribimos ANTES de disparar la tarea, así no hay
    race condition con el worker.
    """
    from ..tasks.jobs import run_batch_job

    channel = f"job:{uuid.uuid4()}"
    redis = aioredis.from_url(settings.redis_url)
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)

    # Disparamos la tarea pasándole el canal donde debe publicar.
    run_batch_job.apply_async(args=[tool_input, channel])

    result_content = "La tarea terminó sin devolver resultado."
    is_error = False
    try:
        async for raw in pubsub.listen():
            if raw["type"] != "message":
                continue
            data = json.loads(raw["data"])
            event = data.get("event")
            if event == "progress":
                await emit(
                    {
                        "type": "tool_progress",
                        "tool_use_id": tool_use_id,
                        "progress": data.get("progress"),
                        "message": data.get("message"),
                    }
                )
            elif event == "done":
                result_content = data.get("result", result_content)
                break
            elif event == "error":
                result_content = f"La tarea falló: {data.get('message')}"
                is_error = True
                break
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await redis.aclose()

    return result_content, is_error


async def _log_agent_event(
    event_type: str,
    user_email: str | None,
    app_id: str | None,
    message: str,
    status: str = "ok",
) -> None:
    """Registra en la bitácora una acción/trace del agente. Best-effort.

    Nota: nunca incluimos los `input` de las herramientas (pueden traer datos del
    usuario); solo metadatos no sensibles (nombre de la tool, estado)."""
    try:
        from ..core.events import log_event

        await log_event(
            event_type, status=status, user_email=user_email, app_id=app_id, message=message
        )
    except Exception:  # noqa: BLE001
        pass


async def run_agent(
    messages: list[dict[str, Any]],
    emit: Emit,
    model: str | None = None,
    app_id: str | None = None,
    user_email: str | None = None,
    user_sub: str | None = None,
) -> list[dict[str, Any]]:
    """Ejecuta el bucle agéntico sobre `messages` (in-place) hasta que Claude termina.

    `messages` se va ampliando con los turnos del asistente y los tool_result.
    Devuelve la lista de mensajes nuevos generados (asistente + tool_results) para persistir.
    `model` es el modelo elegido en la UI (validado contra AVAILABLE_MODELS).
    `user_sub` fija el usuario vigente: los conectores se resuelven SOLO contra los suyos.
    """
    # Aislamiento por-usuario: todo conector que se use en este run (MCP, API directa,
    # contenedores self-hosted) se resuelve contra las conexiones de ESTE usuario.
    from ..mcp.connstore import set_user

    set_user(user_sub)

    new_messages: list[dict[str, Any]] = []
    selected_model = resolve_model(model)

    # Feedback inmediato: avisamos "trabajando" ANTES de preparar los MCP (que puede tardar
    # si hay que listar tools de contenedores), para que la UI no parezca congelada.
    await emit({"type": "status", "state": "running"})

    # Bloque de system con el estado vivo de conexiones (sin cache_control: va después del
    # breakpoint, no invalida el prefijo cacheado).
    system_blocks = SYSTEM_BLOCKS + [{"type": "text", "text": await _capabilities_context()}]

    # Conecta los servidores MCP activos (p. ej. Notion) al agente.
    # Cada mcp_server DEBE referenciarse con un mcp_toolset en tools, o la API da 400.
    # Mandamos `tools` por extra_body para no chocar con la validación de tipos del SDK.
    mcp = await active_mcp_servers()
    tools_list: list[dict[str, Any]] = list(TOOL_SCHEMAS)
    extra_body: dict[str, Any] = dict(MODEL_EXTRA_BODY)
    extra_headers = None
    if mcp:
        extra_body["mcp_servers"] = mcp
        tools_list += [{"type": "mcp_toolset", "mcp_server_name": s["name"]} for s in mcp]
        extra_headers = {"anthropic-beta": MCP_BETA}

    # Puente: tools de los MCP self-hosted (Slack, etc.) como tools nativas del agente.
    # El backend las ejecuta contra el contenedor; la nube de Anthropic no los alcanza.
    bridge_schemas, bridge_routes = await bridged_tools()
    tools_list += bridge_schemas
    # Búsqueda web NATIVA de Anthropic (server-side): el modelo busca en la nube de Anthropic
    # y los resultados vuelven inline con citas. max_uses acota el costo por turno.
    tools_list.append({"type": "web_search_20250305", "name": "web_search", "max_uses": 5})
    extra_body["tools"] = tools_list

    # Corta-loops: si el agente repite la MISMA llamada (tool|args) que falla, la frenamos.
    fail_sigs: dict[str, int] = {}
    LOOP_LIMIT = 3

    for _ in range(MAX_ITERATIONS):
        if fail_sigs and max(fail_sigs.values()) >= LOOP_LIMIT:
            worst = max(fail_sigs, key=fail_sigs.get)
            await emit({
                "type": "error",
                "message": (
                    f"Frené el bucle: el agente repitió una llamada que falla "
                    f"(`{worst.split('|', 1)[0]}`) {LOOP_LIMIT} veces seguidas. Suele ser por "
                    "argumentos inválidos (falta un campo requerido) o un conector mal configurado. "
                    "Revisá el detalle del error de arriba o reformulá el pedido."
                ),
            })
            break

        # Reintentos con backoff exponencial: no mostramos el primer error; reintentamos
        # hasta 3 veces. Solo reintentamos si aún no emitimos tokens en este intento
        # (evita duplicar texto ya mostrado al usuario).
        final = None
        for attempt in range(MAX_RETRIES + 1):
            streamed = False
            try:
                async with get_client().messages.stream(
                    model=selected_model,
                    max_tokens=settings.anthropic_max_tokens,
                    system=system_blocks,
                    messages=_with_cache_breakpoint(_normalize_history(messages)),
                    extra_body=extra_body,
                    extra_headers=extra_headers,
                ) as stream:
                    async for event in stream:
                        if event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                streamed = True
                                await emit({"type": "token", "text": event.delta.text})
                            elif event.delta.type == "thinking_delta":
                                streamed = True
                                await emit({"type": "thinking", "text": event.delta.thinking})
                    final = await stream.get_final_message()
                break
            except Exception:  # noqa: BLE001
                if streamed or attempt >= MAX_RETRIES:
                    raise
                await asyncio.sleep(RETRY_BASE_DELAY * (2**attempt))

        # Persistimos el turno del asistente como bloques JSON (reutilizables y serializables).
        assistant_content = [b.model_dump(mode="json", exclude_none=True) for b in final.content]
        assistant_msg = {"role": "assistant", "content": assistant_content}
        messages.append(assistant_msg)
        new_messages.append(assistant_msg)

        # Surface del uso de herramientas MCP (ejecutadas del lado servidor).
        mcp_uses: dict[str, str] = {}  # tool_use_id -> firma "server:name|args"
        for block in final.content:
            if block.type == "mcp_tool_use":
                server = getattr(block, "server_name", "mcp")
                mcp_uses[block.id] = (
                    f"{server}:{block.name}|"
                    + json.dumps(block.input, sort_keys=True, default=str)
                )
                await emit(
                    {
                        "type": "tool_use",
                        "tool_use_id": block.id,
                        "name": f"{server}:{block.name}",
                        "input": block.input,
                        "long_running": False,
                    }
                )
                await _log_agent_event(
                    "agent.trace", user_email, app_id,
                    f"MCP {server}: {block.name}", status="info",
                )
            elif block.type == "mcp_tool_result":
                is_err = bool(getattr(block, "is_error", False))
                if is_err:
                    sig = mcp_uses.get(getattr(block, "tool_use_id", ""), "mcp|?")
                    fail_sigs[sig] = fail_sigs.get(sig, 0) + 1
                await emit(
                    {
                        "type": "tool_result",
                        "tool_use_id": getattr(block, "tool_use_id", ""),
                        "content": _mcp_result_text(block) or "(resultado MCP vacío)",
                        "is_error": is_err,
                    }
                )
            elif block.type == "server_tool_use":
                # Búsqueda web nativa de Anthropic (server-side).
                await emit(
                    {
                        "type": "tool_use",
                        "tool_use_id": getattr(block, "id", ""),
                        "name": f"🔎 {block.name}",
                        "input": getattr(block, "input", {}),
                        "long_running": False,
                    }
                )
                await _log_agent_event(
                    "agent.trace", user_email, app_id, f"Web: {block.name}", status="info",
                )
            elif block.type == "web_search_tool_result":
                await emit(
                    {
                        "type": "tool_result",
                        "tool_use_id": getattr(block, "tool_use_id", ""),
                        "content": _web_search_text(block),
                        "is_error": False,
                    }
                )

        usage = final.usage
        await emit(
            {
                "type": "message_done",
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
                    "cache_creation_input_tokens": getattr(
                        usage, "cache_creation_input_tokens", 0
                    ),
                },
                "stop_reason": final.stop_reason,
            }
        )

        # pause_turn / server-side incompleto: el conector MCP ejecuta del lado servidor y a
        # veces devuelve el `mcp_tool_use` y pausa (stop_reason pause_turn, o None en streaming)
        # ANTES de traer el `mcp_tool_result`. Reanudamos reenviando el turno para que la API
        # complete el resultado (acotado por MAX_ITERATIONS). Si cortáramos acá, quedaría un
        # mcp_tool_use colgado (sin resultado) y la herramienta nunca respondería.
        _mcp_uses = {getattr(b, "id", None) for b in final.content if getattr(b, "type", None) == "mcp_tool_use"}
        _mcp_res = {getattr(b, "tool_use_id", None) for b in final.content if getattr(b, "type", None) == "mcp_tool_result"}
        dangling_mcp = bool(_mcp_uses - _mcp_res - {None})
        if final.stop_reason == "pause_turn" or dangling_mcp:
            continue
        if final.stop_reason != "tool_use":
            break

        # Ejecutamos cada tool_use y juntamos los resultados en un único turno de usuario.
        tool_results: list[dict[str, Any]] = []
        for block in final.content:
            if block.type != "tool_use":
                continue
            await emit(
                {
                    "type": "tool_use",
                    "tool_use_id": block.id,
                    "name": block.name,
                    "input": block.input,
                    "long_running": block.name in LONG_RUNNING_TOOLS,
                }
            )

            if block.name == "define_dashboard":
                content, is_error = await _save_dashboard(app_id, block.input)
                if not is_error:
                    await emit({"type": "dashboard", "config": block.input})
            elif block.name == "define_app":
                content, is_error = await _save_app_spec(app_id, block.input)
                if not is_error:
                    await emit({"type": "app_spec", "spec": block.input})
                    await _log_agent_event(
                        "app.define", user_email, app_id,
                        f"App definida: {block.input.get('name', '')}",
                    )
            elif block.name == "edit_app":
                content, is_error = await _save_pending_edit(app_id, block.input)
                if not is_error:
                    await _log_agent_event(
                        "app.edit", user_email, app_id,
                        f"Cambio solicitado: {block.input.get('instruction', '')}"[:300],
                    )
            elif block.name in LONG_RUNNING_TOOLS:
                content, is_error = await _execute_long_tool(
                    block.name, block.input, block.id, emit
                )
            elif block.name in INLINE_EXECUTORS:
                content, is_error = await _execute_inline_tool(block.name, block.input)
            elif block.name in bridge_routes:
                provider, tool = bridge_routes[block.name]
                content, is_error = await _execute_bridge_tool(provider, tool, block.input)
            else:
                content, is_error = f"Herramienta desconocida: {block.name}", True

            await emit(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                }
            )
            await _log_agent_event(
                "agent.trace", user_email, app_id,
                f"Herramienta: {block.name}", status="error" if is_error else "ok",
            )
            if is_error:
                sig = f"{block.name}|" + json.dumps(block.input, sort_keys=True, default=str)
                fail_sigs[sig] = fail_sigs.get(sig, 0) + 1
            tool_results.append(_tool_result(block.id, content, is_error))

        tool_msg = {"role": "user", "content": tool_results}
        messages.append(tool_msg)
        new_messages.append(tool_msg)

    await emit({"type": "status", "state": "idle"})
    return new_messages
