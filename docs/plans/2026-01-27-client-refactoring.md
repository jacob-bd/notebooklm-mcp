# NotebookLMClient Refactoring Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

## 🔄 RESUME HERE - Current Status

**Last Commit:** 3a3ebcf - "refactor: Extract ResearchMixin from client.py"

**Completed:**
- ✅ Task 1: utils.py module created and tested (4 tests)
- ✅ Task 2: data_types.py module created and tested (4 tests)
- ✅ Task 3: errors.py module created and tested (7 tests)
- ✅ Task 4: base.py with BaseClient infrastructure (12 tests)
  - NotebookLMClient now inherits from BaseClient
  - ~587 lines of duplicated infrastructure code removed
- ✅ Task 5: NotebookMixin extracted (7 methods)
  - list_notebooks, get_notebook, get_notebook_summary, create_notebook, rename_notebook, configure_chat, delete_notebook
- ✅ Task 6: SharingMixin extracted (3 methods)
  - get_share_status, set_public_access, add_collaborator
- ✅ Task 7: SourceMixin extracted (10 methods)
  - check_source_freshness, sync_drive_source, delete_source, get_notebook_sources_with_types, add_url_source, add_text_source, add_drive_source, upload_file, get_source_guide, get_source_fulltext
- ✅ Task 8: ConversationMixin extracted (8 methods)
  - query, clear_conversation, get_conversation_history, _build_conversation_history, _cache_conversation_turn, _parse_query_response, _extract_answer_from_chunk, _extract_source_ids_from_notebook
- ✅ Task 9: ResearchMixin extracted (4 methods)
  - start_research, poll_research, import_research_sources, _parse_research_sources

**Test Status:** 64 passed, 20 skipped (E2E tests require auth)

**Reduction:** ~940 lines removed from client.py (now 2,362 lines, down from 3,300)

**Next Steps:**
- Task 10: StudioMixin (studio content generation - ~600 lines, 15+ methods)
- Task 11: DownloadMixin (download_audio/video/report etc. - ~300 lines)

**Goal:** Decompose the 4,513-line monolithic `NotebookLMClient` class into focused, maintainable modules using a mixin-based architecture while preserving 100% backward compatibility.


**Architecture:** Use mixin classes that inherit from a `BaseClient` containing HTTP/RPC infrastructure. The final `NotebookLMClient` composes all mixins via multiple inheritance, maintaining the exact same public API. Each mixin handles one domain (notebooks, sources, downloads, etc.).

**Tech Stack:** Python 3.11+, httpx, dataclasses, pytest

---

## Phase 1: Foundation (Create Support Modules)

### Task 1: Create utils.py module ✅ COMPLETED

**Files:**
- Created: `src/notebooklm_tools/core/utils.py` (130 lines)
- Created: `tests/core/test_utils.py` (4 tests)
- Modified: `src/notebooklm_tools/core/client.py`

**Step 1: Write the failing test**

```python
# tests/core/test_utils.py
from notebooklm_tools.core.utils import (
    parse_timestamp,
    extract_cookies_from_chrome_export,
    RPC_NAMES,
)

def test_parse_timestamp_valid():
    result = parse_timestamp([1700000000, 0])
    assert result == "2023-11-14T22:13:20Z"

def test_parse_timestamp_none():
    assert parse_timestamp(None) is None

def test_extract_cookies_header_string():
    result = extract_cookies_from_chrome_export("name=value; other=foo")
    assert result == {"name": "value", "other": "foo"}

def test_rpc_names_exists():
    assert "wXbhsf" in RPC_NAMES
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_utils.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'notebooklm_tools.core.utils'"

**Step 3: Create utils.py with utility functions**

```python
# src/notebooklm_tools/core/utils.py
"""Utility functions for NotebookLM API client."""

import json
import urllib.parse
from datetime import datetime, timezone
from typing import Any

