"""Equipo de devs (pipeline de generación de apps de backoffice reales).

Orquestación ESTRUCTURADA por el backend, en etapas:
  1. App Spec (la produce el chat/arquitecto con la tool `define_app`).
  2. Backend dev (senior): FastAPI async + Celery/Redis + Celery beat, conectores REALES.
  3. Frontend dev (senior): HTML/JS/CSS vanilla (sin build), consume la API en mismo origen.
  4. QA (Fase B): check de sintaxis JS + build + correr + smoke test + loop de fixes.

Cada etapa = una llamada a la API de Anthropic con un system prompt especializado y un
PROTOCOLO MULTI-ARCHIVO de salida (bloques ===FILE:path===). Reutiliza el scaffold de
`deploy.py` (auth SSO, Traefik, Dockerfiles) y el broker de conectores.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger("cashea.qa")

from pydantic import BaseModel, Field

from . import deploy
from ..agent.runner import get_client
from ..core.config import settings
from ..connectors import PROVIDERS

# ============================================================================
# App Spec — lo que el arquitecto (chat) compila con `define_app`.
# ============================================================================


class Field_(BaseModel):
    name: str
    type: str = "string"  # string | number | date | bool


class Entity(BaseModel):
    name: str
    source: str = ""  # bigquery | google_sheets | postgres | none
    location: str = ""  # p.ej. "proyecto.dataset.tabla" o spreadsheet_id/range
    fields: list[Field_] = Field(default_factory=list)


class Screen(BaseModel):
    name: str
    type: str = "table"  # table | form | dashboard | detail
    entity: str = ""
    actions: list[str] = Field(default_factory=list)  # create | update | delete | export | notify


class Job(BaseModel):
    name: str
    schedule: str = ""  # cron, p.ej. "0 9 * * *"
    description: str = ""


class AppSpec(BaseModel):
    name: str
    description: str = ""
    data_sources: list[str] = Field(default_factory=list)  # bigquery, google_sheets, slack…
    entities: list[Entity] = Field(default_factory=list)
    screens: list[Screen] = Field(default_factory=list)
    jobs: list[Job] = Field(default_factory=list)
    notifications: list[str] = Field(default_factory=list)  # slack, notion


# ============================================================================
# Prompts especializados (devs senior)
# ============================================================================

_FILE_PROTOCOL = """\
FORMATO DE SALIDA (exacto, sin texto fuera de los bloques, sin explicaciones):
===FILE:ruta/relativa===
<contenido completo del archivo>
===FILE:otra/ruta===
<contenido>
===END==="""

# Modelo ENTERPRISE: la app usa las credenciales del DUEÑO (no las del visor).
_CONNECTORS_DOC = """\
CONECTORES (datos externos) — MODELO ENTERPRISE: la app usa las credenciales del DUEÑO (las
que él conectó en el builder), NO las del visor. El visor solo necesita acceso (allowlist).
Obtené el token del dueño desde el gateway INTERNO (server-side) con el secreto de la app:
    GET {INTERNAL_GATEWAY}/api/apps/{APP_ID}/owner-token/<provider>   (header X-App-Secret: <APP_SECRET>)
    -> 200 {"access_token": "...", "token_type": "Bearer"}
