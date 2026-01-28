#!/usr/bin/env python3
"""CLI tool to authenticate with NotebookLM MCP via Chromium browsers."""
import json
import re
import sys
import time
import subprocess
import socket
from pathlib import Path
from urllib.parse import urlparse, quote

import httpx
import websocket

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

def find_browser_executable(browser_name: str = "chrome") -> str | None:
    """Find the executable path for a given browser."""
    import platform, os, shutil
    system = platform.system()
    if system == "Windows":
        pf = os.environ.get("ProgramFiles", "C:\\Program Files")
        pfx86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        paths = {
            "chrome": [
                rf"{pf}\Google\Chrome\Application\chrome.exe",
                rf"{pfx86}\Google\Chrome\Application\chrome.exe",
                rf"{local}\Google\Chrome\Application\chrome.exe",
                rf"{local}\Chromium\Application\chrome.exe",
            ],
            "vivaldi": [rf"{pf}\Vivaldi\Application\vivaldi.exe", rf"{local}\Vivaldi\Application\vivaldi.exe"],
            "edge": [rf"{pfx86}\Microsoft\Edge\Application\msedge.exe", rf"{pf}\Microsoft\Edge\Application\msedge.exe"],
            "brave": [rf"{pf}\BraveSoftware\Brave-Browser\Application\brave.exe", rf"{local}\BraveSoftware\Brave-Browser\Application\brave.exe"]
        }
        for path in paths.get(browser_name.lower(), []):
            if os.path.exists(path): return path
    elif system == "Darwin":
        paths = {"chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "vivaldi": "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi"}
        path = paths.get(browser_name.lower())
        if path and os.path.exists(path): return path
    else:
        names = {"chrome": ["google-chrome", "chromium"], "vivaldi": ["vivaldi"]}
        for name in names.get(browser_name.lower(), [browser_name]):
            path = shutil.which(name)
            if path: return path
    return None

def initialize_browser_profile(profile_dir: Path, browser_name: str) -> None:
    """Initialize a browser profile with settings to skip setup wizards."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    if browser_name.lower() == "vivaldi":
        prefs_dir = profile_dir / "Default"
        prefs_dir.mkdir(parents=True, exist_ok=True)
        prefs_file = prefs_dir / "Preferences"
        if not prefs_file.exists():
            prefs = {"vivaldi": {"setup_completed": True, "tabs": {"show_welcome": False}}}
            with open(prefs_file, "w") as f: json.dump(prefs, f)

def launch_browser(port: int, headless: bool = False, browser_name: str = "chrome", browser_path: str | None = None) -> subprocess.Popen | None:
    """Launch a browser with remote debugging enabled."""
    exe_path = browser_path or find_browser_executable(browser_name)
    if not exe_path:
        print(f"Browser '{browser_name}' not found. Use --browser-path.")
        return None
    profile_dir = (Path.home() / ".notebooklm-mcp" / f"{browser_name}-profile").resolve()
    initialize_browser_profile(profile_dir, browser_name)
    args = [str(exe_path), f"--remote-debugging-port={port}", "--no-first-run", "--no-default-browser-check", "--disable-extensions", f"--user-data-dir={str(profile_dir)}", "--remote-allow-origins=*"]
    if headless: args.append("--headless=new")
    try:
        out = subprocess.DEVNULL if headless else None
        p = subprocess.Popen(args, stdout=out, stderr=out)
        time.sleep(3)
        if p.poll() is not None: return None
        return p
    except Exception as e:
        print(f"Failed to launch {browser_name}: {e}")
        return None

def get_chrome_debugger_url(port: int = CDP_DEFAULT_PORT) -> str | None:
    try: return httpx.get(f"http://localhost:{port}/json/version", timeout=5).json().get("webSocketDebuggerUrl")
    except: return None

def get_chrome_pages(port: int = CDP_DEFAULT_PORT) -> list[dict]:
    try: return httpx.get(f"http://localhost:{port}/json", timeout=5).json()
    except: return []

def find_or_create_notebooklm_page(port: int = CDP_DEFAULT_PORT) -> dict | None:
    pages = get_chrome_pages(port)
    for p in pages:
        if "notebooklm.google.com" in p.get("url", ""): return p
    try:
        r = httpx.put(f"http://localhost:{port}/json/new?{quote(NOTEBOOKLM_URL, safe='')}", timeout=15)
        if r.status_code == 200 and r.text.strip(): return r.json()
        return None
    except: return None

def execute_cdp_command(ws_url: str, method: str, params: dict | None = None) -> dict:
    ws = None
    try:
        ws = websocket.create_connection(ws_url, timeout=30)
        ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        start = time.time()
        while time.time() - start < 10:
            try:
                ws.settimeout(2.0)
                msg = ws.recv()
                if not msg: break
                resp = json.loads(msg)
                if resp.get("id") == 1: return resp.get("result", {})
            except (websocket.WebSocketTimeoutException, socket.timeout): continue
            except: break
        return {}
    finally:
        if ws:
            try: ws.close()
            except: pass

def get_page_cookies(ws_url: str) -> list[dict]:
    return execute_cdp_command(ws_url, "Network.getCookies").get("cookies", [])

def get_page_html(ws_url: str) -> str:
    execute_cdp_command(ws_url, "Runtime.enable")
    r = execute_cdp_command(ws_url, "Runtime.evaluate", {"expression": "document.documentElement.outerHTML"})
    return r.get("result", {}).get("value", "")

def get_current_url(ws_url: str) -> str:
    execute_cdp_command(ws_url, "Runtime.enable")
    r = execute_cdp_command(ws_url, "Runtime.evaluate", {"expression": "window.location.href"})
    return r.get("result", {}).get("value", "")

def check_if_logged_in_by_url(url: str) -> bool:
    if not url: return False
    try:
        parsed = urlparse(url)
        if parsed.netloc == "notebooklm.google.com": return True
    except: pass
    return "notebooklm.google.com" in url and "accounts.google.com" not in url.split('?')[0]

def extract_session_id_from_html(html: str) -> str:
    for p in [r'"FdrFJe":"(\d+)"', r'f\.sid["\s:=]+["\']?(\d+)', r'"cfb2h":"([^"]+)"']:
        m = re.search(p, html)
        if m: return m.group(1)
    return ""

def run_headless_auth(port: int = 9223, timeout: int = 30, browser_name: str = "chrome", browser_path: str | None = None) -> AuthTokens | None:
    profile_dir = Path.home() / ".notebooklm-mcp" / f"{browser_name}-profile"
    if not (profile_dir / "Default" / "Cookies").exists(): return None
    p = launch_browser(port, headless=True, browser_name=browser_name, browser_path=browser_path)
    if not p: return None
    try:
        for _ in range(5):
            if get_chrome_debugger_url(port): break
            time.sleep(1)
        page = find_or_create_notebooklm_page(port)
        if not page: return None
        ws = page.get("webSocketDebuggerUrl")
        if not ws or not check_if_logged_in_by_url(get_current_url(ws)): return None
        cl = get_page_cookies(ws)
        ck = {c["name"]: c["value"] for c in cl}
        if not validate_cookies(ck): return None
        html = get_page_html(ws)
        tokens = AuthTokens(cookies=ck, csrf_token=extract_csrf_from_page_source(html) or "", session_id=extract_session_id_from_html(html), extracted_at=time.time())
        save_tokens_to_cache(tokens)
        return tokens
    except: return None
    finally:
        if p: p.terminate(); p.wait(timeout=5)

def run_auth_flow(port: int = CDP_DEFAULT_PORT, auto_launch: bool = True, browser_name: str = "chrome", browser_path: str | None = None) -> AuthTokens | None:
    print(f"NotebookLM MCP Authentication ({browser_name})")
    p = None
    if not (url := get_chrome_debugger_url(port)) and auto_launch:
        p = launch_browser(port, headless=False, browser_name=browser_name, browser_path=browser_path)
        url = get_chrome_debugger_url(port)
    if not url: return None
    page = find_or_create_notebooklm_page(port)
    if not page or not (ws := page.get("webSocketDebuggerUrl")): return None
    if "notebooklm.google.com" not in page.get("url", ""):
        execute_cdp_command(ws, "Page.navigate", {"url": NOTEBOOKLM_URL})
        time.sleep(3)
    
    current_ws = ws
    if not check_if_logged_in_by_url(get_current_url(current_ws)):
        print("\nNOT LOGGED IN. Please log in in the browser window.\nWaiting for login...")
        start = time.time()
        logged = False
        while time.time() - start < 600:
            time.sleep(5)
            try:
                for pg in get_chrome_pages(port):
                    if check_if_logged_in_by_url(pg.get("url", "")):
                        if (new_ws := pg.get("webSocketDebuggerUrl")):
                            current_ws, logged = new_ws, True; break
                if logged: break
            except: pass
        if not logged: return None
    
    cl = get_page_cookies(current_ws)
    ck = {c["name"]: c["value"] for c in cl}
    if not validate_cookies(ck): return None
    html = get_page_html(current_ws)
    tokens = AuthTokens(cookies=ck, csrf_token=extract_csrf_from_page_source(html) or "", session_id=extract_session_id_from_html(html), extracted_at=time.time())
    save_tokens_to_cache(tokens)
    print("\nSUCCESS! Tokens cached.")
    if p: p.terminate(); p.wait(timeout=5)
    return tokens

def run_file_cookie_entry(cookie_file: str | None = None) -> AuthTokens | None:
    if not cookie_file:
        print("Guided file import...")
        try: cookie_file = input("Enter path to cookie file: ").strip()
        except: return None
    if not cookie_file: return None
    try:
        with open(Path(cookie_file).expanduser()) as f: s = f.read().strip()
        ck = {c.split("=")[0].strip(): c.split("=")[1].strip() for c in s.split(";") if "=" in c}
        tokens = AuthTokens(cookies=ck, csrf_token="", session_id="", extracted_at=time.time())
        save_tokens_to_cache(tokens)
        print("SUCCESS!")
        return tokens
    except: return None

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Authenticate with NotebookLM MCP")
    parser.add_argument("--file", nargs="?", const="", metavar="PATH")
    parser.add_argument("--port", type=int, default=CDP_DEFAULT_PORT)
    parser.add_argument("--show-tokens", action="store_true")
    parser.add_argument("--no-auto-launch", action="store_true")
    parser.add_argument("--browser", choices=["chrome", "vivaldi", "edge", "brave"], default="chrome")
    parser.add_argument("--browser-path", metavar="PATH")
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()
    if args.show_tokens:
        p = get_cache_path()
        if p.exists(): print(p.read_text())
        else: print("No tokens.")
        return 0
    try:
        if args.file is not None: tokens = run_file_cookie_entry(args.file or None)
        elif args.visible: tokens = run_auth_flow(args.port, not args.no_auto_launch, args.browser, args.browser_path)
        else:
            tokens = run_headless_auth(args.port+1, 30, args.browser, args.browser_path)
            if not tokens: tokens = run_auth_flow(args.port, not args.no_auto_launch, args.browser, args.browser_path)
        return 0 if tokens else 1
    except KeyboardInterrupt: return 1
    except Exception as e:
        print(f"ERROR: {e}")
        return 1

if __name__ == "__main__": sys.exit(main())
