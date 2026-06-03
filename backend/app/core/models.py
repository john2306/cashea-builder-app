import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(255), default="Nueva conversación")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.seq",
    )
    app_project: Mapped["AppProject | None"] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        uselist=False,
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Orden monotónico de inserción (el id es UUID y created_at empata dentro de un commit).
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), index=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # "user" | "assistant"
    # Guardamos el contenido tal cual lo consume la API de Anthropic:
    # un string (turnos de usuario) o una lista de bloques (turnos del asistente).
    content: Mapped[object] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


def _empty_flow() -> dict[str, list[dict[str, Any]]]:
    return {"nodes": [], "edges": []}


def _empty_integrations() -> dict[str, list[dict[str, Any]]]:
    return {"mcp_servers": [], "apis": [], "tools": []}


class AppProject(Base):
    __tablename__ = "app_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), default="Nueva app")
    # Personalización visual: ícono (emoji) y color de fondo de la tarjeta.
    icon: Mapped[str | None] = mapped_column(String(16), nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Estado del ciclo de vida de la app: draft | testing | production
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    # Despliegue: slug -> subdominio, estado y URL pública.
    slug: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Etapa actual del pipeline del equipo de devs (para progreso en la UI).
    deploy_stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    deploy_state: Mapped[str] = mapped_column(
        String(20), default="idle", server_default="idle"
    )  # idle | deploying | deployed | error
    url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Config de dashboard de Google Sheet (spreadsheet_id, range, kpis, charts, …).
    dashboard: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Spec de app full-stack compilada por el arquitecto (equipo de devs).
    app_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Enterprise: lista de correos con acceso a la app desplegada (allowlist dinámica).
    shared_emails: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Código generado (cache): {spec_hash, main_py, static_files, backend_reqs}.
    # Si la spec no cambió, "Actualizar" reusa esto y saltea la generación LLM.
    build_artifacts: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Cambios libres pendientes (texto) que el usuario pidió por chat sobre una app YA
    # construida (p. ej. "cambiá el color a azul"). En el próximo deploy se aplican como
    # EDICIÓN INCREMENTAL sobre el código actual (diff mínimo) y se limpian.
    pending_edits: Mapped[list | None] = mapped_column(JSON, nullable=True)
    flow: Mapped[dict[str, Any]] = mapped_column(JSON, default=_empty_flow)
    integrations: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=_empty_integrations
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    conversation: Mapped[Conversation] = relationship(back_populates="app_project")


class Connection(Base):
    """Credenciales OAuth2 de un proveedor externo, por app."""

    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("app_id", "user_sub", "provider", name="uq_conn_app_user_provider"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    app_id: Mapped[str] = mapped_column(
        ForeignKey("app_projects.id", ondelete="CASCADE"), index=True
    )
    # Identidad del usuario final (Google `sub`) dueño de esta conexión.
    user_sub: Mapped[str] = mapped_column(String(255), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    account: Mapped[str | None] = mapped_column(String(255), nullable=True)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class McpConnection(Base):
    """Conexión OAuth a un servidor MCP del agente del Builder (global, por proveedor)."""

    __tablename__ = "mcp_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # OAuth self-hosted: env del server stdio (cifrado) resuelto del token via env_map.
    env_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EventLog(Base):
    """Bitácora de auditoría: registra eventos de la plataforma (deploy, MCP, apps, login)
    para la sección Logs (rol admin). Filtrable por tipo, estado, usuario y rango de fechas."""

    __tablename__ = "event_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Tipo punteado: app.create | app.update | app.delete | app.define | app.edit |
    # deploy.start | deploy.done | deploy.error | deploy.rollback |
    # mcp.connect | mcp.disconnect | auth.login
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    status: Mapped[str] = mapped_column(String(16), default="ok", index=True)  # ok|error|info
    user_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    app_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
