# tests/core/test_base.py
"""Tests for BaseClient infrastructure class."""

from unittest.mock import patch, MagicMock
import pytest


def test_base_client_import():
    """Test that BaseClient can be imported."""
    from notebooklm_tools.core.base import BaseClient
    assert BaseClient is not None


def test_base_client_init():
    """Test BaseClient initialization with minimal args."""
    from notebooklm_tools.core.base import BaseClient
    
    with patch.object(BaseClient, '_refresh_auth_tokens'):
        client = BaseClient(cookies={"test": "cookie"})
        assert client.cookies == {"test": "cookie"}
        assert client._client is None


def test_base_client_init_with_csrf():
    """Test BaseClient initialization with CSRF token (skips refresh)."""
    from notebooklm_tools.core.base import BaseClient
    
    # When csrf_token is provided, _refresh_auth_tokens should NOT be called
    with patch.object(BaseClient, '_refresh_auth_tokens') as mock_refresh:
        client = BaseClient(cookies={"test": "cookie"}, csrf_token="test_token")
        mock_refresh.assert_not_called()
        assert client.csrf_token == "test_token"


def test_build_request_body():
    """Test building RPC request body."""
    from notebooklm_tools.core.base import BaseClient
    
    with patch.object(BaseClient, '_refresh_auth_tokens'):
        client = BaseClient(cookies={}, csrf_token="test_token")
        body = client._build_request_body("testRpc", ["param1"])
        assert "f.req=" in body
        assert "at=" in body
        assert "testRpc" in body


def test_build_url():
    """Test building batchexecute URL."""
    from notebooklm_tools.core.base import BaseClient
    
    with patch.object(BaseClient, '_refresh_auth_tokens'):
        client = BaseClient(cookies={}, csrf_token="test_token", session_id="test_sid")
        url = client._build_url("testRpc", "/notebook/123")
        assert "rpcids=testRpc" in url
        assert "source-path=" in url
        assert "f.sid=test_sid" in url


def test_get_httpx_cookies_from_dict():
    """Test converting dict cookies to httpx.Cookies."""
    from notebooklm_tools.core.base import BaseClient
    
    with patch.object(BaseClient, '_refresh_auth_tokens'):
        client = BaseClient(cookies={"SID": "abc123"}, csrf_token="token")
        cookies = client._get_httpx_cookies()
        # Should have cookies for both domains
        assert cookies.get("SID", domain=".google.com") == "abc123"
        assert cookies.get("SID", domain=".googleusercontent.com") == "abc123"


def test_get_httpx_cookies_from_list():
    """Test converting list of cookie dicts to httpx.Cookies."""
    from notebooklm_tools.core.base import BaseClient
    
    with patch.object(BaseClient, '_refresh_auth_tokens'):
        cookie_list = [
            {"name": "SID", "value": "abc123", "domain": ".google.com", "path": "/"}
        ]
        client = BaseClient(cookies=cookie_list, csrf_token="token")
        cookies = client._get_httpx_cookies()
        assert cookies.get("SID", domain=".google.com") == "abc123"
        # Should also duplicate to googleusercontent.com
        assert cookies.get("SID", domain=".googleusercontent.com") == "abc123"


def test_parse_response():
    """Test parsing batchexecute response."""
    from notebooklm_tools.core.base import BaseClient
    
    with patch.object(BaseClient, '_refresh_auth_tokens'):
        client = BaseClient(cookies={}, csrf_token="token")
        
        # Simulate a typical batchexecute response
        response_text = """)]}'
42
[["wrb.fr","testRpc","{\\"data\\":\\"value\\"}"]]
"""
        result = client._parse_response(response_text)
        assert len(result) == 1
        assert result[0][0][0] == "wrb.fr"
        assert result[0][0][1] == "testRpc"


def test_extract_rpc_result():
    """Test extracting RPC result from parsed response."""
    from notebooklm_tools.core.base import BaseClient
    
    with patch.object(BaseClient, '_refresh_auth_tokens'):
        client = BaseClient(cookies={}, csrf_token="token")
        
        parsed = [[["wrb.fr", "testRpc", '{"status": "ok"}']]]
        result = client._extract_rpc_result(parsed, "testRpc")
        assert result == {"status": "ok"}


def test_context_manager():
    """Test BaseClient as context manager."""
    from notebooklm_tools.core.base import BaseClient
    
    with patch.object(BaseClient, '_refresh_auth_tokens'):
        with BaseClient(cookies={}, csrf_token="token") as client:
            assert client is not None
        # After exiting, client should be closed
        assert client._client is None


def test_close():
    """Test explicit close."""
    from notebooklm_tools.core.base import BaseClient
    
    with patch.object(BaseClient, '_refresh_auth_tokens'):
        client = BaseClient(cookies={}, csrf_token="token")
        # Create but don't use _client
        client._client = MagicMock()
        client.close()
        assert client._client is None


def test_constants_available():
    """Test that RPC and API constants are available on BaseClient."""
    from notebooklm_tools.core.base import BaseClient
    
    # Check RPC IDs
    assert hasattr(BaseClient, 'RPC_LIST_NOTEBOOKS')
    assert hasattr(BaseClient, 'RPC_GET_NOTEBOOK')
    assert hasattr(BaseClient, 'RPC_CREATE_STUDIO')
    
    # Check URLs
    assert hasattr(BaseClient, 'BASE_URL')
    assert hasattr(BaseClient, 'BATCHEXECUTE_URL')
    
    # Check constant re-exports
    assert hasattr(BaseClient, 'STUDIO_TYPE_AUDIO')
    assert hasattr(BaseClient, 'AUDIO_FORMAT_DEEP_DIVE')
