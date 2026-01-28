#!/usr/bin/env python3
"""
MCP HTTP Client - Standalone script to interact with NotebookLM MCP server via HTTP transport.

Usage:
    python tests/mcp_client.py [global-options] <command> [command-options]

Examples:
    python tests/mcp_client.py health
    python tests/mcp_client.py list
    python tests/mcp_client.py list --max 10
    python tests/mcp_client.py query <notebook-id> "What is the main topic?"
    python tests/mcp_client.py query <notebook-id> "What is the main topic?" --stream
    python tests/mcp_client.py --json list
"""

import argparse
import json
import sys
import time
from typing import Any, Callable, Optional
from contextlib import contextmanager

import httpx


class MCPClient:
    """HTTP client for MCP server interactions."""

    def __init__(self, base_url: str, timeout: float = 60.0):
        """
        Initialize MCP client.

        Args:
            base_url: Base URL of MCP server (e.g., http://localhost:8000)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.Client] = None
        self._session_id: Optional[str] = None

    def __enter__(self):
        """Enter context manager - create HTTP client and initialize session."""
        self._client = httpx.Client(timeout=self.timeout)
        self._initialize_session()
        return self

    def _initialize_session(self):
        """Initialize MCP session by making a handshake request."""
        if not self._client:
            raise RuntimeError("Client not initialized.")

        url = f"{self.base_url}/mcp"

        # MCP initialization request
        payload = {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "mcp-http-client",
                    "version": "1.0.0"
                }
            }
        }

        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"
        }

        try:
            response = self._client.post(url, json=payload, headers=headers)
            response.raise_for_status()

            # Extract session ID from response headers
            self._session_id = response.headers.get("mcp-session-id")

            if not self._session_id:
                print("Warning: No session ID received from server", file=sys.stderr)

            # Try to parse response to validate session initialization
            if response.text:
                try:
                    response.json()
                except json.JSONDecodeError:
                    pass  # Some servers may return empty response for init

        except Exception as e:
            print(f"Warning: Session initialization failed: {e}", file=sys.stderr)

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager - cleanup HTTP client."""
        if self._client:
            self._client.close()

    def _parse_sse_response(self, sse_text: str) -> dict:
        """
        Parse Server-Sent Events (SSE) format response.

        SSE format looks like:
        event: message
        data: {"jsonrpc":"2.0","id":"1","result":{...}}

        Args:
            sse_text: Raw SSE response text

        Returns:
            Parsed JSON-RPC response
        """
        # Split by lines and find data: lines
        for line in sse_text.split('\n'):
            line = line.strip()
            if line.startswith('data: '):
                data_json = line[6:]  # Remove 'data: ' prefix
                try:
                    return json.loads(data_json)
                except json.JSONDecodeError as e:
                    print(f"Error: Invalid JSON in SSE data: {data_json[:200]}", file=sys.stderr)
                    sys.exit(1)

        # If no data line found, try parsing the whole response as JSON
        try:
            return json.loads(sse_text)
        except json.JSONDecodeError:
            print(f"Error: Could not parse SSE response: {sse_text[:200]}", file=sys.stderr)
            sys.exit(1)

    def _call_tool(self, tool_name: str, arguments: dict) -> dict:
        """
        Call an MCP tool via HTTP POST using JSON-RPC 2.0 format.

        Args:
            tool_name: Name of the MCP tool
            arguments: Tool arguments as dictionary

        Returns:
            Response dictionary from server

        Raises:
            SystemExit: On connection or HTTP errors
        """
        if not self._client:
            raise RuntimeError("Client not initialized. Use as context manager.")

        url = f"{self.base_url}/mcp"

        # Use proper MCP JSON-RPC 2.0 format
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }

        # Add headers required by FastMCP HTTP server
        # Must accept both application/json and text/event-stream for MCP
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"
        }

        # Include session ID if available
        if self._session_id:
            headers["mcp-session-id"] = self._session_id

        try:
            response = self._client.post(url, json=payload, headers=headers)
            response.raise_for_status()

            # Parse SSE (Server-Sent Events) response
            if not response.text:
                print(f"Error: Empty response from server", file=sys.stderr)
                sys.exit(1)

            # MCP HTTP transport uses SSE format
            # Parse "data: {...}" lines from SSE stream
            rpc_response = self._parse_sse_response(response.text)

            # Check for JSON-RPC error
            if "error" in rpc_response:
                error = rpc_response["error"]
                print(f"Error: {error.get('message', 'Unknown error')}", file=sys.stderr)
                sys.exit(1)

            # Extract result from JSON-RPC response
            # MCP wraps the actual result in result.content[0].text
            result = rpc_response.get("result", {})

            # Check if result is wrapped in MCP content format
            if "content" in result and isinstance(result["content"], list) and len(result["content"]) > 0:
                content_item = result["content"][0]
                if content_item.get("type") == "text":
                    # Parse the inner JSON from the text field
                    try:
                        return json.loads(content_item["text"])
                    except json.JSONDecodeError:
                        return result

            return result
        except httpx.ConnectError:
            print(f"Error: Cannot connect to server at {self.base_url}", file=sys.stderr)
            print("Make sure the MCP server is running with HTTP transport.", file=sys.stderr)
            sys.exit(1)
        except httpx.HTTPStatusError as e:
            print(f"Error: Server returned {e.response.status_code}: {e.response.reason_phrase}", file=sys.stderr)
            sys.exit(1)
        except httpx.TimeoutException:
            print(f"Error: Request timed out after {self.timeout}s", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error: Unexpected error: {e}", file=sys.stderr)
            sys.exit(1)

    def _call_tool_streaming(
        self,
        tool_name: str,
        arguments: dict,
        on_progress: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        """
        Call an MCP tool with streaming SSE support.

        Handles progress notifications in real-time as they arrive.

        Args:
            tool_name: Name of the MCP tool
            arguments: Tool arguments as dictionary
            on_progress: Optional callback for progress notifications

        Returns:
            Final response dictionary from server
        """
        if not self._client:
            raise RuntimeError("Client not initialized. Use as context manager.")

        url = f"{self.base_url}/mcp"

        # Generate a unique progress token for this request
        import uuid
        progress_token = str(uuid.uuid4())

        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
                "_meta": {
                    "progressToken": progress_token
                }
            }
        }

        headers = {
            "Accept": "text/event-stream, application/json",
            "Content-Type": "application/json"
        }

        if self._session_id:
            headers["mcp-session-id"] = self._session_id

        try:
            # Use streaming request to get SSE events as they arrive
            with self._client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()

                final_result = None
                buffer = ""

                for chunk in response.iter_text():
                    buffer += chunk

                    # Process complete SSE events
                    while "\n\n" in buffer or "\ndata: " in buffer:
                        # Find complete events
                        lines = buffer.split("\n")
                        processed_lines = 0

                        for i, line in enumerate(lines):
                            line = line.strip()

                            if line.startswith("data: "):
                                data_json = line[6:]
                                try:
                                    event_data = json.loads(data_json)

                                    # Check if this is a progress notification
                                    if event_data.get("method") == "notifications/progress":
                                        if on_progress:
                                            params = event_data.get("params", {})
                                            on_progress(params)
                                    # Check if this is the final result
                                    elif "result" in event_data or "error" in event_data:
                                        final_result = event_data
                                        processed_lines = i + 1
                                        break
                                except json.JSONDecodeError:
                                    pass

                            processed_lines = i + 1

                        # Remove processed lines from buffer
                        buffer = "\n".join(lines[processed_lines:])

                # Parse final result
                if final_result:
                    if "error" in final_result:
                        error = final_result["error"]
                        print(f"Error: {error.get('message', 'Unknown error')}", file=sys.stderr)
                        sys.exit(1)

                    result = final_result.get("result", {})
                    if "content" in result and isinstance(result["content"], list) and len(result["content"]) > 0:
                        content_item = result["content"][0]
                        if content_item.get("type") == "text":
                            try:
                                return json.loads(content_item["text"])
                            except json.JSONDecodeError:
                                return result
                    return result

                # Fallback: parse any remaining buffer as non-streaming response
                return self._parse_sse_response(buffer) if buffer.strip() else {}

        except httpx.ConnectError:
            print(f"Error: Cannot connect to server at {self.base_url}", file=sys.stderr)
            sys.exit(1)
        except httpx.HTTPStatusError as e:
            print(f"Error: Server returned {e.response.status_code}: {e.response.reason_phrase}", file=sys.stderr)
            sys.exit(1)
        except httpx.TimeoutException:
            print(f"Error: Request timed out after {self.timeout}s", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error: Unexpected error: {e}", file=sys.stderr)
            sys.exit(1)

    def health_check(self) -> dict:
        """
        Check server health.

        Returns:
            Health status dictionary
        """
        if not self._client:
            raise RuntimeError("Client not initialized. Use as context manager.")

        url = f"{self.base_url}/health"

        # Add Accept header required by FastMCP HTTP server
        # Must accept both application/json and text/event-stream for MCP
        headers = {"Accept": "application/json, text/event-stream"}

        try:
            response = self._client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError:
            print(f"Error: Cannot connect to server at {self.base_url}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Error: Health check failed: {e}", file=sys.stderr)
            sys.exit(1)

    def list_notebooks(self, max_results: int = 100) -> dict:
        """
        List all notebooks.

        Args:
            max_results: Maximum number of notebooks to return

        Returns:
            Response with notebooks list
        """
        return self._call_tool("notebook_list", {"max_results": max_results})

    def get_notebook(self, notebook_id: str) -> dict:
        """
        Get details of a specific notebook.

        Args:
            notebook_id: Notebook ID

        Returns:
            Response with notebook details
        """
        return self._call_tool("notebook_get", {"notebook_id": notebook_id})

    def query_notebook(
        self,
        notebook_id: str,
        query: str,
        source_ids: Optional[list[str]] = None,
        conversation_id: Optional[str] = None,
        stream: bool = False,
        on_progress: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        """
        Query a notebook with AI.

        Args:
            notebook_id: Notebook ID
            query: Question to ask
            source_ids: Optional list of source IDs to filter
            conversation_id: Optional conversation ID to continue chat
            stream: Enable streaming mode with progress notifications
            on_progress: Callback for progress notifications (only used when stream=True)

        Returns:
            Response with AI answer
        """
        arguments = {"notebook_id": notebook_id, "query": query}
        if source_ids:
            arguments["source_ids"] = source_ids
        if conversation_id:
            arguments["conversation_id"] = conversation_id

        if stream:
            # Use streaming tool with progress callback
            return self._call_tool_streaming(
                "notebook_query_stream",
                arguments,
                on_progress=on_progress,
            )
        else:
            return self._call_tool("notebook_query", arguments)

    def query_notebook_stream(
        self,
        notebook_id: str,
        query: str,
        source_ids: Optional[list[str]] = None,
        conversation_id: Optional[str] = None,
        verbose: bool = True,
    ) -> dict:
        """
        Query a notebook with AI using streaming mode.

        Displays thinking steps and answer progress in real-time.

        Args:
            notebook_id: Notebook ID
            query: Question to ask
            source_ids: Optional list of source IDs to filter
            conversation_id: Optional conversation ID to continue chat
            verbose: Print progress to console

        Returns:
            Response with AI answer and thinking steps
        """
        thinking_count = 0
        start_time = time.time()

        def progress_callback(params: dict):
            nonlocal thinking_count
            message = params.get("message", "")
            progress = params.get("progress", 0)
            total = params.get("total", 0)

            if verbose:
                if message.startswith("🤔"):
                    thinking_count += 1
                    print(f"  [{progress}/{total}] {message}", file=sys.stderr)
                elif message.startswith("💡"):
                    print(f"  [{progress}/{total}] {message}", file=sys.stderr)

        if verbose:
            print(f"📡 Streaming query: {query[:50]}...", file=sys.stderr)
            print("-" * 60, file=sys.stderr)

        result = self.query_notebook(
            notebook_id=notebook_id,
            query=query,
            source_ids=source_ids,
            conversation_id=conversation_id,
            stream=True,
            on_progress=progress_callback,
        )

        if verbose:
            elapsed = time.time() - start_time
            print("-" * 60, file=sys.stderr)
            print(f"✅ Completed in {elapsed:.2f}s with {thinking_count} thinking steps", file=sys.stderr)

        return result


class OutputFormatter:
    """Format output for console display."""

    @staticmethod
    def format_json(data: dict) -> str:
        """Format data as JSON string."""
        return json.dumps(data, indent=2)

    @staticmethod
    def format_pretty(data: dict, command: str) -> str:
        """
        Format data in human-readable format.

        Args:
            data: Response data from server
            command: Command name (health, list, get, query)

        Returns:
            Formatted string
        """
        # Check for API errors
        if data.get("status") == "error":
            error_msg = data.get("error", "Unknown error")
            return f"API Error: {error_msg}"

        if command == "health":
            status = data.get("status", "unknown")
            return f"Server Status: {status}"

        elif command == "list":
            count = data.get("count", 0)
            notebooks = data.get("notebooks", [])

            if count == 0:
                return "No notebooks found."

            lines = [f"Found {count} notebook(s):\n"]
            lines.append(f"{'ID':<40} {'Title':<50} {'Sources':<10}")
            lines.append("-" * 100)

            for nb in notebooks:
                nb_id = nb.get("id", "")[:38]
                title = nb.get("title", "Untitled")[:48]
                # Use source_count field if available, otherwise count sources array
                source_count = nb.get("source_count", len(nb.get("sources", [])))
                lines.append(f"{nb_id:<40} {title:<50} {source_count:<10}")

            return "\n".join(lines)

        elif command == "get":
            notebook_data = data.get("notebook", [])

            # Unwrap nested array structure: notebook is [[title, sources, id, ...]]
            if isinstance(notebook_data, list) and len(notebook_data) > 0:
                nb_array = notebook_data[0] if isinstance(notebook_data[0], list) else []

                title = nb_array[0] if len(nb_array) > 0 else "N/A"
                sources = nb_array[1] if len(nb_array) > 1 and isinstance(nb_array[1], list) else []
                nb_id = nb_array[2] if len(nb_array) > 2 else "N/A"
            else:
                title = "N/A"
                sources = []
                nb_id = "N/A"

            lines = [f"Notebook: {title}"]
            lines.append(f"ID: {nb_id}")
            lines.append(f"Sources: {len(sources)}")

            if sources:
                lines.append("\nSources:")
                for i, src in enumerate(sources, 1):
                    # Source structure: [[id], title, metadata, ...]
                    if isinstance(src, list) and len(src) >= 2:
                        src_title = src[1] if isinstance(src[1], str) else "Untitled"
                        # Try to get source type from metadata
                        src_type = "unknown"
                        if len(src) > 2 and isinstance(src[2], list):
                            metadata = src[2]
                            # Source type is typically at metadata[4]
                            if len(metadata) > 4:
                                type_code = metadata[4]
                                type_map = {1: "google_docs", 2: "google_other", 14: "file", 3: "pasted_text"}
                                src_type = type_map.get(type_code, f"type_{type_code}")
                        lines.append(f"  {i}. [{src_type}] {src_title}")

            return "\n".join(lines)

        elif command == "query":
            answer = data.get("answer", "")
            conv_id = data.get("conversation_id")

            lines = ["Answer:", "-" * 80, answer]

            if conv_id:
                lines.extend(["", f"Conversation ID: {conv_id}"])
                lines.append("(Use this ID to continue the conversation)")

            return "\n".join(lines)

        else:
            # Fallback to JSON for unknown commands
            return json.dumps(data, indent=2)


def create_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="MCP HTTP Client - Interact with NotebookLM MCP server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s health
  %(prog)s list
  %(prog)s list --max 10
  %(prog)s get <notebook-id>
  %(prog)s query <notebook-id> "What is the main topic?"
  %(prog)s query <notebook-id> "What is the main topic?" --stream
  %(prog)s query <notebook-id> "Tell me more" --conversation <conv-id>
  %(prog)s --json list
  %(prog)s -v query <notebook-id> "Search query"
        """,
    )

    # Global options
    parser.add_argument(
        "--host", default="localhost", help="Server host (default: localhost)"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Server port (default: 8000)"
    )
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="Request timeout in seconds (default: 60)"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output raw JSON instead of pretty format"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose output with timing"
    )

    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Health command
    subparsers.add_parser("health", help="Check server health")

    # List command
    list_parser = subparsers.add_parser("list", help="List notebooks")
    list_parser.add_argument(
        "--max", type=int, default=100, dest="max_results", help="Maximum number of notebooks to return"
    )

    # Get command
    get_parser = subparsers.add_parser("get", help="Get notebook details")
    get_parser.add_argument("notebook_id", help="Notebook ID")

    # Query command
    query_parser = subparsers.add_parser("query", help="Query notebook with AI")
    query_parser.add_argument("notebook_id", help="Notebook ID")
    query_parser.add_argument("query", help="Question to ask")
    query_parser.add_argument(
        "--sources", nargs="+", help="Filter to specific source IDs"
    )
    query_parser.add_argument(
        "--conversation", help="Conversation ID to continue chat"
    )
    query_parser.add_argument(
        "--stream", action="store_true", help="Enable streaming mode with progress notifications"
    )

    return parser


def main():
    """Main CLI entry point."""
    # Ensure UTF-8 encoding for console output (fixes Vietnamese/Unicode display on Windows)
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Build base URL
    base_url = f"http://{args.host}:{args.port}"

    # Create client and execute command
    start_time = time.time()

    with MCPClient(base_url, timeout=args.timeout) as client:
        try:
            # Execute command
            if args.command == "health":
                result = client.health_check()
            elif args.command == "list":
                result = client.list_notebooks(max_results=args.max_results)
            elif args.command == "get":
                result = client.get_notebook(args.notebook_id)
            elif args.command == "query":
                if getattr(args, 'stream', False):
                    # Use streaming mode with progress display
                    result = client.query_notebook_stream(
                        args.notebook_id,
                        args.query,
                        source_ids=args.sources,
                        conversation_id=args.conversation,
                        verbose=not args.json,  # Suppress progress output if JSON mode
                    )
                else:
                    result = client.query_notebook(
                        args.notebook_id,
                        args.query,
                        source_ids=args.sources,
                        conversation_id=args.conversation,
                    )
            else:
                print(f"Error: Unknown command '{args.command}'", file=sys.stderr)
                sys.exit(1)

            elapsed = time.time() - start_time

            # Format and print output
            if args.json:
                print(OutputFormatter.format_json(result))
            else:
                print(OutputFormatter.format_pretty(result, args.command))

            # Verbose timing
            if args.verbose:
                print(f"\n[Completed in {elapsed:.2f}s]", file=sys.stderr)

            # Exit with error code if API returned error status
            if result.get("status") == "error":
                sys.exit(1)

        except KeyboardInterrupt:
            print("\nInterrupted by user", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
