from __future__ import annotations

import imaplib
import ssl
from datetime import date

import pytest

from qq_mail_reader.credentials import Credentials
from qq_mail_reader.errors import (
    AuthenticationError,
    MailConnectionError,
    MailProtocolError,
    MessageTooLargeError,
)
from qq_mail_reader.imap_client import (
    HOST,
    MAX_MESSAGE_BYTES,
    MAX_SEARCH_CANDIDATES,
    PORT,
    ImapClient,
)


class FakeImap:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.search_response: tuple[str, list[bytes] | None] = ("OK", [b""])
        self.fetch_responses: dict[str, tuple[str, list[object] | None]] = {}
        self.login_error: Exception | None = None
        self.select_error: Exception | None = None
        self.noop_response: tuple[str, list[bytes]] = ("OK", [b""])
        self.unselect_error: Exception | None = None
        self.logout_error: Exception | None = None

    def login(self, email: str, auth_code: str) -> tuple[str, list[bytes]]:
        self.calls.append(("login", email, auth_code))
        if self.login_error:
            raise self.login_error
        return "OK", [b"logged in"]

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.calls.append(("select", mailbox, readonly))
        if self.select_error:
            raise self.select_error
        return "OK", [b"1"]

    def noop(self) -> tuple[str, list[bytes]]:
        self.calls.append(("noop",))
        return self.noop_response

    def uid(self, command: str, *args: object) -> tuple[str, list[object] | None]:
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return self.search_response
        uid = str(args[0])
        return self.fetch_responses[uid]

    def unselect(self) -> tuple[str, list[bytes]]:
        self.calls.append(("unselect",))
        if self.unselect_error:
            raise self.unselect_error
        return "OK", [b""]

    def logout(self) -> tuple[str, list[bytes]]:
        self.calls.append(("logout",))
        if self.logout_error:
            raise self.logout_error
        return "BYE", [b""]


class FakeConnector:
    def __init__(self, connection: FakeImap | None = None, error: Exception | None = None) -> None:
        self.connection = connection or FakeImap()
        self.error = error
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> FakeImap:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.connection


def client_for(connection: FakeImap) -> ImapClient:
    return ImapClient(
        Credentials("reader@qq.com", "authorization-secret"),
        connector=FakeConnector(connection),
        today=lambda: date(2026, 9, 22),
    )


def header_fetch(
    uid: str,
    *,
    sender: bytes = b"Alice <alice@example.com>",
    subject: bytes = b"Update",
    flags: bytes = b"",
    size: int = 123,
    content_type: bytes = b"text/plain",
) -> tuple[str, list[object]]:
    raw = b"\r\n".join(
        (
            b"From: " + sender,
            b"Subject: " + subject,
            b"Date: Tue, 22 Sep 2026 10:00:00 +0800",
            b"Message-ID: <" + uid.encode() + b"@example.com>",
            b"Content-Type: " + content_type,
            b"",
            b"",
        )
    )
    metadata = (
        b"1 (UID "
        + uid.encode()
        + b" FLAGS ("
        + flags
        + b") RFC822.SIZE "
        + str(size).encode()
        + b" BODY[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID CONTENT-TYPE)] {"
        + str(len(raw)).encode()
        + b"}"
    )
    return "OK", [(metadata, raw), b")"]


def split_header_fetch(
    uid: str,
    *,
    flags: bytes = b"",
    size: int = 123,
) -> tuple[str, list[object]]:
    raw = (
        b"From: sender@example.com\r\n"
        b"Subject: Split metadata\r\n"
        b"Date: Tue, 22 Sep 2026 10:00:00 +0800\r\n"
        b"Message-ID: <split@example.com>\r\n"
        b"Content-Type: text/plain\r\n\r\n"
    )
    prefix = (
        b"1 (UID "
        + uid.encode()
        + b" BODY[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID CONTENT-TYPE)] {"
        + str(len(raw)).encode()
        + b"}"
    )
    trailer = b" FLAGS (" + flags + b") RFC822.SIZE " + str(size).encode() + b")"
    return "OK", [(prefix, raw), trailer]


