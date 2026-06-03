"""Análisis determinista de planillas (CSV / XLSX / Google Sheets) con pandas.

Las tablas adjuntadas en el chat se ingieren a un DataFrame y se guardan en memoria
(por proceso) bajo un `table_id`. El agente las analiza con las tools del módulo
`agent/tools.py` (profile_dataset, analyze_dataset). Los resultados se devuelven como
texto/markdown listo para que el modelo lo lea.

Persistencia: en memoria del proceso del backend. Si el backend reinicia, hay que
re-adjuntar el archivo (el perfil queda en el historial de la conversación).
"""
from __future__ import annotations

import io
import uuid
from typing import Any

import pandas as pd

# table_id -> {"name": str, "df": DataFrame}
_TABLES: dict[str, dict[str, Any]] = {}
MAX_TABLES = 50
MAX_ROWS = 200_000  # cota defensiva al ingerir


def _store(name: str, df: pd.DataFrame) -> str:
    if len(df) > MAX_ROWS:
        df = df.head(MAX_ROWS)
    tid = uuid.uuid4().hex[:8]
    _TABLES[tid] = {"name": name, "df": df}
    # Evicción simple: si superamos el máximo, descartamos las más viejas.
    while len(_TABLES) > MAX_TABLES:
        _TABLES.pop(next(iter(_TABLES)))
    return tid


def get_df(table_id: str) -> pd.DataFrame:
    entry = _TABLES.get(table_id)
    if entry is None:
        raise KeyError(table_id)
    return entry["df"]


def ingest(name: str, data: bytes, fmt: str) -> list[str]:
    """Parsea CSV/TSV/XLSX. Devuelve un perfil (texto) por hoja/tabla creada."""
    fmt = (fmt or "").lower()
    profiles: list[str] = []
    if fmt in ("xlsx", "xls"):
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None)  # {hoja: df}
        for sheet, df in sheets.items():
            tid = _store(f"{name} · {sheet}", df)
            profiles.append(profile(tid))
    else:
        sep = "\t" if fmt == "tsv" else ","
        df = pd.read_csv(io.BytesIO(data), sep=sep)
        tid = _store(name, df)
        profiles.append(profile(tid))
    return profiles


def ingest_text(name: str, text: str, fmt: str = "csv") -> list[str]:
    return ingest(name, text.encode("utf-8"), fmt)


def list_datasets() -> str:
    if not _TABLES:
        return "No hay datasets cargados. Adjunta un CSV/XLSX o usa load_google_sheet."
    lines = ["Datasets cargados:"]
    for tid, e in _TABLES.items():
        df = e["df"]
        lines.append(f"- {tid}: {e['name']} ({len(df)} filas × {len(df.columns)} columnas)")
    return "\n".join(lines)


def profile(table_id: str) -> str:
    """Esquema + tipos + nulos + muestra de filas + describe numérico."""
    try:
        df = get_df(table_id)
    except KeyError:
        return f"Dataset '{table_id}' no encontrado (puede haber expirado; re-adjunta el archivo)."
    e = _TABLES[table_id]
    cols = []
    for c in df.columns:
        nulls = int(df[c].isna().sum())
        sample = df[c].dropna().head(3).tolist()
        cols.append(f"  - {c} ({df[c].dtype}) · nulos={nulls} · ej: {sample}")
    parts = [
        f"Dataset {table_id} — {e['name']}",
        f"Filas: {len(df)} · Columnas: {len(df.columns)}",
        "Columnas:",
        "\n".join(cols),
        "",
        "Primeras filas:",
        df.head(5).to_string(max_cols=30),
    ]
    num = df.select_dtypes("number")
    if not num.empty:
        parts += ["", "Resumen numérico:", num.describe().to_string()]
    return "\n".join(parts)


def _truncate(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[:limit] + "\n… (truncado)"


def analyze(table_id: str, operation: str, params: dict[str, Any]) -> str:
    """Operaciones deterministas con pandas. Devuelve texto/markdown."""
    try:
        df = get_df(table_id)
    except KeyError:
        return f"Dataset '{table_id}' no encontrado (re-adjunta el archivo)."

    op = operation
    try:
        if op == "head":
            n = int(params.get("n", 10))
            return _truncate(df.head(n).to_string(max_cols=30))

        if op == "describe":
            return _truncate(df.describe(include="all").to_string())

        if op == "value_counts":
            col = params["column"]
            n = int(params.get("n", 20))
            return _truncate(df[col].value_counts(dropna=False).head(n).to_string())

        if op == "groupby":
            by = params["by"]
            by = [by] if isinstance(by, str) else list(by)
            metric = params.get("column")
            agg = params.get("agg", "mean")
            if metric:
                res = df.groupby(by)[metric].agg(agg)
            else:
                res = df.groupby(by).size().rename("count")
            res = res.sort_values(ascending=False) if hasattr(res, "sort_values") else res
            return _truncate(res.head(50).to_string())

        if op == "correlation":
            num = df.select_dtypes("number")
            if num.shape[1] < 2:
                return "No hay suficientes columnas numéricas para correlación."
            return _truncate(num.corr().round(3).to_string())

        if op == "filter":
            query = params["query"]  # ej: "edad > 30 and pais == 'PE'"
            res = df.query(query)
            return f"{len(res)} filas coinciden.\n\n" + _truncate(res.head(20).to_string(max_cols=30))

        if op == "sort":
            col = params["column"]
            asc = bool(params.get("ascending", False))
            n = int(params.get("n", 20))
            return _truncate(df.sort_values(col, ascending=asc).head(n).to_string(max_cols=30))

        if op == "sum" or op == "agg":
            col = params["column"]
            func = params.get("agg", "sum")
            return f"{func}({col}) = {df[col].agg(func)}"

        return (
            f"Operación '{op}' no soportada. Usa: head, describe, value_counts, groupby, "
            "correlation, filter, sort, agg."
        )
    except KeyError as exc:
        return f"Columna no encontrada: {exc}. Columnas: {list(df.columns)}"
    except Exception as exc:  # noqa: BLE001
        return f"Error en la operación '{op}': {exc}"
