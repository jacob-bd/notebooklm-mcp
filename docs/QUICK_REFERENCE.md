# Quick Reference - Phase 1 Features

## HTTP Transport

```bash
# Start HTTP server on localhost:8000
notebooklm-mcp --transport http

# Custom host and port
notebooklm-mcp -t http -H 0.0.0.0 -p 3000

# With stateless mode (for load balancing)
notebooklm-mcp --transport http --stateless

# Environment variable method
export NOTEBOOKLM_MCP_TRANSPORT=http
export NOTEBOOKLM_MCP_PORT=9000
notebooklm-mcp
```

**Endpoints when running HTTP:**
- MCP Endpoint: `http://localhost:8000/mcp`
- Health Check: `http://localhost:8000/health`

## Debug Logging

```bash
# Enable debug logging via CLI
notebooklm-mcp --debug

# Enable debug logging via environment
export NOTEBOOKLM_MCP_DEBUG=true
notebooklm-mcp

# Debug + HTTP transport
notebooklm-mcp --transport http --debug
```

**What gets logged:**
- RPC call names and URLs
- Request bodies (truncated)
- Response status codes and bodies (truncated)
- Extracted results (truncated)

## All Flags

### notebooklm-mcp
```
--transport, -t   stdio|http|sse (default: stdio)
--host, -H        Host to bind (default: 127.0.0.1)
--port, -p        Port number (default: 8000)
--path            MCP endpoint path (default: /mcp)
--stateless       Enable stateless mode
--debug           Enable debug logging
```

### notebooklm-mcp-auth
```
--file [PATH]       Import cookies from file
--port PORT         Chrome DevTools port (default: 9222)
--show-tokens       Show cached tokens
--no-auto-launch    Don't auto-launch Chrome
--devtools-timeout  DevTools HTTP timeout in seconds (default: 5.0)
```

## Environment Variables

```bash
NOTEBOOKLM_MCP_TRANSPORT=http|sse|stdio
NOTEBOOKLM_MCP_HOST=0.0.0.0
NOTEBOOKLM_MCP_PORT=8000
NOTEBOOKLM_MCP_PATH=/mcp
NOTEBOOKLM_MCP_STATELESS=true
NOTEBOOKLM_MCP_DEBUG=true
```

## Testing

```bash
# Test help
notebooklm-mcp --help
notebooklm-mcp-auth --help

# Start HTTP server
notebooklm-mcp --transport http

# In another terminal:
curl http://localhost:8000/health
# Expected: {"status":"healthy","service":"notebooklm-mcp","version":"0.1.8"}

# Test debug logging
notebooklm-mcp --transport http --debug
# Should see: "Debug logging: ENABLED"
# Then detailed logs for each API call
```
