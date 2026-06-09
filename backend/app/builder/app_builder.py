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
    data_sources: list[str] = Field(default_factory=list)  # bigquery, google_sheets, postgres, slack…
    entities: list[Entity] = Field(default_factory=list)
    screens: list[Screen] = Field(default_factory=list)
    jobs: list[Job] = Field(default_factory=list)
    notifications: list[str] = Field(default_factory=list)  # slack, notion


# ============================================================================
# Prompts especializados (devs senior)
# ============================================================================

_FILE_PROTOCOL = """\
OUTPUT FORMAT (exact, no text outside the blocks, no explanations):
===FILE:relative/path===
<full file content>
===FILE:another/path===
<content>
===END==="""

# ENTERPRISE model: the app uses the OWNER's credentials (not the viewer's).
_CONNECTORS_DOC = """\
CONNECTORS (external data) — ENTERPRISE MODEL: the app uses the OWNER's credentials (the ones they
connected in the builder), NOT the viewer's. The viewer only needs access (allowlist). Do NOT
forward the viewer's header nor ask them to connect anything. `INTERNAL_GATEWAY`, `APP_ID`,
`APP_SECRET` are in os.environ (use INTERNAL_GATEWAY, NOT AUTH_GATEWAY, for server→server calls).

=== CONNECTOR PROXY (PREFERRED — always use it when the provider supports it) ===
The platform RUNS the operation with the owner's token and returns ONLY the result: the token NEVER
reaches the app (more secure). You call it (server-side, httpx async):
    POST {INTERNAL_GATEWAY}/api/apps/{APP_ID}/mcp/<provider>/<tool>   (header X-App-Secret: <APP_SECRET>)
    body {"arguments": {...}}   ->   {"ok": bool, "text": "<JSON>", "result": {...}}
Read `result` (structured object) or parse `text` (it's `result` as JSON). If `ok` is false, the
`text` field has the error detail. If the POST returns 409, the owner hasn't connected that service:
respond 503 with a clear message. Recommended helper (paste it into main.py):
    async def connector(provider, tool, arguments=None):
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{os.environ['INTERNAL_GATEWAY']}/api/apps/{os.environ['APP_ID']}/mcp/{provider}/{tool}",
                             headers={"X-App-Secret": os.environ["APP_SECRET"]},
                             json={"arguments": arguments or {}})
        if r.status_code == 409:
            raise HTTPException(503, "The app owner hasn't connected this service.")
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise HTTPException(502, data.get("text") or "connector error")
        return data.get("result") or {}

Providers via PROXY and their tools (name + arguments; optional ones with ?):
  google_sheets:
    find_spreadsheets{query} -> {files:[{id,name}]}
    create_spreadsheet{title, headers?} -> {spreadsheet_id, title, url}
    get_metadata{spreadsheet_id} -> {title, sheets:[{sheetId,title,rows,cols}]}
    read_range{spreadsheet_id, range} -> {values:[[...]]}        (A1 range, e.g. "Sheet1!A1:C10")
    update_range{spreadsheet_id, range, values}                  (overwrite)
    append_rows{spreadsheet_id, range, values}                   (append at the end)
    clear_range{spreadsheet_id, range}
    delete_rows_where{spreadsheet_id, sheet_name, column, equals} -> {deleted:N}  (row 1 = headers)
    delete_tab{spreadsheet_id, sheet_name}
    WATCH the TAB NAME: it varies by language/locale (Spanish is often "Hoja 1" WITH a space,
    English "Sheet1"). NEVER hardcode it. On startup, call `get_metadata` and use the real `title`
    of the first tab (`sheets[0].title`) to build ranges; or, if you only need the first sheet, OMIT
    the tab in the range (e.g. "A1:H" instead of "Sheet1!A1:H"). Quote the tab with single quotes if
    it has spaces: "'Hoja 1'!A1:H".
  google_drive:
    search{query?, page_size?} -> {files:[{id,name,mimeType,modifiedTime,size}]}
    list_folder{folder_id, page_size?} -> {files:[...]}
    get_file{file_id} -> {id,name,mimeType,...,webViewLink}
    read_file{file_id} -> {name, content}                        (Docs/Sheets/Slides exported as text)
    create_folder{name, parent?} -> {id,name,webViewLink}
    create_file{name, content?, mime?, parent?} -> {id,name,webViewLink}
    update_file{file_id, content, mime?}
    rename{file_id, name}   move{file_id, new_parent}   copy_file{file_id, name?}
    delete{file_id, permanent?}   share{file_id, email?, role?, anyone?}
  google_docs:
    create{title} -> {documentId, title}                         (URL: https://docs.google.com/document/d/<documentId>/edit)
    read_text{document_id} -> {title, text}
    insert_text{document_id, text, index?}   append_text{document_id, text}
    replace_text{document_id, find, replace}
  gmail:
    search{query?, max_results?} -> {messages:[{id,from,subject,date,snippet}]}
    read_message{message_id} -> {from,to,subject,date,body}
    send{to, subject, body}   create_draft{to, subject, body}
  google_calendar:
    list_calendars{} -> {calendars:[{id,summary,primary}]}
    list_events{calendar_id?, time_min?, time_max?, query?, max_results?} -> {events:[{id,summary,start,end,location}]}
    create_event{summary, start, end, calendar_id?, description?, location?, attendees?}   (start/end: "YYYY-MM-DD" all-day or ISO with time)
    update_event{event_id, calendar_id?, summary?, description?, location?, start?, end?}
    delete_event{event_id, calendar_id?} -> {deleted}
  notion, intercom, miro, bigquery, slack: also via PROXY (hosted/self-hosted MCP). Use the EXACT
    tool names of each provider's MCP. If you get the tool wrong, the proxy responds with the LIST
    of available tools and their required args (field result.available_tools and in `text`): read it
    and retry with the correct name/args. Initial guide:
      bigquery: `execute_sql` with {"sql": "SELECT ..."} (do NOT use query/run_query/execute). To
        discover schema: list_dataset_ids, list_table_ids, get_table_info.
      notion: search, fetch, create-pages, update-page
      slack: post/read messages and channels (names like post_message/list_channels)
  postgres: the app's OWN DATABASE (managed Postgres, dedicated and PERSISTENT schema). Use it when
    the app needs to store its own state/data (not an external sheet). Tools: `execute_sql{sql}`
    (SELECT/WITH/RETURNING → {rows,rowcount}; others → {status}; supports CREATE TABLE/INSERT/
    UPDATE/DELETE), `list_tables{}`, `describe_table{table}`. Use table names WITHOUT prefix (the
    search_path already points to the app's schema). Create the tables you need (idempotent with
    CREATE TABLE IF NOT EXISTS) on startup. Requires data_source "postgres".
Example: `data = await connector("google_sheets", "read_range", {"spreadsheet_id": sid, "range": "Sheet1!A1:E"})` ; `rows = data["values"]`.

ALL external connectors go through the CONNECTOR PROXY. There is NO owner-token: NEVER request a raw
token nor call the provider's API directly. If the proxy returns 409, the owner hasn't connected that
service: respond 503 with a clear message. This pattern works the same in endpoints and in Celery
tasks (tasks run WITHOUT a viewer session, but still use the app secret).

AI / LLM ("smart" features: summarize, classify, extract data, draft, analyze documents): do NOT use
your own API keys nor call OpenAI/Anthropic/Gemini directly. Use the platform PROXY (server-side,
with the app secret):
    POST {INTERNAL_GATEWAY}/api/apps/{APP_ID}/llm   (header X-App-Secret: <APP_SECRET>)
    body: {"model": "<model>", "messages": [{"role":"user","content":"..."}],
           "system": "<optional>", "max_tokens": 1024, "temperature": 0.3}
    -> 200 {"text": "...", "provider": "...", "model": "...", "usage": {...}}
Allowed models: claude-haiku-4-5 (default, cheap), claude-sonnet-4-6, gpt-4o-mini, gpt-4o,
gemini-2.5-flash, gemini-2.5-pro. For DOCUMENTS/IMAGES as INPUT, `content` can be a list of parts:
{"type":"text","text":...}, {"type":"image","mime":"image/png","data":"<base64>"},
{"type":"document","mime":"application/pdf","data":"<base64>"} (PDF only with claude-*/gemini-*).
IMAGE GENERATION & EDITING (Nano Banana): use model "gemini-2.5-flash-image" (or
"gemini-3.1-flash-image" / "gemini-3-pro-image" for higher quality). Put the prompt in a text part;
to EDIT, also pass the source image as an {"type":"image",...} part. The response adds
"images": [{"mime":"image/png","data":"<base64>"}] — render with `<img src="data:{mime};base64,{data}">`
or persist the bytes. Do NOT set max_tokens for image models. The proxy enforces a daily per-app cap
and audits every call. If it returns 429, tell the user the quota was reached; if 400, the model is
not allowed."""

