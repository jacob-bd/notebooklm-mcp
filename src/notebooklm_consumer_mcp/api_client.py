#!/usr/bin/env python3
"""
POC for Consumer NotebookLM (notebooklm.google.com) internal API.

This is a research POC - NOT for production use. The API is undocumented
and may change at any time.

IMPORTANT: This is a SEPARATE system from NotebookLM Enterprise (Vertex AI).
- Consumer: notebooklm.google.com (this POC)
- Enterprise: vertexaisearch.cloud.google.com/notebooklm/ (the MCP server)

## API Discovery Summary

Endpoint: https://notebooklm.google.com/_/LabsTailwindUi/data/batchexecute
Method: POST (application/x-www-form-urlencoded)

Request format:
  - f.req: URL-encoded JSON [[["rpc_id", "params_json", null, "generic"]]]
  - at: CSRF token (from WIZ_global_data.SNlM0e in page)
  - URL params: rpcids, source-path, bl (build), f.sid, hl, rt

Known RPC IDs:
  - wXbhsf: List notebooks with sources (returns ALL notebooks, filtering is client-side)
  - rLM1Ne: Get notebook details
  - CCqFvf: Create new notebook
  - izAoDd: Add source (URL, text, or Drive) - unified RPC for all source types
  - hPTbtc: Get conversation IDs
  - hT54vc: User preferences (filter settings)
  - ZwVcOc: Settings/preferences
  - ozz5Z: Subscription info

Query Endpoint (streaming, NOT batchexecute):
  - POST /_/LabsTailwindUi/data/google.internal.labs.tailwind.orchestration.v1.LabsTailwindOrchestrationService/GenerateFreeFormStreamed
  - Params: [[[source_ids]], query_text, null, [2, null, [1]], conversation_id]
  - Response: Streaming JSON with thinking steps + final answer with citations

Source Types (via izAoDd):
  - URL/YouTube: [null, null, [urls], null, null, null, null, null, null, null, 1]
  - Pasted Text: [null, [title, content], null, 2, null, null, null, null, null, null, 1]
  - Google Drive: [[doc_id, mime_type, 1, title], null, null, null, null, null, null, null, null, null, 1]

Filtering:
  - "My notebooks" vs "Shared with me" filtering is done CLIENT-SIDE
  - The list response includes ownership info that can be used to filter
  - No API parameter to filter by ownership

Response format:
  - Starts with ")]}'" (anti-XSSI prefix)
  - Followed by byte count, then JSON array

Authentication:
  - Cookie-based: SID, SSID, HSID, APISID, SAPISID, OSID, etc.
  - CSRF token required (changes per page load)
  - Session ID (f.sid) required

## Usage

1. Log in to notebooklm.google.com in Chrome
2. Open DevTools > Network tab
3. Find any POST to /_/LabsTailwindUi/data/batchexecute
4. Copy the Cookie header value
5. Copy CSRF token (at= in request body) and session ID (f.sid= in URL)
6. Run: python consumer_notebooklm.py 'COOKIE_HEADER'
"""

import json
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx


# Ownership constants (from metadata position 0)
OWNERSHIP_MINE = 1          # Created by me
OWNERSHIP_SHARED = 2        # Shared with me by someone else


def parse_timestamp(ts_array: list | None) -> str | None:
    """Convert [seconds, nanoseconds] timestamp array to ISO format string.

    Args:
        ts_array: Array like [1766621224, 821240000] (seconds, nanos since epoch)

    Returns:
        ISO format datetime string like "2025-12-24T15:27:04Z" or None if invalid
    """
    if not ts_array or not isinstance(ts_array, list) or len(ts_array) < 1:
        return None

    try:
        seconds = ts_array[0]
        if not isinstance(seconds, (int, float)):
            return None

        # Convert to datetime
        dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OSError, OverflowError):
        return None


@dataclass
class ConsumerNotebook:
    """Represents a consumer NotebookLM notebook."""

    id: str
    title: str
    source_count: int
    sources: list[dict]
    is_owned: bool = True     # True if owned by user, False if shared with user
    is_shared: bool = False   # True if shared with others (for owned notebooks)
    created_at: str | None = None   # ISO format timestamp
    modified_at: str | None = None  # ISO format timestamp

    @property
    def url(self) -> str:
        return f"https://notebooklm.google.com/notebook/{self.id}"

    @property
    def ownership(self) -> str:
        """Return human-readable ownership status."""
        if self.is_owned:
            return "owned"
        return "shared_with_me"