`INTERNAL_GATEWAY`, `APP_ID`, `APP_SECRET` están en os.environ (usá INTERNAL_GATEWAY, NO
AUTH_GATEWAY, para llamadas servidor→servidor). CADA conector tiene SU PROPIO token/scope: usá
el provider EXACTO según el servicio (no reutilices el token de Sheets para Docs, etc.). Providers
válidos: bigquery, google_sheets, google_docs, google_drive, gmail, google_calendar, notion, slack.
Con ese access_token llamás la API del servicio (httpx async):
  - bigquery (owner-token/bigquery) -> POST https://bigquery.googleapis.com/bigquery/v2/projects/<project>/queries  body {"query","useLegacySql":false}
  - google_sheets (owner-token/google_sheets) -> https://sheets.googleapis.com/v4/spreadsheets/<id>/values/<rango>  (?valueInputOption=USER_ENTERED para escribir)
  - google_docs (owner-token/google_docs) -> Docs API: crear POST https://docs.googleapis.com/v1/documents {"title":"..."} (devuelve documentId); insertar texto POST https://docs.googleapis.com/v1/documents/<id>:batchUpdate {"requests":[{"insertText":{"location":{"index":1},"text":"..."}}]}. La URL del doc es https://docs.google.com/document/d/<documentId>/edit
  - google_drive (owner-token/google_drive) -> https://www.googleapis.com/drive/v3/files (crear/listar/mover); upload en https://www.googleapis.com/upload/drive/v3/files
  - gmail (owner-token/gmail) -> https://gmail.googleapis.com/gmail/v1/users/me/... (messages, drafts, send)
  - google_calendar (owner-token/google_calendar) -> https://www.googleapis.com/calendar/v3/calendars/<id>/events
  - notion -> API REST directa (header `Notion-Version: 2022-06-28` + Bearer). Crear página:
      POST https://api.notion.com/v1/pages  body {"parent":{"page_id":"<id>"},
      "properties":{"title":{"title":[{"text":{"content":"<título>"}}]}},
      "children":[{"object":"block","type":"paragraph","paragraph":{"rich_text":[{"text":{"content":"..."}}]}}]}
      -> la respuesta trae `url` (esa es la URL de la página: GUARDALA en la Sheet/historial, nunca "—").
      Necesitás un `parent` real (page_id/database_id compartido con la integración). Buscar:
      POST https://api.notion.com/v1/search {"query":"..."}.
  - slack -> POST https://slack.com/api/chat.postMessage  body {"channel","text"}
NO reenvíes el header del visor ni le pidas conectar nada. Si owner-token da 409, el dueño no
conectó ese servicio: respondé 503 con un mensaje claro. Este patrón sirve igual en endpoints
y en tareas Celery (ambos tienen APP_SECRET).

CONECTORES MCP (Intercom, Miro) — para esos (que siguen siendo MCP) usá el CONNECTOR PROXY, NO
su API directa: POST {INTERNAL_GATEWAY}/api/apps/{APP_ID}/mcp/<provider>/<tool> (header
X-App-Secret) body {"arguments": {...}} -> {"ok","text","result"}. (Notion YA NO es MCP: usá su
API REST directa con owner-token, como arriba.)

IA / LLM (features "inteligentes": resumir, clasificar, extraer datos, redactar, analizar
documentos): NO uses API keys propias ni llames a OpenAI/Anthropic/Gemini directo. Usá el
PROXY de la plataforma (server-side, con el secreto de la app):
    POST {INTERNAL_GATEWAY}/api/apps/{APP_ID}/llm   (header X-App-Secret: <APP_SECRET>)
    body: {"model": "<modelo>", "messages": [{"role":"user","content":"..."}],
           "system": "<opcional>", "max_tokens": 1024, "temperature": 0.3}
    -> 200 {"text": "...", "provider": "...", "model": "...", "usage": {...}}
Modelos permitidos: claude-haiku-4-5 (default, barato), claude-sonnet-4-6, gpt-4o-mini, gpt-4o,
gemini-2.5-flash, gemini-2.5-pro. Para DOCUMENTOS/IMÁGENES, `content` puede ser una lista de
partes: {"type":"text","text":...}, {"type":"image","mime":"image/png","data":"<base64>"},
{"type":"document","mime":"application/pdf","data":"<base64>"} (PDF solo con claude-*/gemini-*).
El proxy aplica un tope diario por app y audita cada llamada. Si responde 429, avisá que se
alcanzó la cuota; si 400, el modelo no está permitido."""

BACKEND_SYSTEM = f"""\
Eres un INGENIERO BACKEND SENIOR. Generás el backend de una app de backoffice real con
FastAPI FULL ASYNC, en UN SOLO archivo `main.py`. Reglas:
- `app = FastAPI()`; TODOS los endpoints `async def`, bajo el prefijo `/api`.
- `GET /api/health` -> {{"status":"ok"}}. CORS abierto (CORSMiddleware allow_origins=["*"]).
- Pydantic v2 para los modelos de entrada/salida. Tipado y validación correctos.
- Implementá los endpoints que cubren las pantallas/acciones de la spec (list/get/create/
  update/delete/export/notify según corresponda). Manejo de errores con HTTPException.
