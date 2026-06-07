# Arquitectura monolítica (VPS único) — Cashea Hub / IziDeploy

Documento de la arquitectura **actual**: todo corre en **un solo VPS** (un Droplet de
DigitalOcean) con **Docker Compose**. La plataforma permite construir y desplegar apps full-stack
reales conversando con un agente (Claude), y cada app desplegada vive como su propio contenedor en
el **mismo host**.

- **Builder + API:** `https://izideploy.com`
- **Apps desplegadas:** `https://<slug>.app.izideploy.com`

> Es deliberadamente monolítico/single-node: simple de operar, barato, sin orquestador. Las
> limitaciones de escala están al final.

---

## 1. Vista general

```
                                  Internet (HTTPS 443 / HTTP 80→443)
                                            │
                                            ▼
┌──────────────────────────── VPS único (Droplet · Docker) ─────────────────────────────┐
│                                                                                         │
│   ┌─────────────┐   red "cashea-web" (Traefik descubre por labels)                      │
│   │   TRAEFIK    │◀──────────────┬───────────────┬───────────────┬──────────────────┐   │
│   │  v3.3 :80/443│               │               │               │                  │   │
│   │  TLS LE DNS-01│              ▼               ▼               ▼                  ▼   │
│   └──────┬───────┘        ┌───────────┐   ┌───────────┐   ┌───────────┐     ┌───────────┐
│          │ Host(izideploy)│ app-<slug>│   │ app-<slug>│   │ app-<slug>│ ... │ app-<slug>│
│          ▼                │ (FastAPI) │   │ (FastAPI) │   │ (FastAPI) │     │ (FastAPI) │
│   ┌───────────┐           └───────────┘   └───────────┘   └───────────┘     └───────────┘
│   │ FRONTEND   │            ▲  contenedores de apps generadas (uno por app)              │
│   │ nginx (SPA)│            │  creados/destruidos por el backend vía docker.sock         │
│   └─────┬──────┘            │                                                            │
│         │ /api,/auth proxy  │                                                            │
│         ▼                   │                                                            │
│   ┌───────────┐  spawn      │     ┌──────────┐        ┌──────────┐                       │
│   │  BACKEND   │────────────┘     │  WORKER   │        │  REDIS    │   ┌──────────┐       │
│   │ FastAPI    │◀── Celery ──────▶│  Celery   │◀──────▶│ pub/sub + │   │ POSTGRES │       │
│   │ (uvicorn)  │   broker         │ (deploy/QA)│        │ streams + │   │   16     │       │
│   │            │─────────────────────────────────────▶│ broker     │   │          │       │
│   └─────┬──────┘   SQLAlchemy async                    └──────────┘    └────┬─────┘       │
│         │  └──────────────────────────────────────────────────────────────▶│  (red default)│
│         │  docker.sock (/var/run/docker.sock) ── spawnea contenedores de apps              │
│         ▼                                                                                 │
│   Anthropic API (Claude) — salida a Internet (agente del Builder + proxy LLM de las apps) │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Servicios (Docker Compose, `docker-compose.prod.yml`)

| Servicio | Imagen / base | Rol | Redes | Expuesto |
|---|---|---|---|---|
| **traefik** | `traefik:v3.3` | Reverse proxy + TLS (Let's Encrypt **DNS-01** con DigitalOcean → cert **wildcard** `*.app.izideploy.com`). Enruta por labels de Docker. | `web` | **80/443** (único público) |
| **frontend** | build `./frontend` → `nginx` | Sirve la **SPA** (React/Vite, build estático) y proxya `/api`,`/auth` al backend. | `web` | vía Traefik (`Host(izideploy.com)`) |
| **backend** | build `./backend` → `uvicorn` | **API HTTP** (FastAPI async): apps, deploy, auth gateway (Google SSO), agente (SSE), proxy LLM, connector-proxy, owner-token, logs. **Spawnea los contenedores de apps** vía `docker.sock`. | `default`, `web` | interno (`:8000`) |
| **worker** | build `./backend` → `celery worker` | Tareas largas: **pipeline de deploy** (generación de código + QA + build + run del contenedor de la app). También usa `docker.sock`. | `default`, `web` | — |
| **redis** | `redis:7-alpine` | **Broker de Celery** + **pub/sub y Streams** para el SSE (progreso del agente y del deploy en tiempo real). | `default` | interno |
| **postgres** | `postgres:16-alpine` | Base de datos (SQLAlchemy async / asyncpg). Esquema por `create_all` + migraciones ligeras (`ALTER … IF NOT EXISTS`) en `app/core/db.py`. | `default` | interno |
| **app-\<slug\>** (N) | `cashea-app-api-base` + delta | **Apps desplegadas**: un contenedor FastAPI por app que sirve API + UI vanilla (HTML/JS/CSS). Creados/destruidos por el backend/worker. | `cashea-web` | vía Traefik (`Host(<slug>.app.izideploy.com)`) |

**Redes:** `web` (nombre real `cashea-web`, la que Traefik observa) + `default` (interna: DB/Redis no se exponen).
**Volúmenes:** `pgdata` (Postgres), `appsdata` (repos git por app, en `/data/apps`), `letsencrypt` (acme.json).
**Acceso a Docker:** backend y worker montan `/var/run/docker.sock` para **levantar/parar contenedores de apps** en el mismo host (por eso NO se usa un PaaS gestionado: se necesita el daemon).

---

## 3. Estructura del backend (`backend/app/`)

```
main.py            API HTTP: apps (CRUD, deploy, versiones), auth gateway (Google SSO + JWT),
                   agente (run SSE + cancel), owner-token, connector-proxy, proxy LLM, /api/users,
                   /api/logs, middleware de sesión + gating admin.
