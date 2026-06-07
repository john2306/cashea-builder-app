"""Definición de herramientas del agente.

Cada herramienta tiene:
  - un esquema JSON (lo que ve Claude),
  - una bandera `long_running` (si se ejecuta en Celery o en línea),
  - un ejecutor async (solo para las herramientas en línea).

Para añadir una herramienta nueva: agrega su esquema a TOOL_SCHEMAS y, si es rápida,
regístrala en INLINE_EXECUTORS. Si es larga, impleméntala en `tasks/jobs.py`.

IMPORTANTE para el caching: el orden y contenido de TOOL_SCHEMAS debe ser determinista
(las herramientas se renderizan en la posición 0 del prompt; reordenarlas invalida la caché).
"""
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

from .skills import get_skill_body, skill_names

# --- Esquemas (lo que Claude ve) -------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "use_skill",
        "description": (
            "Carga un PLAYBOOK (skill) con instrucciones detalladas para construir cierto tipo de "
            "app. Llamalo ANTES de `define_app` cuando el pedido del usuario encaje con una skill "
            "disponible (ver la lista 'SKILLS' en el system prompt). Devuelve el contenido del "
            "playbook; seguilo para armar la spec. Podés combinar skills (p. ej. crud-backoffice + "
            "app-with-database)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Nombre de la skill a cargar (ver la lista 'SKILLS' en el system prompt).",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "current_datetime",
        "description": "Devuelve la fecha y hora actual del servidor en formato ISO 8601 (UTC).",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "fetch_url",
        "description": (
            "Hace una petición HTTP GET a una URL y devuelve el cuerpo de la respuesta "
            "(truncado). Útil para consultar APIs públicas o verificar el estado de un servicio."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL completa, incluyendo https://"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "run_batch_job",
        "description": (
            "Lanza un proceso por lotes en segundo plano (tarea larga). Reporta progreso "
            "en tiempo real y devuelve un resumen al finalizar. Úsalo para trabajos que "
            "tardan: procesamiento de datos, generación de reportes, sincronizaciones, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_name": {
                    "type": "string",
                    "description": "Nombre descriptivo del proceso a ejecutar.",
                },
                "steps": {
                    "type": "integer",
                    "description": "Número de pasos a procesar (1-20).",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["job_name"],
        },
    },
    {
        "name": "list_datasets",
        "description": (
            "Lista las planillas/datasets cargados en la conversación (CSV, XLSX o Google "
            "Sheets), con su table_id, nombre y dimensiones. Úsalo para saber qué datos hay."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "profile_dataset",
        "description": (
            "Devuelve el perfil de un dataset: columnas, tipos, nulos, muestra de filas y "
            "resumen estadístico. Úsalo primero para entender la estructura de los datos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_id": {"type": "string", "description": "ID del dataset (de list_datasets)."},
            },
            "required": ["table_id"],
        },
    },
    {
        "name": "analyze_dataset",
        "description": (
            "Análisis DETERMINISTA con pandas sobre un dataset. Operaciones: head, describe, "
            "value_counts (frecuencias de una columna), groupby (agrupar y agregar), "
            "correlation (correlación numérica), filter (consulta tipo pandas.query), sort, "
            "agg (sum/mean/min/max de una columna). Devuelve resultados exactos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_id": {"type": "string"},
                "operation": {
                    "type": "string",
                    "enum": [
                        "head", "describe", "value_counts", "groupby",
                        "correlation", "filter", "sort", "agg",
                    ],
                },
                "column": {"type": "string", "description": "Columna objetivo (según operación)."},
                "by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Columnas para agrupar (groupby).",
                },
                "agg": {
                    "type": "string",
                    "description": "Función de agregación: mean, sum, count, min, max, median.",
                },
                "query": {
                    "type": "string",
                    "description": "Expresión de filtro estilo pandas, ej: \"edad > 30 and pais == 'PE'\".",
                },
                "n": {"type": "integer", "description": "Cantidad de filas a devolver."},
                "ascending": {"type": "boolean", "description": "Orden ascendente (sort)."},
            },
            "required": ["table_id", "operation"],
        },
    },
    {
        "name": "load_google_sheet",
        "description": (
            "Carga una Google Sheet como dataset analizable (vía el MCP de Google conectado). "
            "Recibe el nombre o el ID del archivo. Luego usá profile_dataset/analyze_dataset."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Nombre o ID de la Google Sheet."},
            },
            "required": ["query"],
        },
    },
    # --- CRUD de Google Sheets (API directa, conexión google_sheets) -------------
    {
        "name": "sheet_create",
        "description": (
            "Crea una NUEVA Google Sheet con el título dado y (opcional) una fila de "
            "encabezados. Devuelve el spreadsheet_id para escribir después. Usá esta tool para "
            "crear planillas — NO uses drive_create_file con un mime de Google."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "headers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Encabezados de la fila 1 (opcional).",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "sheet_find",
        "description": "Busca Google Sheets por nombre. Devuelve sus IDs y títulos.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Texto del nombre a buscar."}},
            "required": ["query"],
        },
    },
    {
        "name": "sheet_info",
        "description": "Metadatos de una Google Sheet: título y pestañas (nombre, filas, columnas).",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": {"type": "string"}},
            "required": ["spreadsheet_id"],
        },
    },
    {
        "name": "sheet_read",
        "description": (
            "Lee un rango A1 de una Google Sheet (ej: 'Hoja1!A1:D50' o 'Hoja1'). "
            "Devuelve las filas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range": {"type": "string", "description": "Rango A1, ej: Hoja1!A1:D100"},
            },
            "required": ["spreadsheet_id", "range"],
        },
    },
    {
        "name": "sheet_update",
        "description": (
            "Escribe/actualiza valores en un rango A1 (sobrescribe). `values` es una matriz "
            "de filas. Ej: range='Hoja1!B2', values=[[500]]."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range": {"type": "string"},
                "values": {
                    "type": "array",
                    "items": {"type": "array", "items": {}},
                    "description": "Matriz de filas (cada fila es un array de celdas).",
                },
            },
            "required": ["spreadsheet_id", "range", "values"],
        },
    },
    {
        "name": "sheet_append",
        "description": "Agrega filas al final de una hoja/rango. `values` es una matriz de filas.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range": {"type": "string", "description": "Hoja o rango, ej: Hoja1"},
                "values": {"type": "array", "items": {"type": "array", "items": {}}},
            },
            "required": ["spreadsheet_id", "range", "values"],
        },
    },
    {
        "name": "sheet_clear",
        "description": "Limpia (vacía) el contenido de un rango A1 sin borrar las filas.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range": {"type": "string"},
            },
            "required": ["spreadsheet_id", "range"],
        },
    },
    {
        "name": "sheet_delete_rows",
        "description": (
            "Borra filas donde una columna (por encabezado, fila 1) sea igual a un valor. "
            "Ej: column='estado', equals='cancelado'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "sheet_name": {"type": "string", "description": "Nombre de la pestaña."},
                "column": {"type": "string", "description": "Encabezado de la columna."},
                "equals": {"type": "string", "description": "Valor a coincidir para borrar."},
            },
            "required": ["spreadsheet_id", "sheet_name", "column", "equals"],
        },
    },
    {
        "name": "sheet_delete_tab",
        "description": "Borra una pestaña/hoja completa de la planilla.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "sheet_name": {"type": "string"},
            },
            "required": ["spreadsheet_id", "sheet_name"],
        },
    },
    # --- CRUD de Google Drive (API directa, conexión google_drive) ---------------
    {
        "name": "drive_search",
        "description": (
            "Busca archivos/carpetas en Google Drive por nombre. Devuelve id, nombre y tipo. "
            "Dejá `query` vacío para listar los más recientes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Texto del nombre a buscar."}},
        },
    },
    {
        "name": "drive_list_folder",
        "description": "Lista el contenido (archivos y subcarpetas) de una carpeta por su ID.",
        "input_schema": {
            "type": "object",
            "properties": {"folder_id": {"type": "string"}},
            "required": ["folder_id"],
        },
    },
    {
        "name": "drive_read",
        "description": (
            "Lee el contenido de texto de un archivo (exporta Google Docs/Sheets/Slides a "
            "texto/CSV). Devuelve el contenido truncado."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
            "required": ["file_id"],
        },
    },
    {
        "name": "drive_create_folder",
        "description": "Crea una carpeta. Opcional: `parent` (ID de la carpeta contenedora).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "parent": {"type": "string", "description": "ID de la carpeta padre (opcional)."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "drive_create_file",
        "description": (
            "Crea un archivo de texto en Drive con el contenido dado. `mime` opcional "
            "(text/plain por defecto; text/csv, text/markdown, text/html…). `parent` opcional."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "content": {"type": "string"},
                "mime": {"type": "string"},
                "parent": {"type": "string", "description": "ID de la carpeta padre (opcional)."},
            },
            "required": ["name", "content"],
        },
    },
    {
        "name": "drive_update_file",
        "description": "Reemplaza el contenido de un archivo de texto existente (por su ID).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "content": {"type": "string"},
                "mime": {"type": "string"},
            },
            "required": ["file_id", "content"],
        },
    },
    {
        "name": "drive_rename",
        "description": "Renombra un archivo o carpeta (por su ID).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["file_id", "name"],
        },
    },
    {
        "name": "drive_move",
        "description": "Mueve un archivo/carpeta a otra carpeta (cambia su padre).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "new_parent": {"type": "string", "description": "ID de la carpeta destino."},
            },
            "required": ["file_id", "new_parent"],
        },
    },
    {
        "name": "drive_copy",
        "description": "Duplica un archivo. Opcional: `name` para la copia.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "drive_delete",
        "description": (
            "Borra un archivo/carpeta. Por defecto lo manda a la PAPELERA (recuperable). "
            "Pasá permanent=true solo si el usuario pidió borrado definitivo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "permanent": {"type": "boolean"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "drive_share",
        "description": (
            "Comparte un archivo/carpeta. Con `email` para una persona, o anyone=true para "
            "cualquiera con el enlace. `role`: reader (def), commenter o writer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "email": {"type": "string", "description": "Correo de la persona (si no es anyone)."},
                "role": {"type": "string", "enum": ["reader", "commenter", "writer"]},
                "anyone": {"type": "boolean", "description": "Compartir con cualquiera con el enlace."},
            },
            "required": ["file_id"],
        },
    },
    # --- Google Docs (API directa, conexión google_docs) -------------------------
    {
        "name": "doc_create",
        "description": "Crea un Google Doc vacío con el título dado. Devuelve su documentId.",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    },
    {
        "name": "doc_read",
        "description": "Lee el contenido de texto de un Google Doc por su ID.",
        "input_schema": {
            "type": "object",
            "properties": {"document_id": {"type": "string"}},
            "required": ["document_id"],
        },
    },
    {
        "name": "doc_append",
        "description": "Agrega texto al final de un Google Doc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["document_id", "text"],
        },
    },
    {
        "name": "doc_replace",
        "description": (
            "Reemplaza TODAS las apariciones de un texto por otro en un Google Doc "
            "(útil para plantillas: reemplazar marcadores como {{nombre}})."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "find": {"type": "string"},
                "replace": {"type": "string"},
            },
            "required": ["document_id", "find", "replace"],
        },
    },
    # --- Notion: ahora vía su MCP hosteado (mcp.notion.com), no API directa.
    #     El agente lo usa por el conector MCP (active_mcp_servers); las apps por connector-proxy.
    # --- Gmail (API directa, conexión gmail) -------------------------------------
    {
        "name": "gmail_search",
        "description": (
            "Busca/lista correos con la sintaxis de Gmail (ej: 'from:juan is:unread', "
            "'subject:factura newer_than:7d'). Devuelve id, remitente, asunto y resumen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query de Gmail (vacío = recientes)."},
                "max_results": {"type": "integer"},
            },
        },
    },
    {
        "name": "gmail_read",
        "description": "Lee un correo completo por su ID (remitente, asunto, fecha y cuerpo).",
        "input_schema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
    },
    {
        "name": "gmail_send",
        "description": "Envía un correo. Requiere destinatario, asunto y cuerpo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Correo del destinatario."},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "gmail_draft",
        "description": "Crea un BORRADOR de correo (no lo envía). Útil para revisión previa.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    # --- Google Calendar (API directa, conexión google_calendar) -----------------
    {
        "name": "calendar_list_calendars",
        "description": "Lista los calendarios del usuario (id y nombre).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "calendar_list_events",
        "description": (
            "Lista/busca eventos de un calendario (por defecto 'primary', de ahora en "
            "adelante). Opcional: rango ISO (time_min/time_max) y búsqueda de texto."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "description": "Def: primary."},
                "time_min": {"type": "string", "description": "ISO 8601 (def: ahora)."},
                "time_max": {"type": "string", "description": "ISO 8601."},
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
            },
        },
    },
    {
        "name": "calendar_create_event",
        "description": (
            "Crea un evento. `start`/`end` en ISO 8601 con hora (2026-06-10T15:00:00-05:00) "
            "o fecha YYYY-MM-DD para todo el día. Opcional: descripción, ubicación, invitados."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Título del evento."},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "calendar_id": {"type": "string", "description": "Def: primary."},
                "description": {"type": "string"},
                "location": {"type": "string"},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Correos de los invitados.",
                },
            },
            "required": ["summary", "start", "end"],
        },
    },
    {
        "name": "calendar_update_event",
        "description": "Actualiza un evento existente (cualquier campo: título, horas, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "calendar_id": {"type": "string", "description": "Def: primary."},
                "summary": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "description": {"type": "string"},
                "location": {"type": "string"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "calendar_delete_event",
        "description": "Borra un evento por su ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "calendar_id": {"type": "string", "description": "Def: primary."},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "define_app",
        "description": (
            "Compila la app de backoffice descrita por el usuario en una SPEC y la guarda en "
            "la app actual. El equipo de devs (backend FastAPI async + frontend React TS + QA) "
            "la construye al Desplegar. Definí entidades (con su fuente: bigquery / "
            "google_sheets / postgres, y location p.ej. 'proyecto.dataset.tabla' o el nombre de "
            "tabla en postgres), pantallas (table/form/dashboard/detail con sus acciones) y "
            "notificaciones. Si el usuario quiere que la app GESTIONE sus propios datos/estados/"
            "relaciones, usá data_source 'postgres' (schema aislado por app). Sé concreto y completo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "data_sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Fuentes: bigquery, google_sheets, slack, gmail, notion, postgres. "
                        "Usá 'postgres' cuando la app necesita su PROPIA base de datos para "
                        "gestionar su estado/datos/relaciones (CRUD persistente, entidades "
                        "relacionadas) en vez de una hoja/servicio externo: se aprovisiona un "
                        "SCHEMA aislado y dedicado por app. Es la opción correcta si el usuario "
                        "pide 'una base de datos para la app', 'gestionar estados', 'relaciones', etc."
                    ),
                },
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "source": {"type": "string"},
                            "location": {"type": "string", "description": "proyecto.dataset.tabla / spreadsheet_id"},
                            "fields": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "type": {"type": "string"},
                                    },
                                    "required": ["name"],
                                },
                            },
                        },
                        "required": ["name"],
                    },
                },
                "screens": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string", "enum": ["table", "form", "dashboard", "detail"]},
                            "entity": {"type": "string"},
                            "actions": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "type"],
                    },
                },
                "notifications": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "screens"],
        },
    },
    {
        "name": "inspect_app_code",
        "description": (
            "Lee (solo-lectura) el CÓDIGO REAL ya generado de la app actual: el backend "
            "(main.py, FastAPI), el frontend (static/app.js, static/styles.css, index.html) y "
            "requirements.txt. Llamalo SIN `path` para ver el índice de archivos (nombres + "
            "tamaño); llamalo CON `path` (p. ej. 'static/app.js' o 'main.py') para ver el "
            "contenido completo de un archivo. USALO ANTES de `edit_app` cuando el cambio no sea "
            "trivial, para ubicar exactamente qué tocar y redactar una instrucción ESPECÍFICA "
            "(nombre de función, selector CSS, endpoint) en vez de una vaga. Solo disponible "
            "después del primer Despliegue (antes no hay código)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Archivo a leer completo (ej: 'main.py', 'static/app.js', "
                        "'static/styles.css'). Omitilo para ver el índice de archivos."
                    ),
                },
            },
        },
    },
    {
        "name": "edit_app",
        "description": (
            "Pedí un CAMBIO INCREMENTAL sobre una app YA construida/desplegada, en lenguaje "
            "natural (p. ej. 'cambiá el color de los botones a azul', 'agregá una columna "
            "Estado a la tabla', 'el título debería decir Clientes Activos'). En el próximo "
            "Desplegar, el equipo edita el código ACTUAL con el cambio mínimo (no regenera "
            "todo): si es solo de UI, el deploy es un refresh instantáneo. Usá esta tool para "
            "ajustes sobre algo que ya existe; usá `define_app` solo para la definición inicial "
            "o cambios estructurales grandes (nuevas entidades/pantallas/fuentes de datos)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "El cambio a aplicar, claro y específico, en una frase.",
                },
            },
            "required": ["instruction"],
        },
    },
]