def body_fetch(uid: str, raw: bytes) -> tuple[str, list[object]]:
    metadata = b"1 (UID " + uid.encode() + b" BODY[] {" + str(len(raw)).encode() + b"}"
    return "OK", [(metadata, raw), b")"]


def size_fetch(uid: str, size: int) -> tuple[str, list[object]]:
    return "OK", [b"1 (UID " + uid.encode() + b" RFC822.SIZE " + str(size).encode() + b")"]


def test_check_connection_uses_secure_endpoint_readonly_inbox_and_cleanup() -> None:
    connection = FakeImap()
    connector = FakeConnector(connection)
    client = ImapClient(Credentials("reader@qq.com", "secret"), connector=connector)

    client.check_connection()

    assert len(connector.calls) == 1
    options = connector.calls[0]
    assert options["host"] == HOST == "imap.qq.com"
    assert options["port"] == PORT == 993
    assert options["timeout"] == 20
    assert isinstance(options["ssl_context"], ssl.SSLContext)
    assert connection.calls == [
        ("login", "reader@qq.com", "secret"),
        ("select", "INBOX", True),
        ("noop",),
        ("unselect",),
        ("logout",),
    ]


def test_list_recent_uses_uid_commands_and_returns_decoded_newest_first() -> None:
    connection = FakeImap()
    connection.search_response = ("OK", [b"10 11"])
    connection.fetch_responses = {
        "10": header_fetch("10", subject=b"Older", flags=b"\\Seen", size=91),
        "11": header_fetch(
            "11",
            sender=b"=?utf-8?b?5byg5LiJ?= <zhang@example.com>",
            subject=b"=?utf-8?b?5paw6YKu5Lu2?=",
            size=92,
        ),
    }

    result = client_for(connection).list_recent(2)

    assert [item["uid"] for item in result] == ["11", "10"]
    assert result[0] == {
        "uid": "11",
        "sender": "张三 <zhang@example.com>",
        "subject": "新邮件",
        "date": "Tue, 22 Sep 2026 10:00:00 +0800",
        "message_id": "<11@example.com>",
        "unread": True,
        "has_attachments": False,
        "size": 92,
    }
    assert result[1]["unread"] is False
    assert ("uid", "SEARCH", None, "ALL") in connection.calls
    fetch_calls = [call for call in connection.calls if call[:2] == ("uid", "FETCH")]
    assert [call[2] for call in fetch_calls] == ["11", "10"]
    assert all("BODY.PEEK[HEADER.FIELDS" in str(call[3]) for call in fetch_calls)
    assert not any(call[0] in {"store", "close", "expunge"} for call in connection.calls)


def test_list_recent_returns_empty_mailbox_without_fetching() -> None:
    connection = FakeImap()

    assert client_for(connection).list_recent(5) == []
    assert not any(call[:2] == ("uid", "FETCH") for call in connection.calls)


def test_list_recent_sorts_unsorted_search_uids_numerically_before_limiting() -> None:
    connection = FakeImap()
    connection.search_response = ("OK", [b"10 2 7"])
    connection.fetch_responses = {
        "10": header_fetch("10"),
        "7": header_fetch("7"),
    }

    result = client_for(connection).list_recent(2)

    assert [item["uid"] for item in result] == ["10", "7"]
    fetch_uids = [call[2] for call in connection.calls if call[:2] == ("uid", "FETCH")]
    assert fetch_uids == ["10", "7"]


def test_list_recent_parses_flags_and_size_from_fetch_trailer() -> None:
    connection = FakeImap()
    connection.search_response = ("OK", [b"7"])
    connection.fetch_responses["7"] = split_header_fetch("7", flags=b"\\Seen", size=321)

    summary = client_for(connection).list_recent(1)[0]

    assert summary["uid"] == "7"
    assert summary["subject"] == "Split metadata"
    assert summary["unread"] is False
    assert summary["size"] == 321


