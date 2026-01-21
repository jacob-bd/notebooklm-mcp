
import pytest
from unittest.mock import patch
from notebooklm_mcp.api_client import NotebookLMClient

@pytest.fixture
def mock_client():
    cookies = {"SID": "test_sid"}
    with patch.object(NotebookLMClient, '_refresh_auth_tokens'):
        client = NotebookLMClient(cookies=cookies, csrf_token="old_token", session_id="old_sid")
        return client

class TestNotebookLMClientFiltering:
    def test_poll_research_filtering(self, mock_client):
        """Test poll_research filters by task_id and handles status codes correctly."""
        
        # Mock API response for poll_research (RPC e3bVqc)
        # Structure: [[task_id, [query, [text, type], mode, [sources], status_code]], ...]
        
        task1 = ["task_uuid_1", [
            None, ["Query 1", 1], 1, [[], "Summary 1"], 2  # Status 2 = Completed
        ]]
        
        task2 = ["task_uuid_2", [
            None, ["Query 2", 1], 1, [[], "Summary 2"], 6  # Status 6 = Imported (also Completed)
        ]]
        
        task3 = ["task_uuid_3", [
            None, ["Query 3", 1], 1, [[], "Summary 3"], 1  # Status 1 = In Progress
        ]]
        
        # Outer wrapper: [[tasks], [timestamps]]
        mock_raw_result = [[task3, task2, task1]] # Tasks returned in some order
        
        with patch.object(mock_client, '_get_client') as mock_get_client, \
             patch.object(mock_client, '_extract_rpc_result', return_value=mock_raw_result):
            
            # Mock the http client to avoid network calls
            mock_http = mock_get_client.return_value
            mock_http.post.return_value.status_code = 200
            mock_http.post.return_value.text = ")]}'\n10\n[]" # Dummy content, ignored since we mock extract

            # 1. Test filtering by task_id (task 1)
            result1 = mock_client.poll_research("nb_id", target_task_id="task_uuid_1")
            assert result1 is not None
            assert result1["task_id"] == "task_uuid_1"
            assert result1["status"] == "completed" # Status 2 -> completed
            
            # 2. Test filtering by task_id (task 2 - status 6)
            result2 = mock_client.poll_research("nb_id", target_task_id="task_uuid_2")
            assert result2 is not None
            assert result2["task_id"] == "task_uuid_2"
            assert result2["status"] == "completed" # Status 6 -> completed (Fix)
            
            # 3. Test filtering by task_id (task 3 - in progress)
            result3 = mock_client.poll_research("nb_id", target_task_id="task_uuid_3")
            assert result3 is not None
            assert result3["task_id"] == "task_uuid_3"
            assert result3["status"] == "in_progress" # Status 1 -> in_progress
            
            # 4. Test unknown task_id
            result_unknown = mock_client.poll_research("nb_id", target_task_id="unknown_uuid")
            assert result_unknown is None
            
            # 5. Test default behavior (no task_id) - returns first valid task (task3 in our mock list)
            # Logic: "Return the most recent (first) task"
            result_default = mock_client.poll_research("nb_id")
            assert result_default is not None
            assert result_default["task_id"] == "task_uuid_3"