# Herramientas que se ejecutan en Celery (no en el proceso de FastAPI).
LONG_RUNNING_TOOLS: set[str] = {"run_batch_job"}


# --- Ejecutores en línea (rápidos, dentro del loop async) ------------------------

async def _current_datetime(_: dict[str, Any]) -> str:
    return datetime.now(timezone.utc).isoformat()


async def _fetch_url(args: dict[str, Any]) -> str:
    url = args["url"]
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(url)
    body = resp.text[:4000]
    return f"HTTP {resp.status_code}\n\n{body}"


# --- Análisis de planillas (CSV/XLSX/Google Sheets) con pandas -------------------

async def _list_datasets(_: dict[str, Any]) -> str:
    from ..analysis import datasets

    return datasets.list_datasets()


async def _profile_dataset(args: dict[str, Any]) -> str:
    from ..analysis import datasets

    return datasets.profile(args["table_id"])


async def _analyze_dataset(args: dict[str, Any]) -> str:
    from ..analysis import datasets

    params = {k: v for k, v in args.items() if k not in ("table_id", "operation")}
    return datasets.analyze(args["table_id"], args["operation"], params)


async def _load_google_sheet(args: dict[str, Any]) -> str:
    """Lee una Google Sheet (API directa) y la ingiere como dataset para análisis pandas."""
    import csv
    import io

    from ..connectors import sheets as sheets_api; from ..analysis import datasets

    query = args["query"].strip()
    try:
        # Si parece un ID (largo, sin espacios) lo usamos directo; si no, buscamos por nombre.
        if " " not in query and len(query) > 25:
            sid, name = query, query
        else:
            files = await sheets_api.find_spreadsheets(query)
            if not files:
                return f"No se encontró ninguna Google Sheet con el nombre '{query}'."
            sid, name = files[0]["id"], files[0]["name"]
        meta = await sheets_api.metadata(sid)
        first_tab = meta["sheets"][0]["title"] if meta["sheets"] else "Sheet1"
        rows = await sheets_api.read_range(sid, first_tab)
        if not rows:
            return f"La hoja '{name}' está vacía."
        buf = io.StringIO()
        csv.writer(buf).writerows(rows)
        profiles = datasets.ingest_text(name, buf.getvalue(), "csv")
        return f"Cargada '{name}' (pestaña {first_tab}).\n\n" + "\n\n".join(profiles)
    except sheets_api.NotConnected as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"No se pudo leer la Google Sheet '{query}': {exc}"


