from __future__ import annotations

import csv
from pathlib import Path

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("my-vertex-chat-tools")


@mcp.tool()
def health_check() -> str:
    """Simple connectivity check for MCP clients."""
    return "MCP server is running."


@mcp.tool()
def summarize_csv(file_path: str, max_rows: int = 5) -> dict:
    """Return basic CSV metadata and a short preview."""
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return {"error": f"File not found: {file_path}"}

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return {"file": file_path, "rows": 0, "columns": 0, "preview": []}

    header = rows[0]
    data_rows = rows[1:]
    preview = data_rows[: max(0, max_rows)]

    return {
        "file": file_path,
        "rows": len(data_rows),
        "columns": len(header),
        "header": header,
        "preview": preview,
    }


@mcp.tool()
def word_count(text: str) -> dict:
    """Count characters, words, and lines for quick analysis tasks."""
    stripped = text.strip()
    words = stripped.split() if stripped else []
    lines = text.splitlines() if text else []
    return {
        "characters": len(text),
        "words": len(words),
        "lines": len(lines),
    }


if __name__ == "__main__":
    mcp.run()