class ConsumerNotebookLMClient:
    """Client for consumer NotebookLM internal API."""

    BASE_URL = "https://notebooklm.google.com"
    BATCHEXECUTE_URL = f"{BASE_URL}/_/LabsTailwindUi/data/batchexecute"

    # Known RPC IDs
    RPC_LIST_NOTEBOOKS = "wXbhsf"
    RPC_GET_NOTEBOOK = "rLM1Ne"
    RPC_CREATE_NOTEBOOK = "CCqFvf"
    RPC_RENAME_NOTEBOOK = "s0tc2d"
    RPC_DELETE_NOTEBOOK = "WWINqb"
    RPC_ADD_SOURCE = "izAoDd"  # Used for URL, text, and Drive sources
    RPC_GET_SOURCE = "hizoJc"  # Get source details
    RPC_CHECK_FRESHNESS = "yR9Yof"  # Check if Drive source is stale
    RPC_SYNC_DRIVE = "FLmJqe"  # Sync Drive source with latest content
    RPC_GET_CONVERSATIONS = "hPTbtc"
    RPC_PREFERENCES = "hT54vc"
    RPC_SUBSCRIPTION = "ozz5Z"
    RPC_SETTINGS = "ZwVcOc"

    # Research RPCs (source discovery)
    RPC_START_FAST_RESEARCH = "Ljjv0c"  # Start Fast Research (Web or Drive)
    RPC_START_DEEP_RESEARCH = "QA9ei"   # Start Deep Research (Web only)
    RPC_POLL_RESEARCH = "e3bVqc"        # Poll research results
    RPC_IMPORT_RESEARCH = "LBwxtb"      # Import research sources

    # Research source types
    RESEARCH_SOURCE_WEB = 1
    RESEARCH_SOURCE_DRIVE = 2

    # Research modes
    RESEARCH_MODE_FAST = 1
    RESEARCH_MODE_DEEP = 5

    # Research result types (from poll response)
    RESULT_TYPE_WEB = 1
    RESULT_TYPE_GOOGLE_DOC = 2
    RESULT_TYPE_GOOGLE_SLIDES = 3
    RESULT_TYPE_DEEP_REPORT = 5
    RESULT_TYPE_GOOGLE_SHEETS = 8

    # Studio RPCs (Audio/Video Overviews)
    RPC_CREATE_STUDIO = "R7cb6c"   # Create Audio or Video Overview
    RPC_POLL_STUDIO = "gArtLc"     # Poll for studio content status
    RPC_DELETE_STUDIO = "V5N4be"   # Delete Audio or Video Overview

    # Studio content types
    STUDIO_TYPE_AUDIO = 1
    STUDIO_TYPE_VIDEO = 3

    # Audio Overview formats
    AUDIO_FORMAT_DEEP_DIVE = 1   # Lively conversation between two hosts
    AUDIO_FORMAT_BRIEF = 2       # Bite-sized overview of core ideas
    AUDIO_FORMAT_CRITIQUE = 3    # Expert review offering feedback
    AUDIO_FORMAT_DEBATE = 4      # Thoughtful debate with different perspectives

    # Audio Overview lengths
    AUDIO_LENGTH_SHORT = 1
    AUDIO_LENGTH_DEFAULT = 2
    AUDIO_LENGTH_LONG = 3

    # Video Overview formats
    VIDEO_FORMAT_EXPLAINER = 1   # Structured, comprehensive overview
    VIDEO_FORMAT_BRIEF = 2       # Bite-sized overview of core ideas

    # Video visual styles
    VIDEO_STYLE_AUTO_SELECT = 1
    VIDEO_STYLE_CUSTOM = 2
    VIDEO_STYLE_CLASSIC = 3
    VIDEO_STYLE_WHITEBOARD = 4
    VIDEO_STYLE_KAWAII = 5
    VIDEO_STYLE_ANIME = 6
    VIDEO_STYLE_WATERCOLOR = 7
    VIDEO_STYLE_RETRO_PRINT = 8
    VIDEO_STYLE_HERITAGE = 9
    VIDEO_STYLE_PAPER_CRAFT = 10

    # Additional Studio types (also created via RPC_CREATE_STUDIO)
    STUDIO_TYPE_REPORT = 2
    STUDIO_TYPE_FLASHCARDS = 4
    STUDIO_TYPE_INFOGRAPHIC = 7
    STUDIO_TYPE_SLIDE_DECK = 8

    # Mind Map RPCs (separate from R7cb6c)
    RPC_GENERATE_MIND_MAP = "yyryJe"  # Generate mind map JSON from sources
    RPC_SAVE_MIND_MAP = "CYK0Xb"      # Save generated mind map to notebook
    RPC_LIST_MIND_MAPS = "cFji9"       # List existing mind maps

    # Report format constants
    REPORT_FORMAT_BRIEFING_DOC = "Briefing Doc"
    REPORT_FORMAT_STUDY_GUIDE = "Study Guide"
    REPORT_FORMAT_BLOG_POST = "Blog Post"
    REPORT_FORMAT_CUSTOM = "Create Your Own"

    # Flashcard difficulty codes (suspected values)
    FLASHCARD_DIFFICULTY_EASY = 1
    FLASHCARD_DIFFICULTY_MEDIUM = 2
    FLASHCARD_DIFFICULTY_HARD = 3

    # Flashcard count code
    FLASHCARD_COUNT_DEFAULT = 2

    # Infographic orientation codes
    INFOGRAPHIC_ORIENTATION_LANDSCAPE = 1
    INFOGRAPHIC_ORIENTATION_PORTRAIT = 2
    INFOGRAPHIC_ORIENTATION_SQUARE = 3

    # Infographic detail level codes
    INFOGRAPHIC_DETAIL_CONCISE = 1
    INFOGRAPHIC_DETAIL_STANDARD = 2
    INFOGRAPHIC_DETAIL_DETAILED = 3

    # Slide Deck format codes
    SLIDE_DECK_FORMAT_DETAILED = 1      # Comprehensive deck with full text and details
    SLIDE_DECK_FORMAT_PRESENTER = 2     # Clean visual slides with key talking points

    # Slide Deck length codes
    SLIDE_DECK_LENGTH_SHORT = 1
    SLIDE_DECK_LENGTH_DEFAULT = 3       # Note: Default is 3, not 2

    # Chat configuration goal/style codes
    CHAT_GOAL_DEFAULT = 1         # General purpose research and brainstorming
    CHAT_GOAL_CUSTOM = 2          # Custom prompt (up to 10000 chars)
    CHAT_GOAL_LEARNING_GUIDE = 3  # Educational focus

    # Chat configuration response length codes
    CHAT_RESPONSE_DEFAULT = 1     # Default response length
    CHAT_RESPONSE_LONGER = 4      # Verbose/longer responses
    CHAT_RESPONSE_SHORTER = 5     # Concise/shorter responses

    # Source type constants (from metadata position 4)
    # These represent the Google Workspace document type, NOT the source origin
    SOURCE_TYPE_GOOGLE_DOCS = 1           # Google Docs (Documents)
    SOURCE_TYPE_GOOGLE_OTHER = 2          # Google Slides & Sheets (non-Doc Drive files)
    SOURCE_TYPE_PASTED_TEXT = 4           # Pasted text (not from Drive)

    # Query endpoint (different from batchexecute - streaming gRPC-style)
    QUERY_ENDPOINT = "/_/LabsTailwindUi/data/google.internal.labs.tailwind.orchestration.v1.LabsTailwindOrchestrationService/GenerateFreeFormStreamed"

    # Headers required for page fetch (must look like a browser navigation)
    _PAGE_FETCH_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "sec-ch-ua": '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
    }

    def __init__(self, cookies: dict[str, str], csrf_token: str = "", session_id: str = ""):
        """
        Initialize the client.

        Args:
            cookies: Dict of Google auth cookies (SID, SSID, HSID, APISID, SAPISID, etc.)
            csrf_token: CSRF token (optional - will be auto-extracted from page if not provided)
            session_id: Session ID (optional - will be auto-extracted from page if not provided)
        """
        self.cookies = cookies
        self.csrf_token = csrf_token
        self._client: httpx.Client | None = None
        self._session_id = session_id

        # Auto-refresh tokens if not provided
        if not self.csrf_token:
            self._refresh_auth_tokens()

    def _refresh_auth_tokens(self) -> None:
        """
        Refresh CSRF token and session ID by fetching the NotebookLM homepage.

        This method fetches the NotebookLM page using the stored cookies and
        extracts the CSRF token (SNlM0e) and session ID (FdrFJe) from the HTML.

        Raises:
            ValueError: If cookies are expired (redirected to login) or tokens not found
        """
        # Build cookie header
        cookie_header = "; ".join(f"{k}={v}" for k, v in self.cookies.items())

        # Must use browser-like headers for page fetch
        headers = {**self._PAGE_FETCH_HEADERS, "Cookie": cookie_header}

        # Use a temporary client for the page fetch
        with httpx.Client(headers=headers, follow_redirects=True, timeout=15.0) as client:
            response = client.get(f"{self.BASE_URL}/")

            # Check if redirected to login (cookies expired)
            if "accounts.google.com" in str(response.url):
                raise ValueError(
                    "Cookies have expired. Please re-authenticate by extracting fresh cookies "
                    "from Chrome DevTools and calling save_auth_tokens."
                )

            if response.status_code != 200:
                raise ValueError(f"Failed to fetch NotebookLM page: HTTP {response.status_code}")

            html = response.text

            # Extract CSRF token (SNlM0e)
            csrf_match = re.search(r'"SNlM0e":"([^"]+)"', html)
            if not csrf_match:
                # Save HTML for debugging
                from pathlib import Path
                debug_dir = Path.home() / ".notebooklm-consumer"
                debug_dir.mkdir(exist_ok=True)
                debug_path = debug_dir / "debug_page.html"
                debug_path.write_text(html)
                raise ValueError(
                    f"Could not extract CSRF token from page. "
                    f"Page saved to {debug_path} for debugging. "
                    f"The page structure may have changed."
                )

            self.csrf_token = csrf_match.group(1)

            # Extract session ID (FdrFJe) - optional but helps
            sid_match = re.search(r'"FdrFJe":"([^"]+)"', html)
            if sid_match:
                self._session_id = sid_match.group(1)

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            # Build cookie string
            cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())

            self._client = httpx.Client(
                headers={
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    "Origin": self.BASE_URL,
                    "Referer": f"{self.BASE_URL}/",
                    "Cookie": cookie_str,
                    "X-Same-Domain": "1",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                },
                timeout=30.0,
            )
        return self._client

    def _build_request_body(self, rpc_id: str, params: Any) -> str:
        """Build the batchexecute request body."""
        # The params need to be JSON-encoded, then wrapped in the RPC structure
        params_json = json.dumps(params)

        # Build the f.req structure
        f_req = [[[rpc_id, params_json, None, "generic"]]]
        f_req_json = json.dumps(f_req)

        # URL encode
        body_parts = [f"f.req={urllib.parse.quote(f_req_json)}"]

        if self.csrf_token:
            body_parts.append(f"at={urllib.parse.quote(self.csrf_token)}")

        return "&".join(body_parts)

    def _build_url(self, rpc_id: str, source_path: str = "/") -> str:
        """Build the batchexecute URL with query params."""
        params = {
            "rpcids": rpc_id,
            "source-path": source_path,
            "bl": "boq_labs-tailwind-frontend_20251221.14_p0",  # Version string, may change
            "hl": "en",
            "rt": "c",
        }

        if self._session_id:
            params["f.sid"] = self._session_id

        query = urllib.parse.urlencode(params)
        return f"{self.BATCHEXECUTE_URL}?{query}"

    def _parse_response(self, response_text: str) -> Any:
        """Parse the batchexecute response."""
        # Response format:
        # )]}'
        # <byte_count>
        # <json_array>

        # Remove the anti-XSSI prefix
        if response_text.startswith(")]}'"):
            response_text = response_text[4:]

        lines = response_text.strip().split("\n")

        # Parse each chunk
        results = []
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue

            # Try to parse as byte count
            try:
                byte_count = int(line)
                # Next line(s) should be the JSON payload
                i += 1
                if i < len(lines):
                    json_str = lines[i]
                    try:
                        data = json.loads(json_str)
                        results.append(data)
                    except json.JSONDecodeError:
                        pass
                i += 1
            except ValueError:
                # Not a byte count, try to parse as JSON
                try:
                    data = json.loads(line)
                    results.append(data)
                except json.JSONDecodeError:
                    pass
                i += 1

        return results

    def _extract_rpc_result(self, parsed_response: list, rpc_id: str) -> Any:
        """Extract the result for a specific RPC ID from the parsed response."""
        for chunk in parsed_response:
            if isinstance(chunk, list):
                for item in chunk:
                    if isinstance(item, list) and len(item) >= 3:
                        if item[0] == "wrb.fr" and item[1] == rpc_id:
                            # The result is in item[2] as a JSON string
                            result_str = item[2]
                            if isinstance(result_str, str):
                                try:
                                    return json.loads(result_str)
                                except json.JSONDecodeError:
                                    return result_str
                            return result_str
        return None

    def list_notebooks(self, debug: bool = False) -> list[ConsumerNotebook]:
        """List all notebooks."""
        client = self._get_client()

        # Build request
        # [null, 1, null, [2]] - params for list notebooks
        params = [None, 1, None, [2]]
        body = self._build_request_body(self.RPC_LIST_NOTEBOOKS, params)
        url = self._build_url(self.RPC_LIST_NOTEBOOKS)

        if debug:
            print(f"[DEBUG] URL: {url}")
            print(f"[DEBUG] Body: {body[:200]}...")

        response = client.post(url, content=body)
        response.raise_for_status()

        if debug:
            print(f"[DEBUG] Response status: {response.status_code}")
            print(f"[DEBUG] Response length: {len(response.text)} chars")

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_LIST_NOTEBOOKS)

        if debug:
            print(f"[DEBUG] Parsed chunks: {len(parsed)}")
            print(f"[DEBUG] Result type: {type(result)}")
            if result:
                print(f"[DEBUG] Result length: {len(result) if isinstance(result, list) else 'N/A'}")
                if isinstance(result, list) and len(result) > 0:
                    print(f"[DEBUG] First item type: {type(result[0])}")
                    print(f"[DEBUG] First item: {str(result[0])[:500]}...")

        notebooks = []
        if result and isinstance(result, list):
            # Result structure: [[notebook1, notebook2, ...]]
            # Each notebook structure:
            #   [0] = "Title"
            #   [1] = [sources]
            #   [2] = "notebook-uuid"
            #   [3] = "emoji" or null
            #   [4] = null
            #   [5] = [metadata] where metadata[0] = ownership (1=mine, 2=shared_with_me)
            notebook_list = result[0] if result and isinstance(result[0], list) else result

            for nb_data in notebook_list:
                if isinstance(nb_data, list) and len(nb_data) >= 3:
                    title = nb_data[0] if isinstance(nb_data[0], str) else "Untitled"
                    sources_data = nb_data[1] if len(nb_data) > 1 else []
                    notebook_id = nb_data[2] if len(nb_data) > 2 else None

                    # Extract ownership and timestamps from metadata at position 5
                    is_owned = True  # Default to owned
                    is_shared = False  # Default to not shared
                    created_at = None
                    modified_at = None

                    if len(nb_data) > 5 and isinstance(nb_data[5], list) and len(nb_data[5]) > 0:
                        metadata = nb_data[5]
                        ownership_value = metadata[0]
                        # 1 = mine (owned), 2 = shared with me
                        is_owned = ownership_value == OWNERSHIP_MINE

                        # Check if shared (for owned notebooks)
                        # Based on observation: [1, true, true, ...] -> Shared
                        #                       [1, false, true, ...] -> Private
                        if len(metadata) > 1:
                            is_shared = bool(metadata[1])

                        # Extract timestamps from metadata
                        # metadata[5] = [seconds, nanos] = last modified
                        # metadata[8] = [seconds, nanos] = created
                        if len(metadata) > 5:
                            modified_at = parse_timestamp(metadata[5])
                        if len(metadata) > 8:
                            created_at = parse_timestamp(metadata[8])

                    sources = []
                    if isinstance(sources_data, list):
                        for src in sources_data:
                            if isinstance(src, list) and len(src) >= 2:
                                # Source structure: [[source_id], title, metadata, ...]
                                src_ids = src[0] if src[0] else []
                                src_title = src[1] if len(src) > 1 else "Untitled"

                                # Extract the source ID (might be in a list)
                                src_id = src_ids[0] if isinstance(src_ids, list) and src_ids else src_ids

                                sources.append({
                                    "id": src_id,
                                    "title": src_title,
                                })

                    if notebook_id:
                        notebooks.append(ConsumerNotebook(
                            id=notebook_id,
                            title=title,
                            source_count=len(sources),
                            sources=sources,
                            is_owned=is_owned,
                            is_shared=is_shared,
                            created_at=created_at,
                            modified_at=modified_at,
                        ))

        return notebooks

    def get_notebook(self, notebook_id: str) -> dict | None:
        """Get details of a specific notebook."""
        client = self._get_client()

        # [notebook_id, null, [2], null, 0]
        params = [notebook_id, None, [2], None, 0]
        body = self._build_request_body(self.RPC_GET_NOTEBOOK, params)
        url = self._build_url(self.RPC_GET_NOTEBOOK, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_GET_NOTEBOOK)

        return result

    def create_notebook(self, title: str = "") -> ConsumerNotebook | None:
        """Create a new notebook.

        Args:
            title: Optional title for the notebook (empty string for "Untitled notebook")

        Returns:
            ConsumerNotebook with the new notebook's ID, or None on failure
        """
        client = self._get_client()

        # Create notebook params: [title, null, null, [2], [1, null, null, null, null, null, null, null, null, null, [1]]]
        params = [title, None, None, [2], [1, None, None, None, None, None, None, None, None, None, [1]]]
        body = self._build_request_body(self.RPC_CREATE_NOTEBOOK, params)
        url = self._build_url(self.RPC_CREATE_NOTEBOOK)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_NOTEBOOK)

        if result and isinstance(result, list) and len(result) >= 3:
            # Response: ["", null, "notebook-uuid", ...]
            notebook_id = result[2]
            if notebook_id:
                return ConsumerNotebook(
                    id=notebook_id,
                    title=title or "Untitled notebook",
                    source_count=0,
                    sources=[],
                )
        return None

    def rename_notebook(self, notebook_id: str, new_title: str) -> bool:
        """Rename a notebook.

        Args:
            notebook_id: The notebook UUID
            new_title: The new title for the notebook

        Returns:
            True on success, False on failure
        """
        client = self._get_client()

        # Rename notebook params: [notebook_id, [[null, null, null, [null, "New Title"]]]]
        params = [notebook_id, [[None, None, None, [None, new_title]]]]
        body = self._build_request_body(self.RPC_RENAME_NOTEBOOK, params)
        url = self._build_url(self.RPC_RENAME_NOTEBOOK, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_RENAME_NOTEBOOK)

        # Success returns the notebook info with new title
        return result is not None

    def configure_chat(
        self,
        notebook_id: str,
        goal: str = "default",
        custom_prompt: str | None = None,
        response_length: str = "default",
    ) -> dict[str, Any]:
        """Configure the chat settings for a notebook.

        This sets the conversational goal/style and response length for the notebook's
        AI chat. These settings affect how the AI responds to queries.

        Args:
            notebook_id: The notebook UUID
            goal: The conversational goal/style. One of:
                - "default": General purpose research and brainstorming
                - "learning_guide": Educational focus, helps grasp new concepts
                - "custom": Use a custom prompt (requires custom_prompt)
            custom_prompt: Custom prompt text when goal="custom" (up to 10000 chars)
            response_length: Response length preference. One of:
                - "default": Balanced response length
                - "longer": Verbose, more detailed responses
                - "shorter": Concise, brief responses

        Returns:
            Dict with status and updated settings

        Raises:
            ValueError: If goal="custom" but no custom_prompt provided
            ValueError: If custom_prompt exceeds 10000 characters
        """
        client = self._get_client()

        # Map goal string to code
        goal_map = {
            "default": self.CHAT_GOAL_DEFAULT,
            "learning_guide": self.CHAT_GOAL_LEARNING_GUIDE,
            "custom": self.CHAT_GOAL_CUSTOM,
        }
        if goal not in goal_map:
            raise ValueError(f"Invalid goal: {goal}. Must be one of: {list(goal_map.keys())}")
        goal_code = goal_map[goal]

        # Validate custom prompt
        if goal == "custom":
            if not custom_prompt:
                raise ValueError("custom_prompt is required when goal='custom'")
            if len(custom_prompt) > 10000:
                raise ValueError(f"custom_prompt exceeds 10000 chars (got {len(custom_prompt)})")

        # Map response length string to code
        length_map = {
            "default": self.CHAT_RESPONSE_DEFAULT,
            "longer": self.CHAT_RESPONSE_LONGER,
            "shorter": self.CHAT_RESPONSE_SHORTER,
        }
        if response_length not in length_map:
            raise ValueError(f"Invalid response_length: {response_length}. Must be one of: {list(length_map.keys())}")
        length_code = length_map[response_length]

        # Build the goal setting
        if goal == "custom" and custom_prompt:
            goal_setting = [goal_code, custom_prompt]
        else:
            goal_setting = [goal_code]

        # Build the chat settings params
        # Structure: [notebook_id, [[null, null, null, null, null, null, null, [[goal], [length]]]]]
        chat_settings = [goal_setting, [length_code]]
        params = [notebook_id, [[None, None, None, None, None, None, None, chat_settings]]]

        body = self._build_request_body(self.RPC_RENAME_NOTEBOOK, params)
        url = self._build_url(self.RPC_RENAME_NOTEBOOK, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_RENAME_NOTEBOOK)

        if result:
            # Extract the updated settings from response
            # Response format: [title, null, id, emoji, null, metadata, null, [[goal_code, prompt?], [length_code]]]
            settings = result[7] if len(result) > 7 else None
            return {
                "status": "success",
                "notebook_id": notebook_id,
                "goal": goal,
                "custom_prompt": custom_prompt if goal == "custom" else None,
                "response_length": response_length,
                "raw_settings": settings,
            }

        return {
            "status": "error",
            "error": "Failed to configure chat settings",
        }

    def delete_notebook(self, notebook_id: str) -> bool:
        """Delete a notebook permanently.

        WARNING: This action is IRREVERSIBLE. The notebook and all its sources,
        notes, and generated content will be permanently deleted.

        Args:
            notebook_id: The notebook UUID to delete

        Returns:
            True on success, False on failure
        """
        client = self._get_client()

        # Delete notebook params: [[notebook_id], [2]]
        params = [[notebook_id], [2]]
        body = self._build_request_body(self.RPC_DELETE_NOTEBOOK, params)
        url = self._build_url(self.RPC_DELETE_NOTEBOOK)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_DELETE_NOTEBOOK)

        # Success returns empty list []
        return result is not None

    def check_source_freshness(self, source_id: str) -> bool | None:
        """Check if a Drive source is fresh (up-to-date with Google Drive).

        Args:
            source_id: The source UUID

        Returns:
            True if fresh, False if stale (needs sync), None on error
        """
        client = self._get_client()

        # Check freshness params: [null, ["source_id"], [2]]
        params = [None, [source_id], [2]]
        body = self._build_request_body(self.RPC_CHECK_FRESHNESS, params)
        url = self._build_url(self.RPC_CHECK_FRESHNESS)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CHECK_FRESHNESS)

        # Response: [[null, true/false, ["source_id"]]]
        # true = fresh, false = stale
        if result and isinstance(result, list) and len(result) > 0:
            inner = result[0] if result else []
            if isinstance(inner, list) and len(inner) >= 2:
                return inner[1]  # true = fresh, false = stale
        return None

    def sync_drive_source(self, source_id: str) -> dict | None:
        """Sync a Drive source with the latest content from Google Drive.

        Args:
            source_id: The source UUID

        Returns:
            Dict with updated source info (id, title, synced_at) or None on failure
        """
        client = self._get_client()

        # Sync params: [null, ["source_id"], [2]]
        params = [None, [source_id], [2]]
        body = self._build_request_body(self.RPC_SYNC_DRIVE, params)
        url = self._build_url(self.RPC_SYNC_DRIVE)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_SYNC_DRIVE)

        # Response: [[[source_id], "title", [metadata...], [null, 2]]]
        if result and isinstance(result, list) and len(result) > 0:
            source_data = result[0] if result else []
            if isinstance(source_data, list) and len(source_data) >= 3:
                source_id_result = source_data[0][0] if source_data[0] else None
                title = source_data[1] if len(source_data) > 1 else "Unknown"
                metadata = source_data[2] if len(source_data) > 2 else []

                # Extract sync timestamp from metadata[3]
                synced_at = None
                if isinstance(metadata, list) and len(metadata) > 3:
                    sync_info = metadata[3]
                    if isinstance(sync_info, list) and len(sync_info) > 1:
                        ts = sync_info[1]
                        if isinstance(ts, list) and len(ts) > 0:
                            synced_at = ts[0]

                return {
                    "id": source_id_result,
                    "title": title,
                    "synced_at": synced_at,
                }
        return None

    def get_notebook_sources_with_types(self, notebook_id: str) -> list[dict]:
        """Get all sources from a notebook with their type information.

        Args:
            notebook_id: The notebook UUID

        Returns:
            List of source dicts with id, title, source_type, and drive_doc_id
        """
        result = self.get_notebook(notebook_id)

        sources = []
        # Result structure: [[title, [sources], notebook_id, ...]]
        # The notebook data is wrapped in an outer array
        if result and isinstance(result, list) and len(result) >= 1:
            notebook_data = result[0] if isinstance(result[0], list) else result
            # Sources are in notebook_data[1]
            sources_data = notebook_data[1] if len(notebook_data) > 1 else []

            if isinstance(sources_data, list):
                for src in sources_data:
                    if isinstance(src, list) and len(src) >= 3:
                        # Source structure: [[id], title, [metadata...], [null, 2]]
                        source_id = src[0][0] if src[0] and isinstance(src[0], list) else None
                        title = src[1] if len(src) > 1 else "Untitled"
                        metadata = src[2] if len(src) > 2 else []

                        # Extract source type from metadata[4]
                        source_type = None
                        drive_doc_id = None
                        if isinstance(metadata, list):
                            if len(metadata) > 4:
                                source_type = metadata[4]
                            # Drive doc info at metadata[0]
                            if len(metadata) > 0 and isinstance(metadata[0], list):
                                drive_doc_id = metadata[0][0] if metadata[0] else None

                        # Google Docs (type 1) and Slides/Sheets (type 2) are stored in Drive
                        # and can be synced if they have a drive_doc_id
                        can_sync = drive_doc_id is not None and source_type in (
                            self.SOURCE_TYPE_GOOGLE_DOCS,
                            self.SOURCE_TYPE_GOOGLE_OTHER,
                        )

                        sources.append({
                            "id": source_id,
                            "title": title,
                            "source_type": source_type,
                            "source_type_name": self._get_source_type_name(source_type),
                            "drive_doc_id": drive_doc_id,
                            "can_sync": can_sync,  # True for Drive docs AND Gemini Notes
                        })

        return sources

    @staticmethod
    def _get_source_type_name(source_type: int | None) -> str:
        """Convert source type number to human-readable name."""
        if source_type == 1:
            return "google_docs"
        elif source_type == 2:
            return "google_slides_sheets"  # Slides and Sheets both use type 2
        elif source_type == 4:
            return "pasted_text"
        return "unknown"

    def add_url_source(self, notebook_id: str, url: str) -> dict | None:
        """Add a URL (website or YouTube) as a source to a notebook.

        Args:
            notebook_id: The notebook UUID
            url: The URL to add (website or YouTube video)

        Returns:
            Dict with source info (id, title) or None on failure
        """
        client = self._get_client()

        # URL source params structure:
        # [[[null, null, [urls], null, null, null, null, null, null, null, 1]], notebook_id, [2], settings]
        source_data = [None, None, [url], None, None, None, None, None, None, None, 1]
        params = [
            [[source_data]],
            notebook_id,
            [2],
            [1, None, None, None, None, None, None, None, None, None, [1]]
        ]
        body = self._build_request_body(self.RPC_ADD_SOURCE, params)
        source_path = f"/notebook/{notebook_id}"
        url_endpoint = self._build_url(self.RPC_ADD_SOURCE, source_path)

        response = client.post(url_endpoint, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_ADD_SOURCE)

        if result and isinstance(result, list) and len(result) > 0:
            # Response: [[[[source_id], title, metadata, ...]]]
            source_list = result[0] if result else []
            if source_list and len(source_list) > 0:
                source_data = source_list[0]
                source_id = source_data[0][0] if source_data[0] else None
                source_title = source_data[1] if len(source_data) > 1 else "Untitled"
                return {"id": source_id, "title": source_title}
        return None

    def add_text_source(self, notebook_id: str, text: str, title: str = "Pasted Text") -> dict | None:
        """Add pasted text as a source to a notebook.

        Args:
            notebook_id: The notebook UUID
            text: The text content to add
            title: Optional title for the source (default: "Pasted Text")

        Returns:
            Dict with source info (id, title) or None on failure
        """
        client = self._get_client()

        # Text source params structure:
        # [[[null, [title, content], null, 2, null, null, null, null, null, null, 1]], notebook_id, [2], settings]
        source_data = [None, [title, text], None, 2, None, None, None, None, None, None, 1]
        params = [
            [[source_data]],
            notebook_id,
            [2],
            [1, None, None, None, None, None, None, None, None, None, [1]]
        ]
        body = self._build_request_body(self.RPC_ADD_SOURCE, params)
        source_path = f"/notebook/{notebook_id}"
        url_endpoint = self._build_url(self.RPC_ADD_SOURCE, source_path)

        response = client.post(url_endpoint, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_ADD_SOURCE)

        if result and isinstance(result, list) and len(result) > 0:
            source_list = result[0] if result else []
            if source_list and len(source_list) > 0:
                source_data = source_list[0]
                source_id = source_data[0][0] if source_data[0] else None
                source_title = source_data[1] if len(source_data) > 1 else title
                return {"id": source_id, "title": source_title}
        return None

    def add_drive_source(
        self,
        notebook_id: str,
        document_id: str,
        title: str,
        mime_type: str = "application/vnd.google-apps.document"
    ) -> dict | None:
        """Add a Google Drive document as a source to a notebook.

        Args:
            notebook_id: The notebook UUID
            document_id: The Google Drive document ID (from the URL)
            title: The document title/name to display
            mime_type: The MIME type (default: Google Doc)
                - application/vnd.google-apps.document (Google Doc)
                - application/vnd.google-apps.presentation (Google Slides)
                - application/vnd.google-apps.spreadsheet (Google Sheets)
                - application/pdf (PDF)

        Returns:
            Dict with source info (id, title) or None on failure
        """
        client = self._get_client()

        # Drive source params structure (verified from network capture):
        # [[doc_id, mime_type, 1, title], null, null, null, null, null, null, null, null, null, 1]
        source_data = [
            [document_id, mime_type, 1, title],  # Drive document info at position 0
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            1
        ]
        params = [
            [[source_data]],
            notebook_id,
            [2],
            [1, None, None, None, None, None, None, None, None, None, [1]]
        ]
        body = self._build_request_body(self.RPC_ADD_SOURCE, params)
        source_path = f"/notebook/{notebook_id}"
        url_endpoint = self._build_url(self.RPC_ADD_SOURCE, source_path)

        response = client.post(url_endpoint, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_ADD_SOURCE)

        if result and isinstance(result, list) and len(result) > 0:
            source_list = result[0] if result else []
            if source_list and len(source_list) > 0:
                source_data = source_list[0]
                source_id = source_data[0][0] if source_data[0] else None
                source_title = source_data[1] if len(source_data) > 1 else document_name
                return {"id": source_id, "title": source_title}
        return None

    def query(
        self,
        notebook_id: str,
        query_text: str,
        source_ids: list[str] | None = None,
        conversation_id: str | None = None,
    ) -> dict | None:
        """Query the notebook with a question.

        Args:
            notebook_id: The notebook UUID
            query_text: The question to ask
            source_ids: Optional list of source IDs to query (default: all sources)
            conversation_id: Optional conversation ID for follow-up questions

        Returns:
            Dict with answer text, citations, and conversation_id for follow-ups
        """
        import uuid

        client = self._get_client()

        # If no source_ids provided, get them from the notebook
        if source_ids is None:
            notebook_data = self.get_notebook(notebook_id)
            if notebook_data and isinstance(notebook_data, list) and len(notebook_data) > 0:
                # Extract source IDs from notebook data
                # Structure varies, try to find source IDs
                source_ids = []
                # This needs refinement based on actual notebook structure
            else:
                source_ids = []

        # Generate conversation ID if not provided
        if conversation_id is None:
            conversation_id = str(uuid.uuid4())

        # Build source IDs structure: [[["id1"]], [["id2"]], ...]
        sources_array = [[[[sid]]] for sid in source_ids] if source_ids else []

        # Query params structure (from network capture)
        params = [
            sources_array,
            query_text,
            None,
            [2, None, [1]],
            conversation_id
        ]
        params_json = json.dumps(params)

        # Build request body (similar to batchexecute but different structure)
        f_req = [None, params_json]
        f_req_json = json.dumps(f_req)

        body_parts = [f"f.req={urllib.parse.quote(f_req_json)}"]
        if self.csrf_token:
            body_parts.append(f"at={urllib.parse.quote(self.csrf_token)}")
        body = "&".join(body_parts)

        # Build URL
        url_params = {
            "bl": "boq_labs-tailwind-frontend_20251221.14_p0",
            "hl": "en",
            "rt": "c",
        }
        if self._session_id:
            url_params["f.sid"] = self._session_id

        query_string = urllib.parse.urlencode(url_params)
        url = f"{self.BASE_URL}{self.QUERY_ENDPOINT}?{query_string}"

        response = client.post(url, content=body)
        response.raise_for_status()

        # Parse streaming response - collect all chunks
        parsed = self._parse_response(response.text)

        # Extract final answer from the last chunk
        answer_text = ""
        for chunk in reversed(parsed):
            if isinstance(chunk, list):
                for item in chunk:
                    if isinstance(item, list) and len(item) >= 3:
                        if item[0] == "wrb.fr" and item[2]:
                            try:
                                result = json.loads(item[2])
                                if result and isinstance(result, list) and len(result) > 0:
                                    inner = result[0]
                                    if isinstance(inner, list) and len(inner) > 0:
                                        answer_text = inner[0]
                                        break
                            except json.JSONDecodeError:
                                pass
                if answer_text:
                    break

        return {
            "answer": answer_text,
            "conversation_id": conversation_id,
            "raw_response": parsed,
        }

    def start_research(
        self,
        notebook_id: str,
        query: str,
        source: str = "web",
        mode: str = "fast",
    ) -> dict | None:
        """Start a research session to discover sources.

        Args:
            notebook_id: The notebook UUID
            query: The search query
            source: Source type - "web" or "drive"
            mode: Research mode - "fast" (10 sources, ~30s) or "deep" (40+ sources, 3-5min, web only)

        Returns:
            Dict with task_id and research info, or None on failure

        Raises:
            ValueError: If invalid source/mode combination (deep + drive not supported)
        """
        # Validate inputs
        source_lower = source.lower()
        mode_lower = mode.lower()

        if source_lower not in ("web", "drive"):
            raise ValueError(f"Invalid source '{source}'. Use 'web' or 'drive'.")

        if mode_lower not in ("fast", "deep"):
            raise ValueError(f"Invalid mode '{mode}'. Use 'fast' or 'deep'.")

        if mode_lower == "deep" and source_lower == "drive":
            raise ValueError("Deep Research only supports Web sources. Use mode='fast' for Drive.")

        # Map to internal constants
        source_type = self.RESEARCH_SOURCE_WEB if source_lower == "web" else self.RESEARCH_SOURCE_DRIVE

        client = self._get_client()

        if mode_lower == "fast":
            # Fast Research: Ljjv0c
            # Params: [["query", source_type], null, 1, "notebook_id"]
            params = [[query, source_type], None, 1, notebook_id]
            rpc_id = self.RPC_START_FAST_RESEARCH
        else:
            # Deep Research: QA9ei
            # Params: [null, [1], ["query", source_type], 5, "notebook_id"]
            params = [None, [1], [query, source_type], 5, notebook_id]
            rpc_id = self.RPC_START_DEEP_RESEARCH

        body = self._build_request_body(rpc_id, params)
        url = self._build_url(rpc_id, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, rpc_id)

        if result and isinstance(result, list) and len(result) > 0:
            task_id = result[0]
            report_id = result[1] if len(result) > 1 else None

            return {
                "task_id": task_id,
                "report_id": report_id,
                "notebook_id": notebook_id,
                "query": query,
                "source": source_lower,
                "mode": mode_lower,
            }
        return None

    def poll_research(self, notebook_id: str) -> dict | None:
        """Poll for research results.

        Call this repeatedly until status is "completed".

        Args:
            notebook_id: The notebook UUID

        Returns:
            Dict with status, sources, and summary when complete
        """
        client = self._get_client()

        # Poll params: [null, null, "notebook_id"]
        params = [None, None, notebook_id]
        body = self._build_request_body(self.RPC_POLL_RESEARCH, params)
        url = self._build_url(self.RPC_POLL_RESEARCH, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_POLL_RESEARCH)

        if not result or not isinstance(result, list) or len(result) == 0:
            return {"status": "no_research", "message": "No active research found"}

        # Result structure: [[[task_id, task_info, status], [ts1], [ts2]]]
        # Unwrap the outer array to get [[task_id, task_info, status], [ts1], [ts2]]
        if isinstance(result[0], list) and len(result[0]) > 0 and isinstance(result[0][0], list):
            result = result[0]

        # Result may contain multiple research tasks - find the most recent/active one
        research_tasks = []

        for task_data in result:
            if not isinstance(task_data, list) or len(task_data) < 3:
                continue

            task_id = task_data[0]
            task_info = task_data[1] if len(task_data) > 1 else None

            # Skip timestamp arrays (task_id should be a UUID string, not an int)
            if not isinstance(task_id, str):
                continue

            if not task_info or not isinstance(task_info, list):
                continue

            # Parse task info structure:
            # [notebook_id, [query, source_type], mode, [[sources], summary], status]
            # Note: status is at task_info[4], NOT task_data[2] (which is a timestamp)
            query_info = task_info[1] if len(task_info) > 1 else None
            research_mode = task_info[2] if len(task_info) > 2 else None
            sources_and_summary = task_info[3] if len(task_info) > 3 else []
            status_code = task_info[4] if len(task_info) > 4 else None

            query_text = query_info[0] if query_info and len(query_info) > 0 else ""
            source_type = query_info[1] if query_info and len(query_info) > 1 else 1

            # Extract sources_data and summary from bundled array [[sources], summary]
            sources_data = []
            summary = ""
            if isinstance(sources_and_summary, list) and len(sources_and_summary) >= 2:
                sources_data = sources_and_summary[0] if isinstance(sources_and_summary[0], list) else []
                summary = sources_and_summary[1] if isinstance(sources_and_summary[1], str) else ""

            # Parse sources - sources_data should be [[url, title, desc, type], ...]
            sources = []
            if isinstance(sources_data, list) and len(sources_data) > 0:
                for idx, src in enumerate(sources_data):
                    if isinstance(src, list) and len(src) >= 3:
                        url = src[0] if isinstance(src[0], str) else ""
                        title = src[1] if isinstance(src[1], str) else ""
                        desc = src[2] if isinstance(src[2], str) else ""
                        result_type = src[3] if len(src) > 3 and isinstance(src[3], int) else 1

                        sources.append({
                            "index": idx,
                            "url": url,
                            "title": title,
                            "description": desc,
                            "result_type": result_type,
                            "result_type_name": self._get_result_type_name(result_type),
                        })

            # Determine status (1 = in_progress, 2 = completed)
            status = "completed" if status_code == 2 else "in_progress"

            research_tasks.append({
                "task_id": task_id,
                "status": status,
                "query": query_text,
                "source_type": "web" if source_type == 1 else "drive",
                "mode": "deep" if research_mode == 5 else "fast",
                "sources": sources,
                "source_count": len(sources),
                "summary": summary,
            })

        if not research_tasks:
            return {"status": "no_research", "message": "No active research found"}

        # Return the most recent (first) task
        return research_tasks[0]

    @staticmethod
    def _get_result_type_name(result_type: int) -> str:
        """Convert research result type to human-readable name."""
        type_names = {
            1: "web",
            2: "google_doc",
            3: "google_slides",
            5: "deep_report",
            8: "google_sheets",
        }
        return type_names.get(result_type, "unknown")

    def import_research_sources(
        self,
        notebook_id: str,
        task_id: str,
        sources: list[dict],
    ) -> list[dict]:
        """Import research sources into the notebook.

        Args:
            notebook_id: The notebook UUID
            task_id: The research task ID from start_research
            sources: List of source dicts from poll_research to import
                     Each dict should have: url, title, result_type

        Returns:
            List of created source dicts with id and title
        """
        if not sources:
            return []

        client = self._get_client()

        # Build source array for import
        # Web source: [null, null, ["url", "title"], null, null, null, null, null, null, null, 2]
        # Drive source: Extract doc_id from URL and use different structure
        source_array = []

        for src in sources:
            url = src.get("url", "")
            title = src.get("title", "Untitled")
            result_type = src.get("result_type", 1)

            if result_type == 1:
                # Web source
                source_data = [None, None, [url, title], None, None, None, None, None, None, None, 2]
            else:
                # Drive source - extract document ID from URL
                # URL format: https://drive.google.com/a/redhat.com/open?id=<doc_id>
                doc_id = None
                if "id=" in url:
                    doc_id = url.split("id=")[-1].split("&")[0]

                if doc_id:
                    # Determine MIME type from result_type
                    mime_types = {
                        2: "application/vnd.google-apps.document",
                        3: "application/vnd.google-apps.presentation",
                        8: "application/vnd.google-apps.spreadsheet",
                    }
                    mime_type = mime_types.get(result_type, "application/vnd.google-apps.document")
                    # Drive source structure: [[doc_id, mime_type, 1, title], null x9, 2]
                    # The 1 at position 2 and trailing 2 are required for Drive sources
                    source_data = [[doc_id, mime_type, 1, title], None, None, None, None, None, None, None, None, None, 2]
                else:
                    # Fallback to web-style import
                    source_data = [None, None, [url, title], None, None, None, None, None, None, None, 2]

            source_array.append(source_data)

        # Import params: [null, [1], "task_id", "notebook_id", [sources]]
        # Note: source_array is already [source1, source2, ...], don't double-wrap
        params = [None, [1], task_id, notebook_id, source_array]
        body = self._build_request_body(self.RPC_IMPORT_RESEARCH, params)
        url = self._build_url(self.RPC_IMPORT_RESEARCH, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_IMPORT_RESEARCH)

        imported_sources = []
        if result and isinstance(result, list):
            # Response is wrapped: [[source1, source2, ...]]
            # Unwrap if first element is a list of lists (sources array)
            if (
                len(result) > 0
                and isinstance(result[0], list)
                and len(result[0]) > 0
                and isinstance(result[0][0], list)
            ):
                result = result[0]

            for src_data in result:
                if isinstance(src_data, list) and len(src_data) >= 2:
                    src_id = src_data[0][0] if src_data[0] and isinstance(src_data[0], list) else None
                    src_title = src_data[1] if len(src_data) > 1 else "Untitled"
                    if src_id:
                        imported_sources.append({"id": src_id, "title": src_title})

        return imported_sources

    def create_audio_overview(
        self,
        notebook_id: str,
        source_ids: list[str],
        format_code: int = 1,  # AUDIO_FORMAT_DEEP_DIVE
        length_code: int = 2,  # AUDIO_LENGTH_DEFAULT
        language: str = "en",
        focus_prompt: str = "",
    ) -> dict | None:
        """Create an Audio Overview (podcast) for a notebook.

        Args:
            notebook_id: The notebook UUID
            source_ids: List of source UUIDs to include
            format_code: Audio format (1=Deep Dive, 2=Brief, 3=Critique, 4=Debate)
            length_code: Length (1=Short, 2=Default, 3=Long)
            language: BCP-47 language code (e.g., "en", "es", "fr")
            focus_prompt: Optional text describing what AI should focus on

        Returns:
            Dict with artifact_id and status, or None on failure
        """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Build source IDs in the simpler format: [[id1], [id2], ...]
        sources_simple = [[sid] for sid in source_ids]

        # Build the audio options structure
        audio_options = [
            None,
            [
                focus_prompt,
                length_code,
                None,
                sources_simple,
                language,
                None,
                format_code
            ]
        ]

        # Build the full params
        params = [
            [2],
            notebook_id,
            [
                None, None,
                self.STUDIO_TYPE_AUDIO,
                sources_nested,
                None, None,
                audio_options
            ]
        ]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            # Response: [[artifact_id, title, type, sources, status, ...]]
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "audio",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "format": self._get_audio_format_name(format_code),
                "length": self._get_audio_length_name(length_code),
                "language": language,
            }

        return None

    def create_video_overview(
        self,
        notebook_id: str,
        source_ids: list[str],
        format_code: int = 1,  # VIDEO_FORMAT_EXPLAINER
        visual_style_code: int = 1,  # VIDEO_STYLE_AUTO_SELECT
        language: str = "en",
        focus_prompt: str = "",
    ) -> dict | None:
        """Create a Video Overview for a notebook.

        Args:
            notebook_id: The notebook UUID
            source_ids: List of source UUIDs to include
            format_code: Video format (1=Explainer, 2=Brief)
            visual_style_code: Visual style (1=Auto, 2=Custom, 3=Classic, etc.)
            language: BCP-47 language code (e.g., "en", "es", "fr")
            focus_prompt: Optional text describing what AI should focus on

        Returns:
            Dict with artifact_id and status, or None on failure
        """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Build source IDs in the simpler format: [[id1], [id2], ...]
        sources_simple = [[sid] for sid in source_ids]

        # Build the video options structure
        video_options = [
            None, None,
            [
                sources_simple,
                language,
                focus_prompt,
                None,
                format_code,
                visual_style_code
            ]
        ]

        # Build the full params
        params = [
            [2],
            notebook_id,
            [
                None, None,
                self.STUDIO_TYPE_VIDEO,
                sources_nested,
                None, None, None, None,
                video_options
            ]
        ]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            # Response: [[artifact_id, title, type, sources, status, ...]]
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "video",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "format": self._get_video_format_name(format_code),
                "visual_style": self._get_video_style_name(visual_style_code),
                "language": language,
            }

        return None

    def poll_studio_status(self, notebook_id: str) -> list[dict]:
        """Poll for studio content (audio/video overviews) status.

        Args:
            notebook_id: The notebook UUID

        Returns:
            List of studio artifacts with their status and URLs
        """
        client = self._get_client()

        # Poll params: [[2], notebook_id, 'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"']
        params = [[2], notebook_id, 'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"']
        body = self._build_request_body(self.RPC_POLL_STUDIO, params)
        url = self._build_url(self.RPC_POLL_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_POLL_STUDIO)

        artifacts = []
        if result and isinstance(result, list) and len(result) > 0:
            # Response is an array of artifacts, possibly wrapped
            artifact_list = result[0] if isinstance(result[0], list) else result

            for artifact_data in artifact_list:
                if not isinstance(artifact_data, list) or len(artifact_data) < 5:
                    continue

                artifact_id = artifact_data[0]
                title = artifact_data[1] if len(artifact_data) > 1 else ""
                type_code = artifact_data[2] if len(artifact_data) > 2 else None
                status_code = artifact_data[4] if len(artifact_data) > 4 else None

                # Extract audio/video URLs from the options structure
                audio_url = None
                video_url = None
                duration_seconds = None

                # Audio artifacts have URLs at position 6
                if type_code == self.STUDIO_TYPE_AUDIO and len(artifact_data) > 6:
                    audio_options = artifact_data[6]
                    if isinstance(audio_options, list) and len(audio_options) > 3:
                        audio_url = audio_options[3] if isinstance(audio_options[3], str) else None
                        # Duration is often at position 9
                        if len(audio_options) > 9 and isinstance(audio_options[9], list):
                            duration_seconds = audio_options[9][0] if audio_options[9] else None

                # Video artifacts have URLs at position 8
                if type_code == self.STUDIO_TYPE_VIDEO and len(artifact_data) > 8:
                    video_options = artifact_data[8]
                    if isinstance(video_options, list) and len(video_options) > 3:
                        video_url = video_options[3] if isinstance(video_options[3], str) else None

                # Infographic artifacts have image URL at position 14
                # Structure: [settings, title, [[title, [url, width, height], desc, text]]]
                infographic_url = None
                if type_code == self.STUDIO_TYPE_INFOGRAPHIC and len(artifact_data) > 14:
                    infographic_options = artifact_data[14]
                    if isinstance(infographic_options, list) and len(infographic_options) > 2:
                        # URL is at [2][0][1][0] - image_data[0][1][0]
                        image_data = infographic_options[2]
                        if isinstance(image_data, list) and len(image_data) > 0:
                            first_image = image_data[0]
                            if isinstance(first_image, list) and len(first_image) > 1:
                                image_details = first_image[1]
                                if isinstance(image_details, list) and len(image_details) > 0:
                                    url = image_details[0]
                                    if isinstance(url, str) and url.startswith("http"):
                                        infographic_url = url

                # Slide deck artifacts have download URL at position 16
                slide_deck_url = None
                if type_code == self.STUDIO_TYPE_SLIDE_DECK and len(artifact_data) > 16:
                    slide_deck_options = artifact_data[16]
                    if isinstance(slide_deck_options, list) and len(slide_deck_options) > 0:
                        # URL is typically at position 0 in the options
                        if isinstance(slide_deck_options[0], str) and slide_deck_options[0].startswith("http"):
                            slide_deck_url = slide_deck_options[0]
                        # Or may be nested deeper
                        elif len(slide_deck_options) > 3 and isinstance(slide_deck_options[3], str):
                            slide_deck_url = slide_deck_options[3]

                # Report artifacts have content at position 7
                report_content = None
                if type_code == self.STUDIO_TYPE_REPORT and len(artifact_data) > 7:
                    report_options = artifact_data[7]
                    if isinstance(report_options, list) and len(report_options) > 1:
                        # Content is nested in the options
                        content_data = report_options[1] if isinstance(report_options[1], list) else None
                        if content_data and len(content_data) > 0:
                            # Report content is typically markdown text
                            report_content = content_data[0] if isinstance(content_data[0], str) else None

                # Flashcard artifacts have cards data at position 9
                flashcard_count = None
                if type_code == self.STUDIO_TYPE_FLASHCARDS and len(artifact_data) > 9:
                    flashcard_options = artifact_data[9]
                    if isinstance(flashcard_options, list) and len(flashcard_options) > 1:
                        # Count cards in the data
                        cards_data = flashcard_options[1] if isinstance(flashcard_options[1], list) else None
                        if cards_data:
                            flashcard_count = len(cards_data) if isinstance(cards_data, list) else None

                # Extract created_at timestamp
                # Position varies by type but often at position 10, 15, or similar
                created_at = None
                # Try common timestamp positions
                for ts_pos in [10, 15, 17]:
                    if len(artifact_data) > ts_pos:
                        ts_candidate = artifact_data[ts_pos]
                        if isinstance(ts_candidate, list) and len(ts_candidate) >= 2:
                            # Check if it looks like a timestamp [seconds, nanos]
                            if isinstance(ts_candidate[0], (int, float)) and ts_candidate[0] > 1700000000:
                                created_at = parse_timestamp(ts_candidate)
                                break

                # Map type codes to type names
                type_map = {
                    self.STUDIO_TYPE_AUDIO: "audio",
                    self.STUDIO_TYPE_REPORT: "report",
                    self.STUDIO_TYPE_VIDEO: "video",
                    self.STUDIO_TYPE_FLASHCARDS: "flashcards",
                    self.STUDIO_TYPE_INFOGRAPHIC: "infographic",
                    self.STUDIO_TYPE_SLIDE_DECK: "slide_deck",
                }
                artifact_type = type_map.get(type_code, "unknown")
                status = "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown"

                artifacts.append({
                    "artifact_id": artifact_id,
                    "title": title,
                    "type": artifact_type,
                    "status": status,
                    "created_at": created_at,
                    "audio_url": audio_url,
                    "video_url": video_url,
                    "infographic_url": infographic_url,
                    "slide_deck_url": slide_deck_url,
                    "report_content": report_content,
                    "flashcard_count": flashcard_count,
                    "duration_seconds": duration_seconds,
                })

        return artifacts

    def delete_studio_artifact(self, artifact_id: str) -> bool:
        """Delete a studio artifact (Audio or Video Overview).

        WARNING: This action is IRREVERSIBLE. The artifact will be permanently deleted.

        Args:
            artifact_id: The artifact UUID to delete

        Returns:
            True on success, False on failure
        """
        client = self._get_client()

        # Delete studio artifact params: [[2], "artifact_id"]
        params = [[2], artifact_id]
        body = self._build_request_body(self.RPC_DELETE_STUDIO, params)
        url = self._build_url(self.RPC_DELETE_STUDIO)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_DELETE_STUDIO)

        # Success returns empty list []
        return result is not None

    def create_infographic(
        self,
        notebook_id: str,
        source_ids: list[str],
        orientation_code: int = 1,  # INFOGRAPHIC_ORIENTATION_LANDSCAPE
        detail_level_code: int = 2,  # INFOGRAPHIC_DETAIL_STANDARD
        language: str = "en",
        focus_prompt: str = "",
    ) -> dict | None:
        """Create an Infographic from notebook sources.

        Args:
            notebook_id: The notebook UUID
            source_ids: List of source UUIDs to include
            orientation_code: Orientation (1=Landscape, 2=Portrait, 3=Square)
            detail_level_code: Detail level (1=Concise, 2=Standard, 3=Detailed)
            language: BCP-47 language code (e.g., "en", "es", "fr")
            focus_prompt: Optional text describing what AI should focus on

        Returns:
            Dict with artifact_id and status, or None on failure
        """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Build the infographic options structure
        # Options at position 14: [[focus_prompt, language, null, orientation, detail_level]]
        # Captured RPC structure was [[null, "en", null, 1, 2]]
        infographic_options = [[focus_prompt or None, language, None, orientation_code, detail_level_code]]

        # Build the content array with 10 nulls between source_ids and options
        # Structure: [null, null, 7, [source_ids], null*10, [[options]]]
        content = [
            None, None,
            self.STUDIO_TYPE_INFOGRAPHIC,
            sources_nested,
            None, None, None, None, None, None, None, None, None, None,  # 10 nulls (positions 4-13)
            infographic_options  # position 14
        ]

        # Build the full params
        params = [
            [2],
            notebook_id,
            content
        ]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            # Response: [[artifact_id, title, type, sources, status, ...]]
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "infographic",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "orientation": self._get_infographic_orientation_name(orientation_code),
                "detail_level": self._get_infographic_detail_name(detail_level_code),
                "language": language,
            }

        return None

    def create_slide_deck(
        self,
        notebook_id: str,
        source_ids: list[str],
        format_code: int = 1,  # SLIDE_DECK_FORMAT_DETAILED
        length_code: int = 3,  # SLIDE_DECK_LENGTH_DEFAULT
        language: str = "en",
        focus_prompt: str = "",
    ) -> dict | None:
        """Create a Slide Deck from notebook sources.

        Args:
            notebook_id: The notebook UUID
            source_ids: List of source UUIDs to include
            format_code: Format (1=Detailed Deck, 2=Presenter Slides)
            length_code: Length (1=Short, 3=Default)
            language: BCP-47 language code (e.g., "en", "es", "fr")
            focus_prompt: Optional text describing what AI should focus on

        Returns:
            Dict with artifact_id and status, or None on failure
        """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Build the slide deck options structure
        # Options at position 16: [[focus_prompt, language, format, length]]
        slide_deck_options = [[focus_prompt or None, language, format_code, length_code]]

        # Build the content array with 12 nulls between source_ids and options
        # Structure: [null, null, 8, [source_ids], null*12, [[options]]]
        content = [
            None, None,
            self.STUDIO_TYPE_SLIDE_DECK,
            sources_nested,
            None, None, None, None, None, None, None, None, None, None, None, None,  # 12 nulls (positions 4-15)
            slide_deck_options  # position 16
        ]

        # Build the full params
        params = [
            [2],
            notebook_id,
            content
        ]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            # Response: [[artifact_id, title, type, sources, status, ...]]
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "slide_deck",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "format": self._get_slide_deck_format_name(format_code),
                "length": self._get_slide_deck_length_name(length_code),
                "language": language,
            }

        return None

    def create_report(
        self,
        notebook_id: str,
        source_ids: list[str],
        report_format: str = "Briefing Doc",
        custom_prompt: str = "",
        language: str = "en",
    ) -> dict | None:
        """Create a Report from notebook sources.

        Args:
            notebook_id: The notebook UUID
            source_ids: List of source UUIDs to include
            report_format: Report format - one of:
                - "Briefing Doc": Key insights and important quotes
                - "Study Guide": Short-answer quiz, essay questions, glossary
                - "Blog Post": Insightful takeaways in readable article format
                - "Create Your Own": Custom format with user-defined prompt
            custom_prompt: Custom prompt when report_format="Create Your Own"
            language: BCP-47 language code (e.g., "en", "es", "fr")

        Returns:
            Dict with artifact_id and status, or None on failure
        """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Build source IDs in the simpler format: [[id1], [id2], ...]
        sources_simple = [[sid] for sid in source_ids]

        # Map report format to title, description, and prompt
        format_configs = {
            "Briefing Doc": {
                "title": "Briefing Doc",
                "description": "Key insights and important quotes",
                "prompt": (
                    "Create a comprehensive briefing document that includes an "
                    "Executive Summary, detailed analysis of key themes, important "
                    "quotes with context, and actionable insights."
                ),
            },
            "Study Guide": {
                "title": "Study Guide",
                "description": "Short-answer quiz, essay questions, glossary",
                "prompt": (
                    "Create a comprehensive study guide that includes key concepts, "
                    "short-answer practice questions, essay prompts for deeper "
                    "exploration, and a glossary of important terms."
                ),
            },
            "Blog Post": {
                "title": "Blog Post",
                "description": "Insightful takeaways in readable article format",
                "prompt": (
                    "Write an engaging blog post that presents the key insights "
                    "in an accessible, reader-friendly format. Include an attention-"
                    "grabbing introduction, well-organized sections, and a compelling "
                    "conclusion with takeaways."
                ),
            },
            "Create Your Own": {
                "title": "Custom Report",
                "description": "Custom format",
                "prompt": custom_prompt or "Create a report based on the provided sources.",
            },
        }

        if report_format not in format_configs:
            raise ValueError(
                f"Invalid report_format: {report_format}. "
                f"Must be one of: {list(format_configs.keys())}"
            )

        config = format_configs[report_format]

        # Build the report options structure
        # Options at position 7: [null, [title, desc, null, sources, lang, prompt, null, True]]
        report_options = [
            None,
            [
                config["title"],
                config["description"],
                None,
                sources_simple,
                language,
                config["prompt"],
                None,
                True
            ]
        ]

        # Build the content array
        # Structure: [null, null, 2, [source_ids], null, null, null, [report_options]]
        content = [
            None, None,
            self.STUDIO_TYPE_REPORT,
            sources_nested,
            None, None, None,
            report_options
        ]

        # Build the full params
        params = [
            [2],
            notebook_id,
            content
        ]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            # Response: [[artifact_id, title, type, sources, status, ...]]
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "report",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "format": report_format,
                "language": language,
            }

        return None

    def create_flashcards(
        self,
        notebook_id: str,
        source_ids: list[str],
        difficulty: str = "medium",
        card_count: str = "default",
    ) -> dict | None:
        """Create Flashcards from notebook sources.

        Args:
            notebook_id: The notebook UUID
            source_ids: List of source UUIDs to include
            difficulty: Difficulty level - "easy", "medium", or "hard"
            card_count: Card count preset - "default" (uses default count)

        Returns:
            Dict with artifact_id and status, or None on failure
        """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Map difficulty string to code
        difficulty_map = {
            "easy": self.FLASHCARD_DIFFICULTY_EASY,
            "medium": self.FLASHCARD_DIFFICULTY_MEDIUM,
            "hard": self.FLASHCARD_DIFFICULTY_HARD,
        }
        if difficulty.lower() not in difficulty_map:
            raise ValueError(f"Invalid difficulty: {difficulty}. Must be one of: {list(difficulty_map.keys())}")
        difficulty_code = difficulty_map[difficulty.lower()]

        # Card count code (default = 2)
        count_code = self.FLASHCARD_COUNT_DEFAULT

        # Build the flashcard options structure
        # Options at position 9: [null, [1, null*5, [difficulty, card_count]]]
        flashcard_options = [
            None,
            [
                1,  # Unknown (possibly default count base)
                None, None, None, None, None,
                [difficulty_code, count_code]
            ]
        ]

        # Build the content array
        # Structure: [null, null, 4, [source_ids], null*5, [flashcard_options]]
        content = [
            None, None,
            self.STUDIO_TYPE_FLASHCARDS,
            sources_nested,
            None, None, None, None, None,  # 5 nulls (positions 4-8)
            flashcard_options  # position 9
        ]

        # Build the full params
        params = [
            [2],
            notebook_id,
            content
        ]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            # Response: [[artifact_id, title, type, sources, status, ...]]
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "flashcards",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "difficulty": difficulty.lower(),
            }

        return None

    def generate_mind_map(
        self,
        source_ids: list[str],
    ) -> dict | None:
        """Generate a Mind Map JSON from sources.

        This is step 1 of 2 for creating a mind map. After generation,
        use save_mind_map() to save it to a notebook.

        Args:
            source_ids: List of source UUIDs to include

        Returns:
            Dict with mind_map_json and generation_id, or None on failure
        """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Build the params
        # Structure: [sources, null*4, ["interactive_mindmap", [["[CONTEXT]", ""]], ""], null, [2, null, [1]]]
        params = [
            sources_nested,
            None, None, None, None,
            ["interactive_mindmap", [["[CONTEXT]", ""]], ""],
            None,
            [2, None, [1]]
        ]

        body = self._build_request_body(self.RPC_GENERATE_MIND_MAP, params)
        url = self._build_url(self.RPC_GENERATE_MIND_MAP)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_GENERATE_MIND_MAP)

        if result and isinstance(result, list) and len(result) > 0:
            # Response is nested: [[json_string, null, [gen_ids]]]
            # So result[0] is [json_string, null, [gen_ids]]
            inner = result[0] if isinstance(result[0], list) else result

            mind_map_json = inner[0] if isinstance(inner[0], str) else None
            generation_info = inner[2] if len(inner) > 2 else None

            generation_id = None
            if isinstance(generation_info, list) and len(generation_info) > 0:
                generation_id = generation_info[0]

            return {
                "mind_map_json": mind_map_json,
                "generation_id": generation_id,
                "source_ids": source_ids,
            }

        return None

    def save_mind_map(
        self,
        notebook_id: str,
        mind_map_json: str,
        source_ids: list[str],
        title: str = "Mind Map",
    ) -> dict | None:
        """Save a generated Mind Map to a notebook.

        This is step 2 of 2 for creating a mind map. First use
        generate_mind_map() to create the JSON structure.

        Args:
            notebook_id: The notebook UUID
            mind_map_json: The JSON string from generate_mind_map()
            source_ids: List of source UUIDs used to generate the map
            title: Display title for the mind map

        Returns:
            Dict with mind_map_id and saved info, or None on failure
        """
        client = self._get_client()

        # Build source IDs in the simpler format: [[id1], [id2], ...]
        sources_simple = [[sid] for sid in source_ids]

        # Build the metadata structure
        # [2, null, null, 5, [[source_id1], [source_id2], ...]]
        metadata = [2, None, None, 5, sources_simple]

        # Build the params
        # [notebook_id, json_string, metadata, null, title]
        params = [
            notebook_id,
            mind_map_json,
            metadata,
            None,
            title
        ]

        body = self._build_request_body(self.RPC_SAVE_MIND_MAP, params)
        url = self._build_url(self.RPC_SAVE_MIND_MAP, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_SAVE_MIND_MAP)

        if result and isinstance(result, list) and len(result) > 0:
            # Response is nested: [[mind_map_id, json, metadata, null, title]]
            inner = result[0] if isinstance(result[0], list) else result

            mind_map_id = inner[0] if len(inner) > 0 else None
            saved_json = inner[1] if len(inner) > 1 else None
            saved_title = inner[4] if len(inner) > 4 else title

            return {
                "mind_map_id": mind_map_id,
                "notebook_id": notebook_id,
                "title": saved_title,
                "mind_map_json": saved_json,
            }

        return None

    def list_mind_maps(self, notebook_id: str) -> list[dict]:
        """List all Mind Maps in a notebook.

        Args:
            notebook_id: The notebook UUID

        Returns:
            List of mind map dicts with id, title, and json
        """
        client = self._get_client()

        # Params: [notebook_id]
        params = [notebook_id]

        body = self._build_request_body(self.RPC_LIST_MIND_MAPS, params)
        url = self._build_url(self.RPC_LIST_MIND_MAPS, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_LIST_MIND_MAPS)

        mind_maps = []
        if result and isinstance(result, list) and len(result) > 0:
            # Response: [[[mind_map_id, [details]], ...], [timestamp]]
            mind_map_list = result[0] if isinstance(result[0], list) else []

            for mind_map_data in mind_map_list:
                if not isinstance(mind_map_data, list) or len(mind_map_data) < 2:
                    continue

                mind_map_id = mind_map_data[0]
                details = mind_map_data[1] if len(mind_map_data) > 1 else []

                if isinstance(details, list) and len(details) >= 5:
                    # Details: [id, json, metadata, null, title]
                    mind_map_json = details[1] if len(details) > 1 else None
                    title = details[4] if len(details) > 4 else "Mind Map"
                    metadata = details[2] if len(details) > 2 else []

                    # Extract created_at from metadata
                    created_at = None
                    if isinstance(metadata, list) and len(metadata) > 2:
                        ts = metadata[2]
                        created_at = parse_timestamp(ts)

                    mind_maps.append({
                        "mind_map_id": mind_map_id,
                        "title": title,
                        "mind_map_json": mind_map_json,
                        "created_at": created_at,
                    })

        return mind_maps

    @staticmethod
    def _get_audio_format_name(format_code: int) -> str:
        """Convert audio format code to human-readable name."""
        formats = {
            1: "deep_dive",
            2: "brief",
            3: "critique",
            4: "debate",
        }
        return formats.get(format_code, "unknown")

    @staticmethod
    def _get_audio_length_name(length_code: int) -> str:
        """Convert audio length code to human-readable name."""
        lengths = {
            1: "short",
            2: "default",
            3: "long",
        }
        return lengths.get(length_code, "unknown")

    @staticmethod
    def _get_video_format_name(format_code: int) -> str:
        """Convert video format code to human-readable name."""
        formats = {
            1: "explainer",
            2: "brief",
        }
        return formats.get(format_code, "unknown")

    @staticmethod
    def _get_video_style_name(style_code: int) -> str:
        """Convert video style code to human-readable name."""
        styles = {
            1: "auto_select",
            2: "custom",
            3: "classic",
            4: "whiteboard",
            5: "kawaii",
            6: "anime",
            7: "watercolor",
            8: "retro_print",
            9: "heritage",
            10: "paper_craft",
        }
        return styles.get(style_code, "unknown")

    @staticmethod
    def _get_infographic_orientation_name(orientation_code: int) -> str:
        """Convert infographic orientation code to human-readable name."""
        orientations = {
            1: "landscape",
            2: "portrait",
            3: "square",
        }
        return orientations.get(orientation_code, "unknown")

    @staticmethod
    def _get_infographic_detail_name(detail_code: int) -> str:
        """Convert infographic detail level code to human-readable name."""
        details = {
            1: "concise",
            2: "standard",
            3: "detailed",
        }
        return details.get(detail_code, "unknown")

    @staticmethod
    def _get_slide_deck_format_name(format_code: int) -> str:
        """Convert slide deck format code to human-readable name."""
        formats = {
            1: "detailed_deck",
            2: "presenter_slides",
        }
        return formats.get(format_code, "unknown")

    @staticmethod
    def _get_slide_deck_length_name(length_code: int) -> str:
        """Convert slide deck length code to human-readable name."""
        lengths = {
            1: "short",
            3: "default",
        }
        return lengths.get(length_code, "unknown")

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None