BACKEND_SYSTEM = f"""\
You are a SENIOR BACKEND ENGINEER. You generate the backend of a real back-office app with
FastAPI FULL ASYNC, in a SINGLE `main.py` file. Rules:
- `app = FastAPI()`; ALL endpoints `async def`, under the `/api` prefix.
- `GET /api/health` -> {{"status":"ok"}}. Open CORS (CORSMiddleware allow_origins=["*"]).
- Pydantic v2 for input/output models. Correct typing and validation.
- ROBUSTNESS + ASYNC (senior level): NEVER block the event loop — use `httpx.AsyncClient` (not
  `requests`), async I/O always; for unavoidable sync libs (pandas, heavy parsing) use
  `await asyncio.to_thread(...)`; parallelize independent work with `asyncio.gather(...)`; put
  TIMEOUTS on every external call. Errors with the correct HTTP code (400 validation, 404 not
  found, 409 conflict, 502 upstream, 503 connector not connected), never swallow exceptions.
  Idempotent writes; large reads PAGINATED and filtered/aggregated AT THE SOURCE (SQL GROUP BY/
  WHERE, not in memory); avoid N+1. Cache in memory (short TTL) what's expensive and requested
  often. Validate/parametrize user input (anti-injection).
- Implement the endpoints that cover the spec's screens/actions (list/get/create/update/delete/
  export/notify as appropriate). Error handling with HTTPException.
- To read/write external data, use the connectors as described below. To read the session header
  use a parameter `authorization: str = Header(None)` and forward it.
- SCHEDULED TASKS (only if the spec has `jobs`): define at module level
  `celery_app = Celery("app", broker=os.environ.get("CELERY_BROKER_URL","redis://localhost:6379/0"), backend=...)`
  with `celery_app.conf.beat_schedule` (one entry per job, using `crontab(...)` from
  `celery.schedules` parsed from the job's cron) and the tasks `@celery_app.task(name="main.<x>")`.
  WARNING! tasks run WITHOUT a user session -> they CANNOT use per-user connector tokens; limit
  them to internal/compute work or to recording results (logging, saving state). If there are NO
  jobs, do not include Celery. Never use os.environ[...] without a default (it would break import).
- Base deps are ALREADY installed (fastapi, uvicorn, httpx, pydantic, celery, redis). ANY other
  library you import (e.g. pandas, numpy, openpyxl, python-docx, Pillow, reportlab, matplotlib,
  beautifulsoup4) is NOT installed and you MUST declare it in ===FILE:requirements.txt=== (one per
  line, version optional) or the backend will NOT start (ModuleNotFoundError). Rule: if you import
  it, it goes in requirements.txt. If you don't use extras, do NOT emit requirements.txt. No stubs.
- EXECUTION LOGS (ALWAYS, in EVERY app): keep an in-memory buffer of events/errors and expose
  `GET /api/_logs` -> {{"logs": [{{"ts","level","message"}}, ...]}} (newest last). Define at module
  level something like:
    `_LOGS = []`
    `def _log(level, message): _LOGS.append({{"ts": datetime.now(timezone.utc).isoformat(), "level": level, "message": str(message)[:500]}}); del _LOGS[:-300]`
  and USE it on every important action/process: success (`_log("info", ...)`) and ESPECIALLY in the
  `except` blocks (`_log("error", f"... {{exc}}")`) capturing the real error detail (including the
  text of 4xx/5xx responses from connectors/LLM). Goal: the user SEES in the app what failed so they
  can iterate with the builder. If the app already has a history with statuses/errors, still expose
  `/api/_logs` with the recent execution events.

{_CONNECTORS_DOC}

{_FILE_PROTOCOL}
Return ===FILE:main.py=== (and ===FILE:requirements.txt=== only if you use extra pip libs)."""