# --- CRUD de Google Sheets (API directa) -----------------------------------------

async def _sheet_create(args: dict[str, Any]) -> str:
    from ..connectors import sheets as sheets_api

    res = await sheets_api.create_spreadsheet(args["title"], args.get("headers"))
    return f"Planilla creada: {res['title']} (id: {res['spreadsheet_id']})\n{res.get('url', '')}"


async def _sheet_find(args: dict[str, Any]) -> str:
    from ..connectors import sheets as sheets_api

    files = await sheets_api.find_spreadsheets(args["query"])
    if not files:
        return "No se encontraron planillas con ese nombre."
    return "\n".join(f"- {f['name']} (id: {f['id']})" for f in files)


async def _sheet_info(args: dict[str, Any]) -> str:
    from ..connectors import sheets as sheets_api

    m = await sheets_api.metadata(args["spreadsheet_id"])
    tabs = "\n".join(f"  - {s['title']} ({s['rows']}×{s['cols']})" for s in m["sheets"])
    return f"{m['title']}\nPestañas:\n{tabs}"


def _rows_to_text(rows: list) -> str:
    if not rows:
        return "(vacío)"
    return "\n".join(" | ".join(str(c) for c in r) for r in rows[:100])


async def _sheet_read(args: dict[str, Any]) -> str:
    from ..connectors import sheets as sheets_api

    rows = await sheets_api.read_range(args["spreadsheet_id"], args["range"])
    return f"{len(rows)} filas:\n" + _rows_to_text(rows)


