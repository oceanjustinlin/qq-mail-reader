from __future__ import annotations

import asyncio
import logging

import pytest

from qq_mail_reader import server
from qq_mail_reader.errors import ConfigurationError


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.error: Exception | None = None

    def _result(self, call: tuple[object, ...], result: object) -> object:
        self.calls.append(call)
        if self.error is not None:
            raise self.error
        return result

    def status(self, check_connection: bool = False) -> dict[str, object]:
        return self._result(("status", check_connection), {"configured": False})  # type: ignore[return-value]

    def list_recent(self, limit: int = 20) -> list[dict[str, object]]:
        return self._result(("list_recent", limit), [{"uid": "1"}])  # type: ignore[return-value]

    def search(
        self,
        since_days: int = 7,
        sender: str | None = None,
        subject: str | None = None,
        unread: bool | None = None,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        return self._result(("search", since_days, sender, subject, unread, limit), [{"uid": "2"}])  # type: ignore[return-value]

    def read(self, uid: str) -> dict[str, object]:
        return self._result(("read", uid), {"uid": uid, "body": "hello"})  # type: ignore[return-value]


@pytest.fixture
def fake_service(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    fake = FakeService()
    monkeypatch.setattr(server, "service", fake)
    return fake


def test_tools_are_registered_with_expected_names() -> None:
    assert {tool.name for tool in asyncio.run(server.mcp.list_tools())} == {
        "qq_mail_status",
        "qq_mail_list",
        "qq_mail_search",
        "qq_mail_read",
    }


def test_sdk_call_returns_structured_tool_result(fake_service: FakeService) -> None:
    result = asyncio.run(server.mcp.call_tool("qq_mail_list", {"limit": 1}))

    assert result.structured_content == {"ok": True, "messages": [{"uid": "1"}]}
    assert fake_service.calls == [("list_recent", 1)]


def test_tools_return_structured_results_and_dispatch_defaults(fake_service: FakeService) -> None:
    assert server.qq_mail_status() == {"ok": True, "status": {"configured": False}}
    assert server.qq_mail_list() == {"ok": True, "messages": [{"uid": "1"}]}
    assert server.qq_mail_search() == {"ok": True, "messages": [{"uid": "2"}]}
    assert server.qq_mail_read("5") == {
        "ok": True,
        "message": {"uid": "5", "body": "hello"},
    }
    assert fake_service.calls == [
        ("status", False),
        ("list_recent", 20),
        ("search", 7, None, None, None, 20),
        ("read", "5"),
    ]


def test_tools_dispatch_explicit_values(fake_service: FakeService) -> None:
    assert server.qq_mail_status(True)["ok"] is True
    assert server.qq_mail_list(100)["ok"] is True
    assert server.qq_mail_search(30, "alice", "receipt", True, 1)["ok"] is True
    assert fake_service.calls == [
        ("status", True),
        ("list_recent", 100),
        ("search", 30, "alice", "receipt", True, 1),
    ]


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (server.qq_mail_status, ()),
        (server.qq_mail_list, ()),
        (server.qq_mail_search, ()),
        (server.qq_mail_read, ("1",)),
    ],
)
def test_known_mail_errors_have_safe_envelope(
    fake_service: FakeService, tool: object, args: tuple[object, ...]
) -> None:
    fake_service.error = ConfigurationError("QQ Mail credentials are not configured.")

    assert tool(*args) == {"ok": False, "error": "QQ Mail credentials are not configured."}  # type: ignore[operator]


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (server.qq_mail_status, ()),
        (server.qq_mail_list, ()),
        (server.qq_mail_search, ()),
        (server.qq_mail_read, ("1",)),
    ],
)
def test_unknown_errors_hide_secrets_and_log_only_type(
    fake_service: FakeService,
    tool: object,
    args: tuple[object, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "authorization-secret"
    fake_service.error = RuntimeError(secret)

    with caplog.at_level(logging.ERROR):
        result = tool(*args)  # type: ignore[operator]

    assert result == {"ok": False, "error": "Unexpected local mail-reader error."}
    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_main_runs_server(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(server.mcp, "run", lambda: calls.append("run"))

    server.main()

    assert calls == ["run"]