- Para leer/escribir datos externos, usá los conectores como se indica abajo. Para leer el
  header de sesión usá un parámetro `authorization: str = Header(None)` y reenvialo.
- TAREAS PROGRAMADAS (solo si la spec trae `jobs`): definí a nivel módulo
  `celery_app = Celery("app", broker=os.environ.get("CELERY_BROKER_URL","redis://localhost:6379/0"), backend=...)`
  con `celery_app.conf.beat_schedule` (una entrada por job, usando `crontab(...)` de
  `celery.schedules` parseado del cron del job) y las tasks `@celery_app.task(name="main.<x>")`.
  ¡OJO! las tasks corren SIN sesión de usuario -> NO pueden usar tokens de conector por-usuario;
  limitalas a trabajo interno/cómputo o a registrar resultados (loggear, guardar en estado).
  Si NO hay jobs, no incluyas Celery. Nunca uses os.environ[...] sin default (rompería el import).
- Las deps base YA están instaladas (fastapi, uvicorn, httpx, pydantic, celery, redis). CUALQUIER
  otra librería que importes (p.ej. pandas, numpy, openpyxl, python-docx, Pillow, reportlab,
  matplotlib, beautifulsoup4) NO está y DEBÉS declararla en ===FILE:requirements.txt=== (una por
  línea, versión opcional) o el backend NO levanta (ModuleNotFoundError). Regla: si la importás,
  va en requirements.txt. Si no usás extras, NO emitas requirements.txt. Nada de stubs.
- LOGS DE EJECUCIÓN (SIEMPRE, en TODA app): mantené un buffer en memoria de eventos/errores y
  exponé `GET /api/_logs` -> {{"logs": [{{"ts","level","message"}}, ...]}} (más nuevos al final).
  Definí a nivel módulo algo como:
    `_LOGS = []`
    `def _log(level, message): _LOGS.append({{"ts": datetime.now(timezone.utc).isoformat(), "level": level, "message": str(message)[:500]}}); del _LOGS[:-300]`
  y USALO en cada acción/proceso importante: éxito (`_log("info", ...)`) y SOBRE TODO en los
  `except` (`_log("error", f"... {{exc}}")`) capturando el detalle real del error (incluido el
  texto de respuestas 4xx/5xx de conectores/LLM). Objetivo: que el usuario VEA en la app qué
  falló para volver a iterar con el builder. Si la app ya tiene un historial con estados/errores,
  igual exponé `/api/_logs` con los eventos de ejecución recientes.

{_CONNECTORS_DOC}

{_FILE_PROTOCOL}
Devolvé ===FILE:main.py=== (y ===FILE:requirements.txt=== solo si usás libs pip extra)."""

FRONTEND_SYSTEM = f"""\
Eres un INGENIERO FRONTEND SENIOR. Generás el frontend de un dashboard/backoffice real en
HTML/JS/CSS VANILLA (sin build, sin framework, sin TypeScript):
- Archivo principal `static/app.js` (un <script> CLÁSICO). Definí
  `window.startApp = function () {{ … }}`: el gate de autenticación la llama cuando el usuario
  ya está logueado y autorizado. Dentro, renderizá la UI en `document.getElementById("app")`.