async def _sheet_update(args: dict[str, Any]) -> str:
    from ..connectors import sheets as sheets_api

    res = await sheets_api.update_range(args["spreadsheet_id"], args["range"], args["values"])
    return f"Actualizado: {res.get('updatedCells', '?')} celdas en {res.get('updatedRange', args['range'])}."


async def _sheet_append(args: dict[str, Any]) -> str:
    from ..connectors import sheets as sheets_api

    await sheets_api.append_rows(args["spreadsheet_id"], args["range"], args["values"])
    return f"Agregadas {len(args['values'])} fila(s)."


async def _sheet_clear(args: dict[str, Any]) -> str:
    from ..connectors import sheets as sheets_api

    await sheets_api.clear_range(args["spreadsheet_id"], args["range"])
    return f"Rango {args['range']} limpiado."


async def _sheet_delete_rows(args: dict[str, Any]) -> str:
    from ..connectors import sheets as sheets_api

    res = await sheets_api.delete_rows_where(
        args["spreadsheet_id"], args["sheet_name"], args["column"], args["equals"]
    )
    return f"Filas borradas: {res['deleted']}."


async def _sheet_delete_tab(args: dict[str, Any]) -> str:
    from ..connectors import sheets as sheets_api

    await sheets_api.delete_tab(args["spreadsheet_id"], args["sheet_name"])
    return f"Pestaña '{args['sheet_name']}' borrada."


