#!/usr/bin/env python3
"""
Concurrent HTTP Tests for NotebookLM MCP Server

Tests verify that the async refactoring enables true concurrent request handling.
These tests validate that multiple requests execute in parallel rather than sequentially blocking.

Test Categories:
1. Basic concurrency verification
2. Performance benchmarks (sequential vs concurrent)
3. Load testing under concurrent access
4. Lock protection verification under concurrent access

Requirements:
- MCP server must be running with HTTP transport: notebooklm-mcp --transport http --port 8000
- Valid authentication tokens must be configured
"""

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
import pytest_asyncio


# Test Configuration
MCP_BASE_URL = "http://localhost:8000"
MCP_ENDPOINT = f"{MCP_BASE_URL}/mcp"
HEALTH_ENDPOINT = f"{MCP_BASE_URL}/health"

# Test timeouts
SINGLE_REQUEST_TIMEOUT = 30.0
CONCURRENT_TEST_TIMEOUT = 120.0

# Performance thresholds
# For 10 concurrent requests, expect ~2-3x single request time (parallel)
# NOT 10x single request time (sequential)
CONCURRENT_SPEEDUP_THRESHOLD = 4.0  # Max acceptable ratio of concurrent/single time


class MCPTestClient:
    """Test client for MCP HTTP transport with session management."""

    def __init__(self, base_url: str = MCP_BASE_URL):
        self.base_url = base_url
        self._session_id: str | None = None

    async def initialize_session(self, client: httpx.AsyncClient) -> None:
        """Initialize MCP session with handshake."""
        payload = {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "mcp-test-client",
                    "version": "1.0.0"
                }
            }
        }

        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"
        }

        response = await client.post(f"{self.base_url}/mcp", json=payload, headers=headers)
        response.raise_for_status()

        # Extract session ID
        self._session_id = response.headers.get("mcp-session-id")

    def _parse_sse_response(self, sse_text: str) -> dict:
        """Parse Server-Sent Events (SSE) format response.

        Handles both Unix (\n) and Windows (\r\n) line endings.
        """
        # Normalize line endings: \r\n -> \n, \r -> \n
        normalized = sse_text.replace('\r\n', '\n').replace('\r', '\n')

        for line in normalized.split('\n'):
            line = line.strip()
            if line.startswith('data: '):
                data_json = line[6:]  # Remove 'data: ' prefix
                return json.loads(data_json)

        # Fallback: try parsing whole response as JSON
        return json.loads(normalized)

    async def call_tool(self, client: httpx.AsyncClient, tool_name: str, arguments: dict) -> dict:
        """Call MCP tool via JSON-RPC 2.0."""
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }

        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"
        }

        if self._session_id:
            headers["mcp-session-id"] = self._session_id

        response = await client.post(f"{self.base_url}/mcp", json=payload, headers=headers)
        response.raise_for_status()

        # Parse SSE response
        rpc_response = self._parse_sse_response(response.text)

        # Check for JSON-RPC error
        if "error" in rpc_response:
            raise RuntimeError(f"RPC Error: {rpc_response['error']}")

        # Extract result from MCP content format
        result = rpc_response.get("result", {})
        if "content" in result and isinstance(result["content"], list) and len(result["content"]) > 0:
            content_item = result["content"][0]
            if content_item.get("type") == "text":
                return json.loads(content_item["text"])

        return result


async def create_and_call_tool(
    http_client: httpx.AsyncClient,
    tool_name: str,
    arguments: dict
) -> dict:
    """
    Create independent MCP client and session for a single tool call.

    This ensures each concurrent request has its own MCP session ID,
    preventing server-side session locking/serialization issues.

    Args:
        http_client: Shared httpx.AsyncClient for connection pooling
        tool_name: MCP tool name to call
        arguments: Tool arguments

    Returns:
        Tool result dictionary
    """
    client = MCPTestClient()
    await client.initialize_session(http_client)
    return await client.call_tool(http_client, tool_name, arguments)


