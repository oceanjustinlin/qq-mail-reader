"""In-memory MCP discovery contract for the read-only QQ Mail server."""

import pytest
from mcp import Client

from qq_mail_reader.server import mcp


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_mcp_discovers_exactly_the_four_read_tools() -> None:
    async with Client(mcp) as client:
        result = await client.list_tools()

    assert {tool.name for tool in result.tools} == {
        "qq_mail_status",
        "qq_mail_list",
        "qq_mail_search",
        "qq_mail_read",
    }