core/
  config.py        settings (env): claves, modelo, ADMIN_EMAILS, dominios.
  db.py            engine async + init_db (create_all + migraciones ligeras + backfills).
  models.py        Conversation, Message, AppProject, Connection, McpConnection, EventLog, User.
  schemas.py       Pydantic (AppProjectOut/Detail, AppListPage, etc.).
  crypto.py        Fernet (cifra tokens OAuth en DB).
agent/
  runner.py        Loop agéntico (Claude), streaming, thinking adaptativo, retries con backoff,
                   web_search nativo, MCP servers, bridged tools, loop-breaker.
  run_service.py   Run DESACOPLADO: tarea de fondo → Redis Stream; el cliente lo lee por SSE.
  prompts.py       System prompt del agente (idioma del usuario; apps en inglés).
  tools.py         Tools del agente (define_app, edit_app, conectores directos, etc.).
mcp/
  catalog.yaml     Catálogo de conectores (hosted / self_hosted / api).
  connstore.py     Conexiones POR-USUARIO (scoping por email vía contextvar).
  client/pool/registry/bridge/proxy/oauth   Runtime MCP (contenedores self-hosted, owner-token).
connectors/         Clientes API directos: sheets, drive, docs, gmail, calendar, notion.
builder/
  app_builder.py   Generación del código de la app + QA (auto-fix de deps).
  deploy.py        Dockerfiles, labels Traefik, gate de auth, build/run/teardown de contenedores.
  deploy_runner.py Orquesta el deploy; publica progreso a Redis (SSE).
  app_repo.py      Versionado (git local por app, en /data/apps).
tasks/
  celery_app.py    App de Celery.
  jobs.py          run_deploy_task (corre el pipeline en el worker).
