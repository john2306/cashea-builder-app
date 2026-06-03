"""Conectores OAuth (registry) + acceso a Google Sheets (sheets). Re-exporta la API
pública para mantener `from .connectors import PROVIDERS, ...` estable."""
from .registry import (  # noqa: F401
    PROVIDERS,
    build_authorize_url,
    detect_providers,
    is_configured,
)
