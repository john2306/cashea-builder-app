from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AppStatus = Literal["draft", "testing", "production"]


class MessageOut(BaseModel):
    id: str
    role: str
    content: Any
    created_at: datetime

    class Config:
        from_attributes = True


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: datetime

    class Config:
        from_attributes = True


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class AppFlow(BaseModel):
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class AppIntegrations(BaseModel):
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    apis: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)


class AppProjectOut(BaseModel):
    id: str
    conversation_id: str
    title: str
    icon: str | None = None
    color: str | None = None
    status: AppStatus = "draft"
    deploy_state: str = "idle"
    deploy_stage: str | None = None
    slug: str | None = None
    url: str | None = None
    owner_email: str | None = None
    # Rol del usuario que pide la lista sobre esta app: admin|owner|editor|viewer.
    # La UI lo usa para mostrar/ocultar acciones (eliminar = solo owner/admin; editar = +editor).
    my_role: str = "viewer"
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AppProjectDetail(AppProjectOut):
    flow: AppFlow = Field(default_factory=AppFlow)
    integrations: AppIntegrations = Field(default_factory=AppIntegrations)


class AppListPage(BaseModel):
    """Página de apps (paginación server-side). `total` = total que coincide con el filtro."""
    items: list[AppProjectOut]
    total: int
    limit: int
    offset: int


class AppProjectUpdate(BaseModel):
    title: str | None = None
    icon: str | None = None
    color: str | None = None
    status: AppStatus | None = None
    flow: AppFlow | None = None
    integrations: AppIntegrations | None = None


class AppProjectCreate(BaseModel):
    title: str = "Nueva app"


class ConnectorInfo(BaseModel):
    id: str
    label: str
    configured: bool


class ConnectionOut(BaseModel):
    provider: str
    account: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True
