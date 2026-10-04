"""Check the packaged EDA MCP over its real stdio transport."""

import asyncio
import argparse
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(config_file):
    if config_file:
        config = json.load(open(config_file, encoding="utf-8"))["mcpServers"]["eda"]
        server = StdioServerParameters(command=config["command"], args=config["args"])
    else:
        server = StdioServerParameters(command=sys.executable, args=["-m", "eda_harness.cli", "mcp"])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            names = {tool.name for tool in tools}
            required = {"get_server_info", "check_environment", "run_action", "run_until", "get_run", "get_artifacts"}
            if len(tools) != 25 or not required <= names:
                raise RuntimeError(f"Expected 25 EDA MCP tools, found {len(tools)}: {sorted(names)}")
            info = await session.call_tool("get_server_info", {})
            if info.isError:
                raise RuntimeError(f"get_server_info failed: {info}")
            if config_file:
                context = await session.call_tool("get_operational_context", {})
                if context.isError:
                    raise RuntimeError(f"Project-bound get_operational_context failed: {context}")
            print(json.dumps({"status": "ok", "toolCount": len(tools), "projectBound": bool(config_file), "requiredTools": sorted(required)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-config")
    args = parser.parse_args()
    asyncio.run(main(args.mcp_config))
