"""Orquestación de despliegue por app con docker-py.

Cada app = UN SOLO contenedor FastAPI full-async, enrutado por Traefik en `<slug>.localhost`:
  - sirve la API en `/api/*` (async, conectores con las credenciales del dueño)
  - sirve la UI estática (HTML/JS/CSS vanilla, sin build) en `/` y `/static/*`

No hay node, ni Vite, ni nginx, ni segundo contenedor: el propio backend sirve el UI.
Las imágenes se construyen desde un contexto tar EN MEMORIA enviado al daemon por el
socket (sin paths de host -> funciona en Windows/Docker Desktop).
Requiere socket de Docker montado y Traefik en TRAEFIK_NETWORK.
"""
import hashlib
import hmac
import io
import json
import os
import tarfile
import time

import docker

from ..core.config import settings


def _app_secret(app_id: str) -> str:
    """Secreto determinístico por-app (igual que main.app_secret) para credenciales/acceso."""
    return hmac.new(
        settings.session_secret.encode(), app_id.encode(), hashlib.sha256
    ).hexdigest()

APP_DOMAIN_PORT = os.environ.get("APP_DOMAIN_PORT", "5173")
TRAEFIK_NETWORK = os.environ.get("TRAEFIK_NETWORK", "cashea-web")
# Dominio base de las apps desplegadas: <slug>.<APP_DOMAIN>. Dev: localhost. Prod: app.izideploy.com
APP_DOMAIN = os.environ.get("APP_DOMAIN", "localhost")
# Si está seteado (prod), las apps salen por HTTPS con este certresolver de Traefik (Let's Encrypt).
APP_CERTRESOLVER = os.environ.get("APP_CERTRESOLVER", "")
# URL del builder alcanzable CONTENEDOR→CONTENEDOR (la pública AUTH_GATEWAY/localhost solo
# sirve desde el navegador). El backend desplegado la usa para access + owner-token.
INTERNAL_GATEWAY = os.environ.get("INTERNAL_GATEWAY", "http://backend:8000")
TRAEFIK_ENTRYPOINT = os.environ.get("TRAEFIK_ENTRYPOINT", "web")


def _traefik_labels(slug: str, host: str) -> dict[str, str]:
    """Labels de Traefik para enrutar la app desplegada. En prod (APP_CERTRESOLVER seteado)
    agrega HTTPS con el certresolver y un cert WILDCARD (*.APP_DOMAIN) — un solo cert sirve a
    todas las apps."""
    ep = "websecure" if APP_CERTRESOLVER else TRAEFIK_ENTRYPOINT
    labels = {
        "traefik.enable": "true",
        f"traefik.http.routers.{slug}.rule": f"Host(`{host}`)",
        f"traefik.http.routers.{slug}.entrypoints": ep,
        f"traefik.http.services.{slug}.loadbalancer.server.port": "80",
    }
    if APP_CERTRESOLVER:
        labels[f"traefik.http.routers.{slug}.tls"] = "true"
        labels[f"traefik.http.routers.{slug}.tls.certresolver"] = APP_CERTRESOLVER
        labels[f"traefik.http.routers.{slug}.tls.domains[0].main"] = APP_DOMAIN
        labels[f"traefik.http.routers.{slug}.tls.domains[0].sans"] = f"*.{APP_DOMAIN}"
    return labels


def public_url_parts() -> dict[str, str]:
    """Esquema y sufijo del dominio de las apps desplegadas (misma lógica que el deploy real).
    Lo usa el modal de la UI para mostrar el dominio correcto: en prod `.app.izideploy.com`
    (https, sin puerto) y en dev `.localhost:5173` (http)."""
    https = bool(APP_CERTRESOLVER)
    return {
        "scheme": "https" if https else "http",
        "domain": APP_DOMAIN,
        "suffix": f".{APP_DOMAIN}" if https else f".{APP_DOMAIN}:{APP_DOMAIN_PORT}",
    }

# ---------- Imagen BASE (se construye una vez, compartida por todas las apps) ----------
# Trae las deps comunes pre-instaladas; cada app instala SOLO su delta (requirements.txt).
API_BASE_TAG = "cashea-app-api-base:latest"

API_BASE_DOCKERFILE = """\
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" httpx "celery[redis]" redis "pydantic[email]" python-multipart
"""