- Para llamar al backend usá SIEMPRE `window.authFetch(path, init)` (REFERENCIALO COMO
  `window.authFetch`, no como `authFetch` suelto — lo define el gate y así nunca da
  "authFetch is not defined"). Ya agrega la sesión y es mismo origen que la API:
  `window.authFetch("/api/...").then(function (r) {{ return r.json(); }})`.
  NO implementes login ni manejes tokens (el gate ya lo hace).
- CONTRATO = VERDAD: el `main.py` del backend (te lo paso completo) es la fuente. Para CADA
  endpoint que consumas, localizá su `return {{...}}` y usá los nombres de claves y campos
  EXACTOS (carácter por carácter). NO inventes ni traduzcas nombres: si el backend devuelve
  `cumulative_customers_over_time` no leas `cumulative_customers`; si una fila trae
  `{{"month": ..., "new_customers": ...}}` no leas `count`. Un nombre que no coincide = gráfico
  o tabla VACÍA aunque la data llegue. Antes de mapear, verificá que la clave existe en el JSON.
- JS de navegador compatible (ES2017): NADA de TypeScript, JSX, ni `import`/`export`
  (es un script clásico). Evitá optional chaining (`?.`) y nullish (`??`); usá `&&`/`||` y
  chequeos explícitos. Manipulá el DOM con document.createElement / innerHTML.
- Gráficos: Chart.js YA está disponible como global `Chart` (no lo importes ni lo cargues).
  Usalo para barras/líneas/torta cuando la spec pida métricas o dashboards.
- Estilos opcionales en `static/app.css`.
- Implementá EXACTAMENTE las pantallas de la spec consumiendo los endpoints del backend:
  tablas con datos, KPIs/gráficos, formularios con validación para create/update, confirmación
  para delete, estados de carga y error. JS válido, sin errores de sintaxis.
- TODO el TEXTO de la UI de la app generada DEBE estar en INGLÉS (labels, botones, títulos,
  estados de carga/error, mensajes, títulos de gráficos): "Loading…", "Save", "Update", "Search",
  "Delete", "Create", "Close", etc. SIEMPRE en inglés, sin importar el idioma del pedido del usuario.
- PANEL DE LOGS (SIEMPRE, en TODA app): incluí un panel "Execution log" FIJO abajo
  (position: fixed; bottom 0; ancho completo), COLAPSABLE: un encabezado clickeable con un
  indicador ▲/▼ que expande/contrae el cuerpo; arranca CONTRAÍDO (solo la barra de título, sin
  tapar la app). Al expandir: hacé `authFetch("/api/_logs")`, mostrá las líneas con su `ts`
  (hora) y `message`, resaltando en ROJO las de `level==="error"`; refrescá cada ~4s mientras
  esté abierto (parando el intervalo al contraer) y ofrecé un botón "Refresh". El cuerpo con
  scroll y alto acotado (p.ej. 220px). ORDEN: cronológico, MÁS ANTIGUO ARRIBA y más reciente
  abajo. SCROLL: al refrescar NO reposiciones el scroll a la fuerza; preservá la posición de
  lectura del usuario y solo auto-scrolleá al fondo si YA estaba pegado al fondo (umbral ~40px:
  `el.scrollHeight - el.scrollTop - el.clientHeight < 40`). Propósito: que el usuario vea los
  errores de ejecución de sus procesos y pueda volver a iterar con el builder. Estilizá el panel
  en static/app.css.

{_FILE_PROTOCOL}
Incluí siempre ===FILE:static/app.js=== (+ ===FILE:static/app.css=== si hace falta)."""

QA_SYSTEM = f"""\
Eres un INGENIERO QA SENIOR. Te paso el código actual y los ERRORES (build, runtime/logs del
contenedor, typecheck o smoke test de endpoints). Diagnosticá la causa y devolvé SOLO los
archivos corregidos (completos), mínimos para arreglar el problema, sin romper lo que anda ni
agregar features.

