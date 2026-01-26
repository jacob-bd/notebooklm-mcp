# Async Refactoring Plan for NotebookLMClient

> **Document Version:** 1.0  
> **Created:** January 26, 2026  
> **Branch:** feature/http-concurrency  
> **Status:** Planning

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current Architecture Analysis](#2-current-architecture-analysis)
3. [Target Architecture](#3-target-architecture)
4. [Detailed Refactoring Plan](#4-detailed-refactoring-plan)
5. [Method Inventory](#5-method-inventory)
6. [Code Changes Specification](#6-code-changes-specification)
7. [Migration Strategy](#7-migration-strategy)
8. [Testing Plan](#8-testing-plan)
9. [Risks and Mitigations](#9-risks-and-mitigations)
10. [Estimated Timeline](#10-estimated-timeline)

---

## 1. Executive Summary

### Problem Statement

The current `NotebookLMClient` uses synchronous `httpx.Client` which **blocks the event loop** when making HTTP requests. This prevents true concurrent request handling in the MCP server, even though FastMCP/Uvicorn supports async operations.

### Proposed Solution

Refactor `NotebookLMClient` to use `httpx.AsyncClient` with proper `async/await` patterns throughout the codebase, enabling non-blocking concurrent request processing.

### Expected Benefits

| Metric | Current | After Refactor |
|--------|---------|----------------|
| Concurrent Requests | Sequential (blocked) | True parallel |
| Event Loop Utilization | Blocked during HTTP | Free during I/O wait |
| Throughput (req/sec) | ~5-10 | ~50-100 (estimated) |
| Long Query Impact | Blocks all requests | Isolated |

---

## 2. Current Architecture Analysis

### 2.1 Request Flow (Synchronous)

```
┌─────────────────────────────────────────────────────────────────┐
│                     CURRENT SYNC FLOW                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  MCP Request ──► Tool Handler ──► get_client() ──► client.method()
│       │                                               │
│       │                                               ▼
│       │                                         _call_rpc()
│       │                                               │
│       │         ┌─────────────────────────────────────┘
│       │         │
│       │         ▼
│       │    httpx.Client.post()  ◄── BLOCKING CALL
│       │         │
│       │         │  ╔═══════════════════════════════════╗
│       │         │  ║  EVENT LOOP BLOCKED HERE!         ║
│       │         │  ║  No other requests can proceed    ║
│       │         │  ║  until HTTP response received     ║
│       │         │  ╚═══════════════════════════════════╝
│       │         │
│       │         ▼
│       │    Response received
│       │         │
│       ◄─────────┘
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Identified Bottlenecks

#### 2.2.1 Global Singleton Without Lock Protection

**File:** `src/notebooklm_mcp/server.py` (Lines 41-109)

```python
# PROBLEM: No thread/async safety
_client: NotebookLMClient | None = None

def get_client() -> NotebookLMClient:
    global _client
    if _client is None:  # ← Race condition window
        # ... initialization code ...
        _client = NotebookLMClient(...)
    return _client
```

**Issue:** Multiple concurrent requests during startup can create multiple client instances.

#### 2.2.2 Synchronous HTTP Client

**File:** `src/notebooklm_mcp/api_client.py` (Lines 443-460)

```python
# PROBLEM: Synchronous client blocks event loop
def _get_client(self) -> httpx.Client:
    if self._client is None:
        self._client = httpx.Client(
            headers={...},
            timeout=30.0,
        )
    return self._client
```

#### 2.2.3 Unprotected Shared State

**File:** `src/notebooklm_mcp/api_client.py` (Line 347)

```python
# PROBLEM: No lock protection for concurrent access
self._conversation_cache: dict[str, list[ConversationTurn]] = {}
```

#### 2.2.4 Non-Atomic Counter

**File:** `src/notebooklm_mcp/api_client.py` (Lines 349-350)

```python
# PROBLEM: Race condition on increment
self._reqid_counter = random.randint(100000, 999999)
# Later: self._reqid_counter += 100000
```

---

## 3. Target Architecture

### 3.1 Request Flow (Asynchronous)

```
┌─────────────────────────────────────────────────────────────────┐
│                     TARGET ASYNC FLOW                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  MCP Request 1 ──► async tool_handler() ──► await get_client()  │
│       │                                            │             │
│  MCP Request 2 ──► async tool_handler() ──►       │             │
│       │                                    ┌───────┘             │
│  MCP Request 3 ──► async tool_handler() ──►│                    │
│       │                                    │                     │
│       │                                    ▼                     │
│       │                        await client.method()             │
│       │                                    │                     │
│       │                                    ▼                     │
│       │                        await _call_rpc()                 │
│       │                                    │                     │
│       │                                    ▼                     │
│       │              await httpx.AsyncClient.post()              │
│       │                                    │                     │
│       │         ╔══════════════════════════════════════════╗    │
│       │         ║  EVENT LOOP FREE!                        ║    │
│       │         ║  Other requests continue processing      ║    │
│       │         ║  while waiting for HTTP response         ║    │
│       │         ╚══════════════════════════════════════════╝    │
│       │                                    │                     │
│       │                                    ▼                     │
│       ◄────────────────────────── Response 1, 2, 3              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 Key Architectural Changes

| Component | Current | Target |
|-----------|---------|--------|
| HTTP Client | `httpx.Client` | `httpx.AsyncClient` |
| Client Singleton | No lock | `asyncio.Lock` |
| Tool Handlers | `def func()` | `async def func()` |
| API Methods | `def method()` | `async def method()` |
| Conversation Cache | No lock | `asyncio.Lock` |
| Request Counter | Non-atomic | `asyncio.Lock` protected |

---

## 4. Detailed Refactoring Plan

### Phase 1: Foundation (Non-Breaking)

**Goal:** Add thread safety without changing sync/async behavior.

#### 4.1.1 Add Lock to Client Singleton

**File:** `src/notebooklm_mcp/server.py`

```python
# ADD: Import
import asyncio

# ADD: Lock instance
_client_lock: asyncio.Lock | None = None

def _get_lock() -> asyncio.Lock:
    """Get or create the async lock for client initialization."""
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock
```

#### 4.1.2 Add Lock to Conversation Cache

**File:** `src/notebooklm_mcp/api_client.py`

```python
# In __init__:
self._conversation_cache: dict[str, list[ConversationTurn]] = {}
self._cache_lock: asyncio.Lock = asyncio.Lock()  # ADD
self._reqid_lock: asyncio.Lock = asyncio.Lock()  # ADD
```

---

### Phase 2: Core HTTP Client Conversion

**Goal:** Convert core HTTP infrastructure to async.

#### 4.2.1 Convert `_get_client()`

**Before:**
```python
def _get_client(self) -> httpx.Client:
    if self._client is None:
        cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        self._client = httpx.Client(
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Origin": self.BASE_URL,
                "Referer": f"{self.BASE_URL}/",
                "Cookie": cookie_str,
                "X-Same-Domain": "1",
                "User-Agent": "Mozilla/5.0 ...",
            },
            timeout=30.0,
        )
    return self._client
```

**After:**
```python
async def _get_client(self) -> httpx.AsyncClient:
    if self._client is None:
        cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        self._client = httpx.AsyncClient(
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Origin": self.BASE_URL,
                "Referer": f"{self.BASE_URL}/",
                "Cookie": cookie_str,
                "X-Same-Domain": "1",
                "User-Agent": "Mozilla/5.0 ...",
            },
            timeout=30.0,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=30.0,
            ),
        )
    return self._client
```

#### 4.2.2 Convert `_call_rpc()`

**Before:**
```python
def _call_rpc(
    self,
    rpc_id: str,
    params: Any,
    path: str = "/",
    timeout: float | None = None,
    _retry: bool = False,
    _deep_retry: bool = False,
) -> Any:
    client = self._get_client()
    body = self._build_request_body(rpc_id, params)
    url = self._build_url(rpc_id, path)
    
    # ... debug logging ...
    
    try:
        if timeout:
            response = client.post(url, content=body, timeout=timeout)
        else:
            response = client.post(url, content=body)
        
        response.raise_for_status()
        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, rpc_id)
        return result
    except (httpx.HTTPStatusError, AuthenticationError) as e:
        # ... retry logic ...
```

**After:**
```python
async def _call_rpc(
    self,
    rpc_id: str,
    params: Any,
    path: str = "/",
    timeout: float | None = None,
    _retry: bool = False,
    _deep_retry: bool = False,
) -> Any:
    client = await self._get_client()
    body = self._build_request_body(rpc_id, params)
    url = self._build_url(rpc_id, path)
    
    # ... debug logging (unchanged) ...
    
    try:
        if timeout:
            response = await client.post(url, content=body, timeout=timeout)
        else:
            response = await client.post(url, content=body)
        
        response.raise_for_status()
        parsed = self._parse_response(response.text)  # Pure function, no await
        result = self._extract_rpc_result(parsed, rpc_id)  # Pure function, no await
        return result
    except (httpx.HTTPStatusError, AuthenticationError) as e:
        # ... retry logic with await ...
```

#### 4.2.3 Convert `_refresh_auth_tokens()`

**Before:**
```python
def _refresh_auth_tokens(self) -> None:
    cookie_header = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
    headers = {**self._PAGE_FETCH_HEADERS, "Cookie": cookie_header}
    
    with httpx.Client(headers=headers, follow_redirects=True, timeout=15.0) as client:
        response = client.get(f"{self.BASE_URL}/")
        # ... parse response ...
```

**After:**
```python
async def _refresh_auth_tokens(self) -> None:
    cookie_header = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
    headers = {**self._PAGE_FETCH_HEADERS, "Cookie": cookie_header}
    
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15.0) as client:
        response = await client.get(f"{self.BASE_URL}/")
        # ... parse response (unchanged) ...
```

---

### Phase 3: API Methods Conversion

**Goal:** Convert all public API methods to async.

#### 4.3.1 Conversion Pattern

For most methods, the conversion follows this pattern:

```python
# BEFORE
def method_name(self, param1: str, param2: int = 10) -> dict | None:
    client = self._get_client()
    # ... logic ...
    result = self._call_rpc(RPC_ID, params, path)
    # ... process result ...
    return result

# AFTER
async def method_name(self, param1: str, param2: int = 10) -> dict | None:
    client = await self._get_client()
    # ... logic (unchanged) ...
    result = await self._call_rpc(RPC_ID, params, path)
    # ... process result (unchanged) ...
    return result
```

#### 4.3.2 Methods with Direct HTTP Calls

Some methods bypass `_call_rpc()` and use the client directly:

```python
# BEFORE (list_notebooks uses client directly)
def list_notebooks(self, debug: bool = False) -> list[Notebook]:
    client = self._get_client()
    body = self._build_request_body(self.RPC_LIST_NOTEBOOKS, params)
    url = self._build_url(self.RPC_LIST_NOTEBOOKS)
    response = client.post(url, content=body)
    # ...

# AFTER
async def list_notebooks(self, debug: bool = False) -> list[Notebook]:
    client = await self._get_client()
    body = self._build_request_body(self.RPC_LIST_NOTEBOOKS, params)
    url = self._build_url(self.RPC_LIST_NOTEBOOKS)
    response = await client.post(url, content=body)
    # ...
```

---

### Phase 4: Server Tool Handlers

**Goal:** Update all MCP tool handlers to async.

#### 4.4.1 Update `get_client()` in server.py

**Before:**
```python
def get_client() -> NotebookLMClient:
    global _client
    if _client is None:
        # ... initialization ...
        _client = NotebookLMClient(...)
    return _client
```

**After:**
```python
async def get_client() -> NotebookLMClient:
    global _client
    async with _get_lock():
        if _client is None:
            # ... initialization ...
            _client = NotebookLMClient(...)
            # Initialize client (triggers async auth token refresh)
            await _client._ensure_initialized()
    return _client
```

#### 4.4.2 Update Tool Decorator

**Before:**
```python
def logged_tool():
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # ... logging ...
            result = func(*args, **kwargs)
            # ... logging ...
            return result
        return mcp.tool()(wrapper)
    return decorator
```

**After:**
```python
def logged_tool():
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # ... logging ...
            result = await func(*args, **kwargs)
            # ... logging ...
            return result
        return mcp.tool()(wrapper)
    return decorator
```

#### 4.4.3 Update Tool Handlers

**Before:**
```python
@logged_tool()
def notebook_list(max_results: int = 100) -> dict[str, Any]:
    try:
        client = get_client()
        notebooks = client.list_notebooks()
        # ...
```

**After:**
```python
@logged_tool()
async def notebook_list(max_results: int = 100) -> dict[str, Any]:
    try:
        client = await get_client()
        notebooks = await client.list_notebooks()
        # ...
```

---

### Phase 5: Thread-Safe Shared State

#### 4.5.1 Conversation Cache Operations

**Before:**
```python
def _cache_conversation_turn(self, conversation_id: str, query: str, answer: str) -> None:
    if conversation_id not in self._conversation_cache:
        self._conversation_cache[conversation_id] = []
    turn_number = len(self._conversation_cache[conversation_id]) + 1
    turn = ConversationTurn(query=query, answer=answer, turn_number=turn_number)
    self._conversation_cache[conversation_id].append(turn)
```

**After:**
```python
async def _cache_conversation_turn(self, conversation_id: str, query: str, answer: str) -> None:
    async with self._cache_lock:
        if conversation_id not in self._conversation_cache:
            self._conversation_cache[conversation_id] = []
        turn_number = len(self._conversation_cache[conversation_id]) + 1
        turn = ConversationTurn(query=query, answer=answer, turn_number=turn_number)
        self._conversation_cache[conversation_id].append(turn)
```

#### 4.5.2 Request Counter

**Before:**
```python
self._reqid_counter += 100000
```

**After:**
```python
async with self._reqid_lock:
    self._reqid_counter += 100000
    reqid = self._reqid_counter
```

---

## 5. Method Inventory

### 5.1 Methods Requiring Async Conversion

#### Core Infrastructure (api_client.py)

| Method | Line | Priority | Complexity | Notes |
|--------|------|----------|------------|-------|
| `_get_client` | 443 | Critical | Low | Foundation for all HTTP |
| `_call_rpc` | 564 | Critical | Medium | Core RPC method |
| `_refresh_auth_tokens` | 357 | Critical | Medium | Uses temp client |
| `_update_cached_tokens` | 413 | High | Low | Called after refresh |
| `_try_reload_or_headless_auth` | 667 | High | Medium | Auth recovery |

#### Notebook Operations (api_client.py)

| Method | Line | Priority | Complexity |
|--------|------|----------|------------|
| `list_notebooks` | 769 | High | Low |
| `get_notebook` | 871 | High | Low |
| `get_notebook_summary` | 879 | Medium | Low |
| `create_notebook` | 1014 | High | Low |
| `rename_notebook` | 1028 | Medium | Low |
| `configure_chat` | 1034 | Medium | Low |
| `delete_notebook` | 1081 | Medium | Low |

#### Source Operations (api_client.py)

| Method | Line | Priority | Complexity |
|--------|------|----------|------------|
| `add_url_source` | 1251 | High | Medium |
| `add_text_source` | 1295 | High | Medium |
| `add_drive_source` | 1337 | High | Medium |
| `delete_source` | ~1170 | Medium | Low |
| `get_source_guide` | 907 | Medium | Low |
| `get_source_fulltext` | 929 | Medium | Low |
| `check_source_freshness` | ~1107 | Low | Low |
| `sync_drive_source` | ~1130 | Low | Low |

#### Query Operations (api_client.py)

| Method | Line | Priority | Complexity |
|--------|------|----------|------------|
| `query` | 1393 | Critical | High |
| `_cache_conversation_turn` | 738 | High | Low |
| `_build_conversation_history` | 705 | Low | None (pure) |
| `clear_conversation` | 745 | Low | Low |

#### Research Operations (api_client.py)

| Method | Line | Priority | Complexity |
|--------|------|----------|------------|
| `start_research` | 1659 | High | Medium |
| `poll_research` | 1716 | High | Medium |
| `import_research_sources` | 1862 | Medium | Medium |

#### Studio Operations (api_client.py)

| Method | Line | Priority | Complexity |
|--------|------|----------|------------|
| `create_audio_overview` | ~1960 | Medium | Medium |
| `create_video_overview` | ~2050 | Medium | Medium |
| `create_infographic` | ~2200 | Medium | Medium |
| `create_slide_deck` | ~2300 | Medium | Medium |
| `create_report` | ~2420 | Medium | Medium |
| `create_flashcards` | ~2530 | Medium | Medium |
| `create_quiz` | ~2600 | Medium | Medium |
| `poll_studio_status` | ~2100 | Medium | Low |
| `delete_studio_content` | ~2160 | Low | Low |

#### Mind Map Operations (api_client.py)

| Method | Line | Priority | Complexity |
|--------|------|----------|------------|
| `generate_mind_map` | ~2750 | Low | Medium |
| `save_mind_map` | ~2820 | Low | Low |
| `list_mind_maps` | ~2860 | Low | Low |
| `delete_mind_map` | ~2900 | Low | Low |

### 5.2 Methods NOT Requiring Changes (Pure Functions)

| Method | Line | Reason |
|--------|------|--------|
| `_build_request_body` | 462 | Pure string manipulation |
| `_build_url` | 480 | Pure string manipulation |
| `_parse_response` | 496 | Pure parsing |
| `_extract_rpc_result` | 542 | Pure extraction |
| `_extract_answer_from_chunk` | ~1590 | Pure parsing |
| `_parse_query_response` | ~1530 | Pure parsing |
| `_extract_source_ids_from_notebook` | 1505 | Pure extraction |
| `_extract_all_text` | 1004 | Pure recursion |

### 5.3 Server Tool Handlers (server.py)

**Total: ~40 handlers need conversion**

| Category | Count | Examples |
|----------|-------|----------|
| Notebook tools | 8 | `notebook_list`, `notebook_create`, `notebook_get`, etc. |
| Source tools | 10 | `notebook_add_url`, `source_delete`, `source_describe`, etc. |
| Query tools | 3 | `notebook_query`, `conversation_clear`, `conversation_history` |
| Research tools | 3 | `research_start`, `research_status`, `research_import` |
| Studio tools | 8 | `studio_create_audio`, `studio_status`, etc. |
| Mind map tools | 4 | `mindmap_generate`, `mindmap_save`, etc. |
| Auth tools | 2 | `refresh_auth`, `save_auth_tokens` |
| Config tools | 2 | `notebook_configure_chat`, etc. |

---

## 6. Code Changes Specification

### 6.1 File: `src/notebooklm_mcp/api_client.py`

#### 6.1.1 Import Changes

```python
# ADD at top of file
import asyncio

# CHANGE httpx import
import httpx  # No change needed, supports both sync and async
```

#### 6.1.2 Class Attribute Changes

```python
class NotebookLMClient:
    def __init__(self, cookies: dict[str, str], csrf_token: str = "", session_id: str = ""):
        self.cookies = cookies
        self.csrf_token = csrf_token
        self._client: httpx.AsyncClient | None = None  # CHANGE: Type hint
        self._session_id = session_id
        
        self._conversation_cache: dict[str, list[ConversationTurn]] = {}
        self._cache_lock: asyncio.Lock = asyncio.Lock()  # ADD
        
        self._reqid_counter = random.randint(100000, 999999)
        self._reqid_lock: asyncio.Lock = asyncio.Lock()  # ADD
        
        self._initialized: bool = False  # ADD
        self._init_lock: asyncio.Lock = asyncio.Lock()  # ADD
```

#### 6.1.3 Add Initialization Method

```python
async def _ensure_initialized(self) -> None:
    """Ensure client is initialized with valid auth tokens."""
    async with self._init_lock:
        if self._initialized:
            return
        if not self.csrf_token:
            await self._refresh_auth_tokens()
        self._initialized = True
```

#### 6.1.4 Client Cleanup Method

```python
async def close(self) -> None:
    """Close the HTTP client and release resources."""
    if self._client is not None:
        await self._client.aclose()
        self._client = None
```

### 6.2 File: `src/notebooklm_mcp/server.py`

#### 6.2.1 Import Changes

```python
# ADD
import asyncio
```

#### 6.2.2 Global State Changes

```python
# Global state
_client: NotebookLMClient | None = None
_client_lock: asyncio.Lock | None = None  # ADD
_query_timeout: float = float(os.environ.get("NOTEBOOKLM_QUERY_TIMEOUT", "120.0"))


def _get_lock() -> asyncio.Lock:  # ADD
    """Get or create the async lock for client initialization."""
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock
```

---

## 7. Migration Strategy

### 7.1 Phased Rollout

```
┌─────────────────────────────────────────────────────────────────┐
│                    MIGRATION PHASES                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Phase 1: Foundation (2-3 hours)                                │
│  ├── Add asyncio.Lock to server.py singleton                    │
│  ├── Add lock attributes to NotebookLMClient.__init__           │
│  └── Test: Verify existing sync behavior unchanged              │
│                                                                  │
│  Phase 2: Core HTTP (3-4 hours)                                 │
│  ├── Convert _get_client() to async                             │
│  ├── Convert _call_rpc() to async                               │
│  ├── Convert _refresh_auth_tokens() to async                    │
│  └── Test: Basic API call works                                 │
│                                                                  │
│  Phase 3: API Methods - Batch 1 (4-5 hours)                     │
│  ├── Notebook operations (list, get, create, delete)            │
│  ├── Source operations (add, delete, get)                       │
│  └── Test: CRUD operations work                                 │
│                                                                  │
│  Phase 4: API Methods - Batch 2 (3-4 hours)                     │
│  ├── Query operations                                           │
│  ├── Research operations                                        │
│  └── Test: Query and research work                              │
│                                                                  │
│  Phase 5: API Methods - Batch 3 (3-4 hours)                     │
│  ├── Studio operations                                          │
│  ├── Mind map operations                                        │
│  └── Test: Content generation works                             │
│                                                                  │
│  Phase 6: Server Handlers (4-6 hours)                           │
│  ├── Update logged_tool() decorator                             │
│  ├── Update all tool handler functions                          │
│  └── Test: Full integration                                     │
│                                                                  │
│  Phase 7: Testing & Polish (4-6 hours)                          │
│  ├── Update test_concurrent_http.py                             │
│  ├── Add async-specific tests                                   │
│  ├── Performance benchmarking                                   │
│  └── Documentation updates                                      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 Git Workflow

```bash
# Create feature branch from current
git checkout feature/http-concurrency

# Commit after each phase
git commit -m "Phase 1: Add asyncio locks for thread safety"
git commit -m "Phase 2: Convert core HTTP methods to async"
# ... etc


### 7.3 Rollback Plan

If issues arise during migration:

1. **Phase-level rollback:** Revert commits for that phase
2. **Full rollback:** Reset to `main` branch
3. **Hybrid approach:** Keep sync methods with `_sync` suffix for fallback

---

## 8. Testing Plan

### 8.1 Unit Tests

#### 8.1.1 New Test File: `tests/test_async_client.py`

```python
"""Unit tests for async NotebookLMClient."""
import pytest
import asyncio

@pytest.mark.asyncio
async def test_client_initialization():
    """Test async client initialization."""
    # ...

@pytest.mark.asyncio
async def test_concurrent_requests():
    """Test multiple concurrent API calls."""
    # ...

@pytest.mark.asyncio
async def test_conversation_cache_thread_safety():
    """Test conversation cache under concurrent access."""
    # ...
```

### 8.2 Integration Tests

#### 8.2.1 Update: `tests/test_concurrent_http.py`

```python
@pytest.mark.asyncio
async def test_true_concurrent_notebook_list():
    """Test truly concurrent notebook_list calls."""
    async with httpx.AsyncClient() as client:
        # Fire 20 concurrent requests
        tasks = [
            client.post(MCP_ENDPOINT, json={"tool": "notebook_list", "arguments": {}})
            for _ in range(20)
        ]
        
        start = time.time()
        responses = await asyncio.gather(*tasks)
        elapsed = time.time() - start
        
        # All should complete in roughly the same time (parallel)
        # Not 20x single request time (sequential)
        assert elapsed < 10.0  # Should be ~2-3 seconds, not 20+
```

### 8.3 Performance Benchmarks

| Metric | Current | Target | Test Method |
|--------|---------|--------|-------------|
| Sequential requests (10x) | ~30s | ~30s | No change expected |
| Concurrent requests (10x) | ~30s | ~5s | Parallel execution |
| Throughput (req/sec) | ~5 | ~50 | Load test |
| P99 latency | ~6s | ~3s | Percentile tracking |

### 8.4 Test Commands

```bash
# Run all tests
pytest tests/ -v

# Run async tests only
pytest tests/test_async_client.py -v

# Run concurrency tests with output
pytest tests/test_concurrent_http.py -v -s

# Run with coverage
pytest tests/ --cov=src/notebooklm_mcp --cov-report=html
```

---

## 9. Risks and Mitigations

### 9.1 Risk Matrix

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Breaking existing functionality | Medium | High | Comprehensive test coverage before changes |
| Race conditions in shared state | Medium | High | Use `asyncio.Lock` for all shared state |
| Connection pool exhaustion | Low | Medium | Configure `httpx.Limits` appropriately |
| Auth refresh conflicts | Medium | Medium | Lock auth operations |
| Performance regression | Low | Medium | Benchmark before/after |
| Incomplete async chain | High | High | Careful review of call chains |

### 9.2 Mitigation Details

#### 9.2.1 Incomplete Async Chain

**Problem:** Calling sync method from async context blocks event loop.

**Detection:**
```python
# Add runtime check (development only)
import asyncio

def _check_async_context():
    try:
        asyncio.get_running_loop()
        raise RuntimeError("Sync method called from async context!")
    except RuntimeError:
        pass  # No loop running, OK to use sync
```

**Prevention:** Code review checklist item for all method conversions.

#### 9.2.2 Connection Pool Exhaustion

**Configuration:**
```python
self._client = httpx.AsyncClient(
    limits=httpx.Limits(
        max_connections=100,          # Total connections
        max_keepalive_connections=20, # Persistent connections
        keepalive_expiry=30.0,        # Connection lifetime
    ),
)
```

#### 9.2.3 Auth Refresh Conflicts

**Solution:** Serialize auth operations:
```python
self._auth_lock: asyncio.Lock = asyncio.Lock()

async def _refresh_auth_tokens(self) -> None:
    async with self._auth_lock:
        # Only one refresh at a time
        # ...
```

---

## 10. Estimated Timeline

### 10.1 Time Estimates

| Phase | Description | Hours | Dependencies |
|-------|-------------|-------|--------------|
| 1 | Foundation (locks) | 2-3 | None |
| 2 | Core HTTP conversion | 3-4 | Phase 1 |
| 3 | API Methods Batch 1 | 4-5 | Phase 2 |
| 4 | API Methods Batch 2 | 3-4 | Phase 2 |
| 5 | API Methods Batch 3 | 3-4 | Phase 2 |
| 6 | Server Handlers | 4-6 | Phases 3-5 |
| 7 | Testing & Polish | 4-6 | Phase 6 |
| **Total** | | **24-32** | |

### 10.2 Timeline (Assuming Full-Time)

```
Day 1: Phases 1-2 (Foundation + Core HTTP)
Day 2: Phases 3-4 (API Methods Batches 1-2)
Day 3: Phases 5-6 (API Methods Batch 3 + Server)
Day 4: Phase 7 (Testing + Documentation)
Day 5: Buffer for issues, code review, final polish
```

### 10.3 Milestones

| Milestone | Criteria | Target |
|-----------|----------|--------|
| M1: Foundation Complete | Locks added, tests pass | Day 1 EOD |
| M2: Core Async Working | Basic API call works async | Day 1 EOD |
| M3: CRUD Operations | Notebook/source ops work | Day 2 EOD |
| M4: Full API Async | All API methods converted | Day 3 AM |
| M5: Server Integration | All tools work async | Day 3 EOD |
| M6: Testing Complete | All tests pass, benchmarks met | Day 4 EOD |
| M7: Ready for Review | Documentation complete | Day 5 |

---

## Appendix A: Quick Reference - Async Conversion Patterns

### A.1 Simple Method Conversion

```python
# BEFORE
def method(self, arg: str) -> dict:
    result = self._call_rpc(RPC_ID, [arg])
    return {"data": result}

# AFTER
async def method(self, arg: str) -> dict:
    result = await self._call_rpc(RPC_ID, [arg])
    return {"data": result}
```

### A.2 Method with Direct Client Usage

```python
# BEFORE
def method(self, arg: str) -> dict:
    client = self._get_client()
    response = client.post(url, content=body)
    return response.json()

# AFTER
async def method(self, arg: str) -> dict:
    client = await self._get_client()
    response = await client.post(url, content=body)
    return response.json()
```

### A.3 Method with Exception Handling

```python
# BEFORE
def method(self, arg: str) -> dict:
    try:
        result = self._call_rpc(RPC_ID, [arg])
        return {"status": "success", "data": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}

# AFTER
async def method(self, arg: str) -> dict:
    try:
        result = await self._call_rpc(RPC_ID, [arg])
        return {"status": "success", "data": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}
```

### A.4 Tool Handler Conversion

```python
# BEFORE
@logged_tool()
def tool_name(arg: str) -> dict[str, Any]:
    client = get_client()
    result = client.method(arg)
    return {"status": "success", "result": result}

# AFTER
@logged_tool()
async def tool_name(arg: str) -> dict[str, Any]:
    client = await get_client()
    result = await client.method(arg)
    return {"status": "success", "result": result}
```

---

## Appendix B: Checklist for Each Method

- [ ] Add `async` keyword to function definition
- [ ] Add `await` before `self._get_client()` calls
- [ ] Add `await` before `self._call_rpc()` calls
- [ ] Add `await` before `client.post()` / `client.get()` calls
- [ ] Add `await` before any calls to other async methods
- [ ] Update type hints if return type changes
- [ ] Add `async with self._lock:` for shared state access
- [ ] Test method individually
- [ ] Update any callers of this method

---

## Appendix C: Files to Modify Summary

| File | Changes |
|------|---------|
| `src/notebooklm_mcp/api_client.py` | Convert ~35 methods to async |
| `src/notebooklm_mcp/server.py` | Convert ~40 tool handlers, update `get_client()` |
| `src/notebooklm_mcp/__init__.py` | No changes |
| `src/notebooklm_mcp/auth.py` | No changes (file I/O, not HTTP) |
| `src/notebooklm_mcp/auth_cli.py` | No changes (CLI tool) |
| `src/notebooklm_mcp/constants.py` | No changes |
| `tests/test_concurrent_http.py` | Update tests for async |
| `tests/test_api_client.py` | Update tests for async |
| `pyproject.toml` | May need `pytest-asyncio` dependency |

---

*End of Document*