FRONTEND_SYSTEM = f"""\
You are a SENIOR FRONTEND ENGINEER. You generate the frontend of a real dashboard/back-office in
VANILLA HTML/JS/CSS (no build, no framework, no TypeScript):
- Main file `static/app.js` (a CLASSIC <script>). Define
  `window.startApp = function () {{ … }}`: the auth gate calls it once the user is logged in and
  authorized. Inside, render the UI into `document.getElementById("app")`.
- To call the backend ALWAYS use `window.authFetch(path, init)` (REFERENCE IT AS `window.authFetch`,
  not bare `authFetch` — the gate defines it so it never throws "authFetch is not defined"). It
  already adds the session and is same-origin as the API:
  `window.authFetch("/api/...").then(function (r) {{ return r.json(); }})`.
  Do NOT implement login nor manage tokens (the gate already does it).
- CONTRACT = TRUTH: the backend `main.py` (passed to you in full) is the source. For EACH endpoint
  you consume, find its `return {{...}}` and use the EXACT key/field names (character by character).
  Do NOT invent or translate names: if the backend returns `cumulative_customers_over_time` do not
  read `cumulative_customers`; if a row has `{{"month": ..., "new_customers": ...}}` do not read
  `count`. A name that doesn't match = EMPTY chart or table even if the data arrives. Before mapping,
  verify the key exists in the JSON.
- Browser-compatible JS (ES2017): NO TypeScript, JSX, nor `import`/`export` (it's a classic script).
  Avoid optional chaining (`?.`) and nullish (`??`); use `&&`/`||` and explicit checks. Manipulate
  the DOM with document.createElement / innerHTML.
- Charts: Chart.js is ALREADY available as the global `Chart` (don't import or load it). Use it for
  bar/line/pie charts when the spec asks for metrics or dashboards (sober palette aligned to the
  accent, white background, discreet legends).
- DESIGN (MANDATORY STANDARD — the UI must be BEAUTIFUL, MINIMALIST and MODERN): WHITE background
  (#ffffff), plenty of whitespace, clear hierarchy, subtle 1px borders, VERY light shadows, soft
  radii, a SINGLE accent tint, system typography (weights 500–700 for titles). Define design tokens
  in `:root` (--bg, --surface, --text, --muted, --border, --accent, --radius, --shadow, --font) in
  `static/app.css` and REUSE them everywhere; smooth transitions (~.15s) and visible `:focus`/
  `:hover` states. NO loud gradients, strong colors or decorative emojis.
- The NATIVE `<select>` is FORBIDDEN: implement a custom DROPDOWN (button + dropdown panel, with
  click-outside, keyboard, hover and animation) in the same visual language as the rest.
- DATES: nothing of the ugly default datepicker. Style the `input[type=date]` (same border/radius/
  focus as the other fields) or, better, a custom mini-calendar (month grid, ‹ › navigation, chosen
  day with accent background, today with a border).
- NEVER use `alert()`, `confirm()` or `prompt()`. For confirmations/blocking messages create a
  beautiful MODAL (overlay with light blur + centered card: title, message, Cancel/Confirm; the
  destructive button in red; closes with `Esc` and outside click). For non-blocking notices (saved
  OK, network error) use a floating, auto-dismissing TOAST (~3s). Deletion is ALWAYS confirmed via modal.
- Implement EXACTLY the spec's screens consuming the backend endpoints: tables with search/filters
  (using the custom dropdown), KPIs/charts, forms with validation for create/update, delete
  confirmation via MODAL, and loading/empty/error states on every screen (never a blank screen).
  Valid JS (ES2017), no syntax errors.
- ALL the generated app UI TEXT MUST be in ENGLISH (labels, buttons, titles, loading/error states,
  messages, chart titles): "Loading…", "Save", "Update", "Search", "Delete", "Create", "Close", etc.
  ALWAYS in English, regardless of the language of the user's request.
- LOGS PANEL (ALWAYS, in EVERY app): include a FIXED "Execution log" panel at the bottom (position:
  fixed; bottom 0; full width), COLLAPSIBLE: a clickable header with a ▲/▼ indicator that expands/
  collapses the body; starts COLLAPSED (only the title bar, not covering the app). On expand: call
  `authFetch("/api/_logs")`, show the lines with their `ts` (time) and `message`, highlighting in RED
  those with `level==="error"`; refresh every ~4s while open (stopping the interval on collapse) and
  offer a "Refresh" button. The body scrolls with a bounded height (e.g. 220px). ORDER: chronological,
  OLDEST AT TOP and newest at the bottom. SCROLL: on refresh do NOT force-reposition the scroll;
  preserve the user's reading position and only auto-scroll to the bottom if they were ALREADY at the
  bottom (threshold ~40px: `el.scrollHeight - el.scrollTop - el.clientHeight < 40`). Purpose: the user
  sees their processes' execution errors and can iterate with the builder. Style the panel in
  static/app.css.

{_FILE_PROTOCOL}
Always include ===FILE:static/app.js=== (+ ===FILE:static/app.css=== if needed)."""