# El backend parte de la base y solo instala el delta declarado en requirements.txt.
# COPIA main.py + scaffold + la UI estática (static/*); uvicorn sirve API + UI.
APP_DOCKERFILE = """\
FROM cashea-app-api-base:latest
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
EXPOSE 80
CMD ["uvicorn", "app_entry:app", "--host", "0.0.0.0", "--port", "80"]
"""

# Punto de entrada fijo: envuelve el `app` generado con (1) el gate de autenticación y
# (2) el servido de la UI estática. Así la protección y el UI no dependen del code-gen.
APP_ENTRY_PY = """\
import os
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from main import app
from auth import install_auth

install_auth(app)

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_INDEX = os.path.join(_STATIC, "index.html")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/")
async def _root():
    return FileResponse(_INDEX)


@app.get("/{full_path:path}")
async def _spa(full_path: str):
    # /api/* y /static/* los resuelven la app generada y el mount; cualquier otra ruta
    # devuelve el index (SPA). Las /api inexistentes deben dar 404 (no el HTML).
    if full_path.startswith("api") or full_path.startswith("static"):
        return JSONResponse({"detail": "not found"}, status_code=404)
    return FileResponse(_INDEX)
"""

# Backend mínimo para apps de dashboard (los datos vienen del gateway con el token del dueño).
DASHBOARD_MAIN_PY = """\
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
"""

# Verificación de sesión (JWT HS256, solo stdlib) + middleware que protege /api/*.
APP_AUTH_PY = '''\
import base64, hashlib, hmac, json, os, time
import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

SECRET = os.environ.get("SESSION_SECRET", "")
# Gateway alcanzable contenedor→contenedor (no la pública del navegador).
GATEWAY = os.environ.get("INTERNAL_GATEWAY", "") or os.environ.get("AUTH_GATEWAY", "")
APP_ID = os.environ.get("APP_ID", "")
APP_SECRET = os.environ.get("APP_SECRET", "")

_access_cache = {}  # email -> (allowed: bool, ts)


def _b64d(seg):
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def _decode(token):
    try:
        h, p, s = token.split(".")
        seg = h + "." + p
        expected = base64.urlsafe_b64encode(
            hmac.new(SECRET.encode(), seg.encode(), hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        if not hmac.compare_digest(expected, s):
            return None
        payload = json.loads(_b64d(p))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


async def _has_access(email):
    """Allowlist dinámica: el builder es la fuente de verdad (cache corto)."""
    now = time.time()
    hit = _access_cache.get(email)
    if hit and now - hit[1] < 30:
        return hit[0]
    allowed = False
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"{GATEWAY}/api/apps/{APP_ID}/access",
                params={"email": email},
                headers={"X-App-Secret": APP_SECRET},
            )
            allowed = r.status_code == 200 and r.json().get("allowed", False)
    except Exception:
        allowed = False
    _access_cache[email] = (allowed, now)
    return allowed


def install_auth(app):
    @app.middleware("http")
    async def _gate(request, call_next):
        path = request.url.path
        # La UI estática y el index son públicos (no llevan token en una navegación normal);
        # el gate del navegador (auth.js) hace el flujo y protege los datos vía /api.
        if request.method == "OPTIONS" or path == "/api/health" or not path.startswith("/api"):
            return await call_next(request)
        header = request.headers.get("authorization", "")
        token = header[7:] if header[:7].lower() == "bearer " else ""
        user = _decode(token) if token else None
        if not user:
            return JSONResponse(status_code=401, content={"detail": "login required"})
        if not await _has_access(user.get("email", "")):
            return JSONResponse(status_code=403, content={"detail": "forbidden"})
        request.state.user = user
        return await call_next(request)

    @app.get("/api/__whoami")
    async def _whoami(request: Request):
        # Endpoint del gate: 200 si el visor está logueado y autorizado (el middleware ya
        # devolvió 401/403 si no). Existir evita el 404 ruidoso en consola.
        u = getattr(request.state, "user", None) or {}
        return {"ok": True, "email": u.get("email", "")}
'''

# ---------- UI estática FIJA (vanilla, sin build) ----------
# index.html carga: config.js (valores del gateway) -> Chart.js (CDN) -> base.css/app.css
# -> app.js (define window.startApp) -> auth.js (gate; llama a startApp cuando hay acceso).
INDEX_HTML = """\
<!doctype html>
<html lang="es">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>App</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
    <script src="/static/config.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <link rel="stylesheet" href="/static/base.css" />
    <link rel="stylesheet" href="/static/app.css" />
  </head>
  <body>
    <div id="root"></div>
    <script src="/static/app.js"></script>
    <script src="/static/auth.js"></script>
  </body>
</html>
"""