def test_fetch_literal_rejects_conflicting_uid_across_prefix_and_trailer() -> None:
    connection = FakeImap()
    connection.search_response = ("OK", [b"7"])
    status, data = header_fetch("7")
    assert data is not None
    connection.fetch_responses["7"] = (status, [data[0], b" UID 8)"])

    with pytest.raises(MailProtocolError):
        client_for(connection).list_recent(1)


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [(b'multipart/alternative; boundary="a"', False), (b'multipart/mixed; boundary="m"', True)],
)
def test_summary_attachment_hint_is_best_effort_from_top_level_headers(
    content_type: bytes, expected: bool
) -> None:
    connection = FakeImap()
    connection.search_response = ("OK", [b"1"])
    connection.fetch_responses["1"] = header_fetch("1", content_type=content_type)

    summary = client_for(connection).list_recent(1)[0]

    assert summary["has_attachments"] is expected


@pytest.mark.parametrize(("unread", "criterion"), [(True, "UNSEEN"), (False, "SEEN")])
def test_search_uses_server_date_and_seen_criterion(unread: bool, criterion: str) -> None:
    connection = FakeImap()

    assert client_for(connection).search(3, None, None, unread, 10) == []
    assert ("uid", "SEARCH", None, "SINCE", "20-Sep-2026", criterion) in connection.calls


def test_search_without_local_filters_stops_after_limit() -> None:
    connection = FakeImap()
    connection.search_response = ("OK", [b"1 2 3"])
    connection.fetch_responses["3"] = header_fetch("3")

    result = client_for(connection).search(1, None, None, None, 1)

    assert [item["uid"] for item in result] == ["3"]
    fetch_uids = [call[2] for call in connection.calls if call[:2] == ("uid", "FETCH")]
    assert fetch_uids == ["3"]


def test_search_filters_unicode_casefold_and_stops_after_limit_matches() -> None:
    connection = FakeImap()
    all_uids = [str(value) for value in range(1, MAX_SEARCH_CANDIDATES + 6)]
    connection.search_response = ("OK", [" ".join(all_uids).encode()])
    for uid in all_uids[-MAX_SEARCH_CANDIDATES:]:
        sender = "STRASSE@example.com" if uid in {"503", "504"} else "other@example.com"
        subject = "新闻提醒" if uid in {"503", "504"} else "other"
        connection.fetch_responses[uid] = header_fetch(
            uid, sender=sender.encode(), subject=subject.encode()
        )

    result = client_for(connection).search(1, "straße", "新闻", None, 1)

    assert [item["uid"] for item in result] == ["504"]
    fetch_uids = [call[2] for call in connection.calls if call[:2] == ("uid", "FETCH")]
    assert fetch_uids == ["505", "504"]


def test_search_caps_filtered_candidate_scan() -> None:
    connection = FakeImap()
    all_uids = [str(value) for value in range(1, MAX_SEARCH_CANDIDATES + 6)]
    connection.search_response = ("OK", [" ".join(all_uids).encode()])
    for uid in all_uids[-MAX_SEARCH_CANDIDATES:]:
        connection.fetch_responses[uid] = header_fetch(uid)

    result = client_for(connection).search(1, "never-matches", None, None, 1)

    assert result == []
    fetch_uids = [call[2] for call in connection.calls if call[:2] == ("uid", "FETCH")]
    assert len(fetch_uids) == MAX_SEARCH_CANDIDATES
    assert fetch_uids[0] == "505"
    assert fetch_uids[-1] == "6"