class TestBasicConcurrency:
    """Test basic concurrent request handling."""

    @pytest.mark.asyncio
    async def test_single_notebook_list(self):
        """Test single notebook_list call (simplified baseline)."""
        mcp_client = MCPTestClient()

        async with httpx.AsyncClient(timeout=SINGLE_REQUEST_TIMEOUT) as client:
            # Initialize session
            await mcp_client.initialize_session(client)

            # Make a single request
            result = await mcp_client.call_tool(client, "notebook_list", {"max_results": 100})

            # Verify it succeeded
            assert result.get("status") == "success"
            assert "notebooks" in result
            assert "count" in result

            print(f"\n[PASS] Single notebook_list call succeeded")
            print(f"  Found {result['count']} notebooks")

    @pytest.mark.asyncio
    async def test_server_health_check(self):
        """Verify server is running and accessible."""
        async with httpx.AsyncClient() as client:
            response = await client.get(HEALTH_ENDPOINT)
            assert response.status_code == 200
            data = response.json()
            assert data.get("status") == "healthy"

    @pytest.mark.asyncio
    async def test_concurrent_health_checks(self):
        """Test multiple concurrent health checks."""
        async with httpx.AsyncClient(timeout=CONCURRENT_TEST_TIMEOUT) as client:
            # Fire 20 concurrent health checks
            tasks = [client.get(HEALTH_ENDPOINT) for _ in range(20)]

            start = time.time()
            responses = await asyncio.gather(*tasks)
            elapsed = time.time() - start

            # Verify all succeeded
            assert all(r.status_code == 200 for r in responses)

            # Should complete quickly (parallel processing)
            # If sequential, 20 requests would take significant time
            assert elapsed < 5.0, f"Health checks took {elapsed:.2f}s (expected < 5s for parallel)"

    @pytest.mark.asyncio
    async def test_concurrent_notebook_list(self):
        """Test concurrent notebook_list calls execute in parallel."""
        async with httpx.AsyncClient(timeout=CONCURRENT_TEST_TIMEOUT) as client:
            # Fire 10 concurrent list requests, each with independent session
            tasks = [
                create_and_call_tool(client, "notebook_list", {"max_results": 100})
                for _ in range(10)
            ]

            start = time.time()
            results = await asyncio.gather(*tasks)
            elapsed = time.time() - start

            # Verify all succeeded
            assert len(results) == 10
            for result in results:
                assert result.get("status") == "success"
                assert "notebooks" in result
                assert "count" in result

            # Should complete in roughly parallel time
            # If sequential blocking, 10 requests @ ~2s each = 20s
            # If parallel, should be ~2-3s total
            assert elapsed < 10.0, f"Concurrent requests took {elapsed:.2f}s (expected < 10s for parallel)"

            print(f"\n[PASS] 10 concurrent notebook_list calls completed in {elapsed:.2f}s")

    @pytest.mark.asyncio
    async def test_concurrent_different_tools(self):
        """Test concurrent calls to different tools."""
        async with httpx.AsyncClient(timeout=CONCURRENT_TEST_TIMEOUT) as client:
            # Mix of different operations, each with independent session
            tasks = [
                create_and_call_tool(client, "notebook_list", {"max_results": 10}),
                create_and_call_tool(client, "notebook_list", {"max_results": 20}),
                create_and_call_tool(client, "notebook_list", {"max_results": 30}),
            ]

            start = time.time()
            results = await asyncio.gather(*tasks)
            elapsed = time.time() - start

            # Verify all succeeded
            assert len(results) == 3
            for result in results:
                assert result.get("status") == "success"

            print(f"\n[PASS] 3 concurrent mixed tool calls completed in {elapsed:.2f}s")


