#!/usr/bin/env python3
"""NotebookLM MCP API client (notebooklm.google.com).

Internal API. See CLAUDE.md for full documentation.
"""

import json
import logging
import os
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


import httpx

from . import constants

# Configure logger (API internals only logged at DEBUG level, usually disabled)
logger = logging.getLogger("notebooklm_mcp.api")
logger.setLevel(logging.WARNING)  # Suppress internal API logs by default

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
    "yyryJe": "generate_mind_map",
    "CYK0Xb": "save_mind_map",
    "cFji9": "list_mind_maps",
    "AH0mwd": "delete_mind_map",
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
        # Parse the URL-encoded body
        parsed = urllib.parse.parse_qs(body.rstrip("&"))

        # Decode f.req (the main request data)
        if "f.req" in parsed:
            f_req_raw = parsed["f.req"][0]
            try:
                f_req = json.loads(f_req_raw)
                result["f.req"] = f_req
                # Extract the inner params if available
                if isinstance(f_req, list) and len(f_req) > 0:
                    inner = f_req[0]
                    if isinstance(inner, list) and len(inner) > 0:
                        rpc_call = inner[0]
                        if isinstance(rpc_call, list) and len(rpc_call) >= 2:
                            result["rpc_id"] = rpc_call[0]
                            # Parse the params JSON string
                            params_str = rpc_call[1]
                            if isinstance(params_str, str):
                                try:
                                    result["params"] = json.loads(params_str)
                                except json.JSONDecodeError:
                                    result["params"] = params_str
            except json.JSONDecodeError:
                result["f.req"] = f_req_raw

        # Include CSRF token reference (don't log actual value)
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
        # Flatten single-value lists
        return {k: v[0] if len(v) == 1 else v for k, v in params.items()}
    except Exception:
        return {}


class AuthenticationError(Exception):
    """Raised when authentication fails (HTTP 401/403 or RPC Error 16)."""
    pass


# Timeout configuration (seconds)
DEFAULT_TIMEOUT = 30.0  # Default for most operations
SOURCE_ADD_TIMEOUT = 120.0  # Extended timeout for all source operations (large slides/docs/websites)


# Ownership constants (from metadata position 0)
OWNERSHIP_MINE = constants.OWNERSHIP_MINE
OWNERSHIP_SHARED = constants.OWNERSHIP_SHARED


@dataclass
class ConversationTurn:
    """Represents a single turn in a conversation (query + response).

    Used to track conversation history for follow-up queries.
    NotebookLM requires the full conversation history in follow-up requests.
    """
    query: str       # The user's question
    answer: str      # The AI's response
    turn_number: int  # 1-indexed turn number in the conversation



