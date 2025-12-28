#!/usr/bin/env python3
"""CLI tool to authenticate with NotebookLM Consumer.

This tool connects to Chrome via DevTools Protocol, navigates to NotebookLM,
and extracts authentication tokens. If the user is not logged in, it waits
for them to log in via the Chrome window.

Usage:
    1. Start Chrome with remote debugging:
       /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222

    2. Or, if Chrome is already running, it may already have debugging enabled.

    3. Run this tool:
       notebooklm-consumer-auth

    4. If not logged in, log in via the Chrome window

    5. Tokens are cached to ~/.notebooklm-consumer/auth.json
"""

import json
import re
import sys
import time
from pathlib import Path

import httpx

from .auth import (
    AuthTokens,
    REQUIRED_COOKIES,
    extract_csrf_from_page_source,
    get_cache_path,
    save_tokens_to_cache,
    validate_cookies,
)


CDP_DEFAULT_PORT = 9222
NOTEBOOKLM_URL = "https://notebooklm.google.com/"


def get_chrome_user_data_dir() -> str | None:
    """Get the default Chrome user data directory."""
    import platform
    from pathlib import Path

    system = platform.system()
    home = Path.home()

    if system == "Darwin":
        return str(home / "Library/Application Support/Google/Chrome")
    elif system == "Linux":
        return str(home / ".config/google-chrome")
    elif system == "Windows":
        return str(home / "AppData/Local/Google/Chrome/User Data")
    return None


def launch_chrome(port: int, headless: bool = False) -> bool:
    """Launch Chrome with remote debugging enabled.

    Args:
        port: The debugging port to use
        headless: If True, launch in headless mode (no visible window)

    Returns:
        True if Chrome was launched, False if failed
    """
    import platform
    import subprocess

    system = platform.system()

    if system == "Darwin":
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    elif system == "Linux":
        chrome_path = "google-chrome"
    elif system == "Windows":
        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    else:
        print(f"Unsupported platform: {system}")
        return False

    args = [
        chrome_path,
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    # Use the user's default Chrome profile to access existing cookies
    user_data_dir = get_chrome_user_data_dir()
    if user_data_dir:
        args.append(f"--user-data-dir={user_data_dir}")

    if headless:
        args.append("--headless=new")

    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)  # Wait for Chrome to start
        return True
    except Exception as e:
        print(f"Failed to launch Chrome: {e}")
        return False


def get_chrome_debugger_url(port: int = CDP_DEFAULT_PORT) -> str | None:
    """Get the WebSocket debugger URL for Chrome."""
    try:
        response = httpx.get(f"http://localhost:{port}/json/version", timeout=5)
        data = response.json()
        return data.get("webSocketDebuggerUrl")
    except Exception:
        return None


def get_chrome_pages(port: int = CDP_DEFAULT_PORT) -> list[dict]:
    """Get list of open pages in Chrome."""
    try:
        response = httpx.get(f"http://localhost:{port}/json", timeout=5)
        return response.json()
    except Exception:
        return []


def find_or_create_notebooklm_page(port: int = CDP_DEFAULT_PORT) -> dict | None:
    """Find an existing NotebookLM page or create a new one."""
    from urllib.parse import quote

    pages = get_chrome_pages(port)

    # Look for existing NotebookLM page
    for page in pages:
        url = page.get("url", "")
        if "notebooklm.google.com" in url:
            return page

    # Create a new page - URL must be properly encoded
    try:
        encoded_url = quote(NOTEBOOKLM_URL, safe="")
        response = httpx.put(
            f"http://localhost:{port}/json/new?{encoded_url}",
            timeout=15
        )
        if response.status_code == 200 and response.text.strip():
            return response.json()

        # Fallback: create blank page then navigate
        response = httpx.put(f"http://localhost:{port}/json/new", timeout=10)
        if response.status_code == 200 and response.text.strip():
            page = response.json()
            # Navigate to NotebookLM using the page's websocket
            ws_url = page.get("webSocketDebuggerUrl")
            if ws_url:
                navigate_to_url(ws_url, NOTEBOOKLM_URL)
            return page

        print(f"Failed to create page: status={response.status_code}")
        return None
    except Exception as e:
        print(f"Failed to create new page: {e}")
        return None