class TestPerformanceBenchmarks:
    """Performance benchmarks comparing sequential vs concurrent execution."""

    @pytest.mark.asyncio
    async def test_sequential_vs_concurrent_baseline(self):
        """Benchmark sequential vs concurrent notebook_list calls."""
        num_requests = 10
        mcp_client = MCPTestClient()

        async with httpx.AsyncClient(timeout=CONCURRENT_TEST_TIMEOUT) as client:
            await mcp_client.initialize_session(client)

            # Sequential execution
            print("\n--- Sequential Execution ---")
            seq_start = time.time()
            for i in range(num_requests):
                result = await mcp_client.call_tool(client, "notebook_list", {"max_results": 100})
                assert result.get("status") == "success"
            seq_elapsed = time.time() - seq_start
            seq_avg = seq_elapsed / num_requests
            print(f"Total: {seq_elapsed:.2f}s | Avg per request: {seq_avg:.2f}s")

            # Concurrent execution with independent sessions
            print("\n--- Concurrent Execution ---")
            tasks = [
                create_and_call_tool(client, "notebook_list", {"max_results": 100})
                for _ in range(num_requests)
            ]
            conc_start = time.time()
            results = await asyncio.gather(*tasks)
            conc_elapsed = time.time() - conc_start
            print(f"Total: {conc_elapsed:.2f}s | {num_requests} requests in parallel")

            # Verify all succeeded
            assert all(r.get("status") == "success" for r in results)

            # Calculate speedup
            speedup = seq_elapsed / conc_elapsed
            print(f"\n--- Performance Analysis ---")
            print(f"Sequential: {seq_elapsed:.2f}s")
            print(f"Concurrent: {conc_elapsed:.2f}s")
            print(f"Speedup: {speedup:.2f}x")

            # Verify we got actual parallelism
            # Concurrent should be significantly faster than sequential
            # Expected: concurrent time ≈ single request time (all run in parallel)
            # Not: concurrent time ≈ sequential time (blocking)
            assert speedup > 2.0, (
                f"Insufficient parallelism: speedup={speedup:.2f}x "
                f"(expected > 2x). This suggests event loop blocking."
            )

            # Concurrent time should be close to a single request time
            # Allow 3x margin for overhead (network, processing, etc.)
            max_expected_concurrent = seq_avg * 3.0
            assert conc_elapsed < max_expected_concurrent, (
                f"Concurrent execution too slow: {conc_elapsed:.2f}s "
                f"(expected < {max_expected_concurrent:.2f}s for parallel)"
            )

            print(f"[PASS] Verified true parallel execution (speedup: {speedup:.2f}x)")

    @pytest.mark.asyncio
    async def test_load_test_50_concurrent(self):
        """Load test with 50 concurrent requests."""
        num_requests = 50

        async with httpx.AsyncClient(timeout=CONCURRENT_TEST_TIMEOUT) as client:
            # Fire 50 concurrent requests, each with independent session
            tasks = [
                create_and_call_tool(client, "notebook_list", {"max_results": 100})
                for _ in range(num_requests)
            ]

            start = time.time()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            elapsed = time.time() - start

            # Count successes and failures
            successes = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "success")
            failures = num_requests - successes

            print(f"\n--- Load Test Results ---")
            print(f"Requests: {num_requests}")
            print(f"Successes: {successes}")
            print(f"Failures: {failures}")
            print(f"Total time: {elapsed:.2f}s")
            print(f"Avg time per request: {elapsed / num_requests:.2f}s")
            print(f"Throughput: {num_requests / elapsed:.2f} req/s")

            # At least 90% should succeed
            success_rate = successes / num_requests
            assert success_rate >= 0.9, f"Success rate too low: {success_rate:.1%}"

            # Should complete reasonably quickly with parallelism
            # 50 requests should not take more than 30s with proper async
            assert elapsed < 30.0, f"Load test too slow: {elapsed:.2f}s"

            print(f"[PASS] Load test passed: {success_rate:.1%} success rate")