{_FILE_PROTOCOL}"""


# ============================================================================
# Orquestación
# ============================================================================

# data_source de la spec -> provider id del broker de conectores.
DATA_SOURCE_TO_PROVIDER = {
    "bigquery": "bigquery",
    "google_sheets": "google-sheets",
    "google-sheets": "google-sheets",
    "slack": "slack",
    "gmail": "gmail",
    "notion": "notion",
}


def needed_connectors(spec: AppSpec) -> list[str]:
    srcs = set(spec.data_sources) | set(spec.notifications)
    return sorted({DATA_SOURCE_TO_PROVIDER[s] for s in srcs if s in DATA_SOURCE_TO_PROVIDER})


def _strip_fences(content: str) -> str:
    """Quita cercas markdown (```lang … ```) que el LLM a veces agrega dentro del bloque.

    Las trata INDEPENDIENTEMENTE: si solo viene la de apertura, o solo la de cierre, igual
    se quitan. Una cerca colgada metía un ``` dentro del .tsx/.py y rompía el build (p. ej.
    "Unterminated string literal"). Robusto > frágil porque los fixes de QA usan el mismo parser.
    """
    lines = content.split("\n")
    if lines and re.match(r"^```[\w.+-]*$", lines[0].strip()):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_files(text: str) -> dict[str, str]:
    files: dict[str, str] = {}
    parts = re.split(r"===FILE:(.*?)===", text)
    for i in range(1, len(parts) - 1, 2):
        path = parts[i].strip()
        content = parts[i + 1].split("===END===")[0].strip()
        files[path] = _strip_fences(content)
    return files