# Gate de autenticación (vanilla, FIJO). Captura el token del fragmento (#token=...),
# expone `window.authFetch`, y muestra login / "Sin acceso" / arranca la app.
AUTH_JS = """\
(function () {
  var KEY = "cashea_session";
  var root = document.getElementById("root");

  // --- Captura DETERMINÍSTICA de errores del front (la controla el gate, no el LLM) ---
  // Si el app.js generado falla en runtime (p.ej. "authFetch is not defined"), en vez de
  // quedar en "Cargando…" mostramos un banner visible con el detalle, para que el usuario
  // sepa qué pasó y pueda volver a iterar en el Builder.
  var _errs = [];
  function _showErrs() {
    var bar = document.getElementById("cashea-errbar");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "cashea-errbar";
      bar.className = "cashea-errbar";
      document.body.appendChild(bar);
    }
    var body = _errs.join("\\n\\n").replace(/&/g, "&amp;").replace(/</g, "&lt;");
    bar.innerHTML =
      '<div class="cashea-errbar-head">⚠️ La app tuvo ' + _errs.length +
      ' error(es) de ejecución. Contáselo al Builder para corregirlo.' +
      '<button id="cashea-errbar-x" aria-label="Cerrar">✕</button></div>' +
      '<pre class="cashea-errbar-body">' + body + '</pre>';
    var x = document.getElementById("cashea-errbar-x");
    if (x) x.onclick = function () { bar.remove(); };
  }
  function _report(msg) {
    _errs.push(String(msg).slice(0, 1500));
    if (_errs.length > 12) _errs.shift();
    try { _showErrs(); } catch (e) {}
  }
  window.addEventListener("error", function (e) {
    _report((e.message || "Error") + (e.filename ? "  (" + e.filename + ":" + e.lineno + ")" : ""));
  });
  window.addEventListener("unhandledrejection", function (e) {
    var r = e.reason;
    _report("Promesa rechazada: " + (r && r.message ? r.message : r));
  });

  var m = location.hash.match(/token=([^&]+)/);
  if (m) {
    localStorage.setItem(KEY, decodeURIComponent(m[1]));
    history.replaceState(null, "", location.pathname + location.search);
  }
  function token() { return localStorage.getItem(KEY); }
  function loginUrl() {
    return window.AUTH_GATEWAY + "/auth/google/login?return_to=" +
      encodeURIComponent(location.origin + "/");
  }
  // Global: agrega Authorization: Bearer a las llamadas al backend (mismo origen).
  window.authFetch = function (path, init) {
    init = init || {};
    var headers = Object.assign({}, init.headers || {});
    var t = token();
    if (t) headers["Authorization"] = "Bearer " + t;
    return fetch(path, Object.assign({}, init, { headers: headers }));
  };

  var G_ICON =
    '<svg viewBox="0 0 18 18" aria-hidden="true">' +
    '<path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.71-1.57 2.68-3.89 2.68-6.62z"/>' +
    '<path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z"/>' +
    '<path fill="#FBBC05" d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z"/>' +
    '<path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z"/></svg>';
  var BRAND_MARK =
    '<div class="cashea-brand"><svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M12 3a9 9 0 1 0 6.5 15.2A6.5 6.5 0 1 1 12 5.5V3z" fill="#15150a"/></svg></div>';

  function screen(inner) {
    root.innerHTML = '<div class="cashea-gate"><div class="cashea-card">' + inner + "</div></div>";
  }
  function showLogin() {
    screen(
      BRAND_MARK +
      '<h1>Inicia sesión</h1><p>Accede con tu cuenta de Google para usar esta app.</p>' +
      '<a class="cashea-gbtn" href="' + loginUrl() + '">' + G_ICON + 'Continuar con Google</a>' +
      '<div class="cashea-foot">Powered by Cashea Hub</div>'
    );
  }
  function showDenied(email) {
    screen(
      '<div class="cashea-lock">🔒</div><h1>Sin acceso</h1>' +
      '<p>No tenés acceso a esta app. Pedíselo al dueño para que agregue tu correo.</p>' +
      (email ? '<p class="cashea-muted">Conectado como <b>' + email + "</b></p>" : "") +
      '<button class="cashea-gbtn" id="cashea-switch">' + G_ICON + 'Usar otra cuenta</button>'
    );
    document.getElementById("cashea-switch").onclick = function () {
      localStorage.removeItem(KEY);
      location.href = loginUrl();
    };
  }
  function start() {
    root.innerHTML = '<div id="app"></div>';
    try {
      if (typeof window.startApp === "function") window.startApp();
    } catch (e) {
      _report((e && e.message) ? e.message : e);
    }
  }

  var t = token();
  if (!t) { showLogin(); return; }
  // El middleware protege /api/*: 401 = sin login, 403 = sin acceso (allowlist).
  window.authFetch("/api/__whoami")
    .then(function (r) {
      if (r.status === 401) { localStorage.removeItem(KEY); showLogin(); return; }
      if (r.status === 403) {
        var email = "";
        try { email = JSON.parse(atob(t.split(".")[1])).email || ""; } catch (e) {}
        showDenied(email);
        return;
      }
      start();
    })
    .catch(function () { start(); });
})();
"""