def execute_cdp_command(ws_url: str, method: str, params: dict = None) -> dict:
    """Execute a CDP command via WebSocket."""
    import websocket

    ws = websocket.create_connection(ws_url, timeout=30)
    try:
        command = {
            "id": 1,
            "method": method,
            "params": params or {}
        }
        ws.send(json.dumps(command))

        # Wait for response
        while True:
            response = json.loads(ws.recv())
            if response.get("id") == 1:
                return response.get("result", {})
    finally:
        ws.close()


def get_page_cookies(ws_url: str) -> list[dict]:
    """Get all cookies for the page."""
    result = execute_cdp_command(ws_url, "Network.getCookies")
    return result.get("cookies", [])


def get_page_html(ws_url: str) -> str:
    """Get the page HTML to extract CSRF token."""
    # Enable Runtime domain
    execute_cdp_command(ws_url, "Runtime.enable")

    # Execute JavaScript to get page HTML
    result = execute_cdp_command(
        ws_url,
        "Runtime.evaluate",
        {"expression": "document.documentElement.outerHTML"}
    )

    return result.get("result", {}).get("value", "")


def navigate_to_url(ws_url: str, url: str) -> None:
    """Navigate the page to a URL."""
    execute_cdp_command(ws_url, "Page.enable")
    execute_cdp_command(ws_url, "Page.navigate", {"url": url})
    # Wait for page to load
    time.sleep(3)


def get_current_url(ws_url: str) -> str:
    """Get the current page URL via CDP (cheap operation, no JS evaluation)."""
    execute_cdp_command(ws_url, "Runtime.enable")
    result = execute_cdp_command(
        ws_url,
        "Runtime.evaluate",
        {"expression": "window.location.href"}
    )
    return result.get("result", {}).get("value", "")


def check_if_logged_in_by_url(url: str) -> bool:
    """Check login status by URL - much cheaper than parsing HTML.

    If NotebookLM redirects to accounts.google.com, user is not logged in.
    If URL stays on notebooklm.google.com, user is authenticated.
    """
    if "accounts.google.com" in url:
        return False
    if "notebooklm.google.com" in url:
        return True
    # Unknown URL - assume not logged in
    return False


def extract_session_id_from_html(html: str) -> str:
    """Extract session ID from page HTML."""
    patterns = [
        r'"FdrFJe":"(\d+)"',
        r'f\.sid["\s:=]+["\']?(\d+)',
        r'"cfb2h":"([^"]+)"',
    ]

    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)

    return ""


def is_chrome_running() -> bool:
    """Check if Chrome is already running (without debugging)."""
    import subprocess
    import platform

    system = platform.system()
    try:
        if system == "Darwin":
            result = subprocess.run(
                ["pgrep", "-f", "Google Chrome"],
                capture_output=True, text=True
            )
            return result.returncode == 0
        elif system == "Linux":
            result = subprocess.run(
                ["pgrep", "-f", "chrome"],
                capture_output=True, text=True
            )
            return result.returncode == 0
        elif system == "Windows":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                capture_output=True, text=True
            )
            return "chrome.exe" in result.stdout
    except Exception:
        pass
    return False


