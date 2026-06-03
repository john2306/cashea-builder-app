# Cashea Hub App

Plataforma para **construir y desplegar aplicaciones reales conversando**. Describís lo que
necesitás en lenguaje natural y un agente (Claude — Anthropic, `claude-opus-4-8`) **diseña** la
app; al pulsar **Desplegar**, un pipeline genera un stack real (FastAPI + UI web) y lo publica en
su propio subdominio. Incluye SSO con Google, conectores a servicios externos (Google Workspace,
Notion, Slack, BigQuery, …), un **proxy LLM** para features inteligentes dentro de las apps,
búsqueda web, y una bitácora de auditoría.

> **Construir ≠ ejecutar:** el agente del chat **diseña** la app (`define_app`); la **app
> desplegada** es la que en runtime lee correos, escribe en Sheets, crea páginas, llama a la IA,
> etc. — con las credenciales del **dueño**, heredadas automáticamente.

---

## Arquitectura

```
                         (build & deploy por chat)
┌──────────────┐  POST /api/agent/run   ┌─────────────────────┐   tareas largas   ┌──────────┐
│  React + Vite │ ─────────────────────► │      FastAPI        │ ────────────────► │  Celery  │
│   (builder)   │  SSE (replay/resume)   │   (full async)      │ ◄── progreso ──── │  worker  │
│   :5180       │ ◄───────────────────── │  loop agente Claude │   (Redis pub/sub) └────┬─────┘
└──────────────┘   tokens/thinking/     │  + proxy LLM        │                        │
                   tool_use/result      └─────────┬───────────┘                        │
                                                  │                                     │
                              ┌───────────────────┼─────────────────────┐              │
                        ┌─────▼──────┐      ┌──────▼──────┐       ┌───────▼──────┐ ┌─────▼─────┐
                        │ PostgreSQL │      │   Redis     │       │   Traefik    │ │  Docker   │
                        │ (conv/apps)│      │ (stream/bus)│       │  :5173       │ │  (apps +  │
                        └────────────┘      └─────────────┘       │ <slug>.local │ │  MCP)     │
                                                                  └──────┬───────┘ └───────────┘
                                                                         │
                                                          ┌──────────────▼──────────────┐
                                                          │  app-<slug>  (FastAPI + UI)  │
                                                          │  SSO Google · owner-token    │
                                                          └──────────────────────────────┘
```

- **FastAPI (full async)** ejecuta el bucle agéntico de Claude con **streaming**, *thinking*
  adaptativo, `effort: high`, **prompt caching** y **web search nativa**.
- **Run desacoplado del transporte:** el run corre en background y publica eventos a un **Redis
  Stream**; el cliente los consume por **SSE** y **reanuda** con `Last-Event-ID` (sobrevive a
  desconexiones; se puede **Detener**).
- **Celery + Redis** para tareas largas (deploy, jobs) con progreso en tiempo real.
- **PostgreSQL** (SQLAlchemy async) persiste conversaciones, mensajes, apps y la bitácora.
- **Traefik** enruta cada app desplegada a `http://<slug>.localhost:5173`; cada app corre en su
  **propio contenedor** (FastAPI + UI vanilla) con login Google.

---

## Requisitos

- Docker + Docker Compose
- Node 18+ (frontend del builder en desarrollo)
- Un archivo `.env` en la raíz. Mínimo:

```dotenv
# Núcleo
ANTHROPIC_API_KEY=sk-ant-...
SESSION_SECRET=<secreto-estable-para-firmar-JWT>
PUBLIC_BASE_URL=http://localhost:8000

# Login Google (SSO del builder y de las apps) + conectores Google
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...

# Proxy LLM para apps (opcionales según proveedor)
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...

# Conectores OAuth (opcionales, según lo que uses)
NOTION_CLIENT_ID=...
NOTION_CLIENT_SECRET=...
SLACK_CLIENT_ID=...
SLACK_CLIENT_SECRET=...

# Admin de la sección Logs (CSV de correos; vacío = todo usuario autenticado es admin)
ADMIN_EMAILS=tu@correo.com
```

> Redirect URI a configurar en cada proveedor OAuth: `http://localhost:8000/api/mcp/oauth/callback`
> (para los conectores) y `http://localhost:8000/auth/google/callback` (para el login Google).

---

## Arrancar

### 1) Backend + infraestructura (Docker)

```bash
docker compose up --build
```

Levanta **PostgreSQL**, **Redis**, **FastAPI** (`:8000`), el **worker Celery** y **Traefik**
(`:5173`, para las apps desplegadas). Salud: <http://localhost:8000/api/health>

> Al cambiar variables del `.env`, recreá el contenedor (no alcanza con `restart`):
> `docker compose up -d --force-recreate backend worker`

### 2) Frontend del builder (Vite)

```bash
cd frontend
npm install
npm run dev
```

Abrí <http://localhost:5180>. Vite hace proxy de `/api` y `/auth` al backend. Iniciá
sesión con Google.

### 3) Apps desplegadas

Cada app publicada queda en `http://<slug>.localhost:5173` (Traefik). El acceso se controla por
allowlist de correos (botón **Compartir**); login con Google.

---

## Estructura

