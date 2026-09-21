"""
test_client.py - optional check that the server speaks MCP correctly.

Starts research_server.py as a subprocess over stdio (exactly what Claude
Desktop does), then lists and calls each endpoint. Run with the venv active:

    python test_client.py

Client code follows the "Writing MCP client" example in the MCP Python SDK
README: https://github.com/modelcontextprotocol/python-sdk#writing-mcp-clients
"""

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent / "research_server.py"


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("tools:    ", [t.name for t in tools.tools])
            resources = await session.list_resources()
            print("resources:", [str(r.uri) for r in resources.resources])
            prompts = await session.list_prompts()
            print("prompts:  ", [p.name for p in prompts.prompts])

            result = await session.call_tool(
                "search_papers", {"query": "model context protocol", "limit": 2})
            print("\nsearch_papers ->", result.content[0].text[:400], "...")

            result = await session.call_tool(
                "cite_paper", {"paper_id": "W7129030749", "style": "apa"})
            print("\ncite_paper ->", result.content[0].text[:300])

            resource = await session.read_resource("bibliography://all")
            print("\nbibliography://all ->", resource.contents[0].text[:300])

            prompt = await session.get_prompt(
                "literature_review", {"topic": "MCP security", "num_papers": "3"})
            print("\nliterature_review ->", prompt.messages[0].content.text[:200], "...")


if __name__ == "__main__":
    asyncio.run(main())