# Estilos base FIJOS: reset suave + estilos del gate. La app puede sumar /static/app.css.
BASE_CSS = """\
* { box-sizing: border-box; }
:root { --c-brand: #fdfa3d; --c-ink: #15150a; --c-text: #1d1d1f; --c-muted: #6b6f76; --c-line: #e7e9ee; }
body { margin: 0; color: var(--c-text);
  font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  background:
    radial-gradient(1100px 480px at 50% -8%, #eef1f8 0%, transparent 60%),
    linear-gradient(180deg, #fafbfd 0%, #f3f5f9 100%);
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }
.cashea-gate { display: grid; place-items: center; min-height: 100vh; padding: 24px; }
.cashea-card { width: 100%; max-width: 400px; text-align: center; background: #fff;
  padding: 40px 34px; border: 1px solid var(--c-line); border-radius: 20px;
  box-shadow: 0 1px 2px rgba(16,24,40,.04), 0 24px 60px -20px rgba(16,24,40,.18);
  animation: cashea-in .35s ease; }
@keyframes cashea-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
.cashea-brand { width: 56px; height: 56px; margin: 0 auto 18px; border-radius: 16px;
  display: grid; place-items: center; background: linear-gradient(145deg, #fdfa3d, #f4d000);
  box-shadow: 0 8px 22px -6px rgba(244,208,0,.6); }
.cashea-brand svg { width: 30px; height: 30px; }
.cashea-card h1 { margin: 0 0 6px; font-size: 1.5rem; font-weight: 700; letter-spacing: -.01em; color: var(--c-ink); }
.cashea-card p { color: var(--c-muted); margin: 0 0 22px; font-size: .95rem; line-height: 1.55; }
.cashea-gbtn { display: inline-flex; align-items: center; justify-content: center; gap: 10px;
  width: 100%; padding: 12px 18px; border: 1px solid var(--c-line); border-radius: 12px;
  background: #fff; color: #1d1d1f; font: inherit; font-size: .95rem; font-weight: 600;
  text-decoration: none; cursor: pointer;
  transition: box-shadow .15s ease, transform .15s ease, border-color .15s ease; }
.cashea-gbtn:hover { border-color: #d4d8e0; box-shadow: 0 8px 20px -8px rgba(16,24,40,.25); transform: translateY(-1px); }
.cashea-gbtn svg { width: 18px; height: 18px; }
.cashea-lock { width: 56px; height: 56px; margin: 0 auto 18px; border-radius: 50%;
  background: #fff4f4; color: #d94343; display: grid; place-items: center; font-size: 26px; }
.cashea-muted { color: #86868b; font-size: 13px; margin-top: 14px; }
.cashea-foot { margin-top: 22px; color: #aeb2bb; font-size: 12px; letter-spacing: .02em; }
.cashea-errbar { position: fixed; top: 0; left: 0; right: 0; z-index: 99999;
  background: #fff4f4; border-bottom: 2px solid #d94343; color: #b3261e;
  padding: 10px 16px; max-height: 42vh; overflow: auto;
  box-shadow: 0 6px 20px rgba(0,0,0,.12); }
.cashea-errbar-head { display: flex; align-items: flex-start; gap: 10px; font-weight: 700;
  font-size: 13px; margin-bottom: 6px; }
.cashea-errbar-head button { margin-left: auto; border: 0; background: transparent;
  color: #b3261e; font-size: 14px; cursor: pointer; line-height: 1; }
.cashea-errbar-body { margin: 0; white-space: pre-wrap; word-break: break-word;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 12px;
  color: #7a1d16; }
"""

