from __future__ import annotations

import pytest

from qq_mail_reader.credentials import Credentials
from qq_mail_reader.errors import MailProtocolError
from qq_mail_reader.service import QQMailService


class FakeStore:
    def __init__(self, email: str | None = "reader@qq.com") -> None:
        self.email = email
        self.configured_calls = 0
        self.load_calls = 0
        self.credentials = Credentials("reader@qq.com", "authorization-secret")

    def configured_email(self) -> str | None:
        self.configured_calls += 1
        return self.email

    def load(self) -> Credentials:
        self.load_calls += 1
        return self.credentials


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def check_connection(self) -> None:
        self.calls.append(("check_connection",))

    def list_recent(self, limit: int) -> list[dict[str, object]]:
        self.calls.append(("list_recent", limit))
        return [{"uid": "7"}]

    def search(
        self,
        since_days: int,
        sender: str | None,
        subject: str | None,
        unread: bool | None,
        limit: int,
    ) -> list[dict[str, object]]:
        self.calls.append(("search", since_days, sender, subject, unread, limit))
        return [{"uid": "8"}]

    def read(self, uid: str) -> dict[str, object]:
        self.calls.append(("read", uid))
        return {"uid": uid, "body": "hello"}


def service_with_fakes(
    email: str | None = "reader@qq.com",
) -> tuple[QQMailService, FakeStore, FakeClient, list[Credentials]]:
    store = FakeStore(email)
    client = FakeClient()
    supplied: list[Credentials] = []

    def make_client(credentials: Credentials) -> FakeClient:
        supplied.append(credentials)
        return client

    return QQMailService(store=store, client_factory=make_client), store, client, supplied


def test_status_unconfigured_does_not_load_secret_or_open_client() -> None:
    service, store, client, supplied = service_with_fakes(None)

    assert service.status() == {
        "configured": False,
        "email": None,
        "server": "imap.qq.com:993",
        "connection_checked": False,
        "connected": None,
    }
    assert store.configured_calls == 1
    assert store.load_calls == 0
    assert client.calls == []
    assert supplied == []


def test_status_masks_configured_email_without_loading_secret() -> None:
    service, store, client, supplied = service_with_fakes("reader@qq.com")

    assert service.status() == {
        "configured": True,
        "email": "re***er@qq.com",
        "server": "imap.qq.com:993",
        "connection_checked": False,
        "connected": None,
    }
    assert store.load_calls == 0
    assert client.calls == []
    assert supplied == []


def test_status_connection_check_loads_credentials_and_checks_client() -> None:
    service, store, client, supplied = service_with_fakes()

    assert service.status(check_connection=True) == {
        "configured": True,
        "email": "re***er@qq.com",
        "server": "imap.qq.com:993",
        "connection_checked": True,
        "connected": True,
    }
    assert store.load_calls == 1
    assert supplied == [store.credentials]
    assert client.calls == [("check_connection",)]


@pytest.mark.parametrize("check_connection", ["false", "true", 0, 1, None, [], {}])
def test_status_rejects_non_boolean_before_configuration_access(check_connection: object) -> None:
    service, store, client, supplied = service_with_fakes()

    with pytest.raises(MailProtocolError, match=r"^check_connection must be true or false\.$"):
        service.status(check_connection=check_connection)  # type: ignore[arg-type]

    assert store.configured_calls == 0
    assert store.load_calls == 0
    assert client.calls == []
    assert supplied == []


def test_list_recent_uses_default_and_dispatches_boundary_limit() -> None:
    service, store, client, supplied = service_with_fakes()

    assert service.list_recent() == [{"uid": "7"}]
    assert service.list_recent(1) == [{"uid": "7"}]
    assert service.list_recent(100) == [{"uid": "7"}]
    assert client.calls == [("list_recent", 20), ("list_recent", 1), ("list_recent", 100)]
    assert store.load_calls == 3
    assert supplied == [store.credentials] * 3


@pytest.mark.parametrize("limit", [0, 101, True, False, 1.5, "20", None])
def test_list_recent_rejects_invalid_limit_before_loading(limit: object) -> None:
    service, store, client, _ = service_with_fakes()

    with pytest.raises(MailProtocolError, match=r"^limit must be between 1 and 100\.$"):
        service.list_recent(limit)  # type: ignore[arg-type]

    assert store.load_calls == 0
    assert client.calls == []


def test_search_dispatches_defaults_and_explicit_values() -> None:
    service, store, client, _ = service_with_fakes()

    assert service.search() == [{"uid": "8"}]
    assert service.search(30, "a" * 200, "b" * 200, False, 100) == [{"uid": "8"}]
    assert client.calls == [
        ("search", 7, None, None, None, 20),
        ("search", 30, "a" * 200, "b" * 200, False, 100),
    ]
    assert store.load_calls == 2


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"since_days": 0}, "since_days must be between 1 and 30."),
        ({"since_days": 31}, "since_days must be between 1 and 30."),
        ({"since_days": True}, "since_days must be between 1 and 30."),
        ({"since_days": 1.5}, "since_days must be between 1 and 30."),
        ({"since_days": "7"}, "since_days must be between 1 and 30."),
        ({"limit": False}, "limit must be between 1 and 100."),
        ({"limit": 101}, "limit must be between 1 and 100."),
        ({"sender": 42}, "sender must be text or None, up to 200 characters."),
        ({"sender": "x" * 201}, "sender must be text or None, up to 200 characters."),
        ({"subject": []}, "subject must be text or None, up to 200 characters."),
        ({"subject": "x" * 201}, "subject must be text or None, up to 200 characters."),
        ({"unread": 1}, "unread must be true, false, or None."),
        ({"unread": "false"}, "unread must be true, false, or None."),
    ],
)
def test_search_rejects_invalid_inputs_before_loading(
    kwargs: dict[str, object], message: str
) -> None:
    service, store, client, _ = service_with_fakes()

    with pytest.raises(MailProtocolError) as error:
        service.search(**kwargs)  # type: ignore[arg-type]

    assert str(error.value) == message
    assert store.load_calls == 0
    assert client.calls == []


def test_read_dispatches_ascii_uid() -> None:
    service, store, client, supplied = service_with_fakes()

    assert service.read("00123") == {"uid": "00123", "body": "hello"}
    assert client.calls == [("read", "00123")]
    assert store.load_calls == 1
    assert supplied == [store.credentials]


@pytest.mark.parametrize("uid", ["", "１２３", "١٢٣", "12a", "-1", " 1", 12, None])
def test_read_rejects_invalid_uid_before_loading(uid: object) -> None:
    service, store, client, _ = service_with_fakes()

    with pytest.raises(MailProtocolError, match=r"^Message UID must contain ASCII digits only\.$"):
        service.read(uid)  # type: ignore[arg-type]

    assert store.load_calls == 0
    assert client.calls == []
