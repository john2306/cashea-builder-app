"""Versionado local (git) del código generado por app — trazabilidad + rollback, sin red.

Cada app = un repo git en un volumen compartido (APPS_DATA_DIR/<app_id>). Cada deploy OK
hace un commit con el código actual (main.py, static/*, requirements.txt) + la spec. El
código es un artefacto derivado de la spec (la fuente de verdad), por eso versionamos ambos.
Usa el binario `git` por subprocess (simple y da `git show sha:path` para el rollback).
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone

APPS_DATA_DIR = os.environ.get("APPS_DATA_DIR", "/data/apps")


def _repo_path(app_id: str) -> str:
    return os.path.join(APPS_DATA_DIR, app_id)


def _git(repo: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, check=check,
    )


def ensure_repo(app_id: str) -> str:
    repo = _repo_path(app_id)
    os.makedirs(repo, exist_ok=True)
    if not os.path.isdir(os.path.join(repo, ".git")):
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "builder@cashea.app")
        _git(repo, "config", "user.name", "Cashea Builder")
    return repo


def _write_files(repo: str, artifacts: dict, spec: dict | None) -> None:
    files: dict[str, str] = {
        "main.py": artifacts.get("main_py", "") or "",
        "requirements.txt": artifacts.get("backend_reqs", "") or "",
        "spec.json": json.dumps(spec or {}, indent=2, ensure_ascii=False),
    }
    for k, v in (artifacts.get("static_files") or {}).items():
        files[k] = v or ""  # p. ej. "static/app.js"
    for rel, content in files.items():
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def commit_version(app_id: str, artifacts: dict, spec: dict | None, message: str) -> str | None:
    """Escribe el código actual en el repo y commitea. Devuelve el sha corto, o None si no
    hubo cambios o falló (best-effort: nunca rompe el deploy)."""
    try:
        repo = ensure_repo(app_id)
        _write_files(repo, artifacts, spec)
        _git(repo, "add", "-A")
        r = _git(repo, "commit", "-m", message or "deploy", check=False)
        if r.returncode != 0:
            # típicamente "nothing to commit" -> no es error
            return None
        return _git(repo, "rev-parse", "--short=12", "HEAD").stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def list_versions(app_id: str, limit: int = 50) -> list[dict]:
    """Historial de commits (más reciente primero): [{sha, date, message}]."""
    repo = _repo_path(app_id)
    if not os.path.isdir(os.path.join(repo, ".git")):
        return []
    r = _git(repo, "log", f"-{limit}", "--pretty=format:%H%x1f%ct%x1f%s", check=False)
    out: list[dict] = []
    for line in r.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            sha, ts, subj = parts
            out.append({
                "sha": sha[:12],
                "date": datetime.fromtimestamp(int(ts), timezone.utc).isoformat(),
                "message": subj,
            })
    return out


def read_version(app_id: str, sha: str) -> dict | None:
    """Reconstruye los artifacts de un commit: {main_py, backend_reqs, static_files, spec}."""
    repo = _repo_path(app_id)
    if not os.path.isdir(os.path.join(repo, ".git")):
        return None

    def show(path: str) -> str | None:
        r = _git(repo, "show", f"{sha}:{path}", check=False)
        return r.stdout if r.returncode == 0 else None

    main_py = show("main.py")
    if main_py is None:
        return None
    static: dict[str, str] = {}
    for p in ("static/app.js", "static/app.css"):
        c = show(p)
        if c is not None:
            static[p] = c
    spec_txt = show("spec.json")
    return {
        "main_py": main_py,
        "backend_reqs": show("requirements.txt") or "",
        "static_files": static,
        "spec": json.loads(spec_txt) if spec_txt else None,
    }
