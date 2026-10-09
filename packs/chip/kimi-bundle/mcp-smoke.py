"""Discover the installed bundle's actual MCP launcher over stdio."""

import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    # The launcher owns its relocatable Python paths. MCP's default environment
    # intentionally omits PYTHONPATH, so do not launch bare `python -m` here.
    root = Path(__file__).resolve().parent
    server = StdioServerParameters(command=str(root / "bin/eda-chip"), args=["mcp"])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert len(names) == 25 and {"get_server_info", "run_action", "get_run", "get_tool_guide"} <= names, names
            info = await session.call_tool("get_server_info", {})
            assert not info.isError, info
            print(json.dumps({"status": "PASS", "stdioToolCount": len(names), "launcher": str(root / "bin/eda-chip")}))


if __name__ == "__main__":
    asyncio.run(main())