QA_SYSTEM = f"""\
You are a SENIOR QA ENGINEER. I give you the current code and the ERRORS found by automated testing:
build errors, container runtime/logs, JS syntax check, and — importantly — a FUNCTIONAL ENDPOINT
TEST that calls the app's real /api endpoints and reports any that return HTTP 500 (a backend bug),
plus the app's own execution-log errors. Diagnose the ROOT CAUSE and return ONLY the corrected files
(complete), minimal to fix the problem, WITHOUT breaking what works or adding features. Make the
failing endpoints actually work end to end (correct params, connector usage, response shape).

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
    # Base de datos PROPIA de la app (Postgres gestionado, schema por app). No es un conector
    # OAuth: se aprovisiona en el deploy y se usa vía el connector-proxy (provider "postgres").
    "postgres": "postgres",
    "database": "postgres",
}


def needed_connectors(spec: AppSpec) -> list[str]:
    srcs = set(spec.data_sources) | set(spec.notifications)
    return sorted({DATA_SOURCE_TO_PROVIDER[s] for s in srcs if s in DATA_SOURCE_TO_PROVIDER})


def _fmt_tool(t: dict) -> str:
    """'name(req, opt?)' a partir del input_schema (los opcionales con ?)."""
    schema = t.get("input_schema") or {}
    props = list((schema.get("properties") or {}).keys())
    req = set(schema.get("required") or [])
    args = ", ".join(p if p in req else f"{p}?" for p in props)
    return f"{t['name']}({args})"


async def connector_tools_doc(spec: AppSpec) -> str:
    """Catálogo REAL de tools de los conectores que la app necesita (introspección en vivo).

    Para los MCP locales (Google) es in-process e instantáneo; para los hosted/self-hosted
    (BigQuery, Slack, Notion, Intercom, Miro) usa la conexión del DUEÑO (el `use_user` vigente).
    Best-effort: si un provider no está conectado o falla, se omite (el runtime igual auto-documenta
    las tools al errar). Así el modelo genera con los nombres/args EXACTOS desde el inicio.
    """
    from ..mcp.catalog import load_catalog
    from ..mcp.servers import get_local_server

    from ..mcp import state as connector_state

    catalog = load_catalog()
    blocks: list[str] = []
    for pid in needed_connectors(spec):
        key = pid.replace("-", "_")
        if not connector_state.is_enabled(key):
            continue  # conector deshabilitado por el admin (Manager)
        tools: list[dict] = []
        try:
            local = get_local_server(key)
            if local is not None:
                tools = local.list_tools()
            elif key in catalog and catalog[key].transport in ("hosted", "self_hosted"):
                from ..mcp import client as mcp_client

                tools = await mcp_client.list_tools(key, quick=True)
        except Exception:  # noqa: BLE001 — best-effort; nunca rompe la generación
            tools = []
        if not tools:
            continue
        blocks.append(f"  {key}:\n" + "\n".join(f"    {_fmt_tool(t)}" for t in tools))

    if not blocks:
        return ""
    return (
        "REAL TOOL CATALOG of THIS app's connectors (live MCP introspection). Call via the "
        "connector-proxy with EXACTLY these names and arguments (? = optional):\n"
        + "\n".join(blocks)
    )


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


async def _agent_raw(system: str, prompt: str, max_tokens: int = 32000) -> str:
    # Streaming: la SDK lo exige para max_tokens alto (requests potencialmente largos).
    async with get_client().messages.stream(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        message = await stream.get_final_message()
    return "".join(b.text for b in message.content if b.type == "text")


async def _agent(system: str, prompt: str, max_tokens: int = 32000) -> dict[str, str]:
    return parse_files(await _agent_raw(system, prompt, max_tokens))


# --- Ediciones quirúrgicas por SEARCH/REPLACE (diffs) ---------------------------
# El editor devuelve bloques que cambian SOLO regiones puntuales; todo lo demás queda byte a
# byte igual (clave para que sea predecible y para disparar el fast-path de solo-UI).
_SR_RE = re.compile(
    r"===EDIT:(?P<path>[^\n=]+?)===[ \t]*\n"
    r"<<<<<<< SEARCH\n(?P<search>.*?)\n=======\n(?P<replace>.*?)\n>>>>>>> REPLACE",
    re.DOTALL,
)


def _fuzzy_apply(text: str, search: str, replace: str) -> str | None:
    """Aplica el reemplazo tolerando diferencias de espacios/indentación, SOLO si el patrón
    aparece EXACTAMENTE una vez (para no tocar la región equivocada). Devuelve el texto nuevo o None."""
    pattern = re.sub(r"\s+", r"\\s+", re.escape(search.strip()))
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        return None
    m = matches[0]
    return text[: m.start()] + replace + text[m.end():]


def apply_edits(
    prev_files: dict[str, str], text: str
) -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    """Aplica a `prev_files` los bloques ===EDIT: (SEARCH/REPLACE) y ===FILE: (archivo completo).
    Devuelve (archivos_nuevos, bloques_no_aplicados). Los archivos no mencionados quedan idénticos."""
    result = dict(prev_files)
    # 1) Reemplazos de archivo COMPLETO (último recurso del modelo).
    for path, content in parse_files(text).items():
        result[path] = content
    # 2) Bloques SEARCH/REPLACE: exacto → fuzzy (1 sola coincidencia) → no aplicado.
    unmatched: list[tuple[str, str, str]] = []
    for m in _SR_RE.finditer(text):
        path = m.group("path").strip()
        search, replace = m.group("search"), m.group("replace")
        cur = result.get(path)
        if cur is None:
            unmatched.append((path, search, replace))
            continue
        if cur.count(search) == 1:
            result[path] = cur.replace(search, replace, 1)
        else:
            fixed = _fuzzy_apply(cur, search, replace)
            if fixed is None:
                unmatched.append((path, search, replace))
            else:
                result[path] = fixed
    return result, unmatched


async def build_backend(spec: AppSpec) -> tuple[str, str]:
    """Devuelve (main.py, requirements.txt extra)."""
    tools_doc = await connector_tools_doc(spec)
    prompt = (
        "Generate the backend (main.py) for this back-office app spec:\n\n"
        f"{spec.model_dump_json(indent=2)}"
    )
    if tools_doc:
        prompt += f"\n\n{tools_doc}"
    files = await _agent(BACKEND_SYSTEM, prompt)
    main_py = files.get("main.py") or next(
        (v for k, v in files.items() if k != "requirements.txt"), ""
    )
    if "FastAPI" not in main_py:
        raise RuntimeError("The backend dev did not produce a valid FastAPI app.")
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
        "Generate the frontend (vanilla HTML/JS/CSS) for this spec, consuming the backend API.\n\n"
        f"SPEC:\n{spec.model_dump_json(indent=2)}\n\n"
        "BACKEND CONTRACT (FULL main.py — use the EXACT key/field names from each `return`):\n"
        f"{main_py[:20000]}",
    )
    static = _static_files(files)
    if "static/app.js" not in static:
        raise RuntimeError("The frontend dev did not produce static/app.js.")
    return static


async def qa_fix(
    main_py: str, static_files: dict[str, str], error_log: str
) -> dict[str, str]:
    """El QA dev devuelve los archivos corregidos a partir del código + los errores."""
    code = {"main.py": main_py, **static_files}
    bundle = "\n".join(f"===FILE:{p}===\n{c}\n===END===" for p, c in code.items())
    prompt = (
        f"CURRENT CODE:\n{bundle[:60000]}\n\n"
        f"ERRORS detected by QA (build / runtime / endpoint tests):\n{error_log[:6000]}\n\n"
        "Return ONLY the corrected files."
    )
    return await _agent(QA_SYSTEM, prompt)


EDIT_SYSTEM = f"""\
You are a SENIOR full-stack engineer making a SURGICAL, MINIMAL edit to an EXISTING back-office app
(FastAPI `main.py` + vanilla `static/app.js` [+ optional `static/app.css`]).