# --- CRUD de Google Drive (API directa) ------------------------------------------

def _files_to_text(files: list[dict[str, Any]]) -> str:
    if not files:
        return "(sin resultados)"
    out = []
    for f in files:
        kind = "📁" if f.get("mimeType") == "application/vnd.google-apps.folder" else "📄"
        out.append(f"{kind} {f.get('name')} (id: {f.get('id')})")
    return "\n".join(out)


async def _drive_search(args: dict[str, Any]) -> str:
    from ..connectors import drive as drive_api

    return _files_to_text(await drive_api.search(args.get("query", "")))


async def _drive_list_folder(args: dict[str, Any]) -> str:
    from ..connectors import drive as drive_api

    return _files_to_text(await drive_api.list_folder(args["folder_id"]))


async def _drive_read(args: dict[str, Any]) -> str:
    from ..connectors import drive as drive_api

    name, content = await drive_api.read_file(args["file_id"])
    return f"{name}:\n\n{content[:4000]}"


async def _drive_create_folder(args: dict[str, Any]) -> str:
    from ..connectors import drive as drive_api

    f = await drive_api.create_folder(args["name"], args.get("parent"))
    return f"Carpeta creada: {f.get('name')} (id: {f.get('id')})"


async def _drive_create_file(args: dict[str, Any]) -> str:
    from ..connectors import drive as drive_api

    f = await drive_api.create_file(
        args["name"], args.get("content", ""), args.get("mime", "text/plain"), args.get("parent")
    )
    return f"Archivo creado: {f.get('name')} (id: {f.get('id')})"


