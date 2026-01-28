# Plan: MCP Streaming Response for Query Endpoint

Add streaming support to the NotebookLM MCP server's `notebook_query` tool, enabling real-time display of "thinking steps" and progressive answer delivery to MCP clients.

## Background

**Current State:**
- `api_client.query()` waits for complete response before returning
- MCP tool `notebook_query` returns single complete response
- MCP client (`tests/mcp_client.py`) uses synchronous `httpx.Client` with SSE parsing
- Streaming test script validates the API supports real-time streaming with type 1 (answer) and type 2 (thinking) chunks

**FastMCP Streaming Options:**
- FastMCP uses `ctx.report_progress()` for progress updates during tool execution
- StreamableHTTP transport with `EventStore` supports SSE polling for long-running operations
- MCP protocol supports progress notifications via `notifications/progress`

## Implementation Steps

### Step 1: Add streaming query method to `api_client.py`

Create `query_stream()` async generator method:

```python
async def query_stream(
    self,
    notebook_id: str,
    query_text: str,
    source_ids: list[str] | None = None,
    conversation_id: str | None = None,
) -> AsyncIterator[dict]:
    """Stream query response chunks in real-time.
    
    Yields dicts with:
    - type: "thinking" | "answer"
    - text: chunk content
    - is_final: bool (last chunk)
    """
```

- Reuse request building logic from existing `query()` method
- Use `httpx.AsyncClient.stream()` instead of `post()`
- Parse chunks incrementally and yield as they arrive
- Cache conversation turn after completion

### Step 2: Add streaming query tool to `server.py`

Create new tool `notebook_query_stream`:

```python
@mcp.tool()
async def notebook_query_stream(
    notebook_id: str,
    query: str,
    source_ids: list[str] | str | None = None,
    conversation_id: str | None = None,
    ctx: Context,  # FastMCP context for progress reporting
) -> dict[str, Any]:
    """Ask AI with real-time streaming of thinking steps and answer.
    
    Shows progress as "thinking" chunks arrive, then streams answer.
    """
```

- Use `ctx.report_progress()` to send thinking steps to client
- Accumulate answer chunks and return final combined response
- Provide `conversation_id` for follow-up queries

### Step 3: Update existing `notebook_query` tool

Add optional `stream` parameter:

```python
async def notebook_query(
    notebook_id: str,
    query: str,
    source_ids: list[str] | str | None = None,
    conversation_id: str | None = None,
    timeout: float | None = None,
    stream: bool = False,  # NEW: enable streaming mode
    ctx: Context | None = None,
) -> dict[str, Any]:
```

- When `stream=True` and `ctx` available, use streaming internally
- Report progress via `ctx.report_progress()` as chunks arrive
- Backward compatible: default `stream=False` uses existing behavior

### Step 4: Configure MCP server for streaming transport

Update server initialization to enable StreamableHTTP:

```python
from fastmcp.server.event_store import EventStore

# Enable SSE polling for streaming progress
event_store = EventStore()

# Run with streaming support
mcp.run_http_async(
    event_store=event_store,
    retry_interval=2000,
)
```

### Step 5: Add streaming support to MCP client (`tests/mcp_client.py`)

Update the test client to handle streaming SSE responses:

```python
def _call_tool_streaming(
    self,
    tool_name: str,
    arguments: dict,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Call MCP tool with streaming SSE support.
    
    Args:
        tool_name: Name of the MCP tool
        arguments: Tool arguments
        on_progress: Callback for progress notifications
        
    Returns:
        Final tool result
    """
```

**Changes to `MCPClient`:**

1. **Add `_call_tool_streaming()` method:**
   - Use `httpx.Client.stream()` for real-time SSE parsing
   - Parse multiple SSE events as they arrive
   - Handle `notifications/progress` events and invoke callback
   - Return final result when `tools/call` response received

2. **Update `query_notebook()` method:**
   ```python
   def query_notebook(
       self,
       notebook_id: str,
       query: str,
       source_ids: list[str] | None = None,
       conversation_id: str | None = None,
       stream: bool = False,  # NEW
       on_progress: Callable[[dict], None] | None = None,  # NEW
   ) -> dict:
   ```

3. **Add `query_notebook_stream()` convenience method:**
   - Wrapper that enables streaming by default
   - Pretty-prints thinking steps and answer chunks
   - Returns final combined response

4. **Add CLI `--stream` flag for query command:**
   ```bash
   python tests/mcp_client.py query <notebook-id> "question" --stream
   ```

**SSE Progress Event Format:**
```json
event: message
data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progressToken":"...","progress":1,"total":10,"message":"🤔 Understanding sources..."}}

event: message  
data: {"jsonrpc":"2.0","id":"1","result":{"content":[{"type":"text","text":"{...}"}]}}
```

### Step 6: Add integration tests

Create `tests/test_streaming_mcp.py`:
- Test `notebook_query_stream` tool directly
- Verify progress notifications are sent
- Test client receives thinking + answer chunks
- Test conversation follow-up with streaming

## Architecture Diagram

```
┌─────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  MCP Client │────▶│ FastMCP Server  │────▶│ NotebookLM API   │
│ (mcp_client │     │   (server.py)   │     │ (api_client.py)  │
│  .py/Claude)│     └─────────────────┘     └──────────────────┘
└─────────────┘              │                        │
       │                     │                        │
       │  SSE Stream         │  query_stream()        │  POST stream
       │  - progress events  │  async generator       │  chunks
       │  - final result     │                        │
       ◀─────────────────────◀────────────────────────◀───────────
       
       Event: notifications/progress {message: "🤔 Understanding..."}
       Event: notifications/progress {message: "🤔 Exploring themes..."}
       Event: tools/call result {answer: "💡 Based on the sources..."}
```

## Files to Modify

1. **[src/notebooklm_mcp/api_client.py](../src/notebooklm_mcp/api_client.py)**
   - Add `query_stream()` async generator method
   - Extract shared request building logic into helper method

2. **[src/notebooklm_mcp/server.py](../src/notebooklm_mcp/server.py)**
   - Add `notebook_query_stream` tool with `Context` parameter
   - Optionally update `notebook_query` with `stream` parameter
   - Configure `EventStore` for streaming transport

3. **[tests/mcp_client.py](../tests/mcp_client.py)**
   - Add `_call_tool_streaming()` method with SSE stream parsing
   - Update `query_notebook()` with `stream` and `on_progress` params
   - Add `query_notebook_stream()` convenience method
   - Add `--stream` CLI flag for query command

4. **[tests/test_streaming_mcp.py](../tests/test_streaming_mcp.py)** (new)
   - Integration tests for streaming query tool
   - Test MCP client streaming support

## Further Considerations

### 1. Client Compatibility
MCP clients must support progress notifications to benefit from streaming. Clients that don't support it will still work but won't see real-time updates.

### 2. Error Handling
- What happens if stream disconnects mid-response?
- Should we buffer partial answers and return them?
- Retry logic for transient failures?

### 3. Rate Limiting
- NotebookLM may have rate limits on streaming connections
- Consider adding backoff/retry for 429 responses

### 4. Backward Compatibility
- Keep existing `notebook_query` working as-is by default
- New streaming behavior opt-in via `stream=True` or separate tool

### 5. Performance Metrics
- Track time-to-first-chunk in telemetry
- Compare streaming vs non-streaming latency