# Dashboard genérico (vanilla + Chart.js) dirigido por config: lee {title, headers, rows,
# config} del gateway (con el token del dueño) y renderiza KPIs + gráficos + tabla.
DASHBOARD_APP_JS = r"""
window.startApp = function () {
  var app = document.getElementById("app");
  var charts = [];
  var COLORS = ["#0f9d58", "#1a73e8", "#fbbc04", "#ea4335", "#9334e6", "#00acc1"];

  function toNum(v) {
    var n = parseFloat(String(v == null ? "" : v).replace(/[^0-9.\-]/g, ""));
    return isNaN(n) ? 0 : n;
  }
  function agg(vals, fn) {
    if (!vals.length) return 0;
    if (fn === "sum") return vals.reduce(function (a, b) { return a + b; }, 0);
    if (fn === "avg") return vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
    if (fn === "min") return Math.min.apply(null, vals);
    if (fn === "max") return Math.max.apply(null, vals);
    return vals.length;
  }
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  function render(data) {
    charts.forEach(function (c) { c.destroy(); });
    charts = [];
    app.innerHTML = "";
    var idx = function (name) { return data.headers.indexOf(name); };

    var wrap = el("div", "dash");
    wrap.appendChild(el("h1", null, data.title || "Dashboard"));
    wrap.appendChild(el("p", "dash-sub", data.rows.length + " filas · actualiza cada 30s"));

    var kpiRow = el("div", "kpi-row");
    (data.config.kpis || []).forEach(function (k) {
      var i = idx(k.column);
      var vals = i < 0 ? [] : data.rows.map(function (r) { return toNum(r[i]); });
      var v = agg(vals, k.agg);
      var card = el("div", "kpi");
      card.appendChild(el("div", "kpi-label", k.label));
      card.appendChild(el("div", "kpi-value", Number.isInteger(v) ? v : v.toFixed(2)));
      kpiRow.appendChild(card);
    });
    wrap.appendChild(kpiRow);

    var grid = el("div", "chart-grid");
    (data.config.charts || []).forEach(function (c, i) {
      var xi = idx(c.x), yi = idx(c.y), groups = {};
      if (xi >= 0) {
        data.rows.forEach(function (r) {
          var key = String(r[xi] == null ? "—" : r[xi]);
          (groups[key] = groups[key] || []).push(yi >= 0 ? toNum(r[yi]) : 1);
        });
      }
      var labels = Object.keys(groups);
      var values = labels.map(function (k) { return agg(groups[k], c.agg); });
      var card = el("div", "card");
      card.appendChild(el("h3", "card-title", c.title || (c.agg + "(" + c.y + ") por " + c.x)));
      var canvas = document.createElement("canvas");
      card.appendChild(canvas);
      grid.appendChild(card);
      var type = c.type === "line" ? "line" : c.type === "pie" ? "pie" : "bar";
      charts.push(new Chart(canvas, {
        type: type,
        data: {
          labels: labels,
          datasets: [{
            label: c.y || "",
            data: values,
            backgroundColor: type === "pie"
              ? labels.map(function (_, j) { return COLORS[j % COLORS.length]; })
              : COLORS[i % COLORS.length],
            borderColor: COLORS[i % COLORS.length],
          }],
        },
        options: { responsive: true, plugins: { legend: { display: type === "pie" } } },
      }));
    });
    wrap.appendChild(grid);

    var cols = (data.config.table_columns && data.config.table_columns.length)
      ? data.config.table_columns : data.headers;
    var tcard = el("div", "card");
    tcard.appendChild(el("h3", "card-title", "Datos"));
    var thead = "<tr>" + cols.map(function (h) { return "<th>" + h + "</th>"; }).join("") + "</tr>";
    var tbody = data.rows.slice(0, 100).map(function (r) {
      return "<tr>" + cols.map(function (h) {
        var v = r[idx(h)]; return "<td>" + (v == null ? "" : String(v)) + "</td>";
      }).join("") + "</tr>";
    }).join("");
    var tableWrap = el("div", "table-wrap");
    tableWrap.appendChild(el("table", "data-table", "<thead>" + thead + "</thead><tbody>" + tbody + "</tbody>"));
    tcard.appendChild(tableWrap);
    wrap.appendChild(tcard);

    app.innerHTML = "";
    app.appendChild(wrap);
  }

  function load() {
    window.authFetch(window.AUTH_GATEWAY + "/api/dashboards/" + window.APP_ID + "/data")
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(render)
      .catch(function (e) {
        app.innerHTML = '<div class="dash"><h2>Dashboard</h2><p style="color:#ea4335">Error: ' +
          String(e) + "</p></div>";
      });
  }
  load();
  setInterval(load, 30000);
};
"""

