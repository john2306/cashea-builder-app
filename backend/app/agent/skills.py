"""Agent Skills (playbooks) del Builder — progressive disclosure, gestionables por admins.

Fuente de verdad = tabla `agent_skills` (CRUD desde la sección Manager). Las built-in se SIEMBRAN
desde los `.md` de `skills/` la primera vez. El agente ve solo las `enabled`.

El ÍNDICE liviano (name + description + when_to_use) va en el system prompt; el CUERPO completo se
carga on-demand con la tool `use_skill`. Para no pegarle a la DB en cada turno, mantenemos un
SNAPSHOT en memoria de las skills habilitadas; se refresca al inicio y tras cada edición admin.
Hasta el primer refresh (import), el snapshot se llena desde los archivos (defaults).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).with_name("skills")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    when_to_use: str
    body: str


def _parse(text: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    body = text
    if text.lstrip().startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            _, fm, body = parts
            for line in fm.strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
    return meta, body.strip()


def files_skills() -> dict[str, Skill]:
    """Skills definidas en archivos `.md` (defaults / semilla)."""
    out: dict[str, Skill] = {}
    if not SKILLS_DIR.exists():
        return out
    for f in sorted(SKILLS_DIR.glob("*.md")):
        meta, body = _parse(f.read_text(encoding="utf-8"))
        name = meta.get("name") or f.stem
        out[name] = Skill(name, meta.get("description", ""), meta.get("when_to_use", ""), body)
    return out


# Snapshot en memoria de las skills HABILITADAS (name -> Skill). Arranca con los archivos.
_SNAPSHOT: dict[str, Skill] = files_skills()


async def seed_from_files() -> None:
    """Inserta en la DB las skills de archivo que aún no existan (built_in). Idempotente."""
    from sqlalchemy import select

    from ..core.db import SessionLocal
    from ..core.models import AgentSkill

    files = files_skills()
    if not files:
        return
    async with SessionLocal() as s:
        existing = set((await s.execute(select(AgentSkill.name))).scalars().all())
        for name, sk in files.items():
            if name not in existing:
                s.add(AgentSkill(
                    name=name, description=sk.description, when_to_use=sk.when_to_use,
                    body=sk.body, enabled=True, built_in=True,
                ))
        await s.commit()


async def refresh() -> None:
    """Recarga el snapshot en memoria desde la DB (solo skills habilitadas)."""
    global _SNAPSHOT
    from sqlalchemy import select

    from ..core.db import SessionLocal
    from ..core.models import AgentSkill

    try:
        async with SessionLocal() as s:
            rows = (
                await s.execute(select(AgentSkill).where(AgentSkill.enabled.is_(True)))
            ).scalars().all()
        _SNAPSHOT = {
            r.name: Skill(r.name, r.description or "", r.when_to_use or "", r.body or "")
            for r in rows
        }
    except Exception:  # noqa: BLE001 — si la DB no está lista, conserva el snapshot actual
        pass


# --- API sincrónica para el agente (lee el snapshot) ----------------------------
def skill_names() -> list[str]:
    return list(_SNAPSHOT)


def skills_index() -> str:
    if not _SNAPSHOT:
        return ""
    lines = [
        "SKILLS (playbooks reutilizables) — si el pedido del usuario encaja con una skill, llamá",
        "la tool `use_skill` con su `name` ANTES de `define_app` para cargar el playbook y seguilo.",
        "Disponibles:",
    ]
    for s in _SNAPSHOT.values():
        when = f" — usar cuando: {s.when_to_use}" if s.when_to_use else ""
        lines.append(f"  - {s.name}: {s.description}{when}")
    return "\n".join(lines)


def get_skill_body(name: str) -> str | None:
    s = _SNAPSHOT.get((name or "").strip())
    return s.body if s else None