You receive the REQUESTED CHANGES and the CURRENT CODE. Apply ONLY what is requested.
Do NOT refactor, re-style, rename, reorder, "improve", reformat, or change ANYTHING that wasn't
explicitly requested. Everything not requested MUST stay byte-for-byte identical. Predictability is
the priority: a request to change X must change ONLY X.

Return the changes as SEARCH/REPLACE edit blocks — NOT whole files. Exact format:

===EDIT:<path>===
<<<<<<< SEARCH
<snippet copied VERBATIM from the current file — include enough surrounding lines to be UNIQUE>
=======
<the replacement for that exact snippet>
>>>>>>> REPLACE

Rules:
- Copy the SEARCH text EXACTLY from the current file (same indentation and spacing). It must occur
  exactly ONCE; add a few surrounding lines if needed to make it unique.
- Make the SMALLEST possible change. Prefer several tiny blocks over one big block.
- You may emit multiple ===EDIT:<path>=== blocks (same file or different files).
- UI-only change? Edit ONLY `static/app.js` / `static/app.css` — do NOT touch `main.py` (so the
  deploy is an instant UI refresh, no rebuild).
- New pip dependency? Emit a FULL `===FILE:requirements.txt===` … `===END===` with ALL extras.
- ONLY if a change is so large that surgical blocks are impractical, emit a FULL
  `===FILE:<path>===` … `===END===` with the complete file (LAST RESORT, avoid it).
