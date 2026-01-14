import structlog
from telegram.ext import Application, ConversationHandler, MessageHandler, filters

from src.app.config import settings
from src.bot.handlers import (
    error_handler,
    get_auth_method_handlers,
    get_destination_handlers,
    get_monitoring_handlers,
    get_qr_auth_handlers,
    get_sources_handlers,
    get_start_handlers,
)
from src.bot.handlers.auth import (
    get_auth_handlers,
    handle_2fa,
    handle_code,
    handle_phone,
    handle_qr_state_text,
)
from src.bot.handlers.destination import handle_destination_input
from src.bot.handlers.sources import (
    handle_source_file,
    handle_source_text,
)
from src.bot.states import (
    ADD_SOURCE_FILE,
    ADD_SOURCE_TEXT,
    AUTH_METHOD_CHOICE,
    AWAITING_2FA,
    AWAITING_CODE,
    AWAITING_PHONE,
    AWAITING_QR,
    DESTINATION_SETUP,
    MAIN_MENU,
    REMOVE_SOURCE,
    SOURCES_MENU,
)
from src.mtproto.client import MTProtoClientManager
from src.mtproto.session_manager import SessionManager
from src.services import (
    AuthService,
    DeliveryService,
    DestinationService,
    ForwarderService,
    SourceService,
)
from src.storage import get_database
from src.storage.repositories import SourceRepository, UserRepository

logger = structlog.get_logger()


class Bot:
    """Main bot application."""

    def __init__(self):
        """Initialize bot."""
        self._app: Application | None = None
        self._db = get_database()
        self._client_manager = MTProtoClientManager()
        self._session_manager: SessionManager | None = None

    async def _post_init(self, application: Application) -> None:
        """Post-init callback for async setup."""
        logger.info("setting_up_bot")

        # Create database tables
        await self._db.create_tables()

        # Initialize session manager
        self._session_manager = SessionManager(self._db)

        # Create services
        delivery_service = DeliveryService(self._db)

        auth_service = AuthService(
            database=self._db,
            session_manager=self._session_manager,
            client_manager=self._client_manager,
        )

        source_service = SourceService(
            database=self._db,
            session_manager=self._session_manager,
            client_manager=self._client_manager,
        )

        destination_service = DestinationService(
            database=self._db,
            session_manager=self._session_manager,
            client_manager=self._client_manager,
        )

        forwarder_service = ForwarderService(
            database=self._db,
            session_manager=self._session_manager,
            client_manager=self._client_manager,
            delivery_service=delivery_service,
            bot=application.bot,
        )

        # Store services in bot_data for handlers
        application.bot_data["auth_service"] = auth_service
        application.bot_data["source_service"] = source_service
        application.bot_data["destination_service"] = destination_service
        application.bot_data["forwarder_service"] = forwarder_service
        application.bot_data["delivery_service"] = delivery_service

        # Auto-start monitoring for all users with sources
        await self._start_all_monitoring(forwarder_service)

        # Set bot commands menu
        await self._set_commands(application)

        logger.info("bot_setup_complete")

    async def _set_commands(self, application: Application) -> None:
        """Set bot commands for menu."""
        from telegram import BotCommand

        commands = [
            BotCommand("start", "Главное меню"),
            BotCommand("channels", "Управление каналами"),
            BotCommand("destination", "Настроить получателя"),
            BotCommand("status", "Текущий статус"),
            BotCommand("cancel", "Отмена действия"),
            BotCommand("help", "Справка"),
        ]

        await application.bot.set_my_commands(commands)
        logger.info("bot_commands_set")

    async def _start_all_monitoring(self, forwarder_service: ForwarderService) -> None:
        """Start monitoring for all users who have sources configured."""
        logger.info("starting_all_user_monitoring")

        async with self._db.session() as session:
            user_repo = UserRepository(session)
            source_repo = SourceRepository(session)

            # Get all users with active sessions
            users = await user_repo.get_all_with_sessions()

            started_count = 0
            for user in users:
                # Check if user has sources
                source_count = await source_repo.count_by_user(user.id)
                if source_count > 0:
                    try:
                        await forwarder_service.start_user_monitoring(user.id)
                        started_count += 1
                        logger.info("user_monitoring_started", user_id=user.id, sources=source_count)
                    except Exception as e:
                        logger.error("user_monitoring_failed", user_id=user.id, error=str(e))

        logger.info("all_user_monitoring_started", count=started_count)

    async def _post_shutdown(self, application: Application) -> None:
        """Post-shutdown callback for cleanup."""
        logger.info("stopping_bot")
        await self._client_manager.close_all()
        await self._db.close()
        logger.info("bot_stopped")

    def _setup(self) -> None:
        """Set up bot application (sync)."""
        # Build application with callbacks
        self._app = (
            Application.builder()
            .token(settings.bot_token.get_secret_value())
            .post_init(self._post_init)
            .post_shutdown(self._post_shutdown)
            .build()
        )

        # Build main conversation handler
        main_conversation = self._build_conversation_handler()
        self._app.add_handler(main_conversation)

        # Add error handler
        self._app.add_error_handler(error_handler)

    def _build_conversation_handler(self) -> ConversationHandler:
        """Build the main conversation handler."""
        # Entry points - start handlers
        entry_points = get_start_handlers()

        # State handlers
        states = {
            MAIN_MENU: [
                *get_start_handlers(),
                *get_auth_handlers(),
                *get_sources_handlers(),
                *get_destination_handlers(),
                *get_monitoring_handlers(),
            ],
            AUTH_METHOD_CHOICE: [
                *get_auth_method_handlers(),
                *get_auth_handlers(),
            ],
            AWAITING_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone),
                *get_auth_handlers(),  # Cancel button support
            ],
            AWAITING_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code),
                *get_auth_handlers(),  # Cancel button support
            ],
            AWAITING_2FA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_2fa),
                *get_auth_handlers(),  # Cancel button support
            ],
            AWAITING_QR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_qr_state_text),
                *get_qr_auth_handlers(),
                *get_auth_handlers(),
            ],
            SOURCES_MENU: [
                *get_sources_handlers(),
                *get_start_handlers(),
            ],
            ADD_SOURCE_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_source_text),
                *get_sources_handlers(),
            ],
            ADD_SOURCE_FILE: [
                MessageHandler(filters.Document.ALL, handle_source_file),
                *get_sources_handlers(),
            ],
            REMOVE_SOURCE: [
                *get_sources_handlers(),
            ],
            DESTINATION_SETUP: [
                MessageHandler(
                    (filters.TEXT & ~filters.COMMAND) | filters.FORWARDED,
                    handle_destination_input,
                ),
                *get_destination_handlers(),
            ],
        }

        # Fallbacks - commands that work from any state
        fallbacks = [
            *get_start_handlers(),
            *get_destination_handlers(),
            *get_monitoring_handlers(),
        ]

        return ConversationHandler(
            entry_points=entry_points,
            states=states,
            fallbacks=fallbacks,
            name="main_conversation",
            persistent=False,
            allow_reentry=True,
        )

    def run(self) -> None:
        """Run the bot (blocking)."""
        self._setup()
        logger.info("starting_bot")
        self._app.run_polling(drop_pending_updates=True)


def create_bot() -> Bot:
    """Create bot instance."""
    return Bot()