# RPC ID to method name mapping for debug logging
RPC_NAMES = {
    "wXbhsf": "list_notebooks",
    "rLM1Ne": "get_notebook",
    "CCqFvf": "create_notebook",
    "s0tc2d": "rename_notebook",
    "WWINqb": "delete_notebook",
    "izAoDd": "add_source",
    "hizoJc": "get_source",
    "yR9Yof": "check_freshness",
    "FLmJqe": "sync_drive",
    "tGMBJ": "delete_source",
    "hPTbtc": "get_conversations",
    "hT54vc": "preferences",
    "ozz5Z": "subscription",
    "ZwVcOc": "settings",
    "VfAZjd": "get_summary",
    "tr032e": "get_source_guide",
    "Ljjv0c": "start_fast_research",
    "QA9ei": "start_deep_research",
    "e3bVqc": "poll_research",
    "LBwxtb": "import_research",
    "R7cb6c": "create_studio",
    "gArtLc": "poll_studio",
    "V5N4be": "delete_studio",
    "v9rmvd": "get_interactive_html",
    "yyryJe": "generate_mind_map",
    "CYK0Xb": "save_mind_map",
    "cFji9": "list_mind_maps",
    "AH0mwd": "delete_mind_map",
    "QDyure": "share_notebook",
    "JFMDGd": "get_share_status",
}


def _format_debug_json(data: Any, max_length: int = 2000) -> str:
    """Format data as pretty-printed JSON for debug logging."""
    try:
        formatted = json.dumps(data, indent=2, ensure_ascii=False)
        if len(formatted) > max_length:
            return formatted[:max_length] + "\n  ... (truncated)"
        return formatted
    except (TypeError, ValueError):
        result = str(data)
        if len(result) > max_length:
            return result[:max_length] + "... (truncated)"
        return result


def _decode_request_body(body: str) -> dict[str, Any]:
    """Decode URL-encoded request body and parse JSON structures."""
    result = {}
    try:
        parsed = urllib.parse.parse_qs(body.rstrip("&"))
        if "f.req" in parsed:
            f_req_raw = parsed["f.req"][0]
            try:
                f_req = json.loads(f_req_raw)
                result["f.req"] = f_req
                if isinstance(f_req, list) and len(f_req) > 0:
                    inner = f_req[0]
                    if isinstance(inner, list) and len(inner) > 0:
                        rpc_call = inner[0]
                        if isinstance(rpc_call, list) and len(rpc_call) >= 2:
                            result["rpc_id"] = rpc_call[0]
                            params_str = rpc_call[1]
                            if isinstance(params_str, str):
                                try:
                                    result["params"] = json.loads(params_str)
                                except json.JSONDecodeError:
                                    result["params"] = params_str
            except json.JSONDecodeError:
                result["f.req"] = f_req_raw
        if "at" in parsed:
            result["at"] = "(csrf_token)"
    except Exception:
        result["raw"] = body[:500] if len(body) > 500 else body
    return result


def _parse_url_params(url: str) -> dict[str, Any]:
    """Parse URL query parameters for debug display."""
    try:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        return {k: v[0] if len(v) == 1 else v for k, v in params.items()}
    except Exception:
        return {}


def parse_timestamp(ts_array: list | None) -> str | None:
    """Convert [seconds, nanoseconds] timestamp array to ISO format string."""
    if not ts_array or not isinstance(ts_array, list) or len(ts_array) < 1:
        return None
    try:
        seconds = ts_array[0]
        if not isinstance(seconds, (int, float)):
            return None
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OSError, OverflowError):
        return None


def extract_cookies_from_chrome_export(cookie_data: str | list[dict]) -> dict[str, str]:
    """Extract cookies from Chrome export format (JSON) or header string."""
    if isinstance(cookie_data, list):
        return {c.get("name"): c.get("value") for c in cookie_data if "name" in c and "value" in c}
    if not isinstance(cookie_data, str):
        return {}
    try:
        data = json.loads(cookie_data)
        if isinstance(data, list):
            return {c.get("name"): c.get("value") for c in data if "name" in c and "value" in c}
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except json.JSONDecodeError:
        pass
    cookies = {}
    for item in cookie_data.split(";"):
        if "=" in item:
            name, value = item.strip().split("=", 1)
            cookies[name] = value
    return cookies
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_utils.py -v`
Expected: PASS

**Step 5: Update client.py imports**

Replace lines 26-125 in client.py with imports from utils.py, keeping backward-compatible re-exports.

**Step 6: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

**Step 7: Commit** ✅ DONE (commit e08b276)

---

### Task 2: Create data_types.py module 🔄 IN PROGRESS

**Status:** Test written and failing as expected. Ready for Step 3 (create module).

**Files:**
- Create: `src/notebooklm_tools/core/data_types.py`
- Created: `tests/core/test_data_types.py` ✅
- Modify: `src/notebooklm_tools/core/client.py` (lines 174-280)

**Step 1: Write the failing test** ✅ DONE

**Step 2: Run test to verify it fails** ✅ DONE (ModuleNotFoundError as expected)

**Step 3: Create data_types.py** ⏭️ START HERE

```python
# tests/core/test_data_types.py
from notebooklm_tools.core.data_types import (
    ConversationTurn,
    Collaborator,
    ShareStatus,
    Notebook,
)

