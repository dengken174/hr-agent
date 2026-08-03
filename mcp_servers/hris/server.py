import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from mcp_servers.hris import server_name
from mcp_servers.hris.tools import HRIS_TOOLS

server = Server(server_name)


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [t.to_mcp_tool() for t in HRIS_TOOLS]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    full_name = f"{server_name}.{name}"
    for t in HRIS_TOOLS:
        if t.name == name:
            return await t.handler(**arguments)
    return [types.TextContent(type="text", text=f"Unknown tool: {full_name}")]


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
