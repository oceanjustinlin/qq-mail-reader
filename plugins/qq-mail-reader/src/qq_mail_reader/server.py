"""MCP entry point for read-only QQ Mail tools."""

from __future__ import annotations

import logging

from mcp.server import MCPServer

from .errors import QQMailError
from .service import QQMailService

logger = logging.getLogger(__name__)
mcp = MCPServer("QQ Mail Reader")
service = QQMailService()


def _error_result(error: Exception) -> dict[str, object]:
    if isinstance(error, QQMailError):
        return {"ok": False, "error": str(error)}
    logger.error("Unexpected local mail-reader exception type: %s", type(error).__name__)
    return {"ok": False, "error": "Unexpected local mail-reader error."}


@mcp.tool()
def qq_mail_status(check_connection: bool = False) -> dict[str, object]:
    """Show QQ Mail setup status and optionally check the connection."""
    try:
        return {"ok": True, "status": service.status(check_connection)}
    except Exception as error:
        return _error_result(error)


@mcp.tool()
def qq_mail_list(limit: int = 20) -> dict[str, object]:
    """List recent QQ Mail message summaries."""
    try:
        return {"ok": True, "messages": service.list_recent(limit)}
    except Exception as error:
        return _error_result(error)


@mcp.tool()
def qq_mail_search(
    since_days: int = 7,
    sender: str | None = None,
    subject: str | None = None,
    unread: bool | None = None,
    limit: int = 20,
) -> dict[str, object]:
    """Search QQ Mail message summaries."""
    try:
        return {
            "ok": True,
            "messages": service.search(since_days, sender, subject, unread, limit),
        }
    except Exception as error:
        return _error_result(error)


@mcp.tool()
def qq_mail_read(uid: str) -> dict[str, object]:
    """Read one QQ Mail message without changing its read state."""
    try:
        return {"ok": True, "message": service.read(uid)}
    except Exception as error:
        return _error_result(error)


def main() -> None:
    """Run the QQ Mail MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