def test_conversation_turn():
    turn = ConversationTurn(query="What is AI?", answer="AI is...", turn_number=1)
    assert turn.query == "What is AI?"

def test_collaborator():
    collab = Collaborator(email="test@example.com", role="editor")
    assert collab.role == "editor"

def test_share_status():
    status = ShareStatus(is_public=True, access_level="public", collaborators=[])
    assert status.is_public is True

def test_notebook_url():
    nb = Notebook(id="abc-123", title="Test", source_count=0, sources=[])
    assert nb.url == "https://notebooklm.google.com/notebook/abc-123"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_data_types.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Create data_types.py**

```python
# src/notebooklm_tools/core/data_types.py
"""Dataclasses for NotebookLM API client."""

from dataclasses import dataclass


@dataclass
class ConversationTurn:
    """Represents a single turn in a conversation (query + response)."""
    query: str
    answer: str
    turn_number: int


@dataclass
class Collaborator:
    """A user with access to a notebook."""
    email: str
    role: str  # "owner", "editor", "viewer"
    is_pending: bool = False
    display_name: str | None = None


@dataclass
class ShareStatus:
    """Current sharing state of a notebook."""
    is_public: bool
    access_level: str  # "restricted" or "public"
    collaborators: list[Collaborator]
    public_link: str | None = None


@dataclass
class Notebook:
    """Represents a NotebookLM notebook."""
    id: str
    title: str
    source_count: int
    sources: list[dict]
    is_owned: bool = True
    is_shared: bool = False
    created_at: str | None = None
    modified_at: str | None = None

    @property
    def url(self) -> str:
        return f"https://notebooklm.google.com/notebook/{self.id}"

    @property
    def ownership(self) -> str:
        return "owned" if self.is_owned else "shared_with_me"
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_data_types.py -v`
Expected: PASS

**Step 5: Update client.py imports**

**Step 6: Run full test suite**

**Step 7: Commit**

```bash
git add src/notebooklm_tools/core/data_types.py tests/core/test_data_types.py
git commit -m "refactor: extract dataclasses to data_types.py

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

### Task 3: Create errors.py module for artifact exceptions

**Files:**
- Create: `src/notebooklm_tools/core/errors.py`
- Modify: `src/notebooklm_tools/core/client.py` (lines 126-165)

**Step 1: Write the failing test**

```python
# tests/core/test_errors.py
from notebooklm_tools.core.errors import (
    NotebookLMError,
    ArtifactError,
    ArtifactNotReadyError,
    ArtifactParseError,
    ArtifactDownloadError,
)

def test_artifact_not_ready_error():
    err = ArtifactNotReadyError("audio", "abc-123")
    assert "audio" in str(err)
    assert "abc-123" in str(err)

def test_artifact_parse_error():
    err = ArtifactParseError("video", details="Invalid structure")
    assert "video" in str(err)
    assert "Invalid structure" in str(err)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_errors.py -v`
Expected: FAIL

**Step 3: Create errors.py**

```python
# src/notebooklm_tools/core/errors.py
"""Exception classes for NotebookLM API artifacts."""


class NotebookLMError(Exception):
    """Base exception for NotebookLM errors."""
    pass


class ArtifactError(NotebookLMError):
    """Base exception for artifact errors."""
    pass


class ArtifactNotReadyError(ArtifactError):
    """Raised when an artifact is not ready for download."""
    def __init__(self, artifact_type: str, artifact_id: str | None = None):
        msg = f"{artifact_type} is not ready or does not exist"
        if artifact_id:
            msg += f" (ID: {artifact_id})"
        super().__init__(msg)


class ArtifactParseError(ArtifactError):
    """Raised when artifact metadata cannot be parsed."""
    def __init__(self, artifact_type: str, details: str = "", cause: Exception | None = None):
        msg = f"Failed to parse {artifact_type} metadata: {details}"
        super().__init__(msg)
        self.__cause__ = cause


class ArtifactDownloadError(ArtifactError):
    """Raised when artifact download fails."""
    def __init__(self, artifact_type: str, details: str = ""):
        super().__init__(f"Failed to download {artifact_type}: {details}")