def parse_timestamp(ts_array: list | None) -> str | None:
    """Convert [seconds, nanoseconds] timestamp array to ISO format string.
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
class Notebook:
    """Represents a NotebookLM notebook."""

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


class NotebookLMClient:
    """Client for NotebookLM MCP internal API."""

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
    RPC_DELETE_SOURCE = "tGMBJ"  # Delete a source from notebook
    RPC_GET_CONVERSATIONS = "hPTbtc"
    RPC_PREFERENCES = "hT54vc"
    RPC_SUBSCRIPTION = "ozz5Z"
    RPC_SETTINGS = "ZwVcOc"
    RPC_GET_SUMMARY = "VfAZjd"  # Get notebook summary and suggested report topics
    RPC_GET_SOURCE_GUIDE = "tr032e"  # Get source guide (AI summary + keyword chips)

    # Research RPCs (source discovery)
    RPC_START_FAST_RESEARCH = "Ljjv0c"  # Start Fast Research (Web or Drive)
    RPC_START_DEEP_RESEARCH = "QA9ei"   # Start Deep Research (Web only)
    RPC_POLL_RESEARCH = "e3bVqc"        # Poll research results
    RPC_IMPORT_RESEARCH = "LBwxtb"      # Import research sources

    # Research source types
    RESEARCH_SOURCE_WEB = constants.RESEARCH_SOURCE_WEB
    RESEARCH_SOURCE_DRIVE = constants.RESEARCH_SOURCE_DRIVE
    RESEARCH_MODE_FAST = constants.RESEARCH_MODE_FAST
    RESEARCH_MODE_DEEP = constants.RESEARCH_MODE_DEEP
    RESULT_TYPE_WEB = constants.RESULT_TYPE_WEB
    RESULT_TYPE_GOOGLE_DOC = constants.RESULT_TYPE_GOOGLE_DOC
    RESULT_TYPE_GOOGLE_SLIDES = constants.RESULT_TYPE_GOOGLE_SLIDES
    RESULT_TYPE_DEEP_REPORT = constants.RESULT_TYPE_DEEP_REPORT
    RESULT_TYPE_GOOGLE_SHEETS = constants.RESULT_TYPE_GOOGLE_SHEETS
    RPC_CREATE_STUDIO = "R7cb6c"   # Create Audio or Video Overview
    RPC_POLL_STUDIO = "gArtLc"     # Poll for studio content status
    RPC_DELETE_STUDIO = "V5N4be"   # Delete Audio or Video Overview

    # Studio content types
    STUDIO_TYPE_AUDIO = constants.STUDIO_TYPE_AUDIO
    STUDIO_TYPE_VIDEO = constants.STUDIO_TYPE_VIDEO
    AUDIO_FORMAT_DEEP_DIVE = constants.AUDIO_FORMAT_DEEP_DIVE
    AUDIO_FORMAT_BRIEF = constants.AUDIO_FORMAT_BRIEF
    AUDIO_FORMAT_CRITIQUE = constants.AUDIO_FORMAT_CRITIQUE
    AUDIO_FORMAT_DEBATE = constants.AUDIO_FORMAT_DEBATE

    # Audio Overview lengths
    AUDIO_LENGTH_SHORT = constants.AUDIO_LENGTH_SHORT
    AUDIO_LENGTH_DEFAULT = constants.AUDIO_LENGTH_DEFAULT
    AUDIO_LENGTH_LONG = constants.AUDIO_LENGTH_LONG
    VIDEO_FORMAT_EXPLAINER = constants.VIDEO_FORMAT_EXPLAINER
    VIDEO_FORMAT_BRIEF = constants.VIDEO_FORMAT_BRIEF

    # Video visual styles
    VIDEO_STYLE_AUTO_SELECT = constants.VIDEO_STYLE_AUTO_SELECT
    VIDEO_STYLE_CUSTOM = constants.VIDEO_STYLE_CUSTOM
    VIDEO_STYLE_CLASSIC = constants.VIDEO_STYLE_CLASSIC
    VIDEO_STYLE_WHITEBOARD = constants.VIDEO_STYLE_WHITEBOARD
    VIDEO_STYLE_KAWAII = constants.VIDEO_STYLE_KAWAII
    VIDEO_STYLE_ANIME = constants.VIDEO_STYLE_ANIME
    VIDEO_STYLE_WATERCOLOR = constants.VIDEO_STYLE_WATERCOLOR
    VIDEO_STYLE_RETRO_PRINT = constants.VIDEO_STYLE_RETRO_PRINT
    VIDEO_STYLE_HERITAGE = constants.VIDEO_STYLE_HERITAGE
    VIDEO_STYLE_PAPER_CRAFT = constants.VIDEO_STYLE_PAPER_CRAFT
    STUDIO_TYPE_REPORT = constants.STUDIO_TYPE_REPORT
    STUDIO_TYPE_FLASHCARDS = constants.STUDIO_TYPE_FLASHCARDS  # Also used for Quiz (differentiated by options)
    STUDIO_TYPE_INFOGRAPHIC = constants.STUDIO_TYPE_INFOGRAPHIC
    STUDIO_TYPE_SLIDE_DECK = constants.STUDIO_TYPE_SLIDE_DECK
    STUDIO_TYPE_DATA_TABLE = constants.STUDIO_TYPE_DATA_TABLE
    RPC_GENERATE_MIND_MAP = "yyryJe"  # Generate mind map JSON from sources
    RPC_SAVE_MIND_MAP = "CYK0Xb"      # Save generated mind map to notebook
    RPC_LIST_MIND_MAPS = "cFji9"       # List existing mind maps
    RPC_DELETE_MIND_MAP = "AH0mwd"     # Delete a mind map

    # Report format constants
    REPORT_FORMAT_BRIEFING_DOC = constants.REPORT_FORMAT_BRIEFING_DOC
    REPORT_FORMAT_STUDY_GUIDE = constants.REPORT_FORMAT_STUDY_GUIDE
    REPORT_FORMAT_BLOG_POST = constants.REPORT_FORMAT_BLOG_POST
    REPORT_FORMAT_CUSTOM = constants.REPORT_FORMAT_CUSTOM

    # Flashcard difficulty codes (suspected values)
    FLASHCARD_DIFFICULTY_EASY = constants.FLASHCARD_DIFFICULTY_EASY
    FLASHCARD_DIFFICULTY_MEDIUM = constants.FLASHCARD_DIFFICULTY_MEDIUM
    FLASHCARD_DIFFICULTY_HARD = constants.FLASHCARD_DIFFICULTY_HARD
    FLASHCARD_COUNT_DEFAULT = constants.FLASHCARD_COUNT_DEFAULT
    INFOGRAPHIC_ORIENTATION_LANDSCAPE = constants.INFOGRAPHIC_ORIENTATION_LANDSCAPE
    INFOGRAPHIC_ORIENTATION_PORTRAIT = constants.INFOGRAPHIC_ORIENTATION_PORTRAIT
    INFOGRAPHIC_ORIENTATION_SQUARE = constants.INFOGRAPHIC_ORIENTATION_SQUARE
    INFOGRAPHIC_DETAIL_CONCISE = constants.INFOGRAPHIC_DETAIL_CONCISE
    INFOGRAPHIC_DETAIL_STANDARD = constants.INFOGRAPHIC_DETAIL_STANDARD
    INFOGRAPHIC_DETAIL_DETAILED = constants.INFOGRAPHIC_DETAIL_DETAILED
    SLIDE_DECK_FORMAT_DETAILED = constants.SLIDE_DECK_FORMAT_DETAILED
    SLIDE_DECK_FORMAT_PRESENTER = constants.SLIDE_DECK_FORMAT_PRESENTER

    # Slide Deck length codes
    SLIDE_DECK_LENGTH_SHORT = constants.SLIDE_DECK_LENGTH_SHORT
    SLIDE_DECK_LENGTH_DEFAULT = constants.SLIDE_DECK_LENGTH_DEFAULT

    # Chat configuration goal/style codes
    CHAT_GOAL_DEFAULT = constants.CHAT_GOAL_DEFAULT
    CHAT_GOAL_CUSTOM = constants.CHAT_GOAL_CUSTOM
    CHAT_GOAL_LEARNING_GUIDE = constants.CHAT_GOAL_LEARNING_GUIDE

    # Chat configuration response length codes
    CHAT_RESPONSE_DEFAULT = constants.CHAT_RESPONSE_DEFAULT
    CHAT_RESPONSE_LONGER = constants.CHAT_RESPONSE_LONGER
    CHAT_RESPONSE_SHORTER = constants.CHAT_RESPONSE_SHORTER

    # Source type constants (from metadata position 4)
    # These represent the Google Workspace document type, NOT the source origin
    SOURCE_TYPE_GOOGLE_DOCS = constants.SOURCE_TYPE_GOOGLE_DOCS
    SOURCE_TYPE_GOOGLE_OTHER = constants.SOURCE_TYPE_GOOGLE_OTHER
    SOURCE_TYPE_PASTED_TEXT = constants.SOURCE_TYPE_PASTED_TEXT

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

        # Conversation cache for follow-up queries
        # Key: conversation_id, Value: list of ConversationTurn objects
        self._conversation_cache: dict[str, list[ConversationTurn]] = {}

        # Request counter for _reqid parameter (required for query endpoint)
        import random
        self._reqid_counter = random.randint(100000, 999999)

        # Only refresh CSRF token if not provided - tokens actually last hours/days, not minutes
        # The retry logic in _call_rpc() handles expired tokens gracefully
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
                    "Authentication expired. AI assistants: Run `notebooklm-mcp-auth` via Bash/terminal tool to re-authenticate automatically. Users: Run `notebooklm-mcp-auth` in your terminal."
                )

            if response.status_code != 200:
                raise ValueError(f"Failed to fetch NotebookLM page: HTTP {response.status_code}")

            html = response.text

            # Extract CSRF token (SNlM0e)
            csrf_match = re.search(r'"SNlM0e":"([^"]+)"', html)
            if not csrf_match:
                # Save HTML for debugging
                from pathlib import Path
                debug_dir = Path.home() / ".notebooklm-mcp"
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

            # Cache the extracted tokens to avoid re-fetching the page on next request
            self._update_cached_tokens()

    def _update_cached_tokens(self) -> None:
        """Update the cached auth tokens with newly extracted CSRF token and session ID.

        This avoids re-fetching the NotebookLM page on every client initialization,
        significantly improving performance for subsequent API calls.
        """
        try:
            import time
            from .auth import AuthTokens, save_tokens_to_cache, load_cached_tokens

            # Load existing cache or create new
            cached = load_cached_tokens()
            if cached:
                # Update existing cache with new tokens
                cached.csrf_token = self.csrf_token
                cached.session_id = self._session_id
            else:
                # Create new cache entry
                cached = AuthTokens(
                    cookies=self.cookies,
                    csrf_token=self.csrf_token,
                    session_id=self._session_id,
                    extracted_at=time.time(),
                )

            save_tokens_to_cache(cached, silent=True)
        except Exception:
            # Silently fail - caching is an optimization, not critical
            pass

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
        # Use separators to match Chrome's compact format (no spaces)
        params_json = json.dumps(params, separators=(',', ':'))

        f_req = [[[rpc_id, params_json, None, "generic"]]]
        f_req_json = json.dumps(f_req, separators=(',', ':'))

        # URL encode (safe='' encodes all characters including /)
        body_parts = [f"f.req={urllib.parse.quote(f_req_json, safe='')}"]

        if self.csrf_token:
            body_parts.append(f"at={urllib.parse.quote(self.csrf_token, safe='')}")

        # Add trailing & to match NotebookLM's format
        return "&".join(body_parts) + "&"

    def _build_url(self, rpc_id: str, source_path: str = "/") -> str:
        """Build the batchexecute URL with query params."""
        params = {
            "rpcids": rpc_id,
            "source-path": source_path,
            "bl": os.environ.get("NOTEBOOKLM_BL", "boq_labs-tailwind-frontend_20260108.06_p0"),
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
                            # Check for generic error signature (e.g. auth expired)
                            # Signature: ["wrb.fr", "RPC_ID", null, null, null, [16], "generic"]
                            if len(item) > 6 and item[6] == "generic" and isinstance(item[5], list) and 16 in item[5]:
                                raise AuthenticationError("RPC Error 16: Authentication expired")

                            result_str = item[2]
                            if isinstance(result_str, str):
                                try:
                                    return json.loads(result_str)
                                except json.JSONDecodeError:
                                    return result_str
                            return result_str
        return None

    def _call_rpc(
        self,
        rpc_id: str,
        params: Any,
        path: str = "/",
        timeout: float | None = None,
        _retry: bool = False,
        _deep_retry: bool = False,
    ) -> Any:
        """Execute an RPC call and return the extracted result.

        Includes automatic retry on auth failures with three-layer recovery:
        1. Refresh CSRF/session tokens (fast, handles token expiry)
        2. Reload cookies from disk (handles external re-authentication)
        3. Run headless auth (auto-refresh if Chrome profile has saved login)
        """
        client = self._get_client()
        body = self._build_request_body(rpc_id, params)
        url = self._build_url(rpc_id, path)

        # Enhanced debug logging
        if logger.isEnabledFor(logging.DEBUG):
            method_name = RPC_NAMES.get(rpc_id, "unknown")
            logger.debug("=" * 70)
            logger.debug(f"RPC Call: {rpc_id} ({method_name})")
            logger.debug("-" * 70)

            # Parse and display URL params
            url_params = _parse_url_params(url)
            logger.debug("URL Parameters:")
            for key, value in url_params.items():
                logger.debug(f"  {key}: {value}")

            # Decode and display request body
            logger.debug("-" * 70)
            logger.debug("Request Params:")
            decoded_body = _decode_request_body(body)
            if "params" in decoded_body:
                logger.debug(_format_debug_json(decoded_body["params"]))
            elif "f.req" in decoded_body:
                logger.debug(_format_debug_json(decoded_body["f.req"]))
            else:
                logger.debug(_format_debug_json(decoded_body))

        try:
            if timeout:
                response = client.post(url, content=body, timeout=timeout)
            else:
                response = client.post(url, content=body)

            # Log response before raise_for_status (so we can see error responses)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("-" * 70)
                logger.debug(f"Response Status: {response.status_code}")
                if response.status_code >= 400:
                    logger.debug("Error Response Body:")
                    logger.debug(response.text[:2000] if len(response.text) > 2000 else response.text)
                    logger.debug("=" * 70)

            response.raise_for_status()

            # Check for RPC-level errors (soft auth failure)
            parsed = self._parse_response(response.text)
            result = self._extract_rpc_result(parsed, rpc_id)

            # Enhanced debug logging for extracted result
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("-" * 70)
                logger.debug("Response Data:")
                logger.debug(_format_debug_json(result))
                logger.debug("=" * 70)
            
            return result

        except (httpx.HTTPStatusError, AuthenticationError) as e:
            # Check for auth failures (401/403 HTTP or RPC Error 16)
            is_http_auth = isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (401, 403)
            is_rpc_auth = isinstance(e, AuthenticationError)
            
            if not (is_http_auth or is_rpc_auth):
                # Not an auth error, re-raise immediately
                raise
            
            # Layer 1: Refresh CSRF/session tokens (first retry only)
            if not _retry:
                try:
                    self._refresh_auth_tokens()
                    self._client = None
                    return self._call_rpc(rpc_id, params, path, timeout, _retry=True)
                except ValueError:
                    # CSRF refresh failed (cookies expired) - continue to layer 2
                    pass
            
            # Layer 2 & 3: Reload from disk or run headless auth (deep retry)
            if not _deep_retry:
                if self._try_reload_or_headless_auth():
                    self._client = None
                    return self._call_rpc(rpc_id, params, path, timeout, _retry=True, _deep_retry=True)
            
            # All recovery attempts failed
            raise AuthenticationError(
                "Authentication expired. Run 'notebooklm-mcp-auth' in your terminal to re-authenticate."
            )

    def _try_reload_or_headless_auth(self) -> bool:
        """Try to recover authentication by reloading from disk or running headless auth.
        
        Returns True if new valid tokens were obtained, False otherwise.
        """
        from .auth import load_cached_tokens, get_cache_path
        
        # Check if auth.json has tokens - always try them since current tokens failed
        cache_path = get_cache_path()
        if cache_path.exists():
            cached = load_cached_tokens()
            if cached and cached.cookies:
                # Always reload from disk when auth fails - current tokens are known-bad
                # The cached tokens may be fresher (user ran notebooklm-mcp-auth)
                # or the same, but worth retrying with a fresh CSRF token extraction
                self.cookies = cached.cookies
                self.csrf_token = ""  # Force re-extraction of CSRF token
                self._session_id = ""  # Force re-extraction of session ID
                return True
        
        # Try headless auth if Chrome profile exists
        try:
            from .auth_cli import run_headless_auth
            tokens = run_headless_auth()
            if tokens:
                self.cookies = tokens.cookies
                self.csrf_token = tokens.csrf_token
                self._session_id = tokens.session_id
                return True
        except Exception:
            pass
        
        return False

    # =========================================================================
    # Conversation Management (for query follow-ups)
    # =========================================================================

    def _build_conversation_history(self, conversation_id: str) -> list | None:
        """Build the conversation history array for follow-up queries.

        Chrome expects history in format: [[answer, null, 2], [query, null, 1], ...]
        where type 1 = user message, type 2 = AI response.

        The history includes ALL previous turns, not just the most recent one.
        Turns are added in chronological order (oldest first).

        Args:
            conversation_id: The conversation ID to get history for

        Returns:
            List in Chrome's expected format, or None if no history exists
        """
        turns = self._conversation_cache.get(conversation_id, [])
        if not turns:
            return None

        history = []
        # Add turns in chronological order (oldest first)
        # Each turn adds: [answer, null, 2] then [query, null, 1]
        for turn in turns:
            history.append([turn.answer, None, 2])
            history.append([turn.query, None, 1])

        return history if history else None

    def _cache_conversation_turn(
        self, conversation_id: str, query: str, answer: str
    ) -> None:
        """Cache a conversation turn for future follow-up queries.
    """
        if conversation_id not in self._conversation_cache:
            self._conversation_cache[conversation_id] = []

        turn_number = len(self._conversation_cache[conversation_id]) + 1
        turn = ConversationTurn(query=query, answer=answer, turn_number=turn_number)
        self._conversation_cache[conversation_id].append(turn)

    def clear_conversation(self, conversation_id: str) -> bool:
        """Clear the conversation cache for a specific conversation.
    """
        if conversation_id in self._conversation_cache:
            del self._conversation_cache[conversation_id]
            return True
        return False

    def get_conversation_history(self, conversation_id: str) -> list[dict] | None:
        """Get the conversation history for a specific conversation.
    """
        turns = self._conversation_cache.get(conversation_id)
        if not turns:
            return None

        return [
            {"turn": t.turn_number, "query": t.query, "answer": t.answer}
            for t in turns
        ]

    # =========================================================================
    # Notebook Operations
    # =========================================================================

    def list_notebooks(self, debug: bool = False) -> list[Notebook]:
        """List all notebooks."""
        client = self._get_client()

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
                        notebooks.append(Notebook(
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
        """Get notebook details."""
        return self._call_rpc(
            self.RPC_GET_NOTEBOOK,
            [notebook_id, None, [2], None, 0],
            f"/notebook/{notebook_id}",
        )

    def get_notebook_summary(self, notebook_id: str) -> dict[str, Any]:
        """Get AI-generated summary and suggested topics for a notebook."""
        result = self._call_rpc(
            self.RPC_GET_SUMMARY, [notebook_id, [2]], f"/notebook/{notebook_id}"
        )
        summary = ""
        suggested_topics = []

        if result and isinstance(result, list):
            # Summary is at result[0][0]
            if len(result) > 0 and isinstance(result[0], list) and len(result[0]) > 0:
                summary = result[0][0]

            # Suggested topics are at result[1][0]
            if len(result) > 1 and result[1]:
                topics_data = result[1][0] if isinstance(result[1], list) and len(result[1]) > 0 else []
                for topic in topics_data:
                    if isinstance(topic, list) and len(topic) >= 2:
                        suggested_topics.append({
                            "question": topic[0],
                            "prompt": topic[1],
                        })

        return {
            "summary": summary,
            "suggested_topics": suggested_topics,
        }

    def get_source_guide(self, source_id: str) -> dict[str, Any]:
        """Get AI-generated summary and keywords for a source."""
        result = self._call_rpc(self.RPC_GET_SOURCE_GUIDE, [[[[source_id]]]], "/")
        summary = ""
        keywords = []

        if result and isinstance(result, list):
            if len(result) > 0 and isinstance(result[0], list):
                if len(result[0]) > 0 and isinstance(result[0][0], list):
                    inner = result[0][0]

                    if len(inner) > 1 and isinstance(inner[1], list) and len(inner[1]) > 0:
                        summary = inner[1][0]

                    if len(inner) > 2 and isinstance(inner[2], list) and len(inner[2]) > 0:
                        keywords = inner[2][0] if isinstance(inner[2][0], list) else []

        return {
            "summary": summary,
            "keywords": keywords,
        }

    def get_source_fulltext(self, source_id: str) -> dict[str, Any]:
        """Get the full text content of a source.

        Returns the raw text content that was indexed from the source,
        along with metadata like title and source type.

        Args:
            source_id: The source UUID

        Returns:
            Dict with content, title, source_type, and char_count
        """
        # The hizoJc RPC returns source details including full text
        params = [[source_id], [2], [2]]
        result = self._call_rpc(self.RPC_GET_SOURCE, params, "/")

        content = ""
        title = ""
        source_type = ""
        url = None

        if result and isinstance(result, list):
            # Response structure:
            # result[0] = [[source_id], title, metadata, ...]
            # result[1] = null
            # result[2] = null
            # result[3] = [[content_blocks]]
            #
            # Each content block: [start_pos, end_pos, content_data, ...]

            # Extract from result[0] which contains source metadata
            if len(result) > 0 and isinstance(result[0], list):
                source_meta = result[0]

                # Title is at position 1
                if len(source_meta) > 1 and isinstance(source_meta[1], str):
                    title = source_meta[1]

                # Metadata is at position 2
                if len(source_meta) > 2 and isinstance(source_meta[2], list):
                    metadata = source_meta[2]
                    # Source type code is at position 4
                    if len(metadata) > 4:
                        type_code = metadata[4]
                        source_type = constants.SOURCE_TYPES.get_name(type_code)

                    # URL might be at position 7 for web sources
                    if len(metadata) > 7 and isinstance(metadata[7], list):
                        url_info = metadata[7]
                        if len(url_info) > 0 and isinstance(url_info[0], str):
                            url = url_info[0]

            # Extract content from result[3][0] - array of content blocks
            if len(result) > 3 and isinstance(result[3], list):
                content_wrapper = result[3]
                if len(content_wrapper) > 0 and isinstance(content_wrapper[0], list):
                    content_blocks = content_wrapper[0]
                    # Collect all text from content blocks
                    text_parts = []
                    for block in content_blocks:
                        if isinstance(block, list):
                            # Each block is [start, end, content_data, ...]
                            # Extract all text strings recursively
                            texts = self._extract_all_text(block)
                            text_parts.extend(texts)
                    content = "\n\n".join(text_parts)

        return {
            "content": content,
            "title": title,
            "source_type": source_type,
            "url": url,
            "char_count": len(content),
        }

    def _extract_all_text(self, data: list) -> list[str]:
        """Recursively extract all text strings from nested arrays."""
        texts = []
        for item in data:
            if isinstance(item, str) and len(item) > 0:
                texts.append(item)
            elif isinstance(item, list):
                texts.extend(self._extract_all_text(item))
        return texts

    def create_notebook(self, title: str = "") -> Notebook | None:
        """Create a new notebook."""
        params = [title, None, None, [2], [1, None, None, None, None, None, None, None, None, None, [1]]]
        result = self._call_rpc(self.RPC_CREATE_NOTEBOOK, params)
        if result and isinstance(result, list) and len(result) >= 3:
            notebook_id = result[2]
            if notebook_id:
                return Notebook(
                    id=notebook_id,
                    title=title or "Untitled notebook",
                    source_count=0,
                    sources=[],
                )
        return None

    def rename_notebook(self, notebook_id: str, new_title: str) -> bool:
        """Rename a notebook."""
        params = [notebook_id, [[None, None, None, [None, new_title]]]]
        result = self._call_rpc(self.RPC_RENAME_NOTEBOOK, params, f"/notebook/{notebook_id}")
        return result is not None

    def configure_chat(
        self,
        notebook_id: str,
        goal: str = "default",
        custom_prompt: str | None = None,
        response_length: str = "default",
    ) -> dict[str, Any]:
        """Configure chat goal/style and response length for a notebook."""
        goal_code = constants.CHAT_GOALS.get_code(goal)

        # Validate custom prompt
        if goal == "custom":
            if not custom_prompt:
                raise ValueError("custom_prompt is required when goal='custom'")
            if len(custom_prompt) > 10000:
                raise ValueError(f"custom_prompt exceeds 10000 chars (got {len(custom_prompt)})")

        # Map response length string to code
        length_code = constants.CHAT_RESPONSE_LENGTHS.get_code(response_length)

        if goal == "custom" and custom_prompt:
            goal_setting = [goal_code, custom_prompt]
        else:
            goal_setting = [goal_code]

        chat_settings = [goal_setting, [length_code]]
        params = [notebook_id, [[None, None, None, None, None, None, None, chat_settings]]]
        result = self._call_rpc(self.RPC_RENAME_NOTEBOOK, params, f"/notebook/{notebook_id}")

        if result:
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

        params = [[notebook_id], [2]]
        body = self._build_request_body(self.RPC_DELETE_NOTEBOOK, params)
        url = self._build_url(self.RPC_DELETE_NOTEBOOK)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_DELETE_NOTEBOOK)

        return result is not None

    def check_source_freshness(self, source_id: str) -> bool | None:
        """Check if a Drive source is fresh (up-to-date with Google Drive).
    """
        client = self._get_client()

        params = [None, [source_id], [2]]
        body = self._build_request_body(self.RPC_CHECK_FRESHNESS, params)
        url = self._build_url(self.RPC_CHECK_FRESHNESS)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CHECK_FRESHNESS)

        # true = fresh, false = stale
        if result and isinstance(result, list) and len(result) > 0:
            inner = result[0] if result else []
            if isinstance(inner, list) and len(inner) >= 2:
                return inner[1]  # true = fresh, false = stale
        return None

    def sync_drive_source(self, source_id: str) -> dict | None:
        """Sync a Drive source with the latest content from Google Drive.
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

        if result and isinstance(result, list) and len(result) > 0:
            source_data = result[0] if result else []
            if isinstance(source_data, list) and len(source_data) >= 3:
                source_id_result = source_data[0][0] if source_data[0] else None
                title = source_data[1] if len(source_data) > 1 else "Unknown"
                metadata = source_data[2] if len(source_data) > 2 else []

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

    def delete_source(self, source_id: str) -> bool:
        """Delete a source from a notebook permanently.

        WARNING: This action is IRREVERSIBLE. The source will be permanently
        deleted from the notebook.

        Args:
            source_id: The source UUID to delete

        Returns:
            True on success, False on failure
        """
        client = self._get_client()

        # Delete source params: [[["source_id"]], [2]]
        # Note: Extra nesting compared to delete_notebook
        params = [[[source_id]], [2]]
        body = self._build_request_body(self.RPC_DELETE_SOURCE, params)
        url = self._build_url(self.RPC_DELETE_SOURCE)

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_DELETE_SOURCE)

        # Response is typically [] on success
        return result is not None

    def get_notebook_sources_with_types(self, notebook_id: str) -> list[dict]:
        """Get all sources from a notebook with their type information.
    """
        result = self.get_notebook(notebook_id)

        sources = []
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

                        # Extract URL if available (position 7)
                        url = None
                        if isinstance(metadata, list) and len(metadata) > 7:
                            url_info = metadata[7]
                            if isinstance(url_info, list) and len(url_info) > 0:
                                url = url_info[0]

                        sources.append({
                            "id": source_id,
                            "title": title,
                            "source_type": source_type,
                            "source_type_name": constants.SOURCE_TYPES.get_name(source_type),
                            "url": url,
                            "drive_doc_id": drive_doc_id,
                            "can_sync": can_sync,  # True for Drive docs AND Gemini Notes
                        })

        return sources


    def add_url_source(self, notebook_id: str, url: str) -> dict | None:
        """Add a URL (website or YouTube) as a source to a notebook.
    """
        client = self._get_client()

        # URL position differs for YouTube vs regular websites:
        # - YouTube: position 7
        # - Regular websites: position 2
        is_youtube = "youtube.com" in url.lower() or "youtu.be" in url.lower()

        if is_youtube:
            # YouTube: [null, null, null, null, null, null, null, [url], null, null, 1]
            source_data = [None, None, None, None, None, None, None, [url], None, None, 1]
        else:
            # Regular website: [null, null, [url], null, null, null, null, null, null, null, 1]
            source_data = [None, None, [url], None, None, None, None, None, None, None, 1]

        params = [
            [source_data],
            notebook_id,
            [2],
            [1, None, None, None, None, None, None, None, None, None, [1]]
        ]
        body = self._build_request_body(self.RPC_ADD_SOURCE, params)
        source_path = f"/notebook/{notebook_id}"
        url_endpoint = self._build_url(self.RPC_ADD_SOURCE, source_path)

        try:
            response = client.post(url_endpoint, content=body, timeout=SOURCE_ADD_TIMEOUT)
            response.raise_for_status()
        except httpx.TimeoutException:
            # Large pages may take longer than the timeout but still succeed on backend
            return {
                "status": "timeout",
                "message": f"Operation timed out after {SOURCE_ADD_TIMEOUT}s but may have succeeded. Check notebook sources before retrying.",
            }

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_ADD_SOURCE)

        if result and isinstance(result, list) and len(result) > 0:
            source_list = result[0] if result else []
            if source_list and len(source_list) > 0:
                source_data = source_list[0]
                source_id = source_data[0][0] if source_data[0] else None
                source_title = source_data[1] if len(source_data) > 1 else "Untitled"
                return {"id": source_id, "title": source_title}
        return None

    def add_text_source(self, notebook_id: str, text: str, title: str = "Pasted Text") -> dict | None:
        """Add pasted text as a source to a notebook.
    """
        client = self._get_client()

        # Text source params structure:
        source_data = [None, [title, text], None, 2, None, None, None, None, None, None, 1]
        params = [
            [source_data],
            notebook_id,
            [2],
            [1, None, None, None, None, None, None, None, None, None, [1]]
        ]
        body = self._build_request_body(self.RPC_ADD_SOURCE, params)
        source_path = f"/notebook/{notebook_id}"
        url_endpoint = self._build_url(self.RPC_ADD_SOURCE, source_path)

        try:
            response = client.post(url_endpoint, content=body, timeout=SOURCE_ADD_TIMEOUT)
            response.raise_for_status()
        except httpx.TimeoutException:
            return {
                "status": "timeout",
                "message": f"Operation timed out after {SOURCE_ADD_TIMEOUT}s but may have succeeded. Check notebook sources before retrying.",
            }

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
    """
        client = self._get_client()

        # Drive source params structure (verified from network capture):
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
            [source_data],
            notebook_id,
            [2],
            [1, None, None, None, None, None, None, None, None, None, [1]]
        ]
        body = self._build_request_body(self.RPC_ADD_SOURCE, params)
        source_path = f"/notebook/{notebook_id}"
        url_endpoint = self._build_url(self.RPC_ADD_SOURCE, source_path)

        try:
            response = client.post(url_endpoint, content=body, timeout=SOURCE_ADD_TIMEOUT)
            response.raise_for_status()
        except httpx.TimeoutException:
            # Large files may take longer than the timeout but still succeed on backend
            return {
                "status": "timeout",
                "message": f"Operation timed out after {SOURCE_ADD_TIMEOUT}s but may have succeeded. Check notebook sources before retrying.",
            }

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

    def query(
        self,
        notebook_id: str,
        query_text: str,
        source_ids: list[str] | None = None,
        conversation_id: str | None = None,
        timeout: float = 120.0,
    ) -> dict | None:
        """Query the notebook with a question.

        Supports both new conversations and follow-up queries. For follow-ups,
        the conversation history is automatically included from the cache.

        Args:
            notebook_id: The notebook UUID
            query_text: The question to ask
            source_ids: Optional list of source IDs to query (default: all sources)
            conversation_id: Optional conversation ID for follow-up questions.
                           If None, starts a new conversation.
                           If provided and exists in cache, includes conversation history.
            timeout: Request timeout in seconds (default: 120.0)

        Returns:
            Dict with:
            - answer: The AI's response text
            - conversation_id: ID to use for follow-up questions
            - turn_number: Which turn this is in the conversation (1 = first)
            - is_follow_up: Whether this was a follow-up query
            - raw_response: The raw parsed response (for debugging)
        """
        import uuid

        client = self._get_client()

        # If no source_ids provided, get them from the notebook
        if source_ids is None:
            notebook_data = self.get_notebook(notebook_id)
            source_ids = self._extract_source_ids_from_notebook(notebook_data)

        # Determine if this is a new conversation or follow-up
        is_new_conversation = conversation_id is None
        if is_new_conversation:
            conversation_id = str(uuid.uuid4())
            conversation_history = None
        else:
            # Check if we have cached history for this conversation
            conversation_history = self._build_conversation_history(conversation_id)

        # Build source IDs structure: [[[sid]]] for each source (3 brackets, not 4!)
        sources_array = [[[sid]] for sid in source_ids] if source_ids else []

        # Query params structure (from network capture)
        # For new conversations: params[2] = None
        # For follow-ups: params[2] = [[answer, null, 2], [query, null, 1], ...]
        params = [
            sources_array,
            query_text,
            conversation_history,  # None for new, history array for follow-ups
            [2, None, [1]],
            conversation_id,
        ]

        # Use compact JSON format matching Chrome (no spaces)
        params_json = json.dumps(params, separators=(",", ":"))

        f_req = [None, params_json]
        f_req_json = json.dumps(f_req, separators=(",", ":"))

        # URL encode with safe='' to encode all characters including /
        body_parts = [f"f.req={urllib.parse.quote(f_req_json, safe='')}"]
        if self.csrf_token:
            body_parts.append(f"at={urllib.parse.quote(self.csrf_token, safe='')}")
        # Add trailing & to match NotebookLM's format
        body = "&".join(body_parts) + "&"

        self._reqid_counter += 100000  # Increment counter
        url_params = {
            "bl": os.environ.get("NOTEBOOKLM_BL", "boq_labs-tailwind-frontend_20260108.06_p0"),
            "hl": "en",
            "_reqid": str(self._reqid_counter),
            "rt": "c",
        }
        if self._session_id:
            url_params["f.sid"] = self._session_id

        query_string = urllib.parse.urlencode(url_params)
        url = f"{self.BASE_URL}{self.QUERY_ENDPOINT}?{query_string}"

        response = client.post(url, content=body, timeout=timeout)
        response.raise_for_status()

        # Parse streaming response
        answer_text = self._parse_query_response(response.text)

        # Cache this turn for future follow-ups (only if we got an answer)
        if answer_text:
            self._cache_conversation_turn(conversation_id, query_text, answer_text)

        # Calculate turn number
        turns = self._conversation_cache.get(conversation_id, [])
        turn_number = len(turns)

        return {
            "answer": answer_text,
            "conversation_id": conversation_id,
            "turn_number": turn_number,
            "is_follow_up": not is_new_conversation,
            "raw_response": response.text[:1000] if response.text else "",  # Truncate for debugging
        }

    def _extract_source_ids_from_notebook(self, notebook_data: Any) -> list[str]:
        """Extract source IDs from notebook data.
    """
        source_ids = []
        if not notebook_data or not isinstance(notebook_data, list):
            return source_ids

        try:
            # Notebook structure: [[notebook_title, sources_array, notebook_id, ...]]
            # The outer array contains one element with all notebook info
            # Sources are at position [0][1]
            if len(notebook_data) > 0 and isinstance(notebook_data[0], list):
                notebook_info = notebook_data[0]
                if len(notebook_info) > 1 and isinstance(notebook_info[1], list):
                    sources = notebook_info[1]
                    for source in sources:
                        # Each source: [[source_id], title, metadata, [null, 2]]
                        if isinstance(source, list) and len(source) > 0:
                            source_id_wrapper = source[0]
                            if isinstance(source_id_wrapper, list) and len(source_id_wrapper) > 0:
                                source_id = source_id_wrapper[0]
                                if isinstance(source_id, str):
                                    source_ids.append(source_id)
        except (IndexError, TypeError):
            pass

        return source_ids

    def _parse_query_response(self, response_text: str) -> str:
        """Parse the streaming query response and extract the final answer.

        The query endpoint returns a streaming response with multiple chunks.
        Each chunk has a type indicator: 1 = actual answer, 2 = thinking step.

        Response format:
        )]}'
        <byte_count>
        [[["wrb.fr", null, "<json_with_text>", ...]]]
        ...more chunks...

        Strategy: Find the LONGEST chunk that is marked as type 1 (actual answer).
        If no type 1 chunks found, fall back to longest overall.

        Args:
            response_text: Raw response text from the query endpoint

        Returns:
            The extracted answer text, or empty string if parsing fails
        """
        # Remove anti-XSSI prefix
        if response_text.startswith(")]}'"):
            response_text = response_text[4:]

        lines = response_text.strip().split("\n")
        longest_answer = ""
        longest_thinking = ""

        # Parse chunks - prioritize type 1 (answers) over type 2 (thinking)
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue

            # Try to parse as byte count (indicates next line is JSON)
            try:
                int(line)
                i += 1
                if i < len(lines):
                    json_line = lines[i]
                    text, is_answer = self._extract_answer_from_chunk(json_line)
                    if text:
                        if is_answer and len(text) > len(longest_answer):
                            longest_answer = text
                        elif not is_answer and len(text) > len(longest_thinking):
                            longest_thinking = text
                i += 1
            except ValueError:
                # Not a byte count, try to parse as JSON directly
                text, is_answer = self._extract_answer_from_chunk(line)
                if text:
                    if is_answer and len(text) > len(longest_answer):
                        longest_answer = text
                    elif not is_answer and len(text) > len(longest_thinking):
                        longest_thinking = text
                i += 1

        # Return answer if found, otherwise fall back to thinking
        return longest_answer if longest_answer else longest_thinking

    def _extract_answer_from_chunk(self, json_str: str) -> tuple[str | None, bool]:
        """Extract answer text from a single JSON chunk.

        The chunk structure is:
        [["wrb.fr", null, "<nested_json>", ...]]

        The nested_json contains: [["answer_text", null, [...], null, [type_info]]]
        where type_info is an array ending with:
        - 1 = actual answer
        - 2 = thinking step

        Args:
            json_str: A single JSON chunk from the response

        Returns:
            Tuple of (text, is_answer) where is_answer is True for actual answers (type 1)
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return None, False

        if not isinstance(data, list) or len(data) == 0:
            return None, False

        for item in data:
            if not isinstance(item, list) or len(item) < 3:
                continue
            if item[0] != "wrb.fr":
                continue

            inner_json_str = item[2]
            if not isinstance(inner_json_str, str):
                continue

            try:
                inner_data = json.loads(inner_json_str)
            except json.JSONDecodeError:
                continue

            # Type indicator is at inner_data[0][4][-1]: 1 = answer, 2 = thinking
            if isinstance(inner_data, list) and len(inner_data) > 0:
                first_elem = inner_data[0]
                if isinstance(first_elem, list) and len(first_elem) > 0:
                    answer_text = first_elem[0]
                    if isinstance(answer_text, str) and len(answer_text) > 20:
                        # Check type indicator at first_elem[4][-1]
                        is_answer = False
                        if len(first_elem) > 4 and isinstance(first_elem[4], list):
                            type_info = first_elem[4]
                            # The type is nested: [[...], None, None, None, type_code]
                            # where type_code is 1 (answer) or 2 (thinking)
                            if len(type_info) > 0 and isinstance(type_info[-1], int):
                                is_answer = type_info[-1] == 1
                        return answer_text, is_answer
                elif isinstance(first_elem, str) and len(first_elem) > 20:
                    return first_elem, False

        return None, False

    def start_research(
        self,
        notebook_id: str,
        query: str,
        source: str = "web",
        mode: str = "fast",
    ) -> dict | None:
        """Start a research session to discover sources.
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
            params = [[query, source_type], None, 1, notebook_id]
            rpc_id = self.RPC_START_FAST_RESEARCH
        else:
            # Deep Research: QA9ei
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

    def poll_research(self, notebook_id: str, target_task_id: str | None = None) -> dict | None:
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

        # Unwrap the outer array to get [[task_id, task_info, status], [ts1], [ts2]]
        if isinstance(result[0], list) and len(result[0]) > 0 and isinstance(result[0][0], list):
            result = result[0]

        # Result may contain multiple research tasks - find the most recent/active one
        research_tasks = []

        for task_data in result:
            # task_data structure: [task_id, task_info] (only 2 elements for deep research)
            if not isinstance(task_data, list) or len(task_data) < 2:
                continue

            task_id = task_data[0]
            task_info = task_data[1] if len(task_data) > 1 else None

            # Skip timestamp arrays (task_id should be a UUID string, not an int)
            if not isinstance(task_id, str):
                continue

            if not task_info or not isinstance(task_info, list):
                continue

            # Parse task info structure:
            # Note: status is at task_info[4], NOT task_data[2] (which is a timestamp)
            query_info = task_info[1] if len(task_info) > 1 else None
            research_mode = task_info[2] if len(task_info) > 2 else None
            sources_and_summary = task_info[3] if len(task_info) > 3 else []
            status_code = task_info[4] if len(task_info) > 4 else None

            query_text = query_info[0] if query_info and len(query_info) > 0 else ""
            source_type = query_info[1] if query_info and len(query_info) > 1 else 1

            sources_data = []
            summary = ""
            report = ""

            # Handle different structures for fast vs deep research
            if isinstance(sources_and_summary, list) and len(sources_and_summary) >= 1:
                # sources_and_summary[0] is always the sources list
                sources_data = sources_and_summary[0] if isinstance(sources_and_summary[0], list) else []
                # For fast research, summary may be at [1]
                if len(sources_and_summary) >= 2 and isinstance(sources_and_summary[1], str):
                    summary = sources_and_summary[1]

            # Parse sources - structure differs between fast and deep research
            # Fast research: [url, title, desc, type, ...]
            # Deep research: [None, title, None, type, None, None, [report], ...]
            sources = []
            if isinstance(sources_data, list) and len(sources_data) > 0:
                for idx, src in enumerate(sources_data):
                    if not isinstance(src, list) or len(src) < 2:
                        continue

                    # Check if this is deep research format (src[0] is None, src[1] is title)
                    if src[0] is None and len(src) > 1 and isinstance(src[1], str):
                        # Deep research format
                        title = src[1] if isinstance(src[1], str) else ""
                        result_type = src[3] if len(src) > 3 and isinstance(src[3], int) else 5
                        # Report is at src[6][0] for deep research
                        if len(src) > 6 and isinstance(src[6], list) and len(src[6]) > 0:
                            report = src[6][0] if isinstance(src[6][0], str) else ""

                        sources.append({
                            "index": idx,
                            "url": "",  # Deep research doesn't have URLs in source list
                            "title": title,
                            "description": "",
                            "result_type": result_type,
                            "result_type_name": constants.RESULT_TYPES.get_name(result_type),
                        })
                    elif isinstance(src[0], str) or len(src) >= 3:
                        # Fast research format: [url, title, desc, type, ...]
                        url = src[0] if isinstance(src[0], str) else ""
                        title = src[1] if len(src) > 1 and isinstance(src[1], str) else ""
                        desc = src[2] if len(src) > 2 and isinstance(src[2], str) else ""
                        result_type = src[3] if len(src) > 3 and isinstance(src[3], int) else 1

                        sources.append({
                            "index": idx,
                            "url": url,
                            "title": title,
                            "description": desc,
                            "result_type": result_type,
                            "result_type_name": constants.RESULT_TYPES.get_name(result_type),
                        })

            # Determine status (1 = in_progress, 2 = completed, 6 = imported/completed)
            # Fix: Status 6 means "Imported" which is also a completed state
            status = "completed" if status_code in (2, 6) else "in_progress"

            research_tasks.append({
                "task_id": task_id,
                "status": status,
                "query": query_text,
                "source_type": "web" if source_type == 1 else "drive",
                "mode": "deep" if research_mode == 5 else "fast",
                "sources": sources,
                "source_count": len(sources),
                "summary": summary,
                "report": report,  # Deep research report (markdown)
            })

        if not research_tasks:
            return {"status": "no_research", "message": "No active research found"}

        # If target_task_id provided, find the specific task
        if target_task_id:
            for task in research_tasks:
                if task["task_id"] == target_task_id:
                    return task
            # If specified task not found, return None or error
            # For now, return None to indicate not found/not ready?
            # Or maybe we shouldn't filter strict if it's not found?
            # Let's return None implies "waiting" or "not found yet"
            return None

        # Return the most recent (first) task if no task_id specified

        return research_tasks[0]


    def import_research_sources(
        self,
        notebook_id: str,
        task_id: str,
        sources: list[dict],
    ) -> list[dict]:
        """Import research sources into the notebook.
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

            # Skip deep_report sources (type 5) - these are research reports, not importable sources
            # Also skip sources with empty URLs
            if result_type == 5 or not url:
                continue

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

        # Note: source_array is already [source1, source2, ...], don't double-wrap
        params = [None, [1], task_id, notebook_id, source_array]
        body = self._build_request_body(self.RPC_IMPORT_RESEARCH, params)
        url = self._build_url(self.RPC_IMPORT_RESEARCH, f"/notebook/{notebook_id}")

        # Import can take a long time when fetching multiple web sources
        # Use 120s timeout instead of the default 30s
        response = client.post(url, content=body, timeout=120.0)
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
    """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Build source IDs in the simpler format: [[id1], [id2], ...]
        sources_simple = [[sid] for sid in source_ids]

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
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "audio",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "format": constants.AUDIO_FORMATS.get_name(format_code),
                "length": constants.AUDIO_LENGTHS.get_name(length_code),
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
    """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Build source IDs in the simpler format: [[id1], [id2], ...]
        sources_simple = [[sid] for sid in source_ids]

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
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "video",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "format": constants.VIDEO_FORMATS.get_name(format_code),
                "visual_style": constants.VIDEO_STYLES.get_name(visual_style_code),
                "language": language,
            }

        return None

    def poll_studio_status(self, notebook_id: str) -> list[dict]:
        """Poll for studio content (audio/video overviews) status.
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
                    self.STUDIO_TYPE_FLASHCARDS: "flashcards",  # Also includes Quiz (type 4)
                    self.STUDIO_TYPE_INFOGRAPHIC: "infographic",
                    self.STUDIO_TYPE_SLIDE_DECK: "slide_deck",
                    self.STUDIO_TYPE_DATA_TABLE: "data_table",
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

    def delete_studio_artifact(self, artifact_id: str, notebook_id: str | None = None) -> bool:
        """Delete a studio artifact (Audio, Video, or Mind Map).

        WARNING: This action is IRREVERSIBLE. The artifact will be permanently deleted.

        Args:
            artifact_id: The artifact UUID to delete
            notebook_id: Optional notebook ID. Required for deleting Mind Maps.

        Returns:
            True on success, False on failure
        """
        # 1. Try standard deletion (Audio, Video, etc.)
        try:
            params = [[2], artifact_id]
            result = self._call_rpc(self.RPC_DELETE_STUDIO, params)
            if result is not None:
                return True
        except Exception:
            # Continue to fallback if standard delete fails
            pass

        # 2. Fallback: Try Mind Map deletion if we have a notebook ID
        # Mind maps require a different RPC (AH0mwd) and payload structure
        if notebook_id:
            return self.delete_mind_map(notebook_id, artifact_id)

        return False

    def delete_mind_map(self, notebook_id: str, mind_map_id: str) -> bool:
        """Delete a Mind Map artifact using the observed two-step RPC sequence.

        Args:
            notebook_id: The notebook UUID.
            mind_map_id: The Mind Map artifact UUID.

        Returns:
            True on success
        """
        # 1. We need the artifact-specific timestamp from LIST_MIND_MAPS
        params = [notebook_id]
        list_result = self._call_rpc(
            self.RPC_LIST_MIND_MAPS, params, f"/notebook/{notebook_id}"
        )

        timestamp = None
        if list_result and isinstance(list_result, list) and len(list_result) > 0:
            mm_list = list_result[0] if isinstance(list_result[0], list) else []
            for mm_entry in mm_list:
                if isinstance(mm_entry, list) and mm_entry[0] == mind_map_id:
                    # Based on debug output: item[1][2][2] contains [seconds, micros]
                    try:
                        timestamp = mm_entry[1][2][2]
                    except (IndexError, TypeError):
                        pass
                    break

        # 2. Step 1: UUID-based deletion (AH0mwd)
        params_v2 = [notebook_id, None, [mind_map_id], [2]]
        self._call_rpc(self.RPC_DELETE_MIND_MAP, params_v2, f"/notebook/{notebook_id}")

        # 3. Step 2: Timestamp-based sync/deletion (cFji9)
        # This is required to fully remove it from the list and avoid "ghosts"
        if timestamp:
            params_v1 = [notebook_id, None, timestamp, [2]]
            self._call_rpc(self.RPC_LIST_MIND_MAPS, params_v1, f"/notebook/{notebook_id}")

        return True

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
    """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Options at position 14: [[focus_prompt, language, null, orientation, detail_level]]
        # Captured RPC structure was [[null, "en", null, 1, 2]]
        infographic_options = [[focus_prompt or None, language, None, orientation_code, detail_level_code]]

        content = [
            None, None,
            self.STUDIO_TYPE_INFOGRAPHIC,
            sources_nested,
            None, None, None, None, None, None, None, None, None, None,  # 10 nulls (positions 4-13)
            infographic_options  # position 14
        ]

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
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "infographic",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "orientation": constants.INFOGRAPHIC_ORIENTATIONS.get_name(orientation_code),
                "detail_level": constants.INFOGRAPHIC_DETAILS.get_name(detail_level_code),
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
    """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Options at position 16: [[focus_prompt, language, format, length]]
        slide_deck_options = [[focus_prompt or None, language, format_code, length_code]]

        content = [
            None, None,
            self.STUDIO_TYPE_SLIDE_DECK,
            sources_nested,
            None, None, None, None, None, None, None, None, None, None, None, None,  # 12 nulls (positions 4-15)
            slide_deck_options  # position 16
        ]

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
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "slide_deck",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "format": constants.SLIDE_DECK_FORMATS.get_name(format_code),
                "length": constants.SLIDE_DECK_LENGTHS.get_name(length_code),
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

        content = [
            None, None,
            self.STUDIO_TYPE_REPORT,
            sources_nested,
            None, None, None,
            report_options
        ]

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
        difficulty_code: int = 2,  # FLASHCARD_DIFFICULTY_MEDIUM
    ) -> dict | None:
        """Create Flashcards from notebook sources.
    """
        client = self._get_client()

        # Build source IDs in the nested format: [[[id1]], [[id2]], ...]
        sources_nested = [[[sid]] for sid in source_ids]

        # Card count code (default = 2)
        count_code = constants.FLASHCARD_COUNT_DEFAULT

        # Options at position 9: [null, [1, null*5, [difficulty, card_count]]]
        flashcard_options = [
            None,
            [
                1,  # Unknown (possibly default count base)
                None, None, None, None, None,
                [difficulty_code, count_code]
            ]
        ]

        content = [
            None, None,
            self.STUDIO_TYPE_FLASHCARDS,
            sources_nested,
            None, None, None, None, None,  # 5 nulls (positions 4-8)
            flashcard_options  # position 9
        ]

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
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "flashcards",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "difficulty": constants.FLASHCARD_DIFFICULTIES.get_name(difficulty_code),
            }

        return None

    def create_quiz(
        self,
        notebook_id: str,
        source_ids: list[str],
        question_count: int = 2,
        difficulty: int = 2,
    ) -> dict | None:
        """Create Quiz from notebook sources.

        Args:
            notebook_id: Notebook UUID
            source_ids: List of source UUIDs
            question_count: Number of questions (default: 2)
            difficulty: Difficulty level (default: 2)
        """
        client = self._get_client()
        sources_nested = [[[sid]] for sid in source_ids]

        # Quiz options at position 9: [null, [2, null*6, [question_count, difficulty]]]
        quiz_options = [
            None,
            [
                2,  # Format/variant code
                None, None, None, None, None, None,
                [question_count, difficulty]
            ]
        ]

        content = [
            None, None,
            self.STUDIO_TYPE_FLASHCARDS,  # Type 4 (shared with flashcards)
            sources_nested,
            None, None, None, None, None,
            quiz_options  # position 9
        ]

        params = [[2], notebook_id, content]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "quiz",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "question_count": question_count,
                "difficulty": constants.FLASHCARD_DIFFICULTIES.get_name(difficulty),
            }

        return None

    def create_data_table(
        self,
        notebook_id: str,
        source_ids: list[str],
        description: str,
        language: str = "en",
    ) -> dict | None:
        """Create Data Table from notebook sources.

        Args:
            notebook_id: Notebook UUID
            source_ids: List of source UUIDs
            description: Description of the data table to create
            language: Language code (default: "en")
        """
        client = self._get_client()
        sources_nested = [[[sid]] for sid in source_ids]

        # Data Table options at position 18: [null, [description, language]]
        datatable_options = [None, [description, language]]

        content = [
            None, None,
            self.STUDIO_TYPE_DATA_TABLE,  # Type 9
            sources_nested,
            None, None, None, None, None, None, None, None, None, None, None, None, None, None,  # 14 nulls (positions 4-17)
            datatable_options  # position 18
        ]

        params = [[2], notebook_id, content]

        body = self._build_request_body(self.RPC_CREATE_STUDIO, params)
        url = self._build_url(self.RPC_CREATE_STUDIO, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_CREATE_STUDIO)

        if result and isinstance(result, list) and len(result) > 0:
            artifact_data = result[0]
            artifact_id = artifact_data[0] if isinstance(artifact_data, list) and len(artifact_data) > 0 else None
            status_code = artifact_data[4] if isinstance(artifact_data, list) and len(artifact_data) > 4 else None

            return {
                "artifact_id": artifact_id,
                "notebook_id": notebook_id,
                "type": "data_table",
                "status": "in_progress" if status_code == 1 else "completed" if status_code == 3 else "unknown",
                "description": description,
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

        metadata = [2, None, None, 5, sources_simple]

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
    """
        client = self._get_client()

        params = [notebook_id]

        body = self._build_request_body(self.RPC_LIST_MIND_MAPS, params)
        url = self._build_url(self.RPC_LIST_MIND_MAPS, f"/notebook/{notebook_id}")

        response = client.post(url, content=body)
        response.raise_for_status()

        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_LIST_MIND_MAPS)

        mind_maps = []
        if result and isinstance(result, list) and len(result) > 0:
            mind_map_list = result[0] if isinstance(result[0], list) else []

            for mind_map_data in mind_map_list:
                # Skip invalid or tombstone entries (deleted entries have details=None)
                # Tombstone format: [uuid, null, 2]
                if not isinstance(mind_map_data, list) or len(mind_map_data) < 2:
                    continue
                
                details = mind_map_data[1]
                if details is None:
                    # This is a tombstone/deleted entry, skip it
                    continue

                mind_map_id = mind_map_data[0]

                if isinstance(details, list) and len(details) >= 5:
                    # Details: [id, json, metadata, null, title]
                    mind_map_json = details[1] if len(details) > 1 else None
                    title = details[4] if len(details) > 4 else "Mind Map"
                    metadata = details[2] if len(details) > 2 else []

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

    print("NotebookLM MCP API POC")
    print("=" * 50)
    print()
    print("To use this POC, you need to:")
    print("1. Go to notebooklm.google.com in Chrome")
    print("2. Open DevTools > Network tab")
    print("3. Find a request to notebooklm.google.com")
    print("4. Copy the entire Cookie header value")
    print()
    print("Then run:")
    print("  python notebooklm_mcp.py 'YOUR_COOKIE_HEADER'")
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

        client = NotebookLMClient(cookies, csrf_token=csrf_token, session_id=session_id)

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
