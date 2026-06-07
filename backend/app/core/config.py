from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Lee variables desde el entorno (Docker Compose las inyecta) o desde un .env local.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Anthropic ---
    anthropic_api_key: str
    anthropic_model: str = "claude-opus-4-7"
    anthropic_max_tokens: int = 16000
    anthropic_effort: str = "high"  # low | medium | high | xhigh | max

    # --- LLM proxy para apps desplegadas (claves NO se exponen a las apps) ---
    openai_api_key: str = ""
    gemini_api_key: str = ""
    # Tope simple anti-abuso: máximo de llamadas LLM por app por día (0 = sin límite).
    llm_daily_call_cap: int = 500

    # --- Infraestructura ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/automation"
    # Postgres SEPARADO para las bases de datos POR APP (schema + rol por app). URL del rol
    # ADMIN (provisiona/borra schemas y roles); las apps NUNCA la ven. DSN asyncpg puro.
    apps_database_admin_url: str = (
        "postgresql://appsadmin:apps-insecure-admin-change-me@apps-postgres:5432/appsdata"
    )
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"

    # CSV de orígenes permitidos para CORS
    cors_origins: str = "http://localhost:5180,http://127.0.0.1:5180"

    # Gateway de login: URL pública del builder y secreto de firma de sesión (JWT).
    # SESSION_SECRET debe ser estable y se inyecta en cada app desplegada.
    public_base_url: str = "http://localhost:8000"
    session_secret: str = "dev-insecure-session-secret-change-me"
    # Clave para cifrar tokens OAuth en reposo. Si vacía, se deriva de session_secret.
    token_encryption_key: str = ""

    # CSV de correos con rol ADMIN (ven la sección Logs). Si está vacío, cualquier usuario
    # autenticado es admin (conveniencia para dev/single-user).
    admin_emails: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]

    def is_admin(self, email: str | None) -> bool:
        admins = self.admin_email_list
        if not admins:
            return True  # sin lista configurada -> todo usuario autenticado es admin (dev)
        return bool(email) and email.lower() in admins


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