def run_auth_flow(port: int = CDP_DEFAULT_PORT, auto_launch: bool = True) -> AuthTokens | None:
    """Run the authentication flow.

    Args:
        port: Chrome DevTools port
        auto_launch: If True, automatically launch Chrome if not running
    """
    print("NotebookLM Consumer Authentication")
    print("=" * 40)
    print()

    # Check if Chrome is running with debugging
    debugger_url = get_chrome_debugger_url(port)

    if not debugger_url and auto_launch:
        # Check if Chrome is running without debugging
        if is_chrome_running():
            print("Chrome is running but without remote debugging enabled.")
            print()
            print("Please either:")
            print("1. Close Chrome completely, then run this command again, OR")
            print("2. Restart Chrome with debugging:")
            print(f'   /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port={port}')
            print()
            return None

        print("Launching Chrome to check login status...")
        # Launch with visible window so user can log in if needed
        launch_chrome(port, headless=False)
        time.sleep(3)
        debugger_url = get_chrome_debugger_url(port)

    if not debugger_url:
        print(f"ERROR: Cannot connect to Chrome on port {port}")
        print()
        print("Please start Chrome with remote debugging enabled:")
        print()
        print("  macOS:")
        print(f'    /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port={port}')
        print()
        print("  Linux:")
        print(f"    google-chrome --remote-debugging-port={port}")
        print()
        print("  Windows:")
        print(f'    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port={port}')
        print()
        return None

    print(f"Connected to Chrome debugger")

    # Find or create NotebookLM page
    page = find_or_create_notebooklm_page(port)
    if not page:
        print("ERROR: Failed to find or create NotebookLM page")
        return None

    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        print("ERROR: No WebSocket URL for page")
        return None

    print(f"Using page: {page.get('title', 'Unknown')}")

    # Navigate to NotebookLM if needed
    current_url = page.get("url", "")
    if "notebooklm.google.com" not in current_url:
        print("Navigating to NotebookLM...")
        navigate_to_url(ws_url, NOTEBOOKLM_URL)

    # Check login status by URL (cheap - no HTML parsing)
    print("Checking login status...")
    current_url = get_current_url(ws_url)

    if not check_if_logged_in_by_url(current_url):
        print()
        print("=" * 40)
        print("NOT LOGGED IN")
        print("=" * 40)
        print()
        print("Please log in to NotebookLM in the Chrome window.")
        print("This tool will wait for you to complete login...")
        print()
        print("(Press Ctrl+C to cancel)")
        print()

        # Wait for login - check URL every 5 seconds (cheap operation)
        max_wait = 300  # 5 minutes
        start_time = time.time()
        while time.time() - start_time < max_wait:
            time.sleep(5)
            try:
                current_url = get_current_url(ws_url)
                if check_if_logged_in_by_url(current_url):
                    print("Login detected!")
                    break
            except Exception as e:
                print(f"Waiting... ({e})")

        if not check_if_logged_in_by_url(current_url):
            print("ERROR: Login timeout. Please try again.")
            return None

    # Extract cookies
    print("Extracting cookies...")
    cookies_list = get_page_cookies(ws_url)
    cookies = {c["name"]: c["value"] for c in cookies_list}

    if not validate_cookies(cookies):
        print("ERROR: Missing required cookies. Please ensure you're fully logged in.")
        print(f"Required: {REQUIRED_COOKIES}")
        print(f"Found: {list(cookies.keys())}")
        return None

    # Extract CSRF token
    print("Extracting CSRF token...")
    csrf_token = extract_csrf_from_page_source(html)
    if not csrf_token:
        print("WARNING: Could not extract CSRF token from page.")
        print("You may need to extract it manually from Network tab.")
        csrf_token = ""

    # Extract session ID
    session_id = extract_session_id_from_html(html)

    # Create tokens object
    tokens = AuthTokens(
        cookies=cookies,
        csrf_token=csrf_token,
        session_id=session_id,
        extracted_at=time.time(),
    )

    # Save to cache
    save_tokens_to_cache(tokens)

    print()
    print("=" * 40)
    print("SUCCESS!")
    print("=" * 40)
    print()
    print(f"Cookies: {len(cookies)} extracted")
    print(f"CSRF Token: {'Yes' if csrf_token else 'No (manual extraction needed)'}")
    print(f"Session ID: {session_id or 'Not found'}")
    print()
    print(f"Tokens cached to: {get_cache_path()}")
    print()
    print("You can now start the MCP server:")
    print("  notebooklm-consumer-mcp")
    print()

    return tokens