async def _drive_update_file(args: dict[str, Any]) -> str:
    from ..connectors import drive as drive_api

    f = await drive_api.update_file(args["file_id"], args["content"], args.get("mime", "text/plain"))
    return f"Archivo actualizado: {f.get('name', args['file_id'])}."


async def _drive_rename(args: dict[str, Any]) -> str:
    from ..connectors import drive as drive_api

    f = await drive_api.rename(args["file_id"], args["name"])
    return f"Renombrado a '{f.get('name')}'."


async def _drive_move(args: dict[str, Any]) -> str:
    from ..connectors import drive as drive_api

    await drive_api.move(args["file_id"], args["new_parent"])
    return "Movido a la carpeta destino."


async def _drive_copy(args: dict[str, Any]) -> str:
    from ..connectors import drive as drive_api

    f = await drive_api.copy_file(args["file_id"], args.get("name"))
    return f"Copia creada: {f.get('name')} (id: {f.get('id')})"


async def _drive_delete(args: dict[str, Any]) -> str:
    from ..connectors import drive as drive_api

    res = await drive_api.delete(args["file_id"], bool(args.get("permanent")))
    return "Borrado definitivo." if res.get("permanent") else "Enviado a la papelera."


async def _drive_share(args: dict[str, Any]) -> str:
    from ..connectors import drive as drive_api

    await drive_api.share(
        args["file_id"], args.get("email"), args.get("role", "reader"), bool(args.get("anyone"))
    )
    target = "cualquiera con el enlace" if args.get("anyone") else args.get("email", "")
    return f"Compartido con {target} ({args.get('role', 'reader')})."


