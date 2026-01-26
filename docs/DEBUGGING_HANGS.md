# Debugging Hangs and Timeouts

## Common Hang Scenarios

### 1. research_status Getting Stuck

**Symptoms:**
- Tool call to `research_status` never returns
- CLI shows no output after starting research status check
- Must manually kill the process

**Root Causes (Fixed in v0.1.10+):**
- ✅ **Infinite loop when task_id not found** - If you polled for a specific `task_id` that didn't exist or already completed, the tool would wait forever
- ✅ **No maximum poll limit** - Research could theoretically run forever if status never changed
- ✅ **No timeout on individual API calls** - Each poll didn't have its own timeout

**Fixes Applied:**
1. Added timeout check even when waiting for `task_id` to appear
2. Added maximum poll attempt limit (100 or 2x expected polls based on max_wait)
3. Added explicit 45s timeout to `poll_research` API calls
4. Error messages now include poll count and elapsed time for debugging

**Workarounds (if using older version):**
- Always set a reasonable `max_wait` (don't use 0 for infinite)
- If stuck, manually stop and check notebook manually to see if research completed
- Use `--debug` flag to see what's happening

### 2. notebook_query Hanging

**Symptoms:**
- Query tool never returns response
- No error message, just waiting forever

**Root Causes:**
- **Network timeout**: NotebookLM backend can be slow for complex queries
- **Large response**: Parsing very long AI responses can be slow
- **API timeout**: Default 120s might not be enough for complex notebooks

**Solutions:**
```python
# Increase timeout via environment variable
export NOTEBOOKLM_QUERY_TIMEOUT=180  # 3 minutes

# Or via CLI flag
notebooklm-mcp --query-timeout 180
```

**Debug Steps:**
1. Enable debug logging: `notebooklm-mcp --debug`
2. Check the API request/response logs
3. Verify the query is reaching the server
4. Check network connectivity
5. Try a simpler query first

### 3. research_start Not Progressing

**Symptoms:**
- `research_start` returns successfully with task_id
- `research_status` shows "in_progress" forever
- Never reaches "completed"

**Possible Causes:**
- NotebookLM backend genuinely taking a long time (Deep Research can take 5+ minutes)
- Research failed on backend but status not updated
- Network issues between polls

**Debugging:**
```bash
# Enable debug mode
notebooklm-mcp --debug

# Check status manually in web UI
# Visit: https://notebooklm.google.com/notebook/<notebook_id>

# Poll with longer intervals
research_status(notebook_id, poll_interval=60, max_wait=600)  # 10 minutes
```

**Best Practices:**
- For Deep Research: `max_wait=600` (10 minutes), `poll_interval=60`
- For Fast Research: `max_wait=120` (2 minutes), `poll_interval=15`
- Always check the web UI if polling times out
- Research may complete successfully even if polling times out

## Debug Logging

Enable comprehensive debug logging to see all API traffic:

```bash
notebooklm-mcp --debug
```

This logs:
- Every MCP tool call with parameters
- Every NotebookLM API request (URL, params)
- Every API response (parsed data)
- RPC method names for easier debugging

**Example Debug Output:**
```
DEBUG - MCP Request: research_status({"notebook_id": "abc123", "max_wait": 300})
DEBUG - ===================================================
DEBUG - RPC Call: e3bVqc (poll_research)
DEBUG - URL Parameters:
DEBUG -   rpcids: e3bVqc
DEBUG -   f.sid: xyz789
DEBUG - Request Params:
DEBUG -   [null, null, "abc123"]
DEBUG - Response Status: 200
DEBUG - Response Data:
DEBUG -   {"status": "in_progress", "task_id": "task123", ...}
DEBUG - MCP Response: research_status -> {"status": "success", ...}
```

## Timeout Configuration

### Query Timeout
Controls how long `notebook_query` waits for AI response:

```bash
# Environment variable (seconds)
export NOTEBOOKLM_QUERY_TIMEOUT=180

# CLI flag
notebooklm-mcp --query-timeout 180

# In tool call (Python client)
notebook_query(notebook_id, query, timeout=180.0)
```

**Recommendations:**
- Simple queries: 60s (default)
- Complex queries (many sources): 120-180s
- Very large notebooks: 240s+

### Research Polling Timeout
Controls how long `research_status` polls before giving up:

```bash
# In tool call
research_status(
    notebook_id,
    max_wait=600,        # 10 minutes total
    poll_interval=60,    # Check every minute
)
```

**Recommendations:**
- Fast Research: `max_wait=120`, `poll_interval=15`
- Deep Research: `max_wait=600`, `poll_interval=60`
- Drive Search: `max_wait=90`, `poll_interval=20`

### API-Level Timeouts (Advanced)
Internal httpx timeouts (usually don't need to change):

```python
DEFAULT_TIMEOUT = 30.0          # Most RPC calls
SOURCE_ADD_TIMEOUT = 120.0      # Adding sources (larger files)
# poll_research timeout = 45.0  # Added in v0.1.10
```

## Monitoring and Troubleshooting

### Check If MCP Server Is Responsive

```bash
# If running HTTP transport
curl http://localhost:8000/health

# Expected response:
# {"status": "healthy", "service": "notebooklm-mcp", "version": "0.1.10"}
```

### Common Error Messages

**"Polling limit reached (100 attempts)"**
- Research is genuinely stuck or max_wait is too high
- Check web UI to see actual status
- May indicate API issue on Google's side

**"Task {task_id} not found after Xs"**
- Task_id doesn't exist (check spelling)
- Research already completed before you started polling
- Research was deleted

**"Authentication expired"**
- Run `notebooklm-mcp-auth` to re-authenticate
- Check cookies haven't been manually cleared

## Performance Tips

1. **Don't poll too frequently**: `poll_interval < 10` wastes API calls
2. **Set reasonable max_wait**: Don't use `max_wait=0` unless you want a single poll
3. **Use compact mode**: `research_status(notebook_id, compact=True)` saves tokens
4. **Monitor poll count**: If `polls_made > 20`, something is probably wrong
5. **Check web UI**: Fastest way to verify actual status

## When to Report a Bug

If you experience hangs after applying these fixes:
1. Enable `--debug` logging
2. Save the debug output
3. Note the exact tool call that hung
4. Check web UI to see actual state
5. Create issue with all above information

Include:
- Version: `notebooklm-mcp --version` (or check pyproject.toml)
- Debug logs showing the hang
- Tool call parameters
- How long it hung before you killed it
- What the web UI showed
