"""Safe domain errors for QQ Mail operations."""


class QQMailError(RuntimeError):
    """Base error for QQ Mail operations."""


class ConfigurationError(QQMailError):
    """Raised when QQ Mail configuration is unavailable or invalid."""


class AuthenticationError(QQMailError):
    """Raised when QQ Mail authentication fails."""


class MailConnectionError(QQMailError):
    """Raised when a QQ Mail connection cannot be established."""


class MailProtocolError(QQMailError):
    """Raised when QQ Mail returns an unexpected protocol response."""


class MessageTooLargeError(QQMailError):
    """Raised when a requested message exceeds the supported size."""