# --- Google Docs (API directa) ---------------------------------------------------

async def _doc_create(args: dict[str, Any]) -> str:
    from ..connectors import docs as docs_api

    d = await docs_api.create(args["title"])
    return f"Documento creado: {d.get('title')} (id: {d.get('documentId')})"


async def _doc_read(args: dict[str, Any]) -> str:
    from ..connectors import docs as docs_api

    title, text = await docs_api.read_text(args["document_id"])
    return f"{title}:\n\n{text[:4000]}"


async def _doc_append(args: dict[str, Any]) -> str:
    from ..connectors import docs as docs_api

    await docs_api.append_text(args["document_id"], args["text"])
    return "Texto agregado al documento."


async def _doc_replace(args: dict[str, Any]) -> str:
    from ..connectors import docs as docs_api

    await docs_api.replace_text(args["document_id"], args["find"], args["replace"])
    return f"Reemplazado «{args['find']}» por «{args['replace']}»."


# --- Notion: vía su MCP hosteado (mcp.notion.com); sin tool de API directa. ------

# --- Gmail (API directa) ---------------------------------------------------------

async def _gmail_search(args: dict[str, Any]) -> str:
    from ..connectors import gmail as gmail_api

    msgs = await gmail_api.search(args.get("query", ""), int(args.get("max_results", 10)))
    if not msgs:
        return "Sin correos para esa búsqueda."
    return "\n".join(
        f"- [{m['id']}] {m['from']} — {m['subject']}\n  {m['snippet']}" for m in msgs
    )


async def _gmail_read(args: dict[str, Any]) -> str:
    from ..connectors import gmail as gmail_api

    m = await gmail_api.read_message(args["message_id"])
    return (
        f"De: {m['from']}\nPara: {m['to']}\nAsunto: {m['subject']}\nFecha: {m['date']}\n\n"
        f"{m['body'][:4000]}"
    )


async def _gmail_send(args: dict[str, Any]) -> str:
    from ..connectors import gmail as gmail_api

    await gmail_api.send(args["to"], args["subject"], args["body"])
    return f"Correo enviado a {args['to']}."


async def _gmail_draft(args: dict[str, Any]) -> str:
    from ..connectors import gmail as gmail_api

    await gmail_api.create_draft(args["to"], args["subject"], args["body"])
    return f"Borrador creado para {args['to']}."


# --- Google Calendar (API directa) -----------------------------------------------

async def _calendar_list_calendars(_: dict[str, Any]) -> str:
    from ..connectors import calendar as cal_api

    cals = await cal_api.list_calendars()
    if not cals:
        return "Sin calendarios."
    return "\n".join(
        f"- {c.get('summary')}{' (primary)' if c.get('primary') else ''} (id: {c.get('id')})"
        for c in cals
    )


async def _calendar_list_events(args: dict[str, Any]) -> str:
    from ..connectors import calendar as cal_api

    events = await cal_api.list_events(
        args.get("calendar_id", "primary"), args.get("time_min"), args.get("time_max"),
        args.get("query"), int(args.get("max_results", 20)),
    )
    if not events:
        return "Sin eventos para ese rango."
    return "\n".join(
        f"- [{e['id']}] {e['start']} — {e['summary']}"
        + (f" @ {e['location']}" if e["location"] else "")
        for e in events
    )