- Keep every existing convention: connectors via the CONNECTOR PROXY (no owner-token), vanilla
  ES2017, `window.startApp`, global `authFetch`/`Chart`, the mandatory design standard
  (white/minimalist, custom dropdowns, modal/toast instead of alert), English UI.

{_CONNECTORS_DOC}

Output ONLY edit/file blocks. No prose, no explanations."""


# Fallback cuando el SEARCH/REPLACE no aplica (no matchea el código actual): se pide el/los
# archivo(s) COMPLETOS para GARANTIZAR que el cambio pedido se aplique (fiabilidad > minimalismo).
EDIT_FULL_SYSTEM = f"""\
You are a SENIOR full-stack engineer applying the REQUESTED CHANGES to an EXISTING back-office app
(FastAPI `main.py` + vanilla `static/app.js` [+ optional `static/app.css`]).

Apply the requested changes RELIABLY — the change MUST actually take effect. Change only what is
requested and keep everything else as close to the original as possible (same structure and
conventions); do not rewrite unrelated parts.

Return the COMPLETE updated content of ONLY the files you change, using:
===FILE:<path>===
<full file content>
===END===
(any of main.py, static/app.js, static/app.css, requirements.txt). Keep every convention: connectors
via the CONNECTOR PROXY (no owner-token), vanilla ES2017, `window.startApp`, global
`authFetch`/`Chart`, the mandatory design standard (white/minimalist, custom dropdowns, modal/toast
instead of alert), English UI.

