"""Generación de código de la app a partir de la conversación, usando Claude.

Produce DOS archivos con criterio:
  - backend `main.py`  -> FastAPI async (rutas bajo /api)
  - frontend `app.js`  -> HTML/JS/CSS vanilla (sin build) que consume /api

El resto del scaffold (index.html, auth.js, Dockerfile) es fijo y vive en `deploy.py`;
el mismo contenedor FastAPI sirve la API y la UI estática.
"""
import re

from ..agent.runner import get_client
from ..core.config import settings

SYSTEM = """Generas el código de una app a partir de una conversación que describe una \
automatización. Produces DOS archivos: un backend FastAPI async y un frontend en JS vanilla.

BACKEND (main.py):
- Solo `fastapi` + librería estándar de Python. `app = FastAPI()`. Endpoints async.
- TODAS las rutas bajo el prefijo `/api` (p. ej. `GET /api/health`, `POST /api/steps/{n}`).
- `GET /api/health` devuelve {"status": "ok"}.
- Un endpoint por cada paso del flujo que devuelva JSON simulando el resultado del paso \
(NO hagas llamadas de red reales ni uses credenciales).
- Habilita CORS abierto (CORSMiddleware, allow_origins=["*"]).

FRONTEND (app.js) — HTML/JS/CSS VANILLA, sin framework ni TypeScript ni build:
- Es un <script> CLÁSICO. Definí `window.startApp = function () { ... }`: el gate de \
autenticación la llama cuando el usuario ya está logueado. Renderizá en \
`document.getElementById("app")` con document.createElement / innerHTML.
- Para llamar al backend usá SIEMPRE el global `authFetch(path, init)` (ya agrega la \
sesión, mismo origen): `authFetch("/api/...").then(function (r) { return r.json(); })`. \
NO implementes login ni manejes tokens (el gate ya lo hace). NO uses import/export ni JSX.
- UI limpia: cabecera con título/descripción, lista de pasos con un botón para ejecutarlos \
que muestre la respuesta JSON, e indicador del estado de `/api/health`.
- JS válido, sin errores de sintaxis.

Formato de salida EXACTO, sin texto adicional ni explicaciones:
===FILE:main.py===
<contenido completo de main.py>
===FILE:app.js===
<contenido completo de app.js>
===END==="""

_DEFAULT_APP_JS = """\
window.startApp = function () {
  var app = document.getElementById("app");
  app.innerHTML =
    '<div style="font-family:system-ui;padding:40px">' +
    "<h1>App desplegada</h1>" +
    '<p>Backend FastAPI disponible en <code>/api</code>.</p></div>';
};
"""


def _parse_files(text: str) -> dict[str, str]:
    files: dict[str, str] = {}
    parts = re.split(r"===FILE:(.*?)===", text)
    for i in range(1, len(parts) - 1, 2):
        name = parts[i].strip()
        content = parts[i + 1].split("===END===")[0].strip()
        # Quita vallas de código si el modelo las añadió.
        fenced = re.match(r"^```[a-zA-Z]*\s*\n(.*?)```$", content, re.DOTALL)
        if fenced:
            content = fenced.group(1).strip()
        files[name] = content
    return files


async def generate_app_files(title: str, transcript: str) -> dict[str, str]:
    client = get_client()
    prompt = (
        f"App: {title}\n\n"
        f"Conversación (si está vacía, usa el título como intención):\n{transcript}\n\n"
        "Genera los dos archivos."
    )
    message = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in message.content if block.type == "text")
    files = _parse_files(text)

    main_py = files.get("main.py", "")
    app_js = files.get("app.js", "") or files.get("static/app.js", "") or _DEFAULT_APP_JS
    if not main_py or "FastAPI" not in main_py:
        raise RuntimeError("El code-gen no produjo un backend FastAPI válido.")
    return {"main_py": main_py, "app_js": app_js}
