"""Spawn the server over stdio exactly as an MCP client would, list tools and call two of them.
Run: uv run python tests/mcp_roundtrip.py"""
from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=["-m", "nse_research_mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.serverInfo.name, init.serverInfo.version)
            tools = await session.list_tools()
            print("tools:", len(tools.tools))
            r = await session.call_tool("get_stock_quote", {"symbol": "TCS"})
            q = json.loads(r.content[0].text)
            print("get_stock_quote TCS ->", q.get("name"), q.get("price"))
            r = await session.call_tool("get_market_pulse", {})
            p = json.loads(r.content[0].text)
            print("get_market_pulse ->", [(i["index"], i["change_pct"]) for i in p.get("key_indices", [])[:3]])
            ok = bool(q.get("price")) and bool(p.get("key_indices"))
            print("ROUNDTRIP", "PASS" if ok else "FAIL")
            return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
