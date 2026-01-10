import asyncio
import logging
import sys

# Fix for Pyrogram on Python 3.10+
# Must be done BEFORE importing pyrogram
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import structlog

from src.app.config import settings
from src.bot import create_bot


def setup_logging() -> None:
    """Configure structured logging."""
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.set_exc_info,
    ]

    if settings.log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=getattr(logging, settings.log_level),
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


def main() -> None:
    """Main entry point."""
    setup_logging()

    logger = structlog.get_logger()
    logger.info("starting_application", log_level=settings.log_level)

    bot = create_bot()

    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("received_keyboard_interrupt")
    except Exception as e:
        logger.exception("fatal_error", error=str(e))
        raise


if __name__ == "__main__":
    main()
