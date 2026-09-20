from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Chrome の `--test-type` を渡す。Playwright MCP が付ける `--disable-blink-features=AutomationControlled`
# が出す「サポートされていないコマンドラインフラグ」のバーを隠すため。
CONFIG = Path(__file__).with_name("playwright-mcp.json")


@asynccontextmanager
async def playwright_session(headless: bool, output_dir: Path) -> AsyncIterator[ClientSession]:
    """Playwright MCP（Chrome）を stdio で起動し、初期化済みのセッションを渡す。"""
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)  # 作らないと、実行した場所に Playwright MCP が .playwright-mcp/ を作る
    args = ["-y", "@playwright/mcp@latest", "--browser", "chrome", "--config", str(CONFIG), "--output-dir", str(output_dir)]
    if headless:
        args.append("--headless")
    async with stdio_client(StdioServerParameters(command="npx", args=args)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call_tool(session: ClientSession, name: str, arguments: dict) -> tuple[str, bool]:
    """MCP のツールを呼び、テキストの出力とエラーかどうかを返す。"""
    result = await session.call_tool(name, arguments)
    text = "".join(c.text for c in result.content if c.type == "text")
    return text, result.is_error or text.startswith("### Error")