def extract_cookies_from_chrome_export(cookie_header: str) -> dict[str, str]:
    """
    Extract cookies from a copy-pasted cookie header value.

    Usage:
    1. Go to notebooklm.google.com in Chrome
    2. Open DevTools > Network tab
    3. Refresh and find any request to notebooklm.google.com
    4. Copy the Cookie header value
    5. Pass it to this function
    """
    cookies = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


# Example usage (for testing)
if __name__ == "__main__":
    import sys

    print("Consumer NotebookLM API POC")
    print("=" * 50)
    print()
    print("To use this POC, you need to:")
    print("1. Go to notebooklm.google.com in Chrome")
    print("2. Open DevTools > Network tab")
    print("3. Find a request to notebooklm.google.com")
    print("4. Copy the entire Cookie header value")
    print()
    print("Then run:")
    print("  python consumer_notebooklm.py 'YOUR_COOKIE_HEADER'")
    print()

    if len(sys.argv) > 1:
        cookie_header = sys.argv[1]
        cookies = extract_cookies_from_chrome_export(cookie_header)

        print(f"Extracted {len(cookies)} cookies")
        print()

        # Session tokens - these need to be extracted from the page
        # To get these:
        # 1. Go to notebooklm.google.com in Chrome
        # 2. Open DevTools > Network tab
        # 3. Find any POST request to /_/LabsTailwindUi/data/batchexecute
        # 4. CSRF token: Look for 'at=' parameter in the request body
        # 5. Session ID: Look for 'f.sid=' parameter in the URL
        #
        # These tokens are session-specific and expire after some time.
        # For automated use, you'd need to extract them from the page's JavaScript.

        # Get tokens from environment or use defaults (update these if needed)
        import os
        csrf_token = os.environ.get(
            "NOTEBOOKLM_CSRF_TOKEN",
            "ACi2F2OxJshr6FHHGUtehylr0NVT:1766372302394"  # Update this
        )
        session_id = os.environ.get(
            "NOTEBOOKLM_SESSION_ID",
            "1975517010764758431"  # Update this
        )

        print(f"Using CSRF token: {csrf_token[:20]}...")
        print(f"Using session ID: {session_id}")
        print()

        client = ConsumerNotebookLMClient(cookies, csrf_token=csrf_token, session_id=session_id)

        try:
            # Demo: List notebooks
            print("Listing notebooks...")
            print()

            notebooks = client.list_notebooks(debug=False)

            print(f"Found {len(notebooks)} notebooks:")
            for nb in notebooks[:5]:  # Limit output
                print(f"  - {nb.title}")
                print(f"    ID: {nb.id}")
                print(f"    URL: {nb.url}")
                print(f"    Sources: {nb.source_count}")
                print()

            # Demo: Create a notebook (commented out to avoid creating test notebooks)
            # print("Creating a new notebook...")
            # new_nb = client.create_notebook(title="Test Notebook from API")
            # if new_nb:
            #     print(f"Created notebook: {new_nb.title}")
            #     print(f"  ID: {new_nb.id}")
            #     print(f"  URL: {new_nb.url}")

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error: {e}")
        finally:
            client.close()