def test_read_fetches_size_before_peek_body_and_returns_structured_parser_result() -> None:
    raw = (
        b"From: sender@example.com\r\nSubject: Delivery\r\n"
        b"Content-Type: multipart/mixed; boundary=m\r\n\r\n"
        b"--m\r\nContent-Type: text/plain\r\n\r\nVisible body\r\n"
        b"--m\r\nContent-Type: application/pdf\r\n"
        b"Content-Disposition: attachment; filename=report.pdf\r\n\r\n"
        b"ATTACHMENT_SECRET\r\n--m--\r\n"
    )
    connection = FakeImap()
    connection.fetch_responses["7"] = size_fetch("7", len(raw))
    responses = [size_fetch("7", len(raw)), body_fetch("7", raw)]

    def sequenced_uid(command: str, *args: object) -> tuple[str, list[object] | None]:
        connection.calls.append(("uid", command, *args))
        return responses.pop(0)

    connection.uid = sequenced_uid  # type: ignore[method-assign]

    result = client_for(connection).read("7")

    fetch_calls = [call for call in connection.calls if call[:2] == ("uid", "FETCH")]
    assert fetch_calls == [
        ("uid", "FETCH", "7", "(RFC822.SIZE)"),
        ("uid", "FETCH", "7", "(BODY.PEEK[])"),
    ]
    assert result["uid"] == "7"
    assert result["size"] == len(raw)
    assert result["sender"] == "sender@example.com"
    assert result["subject"] == "Delivery"
    assert result["body"] == "Visible body"
    assert result["attachments"] == (
        {
            "filename": "report.pdf",
            "content_type": "application/pdf",
            "size": len(b"ATTACHMENT_SECRET"),
            "content_id": None,
        },
    )
    assert "ATTACHMENT_SECRET" not in repr(result)


def test_read_accepts_body_uid_in_fetch_trailer() -> None:
    raw = b"Subject: Trailer UID\r\n\r\nbody"
    connection = FakeImap()
    split_body = (
        "OK",
        [
            (b"1 (BODY[] {" + str(len(raw)).encode() + b"}", raw),
            b" UID 12)",
        ],
    )
    responses = [size_fetch("12", len(raw)), split_body]

    def sequenced_uid(command: str, *args: object) -> tuple[str, list[object] | None]:
        connection.calls.append(("uid", command, *args))
        return responses.pop(0)

    connection.uid = sequenced_uid  # type: ignore[method-assign]

    result = client_for(connection).read("12")

    assert result["subject"] == "Trailer UID"


def test_read_ignores_unrelated_unsolicited_fetch_records() -> None:
    raw = b"test"
    connection = FakeImap()
    size_response = (
        "OK",
        [
            b"1 (UID 7 RFC822.SIZE 4)",
            b"2 (FLAGS (\\Seen) UID 8)",
        ],
    )
    body_response = (
        "OK",
        [
            (b"1 (UID 7 BODY[] {4}", raw),
            b")",
            b"2 (FLAGS (\\Seen) UID 8)",
        ],
    )
    responses = [size_response, body_response]

    def sequenced_uid(command: str, *args: object) -> tuple[str, list[object] | None]:
        connection.calls.append(("uid", command, *args))
        return responses.pop(0)

    connection.uid = sequenced_uid  # type: ignore[method-assign]

    result = client_for(connection).read("7")

    assert result["uid"] == "7"
    assert result["size"] == 4
    assert result["body"] == "test"


@pytest.mark.parametrize("flags_first", [False, True])
def test_read_uses_size_record_when_same_uid_flags_update_is_also_returned(
    flags_first: bool,
) -> None:
    raw = b"test"
    size_record = b"1 (UID 7 RFC822.SIZE 4)"
    flags_record = b"1 (FLAGS (\\Seen) UID 7)"
    records = [flags_record, size_record] if flags_first else [size_record, flags_record]
    connection = FakeImap()
    responses = [("OK", records), body_fetch("7", raw)]

    def sequenced_uid(command: str, *args: object) -> tuple[str, list[object] | None]:
        connection.calls.append(("uid", command, *args))
        return responses.pop(0)

    connection.uid = sequenced_uid  # type: ignore[method-assign]

    result = client_for(connection).read("7")

    assert result["size"] == 4
    assert result["body"] == "test"


def test_read_rejects_duplicate_size_records_for_expected_uid() -> None:
    connection = FakeImap()
    size_record = b"1 (UID 7 RFC822.SIZE 4)"
    connection.fetch_responses["7"] = ("OK", [size_record, size_record])

    with pytest.raises(MailProtocolError):
        client_for(connection).read("7")


