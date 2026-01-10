class BotError(Exception):
    """Base exception for all bot errors."""

    def __init__(self, message: str, user_message: str | None = None):
        super().__init__(message)
        self.user_message = user_message or message


class AuthError(BotError):
    """Authentication related errors."""

    pass


class SessionError(BotError):
    """Session management errors."""

    pass


class SourceError(BotError):
    """Source channel management errors."""

    pass


class DestinationError(BotError):
    """Destination channel errors."""

    pass


class ForwardError(BotError):
    """Message forwarding errors."""

    pass


class ValidationError(BotError):
    """Input validation errors."""

    pass


class RateLimitError(BotError):
    """Rate limiting errors."""

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


class MaxRetriesExceeded(BotError):
    """Maximum retry attempts exceeded."""

    pass


class CircuitBreakerOpen(BotError):
    """Circuit breaker is open, service temporarily unavailable."""

    pass