class ArtifactNotFoundError(ArtifactError):
    """Raised when a specific artifact ID is not found."""
    def __init__(self, artifact_id: str, artifact_type: str = "artifact"):
        super().__init__(f"{artifact_type} not found: {artifact_id}")
        self.artifact_id = artifact_id
        self.artifact_type = artifact_type


class ClientAuthenticationError(Exception):
    """Raised when authentication fails (HTTP 401/403 or RPC Error 16)."""
    pass
```

**Step 4: Run test to verify it passes**

**Step 5: Update client.py imports**

**Step 6: Run full test suite**

**Step 7: Commit**

---

### Task 4: Create base.py with BaseClient class

**Files:**
- Create: `src/notebooklm_tools/core/base.py`
- Modify: `src/notebooklm_tools/core/client.py`

**Step 1: Write the failing test**

```python
# tests/core/test_base.py
from unittest.mock import patch, MagicMock
from notebooklm_tools.core.base import BaseClient

def test_base_client_init():
    with patch.object(BaseClient, '_refresh_auth_tokens'):
        client = BaseClient(cookies={"test": "cookie"})
        assert client.cookies == {"test": "cookie"}
        assert client._client is None

def test_build_request_body():
    with patch.object(BaseClient, '_refresh_auth_tokens'):
        client = BaseClient(cookies={}, csrf_token="test_token")
        body = client._build_request_body("testRpc", ["param1"])
        assert "f.req=" in body
        assert "at=" in body