async def _calendar_create_event(args: dict[str, Any]) -> str:
    from ..connectors import calendar as cal_api

    e = await cal_api.create_event(
        args["summary"], args["start"], args["end"],
        args.get("calendar_id", "primary"), args.get("description"),
        args.get("location"), args.get("attendees"),
    )
    return f"Evento creado: {e.get('summary')} (id: {e.get('id')})."


async def _calendar_update_event(args: dict[str, Any]) -> str:
    from ..connectors import calendar as cal_api

    fields = {k: v for k, v in args.items() if k not in ("event_id", "calendar_id")}
    await cal_api.update_event(args["event_id"], args.get("calendar_id", "primary"), **fields)
    return "Evento actualizado."


async def _calendar_delete_event(args: dict[str, Any]) -> str:
    from ..connectors import calendar as cal_api

    await cal_api.delete_event(args["event_id"], args.get("calendar_id", "primary"))
    return "Evento borrado."


async def _use_skill(args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    body = get_skill_body(name)
    if not body:
        return f"No existe la skill '{name}'. Disponibles: {', '.join(skill_names()) or '(ninguna)'}."
    return f"# SKILL: {name}\n\n{body}\n\n(Seguí este playbook para diseñar la app con define_app.)"


InlineExecutor = Callable[[dict[str, Any]], Awaitable[str]]

INLINE_EXECUTORS: dict[str, InlineExecutor] = {
    "use_skill": _use_skill,
    "current_datetime": _current_datetime,
    "fetch_url": _fetch_url,
    "list_datasets": _list_datasets,
    "profile_dataset": _profile_dataset,
    "analyze_dataset": _analyze_dataset,
    "load_google_sheet": _load_google_sheet,
    "sheet_create": _sheet_create,
    "sheet_find": _sheet_find,
    "sheet_info": _sheet_info,
    "sheet_read": _sheet_read,
    "sheet_update": _sheet_update,
    "sheet_append": _sheet_append,
    "sheet_clear": _sheet_clear,
    "sheet_delete_rows": _sheet_delete_rows,
    "sheet_delete_tab": _sheet_delete_tab,
    "drive_search": _drive_search,
    "drive_list_folder": _drive_list_folder,
    "drive_read": _drive_read,
    "drive_create_folder": _drive_create_folder,
    "drive_create_file": _drive_create_file,
    "drive_update_file": _drive_update_file,
    "drive_rename": _drive_rename,
    "drive_move": _drive_move,
    "drive_copy": _drive_copy,
    "drive_delete": _drive_delete,
    "drive_share": _drive_share,
    "doc_create": _doc_create,
    "doc_read": _doc_read,
    "doc_append": _doc_append,
    "doc_replace": _doc_replace,
    "gmail_search": _gmail_search,
    "gmail_read": _gmail_read,
    "gmail_send": _gmail_send,
    "gmail_draft": _gmail_draft,
    "calendar_list_calendars": _calendar_list_calendars,
    "calendar_list_events": _calendar_list_events,
    "calendar_create_event": _calendar_create_event,
    "calendar_update_event": _calendar_update_event,
    "calendar_delete_event": _calendar_delete_event,
}


# Tools BUILT-IN del agente agrupadas por conector (prefijo de nombre). Si un admin deshabilita
# un conector en Manager, estas tools se ocultan al agente (ver runner.filter_tools_by_state).
PROVIDER_TOOL_PREFIXES: dict[str, tuple[str, ...]] = {
    "google_sheets": ("sheet_", "load_google_sheet"),
    "google_drive": ("drive_",),
    "google_docs": ("doc_",),
    "gmail": ("gmail_",),
    "google_calendar": ("calendar_",),
}


def filter_tools_by_state(schemas: list[dict[str, Any]], disabled: set[str]) -> list[dict[str, Any]]:
    """Quita del set de tools las que pertenecen a conectores deshabilitados (por prefijo)."""
    if not disabled:
        return schemas
    blocked: tuple[str, ...] = tuple(
        p for prov in disabled for p in PROVIDER_TOOL_PREFIXES.get(prov, ())
    )
    if not blocked:
        return schemas
    return [t for t in schemas if not t["name"].startswith(blocked)]
