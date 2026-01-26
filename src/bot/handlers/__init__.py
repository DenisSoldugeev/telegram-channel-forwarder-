from src.bot.handlers.auth import (
    get_auth_handlers,
    get_auth_method_handlers,
    get_qr_auth_handlers,
)
from src.bot.handlers.decorators import require_auth
from src.bot.handlers.destination import get_destination_handlers
from src.bot.handlers.errors import error_handler
from src.bot.handlers.monitoring import get_monitoring_handlers
from src.bot.handlers.sources import get_sources_handlers
from src.bot.handlers.start import get_start_handlers

__all__ = [
    "get_start_handlers",
    "get_auth_handlers",
    "get_auth_method_handlers",
    "get_qr_auth_handlers",
    "get_sources_handlers",
    "get_destination_handlers",
    "get_monitoring_handlers",
    "error_handler",
    "require_auth",
]