```

**Step 2: Run test to verify it fails**

**Step 3: Create base.py with infrastructure methods**

Extract the following from client.py to base.py:
- Class constants (RPC IDs, URLs, headers)
- `__init__`, `__enter__`, `__exit__`, `close`
- `_get_httpx_cookies`, `_get_cookie_header`
- `_get_client`, `_get_async_client`
- `_build_request_body`, `_build_url`
- `_parse_response`, `_extract_rpc_result`
- `_call_rpc` (with all retry logic)
- `_refresh_auth_tokens`, `_update_cached_tokens`
- `_try_reload_or_headless_auth`

**Step 4-7: Test, update imports, run full suite, commit**

---

## Phase 2: Extract Operation Mixins

> **Architecture Note:** Each mixin inherits from `BaseClient` and contains domain-specific methods. The final `NotebookLMClient` uses multiple inheritance to compose all mixins.

---

### Task 5: Create notebooks.py mixin

**Files:**
- Create: `src/notebooklm_tools/core/notebooks.py`
- Create: `tests/core/test_notebooks.py`
- Modify: `src/notebooklm_tools/core/client.py`

**Methods to extract (7 methods, ~340 lines):**
- `list_notebooks` (lines 148-248)
- `get_notebook` (lines 250-256)
- `get_notebook_summary` (lines 258-284)
- `create_notebook` (lines 393-406)
- `rename_notebook` (lines 408-412)
- `configure_chat` (lines 414-458)
- `delete_notebook` (lines 460-484)

**Test Strategy:** Mock `_call_rpc` and verify correct RPC IDs are called with expected parameters.

---

### Task 6: Create sharing.py mixin

**Files:**
- Create: `src/notebooklm_tools/core/sharing.py`
- Create: `tests/core/test_sharing.py`
- Modify: `src/notebooklm_tools/core/client.py`

**Methods to extract (3 methods, ~140 lines):**
- `get_share_status` (lines 490-553)
- `set_public_access` (lines 555-580)
- `add_collaborator` (lines 582-620)

---

### Task 7: Create sources.py mixin

**Files:**
- Create: `src/notebooklm_tools/core/sources.py`
- Create: `tests/core/test_sources.py`
- Modify: `src/notebooklm_tools/core/client.py`

**Methods to extract (12 methods, ~400 lines):**
- `get_source_guide` (lines 286-306)
- `get_source_fulltext` (lines 308-381)
- `_extract_all_text` (lines 383-391)
- `check_source_freshness` (lines 622-643)
- `sync_drive_source` (lines 645-681)
- `delete_source` (lines 683-710)
- `get_notebook_sources_with_types` (lines 712-765)
- `add_url_source` (lines 768-815)
- `add_text_source` (lines 817-853)
- `add_drive_source` (lines 855-910)
- `upload_file` (lines 912-948)

---

### Task 8: Create query.py mixin

**Files:**
- Create: `src/notebooklm_tools/core/query.py`
- Create: `tests/core/test_query.py`
- Modify: `src/notebooklm_tools/core/client.py`

**Methods to extract (7 methods, ~230 lines):**
- `_build_conversation_history` (lines 84-110)
- `_cache_conversation_turn` (lines 112-122)
- `clear_conversation` (lines 124-130)
- `get_conversation_history` (lines 132-142)
- `_extract_source_ids_from_notebook` (lines 1355-1381)
- `_parse_query_response` (lines 1383-1444)
- `_extract_answer_from_chunk` (lines 1446-1504)

**Note:** The `query()` method needs to be located - it may be a docstring issue.

---

### Task 9: Create research.py mixin

**Files:**
- Create: `src/notebooklm_tools/core/research.py`
- Create: `tests/core/test_research.py`
- Modify: `src/notebooklm_tools/core/client.py`

**Methods to extract (3 methods, ~300 lines):**
- `start_research` (lines 1506-1563)
- `poll_research` (lines 1565-1710)
- `import_research_sources` (lines 1713-1800)

---

### Task 10: Create studio.py mixin

**Files:**
- Create: `src/notebooklm_tools/core/studio.py`
- Create: `tests/core/test_studio.py`
- Modify: `src/notebooklm_tools/core/client.py`

**Methods to extract (11 methods, ~600 lines):**
- `create_audio_overview` (lines 1802-1870)
- `create_video_overview` (lines 1872-1939)
- `poll_studio_status` (lines 1941-2080)
- `get_studio_status` (lines 2083-2085)
- `delete_studio_artifact` (lines 2087-2114)
- `create_infographic` (lines 2156-2214)
- `create_slide_deck` (lines 2216-2273)
- `create_report` (lines 2275-2390)
- `create_flashcards` (lines 2392-2454)
- `create_quiz` (lines 2456-2517)
- `create_data_table` (lines 2519-2572)

---

### Task 11: Create mind_maps.py mixin

**Files:**
- Create: `src/notebooklm_tools/core/mind_maps.py`
- Create: `tests/core/test_mind_maps.py`
- Modify: `src/notebooklm_tools/core/client.py`

**Methods to extract (4 methods, ~180 lines):**
- `generate_mind_map` (lines 2574-2629)
- `save_mind_map` (lines 2631-2691)
- `list_mind_maps` (lines 2693-2744)
- `delete_mind_map` (lines 2116-2154)

---

### Task 12: Create downloads.py mixin

**Files:**
- Create: `src/notebooklm_tools/core/downloads.py`
- Create: `tests/core/test_downloads.py`
- Modify: `src/notebooklm_tools/core/client.py`

**Methods to extract (18 methods, ~850 lines):**
- `_download_url` (lines 954-1076)
- `_list_raw` (lines 1078-1105)
- `download_audio` (lines 1107-1174)
- `download_video` (lines 1176-1353)
- `download_infographic` (lines 2755-2810)
- `download_slide_deck` (lines 2813-2868)
- `download_report` (lines 2871-2926)
- `download_mind_map` (lines 2928-2981)
- `download_data_table` (lines 3185-3240)
- `download_quiz` (needs location)
- `download_flashcards` (needs location)
- `_get_artifact_content` (lines 3242-3297)
- `_extract_app_data` (lines 3299-3375)
- `_format_quiz_markdown` (lines 3377-3405)
- `_format_flashcards_markdown` (lines 3407-3433)
- `_format_interactive_content` (lines 3435-3471)
- `_download_interactive_artifact` (lines 3473-3563)
- `_extract_cell_text` (lines 2983-3025)
- `_parse_data_table` (lines 3027-3183)

---

## Phase 2.5: Final Integration

Before finalizing the architecture, create a comprehensive test suite to catch any regressions.

### Task 12.5: Create import verification tests

**Files:**
- Create: `tests/test_imports.py`

**Purpose:** Verify all backward-compatible imports work correctly.

```python
# tests/test_imports.py
"""Verify all backward-compatible imports from client.py still work."""

def test_main_client_import():
    """Test the primary import path."""
    from notebooklm_tools.core.client import NotebookLMClient
    assert NotebookLMClient is not None

def test_utility_imports():
    """Test utility function imports."""
    from notebooklm_tools.core.client import (
        parse_timestamp,
        extract_cookies_from_chrome_export,
    )
    assert parse_timestamp is not None
    assert extract_cookies_from_chrome_export is not None

def test_dataclass_imports():
    """Test dataclass imports."""
    from notebooklm_tools.core.client import (
        Notebook,
        ConversationTurn,
        Collaborator,
        ShareStatus,
    )
    assert Notebook is not None