llm/proxy.py        Proxy LLM para las apps desplegadas (Anthropic/OpenAI/Gemini, cap diario).
```

---

## 4. Flujos principales

### a) Construir con el agente (chat, SSE desacoplado)
1. La SPA hace `POST /api/agent/run` (mensaje + modelo).
2. El backend crea una **tarea de fondo** (`run_service`) y devuelve `run_id`. El run **no depende
   de la conexión**: cada evento (tokens, thinking, tool calls) se publica a un **Redis Stream**.
3. La SPA abre `GET /api/agent/runs/{id}/stream` (**SSE**, con `Last-Event-ID` para reanudar tras
   desconexiones). El agente usa Claude + tools (define/edit app, conectores, web search).

### b) Desplegar una app (pipeline en el worker)
1. `POST /api/apps/{id}/deploy` encola `run_deploy_task` en **Celery** (no bloquea la API).
2. El worker: genera/reusa el código (cache por `spec_hash`) → **QA** (build + smoke + auto-fix de
   librerías) → `docker build` de la imagen `app-<slug>` → corre el contenedor con **labels de
   Traefik** (Host `<slug>.app.izideploy.com`, TLS wildcard) en la red `cashea-web`.
3. El progreso se publica a Redis (`deploy:{app_id}`) y la SPA lo ve por SSE.
4. Cambios solo-front: **hot-swap** del volumen estático sin rebuild.

### c) App desplegada en runtime
- Es un **único contenedor FastAPI** que sirve API + UI vanilla. Gate de login fijo (Google SSO);
  acceso por **allowlist** (`shared_emails`) + dueño.
- Para usar conectores, la app llama al backend (X-App-Secret):
  - **owner-token** `GET /api/apps/{id}/owner-token/{provider}` → token del **DUEÑO** (resuelto por
    su email). API directas (Google/Notion).
  - **connector-proxy** `POST /api/apps/{id}/mcp/{provider}/{tool}` → la plataforma ejecuta la tool
    MCP con la conexión del dueño (token nunca expuesto a la app).
  - **proxy LLM** `POST /api/apps/{id}/llm` → usa las claves de la plataforma (cap diario).

### d) Conectores (por-usuario + owner-token)
- `McpConnection` es **por usuario** (scoping por **email**). El agente usa las conexiones del
  usuario que chatea; las **apps usan las del dueño** de la app.
- Self-hosted MCP: un **contenedor MCP por (usuario, provider)** levantado on-demand (pool).

---

## 5. Modelo de datos (Postgres)

| Tabla | Para qué |
|---|---|
| `users` | Identidad (PK email) + `sub` Google + **rol** (admin/member). Alta/seed en login. |
| `app_projects` | Apps: título, slug, estado de deploy, `owner_email`, `shared_emails`, spec, artefactos de build cacheados. |
| `conversations` / `messages` | Chat del Builder (historial; cascade al borrar la app). |
| `mcp_connections` | Conexiones OAuth **por usuario** (token cifrado Fernet). |
| `connections` | Credenciales OAuth por (app, usuario, provider) para el modelo por-visitante. |
| `event_logs` | Bitácora (deploy, MCP, login, roles) → sección **Logs** (admin). |

Paginación **server-side** en Apps, Users y Logs (escala a miles/decenas de miles de filas).

---

## 6. Seguridad

- **Auth:** Google SSO → **JWT HS256** (firmado con `SESSION_SECRET`). Middleware exige sesión en
  `/api/*` salvo rutas públicas (callbacks OAuth, gateway de apps por `X-App-Secret`).
- **Roles:** admin/member en DB; `ADMIN_EMAILS` (env) = admins permanentes (bootstrap, no degradables).
- **Permisos de app:** el **dueño** edita/elimina/comparte; compartir = **solo lectura**; los admins
  **ven** todas las apps pero en **modo lectura** sobre las ajenas.
- **Secretos por-app:** `HMAC(app_id, SESSION_SECRET)` — la app desplegada se autentica al gateway.
- **Tokens OAuth:** cifrados con **Fernet** en DB; refresh transparente.
- **TLS:** Let's Encrypt wildcard (DNS-01 con DO). HTTP→HTTPS forzado en Traefik.
- **Caché:** HTML con `no-store` (apps y SPA) para que un redeploy no quede servido viejo.

---

## 7. Operación

- **Deploy de la plataforma:** `git pull && docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build`. Ver `DEPLOY.md`.
- **Logs:** `docker compose ... logs -f backend worker traefik`.
- **Backups:** volúmenes `pgdata` (DB) y `appsdata` (repos por app) + snapshots del Droplet.
- **Requisito del host:** Docker v28+ exige `DOCKER_MIN_API_VERSION=1.24` en el daemon (ver `DEPLOY.md`).

---

## 8. Por qué monolítico — y sus límites

**Ventajas (hoy):** un solo servidor, una sola `docker compose up`, sin Kubernetes ni colas
externas; barato; despliegue de apps "en vivo" reutilizando el daemon local.

**Límites / cuándo dejar de serlo:**
- **Single node = single point of failure.** No hay HA ni balanceo: si el VPS cae, todo cae.
- **Recursos compartidos:** Postgres, Redis, backend, worker **y todas las apps** compiten por CPU/RAM
  del mismo host. Muchas apps activas → escalar el Droplet o repartir.
- **`docker.sock` en el host:** el backend ejecuta código generado en contenedores en la **misma
  máquina** (aislamiento por contenedor, no por VM). Para multi-tenant grande conviene aislar
  (nodo de runners separado, gVisor/Firecracker, o un orquestador).
- **Estado local:** `appsdata` (repos git) y la DB viven en el disco del Droplet → migrar a managed
  Postgres + storage de objetos cuando crezca.
- **Escala horizontal:** hoy 1 worker/1 backend. El siguiente paso sería separar **API**, **workers
  de build** y **runtime de apps** en nodos distintos detrás del mismo Traefik (o un ingress).
