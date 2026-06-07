from collections.abc import AsyncGenerator

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    # En produccion esto deberia vivir en migraciones, pero este demo usa create_all.
    from . import models  # noqa: F401  (registra los modelos en el metadata)
    from .models import AppProject, Conversation

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migración ligera para bases existentes: añade columnas nuevas si faltan.
        # create_all no altera tablas ya creadas, así que lo hacemos explícito.
        await conn.execute(
            text(
                "ALTER TABLE app_projects "
                "ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'draft'"
            )
        )
        await conn.execute(
            text("ALTER TABLE app_projects ADD COLUMN IF NOT EXISTS slug VARCHAR(64)")
        )
        await conn.execute(
            text(
                "ALTER TABLE app_projects "
                "ADD COLUMN IF NOT EXISTS deploy_state VARCHAR(20) NOT NULL DEFAULT 'idle'"
            )
        )
        await conn.execute(
            text("ALTER TABLE app_projects ADD COLUMN IF NOT EXISTS url VARCHAR(255)")
        )
        await conn.execute(
            text("ALTER TABLE mcp_connections ADD COLUMN IF NOT EXISTS env_json TEXT")
        )
        # Conectores POR USUARIO: cada fila pertenece a un user_sub (Google sub).
        await conn.execute(
            text("ALTER TABLE mcp_connections ADD COLUMN IF NOT EXISTS user_sub VARCHAR(255)")
        )
        await conn.execute(
            text("ALTER TABLE mcp_connections ADD COLUMN IF NOT EXISTS user_email VARCHAR(255)")
        )
        # Backfill del dueño en conexiones legacy (global, user_sub NULL): atribuir al usuario
        # del último evento `mcp.connect` de ese provider (mapeando email→sub vía users).
        # NOT EXISTS: NO atribuir si el usuario ya tiene una fila propia para ese provider
        # (evita violar el unique (user_sub, provider) cuando ya reconectó por-usuario).
        await conn.execute(
            text(
                "UPDATE mcp_connections mc SET user_sub = u.sub, user_email = u.email "
                "FROM ("
                "  SELECT DISTINCT ON (provider) provider, lower(user_email) AS email"
                "  FROM event_logs"
                "  WHERE event_type = 'mcp.connect' AND user_email IS NOT NULL AND user_email <> ''"
                "  ORDER BY provider, created_at DESC"
                ") ev JOIN users u ON u.email = ev.email "
                "WHERE mc.provider = ev.provider AND mc.user_sub IS NULL "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM mcp_connections m2"
                "  WHERE m2.provider = mc.provider AND m2.user_sub = u.sub"
                ")"
            )
        )
        # Limpieza: filas legacy huérfanas (user_sub aún NULL) que ya tienen equivalente
        # por-usuario para el mismo provider → ya no sirven (el matching es por email).
        await conn.execute(
            text(
                "DELETE FROM mcp_connections mc WHERE mc.user_sub IS NULL "
                "AND EXISTS ("
                "  SELECT 1 FROM mcp_connections m2"
                "  WHERE m2.provider = mc.provider AND m2.user_sub IS NOT NULL"
                ")"
            )
        )
        # El unique viejo era por `provider` (global). Ahora es por (user_sub, provider).
        # Según cómo se creó, puede ser un CONSTRAINT y/o un UNIQUE INDEX → soltamos ambos.
        await conn.execute(
            text("ALTER TABLE mcp_connections DROP CONSTRAINT IF EXISTS mcp_connections_provider_key")
        )
        await conn.execute(text("DROP INDEX IF EXISTS ix_mcp_connections_provider"))
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_mcp_connections_provider ON mcp_connections (provider)")
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_mcp_user_provider "
                "ON mcp_connections (user_sub, provider)"
            )
        )
        # Flujo de dashboard eliminado: limpiamos la columna legacy si quedó de versiones previas.
        await conn.execute(
            text("ALTER TABLE app_projects DROP COLUMN IF EXISTS dashboard")
        )
        await conn.execute(
            text("ALTER TABLE app_projects ADD COLUMN IF NOT EXISTS app_spec JSON")
        )
        await conn.execute(
            text("ALTER TABLE app_projects ADD COLUMN IF NOT EXISTS deploy_stage VARCHAR(80)")
        )
        await conn.execute(
            text("ALTER TABLE app_projects ADD COLUMN IF NOT EXISTS shared_emails JSON")
        )
        await conn.execute(
            text("ALTER TABLE app_projects ADD COLUMN IF NOT EXISTS owner_email VARCHAR(255)")
        )
        await conn.execute(
            text("ALTER TABLE app_projects ADD COLUMN IF NOT EXISTS editor_emails JSON")
        )
        # Backfill del dueño en apps legacy (owner_email NULL): toma el correo del PRIMER
        # evento de esa app en la bitácora (app.create / deploy). Idempotente: solo toca NULLs.
        await conn.execute(
            text(
                "UPDATE app_projects ap SET owner_email = sub.email FROM ("
                "  SELECT DISTINCT ON (app_id) app_id, lower(user_email) AS email"
                "  FROM event_logs"
                "  WHERE app_id IS NOT NULL AND user_email IS NOT NULL AND user_email <> ''"
                "  ORDER BY app_id, created_at ASC"
                ") sub "
                "WHERE ap.id = sub.app_id "
                "AND (ap.owner_email IS NULL OR ap.owner_email = '')"
            )
        )
        # La URL de avatar de Google puede superar 512 chars → ampliamos a TEXT.
        await conn.execute(
            text("ALTER TABLE users ALTER COLUMN picture TYPE TEXT")
        )
        await conn.execute(
            text("ALTER TABLE app_projects ADD COLUMN IF NOT EXISTS build_artifacts JSON")
        )
        await conn.execute(
            text("ALTER TABLE app_projects ADD COLUMN IF NOT EXISTS pending_edits JSON")
        )
        await conn.execute(
            text("ALTER TABLE app_projects ADD COLUMN IF NOT EXISTS icon VARCHAR(16)")
        )
        await conn.execute(
            text("ALTER TABLE app_projects ADD COLUMN IF NOT EXISTS color VARCHAR(16)")
        )
        await conn.execute(
            text("ALTER TABLE app_projects ADD COLUMN IF NOT EXISTS db_password TEXT")
        )
        await conn.execute(
            text("ALTER TABLE app_projects ADD COLUMN IF NOT EXISTS deployed_sha VARCHAR(40)")
        )
        # Si el backend reinició durante un deploy, la tarea murió: reseteamos los
        # despliegues huérfanos (no pueden seguir corriendo) para no dejarlos colgados.
        await conn.execute(
            text(
                "UPDATE app_projects SET deploy_state='idle', deploy_stage=NULL "
                "WHERE deploy_state='deploying'"
            )
        )
        # Orden monotónico de mensajes (created_at empata dentro de un mismo commit).
        await conn.execute(
            text(
                "ALTER TABLE messages ADD COLUMN IF NOT EXISTS seq BIGINT "
                "GENERATED BY DEFAULT AS IDENTITY"
            )
        )
        # Conexiones por usuario: user_sub + unicidad (app, user, provider).
        await conn.execute(
            text("ALTER TABLE connections ADD COLUMN IF NOT EXISTS user_sub VARCHAR(255)")
        )
        await conn.execute(
            text(
                "ALTER TABLE connections "
                "DROP CONSTRAINT IF EXISTS uq_connection_app_provider"
            )
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_conn_app_user_provider "
                "ON connections (app_id, user_sub, provider)"
            )
        )

    async with SessionLocal() as session:
        conversations = (await session.execute(select(Conversation))).scalars().all()
        existing_ids = set(
            (await session.execute(select(AppProject.conversation_id))).scalars().all()
        )
        for conversation in conversations:
            if conversation.id in existing_ids:
                continue
            session.add(
                AppProject(
                    conversation_id=conversation.id,
                    title=conversation.title or "Nueva app",
                )
            )
        await session.commit()