def test_exception_imports():
    """Test exception class imports."""
    from notebooklm_tools.core.client import (
        NotebookLMError,
        ArtifactError,
        ArtifactNotReadyError,
        ArtifactParseError,
        ArtifactDownloadError,
        AuthenticationError,
    )
    assert ArtifactNotReadyError is not None

def test_constant_imports():
    """Test constant imports."""
    from notebooklm_tools.core.client import (
        DEFAULT_TIMEOUT,
        SOURCE_ADD_TIMEOUT,
    )
    assert DEFAULT_TIMEOUT == 30.0
    assert SOURCE_ADD_TIMEOUT == 120.0

def test_package_level_import():
    """Test package-level import."""
    from notebooklm_tools import NotebookLMClient
    assert NotebookLMClient is not None

def test_mcp_server_imports():
    """Test imports used by MCP server."""
    from notebooklm_tools.core.client import (
        NotebookLMClient,
        extract_cookies_from_chrome_export,
        parse_timestamp,
    )
    assert all([NotebookLMClient, extract_cookies_from_chrome_export, parse_timestamp])

def test_cli_download_imports():
    """Test imports used by CLI download commands."""
    from notebooklm_tools.core.client import (
        NotebookLMClient,
        ArtifactNotReadyError,
        ArtifactError,
    )
    assert all([NotebookLMClient, ArtifactNotReadyError, ArtifactError])
```

---

### Task 12.6: Create CLI smoke tests

**Files:**
- Create: `tests/test_cli_smoke.py`

**Purpose:** Verify CLI commands load without import errors.

```python
# tests/test_cli_smoke.py
"""Smoke tests for CLI commands - verify they load without import errors."""
import subprocess
import sys

