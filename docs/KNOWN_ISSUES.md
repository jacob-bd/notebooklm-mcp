# Known Issues and Fragility

This document describes known limitations and potential failure points in the NotebookLM MCP. Since this project uses undocumented internal APIs, certain breakages are expected over time.

---

## 1. Hardcoded `bl` Version String

### What it is
The `bl` (build label) parameter is a frontend version identifier required by NotebookLM's batchexecute API. It looks like:
```
boq_labs-tailwind-frontend_20251221.14_p0
```

### When it breaks
Google deploys new frontend versions periodically. When this happens, the hardcoded `bl` value may become stale. Symptoms:
- API calls return errors or unexpected responses
- Operations that previously worked start failing

### How to fix
Set the `NOTEBOOKLM_BL` environment variable to override the default:

```bash
export NOTEBOOKLM_BL="boq_labs-tailwind-frontend_YYYYMMDD.XX_p0"
```

To find the current value:
1. Open Chrome DevTools on `notebooklm.google.com`
2. Go to Network tab
3. Find any request to `/_/LabsTailwindUi/data/batchexecute`
4. Look for the `bl=` parameter in the URL

---

## 2. Cookie Expiration

### What it is
Authentication uses browser cookies extracted from an active Chrome session. These cookies have a limited lifespan.

### When it breaks
Cookies typically expire after 2-4 weeks. Symptoms:
- `ValueError: Cookies have expired. Please re-authenticate...`
- API calls redirect to Google login page
- Authentication errors on previously working operations

### How to fix
Re-extract fresh cookies using one of these methods:

**Option A: notebooklm-mcp-auth CLI (recommended)**

The built-in authentication CLI automatically launches Chrome, navigates to NotebookLM, and extracts cookies:

```bash
notebooklm-mcp-auth
```

If Chrome is not running, it will be launched automatically. If you're not logged in, the CLI waits for you to complete login in the browser window. Tokens are cached to `~/.notebooklm-mcp/auth.json`.

**Option B: Chrome DevTools MCP**

If your AI assistant has Chrome DevTools MCP available:
1. Navigate to `notebooklm.google.com` in Chrome
2. Use Chrome DevTools MCP to extract cookies from any network request
3. Call `save_auth_tokens(cookies=<cookie_header>)`

**Option C: Manual extraction**
1. Open Chrome DevTools on `notebooklm.google.com`
2. Network tab → find any request → copy Cookie header
3. Set `NOTEBOOKLM_COOKIES` environment variable

---

## 3. Rate Limits

### What it is
The free tier of NotebookLM has usage limits enforced server-side.

### Current limits
- ~50 queries per day (approximate, not officially documented)
- Studio content generation may have separate limits

### Symptoms when exceeded
- API returns rate limit errors
- Operations start failing mid-session

### Mitigation
- Space out operations when possible
- Avoid tight polling loops
- Consider batching queries where the API supports it

---

## 4. API Instability (Undocumented Internal APIs)

### What it is
This MCP uses internal, undocumented APIs that Google can change at any time without notice.

### What can break
- RPC IDs (e.g., `wXbhsf` for list notebooks) may be renamed
- Request/response structure may change
- New required parameters may be added
- Endpoints may be deprecated or moved

### Symptoms
- Parsing errors (unexpected response shape)
- `None` results from previously working operations
- New error messages from the API

### What to do when it breaks
1. Check if the issue is widespread (Google may have deployed changes)
2. Use Chrome DevTools to capture current request/response format
3. Update the relevant RPC handling in `api_client.py`
4. Submit a PR or issue if you discover the fix

---

## 5. CSRF Token and Session ID

### What it is
The MCP auto-extracts CSRF token (`SNlM0e`) and session ID (`FdrFJe`) from the NotebookLM homepage on first use.

### When it breaks
- If the homepage structure changes, extraction may fail
- Tokens are per-session and must be refreshed if the page is not accessible

### Symptoms
- `ValueError: Could not extract CSRF token from page`
- Debug HTML saved to `~/.notebooklm-mcp/debug_page.html`

### How to fix
If auto-extraction fails:
1. Manually extract tokens from Chrome DevTools Network tab
2. Pass them via `save_auth_tokens(cookies=..., request_body=..., request_url=...)`

---

## Reporting Issues

When reporting issues, include:
1. The specific tool/operation that failed
2. The error message (redact any sensitive info)
3. Whether the operation worked before
4. The current date (to correlate with potential Google deployments)