async def _agent(system: str, prompt: str, max_tokens: int = 32000) -> dict[str, str]:
    # Streaming: la SDK lo exige para max_tokens alto (requests potencialmente largos).
    async with get_client().messages.stream(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        message = await stream.get_final_message()
    text = "".join(b.text for b in message.content if b.type == "text")
    return parse_files(text)


async def build_backend(spec: AppSpec) -> tuple[str, str]:
    """Devuelve (main.py, requirements.txt extra)."""
    files = await _agent(
        BACKEND_SYSTEM,
        "Generá el backend (main.py) para esta spec de app de backoffice:\n\n"
        f"{spec.model_dump_json(indent=2)}",
    )
    main_py = files.get("main.py") or next(
        (v for k, v in files.items() if k != "requirements.txt"), ""
    )
    if "FastAPI" not in main_py:
        raise RuntimeError("El backend dev no produjo un FastAPI válido.")
    return main_py, files.get("requirements.txt", "")


def _static_files(files: dict[str, str]) -> dict[str, str]:
    """Normaliza la salida del dev a {static/app.js [, static/app.css]} (el modelo vanilla
    es de un solo JS + un CSS opcional; ignoramos cualquier otro archivo)."""
    static: dict[str, str] = {}
    for k, v in files.items():
        base = k.split("/")[-1]
        if base.endswith(".js"):
            static["static/app.js"] = v
        elif base.endswith(".css"):
            static["static/app.css"] = v
    return static


async def build_frontend(spec: AppSpec, main_py: str) -> dict[str, str]:
    """Devuelve los archivos estáticos generados: {static/app.js [, static/app.css]}."""
    files = await _agent(
        FRONTEND_SYSTEM,
        "Generá el frontend (HTML/JS/CSS vanilla) para esta spec, consumiendo la API del backend.\n\n"
        f"SPEC:\n{spec.model_dump_json(indent=2)}\n\n"
        "CONTRATO DEL BACKEND (main.py COMPLETO — usá los nombres de claves/campos EXACTOS de "
        f"cada `return`):\n{main_py[:20000]}",
    )
    static = _static_files(files)
    if "static/app.js" not in static:
        raise RuntimeError("El frontend dev no produjo static/app.js.")
    return static


async def qa_fix(
    main_py: str, static_files: dict[str, str], error_log: str
) -> dict[str, str]:
    """El QA dev devuelve los archivos corregidos a partir del código + los errores."""
    code = {"main.py": main_py, **static_files}
    bundle = "\n".join(f"===FILE:{p}===\n{c}\n===END===" for p, c in code.items())
    prompt = (
        f"CÓDIGO ACTUAL:\n{bundle[:60000]}\n\n"
        f"ERRORES detectados por QA:\n{error_log[:6000]}\n\n"
        "Devolvé SOLO los archivos corregidos."
    )
    return await _agent(QA_SYSTEM, prompt)


EDIT_SYSTEM = f"""\
Eres un INGENIERO SENIOR full-stack. Te paso el CÓDIGO ACTUAL de una app de backoffice
(backend FastAPI `main.py` + frontend vanilla `static/app.js` [y opcional `static/app.css`]),
la SPEC OBJETIVO y una lista de CAMBIOS pedidos. Modificá el código para cumplir la spec y
aplicar los cambios, tocando lo MÍNIMO posible.

REGLAS CLAVE:
- Devolvé SOLO los archivos que REALMENTE modificás. Si un archivo no necesita cambios, NO lo
  devuelvas (se conserva igual). CRÍTICO: si el cambio es solo de UI (color, texto, layout),
  NO toques `main.py` — así el deploy es un refresh instantáneo sin reconstruir.
- Mantené el estilo y la estructura existentes; NO reescribas de cero.
- Backend (`main.py`): FastAPI async, rutas `/api`, mismas convenciones (incl. owner-token de
  conectores si aplica). Si agregás una dependencia pip nueva, devolvé también
  ===FILE:requirements.txt=== con TODAS las extra (no solo la nueva).
- Frontend (`static/app.js`): JS vanilla ES2017 (sin TS/JSX/import/export, sin `?.`/`??`),
  `window.startApp`, global `authFetch`, Chart.js global. Estilos en `static/app.css`.

{_CONNECTORS_DOC}

{_FILE_PROTOCOL}
Devolvé SOLO los archivos cambiados (alguno de: ===FILE:main.py===, ===FILE:requirements.txt===,
===FILE:static/app.js===, ===FILE:static/app.css===)."""


async def edit_code(
    spec: AppSpec, prev: dict[str, Any], edits: list[str], on_stage=None,
) -> dict[str, Any]:
    """Edición INCREMENTAL: parte del código actual (`prev`) y aplica la spec + los cambios
    pedidos (`edits`) con diff mínimo. Devuelve {main_py, static_files, backend_reqs, needed};
    los archivos no tocados se conservan idénticos (clave para disparar el fast-path)."""
    prev_main = prev.get("main_py", "")
    prev_reqs = prev.get("backend_reqs", "") or ""
    prev_static = dict(prev.get("static_files") or {})
    code = {"main.py": prev_main}
    if prev_reqs:
        code["requirements.txt"] = prev_reqs
    code.update(prev_static)
    bundle = "\n".join(f"===FILE:{p}===\n{c}\n===END===" for p, c in code.items())
    pedido = "\n".join(f"- {e}" for e in edits) if edits else "(sin cambios de texto libre; solo alinear a la spec)"
    changed = await _agent(
        EDIT_SYSTEM,
        f"SPEC OBJETIVO:\n{spec.model_dump_json(indent=2)}\n\n"
        f"CAMBIOS PEDIDOS:\n{pedido}\n\n"
        f"CÓDIGO ACTUAL:\n{bundle[:60000]}\n\n"
        "Modificá lo MÍNIMO para cumplir la spec y aplicar los cambios. Devolvé solo los "
        "archivos que cambiaste.",
    )
    main_py, backend_reqs, static_files = prev_main, prev_reqs, dict(prev_static)
    for k, v in changed.items():
        base = k.split("/")[-1]
        if base == "main.py":
            main_py = v
        elif base == "requirements.txt":
            backend_reqs = v
        elif base.endswith(".js"):
            static_files["static/app.js"] = v
        elif base.endswith(".css"):
            static_files["static/app.css"] = v
    needed = [
        {"id": p, "label": PROVIDERS[p].label}
        for p in needed_connectors(spec)
        if p in PROVIDERS
    ]
    return {
        "main_py": main_py,
        "static_files": static_files,
        "backend_reqs": backend_reqs,
        "needed": needed,
    }


async def generate_code(
    spec: AppSpec, on_stage=None, prev: dict[str, Any] | None = None,
    spec_hash: str | None = None, edits: list[str] | None = None, force_full: bool = False,
) -> dict[str, Any]:
    """Produce el código (sin construir): main.py + static/app.js[/app.css] + reqs.

    Decide el modo:
    - REUSE: si `prev` tiene el mismo spec_hash y no hay edits → reusa exacto (sin LLM).
    - INCREMENTAL: si hay código previo (y no force_full) → edita el actual con diff mínimo
      (aplica spec + edits), dejando intactos los archivos no tocados.
    - FULL: si no hay código previo (o force_full) → genera de cero (backend + frontend).
    Devuelve {main_py, static_files, backend_reqs, needed}.
    """
    async def stage(text: str) -> None:
        if on_stage:
            await on_stage(text)

    edits = edits or []
    has_prev = bool(prev and prev.get("main_py") and prev.get("static_files"))

    if has_prev and not force_full and not edits and prev.get("spec_hash") == spec_hash:
        await stage("Reusando código generado (sin regenerar)…")
        return {
            "main_py": prev["main_py"],
            "static_files": dict(prev.get("static_files") or {}),
            "backend_reqs": prev.get("backend_reqs", "") or "",
            "needed": [
                {"id": p, "label": PROVIDERS[p].label}
                for p in needed_connectors(spec) if p in PROVIDERS
            ],
        }

    if has_prev and not force_full:
        await stage("Editando la app (cambios mínimos)…")
        return await edit_code(spec, prev, edits, on_stage=on_stage)

    await stage("Backend dev: generando API…")
    main_py, backend_reqs = await build_backend(spec)
    await stage("Frontend dev: generando UI (HTML/JS/CSS)…")
    static_files = await build_frontend(spec, main_py)
    needed = [
        {"id": p, "label": PROVIDERS[p].label}
        for p in needed_connectors(spec)
        if p in PROVIDERS
    ]
    return {
        "main_py": main_py,
        "static_files": static_files,
        "backend_reqs": backend_reqs,
        "needed": needed,
    }


# import de Python -> nombre del paquete pip (cuando difieren). El resto usa el mismo nombre.
_PIP_NAME = {
    "sklearn": "scikit-learn", "cv2": "opencv-python-headless", "PIL": "Pillow",
    "bs4": "beautifulsoup4", "yaml": "PyYAML", "dateutil": "python-dateutil",
    "dotenv": "python-dotenv", "jose": "python-jose", "Crypto": "pycryptodome",
    "docx": "python-docx", "pptx": "python-pptx", "fitz": "PyMuPDF", "jwt": "PyJWT",
}
# Ya presentes en la imagen base: nunca se agregan a requirements.
_BASE_MODULES = {
    "fastapi", "uvicorn", "httpx", "pydantic", "pydantic_core", "celery", "redis",
    "starlette", "anyio", "click", "h11",
}


def _missing_modules(log: str) -> list[str]:
    """Extrae dependencias faltantes (`ModuleNotFoundError: No module named 'X'`) del log de QA,
    mapeadas al nombre de paquete pip. Auto-fix determinístico para deploys 'vivos'."""
    out: list[str] = []
    seen: set[str] = set()
    for mod in re.findall(r"No module named '([\w.]+)'", log or ""):
        top = mod.split(".")[0]
        if top in _BASE_MODULES:
            continue
        pkg = _PIP_NAME.get(top, top)
        if pkg.lower() not in seen:
            seen.add(pkg.lower())
            out.append(pkg)
    return out


async def qa_and_fix(
    slug: str, app_id: str, artifacts: dict[str, Any], on_stage=None, max_fixes: int = 2,
) -> dict[str, Any]:
    """QA del código dado: (build imagen + smoke test) → loop de fixes acotado. Al terminar
    OK, la imagen ya está construida; el caller corre el contenedor con run_containers.
    Lanza RuntimeError si QA no pasa. Devuelve los artifacts (con fixes aplicados)."""
    async def stage(text: str) -> None:
        if on_stage:
            await on_stage(text)

    main_py = artifacts["main_py"]
    backend_reqs = artifacts.get("backend_reqs", "") or ""
    static_files = dict(artifacts.get("static_files") or {})

    last_err = ""
    llm_left = max_fixes  # fixes con LLM (sintaxis/lógica/contrato)
    dep_left = 3  # auto-fix DETERMINÍSTICO de dependencias (no consume budget del LLM)
    attempt = 0
    while True:
        await stage(
            "QA: construyendo y probando (puede tardar ~1 min)…"
            if attempt == 0
            else f"QA: reintento {attempt} (corrigiendo y reconstruyendo)…"
        )
        ok, log = await asyncio.to_thread(
            deploy.qa_check, slug, app_id, main_py, static_files, backend_reqs,
        )
        if ok:
            return {
                "main_py": main_py,
                "static_files": static_files,
                "backend_reqs": backend_reqs,
                "needed": artifacts.get("needed", []),
                "qa_attempts": attempt,
            }
        last_err = log
        logger.warning("QA intento %d FALLÓ para %s:\n%s", attempt, slug, log[-3500:])
        attempt += 1

        # 1) Auto-fix DETERMINÍSTICO: dependencia faltante -> agregar a requirements.txt y
        #    reconstruir (deploy 'vivo': se cura solo, sin gastar fixes del LLM).
        new_deps = [d for d in _missing_modules(log) if d.lower() not in backend_reqs.lower()]
        if new_deps and dep_left > 0:
            dep_left -= 1
            backend_reqs = (backend_reqs.rstrip() + "\n" + "\n".join(new_deps)).strip() + "\n"
            await stage(f"QA: agregando dependencia faltante ({', '.join(new_deps)}) y reconstruyendo…")
            continue

        # 2) Fix con LLM (sintaxis/lógica/contrato), acotado.
        if llm_left > 0:
            llm_left -= 1
            await stage("QA: corrigiendo errores…")
            fixed = await qa_fix(main_py, static_files, log)
            if "main.py" in fixed:
                main_py = fixed["main.py"]
            for k, v in fixed.items():
                base = k.split("/")[-1]
                if base == "requirements.txt":
                    backend_reqs = v
                elif base.endswith(".js"):
                    static_files["static/app.js"] = v
                elif base.endswith(".css"):
                    static_files["static/app.css"] = v
            continue

        break

    raise RuntimeError(f"QA no pasó tras {max_fixes} fix(es). Último error:\n{last_err[:1500]}")


async def run_and_qa(
    spec: AppSpec, slug: str, app_id: str, max_fixes: int = 2, on_stage=None,
    prev: dict[str, Any] | None = None, spec_hash: str | None = None,
    edits: list[str] | None = None, force_full: bool = False,
) -> dict[str, Any]:
    """Pipeline completo: genera/edita/reusa + QA con build/smoke + fixes."""
    artifacts = await generate_code(
        spec, on_stage=on_stage, prev=prev, spec_hash=spec_hash,
        edits=edits, force_full=force_full,
    )
    return await qa_and_fix(slug, app_id, artifacts, on_stage=on_stage, max_fixes=max_fixes)