def test_cli_help():
    """Test that CLI --help works."""
    result = subprocess.run(
        [sys.executable, "-m", "notebooklm_tools.cli.main", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "notebook" in result.stdout.lower()

def test_cli_notebook_help():
    """Test notebook subcommand help."""
    result = subprocess.run(
        [sys.executable, "-m", "notebooklm_tools.cli.main", "notebook", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0

def test_cli_download_help():
    """Test download subcommand help."""
    result = subprocess.run(
        [sys.executable, "-m", "notebooklm_tools.cli.main", "download", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0

def test_cli_studio_help():
    """Test studio subcommand help."""
    result = subprocess.run(
        [sys.executable, "-m", "notebooklm_tools.cli.main", "studio", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0

def test_cli_share_help():
    """Test share subcommand help."""
    result = subprocess.run(
        [sys.executable, "-m", "notebooklm_tools.cli.main", "share", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
```

---

### Task 12.7: Create MCP tool verification tests

**Files:**
- Create: `tests/test_mcp_tools.py`

**Purpose:** Verify all MCP tools are registered and callable.

```python
# tests/test_mcp_tools.py
"""Verify MCP tools are properly registered."""
from unittest.mock import patch, MagicMock

def test_mcp_server_imports():
    """Test that MCP server module imports without errors."""
    # This will fail if any import is broken
    from notebooklm_tools.mcp import server
    assert server is not None

def test_mcp_tools_registered():
    """Verify expected tools are defined in server module."""
    from notebooklm_tools.mcp import server

    expected_tools = [
        "notebook_list",
        "notebook_create",
        "notebook_get",
        "notebook_delete",
        "notebook_query",
        "source_describe",
        "source_get_content",
        "source_delete",
        "download_audio",
        "download_video",
        "download_report",
        "studio_status",
        "research_start",
        "research_status",
        "audio_overview_create",
        "video_overview_create",
    ]

    for tool_name in expected_tools:
        assert hasattr(server, tool_name), f"Missing tool: {tool_name}"

def test_get_client_function():
    """Test that get_client function exists and is callable."""
    from notebooklm_tools.mcp.server import get_client
    assert callable(get_client)
```

---

### Task 12.8: Create method signature verification

**Files:**
- Create: `tests/test_method_signatures.py`

**Purpose:** Verify all public method signatures are preserved.

```python
# tests/test_method_signatures.py
"""Verify public method signatures are preserved after refactoring."""
import inspect
from unittest.mock import patch

def test_notebooklm_client_methods():
    """Verify all expected public methods exist on NotebookLMClient."""
    with patch('notebooklm_tools.core.base.BaseClient._refresh_auth_tokens'):
        from notebooklm_tools.core.client import NotebookLMClient

        expected_methods = [
            # Notebook operations
            "list_notebooks",
            "get_notebook",
            "create_notebook",
            "rename_notebook",
            "delete_notebook",
            "configure_chat",
            "get_notebook_summary",
            # Source operations
            "add_url_source",
            "add_text_source",
            "add_drive_source",
            "upload_file",
            "get_source_guide",
            "get_source_fulltext",
            "check_source_freshness",
            "sync_drive_source",
            "delete_source",
            "get_notebook_sources_with_types",
            # Sharing
            "get_share_status",
            "set_public_access",
            "add_collaborator",
            # Query
            "clear_conversation",
            "get_conversation_history",
            # Research
            "start_research",
            "poll_research",
            "import_research_sources",
            # Studio
            "create_audio_overview",
            "create_video_overview",
            "create_infographic",
            "create_slide_deck",
            "create_report",
            "create_flashcards",
            "create_quiz",
            "create_data_table",
            "poll_studio_status",
            "get_studio_status",
            "delete_studio_artifact",
            # Mind maps
            "generate_mind_map",
            "save_mind_map",
            "list_mind_maps",
            "delete_mind_map",
            # Downloads
            "download_audio",
            "download_video",
            "download_infographic",
            "download_slide_deck",
            "download_report",
            "download_mind_map",
            "download_data_table",
            "download_quiz",
            "download_flashcards",
            # Lifecycle
            "close",
        ]

        for method_name in expected_methods:
            assert hasattr(NotebookLMClient, method_name), f"Missing method: {method_name}"
            method = getattr(NotebookLMClient, method_name)
            assert callable(method), f"Not callable: {method_name}"
```

---

## Phase 3: Finalize Architecture

### Task 13: Compose NotebookLMClient facade

**Files:**
- Modify: `src/notebooklm_tools/core/client.py`

**Final client.py structure:**

```python
"""NotebookLM API Client - Facade combining all operation mixins."""

from .base import BaseClient
from .notebooks import NotebookOperationsMixin
from .sources import SourceOperationsMixin
from .sharing import SharingOperationsMixin
from .query import QueryOperationsMixin
from .research import ResearchOperationsMixin
from .studio import StudioOperationsMixin
from .downloads import DownloadOperationsMixin
from .mind_maps import MindMapOperationsMixin

# Re-export for backward compatibility
from .utils import parse_timestamp, extract_cookies_from_chrome_export, RPC_NAMES
from .data_types import Notebook, ConversationTurn, Collaborator, ShareStatus
from .errors import (
    NotebookLMError,
    ArtifactError,
    ArtifactNotReadyError,
    ArtifactParseError,
    ArtifactDownloadError,
    ArtifactNotFoundError,
    ClientAuthenticationError as AuthenticationError,
)

# Timeout configuration
DEFAULT_TIMEOUT = 30.0
SOURCE_ADD_TIMEOUT = 120.0


class NotebookLMClient(
    NotebookOperationsMixin,
    SourceOperationsMixin,
    SharingOperationsMixin,
    QueryOperationsMixin,
    ResearchOperationsMixin,
    StudioOperationsMixin,
    DownloadOperationsMixin,
    MindMapOperationsMixin,
    BaseClient,
):
    """NotebookLM API Client.

    This class provides programmatic access to NotebookLM (notebooklm.google.com).
    It combines operation mixins for notebooks, sources, sharing, queries,
    research, studio content, downloads, and mind maps.

    Example:
        client = NotebookLMClient(cookies={"...": "..."})
        notebooks = client.list_notebooks()
    """
    pass
```

---

### Task 14: Update __init__.py exports

**Files:**
- Modify: `src/notebooklm_tools/core/__init__.py`

Ensure all public APIs are exported for backward compatibility.

---

### Task 15: Remove duplicate close() methods

**Files:**
- Modify: Multiple mixin files

Ensure `close()` is only defined in `BaseClient`, not duplicated in mixins.

---

## Phase 4: Documentation and Cleanup

### Task 16: Update CLAUDE.md architecture section

**Files:**
- Modify: `CLAUDE.md`

Update the Architecture section to reflect the new module structure.

---

### Task 17: Add module docstrings

**Files:**
- Modify: All new `.py` files

Add comprehensive module-level docstrings explaining purpose and usage.

---

### Task 18: Final verification

**Step 1: Run full test suite**

```bash
uv run pytest tests/ -v
```

**Step 2: Verify MCP server works**

```bash
# Test MCP tools
uv run notebooklm-mcp --help
```

**Step 3: Verify CLI works**

```bash
nlm notebook list
```

---

## Verification

After completing all tasks:

1. **Run test suite**: `uv run pytest tests/ -v`
2. **Run type checker**: `uv run pyright src/notebooklm_tools/core/`
3. **Verify imports**: `python -c "from notebooklm_tools.core.client import NotebookLMClient; print('OK')"`
4. **Test MCP server**: Start server and test notebook_list tool
5. **Check file sizes**: Each new module should be 100-500 lines

---

## Backward Compatibility: Critical Import Locations

These files import from `notebooklm_tools.core.client` and MUST continue working:

| File | Imports |
|------|---------|
| `src/notebooklm_tools/__init__.py` | `NotebookLMClient` |
| `src/notebooklm_tools/mcp/server.py` | `NotebookLMClient`, `extract_cookies_from_chrome_export`, `parse_timestamp` |
| `src/notebooklm_tools/cli/main.py` | `NotebookLMClient` |
| `src/notebooklm_tools/cli/utils.py` | `NotebookLMClient`, `NotebookLMError` |
| `src/notebooklm_tools/cli/commands/download.py` | `NotebookLMClient`, `ArtifactNotReadyError`, `ArtifactError` |
| `src/notebooklm_tools/cli/commands/*.py` | `NotebookLMClient` |
| `src/notebooklm_tools/core/alias.py` | `NotebookLMClient` |
| `tests/test_api_client.py` | `NotebookLMClient`, imports `AuthenticationError` from exceptions.py |

**Critical Note:** The test file imports `AuthenticationError` from `notebooklm_tools.core.exceptions`, NOT from client.py. This distinction must be preserved.

---

---

## Phase 5: Server.py Refactoring

After client.py is stable and tested, refactor the MCP server using the same modular pattern.

### Task 19: Analyze server.py structure

**Current State:**
- 2,396 lines
- 45+ MCP tool functions
- All tools in one file

**Proposed Structure:**
```
src/notebooklm_tools/mcp/
├── server.py           # Main server setup, health check, get_client()
├── tools/
│   ├── __init__.py     # Re-export all tools for registration
│   ├── notebooks.py    # notebook_list, notebook_create, notebook_get, etc.
│   ├── sources.py      # source_describe, source_get_content, source_delete, etc.
│   ├── sharing.py      # notebook_share_status, notebook_share_public, etc.
│   ├── research.py     # research_start, research_status, research_import
│   ├── studio.py       # audio_overview_create, video_overview_create, etc.
│   ├── downloads.py    # download_audio, download_video, download_report, etc.
│   └── auth.py         # save_auth_tokens, refresh_auth
└── utils.py            # Helper functions like _compact_research_result
```

---

### Task 20: Create server tools module structure

**Files:**
- Create: `src/notebooklm_tools/mcp/tools/__init__.py`
- Create: `src/notebooklm_tools/mcp/tools/notebooks.py`

**Step 1:** Create tools directory with __init__.py
**Step 2:** Extract notebook tools (6 functions)
**Step 3:** Verify MCP server still works
**Step 4:** Commit

---

### Task 21: Extract remaining tool modules

Extract in order:
1. `sources.py` - 5 tools
2. `sharing.py` - 3 tools
3. `downloads.py` - 9 tools
4. `research.py` - 3 tools
5. `studio.py` - 10 tools
6. `auth.py` - 2 tools

---

### Task 22: Finalize server.py facade

**Final server.py structure:**

```python
"""NotebookLM MCP Server - Tool registration and server setup."""

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

# Import and register all tools
from .tools import (
    notebooks,
    sources,
    sharing,
    downloads,
    research,
    studio,
    auth,
)

mcp = FastMCP("notebooklm-mcp")

# Register tools from each module
# (Each module registers its tools on import)

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})

def main():
    mcp.run()
```

---

### Task 23: Run comprehensive tests

**Step 1:** Run all tests including new integration tests
**Step 2:** Test each MCP tool via MCP Inspector or client
**Step 3:** Verify CLI still works
**Step 4:** Final commit

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Breaking changes | Maintain re-exports in client.py |
| Circular imports | Only base.py imports from other core modules |
| Method resolution order | BaseClient is last in inheritance chain |
| Missing methods | Run pyright after each task |
| Exception naming collision | Use `ClientAuthenticationError` in errors.py, alias to `AuthenticationError` in client.py |