def test_read_does_not_bind_literal_to_uid_from_unsolicited_fetch_record() -> None:
    connection = FakeImap()
    body_response = (
        "OK",
        [
            (b"1 (BODY[] {4}", b"test"),
            b")",
            b"2 (FLAGS (\\Seen) UID 7)",
        ],
    )
    responses = [size_fetch("7", 4), body_response]

    def sequenced_uid(command: str, *args: object) -> tuple[str, list[object] | None]:
        connection.calls.append(("uid", command, *args))
        return responses.pop(0)

    connection.uid = sequenced_uid  # type: ignore[method-assign]

    with pytest.raises(MailProtocolError):
        client_for(connection).read("7")


def test_read_also_accepts_tuple_metadata_for_size_fetch() -> None:
    raw = b"Subject: Tuple metadata\r\n\r\nbody"
    connection = FakeImap()
    tuple_size = (
        "OK",
        [(b"1 (UID 8 RFC822.SIZE " + str(len(raw)).encode() + b")", b"")],
    )
    responses = [tuple_size, body_fetch("8", raw)]

    def sequenced_uid(command: str, *args: object) -> tuple[str, list[object] | None]:
        connection.calls.append(("uid", command, *args))
        return responses.pop(0)

    connection.uid = sequenced_uid  # type: ignore[method-assign]

    result = client_for(connection).read("8")

    assert result["subject"] == "Tuple metadata"
    assert result["size"] == len(raw)


def test_read_requires_tuple_payload_for_body_fetch() -> None:
    connection = FakeImap()
    responses: list[tuple[str, list[object] | None]] = [
        size_fetch("8", 10),
        ("OK", [b"raw body without fetch metadata tuple"]),
    ]

    def sequenced_uid(command: str, *args: object) -> tuple[str, list[object] | None]:
        connection.calls.append(("uid", command, *args))
        return responses.pop(0)

    connection.uid = sequenced_uid  # type: ignore[method-assign]

    with pytest.raises(MailProtocolError):
        client_for(connection).read("8")


@pytest.mark.parametrize(
    "size_data",
    [[b"1 (UID 8)"], [b"1 (UID 9 RFC822.SIZE 10)"], [b"malformed"]],
)
def test_read_rejects_malformed_size_metadata(size_data: list[object]) -> None:
    connection = FakeImap()
    connection.fetch_responses["8"] = ("OK", size_data)

    with pytest.raises(MailProtocolError):
        client_for(connection).read("8")


@pytest.mark.parametrize("uid", ["", "-1", "1 OR ALL", "１"])
def test_read_rejects_non_ascii_digit_uid_without_connecting(uid: str) -> None:
    connector = FakeConnector()
    client = ImapClient(Credentials("reader@qq.com", "secret"), connector=connector)

    with pytest.raises(MailProtocolError, match="UID"):
        client.read(uid)

    assert connector.calls == []


def test_oversized_message_prevents_body_fetch() -> None:
    connection = FakeImap()
    connection.fetch_responses["9"] = size_fetch("9", MAX_MESSAGE_BYTES + 1)

    with pytest.raises(MessageTooLargeError, match="too large"):
        client_for(connection).read("9")

    fetch_calls = [call for call in connection.calls if call[:2] == ("uid", "FETCH")]
    assert fetch_calls == [("uid", "FETCH", "9", "(RFC822.SIZE)")]


def test_oversized_body_literal_is_rejected_when_reported_size_is_within_limit() -> None:
    raw = b"x" * (MAX_MESSAGE_BYTES + 1)
    connection = FakeImap()
    responses = [size_fetch("9", MAX_MESSAGE_BYTES), body_fetch("9", raw)]

    def sequenced_uid(command: str, *args: object) -> tuple[str, list[object] | None]:
        connection.calls.append(("uid", command, *args))
        return responses.pop(0)

    connection.uid = sequenced_uid  # type: ignore[method-assign]

    with pytest.raises(MessageTooLargeError, match="too large"):
        client_for(connection).read("9")


def test_login_failure_is_safe_authentication_error_without_secret_or_cause() -> None:
    connection = FakeImap()
    connection.login_error = imaplib.IMAP4.error("bad authorization-secret")

    with pytest.raises(AuthenticationError) as caught:
        client_for(connection).check_connection()

    assert str(caught.value) == "QQ Mail authentication failed."
    assert "authorization-secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert ("logout",) in connection.calls