DASHBOARD_APP_CSS = """\
.dash { padding: 28px; max-width: 1100px; margin: 0 auto; }
.dash h1 { margin: 0 0 4px; }
.dash-sub { color: #5f6368; margin-top: 0; }
.kpi-row { display: flex; gap: 14px; flex-wrap: wrap; margin: 18px 0; }
.kpi { flex: 1 1 160px; background: #fff; border: 1px solid #e8eaed; border-radius: 14px;
  padding: 18px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.kpi-label { font-size: 13px; color: #5f6368; font-weight: 600; }
.kpi-value { font-size: 30px; font-weight: 800; margin-top: 6px; }
.chart-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 16px; margin: 8px 0 16px; }
.card { background: #fff; border: 1px solid #e8eaed; border-radius: 14px; padding: 18px;
  box-shadow: 0 1px 3px rgba(0,0,0,.06); }
.card-title { margin: 0 0 12px; font-size: 15px; }
.table-wrap { overflow-x: auto; }
.data-table { border-collapse: collapse; width: 100%; font-size: 13px; }
.data-table th { text-align: left; border-bottom: 2px solid #e8eaed; padding: 8px 10px; color: #5f6368; }
.data-table td { border-bottom: 1px solid #f1f3f4; padding: 8px 10px; }
"""


# ---------- Construcción / ejecución de imágenes ----------
def _make_context(files: dict[str, str]) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf


def _build(client, tag: str, files: dict[str, str]):
    image, _ = client.images.build(
        fileobj=_make_context(files), custom_context=True, tag=tag, rm=True, pull=False
    )
    return image


def ensure_base_images(client) -> None:
    """Construye (una vez) la imagen base con las deps comunes. Idempotente."""
    try:
        client.images.get(API_BASE_TAG)
    except docker.errors.ImageNotFound:
        _build(client, API_BASE_TAG, {"Dockerfile": API_BASE_DOCKERFILE})


def _ensure_network(client) -> None:
    try:
        client.networks.get(TRAEFIK_NETWORK)
    except docker.errors.NotFound:
        client.networks.create(TRAEFIK_NETWORK, driver="bridge")


def _remove(client, name: str) -> None:
    try:
        client.containers.get(name).remove(force=True)
    except docker.errors.NotFound:
        pass


def _remove_volume(client, name: str) -> None:
    try:
        client.volumes.get(name).remove(force=True)
    except (docker.errors.NotFound, docker.errors.APIError):
        pass


def container_status(slug: str) -> str | None:
    """Estado del contenedor de la app ('running' | otro), o None si no existe."""
    try:
        return docker.from_env(version="auto").containers.get(f"app-{slug}").status
    except docker.errors.NotFound:
        return None