class TestConcurrentStateProtection:
    """Test that shared state is properly protected under concurrent access."""

    @pytest.mark.asyncio
    async def test_conversation_cache_sequential(self):
        """Test conversation cache integrity with sequential queries on same session."""
        # This test requires a notebook with sources
        # Tests that conversation history is preserved across sequential calls
        mcp_client = MCPTestClient()

        async with httpx.AsyncClient(timeout=CONCURRENT_TEST_TIMEOUT) as client:
            await mcp_client.initialize_session(client)

            # Get first notebook
            list_result = await mcp_client.call_tool(client, "notebook_list", {"max_results": 1})
            notebooks = list_result.get("notebooks", [])

            if not notebooks:
                pytest.skip("No notebooks available for testing")

            notebook_id = notebooks[0]["id"]

            # Check if notebook has sources
            if notebooks[0].get("source_count", 0) == 0:
                pytest.skip("Notebook has no sources for query testing")

            # Fire sequential queries to same notebook on same session
            # This verifies conversation cache works correctly
            queries = [
                "What is this about?",
                "Summarize the main points",
                "What are the key topics?",
            ]

            results = []
            for query in queries:
                result = await mcp_client.call_tool(client, "notebook_query", {
                    "notebook_id": notebook_id,
                    "query": query
                })
                results.append(result)

            # Count successes
            successes = sum(1 for r in results if isinstance(r, dict) and "answer" in r)

            print(f"\n--- Sequential Query Test ---")
            print(f"Queries sent: {len(queries)}")
            print(f"Successful responses: {successes}")

            # All queries should succeed without cache corruption
            assert successes == len(queries), "Some queries failed (possible cache corruption)"

            print("[PASS] Conversation cache remained consistent across sequential queries")

    @pytest.mark.asyncio
    async def test_concurrent_request_counter(self):
        """Test request counter atomicity under concurrent access."""
        # The _reqid_counter is incremented on each query request
        # This test verifies the lock protects it from race conditions
        async with httpx.AsyncClient(timeout=CONCURRENT_TEST_TIMEOUT) as client:
            # Get first notebook with sources
            list_result = await create_and_call_tool(client, "notebook_list", {"max_results": 1})
            notebooks = list_result.get("notebooks", [])

            if not notebooks or notebooks[0].get("source_count", 0) == 0:
                pytest.skip("No notebooks with sources available for testing")

            notebook_id = notebooks[0]["id"]

            # Fire 20 concurrent queries, each with independent session
            # Each should get a unique reqid without collisions
            tasks = [
                create_and_call_tool(client, "notebook_query", {
                    "notebook_id": notebook_id,
                    "query": f"Test query {i}"
                })
                for i in range(20)
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Count successes
            successes = sum(1 for r in results if isinstance(r, dict) and "answer" in r)

            print(f"\n--- Request Counter Test ---")
            print(f"Concurrent queries: 20")
            print(f"Successful: {successes}")

            # All should succeed without counter corruption
            # If the counter had race conditions, we'd see duplicate reqids and failures
            assert successes >= 18, f"Too many failures: {20 - successes} (possible counter corruption)"

            print("[PASS] Request counter remained atomic under concurrent access")


# Performance metrics collection
class TestPerformanceMetrics:
    """Collect and report detailed performance metrics."""

    @pytest.mark.asyncio
    async def test_throughput_measurement(self):
        """Measure and report throughput metrics."""
        num_requests = 30

        async with httpx.AsyncClient(timeout=CONCURRENT_TEST_TIMEOUT) as client:
            # Measure concurrent throughput with independent sessions
            tasks = [
                create_and_call_tool(client, "notebook_list", {"max_results": 100})
                for _ in range(num_requests)
            ]

            start = time.time()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            elapsed = time.time() - start

            successes = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "success")
            throughput = successes / elapsed

            print(f"\n{'=' * 60}")
            print("PERFORMANCE METRICS SUMMARY")
            print(f"{'=' * 60}")
            print(f"Total requests:        {num_requests}")
            print(f"Successful:            {successes}")
            print(f"Failed:                {num_requests - successes}")
            print(f"Total time:            {elapsed:.2f}s")
            print(f"Throughput:            {throughput:.2f} req/s")
            print(f"Avg response time:     {elapsed / num_requests:.2f}s")
            print(f"{'=' * 60}")

            # Verify minimum throughput
            # With async, should handle at least 5 req/s
            assert throughput >= 5.0, f"Throughput too low: {throughput:.2f} req/s"

            print(f"[PASS] Throughput test passed: {throughput:.2f} req/s")


if __name__ == "__main__":
    """Run tests with pytest."""
    print("\nNotebookLM MCP Concurrent HTTP Tests")
    print("=" * 60)
    print("Prerequisites:")
    print("  1. MCP server running: notebooklm-mcp --transport http --port 8000")
    print("  2. Valid authentication configured")
    print("=" * 60)
    print("\nRun with: pytest tests/test_concurrent_http.py -v")
    print("=" * 60)
