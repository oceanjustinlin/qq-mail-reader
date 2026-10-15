"""Small, read-only IMAP client for QQ Mail."""

from __future__ import annotations

import imaplib
import re
import ssl
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, timedelta
from email import policy
from email.parser import BytesParser
from typing import Any

from .credentials import Credentials
from .errors import (
    AuthenticationError,
    MailConnectionError,
    MailProtocolError,
    MessageTooLargeError,
)
from .message_parser import parse_message

HOST = "imap.qq.com"
PORT = 993
MAX_MESSAGE_BYTES = 10 * 1024 * 1024
MAX_SEARCH_CANDIDATES = 500

_MAX_RESULT_LIMIT = 100
_MAX_SINCE_DAYS = 30
_FETCH_HEADERS = (
    "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID CONTENT-TYPE)] FLAGS RFC822.SIZE)"
)
_UID_PATTERN = re.compile(r"[0-9]+")
_FETCH_UID_PATTERN = re.compile(rb"(?:^|[\s(])UID\s+([0-9]+)(?=\s|\))", re.IGNORECASE)
_FETCH_RECORD_START_PATTERN = re.compile(rb"^\s*[0-9]+\s+\(")
_SIZE_PATTERN = re.compile(rb"(?:^|\s)RFC822\.SIZE\s+([0-9]+)(?=\s|\))", re.IGNORECASE)
_FLAGS_PATTERN = re.compile(rb"(?:^|\s)FLAGS\s+\(([^)]*)\)", re.IGNORECASE)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class ImapClient:
    """Perform only read-only operations against the QQ Mail INBOX."""

    def __init__(
        self,
        credentials: Credentials,
        connector: Callable[..., Any] = imaplib.IMAP4_SSL,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.credentials = credentials
        self.connector = connector
        self.today = today

    @contextmanager
    def _session(self) -> Iterator[Any]:
        connection: Any | None = None
        selected = False
        try:
            try:
                connection = self.connector(
                    host=HOST,
                    port=PORT,
                    ssl_context=ssl.create_default_context(),
                    timeout=20,
                )
            except imaplib.IMAP4.error:
                raise MailProtocolError("QQ Mail IMAP protocol operation failed.") from None
            except OSError:
                raise MailConnectionError("Unable to connect to QQ Mail.") from None

            try:
                login_response = connection.login(
                    self.credentials.email,
                    self.credentials.auth_code,
                )
                login_status, _ = _response_parts(login_response)
                if not _is_ok(login_status):
                    raise AuthenticationError("QQ Mail authentication failed.")
            except imaplib.IMAP4.abort:
                raise MailConnectionError("Unable to connect to QQ Mail.") from None
            except imaplib.IMAP4.error:
                raise AuthenticationError("QQ Mail authentication failed.") from None
            except OSError:
                raise MailConnectionError("Unable to connect to QQ Mail.") from None

            try:
                select_response = connection.select("INBOX", readonly=True)
                select_status, _ = _response_parts(select_response)
            except imaplib.IMAP4.error:
                raise MailProtocolError("QQ Mail IMAP protocol operation failed.") from None
            except OSError:
                raise MailConnectionError("Unable to connect to QQ Mail.") from None
            if not _is_ok(select_status):
                raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
            selected = True

            try:
                yield connection
            except (
                AuthenticationError,
                MailConnectionError,
                MailProtocolError,
                MessageTooLargeError,
            ):
                raise
            except imaplib.IMAP4.error:
                raise MailProtocolError("QQ Mail IMAP protocol operation failed.") from None
            except OSError:
                raise MailConnectionError("Unable to connect to QQ Mail.") from None
        finally:
            if connection is not None:
                if selected and hasattr(connection, "unselect"):
                    try:
                        connection.unselect()
                    except Exception:  # noqa: S110 - teardown is deliberately best effort
                        pass
                try:
                    connection.logout()
                except Exception:  # noqa: S110 - teardown is deliberately best effort
                    pass

    def check_connection(self) -> None:
        """Open a read-only session and verify it remains responsive."""
        with self._session() as connection:
            status, _ = _response_parts(connection.noop())
            if not _is_ok(status):
                raise MailProtocolError("QQ Mail IMAP protocol operation failed.")

    def list_recent(self, limit: int) -> list[dict[str, object]]:
        """Return summary metadata for the newest messages in INBOX."""
        _validate_limit(limit)
        with self._session() as connection:
            uids = self._search_uids(connection, ["ALL"])
            selected_uids = reversed(uids[-limit:])
            return [self._fetch_summary(connection, uid) for uid in selected_uids]

    def search(
        self,
        since_days: int,
        sender: str | None,
        subject: str | None,
        unread: bool | None,
        limit: int,
    ) -> list[dict[str, object]]:
        """Search safe server criteria, then filter decoded headers locally."""
        _validate_limit(limit)
        _validate_search_inputs(since_days, sender, subject, unread)

        first_day = self.today() - timedelta(days=since_days - 1)
        since = f"{first_day.day:02d}-{_MONTHS[first_day.month - 1]}-{first_day.year:04d}"
        criteria = ["SINCE", since]
        if unread is True:
            criteria = [*criteria, "UNSEEN"]
        elif unread is False:
            criteria = [*criteria, "SEEN"]

        sender_filter = sender.casefold() if sender else None
        subject_filter = subject.casefold() if subject else None
        matches: list[dict[str, object]] = []
        with self._session() as connection:
            uids = self._search_uids(connection, criteria)[-MAX_SEARCH_CANDIDATES:]
            for uid in reversed(uids):
                summary = self._fetch_summary(connection, uid)
                if (
                    sender_filter is not None
                    and sender_filter not in str(summary["sender"]).casefold()
                ) or (
                    subject_filter is not None
                    and subject_filter not in str(summary["subject"]).casefold()
                ):
                    continue
                matches += [summary]
                if len(matches) == limit:
                    break
        return matches

    def read(self, uid: str) -> dict[str, object]:
        """Read one message without changing its read state."""
        if not isinstance(uid, str) or _UID_PATTERN.fullmatch(uid) is None:
            raise MailProtocolError("Message UID must contain ASCII digits only.")

        with self._session() as connection:
            size_status, size_data = _response_parts(connection.uid("FETCH", uid, "(RFC822.SIZE)"))
            if not _is_ok(size_status):
                raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
            size_meta = _fetch_metadata(size_data, uid)
            size = _message_size(size_meta)
            if size > MAX_MESSAGE_BYTES:
                raise MessageTooLargeError("Message is too large to read safely.")

            body_status, body_data = _response_parts(connection.uid("FETCH", uid, "(BODY.PEEK[])"))
            if not _is_ok(body_status):
                raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
            _, raw_message = _fetch_tuple(body_data, uid)
            if len(raw_message) > MAX_MESSAGE_BYTES:
                raise MessageTooLargeError("Message is too large to read safely.")

        try:
            result: dict[str, object] = asdict(parse_message(raw_message))
        except (TypeError, ValueError):
            raise MailProtocolError("QQ Mail message could not be parsed safely.") from None
        result["uid"] = uid
        result["size"] = size
        return result

    def _search_uids(self, connection: Any, criteria: list[str]) -> list[str]:
        status, data = _response_parts(connection.uid("SEARCH", None, *criteria))
        if not _is_ok(status) or not isinstance(data, Sequence) or isinstance(data, (bytes, str)):
            raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
        if len(data) != 1 or not isinstance(data[0], bytes):
            raise MailProtocolError("QQ Mail IMAP protocol operation failed.")

        try:
            uids = [token.decode("ascii") for token in data[0].split()]
        except UnicodeDecodeError:
            raise MailProtocolError("QQ Mail IMAP protocol operation failed.") from None
        if any(_UID_PATTERN.fullmatch(uid) is None for uid in uids):
            raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
        try:
            return sorted(uids, key=int)
        except ValueError:
            raise MailProtocolError("QQ Mail IMAP protocol operation failed.") from None

    def _fetch_summary(self, connection: Any, uid: str) -> dict[str, object]:
        status, data = _response_parts(connection.uid("FETCH", uid, _FETCH_HEADERS))
        if not _is_ok(status):
            raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
        metadata, raw_headers = _fetch_tuple(data, uid)
        size = _message_size(metadata)
        flags_match = _FLAGS_PATTERN.search(metadata)
        if flags_match is None:
            raise MailProtocolError("QQ Mail IMAP protocol operation failed.")

        try:
            parsed = parse_message(raw_headers)
        except (TypeError, ValueError):
            raise MailProtocolError("QQ Mail message headers could not be parsed safely.") from None
        flags = flags_match.group(1).split()
        return {
            "uid": uid,
            "sender": parsed.sender,
            "subject": parsed.subject,
            "date": parsed.date,
            "message_id": parsed.message_id,
            "unread": not any(flag.lower() == b"\\seen" for flag in flags),
            # A header-only fetch cannot inspect parts. Mixed is the useful conservative hint;
            # alternative merely represents equivalent body formats and is not an attachment.
            "has_attachments": _top_level_is_mixed(raw_headers),
            "size": size,
        }


def _response_parts(response: object) -> tuple[object, object]:
    if not isinstance(response, tuple) or len(response) != 2:
        raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
    return response


def _is_ok(status: object) -> bool:
    if isinstance(status, bytes):
        return status.upper() == b"OK"
    return isinstance(status, str) and status.upper() == "OK"


def _fetch_tuple(data: object, expected_uid: str) -> tuple[bytes, bytes]:
    candidates = [
        record
        for record in _fetch_records(data)
        if _record_uid(record[0]) == expected_uid and record[1] is not None
    ]
    if len(candidates) != 1 or candidates[0][1] is None:
        raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
    return candidates[0][0], candidates[0][1]


def _fetch_metadata(data: object, expected_uid: str) -> bytes:
    candidates = [
        metadata
        for metadata, _ in _fetch_records(data)
        if _record_uid(metadata) == expected_uid and _SIZE_PATTERN.search(metadata) is not None
    ]
    if len(candidates) != 1:
        raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
    return candidates[0]


def _fetch_records(data: object) -> list[tuple[bytes, bytes | None]]:
    if not isinstance(data, Sequence) or isinstance(data, (bytes, str)):
        raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
    records: list[tuple[bytes, bytes | None]] = []
    current_chunks: list[bytes] = []
    current_payload: bytes | None = None
    for item in data:
        if isinstance(item, tuple) and len(item) == 2:
            prefix, literal = item
            if not isinstance(prefix, bytes) or not isinstance(literal, bytes):
                raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
            if _FETCH_RECORD_START_PATTERN.match(prefix) is None:
                raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
            if current_chunks:
                records += [(b" ".join(current_chunks), current_payload)]
            current_chunks = [prefix]
            current_payload = literal
        elif isinstance(item, bytes):
            if _FETCH_RECORD_START_PATTERN.match(item) is not None:
                if current_chunks:
                    records += [(b" ".join(current_chunks), current_payload)]
                current_chunks = [item]
                current_payload = None
            elif current_chunks:
                current_chunks += [item]
            else:
                raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
        else:
            raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
    if current_chunks:
        records += [(b" ".join(current_chunks), current_payload)]
    if not records:
        raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
    return records


def _record_uid(metadata: bytes) -> str | None:
    uid_matches = _FETCH_UID_PATTERN.findall(metadata)
    if len(uid_matches) > 1:
        raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
    return uid_matches[0].decode("ascii") if uid_matches else None


def _message_size(metadata: bytes) -> int:
    match = _SIZE_PATTERN.search(metadata)
    if match is None:
        raise MailProtocolError("QQ Mail IMAP protocol operation failed.")
    try:
        return int(match.group(1))
    except (TypeError, ValueError, OverflowError):
        raise MailProtocolError("QQ Mail IMAP protocol operation failed.") from None


def _top_level_is_mixed(raw_headers: bytes) -> bool:
    try:
        message = BytesParser(policy=policy.compat32).parsebytes(raw_headers, headersonly=True)
        return message.get_content_type().lower() == "multipart/mixed"
    except Exception:
        return False


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_RESULT_LIMIT:
        raise MailProtocolError("limit must be between 1 and 100.")


def _validate_search_inputs(
    since_days: int,
    sender: str | None,
    subject: str | None,
    unread: bool | None,
) -> None:
    if (
        isinstance(since_days, bool)
        or not isinstance(since_days, int)
        or not 1 <= since_days <= _MAX_SINCE_DAYS
    ):
        raise MailProtocolError("since_days must be between 1 and 30.")
    if sender is not None and not isinstance(sender, str):
        raise MailProtocolError("sender must be text or None.")
    if subject is not None and not isinstance(subject, str):
        raise MailProtocolError("subject must be text or None.")
    if unread is not None and not isinstance(unread, bool):
        raise MailProtocolError("unread must be true, false, or None.")
