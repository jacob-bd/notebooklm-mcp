#!/usr/bin/env python3
"""Debug script to test MCP HTTP communication and see actual responses."""

import asyncio
import json
import httpx


async def test_mcp_communication():
    """Test MCP HTTP communication step by step."""
    base_url = "http://localhost:8000"

    print("=" * 60)
    print("MCP HTTP Communication Debug")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: Health check
        print("\n1. Testing health endpoint...")
        health_response = await client.get(f"{base_url}/health")
        print(f"   Status: {health_response.status_code}")
        print(f"   Response: {health_response.text}")

        # Step 2: Initialize session
        print("\n2. Testing MCP session initialization...")
        init_payload = {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "debug-client",
                    "version": "1.0.0"
                }
            }
        }

        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"
        }

        print(f"   Request: {json.dumps(init_payload, indent=2)}")
        init_response = await client.post(f"{base_url}/mcp", json=init_payload, headers=headers)
        print(f"   Status: {init_response.status_code}")
        print(f"   Headers: {dict(init_response.headers)}")
        print(f"   Response text (first 500 chars):\n{init_response.text[:500]}")

        session_id = init_response.headers.get("mcp-session-id")
        print(f"   Session ID: {session_id}")

        # Step 3: Call a tool
        print("\n3. Testing tool call (notebook_list)...")
        tool_payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {
                "name": "notebook_list",
                "arguments": {"max_results": 5}
            }
        }

        if session_id:
            headers["mcp-session-id"] = session_id

        print(f"   Request: {json.dumps(tool_payload, indent=2)}")
        tool_response = await client.post(f"{base_url}/mcp", json=tool_payload, headers=headers)
        print(f"   Status: {tool_response.status_code}")
        print(f"   Response text (first 1000 chars):\n{tool_response.text[:1000]}")

        # Try to parse the response
        print("\n4. Parsing response...")
        response_text = tool_response.text

        # Check if it's SSE format
        if "data: " in response_text:
            print("   Response format: SSE (Server-Sent Events)")
            for line in response_text.split('\n'):
                if line.startswith('data: '):
                    data_json = line[6:]
                    print(f"   SSE data line: {data_json[:200]}...")
                    try:
                        parsed = json.loads(data_json)
                        print(f"   Parsed JSON structure:")
                        print(f"     - Has 'result': {'result' in parsed}")
                        print(f"     - Has 'error': {'error' in parsed}")
                        if 'result' in parsed:
                            result = parsed['result']
                            print(f"     - Result type: {type(result)}")
                            print(f"     - Has 'content': {'content' in result if isinstance(result, dict) else 'N/A'}")
                            if isinstance(result, dict) and 'content' in result:
                                content = result['content']
                                print(f"     - Content type: {type(content)}")
                                if isinstance(content, list) and len(content) > 0:
                                    print(f"     - First content item: {content[0]}")
                    except json.JSONDecodeError as e:
                        print(f"   ERROR parsing JSON: {e}")
        else:
            print("   Response format: Plain JSON")
            try:
                parsed = json.loads(response_text)
                print(f"   Parsed: {json.dumps(parsed, indent=2)[:500]}...")
            except json.JSONDecodeError as e:
                print(f"   ERROR: {e}")

        print("\n" + "=" * 60)
        print("Debug complete!")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_mcp_communication())