def _static_tar(static_files: dict[str, str]) -> bytes:
    """Tar con los estáticos generados (nombres relativos a /app/static: app.js, app.css)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for k, content in static_files.items():
            name = k.split("static/")[-1]  # "static/app.js" -> "app.js"
            data = (content or "").encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf.getvalue()


def apply_static(slug: str, static_files: dict[str, str]) -> bool:
    """Escribe los estáticos en el contenedor VIVO (volumen /app/static) SIN reconstruir la
    imagen. StaticFiles los sirve en el próximo request (cambia mtime/ETag). Devuelve True si
    se aplicó (contenedor corriendo). Es el camino rápido para cambios solo-front."""
    client = docker.from_env(version="auto")
    try:
        c = client.containers.get(f"app-{slug}")
    except docker.errors.NotFound:
        return False
    if c.status != "running":
        return False
    # Incluimos SIEMPRE el gate FIJO (login/SSO) para que los cambios de plantilla se propaguen
    # también por el camino rápido (sin rebuild ni LLM). No tocamos config.js (es per-app).
    files = {
        "static/index.html": INDEX_HTML,
        "static/auth.js": AUTH_JS,
        "static/base.css": BASE_CSS,
        **(static_files or {}),
    }
    return bool(c.put_archive("/app/static", _static_tar(files)))


def prune_dangling() -> None:
    """Borra imágenes huérfanas (<none>) que dejan los rebuilds del pipeline/QA."""
    try:
        docker.from_env(version="auto").images.prune(filters={"dangling": True})
    except Exception:  # noqa: BLE001
        pass


def _config_js(app_id: str) -> str:
    """Valores que el UI necesita en runtime (gateway público + id de la app)."""
    return (
        f"window.AUTH_GATEWAY = {json.dumps(settings.public_base_url)};\n"
        f"window.APP_ID = {json.dumps(app_id)};\n"
    )


def _check_js(js: str) -> str | None:
    """Valida la sintaxis del JS generado (no hay build que la atrape). Best-effort:
    si no está el parser, no bloquea. Atrapa los errores gruesos (comillas/llaves sin cerrar)."""
    if not js:
        return None
    try:
        import esprima  # parser ES puro-python, corre en el worker (no en la app)
    except Exception:  # noqa: BLE001
        return None
    try:
        esprima.parseScript(js)
        return None
    except Exception as exc:  # noqa: BLE001
        return f"ERROR DE SINTAXIS JS (static/app.js):\n{exc}"


def build_app(
    client, slug: str, app_id: str, main_py: str, static_files: dict[str, str] | None = None,
    backend_reqs: str = "",
):
    """Construye la imagen única de la app (FastAPI + UI estática). Parte de la base; solo
    instala el delta de `requirements.txt`. Lanza docker.errors.BuildError si falla."""
    ensure_base_images(client)
    tag = f"app-{slug}:latest"
    files = {
        "Dockerfile": APP_DOCKERFILE,
        "requirements.txt": backend_reqs or "",  # delta de pip (vacío = no-op)
        "main.py": main_py,
        "app_entry.py": APP_ENTRY_PY,
        "auth.py": APP_AUTH_PY,
        # UI estática fija
        "static/index.html": INDEX_HTML,
        "static/auth.js": AUTH_JS,
        "static/base.css": BASE_CSS,
        "static/config.js": _config_js(app_id),
        # defaults (los generados de abajo los sobrescriben)
        "static/app.js": "window.startApp = function(){};\n",
        "static/app.css": "",
    }
    files.update(static_files or {})  # static/app.js (+ static/app.css) generados
    return _build(client, tag, files)


def run_containers(
    slug: str, app_id: str, broker: str | None = None,
) -> str:
    """(Re)lanza el contenedor único de la app desde la imagen ya construida. Devuelve la URL.

    `broker`: si la app tiene jobs, la URL de Redis para que el api pueda encolar tareas.
    """
    client = docker.from_env(version="auto")
    tag, name = f"app-{slug}:latest", f"app-{slug}"
    vol = f"app-{slug}-static"
    host = f"{slug}.{APP_DOMAIN}"

    _ensure_network(client)
    _remove(client, name)
    # Limpia contenedores del modelo viejo de 2 contenedores (api/web), si existieran.
    _remove(client, f"app-{slug}-api")
    _remove(client, f"app-{slug}-web")
    # Recreamos el volumen de estáticos para que Docker lo POBLE desde la imagen recién
    # construida (si reusáramos el viejo, quedaría stale el scaffold fijo o el app.js).
    # Los cambios solo-front posteriores se aplican en caliente con apply_static().
    _remove_volume(client, vol)

    env = {
        "SESSION_SECRET": settings.session_secret,
        "AUTH_GATEWAY": settings.public_base_url,
        "INTERNAL_GATEWAY": INTERNAL_GATEWAY,
        "APP_ID": app_id,
        "APP_SECRET": _app_secret(app_id),
    }
    if broker:
        env["CELERY_BROKER_URL"] = broker
        env["CELERY_RESULT_BACKEND"] = broker
    client.containers.run(
        tag,
        name=name,
        detach=True,
        network=TRAEFIK_NETWORK,
        environment=env,
        # static/ en un volumen: permite hot-swap de la UI sin reconstruir (apply_static).
        volumes={vol: {"bind": "/app/static", "mode": "rw"}},
        labels=_traefik_labels(slug, host),
        restart_policy={"Name": "unless-stopped"},
    )
    if APP_CERTRESOLVER:  # prod (HTTPS, sin puerto)
        return f"https://{host}"
    return f"http://{host}:{APP_DOMAIN_PORT}"


def build_and_run(
    slug: str,
    app_id: str,
    main_py: str,
    static_files: dict[str, str] | None = None,
    backend_reqs: str = "",
    broker: str | None = None,
) -> str:
    """Construye y (re)lanza la app (un solo contenedor). Devuelve la URL pública."""
    client = docker.from_env(version="auto")
    build_app(client, slug, app_id, main_py, static_files, backend_reqs)
    return run_containers(slug, app_id, broker)


def qa_check(
    slug: str,
    app_id: str,
    main_py: str,
    static_files: dict[str, str] | None = None,
    backend_reqs: str = "",
) -> tuple[bool, str]:
    """QA: valida sintaxis del JS, construye la imagen (atrapa errores de build) y hace
    smoke test del backend (corre un contenedor temporal y verifica /api/health).
    Devuelve (ok, log_error)."""
    static_files = static_files or {}
    js_err = _check_js(static_files.get("static/app.js", ""))
    if js_err:
        return False, js_err

    client = docker.from_env(version="auto")
    try:
        image = build_app(client, slug, app_id, main_py, static_files, backend_reqs)
    except docker.errors.BuildError as exc:
        log = "".join(
            str(line.get("stream", "")) for line in exc.build_log if isinstance(line, dict)
        )
        return False, f"ERROR DE BUILD:\n{log[-4000:]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"ERROR DE BUILD: {exc}"

    qa_name = f"qa-{slug}"
    _remove(client, qa_name)
    container = client.containers.run(
        image.id,
        name=qa_name,
        detach=True,
        environment={
            "SESSION_SECRET": settings.session_secret,
            "AUTH_GATEWAY": settings.public_base_url,
            "APP_ID": app_id,
        },
    )
    try:
        time.sleep(4)
        container.reload()
        logs = container.logs(tail=80).decode("utf-8", "ignore")
        if container.status != "running":
            return False, f"ERROR DE RUNTIME (el backend no quedó corriendo):\n{logs[-3000:]}"
        code, out = container.exec_run(
            "python -c \"import urllib.request as u;"
            "print(u.urlopen('http://127.0.0.1:80/api/health').status)\""
        )
        if b"200" not in out:
            return False, f"SMOKE TEST FALLÓ (/api/health):\n{logs[-3000:]}\n{out.decode(errors='ignore')}"
        return True, "ok"
    finally:
        _remove(client, qa_name)


def run_celery_stack(slug: str, app_id: str) -> str:
    """Levanta Redis + Celery worker + Celery beat para una app con jobs programados.

    Worker y beat corren desde la MISMA imagen de la app (`celery -A main.celery_app ...`),
    así que el `main.py` generado debe exponer `celery_app` y su `beat_schedule`.
    """
    client = docker.from_env(version="auto")
    app_tag = f"app-{slug}:latest"
    redis_name = f"app-{slug}-redis"
    worker_name = f"app-{slug}-worker"
    beat_name = f"app-{slug}-beat"
    broker = f"redis://{redis_name}:6379/0"

    _ensure_network(client)
    for n in (redis_name, worker_name, beat_name):
        _remove(client, n)

    client.containers.run(
        "redis:7-alpine", name=redis_name, detach=True, network=TRAEFIK_NETWORK,
        restart_policy={"Name": "unless-stopped"},
    )
    env = {
        "CELERY_BROKER_URL": broker,
        "CELERY_RESULT_BACKEND": broker,
        "SESSION_SECRET": settings.session_secret,
        "AUTH_GATEWAY": settings.public_base_url,
        "INTERNAL_GATEWAY": INTERNAL_GATEWAY,
        "APP_ID": app_id,
        "APP_SECRET": _app_secret(app_id),
    }
    client.containers.run(
        app_tag, name=worker_name, detach=True, network=TRAEFIK_NETWORK, environment=env,
        command="celery -A main.celery_app worker --loglevel=info",
        restart_policy={"Name": "unless-stopped"},
    )
    client.containers.run(
        app_tag, name=beat_name, detach=True, network=TRAEFIK_NETWORK, environment=env,
        command="celery -A main.celery_app beat --loglevel=info",
        restart_policy={"Name": "unless-stopped"},
    )
    return broker


def teardown_app(slug: str) -> None:
    """Elimina contenedores e imágenes de la app (incluye el modelo viejo de 2 contenedores)."""
    client = docker.from_env(version="auto")
    for name in (
        f"app-{slug}", f"app-{slug}-api", f"app-{slug}-web",
        f"app-{slug}-redis", f"app-{slug}-worker", f"app-{slug}-beat",
    ):
        _remove(client, name)
    for tag in (f"app-{slug}:latest", f"app-{slug}-api:latest", f"app-{slug}-web:latest"):
        try:
            client.images.remove(tag, force=True)
        except (docker.errors.ImageNotFound, docker.errors.APIError):
            pass
    _remove_volume(client, f"app-{slug}-static")