def run_manual_cookie_entry() -> AuthTokens | None:
    """Prompt user to paste cookies manually and save them.

    This is a simpler alternative to the Chrome DevTools extraction
    that doesn't require Chrome remote debugging.
    """
    print("NotebookLM Consumer - Manual Cookie Entry")
    print("=" * 50)
    print()
    print("This tool will guide you through saving your NotebookLM cookies.")
    print()
    print("Step 1: Extract cookies from Chrome DevTools")
    print("  1. Go to https://notebooklm.google.com and log in")
    print("  2. Press F12 to open DevTools > Network tab")
    print("  3. Type 'batchexecute' in the filter box")
    print("  4. Click any notebook to trigger a request")
    print("  5. Click on a 'batchexecute' request")
    print("  6. In Headers tab, find 'cookie:' under Request Headers")
    print("  7. Right-click the cookie VALUE and select 'Copy value'")
    print()
    print("Step 2: Paste your cookies below")
    print("-" * 50)
    print()
    print("Paste your cookie string and press Enter:")
    print("(It should look like: SID=xxx; HSID=xxx; SSID=xxx; ...)")
    print()

    # Read cookie string - input() doesn't truncate, but be explicit
    try:
        cookie_string = input("> ").strip()
    except EOFError:
        print("\nNo input received.")
        return None

    if not cookie_string:
        print("\nERROR: No cookie string provided.")
        return None

    print()
    print("Validating cookies...")

    # Parse cookies from header format (key=value; key=value; ...)
    cookies = {}
    for cookie in cookie_string.split(";"):
        cookie = cookie.strip()
        if "=" in cookie:
            key, value = cookie.split("=", 1)
            cookies[key.strip()] = value.strip()

    if not cookies:
        print("\nERROR: Could not parse any cookies from input.")
        print("Make sure you copied the cookie VALUE, not the header name.")
        print()
        print("Expected format: SID=xxx; HSID=xxx; SSID=xxx; ...")
        return None

    # Validate required cookies
    if not validate_cookies(cookies):
        print("\nWARNING: Some required cookies are missing!")
        print(f"Required: {REQUIRED_COOKIES}")
        print(f"Found: {list(cookies.keys())}")
        print()

        response = input("Continue anyway? (y/N): ").strip().lower()
        if response != "y":
            print("Cancelled.")
            return None

    # Create tokens object (CSRF and session ID will be auto-extracted later)
    tokens = AuthTokens(
        cookies=cookies,
        csrf_token="",  # Will be auto-extracted
        session_id="",  # Will be auto-extracted
        extracted_at=time.time(),
    )

    # Save to cache
    print()
    print("Saving cookies...")
    save_tokens_to_cache(tokens)

    print()
    print("=" * 50)
    print("SUCCESS!")
    print("=" * 50)
    print()
    print(f"✓ Cookies saved: {len(cookies)} cookies")
    print(f"✓ Cache location: {get_cache_path()}")
    print()
    print("Note: CSRF token and session ID will be automatically")
    print("      extracted when you first use the MCP.")
    print()
    print("You can now use the MCP tools to test authentication:")
    print("  Ask your AI assistant to call: notebook_list()")
    print()

    return tokens


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Authenticate with NotebookLM Consumer",
        epilog="""
This tool extracts authentication tokens from Chrome for use with the NotebookLM Consumer MCP.

TWO MODES:

1. MANUAL MODE (--manual): Simple cookie paste (recommended)
   - Extract cookies from Chrome DevTools manually
   - Paste them when prompted
   - No Chrome remote debugging required

2. AUTO MODE (default): Automatic extraction via Chrome DevTools
   - Requires Chrome with remote debugging enabled
   - Automatically extracts cookies from browser
   - More complex but fully automated

After authentication, start the MCP server with: notebooklm-consumer-mcp
        """
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Manual mode: prompt for cookie paste (simple, recommended)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=CDP_DEFAULT_PORT,
        help=f"Chrome DevTools port (default: {CDP_DEFAULT_PORT})"
    )
    parser.add_argument(
        "--show-tokens",
        action="store_true",
        help="Show cached tokens (for debugging)"
    )
    parser.add_argument(
        "--no-auto-launch",
        action="store_true",
        help="Don't automatically launch Chrome (requires Chrome to be running with debugging)"
    )

    args = parser.parse_args()

    if args.show_tokens:
        cache_path = get_cache_path()
        if cache_path.exists():
            with open(cache_path) as f:
                data = json.load(f)
            print(json.dumps(data, indent=2))
        else:
            print("No cached tokens found.")
        return 0

    try:
        if args.manual:
            # Simple manual cookie entry
            tokens = run_manual_cookie_entry()
        else:
            # Automatic extraction via Chrome DevTools
            tokens = run_auth_flow(args.port, auto_launch=not args.no_auto_launch)

        return 0 if tokens else 1
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 1
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
