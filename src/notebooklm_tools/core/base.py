#!/usr/bin/env python3
"""Base client infrastructure for NotebookLM API.

This module contains the BaseClient class which provides all HTTP/RPC
infrastructure for interacting with the NotebookLM internal API. Domain-specific
operations (notebooks, sources, studio, etc.) are provided by mixin classes.

Internal API. See CLAUDE.md for full documentation.
"""

import json
import logging
import os
import re
import urllib.parse
from typing import Any

import httpx

from . import constants
from .data_types import ConversationTurn
from .errors import ClientAuthenticationError as AuthenticationError
from .utils import (
    RPC_NAMES,
    _format_debug_json,
    _decode_request_body,
    _parse_url_params,
)

# Configure logger (API internals only logged at DEBUG level, usually disabled)
logger = logging.getLogger("notebooklm_mcp.api")
logger.setLevel(logging.WARNING)  # Suppress internal API logs by default

# Timeout configuration (seconds)
DEFAULT_TIMEOUT = 30.0  # Default for most operations
SOURCE_ADD_TIMEOUT = 120.0  # Extended timeout for all source operations


class BaseClient:
    """Base client providing HTTP/RPC infrastructure for NotebookLM API.
    
    This class handles:
    - Authentication (cookies, CSRF tokens, session management)
    - HTTP client lifecycle
    - RPC request/response protocol (batchexecute)
    - Automatic authentication recovery
    
    Domain-specific operations are provided by mixin classes that inherit
    from this base class.
    """

    BASE_URL = "https://notebooklm.google.com"
    BATCHEXECUTE_URL = f"{BASE_URL}/_/LabsTailwindUi/data/batchexecute"

    # =========================================================================
    # Known RPC IDs
    # =========================================================================
    
    # Notebook operations
    RPC_LIST_NOTEBOOKS = "wXbhsf"
    RPC_GET_NOTEBOOK = "rLM1Ne"
    RPC_CREATE_NOTEBOOK = "CCqFvf"
    RPC_RENAME_NOTEBOOK = "s0tc2d"
    RPC_DELETE_NOTEBOOK = "WWINqb"
    
    # Source operations
    RPC_ADD_SOURCE = "izAoDd"  # Used for URL, text, and Drive sources
    RPC_GET_SOURCE = "hizoJc"  # Get source details
    RPC_CHECK_FRESHNESS = "yR9Yof"  # Check if Drive source is stale
    RPC_SYNC_DRIVE = "FLmJqe"  # Sync Drive source with latest content
    RPC_DELETE_SOURCE = "tGMBJ"  # Delete a source from notebook
    
    # Misc
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

    # Studio content RPCs
    RPC_CREATE_STUDIO = "R7cb6c"   # Create Audio or Video Overview
    RPC_POLL_STUDIO = "gArtLc"     # Poll for studio content status
    RPC_DELETE_STUDIO = "V5N4be"   # Delete Audio or Video Overview
    RPC_GET_INTERACTIVE_HTML = "v9rmvd"  # Fetch quiz/flashcard HTML content

    # Mind map RPCs
    RPC_GENERATE_MIND_MAP = "yyryJe"  # Generate mind map JSON from sources
    RPC_SAVE_MIND_MAP = "CYK0Xb"      # Save generated mind map to notebook
    RPC_LIST_MIND_MAPS = "cFji9"       # List existing mind maps
    RPC_DELETE_MIND_MAP = "AH0mwd"     # Delete a mind map

    # Sharing RPCs
    RPC_SHARE_NOTEBOOK = "QDyure"    # Set sharing settings (visibility, collaborators)
    RPC_GET_SHARE_STATUS = "JFMDGd"  # Get current share status

    # =========================================================================
    # API Constants (re-exported from constants module)
    # =========================================================================
    
    # Ownership
    OWNERSHIP_MINE = constants.OWNERSHIP_MINE
    OWNERSHIP_SHARED = constants.OWNERSHIP_SHARED

    # Research
    RESEARCH_SOURCE_WEB = constants.RESEARCH_SOURCE_WEB
    RESEARCH_SOURCE_DRIVE = constants.RESEARCH_SOURCE_DRIVE
    RESEARCH_MODE_FAST = constants.RESEARCH_MODE_FAST
    RESEARCH_MODE_DEEP = constants.RESEARCH_MODE_DEEP
    RESULT_TYPE_WEB = constants.RESULT_TYPE_WEB
    RESULT_TYPE_GOOGLE_DOC = constants.RESULT_TYPE_GOOGLE_DOC
    RESULT_TYPE_GOOGLE_SLIDES = constants.RESULT_TYPE_GOOGLE_SLIDES
    RESULT_TYPE_DEEP_REPORT = constants.RESULT_TYPE_DEEP_REPORT
    RESULT_TYPE_GOOGLE_SHEETS = constants.RESULT_TYPE_GOOGLE_SHEETS

    # Studio content types
    STUDIO_TYPE_AUDIO = constants.STUDIO_TYPE_AUDIO
    STUDIO_TYPE_VIDEO = constants.STUDIO_TYPE_VIDEO
    STUDIO_TYPE_REPORT = constants.STUDIO_TYPE_REPORT
    STUDIO_TYPE_FLASHCARDS = constants.STUDIO_TYPE_FLASHCARDS
    STUDIO_TYPE_INFOGRAPHIC = constants.STUDIO_TYPE_INFOGRAPHIC
    STUDIO_TYPE_SLIDE_DECK = constants.STUDIO_TYPE_SLIDE_DECK
    STUDIO_TYPE_DATA_TABLE = constants.STUDIO_TYPE_DATA_TABLE

    # Audio formats and lengths
    AUDIO_FORMAT_DEEP_DIVE = constants.AUDIO_FORMAT_DEEP_DIVE
    AUDIO_FORMAT_BRIEF = constants.AUDIO_FORMAT_BRIEF
    AUDIO_FORMAT_CRITIQUE = constants.AUDIO_FORMAT_CRITIQUE
    AUDIO_FORMAT_DEBATE = constants.AUDIO_FORMAT_DEBATE
    AUDIO_LENGTH_SHORT = constants.AUDIO_LENGTH_SHORT
    AUDIO_LENGTH_DEFAULT = constants.AUDIO_LENGTH_DEFAULT
    AUDIO_LENGTH_LONG = constants.AUDIO_LENGTH_LONG

    # Video formats and styles
    VIDEO_FORMAT_EXPLAINER = constants.VIDEO_FORMAT_EXPLAINER
    VIDEO_FORMAT_BRIEF = constants.VIDEO_FORMAT_BRIEF
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

    # Report formats
    REPORT_FORMAT_BRIEFING_DOC = constants.REPORT_FORMAT_BRIEFING_DOC
    REPORT_FORMAT_STUDY_GUIDE = constants.REPORT_FORMAT_STUDY_GUIDE
    REPORT_FORMAT_BLOG_POST = constants.REPORT_FORMAT_BLOG_POST
    REPORT_FORMAT_CUSTOM = constants.REPORT_FORMAT_CUSTOM

    # Flashcard settings
    FLASHCARD_DIFFICULTY_EASY = constants.FLASHCARD_DIFFICULTY_EASY
    FLASHCARD_DIFFICULTY_MEDIUM = constants.FLASHCARD_DIFFICULTY_MEDIUM
    FLASHCARD_DIFFICULTY_HARD = constants.FLASHCARD_DIFFICULTY_HARD
    FLASHCARD_COUNT_DEFAULT = constants.FLASHCARD_COUNT_DEFAULT

    # Infographic settings
    INFOGRAPHIC_ORIENTATION_LANDSCAPE = constants.INFOGRAPHIC_ORIENTATION_LANDSCAPE
    INFOGRAPHIC_ORIENTATION_PORTRAIT = constants.INFOGRAPHIC_ORIENTATION_PORTRAIT
    INFOGRAPHIC_ORIENTATION_SQUARE = constants.INFOGRAPHIC_ORIENTATION_SQUARE
    INFOGRAPHIC_DETAIL_CONCISE = constants.INFOGRAPHIC_DETAIL_CONCISE
    INFOGRAPHIC_DETAIL_STANDARD = constants.INFOGRAPHIC_DETAIL_STANDARD
    INFOGRAPHIC_DETAIL_DETAILED = constants.INFOGRAPHIC_DETAIL_DETAILED

    # Slide deck settings
    SLIDE_DECK_FORMAT_DETAILED = constants.SLIDE_DECK_FORMAT_DETAILED
    SLIDE_DECK_FORMAT_PRESENTER = constants.SLIDE_DECK_FORMAT_PRESENTER
    SLIDE_DECK_LENGTH_SHORT = constants.SLIDE_DECK_LENGTH_SHORT
    SLIDE_DECK_LENGTH_DEFAULT = constants.SLIDE_DECK_LENGTH_DEFAULT

    # Chat configuration
    CHAT_GOAL_DEFAULT = constants.CHAT_GOAL_DEFAULT
    CHAT_GOAL_CUSTOM = constants.CHAT_GOAL_CUSTOM
    CHAT_GOAL_LEARNING_GUIDE = constants.CHAT_GOAL_LEARNING_GUIDE
    CHAT_RESPONSE_DEFAULT = constants.CHAT_RESPONSE_DEFAULT
    CHAT_RESPONSE_LONGER = constants.CHAT_RESPONSE_LONGER
    CHAT_RESPONSE_SHORTER = constants.CHAT_RESPONSE_SHORTER

    # Source types
    SOURCE_TYPE_GOOGLE_DOCS = constants.SOURCE_TYPE_GOOGLE_DOCS
    SOURCE_TYPE_GOOGLE_OTHER = constants.SOURCE_TYPE_GOOGLE_OTHER
    SOURCE_TYPE_PASTED_TEXT = constants.SOURCE_TYPE_PASTED_TEXT

    # Sharing
    SHARE_ROLE_OWNER = constants.SHARE_ROLE_OWNER
    SHARE_ROLE_EDITOR = constants.SHARE_ROLE_EDITOR
    SHARE_ROLE_VIEWER = constants.SHARE_ROLE_VIEWER
    SHARE_ACCESS_RESTRICTED = constants.SHARE_ACCESS_RESTRICTED
    SHARE_ACCESS_PUBLIC = constants.SHARE_ACCESS_PUBLIC

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

    # =========================================================================
    # Lifecycle Methods
    # =========================================================================

    def __init__(self, cookies: dict[str, str] | list[dict], csrf_token: str = "", session_id: str = ""):
        """
        Initialize the base client.

        Args:
            cookies: Dict of Google auth cookies or List of cookie dicts (from CDP)
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

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        """Close the underlying HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    # =========================================================================
    # Cookie Handling
    # =========================================================================

    def _get_httpx_cookies(self) -> httpx.Cookies:
        """Convert cookies to httpx.Cookies object (preserving domains).

        Duplicates cookies for both .google.com and .googleusercontent.com
        to ensure authentication works across redirect domains.
        """
        cookies = httpx.Cookies()

        # Determine if we have raw list[dict] or simple dict[str, str]
        if isinstance(self.cookies, list):
            for cookie in self.cookies:
                name = cookie.get("name")
                value = cookie.get("value")
                domain = cookie.get("domain")
                path = cookie.get("path", "/")

                if name and value:
                    # Set cookie for original domain
                    cookies.set(name, value, domain=domain, path=path)

                    # Also duplicate for .googleusercontent.com if original is .google.com
                    # This is required for artifact downloads that redirect to googleusercontent.com
                    if domain == ".google.com":
                        cookies.set(name, value, domain=".googleusercontent.com", path=path)
        else:
            # Fallback for simple dict - set for both domains
            for name, value in self.cookies.items():
                cookies.set(name, value, domain=".google.com")
                cookies.set(name, value, domain=".googleusercontent.com")

        return cookies

    def _get_cookie_header(self) -> str:
        """Get Cookie header string (backward compatibility)."""
        if isinstance(self.cookies, list):
            # Flatten to simple dict for header
            simple_cookies = {c["name"]: c["value"] for c in self.cookies if "name" in c and "value" in c}
            return "; ".join(f"{k}={v}" for k, v in simple_cookies.items())
        else:
            return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    # =========================================================================
    # HTTP Client Management
    # =========================================================================

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            # Use cookies object directly
            cookies = self._get_httpx_cookies()

            self._client = httpx.Client(
                cookies=cookies,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    "Origin": self.BASE_URL,
                    "Referer": f"{self.BASE_URL}/",
                    "X-Same-Domain": "1",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                },
                timeout=30.0,
            )
            
            # Explicitly set headers if needed, though constructor handles most
            if self.csrf_token:
                self._client.headers["X-Goog-Csrf-Token"] = self.csrf_token
                
        return self._client
    
    def _get_async_client(self) -> httpx.AsyncClient:
        """Get an async client for streaming operations."""
        cookies = self._get_httpx_cookies()

        client = httpx.AsyncClient(
            cookies=cookies,
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Origin": self.BASE_URL,
                "Referer": f"{self.BASE_URL}/",
                "X-Same-Domain": "1",
            },
            timeout=30.0,
        )
        if self.csrf_token:
            client.headers["X-Goog-Csrf-Token"] = self.csrf_token
        return client

    # =========================================================================
    # RPC Request/Response Protocol
    # =========================================================================

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

    # =========================================================================
    # Authentication Management
    # =========================================================================

    def _refresh_auth_tokens(self) -> None:
        """
        Refresh CSRF token and session ID by fetching the NotebookLM homepage.

        This method fetches the NotebookLM page using the stored cookies and
        extracts the CSRF token (SNlM0e) and session ID (FdrFJe) from the HTML.

        Raises:
            ValueError: If cookies are expired (redirected to login) or tokens not found
        """
        # Use httpx.Cookies for proper domain filtering
        cookies = self._get_httpx_cookies()

        # Must use browser-like headers for page fetch
        headers = self._PAGE_FETCH_HEADERS.copy()

        # Use a temporary client for the page fetch
        with httpx.Client(cookies=cookies, headers=headers, follow_redirects=True, timeout=15.0) as client:
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
