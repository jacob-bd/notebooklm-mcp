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

## Non-Blocking Polling Patterns

### The Problem with Blocking Waits

Deep research can take 5+ minutes. Blocking the agent for that entire time is inefficient:
- Agent can't do other work
- User experience is poor (no progress feedback)
- Wastes compute resources

### Recommended: Short Burst Polling

Instead of one long wait, use short polling bursts with work in between:

```python
# Pattern 1: Single poll (instant check)
result = research_status(notebook_id, max_wait=0, query="my query")
if result["research"]["status"] == "in_progress":
    # Research not done - do other work, poll again later
    pass

# Pattern 2: Short burst (60s max)
result = research_status(notebook_id, max_wait=60, query="my query")
if result["research"]["status"] == "in_progress":
    # Still in progress after 60s - continue other tasks
    pass

# Pattern 3: Subagent for long waits
# Main agent spawns subagent for research polling
# Main agent continues with other tasks
# Subagent reports back when done
```

### Deep Research Task ID Mutation

**Important:** Deep research may change `task_id` during processing!

```
research_start() returns task_id: "abc123"
NotebookLM internally mutates to: "xyz789"
Polling with "abc123" fails!
```

**Solution:** Always provide `query` parameter alongside `task_id`:

```python
# Start research
start_result = research_start(notebook_id, query="quantum computing", mode="deep")
original_task_id = start_result["task_id"]

# Poll with BOTH task_id AND query for failsafe
status = research_status(
    notebook_id,
    task_id=original_task_id,  # May become invalid
    query="quantum computing",  # Fallback matching
    max_wait=60
)

# IMPORTANT: Use the returned task_id for import, NOT the original!
actual_task_id = status["research"]["task_id"]  # This is the real one
research_import(notebook_id, task_id=actual_task_id)
```

### Agent Workflow Example

```python
# Step 1: Start research (non-blocking)
start = research_start(notebook_id, query="AI in healthcare", mode="deep")
query = "AI in healthcare"

# Step 2: Quick status check, then do other work
status = research_status(notebook_id, query=query, max_wait=0)
while status["research"]["status"] == "in_progress":
    # Do other productive work here...
    # e.g., process other queries, prepare report templates
    
    # Check again after some time
    time.sleep(60)
    status = research_status(notebook_id, query=query, max_wait=0)

# Step 3: Import using the RETURNED task_id
final_task_id = status["research"]["task_id"]
research_import(notebook_id, task_id=final_task_id)
```

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
