from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Passes Chrome `--test-type`, which hides the "unsupported command-line flag" bar that the
# `--disable-blink-features=AutomationControlled` flag added by Playwright MCP would show.
CONFIG = Path(__file__).with_name("playwright-mcp.json")


@asynccontextmanager
async def playwright_session(headless: bool, output_dir: Path) -> AsyncIterator[ClientSession]:
    """Start Playwright MCP (Chrome) over stdio and yield an initialized session."""
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)  # else Playwright MCP writes .playwright-mcp/ in the cwd
    args = ["-y", "@playwright/mcp@latest", "--browser", "chrome", "--config", str(CONFIG), "--output-dir", str(output_dir)]
    if headless:
        args.append("--headless")
    async with stdio_client(StdioServerParameters(command="npx", args=args)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call_tool(session: ClientSession, name: str, arguments: dict) -> tuple[str, bool]:
    """Call an MCP tool; return its text output and whether it reported an error."""
    result = await session.call_tool(name, arguments)
    text = "".join(c.text for c in result.content if c.type == "text")
    return text, result.is_error or text.startswith("### Error")
