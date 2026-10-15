"""Validated, read-only service boundary for QQ Mail operations."""

from __future__ import annotations

import re
from collections.abc import Callable

from .credentials import Credentials, CredentialStore, mask_email
from .errors import MailProtocolError
from .imap_client import ImapClient

_UID_PATTERN = re.compile(r"[0-9]+")


class QQMailService:
    """Validate local inputs before loading credentials or opening IMAP."""

    def __init__(
        self,
        store: CredentialStore | None = None,
        client_factory: Callable[[Credentials], ImapClient] = ImapClient,
    ) -> None:
        self.store = store if store is not None else CredentialStore()
        self.client_factory = client_factory

    def status(self, check_connection: bool = False) -> dict[str, object]:
        """Report local configuration, optionally checking the read-only connection."""
        if not isinstance(check_connection, bool):
            raise MailProtocolError("check_connection must be true or false.")
        email = self.store.configured_email()
        result: dict[str, object] = {
            "configured": email is not None,
            "email": mask_email(email) if email is not None else None,
            "server": "imap.qq.com:993",
            "connection_checked": check_connection,
            "connected": None,
        }
        if check_connection:
            self.client_factory(self.store.load()).check_connection()
            result["connected"] = True
        return result

    def list_recent(self, limit: int = 20) -> list[dict[str, object]]:
        """List recent message summaries after validating the result limit."""
        _validate_limit(limit)
        return self.client_factory(self.store.load()).list_recent(limit)

    def search(
        self,
        since_days: int = 7,
        sender: str | None = None,
        subject: str | None = None,
        unread: bool | None = None,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        """Search messages after validating every caller-controlled value."""
        _validate_limit(limit)
        if (
            isinstance(since_days, bool)
            or not isinstance(since_days, int)
            or not 1 <= since_days <= 30
        ):
            raise MailProtocolError("since_days must be between 1 and 30.")
        _validate_text("sender", sender)
        _validate_text("subject", subject)
        if unread is not None and not isinstance(unread, bool):
            raise MailProtocolError("unread must be true, false, or None.")
        return self.client_factory(self.store.load()).search(
            since_days, sender, subject, unread, limit
        )

    def read(self, uid: str) -> dict[str, object]:
        """Read one message by an ASCII digit UID."""
        if not isinstance(uid, str) or _UID_PATTERN.fullmatch(uid) is None:
            raise MailProtocolError("Message UID must contain ASCII digits only.")
        return self.client_factory(self.store.load()).read(uid)


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise MailProtocolError("limit must be between 1 and 100.")


def _validate_text(name: str, value: str | None) -> None:
    if value is not None and (not isinstance(value, str) or len(value) > 200):
        raise MailProtocolError(f"{name} must be text or None, up to 200 characters.")