def test_login_abort_is_safe_connection_error_not_authentication_error() -> None:
    connection = FakeImap()
    connection.login_error = imaplib.IMAP4.abort("login connection raw text")

    with pytest.raises(MailConnectionError) as caught:
        client_for(connection).check_connection()

    assert str(caught.value) == "Unable to connect to QQ Mail."
    assert "raw text" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_post_login_imap_failure_is_protocol_error() -> None:
    connection = FakeImap()
    connection.select_error = imaplib.IMAP4.error("server detail")

    with pytest.raises(MailProtocolError, match="protocol"):
        client_for(connection).check_connection()


@pytest.mark.parametrize(
    "error",
    [imaplib.IMAP4.error("constructor raw text"), imaplib.IMAP4.abort("greeting raw text")],
)
def test_constructor_protocol_failures_are_safe_protocol_errors(error: Exception) -> None:
    client = ImapClient(
        Credentials("reader@qq.com", "authorization-secret"),
        connector=FakeConnector(error=error),
    )

    with pytest.raises(MailProtocolError) as caught:
        client.check_connection()

    assert str(caught.value) == "QQ Mail IMAP protocol operation failed."
    assert "raw text" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("error", [OSError("network detail"), ssl.SSLError("tls detail")])
def test_network_and_tls_failures_map_to_safe_connection_error(error: Exception) -> None:
    client = ImapClient(
        Credentials("reader@qq.com", "authorization-secret"),
        connector=FakeConnector(error=error),
    )

    with pytest.raises(MailConnectionError) as caught:
        client.check_connection()

    assert str(caught.value) == "Unable to connect to QQ Mail."
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    ("search_response", "fetch_response"),
    [
        (("NO", [b"server error"]), None),
        (("OK", None), None),
        (("OK", [b"not-a-uid"]), None),
        (("OK", [b"1\xff"]), None),
        (("OK", [b"1"]), ("OK", [b"trailing-only"])),
        (("OK", [b"1"]), ("NO", [b"server error"])),
    ],
)
def test_malformed_search_and_fetch_responses_are_safe_protocol_errors(
    search_response: tuple[str, list[bytes] | None],
    fetch_response: tuple[str, list[object] | None] | None,
) -> None:
    connection = FakeImap()
    connection.search_response = search_response
    if fetch_response is not None:
        connection.fetch_responses["1"] = fetch_response

    with pytest.raises(MailProtocolError) as caught:
        client_for(connection).list_recent(1)

    assert "server error" not in str(caught.value)


def test_cleanup_errors_do_not_mask_primary_operation_error() -> None:
    connection = FakeImap()
    connection.search_response = ("NO", [b"primary server error"])
    connection.unselect_error = RuntimeError("cleanup raw text")

    with pytest.raises(MailProtocolError) as caught:
        client_for(connection).list_recent(1)

    assert str(caught.value) == "QQ Mail IMAP protocol operation failed."
    assert "cleanup raw text" not in str(caught.value)
    assert connection.calls[-2:] == [("unselect",), ("logout",)]


def test_cleanup_errors_after_success_are_ignored_and_each_step_is_attempted() -> None:
    connection = FakeImap()
    connection.unselect_error = RuntimeError("unselect raw text")
    connection.logout_error = RuntimeError("logout raw text")

    result = client_for(connection).list_recent(1)

    assert result == []
    assert connection.calls[-2:] == [("unselect",), ("logout",)]


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("list_recent", (0,)),
        ("search", (0, None, None, None, 1)),
        ("search", (1, None, None, None, -1)),
    ],
)
def test_invalid_ranges_fail_before_connecting(method: str, args: tuple[object, ...]) -> None:
    connector = FakeConnector()
    client = ImapClient(Credentials("reader@qq.com", "secret"), connector=connector)

    with pytest.raises(MailProtocolError):
        getattr(client, method)(*args)

    assert connector.calls == []
