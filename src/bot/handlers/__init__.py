from src.bot.handlers.start import get_start_handlers
from src.bot.handlers.auth import get_auth_handlers
from src.bot.handlers.sources import get_sources_handlers
from src.bot.handlers.destination import get_destination_handlers
from src.bot.handlers.monitoring import get_monitoring_handlers
from src.bot.handlers.errors import error_handler

__all__ = [
    "get_start_handlers",
    "get_auth_handlers",
    "get_sources_handlers",
    "get_destination_handlers",
    "get_monitoring_handlers",
    "error_handler",
]
