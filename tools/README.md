# Tools Directory

This directory contains MCP servers and external tool scripts for agent calls.

## Included MCP Server

- File: `tools/mcp_server.py`
- Transport: stdio
- Server name: `my-vertex-chat-tools`

### Exposed tools

- `health_check()`
- `summarize_csv(file_path: str, max_rows: int = 5)`
- `word_count(text: str)`

### Run locally

```bash
cd /path/to/my-vertex-chat
python tools/mcp_server.py
```

### Example MCP client config snippet

```json
{
	"mcpServers": {
		"my-vertex-chat-tools": {
			"command": "python",
			"args": ["tools/mcp_server.py"],
			"cwd": "/path/to/my-vertex-chat"
		}
	}
}
```
