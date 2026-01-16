import pytest
import json
from unittest.mock import MagicMock, patch
import httpx
from notebooklm_mcp.api_client import NotebookLMClient, AuthenticationError

@pytest.fixture
def mock_client():
    cookies = {"SID": "test_sid"}
    with patch.object(NotebookLMClient, '_refresh_auth_tokens') as mock_refresh:
        client = NotebookLMClient(cookies=cookies, csrf_token="old_token", session_id="old_sid")
        return client

class TestNotebookLMClientAuth:
    """Test authentication and retry logic."""

    def test_rpc_error_16_detection(self, mock_client):
        """Test that RPC Error 16 (auth expired) is correctly detected."""
        # This response mimics the structure of an RPC Error 16
        # Signature: ["wrb.fr", "RPC_ID", null, null, null, [16], "generic"]
        # The parser expects a list of chunks.
        
        # We need to construct a response that _parse_response will turn into the structure above.
        # _parse_response splits by newline and parses JSON.
        
        # A wrapped response might look like:
        # )]}'
        # length
        # [[["wrb.fr", "rLM1Ne", null, null, null, [16], "generic"]]]
        
        rpc_id = "rLM1Ne"
        error_payload = [[["wrb.fr", rpc_id, None, None, None, [16], "generic"]]]
        
        # We'll mock _call_rpc's internal helpers directly or just test _extract_rpc_result
        # Let's test _extract_rpc_result first to be precise
        
        with pytest.raises(AuthenticationError, match="RPC Error 16"):
            mock_client._extract_rpc_result(error_payload, rpc_id)

    def test_auto_retry_on_401(self, mock_client):
        """Test that client refreshes tokens and retries on HTTP 401."""
        
        with patch.object(mock_client, '_get_client') as mock_get_client, \
             patch.object(mock_client, '_refresh_auth_tokens') as mock_refresh:
            
            # Setup HTTP client mock
            http_client = MagicMock(spec=httpx.Client)
            mock_get_client.return_value = http_client
            
            # First call raises 401
            req_401 = httpx.Request("POST", "https://notebooklm.google.com/batchexecute")
            resp_401 = httpx.Response(401, request=req_401)
            
            # Second call succeeds
            req_200 = httpx.Request("POST", "https://notebooklm.google.com/batchexecute")
            resp_200 = httpx.Response(200, request=req_200, text=")]}'\n10\n[[[\"wrb.fr\",\"rLM1Ne\",\"{}\"]]]")
            
            http_client.post.side_effect = [
                httpx.HTTPStatusError("Unauth", request=req_401, response=resp_401),
                resp_200
            ]
            
            # Call RPC
            mock_client._call_rpc("rLM1Ne", [])
            
            # Verify refresh was called
            mock_refresh.assert_called_once()
            
            # Verify post was called twice
            assert http_client.post.call_count == 2

    def test_auto_retry_on_rpc_error_16(self, mock_client):
        """Test that client refreshes tokens and retries on RPC Error 16."""
        
        with patch.object(mock_client, '_get_client') as mock_get_client, \
             patch.object(mock_client, '_refresh_auth_tokens') as mock_refresh:
            
            http_client = MagicMock(spec=httpx.Client)
            mock_get_client.return_value = http_client
            
            req = httpx.Request("POST", "https://notebooklm.google.com/batchexecute")
            
            # 1. First response: RPC Error 16
            # Use 2 levels of nesting so parser wraps it to 3 (Chunk -> Items -> Item)
            error_json = json.dumps([["wrb.fr", "rLM1Ne", None, None, None, [16], "generic"]])
            resp1_text = f")]}}'\n{len(error_json)}\n{error_json}"
            
            # 2. Second response: Success
            success_json = json.dumps([["wrb.fr", "rLM1Ne", "{\"status\":\"ok\"}"]])
            resp2_text = f")]}}'\n{len(success_json)}\n{success_json}"
            
            http_client.post.side_effect = [
                httpx.Response(200, request=req, text=resp1_text),
                httpx.Response(200, request=req, text=resp2_text)
            ]
            
            # Execute
            result = mock_client._call_rpc("rLM1Ne", [])
            
            # Verify refresh called
            mock_refresh.assert_called_once()
            
            # Verify success result
            assert result == {"status": "ok"}

    def test_refresh_auth_tokens_success(self, mock_client):
        """Test that _refresh_auth_tokens extracts tokens correctly."""
        
        # Simplified HTML to minimize whitespace issues
        # api_client.py looks for "FdrFJe":"..." for the session ID
        html = '<html><script>WIZ_global_data = {"SNlM0e":"new_csrf_token", "FdrFJe":"123456789"};</script></html>'
        
        # Restore the original method for this test
        original_method = NotebookLMClient._refresh_auth_tokens
        
        with patch("httpx.Client") as MockClient:
            # Mock the context manager behavior
            client_instance = MockClient.return_value.__enter__.return_value
            
            req = httpx.Request("GET", "https://notebooklm.google.com/")
            client_instance.get.return_value = httpx.Response(200, request=req, text=html)
            
            # Call the real method bound to the instance
            original_method(mock_client)
            
            assert mock_client.csrf_token == "new_csrf_token"
            assert mock_client._session_id == "123456789"

    def test_refresh_auth_tokens_redirect_login(self, mock_client):
        """Test that redirect into login (expired cookies) raises ValueError."""
        
        original_method = NotebookLMClient._refresh_auth_tokens
        
        with patch("httpx.Client") as MockClient:
            client_instance = MockClient.return_value.__enter__.return_value
            
            # Mock redirect to accounts.google.com
            # Note: httpx follows redirects so the final response URL is the login page
            request = httpx.Request("GET", "https://accounts.google.com/ServiceLogin")
            resp = httpx.Response(200, request=request, text="login page")
            client_instance.get.return_value = resp
            
            with pytest.raises(ValueError, match="Authentication expired"):
                original_method(mock_client)

    def test_get_notebook_sources_extracts_url(self, mock_client):
        """Test that get_notebook_sources_with_types extracts URL from metadata."""
        
        # Structure based on my analysis of the real response:
        # Source structure: [[id], title, metadata, [null, 2]]
        # Metadata: [..., ..., ..., ..., type, ..., ..., [url]]
        
        # Metadata mock:
        # 0=doc_id, 1=len, 2=?, 3=?, 4=type(5=web), 5, 6, 7=[url]
        metadata = [
            None, 
            331, 
            [], 
            [], 
            5,  # type=web_page
            None, 
            1, 
            ["https://example.com/test_doc"]  # URL at pos 7
        ]
        
        # Source item
        source_item = [
            ["source_uuid"], 
            "Test Source Title", 
            metadata, 
            [None, 2]
        ]
        
        # Notebook response structure for get_notebook
        # The method calls get_notebook which calls _call_rpc
        # get_notebook returns: [["nb_title", [source_list], ...]]
        
        notebook_response = [[
            "Analysis Notebook",
            [source_item],
            "nb_uuid",
            # ... other fields ignored
        ]]
        
        with patch.object(mock_client, 'get_notebook', return_value=notebook_response):
            sources = mock_client.get_notebook_sources_with_types("nb_uuid")
            
            assert len(sources) == 1
            assert sources[0]["title"] == "Test Source Title"
            assert sources[0]["url"] == "https://example.com/test_doc"
            assert sources[0]["source_type_name"] == "web_page"

    def test_add_drive_source_uses_extended_timeout(self, mock_client):
        """Test that add_drive_source uses extended timeout (120s) for large files."""
        from notebooklm_mcp.api_client import SOURCE_ADD_TIMEOUT
        
        with patch.object(mock_client, '_get_client') as mock_get_client, \
             patch.object(mock_client, '_parse_response') as mock_parse, \
             patch.object(mock_client, '_extract_rpc_result') as mock_extract:
            
            http_client = MagicMock(spec=httpx.Client)
            mock_get_client.return_value = http_client
            
            req = httpx.Request("POST", "https://notebooklm.google.com/batchexecute")
            http_client.post.return_value = httpx.Response(200, request=req, text="...")
            
            mock_parse.return_value = []
            mock_extract.return_value = None
            
            mock_client.add_drive_source("nb_id", "doc_id", "Title")
            
            # Verify timeout=SOURCE_ADD_TIMEOUT was passed
            _, call_kwargs = http_client.post.call_args
            assert call_kwargs.get("timeout") == SOURCE_ADD_TIMEOUT
            assert SOURCE_ADD_TIMEOUT == 120.0  # Verify constant value

    def test_add_drive_source_timeout_returns_status(self, mock_client):
        """Test that add_drive_source returns timeout status on timeout exception."""
        from notebooklm_mcp.api_client import SOURCE_ADD_TIMEOUT
        
        with patch.object(mock_client, '_get_client') as mock_get_client:
            http_client = MagicMock(spec=httpx.Client)
            mock_get_client.return_value = http_client
            
            # Simulate timeout
            http_client.post.side_effect = httpx.TimeoutException("Read timed out")
            
            result = mock_client.add_drive_source("nb_id", "doc_id", "Title")
            
            # Verify result contains friendly message
            assert result["status"] == "timeout"
            assert f"timed out after {SOURCE_ADD_TIMEOUT}s" in result["message"].lower()