{_CONNECTORS_DOC}

Output ONLY file blocks. No prose."""


async def edit_code(
    spec: AppSpec, prev: dict[str, Any], edits: list[str], on_stage=None,
) -> dict[str, Any]:
    """Edición INCREMENTAL: parte del código actual (`prev`) y aplica la spec + los cambios
    pedidos (`edits`) con diff mínimo. Devuelve {main_py, static_files, backend_reqs, needed};
    los archivos no tocados se conservan idénticos (clave para disparar el fast-path)."""
    prev_main = prev.get("main_py", "")
    prev_reqs = prev.get("backend_reqs", "") or ""
    prev_static = dict(prev.get("static_files") or {})
    prev_files: dict[str, str] = {"main.py": prev_main}
    if prev_reqs:
        prev_files["requirements.txt"] = prev_reqs
    prev_files.update(prev_static)  # static/app.js [, static/app.css]

    def _bundle(files: dict[str, str]) -> str:
        return "\n".join(f"===FILE:{p}===\n{c}\n===END===" for p, c in files.items())

    pedido = "\n".join(f"- {e}" for e in edits) if edits else "(no specific changes requested)"
    tools_doc = await connector_tools_doc(spec)
    raw = await _agent_raw(
        EDIT_SYSTEM,
        f"REQUESTED CHANGES:\n{pedido}\n\n"
        + (f"{tools_doc}\n\n" if tools_doc else "")
        + f"CURRENT CODE:\n{_bundle(prev_files)[:60000]}\n\n"
        "Apply ONLY the requested changes as SEARCH/REPLACE edit blocks. Do not change anything else.",
    )
    new_files, unmatched = apply_edits(prev_files, raw)

    # FALLBACK DETERMINÍSTICO: si el surgical no aplicó TODO (bloques sin match) o no cambió NADA,
    # reescribimos el/los archivo(s) COMPLETOS para GARANTIZAR que el cambio pedido se aplique.
    # (Evita el "deploy fantasma": antes, si los SEARCH no matcheaban, no cambiaba nada en silencio.)
    no_change = new_files == prev_files
    if edits and (unmatched or no_change):
        logger.warning(
            "edit_code: surgical incompleto (sin_match=%d, sin_cambios=%s) → fallback a archivo completo.",
            len(unmatched), no_change,
        )
        try:
            raw_full = await _agent_raw(
                EDIT_FULL_SYSTEM,
                f"REQUESTED CHANGES:\n{pedido}\n\n"
                + (f"{tools_doc}\n\n" if tools_doc else "")
                + f"CURRENT CODE:\n{_bundle(prev_files)[:60000]}\n\n"
                "Return the COMPLETE updated content of ONLY the files you change. The requested "
                "changes MUST take effect.",
            )
            full = parse_files(raw_full)
            if full:
                merged = dict(prev_files)
                merged.update(full)  # archivos completos = reemplazo total del archivo
                new_files = merged
        except Exception:  # noqa: BLE001 — el fallback es best-effort; si falla, queda lo surgical
            pass

    main_py = new_files.get("main.py", prev_main)
    backend_reqs = new_files.get("requirements.txt", prev_reqs)
    static_files = {k: v for k, v in new_files.items() if k.startswith("static/")}
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
        await stage("Reusing generated code (no regeneration)…")
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
        await stage("Editing the app (minimal changes)…")
        return await edit_code(spec, prev, edits, on_stage=on_stage)

    await stage("Backend dev: generating the API…")
    main_py, backend_reqs = await build_backend(spec)
    await stage("Frontend dev: generating the UI…")
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
            "QA: building and testing (this may take ~1 min)…"
            if attempt == 0
            else f"QA: retry {attempt} (fixing and rebuilding)…"
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

        # 1) DETERMINISTIC auto-fix: missing dependency -> add it to requirements.txt and rebuild
        #    (self-healing, without spending the LLM fix budget).
        new_deps = [d for d in _missing_modules(log) if d.lower() not in backend_reqs.lower()]
        if new_deps and dep_left > 0:
            dep_left -= 1
            backend_reqs = (backend_reqs.rstrip() + "\n" + "\n".join(new_deps)).strip() + "\n"
            await stage(f"QA: adding missing dependency ({', '.join(new_deps)}) and rebuilding…")
            continue

        # 2) LLM fix (syntax/logic/contract/endpoint failures), bounded.
        if llm_left > 0:
            llm_left -= 1
            await stage("QA: fixing errors…")
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

    raise RuntimeError(f"QA did not pass after {max_fixes} fix(es). Last error:\n{last_err[:1500]}")


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