```
backend/app/
  main.py            # API HTTP: apps, deploy, auth gateway (Google SSO), owner-token,
                     #   proxy LLM, connector proxy (MCP), logs, run desacoplado (SSE)
  auth.py            # JWT de sesión (HS256)
  core/              # config, db (async), crypto (Fernet), models, schemas, events (bitácora)
  agent/             # runner (loop Claude + streaming), prompts, tools, run_service (SSE+Redis),
                     #   conversation (helpers)
  builder/           # app_builder (spec→prompt devs), codegen, deploy / deploy_runner (pipeline)
  connectors/        # API directa: sheets, docs, drive, gmail, calendar, notion + registry OAuth
  mcp/               # catálogo (catalog.yaml), registry, client/pool (self-hosted), proxy, oauth
  llm/               # proxy LLM unificado (Anthropic / OpenAI / Gemini)
  analysis/          # datasets (pandas) para CSV/XLSX/Sheets
  tasks/             # celery_app, jobs
frontend/src/        # SPA del builder (React + TS): vistas Agents/Apps/Connectors/Logs
docker-compose.yml         # postgres, redis, backend, worker, traefik
docker-compose.mcp.yml     # generado: contenedores de MCP self-hosted (ver más abajo)
```

---

## Conectores

El dueño los conecta **una sola vez** en la sección **Connectors**; las apps **heredan** esas
credenciales (modelo enterprise). Dos integraciones:

| Tipo | Cómo | Conectores |
|------|------|-----------|
| **API** directa | Código propio (httpx) con el token OAuth del dueño | Google Sheets, Docs, Drive, Gmail, Calendar, **Notion**, BigQuery* |
| **MCP** | Servidor MCP (hosted o self-hosted) | Intercom, Miro, Slack (self-hosted), Cloud Storage*, BigQuery* (hosted) |

\* BigQuery/Cloud Storage usan el MCP hosted de Google; las apps los consumen vía proxy.

- **Apps → conectores API:** la app llama `GET {INTERNAL_GATEWAY}/api/apps/{APP_ID}/owner-token/<provider>`
  (header `X-App-Secret`) y con ese token llama la API del servicio.
- **Apps → conectores MCP:** la app llama el **connector proxy**
  `POST {INTERNAL_GATEWAY}/api/apps/{APP_ID}/mcp/<provider>/<tool>` (header `X-App-Secret`); la
  plataforma ejecuta la tool con la conexión del dueño. Las credenciales **nunca** salen de la
  plataforma.

### Agregar un MCP server

Editá [`backend/app/mcp/catalog.yaml`](backend/app/mcp/catalog.yaml) (única fuente de verdad).
Si es `self_hosted`, regenerá el compose:

```bash
docker compose exec backend python -m app.mcp.gen > docker-compose.mcp.yml
docker compose -f docker-compose.yml -f docker-compose.mcp.yml up -d
```

> ⚠️ Algunos MCP hosted de Google (Calendar/Gmail/Drive) **solo funcionan con cuentas Workspace**.
> Por eso Gmail, Calendar, Drive, Docs y Sheets están implementados como **API directa** (andan con
> cuentas personales `@gmail.com`).

---

## Proxy LLM (apps inteligentes)

Las apps no manejan API keys: llaman
`POST {INTERNAL_GATEWAY}/api/apps/{APP_ID}/llm` (header `X-App-Secret`) con
`{model, messages, system?, max_tokens?}` y la plataforma reenvía al proveedor con **sus** claves.

- Modelos: `claude-haiku-4-5` (default), `claude-sonnet-4-6`, `gpt-4o-mini`, `gpt-4o`,
  `gemini-2.5-flash`, `gemini-2.5-pro`.
- **Multimodal:** `content` admite partes `image` y `document` (PDF, con claude-*/gemini-*).
- Tope diario por app (`LLM_DAILY_CALL_CAP`) + auditoría en Logs.

---

## Seguridad

- **SSO Google** en el builder y en cada app; sesión por **JWT HS256** (8h).
- **Rol admin** (`ADMIN_EMAILS`) para la sección **Logs** (auditoría de todo: builds, deploys,
  conexiones MCP, llamadas LLM/conector, logins).
- **Tokens cifrados** en reposo (Fernet); nunca se exponen en el chat ni en los logs.
- **Apps usan credenciales del dueño** (no del visitante); secreto por-app = `HMAC(app_id, SESSION_SECRET)`.
- Acceso a cada app por **allowlist de correos**.

---

## Cómo extenderlo

### Herramienta del agente (rápida, en el loop async)

1. Agregá su esquema a `TOOL_SCHEMAS` en
   [`backend/app/agent/tools.py`](backend/app/agent/tools.py).
2. Registrá un ejecutor `async` en `INLINE_EXECUTORS`.

### Herramienta larga (Celery)

1. Agregá su esquema a `TOOL_SCHEMAS` y su nombre a `LONG_RUNNING_TOOLS`.
2. Implementala en [`backend/app/tasks/jobs.py`](backend/app/tasks/jobs.py), publicando
   `progress` / `done` / `error` en el `channel` recibido (Redis pub/sub).

### Conector por API directa (como Sheets/Docs)

1. Creá `backend/app/connectors/<servicio>.py` (cliente httpx con `_token()` desde la conexión).
2. Definí el provider OAuth en [`backend/app/connectors/registry.py`](backend/app/connectors/registry.py).
3. Agregá la entrada `transport: api` en `catalog.yaml` y, si querés, tools `*_` para el agente.

---

## Stack

FastAPI · SQLAlchemy async · Anthropic SDK (aiohttp) · Celery · Redis · PostgreSQL · Docker ·
Traefik · React + Vite + TypeScript.
