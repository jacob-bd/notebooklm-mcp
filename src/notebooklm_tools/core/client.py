#!/usr/bin/env python3
"""NotebookLM MCP API client (notebooklm.google.com).

This module provides the full NotebookLMClient that inherits from BaseClient
and adds all domain-specific operations (notebooks, sources, studio, etc.).

Internal API. See CLAUDE.md for full documentation.
"""

import json
import logging
import re

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from . import constants
from .base import BaseClient, DEFAULT_TIMEOUT, SOURCE_ADD_TIMEOUT, logger


# Import utility functions from utils module
from .utils import (
    RPC_NAMES,
    _format_debug_json,
    _decode_request_body,
    _parse_url_params,
    parse_timestamp,
    extract_cookies_from_chrome_export,
)

# Import dataclasses from data_types module (re-exported for backward compatibility)
from .data_types import (
    ConversationTurn,
    Collaborator,
    ShareStatus,
    Notebook,
)


# Import exception classes from errors module (re-exported for backward compatibility)
from .errors import (
    NotebookLMError,
    ArtifactError,
    ArtifactNotReadyError,
    ArtifactParseError,
    ArtifactDownloadError,
    ArtifactNotFoundError,
    ClientAuthenticationError,
)

# Backward compatibility alias - code importing AuthenticationError from client.py
# will get the ClientAuthenticationError from errors.py
AuthenticationError = ClientAuthenticationError


# Ownership constants (from metadata position 0) - re-exported for backward compatibility
OWNERSHIP_MINE = constants.OWNERSHIP_MINE
OWNERSHIP_SHARED = constants.OWNERSHIP_SHARED


class NotebookLMClient(BaseClient):
    """Client for NotebookLM MCP internal API.
    
    This class extends BaseClient with all domain-specific operations:
    - Notebook management (list, create, rename, delete)
    - Source management (add, sync, delete)
    - Query/chat operations
    - Studio content (audio, video, reports, flashcards, etc.)
    - Research operations
    - Sharing and collaboration
    
    All HTTP/RPC infrastructure is provided by the BaseClient base class.
    """
    
    # Note: All RPC IDs, API constants, and infrastructure methods are inherited from BaseClient

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

    # =========================================================================
    # Sharing Operations
    # =========================================================================

    def get_share_status(self, notebook_id: str) -> ShareStatus:
        """Get current sharing settings and collaborators.

        Args:
            notebook_id: The notebook UUID

        Returns:
            ShareStatus with collaborators list, public access status, and link
        """
        params = [notebook_id, [2]]
        result = self._call_rpc(self.RPC_GET_SHARE_STATUS, params)

        # Parse collaborators from response
        # Response structure: [[collaborator_data...], access_info, ...]
        collaborators: list[Collaborator] = []
        is_public = False
        public_link = None

        if result and isinstance(result, list):
            # Parse collaborators (usually at position 0 or 1)
            for item in result:
                if isinstance(item, list):
                    for entry in item:
                        if isinstance(entry, list) and len(entry) >= 2:
                            # Collaborator format: [email, role_code, [], [name, avatar_url]]
                            email = entry[0] if entry[0] else None
                            if email and isinstance(email, str) and "@" in email:
                                role_code = entry[1] if len(entry) > 1 and isinstance(entry[1], int) else 3
                                role = constants.SHARE_ROLES.get_name(role_code)
                                # Name is in entry[3][0] if present
                                display_name = None
                                if len(entry) > 3 and isinstance(entry[3], list) and len(entry[3]) > 0:
                                    display_name = entry[3][0]
                                # Pending invites may have additional flag
                                is_pending = len(entry) > 4 and entry[4] is True
                                collaborators.append(Collaborator(
                                    email=email,
                                    role=role,
                                    is_pending=is_pending,
                                    display_name=str(display_name) if display_name else None,
                                ))


            # Check for public access flag
            # Usually indicated by access level code in the response
            # Position varies; look for [1] pattern indicating public
            for item in result:
                if isinstance(item, list) and len(item) >= 1:
                    if item[0] == 1:  # Public access indicator
                        is_public = True
                        break

        # Construct public link if public
        if is_public:
            public_link = f"https://notebooklm.google.com/notebook/{notebook_id}"

        access_level = "public" if is_public else "restricted"

        return ShareStatus(
            is_public=is_public,
            access_level=access_level,
            collaborators=collaborators,
            public_link=public_link,
        )

    def set_public_access(self, notebook_id: str, is_public: bool = True) -> str | None:
        """Toggle public link access for a notebook.

        Args:
            notebook_id: The notebook UUID
            is_public: True to enable public link, False to disable

        Returns:
            The public URL if enabled, None if disabled
        """
        # Payload: [[[notebook_id, null, [access_level], [notify, ""]]], 1, null, [2]]
        # access_level: 0 = restricted, 1 = public
        access_code = self.SHARE_ACCESS_PUBLIC if is_public else self.SHARE_ACCESS_RESTRICTED

        params = [
            [[notebook_id, None, [access_code], [0, ""]]],
            1,
            None,
            [2]
        ]

        result = self._call_rpc(self.RPC_SHARE_NOTEBOOK, params)

        if is_public:
            return f"https://notebooklm.google.com/notebook/{notebook_id}"
        return None

    def add_collaborator(
        self,
        notebook_id: str,
        email: str,
        role: str = "viewer",
        notify: bool = True,
        message: str = "",
    ) -> bool:
        """Add a collaborator to a notebook by email.

        Args:
            notebook_id: The notebook UUID
            email: Email address of the collaborator
            role: "viewer" or "editor" (default: viewer)
            notify: Send email notification (default: True)
            message: Optional welcome message

        Returns:
            True if successful
        """
        # Validate role
        role_code = constants.SHARE_ROLES.get_code(role)
        if role_code == constants.SHARE_ROLE_OWNER:
            raise ValueError("Cannot add collaborator as owner")

        # Payload: [[[notebook_id, [[email, null, role_code]], null, [notify_flag, message]]], 1, null, [2]]
        notify_flag = 0 if notify else 1  # 0 = notify, 1 = don't notify

        params = [
            [[notebook_id, [[email, None, role_code]], None, [notify_flag, message]]],
            1,
            None,
            [2]
        ]

        result = self._call_rpc(self.RPC_SHARE_NOTEBOOK, params)

        # Success if result is not None (no error thrown)
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

    def upload_file(
        self,
        notebook_id: str,
        file_path: str,
        headless: bool = False,
        profile_name: str = "default",
    ) -> bool:
        """Upload a local file to a notebook using Chrome automation.

        This method uses the same Chrome profile that was used during login,
        which already contains authentication cookies. Chrome is launched
        (visible by default) to perform the upload via browser automation.

        Args:
            notebook_id: The notebook ID to upload to
            file_path: Path to the local file to upload
            headless: Whether to use headless Chrome (default: False for better compatibility)
            profile_name: Name of the profile to use (default: "default")

        Returns:
            True if upload succeeded

        Raises:
            RuntimeError: If BrowserUploader dependencies are missing
            NLMError: If upload fails or authentication is required
        """
        try:
            from notebooklm_tools.core.uploader import BrowserUploader
            uploader = BrowserUploader(profile_name=profile_name, headless=headless)
            try:
                return uploader.upload_file(notebook_id, file_path)
            finally:
                uploader.close()
        except ImportError as e:
            raise RuntimeError(
                "BrowserUploader not available. Install with: pip install websocket-client"
            ) from e

    # =========================================================================
    # Download Operations
    # =========================================================================

    async def _download_url(
        self,
        url: str,
        output_path: str,
        progress_callback: Callable[[int, int], None] | None = None,
        chunk_size: int = 65536
    ) -> str:
        """Download content from a URL to a local file with streaming support.

        Features:
        - Streams file in chunks to minimize memory usage
        - Optional progress callback for UI integration
        - Per-chunk timeouts to detect stalled connections
        - Temp file usage to prevent corrupted partial downloads
        - Authentication error detection

        Args:
            url: The URL to download
            output_path: The local path to save the file
            progress_callback: Optional callback(bytes_downloaded, total_bytes)
            chunk_size: Size of chunks to read (default 64KB)

        Returns:
            The output path

        Raises:
            ArtifactDownloadError: If download fails
            AuthenticationError: If auth redirect detected
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Use temp file to prevent corrupted partial downloads
        temp_file = output_file.with_suffix(output_file.suffix + ".tmp")

        # Build headers with auth cookies
        base_headers = getattr(self, "_PAGE_FETCH_HEADERS", {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        headers = {**base_headers, "Referer": "https://notebooklm.google.com/"}

        # Use httpx.Cookies for proper cross-domain redirect handling
        cookies = self._get_httpx_cookies()

        # Per-chunk timeouts: 10s connect, 30s per chunk read/write
        # This allows large files to download without timeout while detecting stalls
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0)

        try:
            async with httpx.AsyncClient(
                cookies=cookies,
                headers=headers,
                follow_redirects=True,
                timeout=timeout
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()

                    # Get total size if available
                    content_length = response.headers.get("content-length")
                    total_bytes = int(content_length) if content_length else 0

                    # Check for auth redirect before starting download
                    content_type = response.headers.get("content-type", "").lower()
                    if "text/html" in content_type:
                        # Read first chunk to check for login page
                        first_chunk = b""
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            first_chunk = chunk
                            break

                        if b"<!doctype html>" in first_chunk.lower() or b"sign in" in first_chunk.lower():
                            raise AuthenticationError(
                                "Download failed: Redirected to login page. "
                                "Run 'nlm login' to refresh credentials."
                            )

                        # Not an auth error - write first chunk and continue
                        with open(temp_file, "wb") as f:
                            f.write(first_chunk)
                            bytes_downloaded = len(first_chunk)

                            if progress_callback:
                                progress_callback(bytes_downloaded, total_bytes)

                            # Continue streaming rest of file
                            async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                                f.write(chunk)
                                bytes_downloaded += len(chunk)

                                if progress_callback:
                                    progress_callback(bytes_downloaded, total_bytes)
                    else:
                        # Binary content - stream directly
                        bytes_downloaded = 0
                        with open(temp_file, "wb") as f:
                            async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                                f.write(chunk)
                                bytes_downloaded += len(chunk)

                                if progress_callback:
                                    progress_callback(bytes_downloaded, total_bytes)

            # Move temp file to final location only on success
            temp_file.rename(output_file)
            return str(output_file)

        except httpx.HTTPError as e:
            # Clean up temp file on failure
            if temp_file.exists():
                temp_file.unlink()
            raise ArtifactDownloadError(
                "file",
                details=f"HTTP error downloading from {url[:50]}...: {e}"
            ) from e
        except Exception as e:
            # Clean up temp file on failure
            if temp_file.exists():
                temp_file.unlink()
            raise ArtifactDownloadError(
                "file",
                details=f"Failed to download from {url[:50]}...: {str(e)}"
            ) from e

    def _list_raw(self, notebook_id: str) -> list[Any]:
        """Get raw artifact list for parsing download URLs."""
        # This reuses the list_notebooks parsing logic but returns the raw list
        # needed for extracting deeply nested metadata
        # RPC: wXbhsf is list_notebooks. But we need artifacts within a notebook.
        # Actually, artifacts are usually fetched via list_notebooks (which returns everything)
        # OR via specific RPCs.
        # Let's check how list_notebooks gets data.
        # It calls RPC_LIST_NOTEBOOKS.
        # But wait, audio/video are "studio artifacts".
        # We should use poll_studio_status to get the raw list of artifacts.
        
        # Poll params: [[2], notebook_id, 'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"']
        params = [[2], notebook_id, 'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"']
        body = self._build_request_body(self.RPC_POLL_STUDIO, params)
        url = self._build_url(self.RPC_POLL_STUDIO, f"/notebook/{notebook_id}")
        
        client = self._get_client()
        response = client.post(url, content=body)
        response.raise_for_status()
        
        parsed = self._parse_response(response.text)
        result = self._extract_rpc_result(parsed, self.RPC_POLL_STUDIO)
        
        if result and isinstance(result, list) and len(result) > 0:
             # Response is an array of artifacts, possibly wrapped
             return result[0] if isinstance(result[0], list) else result
        return []

    async def download_audio(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> str:
        """Download an Audio Overview to a file.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the audio file (MP4/MP3).
            artifact_id: Specific artifact ID, or uses first completed audio.
            progress_callback: Optional callback(bytes_downloaded, total_bytes).

        Returns:
            The output path.
        """
        artifacts = self._list_raw(notebook_id)

        # Filter for completed audio (Type 1, Status 3)
        # Type 1 = STUDIO_TYPE_AUDIO (constants.py)
        # Status 3 = COMPLETED
        candidates = []
        for a in artifacts:
            if isinstance(a, list) and len(a) > 4:
                if a[2] == self.STUDIO_TYPE_AUDIO and a[4] == 3: # 3 is COMPLETED
                    candidates.append(a)

        if not candidates:
            raise ArtifactNotReadyError("audio")

        target = None
        if artifact_id:
            target = next((a for a in candidates if a[0] == artifact_id), None)
            if not target:
                raise ArtifactNotReadyError("audio", artifact_id)
        else:
            target = candidates[0]

        # Extract URL from metadata[6][5]
        try:
            metadata = target[6]
            if not isinstance(metadata, list) or len(metadata) <= 5:
                raise ArtifactParseError("audio", details="Invalid audio metadata structure")

            media_list = metadata[5]
            if not isinstance(media_list, list) or len(media_list) == 0:
                raise ArtifactParseError("audio", details="No media URLs found in metadata")

            # Look for audio/mp4 mime type
            url = None
            for item in media_list:
                if isinstance(item, list) and len(item) > 2 and item[2] == "audio/mp4":
                    url = item[0]
                    break

            # Fallback to first URL if no audio/mp4 found
            if not url and len(media_list) > 0 and isinstance(media_list[0], list):
                url = media_list[0][0]

            if not url:
                raise ArtifactDownloadError("audio", details="No download URL found")

            return await self._download_url(url, output_path, progress_callback)

        except (IndexError, TypeError, AttributeError) as e:
            raise ArtifactParseError("audio", details=str(e)) from e

    async def download_video(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> str:
        """Download a Video Overview to a file.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the video file (MP4).
            artifact_id: Specific artifact ID, or uses first completed video.
            progress_callback: Optional callback(bytes_downloaded, total_bytes).

        Returns:
            The output path.
        """
        artifacts = self._list_raw(notebook_id)

        # Filter for completed video (Type 3, Status 3)
        candidates = []
        for a in artifacts:
            if isinstance(a, list) and len(a) > 4:
                 if a[2] == self.STUDIO_TYPE_VIDEO and a[4] == 3:
                     candidates.append(a)

        if not candidates:
            raise ArtifactNotReadyError("video")

        target = None
        if artifact_id:
            target = next((a for a in candidates if a[0] == artifact_id), None)
            if not target:
                raise ArtifactNotReadyError("video", artifact_id)
        else:
            target = candidates[0]

        # Extract URL from metadata[8]
        try:
            metadata = target[8]
            if not isinstance(metadata, list):
                raise ArtifactParseError("video", details="Invalid metadata structure")

            # First, find the media_list (nested list containing URLs)
            media_list = None
            for item in metadata:
                if (isinstance(item, list) and len(item) > 0 and
                    isinstance(item[0], list) and len(item[0]) > 0 and
                    isinstance(item[0][0], str) and item[0][0].startswith("http")):
                    media_list = item
                    break

            if not media_list:
                raise ArtifactDownloadError("video", details="No media URLs found in metadata")

            # Look for video/mp4 with optimal encoding (item[1] == 4 indicates priority)
            url = None
            for item in media_list:
                if isinstance(item, list) and len(item) > 2 and item[2] == "video/mp4":
                    url = item[0]
                    # Prefer URLs with priority flag (item[1] == 4)
                    if len(item) > 1 and item[1] == 4:
                        break

            # Fallback to first URL if no video/mp4 found
            if not url and len(media_list) > 0 and isinstance(media_list[0], list):
                url = media_list[0][0]

            if not url:
                raise ArtifactDownloadError("video", details="No download URL found")

            return await self._download_url(url, output_path, progress_callback)

        except (IndexError, TypeError, AttributeError) as e:
            raise ArtifactParseError("video", details=str(e)) from e

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


    def get_studio_status(self, notebook_id: str) -> list[dict]:
        """Alias for poll_studio_status (used by CLI)."""
        return self.poll_studio_status(notebook_id)

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



    async def download_infographic(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> str:
        """Download an Infographic to a file.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the PNG file.
            artifact_id: Specific artifact ID, or uses first completed infographic.
            progress_callback: Optional callback(bytes_downloaded, total_bytes).

        Returns:
            The output path.
        """
        artifacts = self._list_raw(notebook_id)

        # Filter for completed infographics (Type 7, Status 3)
        candidates = []
        for a in artifacts:
            if isinstance(a, list) and len(a) > 5:
                if a[2] == self.STUDIO_TYPE_INFOGRAPHIC and a[4] == 3:
                    candidates.append(a)

        if not candidates:
            raise ArtifactNotReadyError("infographic")

        target = None
        if artifact_id:
            target = next((a for a in candidates if a[0] == artifact_id), None)
            if not target:
                raise ArtifactNotReadyError("infographic", artifact_id)
        else:
            target = candidates[0]

        # Extract URL from metadata[5][0][0]
        try:
            metadata = target[5]
            if not isinstance(metadata, list) or len(metadata) == 0:
                raise ArtifactParseError("infographic", details="Invalid metadata structure")

            media_list = metadata[0]
            if not isinstance(media_list, list) or len(media_list) == 0:
                raise ArtifactParseError("infographic", details="No media URLs found in metadata")

            url = media_list[0][0] if isinstance(media_list[0], list) else None
            if not url:
                raise ArtifactDownloadError("infographic", details="No download URL found")

            return await self._download_url(url, output_path, progress_callback)

        except (IndexError, TypeError, AttributeError) as e:
            raise ArtifactParseError("infographic", details=str(e)) from e


    async def download_slide_deck(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> str:
        """Download a Slide Deck to a file (PDF).

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the PDF file.
            artifact_id: Specific artifact ID, or uses first completed slide deck.
            progress_callback: Optional callback(bytes_downloaded, total_bytes).

        Returns:
            The output path.
        """
        artifacts = self._list_raw(notebook_id)

        # Filter for completed slide decks (Type 8, Status 3)
        candidates = []
        for a in artifacts:
            if isinstance(a, list) and len(a) > 5:
                if a[2] == self.STUDIO_TYPE_SLIDE_DECK and a[4] == 3:
                    candidates.append(a)

        if not candidates:
            raise ArtifactNotReadyError("slide_deck")

        target = None
        if artifact_id:
            target = next((a for a in candidates if a[0] == artifact_id), None)
            if not target:
                raise ArtifactNotReadyError("slide_deck", artifact_id)
        else:
            target = candidates[0]

        # Extract PDF URL from metadata[12][0][1] (contribution.usercontent.google.com)
        try:
            metadata = target[12]
            if not isinstance(metadata, list) or len(metadata) == 0:
                raise ArtifactParseError("slide_deck", details="Invalid metadata structure")

            media_list = metadata[0]
            if not isinstance(media_list, list) or len(media_list) < 2:
                raise ArtifactParseError("slide_deck", details="No media URLs found in metadata")

            pdf_url = media_list[1]
            if not pdf_url or not isinstance(pdf_url, str):
                raise ArtifactDownloadError("slide_deck", details="No download URL found")

            return await self._download_url(pdf_url, output_path, progress_callback)

        except (IndexError, TypeError, AttributeError) as e:
            raise ArtifactParseError("slide_deck", details=str(e)) from e

        
    def download_report(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
    ) -> str:
        """Download a report artifact as markdown.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the markdown file.
            artifact_id: Specific artifact ID, or uses first completed report.

        Returns:
            The output path where the file was saved.
        """
        artifacts = self._list_raw(notebook_id)

        # Filter for completed reports (Type 6, Status 3)
        candidates = []
        for a in artifacts:
            if isinstance(a, list) and len(a) > 7:
                 if a[2] == self.STUDIO_TYPE_REPORT and a[4] == 3:
                     candidates.append(a)
        
        if not candidates:
             raise ArtifactNotReadyError("report")
        
        target = None
        if artifact_id:
            target = next((a for a in candidates if a[0] == artifact_id), None)
            if not target:
                raise ArtifactNotReadyError("report", artifact_id)
        else:
            target = candidates[0]

        try:
            # Report content is in index 7
            content_wrapper = target[7]
            markdown_content = ""
            
            if isinstance(content_wrapper, list) and len(content_wrapper) > 0:
                markdown_content = content_wrapper[0]
            elif isinstance(content_wrapper, str):
                markdown_content = content_wrapper
            
            if not isinstance(markdown_content, str):
                raise ArtifactParseError("report", details="Invalid content structure")

            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(markdown_content, encoding="utf-8")
            return str(output)

        except (IndexError, TypeError, AttributeError) as e:
            raise ArtifactParseError("report", details=str(e)) from e

    def download_mind_map(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
    ) -> str:
        """Download a mind map as JSON.

        Mind maps are stored in the notes system, not the regular artifacts list.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the JSON file.
            artifact_id: Specific mind map ID (note ID), or uses first available.

        Returns:
            The output path where the file was saved.
        """
        # Mind maps are retrieved via list_mind_maps RPC
        params = [notebook_id]
        result = self._call_rpc(self.RPC_LIST_MIND_MAPS, params, f"/notebook/{notebook_id}")
        
        mind_maps = []
        if result and isinstance(result, list) and len(result) > 0:
            if isinstance(result[0], list):
                 mind_maps = result[0]
        
        if not mind_maps:
            raise ArtifactNotReadyError("mind_map")

        target = None
        if artifact_id:
            target = next((mm for mm in mind_maps if isinstance(mm, list) and mm[0] == artifact_id), None)
            if not target:
                raise ArtifactNotFoundError(artifact_id, artifact_type="mind_map")
        else:
            target = mind_maps[0]

        try:
            # Mind map JSON is stringified in target[1][1]
            if len(target) > 1 and isinstance(target[1], list) and len(target[1]) > 1:
                 json_string = target[1][1]
                 if isinstance(json_string, str):
                     json_data = json.loads(json_string)
                     
                     output = Path(output_path)
                     output.parent.mkdir(parents=True, exist_ok=True)
                     output.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")
                     return str(output)
            
            raise ArtifactParseError("mind_map", details="Invalid structure")

        except (IndexError, TypeError, json.JSONDecodeError, AttributeError) as e:
            raise ArtifactParseError("mind_map", details=str(e)) from e

    @staticmethod
    def _extract_cell_text(cell: Any, _depth: int = 0) -> str:
        """Recursively extract text from a nested data table cell structure.

        Data table cells have deeply nested arrays with position markers (integers)
        and text content (strings). This function traverses the structure and
        concatenates all text fragments found.

        Features:
        - Depth tracking to prevent infinite recursion (max 100 levels)
        - Handles None values gracefully
        - Strips whitespace from extracted text
        - Type validation at each level

        Args:
            cell: The cell data structure (can be str, int, list, or other)
            _depth: Internal recursion depth counter for safety

        Returns:
            Extracted text string, stripped of leading/trailing whitespace
        """
        # Safety: prevent infinite recursion
        if _depth > 100:
            return ""

        # Handle different types
        if cell is None:
            return ""
        if isinstance(cell, str):
            return cell.strip()
        if isinstance(cell, (int, float)):
            return ""  # Position markers are numeric
        if isinstance(cell, list):
            # Recursively extract from all list items
            parts = []
            for item in cell:
                text = NotebookLMClient._extract_cell_text(item, _depth + 1)
                if text:
                    parts.append(text)
            return " ".join(parts) if parts else ""

        # Unknown type - convert to string as fallback
        return str(cell).strip()

    def _parse_data_table(
        self,
        raw_data: list,
        validate_columns: bool = True
    ) -> tuple[list[str], list[list[str]]]:
        """Parse rich-text data table into headers and rows.

        Features:
        - Validates structure at each navigation step with clear error messages
        - Optional column count validation across all rows
        - Handles missing/empty cells gracefully
        - Provides detailed error context for debugging

        Structure: raw_data[0][0][0][0][4][2] contains the rows array where:
        - [0][0][0][0] navigates through wrapper layers
        - [4] contains the table content section [type, flags, rows_array]
        - [2] is the actual rows array

        Each row: [start_pos, end_pos, [cell1, cell2, ...]]
        Each cell: deeply nested with position markers mixed with text

        Args:
            raw_data: The raw data table metadata from artifact[18]
            validate_columns: If True, ensures all rows have same column count as headers

        Returns:
            Tuple of (headers, rows) where:
            - headers: List of column names
            - rows: List of data rows (each row is a list matching header length)

        Raises:
            ArtifactParseError: With detailed context if parsing fails
        """
        # Validate and navigate structure with clear error messages
        try:
            if not isinstance(raw_data, list) or len(raw_data) == 0:
                raise ArtifactParseError(
                    "data_table",
                    details="Invalid raw_data: expected non-empty list at artifact[18]"
                )

            # Navigate: raw_data[0]
            layer1 = raw_data[0]
            if not isinstance(layer1, list) or len(layer1) == 0:
                raise ArtifactParseError(
                    "data_table",
                    details="Invalid structure at raw_data[0]: expected non-empty list"
                )

            # Navigate: [0][0]
            layer2 = layer1[0]
            if not isinstance(layer2, list) or len(layer2) == 0:
                raise ArtifactParseError(
                    "data_table",
                    details="Invalid structure at raw_data[0][0]: expected non-empty list"
                )

            # Navigate: [0][0][0]
            layer3 = layer2[0]
            if not isinstance(layer3, list) or len(layer3) == 0:
                raise ArtifactParseError(
                    "data_table",
                    details="Invalid structure at raw_data[0][0][0]: expected non-empty list"
                )

            # Navigate: [0][0][0][0]
            layer4 = layer3[0]
            if not isinstance(layer4, list) or len(layer4) < 5:
                raise ArtifactParseError(
                    "data_table",
                    details=f"Invalid structure at raw_data[0][0][0][0]: expected list with at least 5 elements, got {len(layer4) if isinstance(layer4, list) else type(layer4).__name__}"
                )

            # Navigate: [0][0][0][0][4] - table content section
            table_section = layer4[4]
            if not isinstance(table_section, list) or len(table_section) < 3:
                raise ArtifactParseError(
                    "data_table",
                    details=f"Invalid table section at raw_data[0][0][0][0][4]: expected list with at least 3 elements, got {len(table_section) if isinstance(table_section, list) else type(table_section).__name__}"
                )

            # Navigate: [0][0][0][0][4][2] - rows array
            rows_array = table_section[2]
            if not isinstance(rows_array, list):
                raise ArtifactParseError(
                    "data_table",
                    details=f"Invalid rows array at raw_data[0][0][0][0][4][2]: expected list, got {type(rows_array).__name__}"
                )

            if not rows_array:
                raise ArtifactParseError(
                    "data_table",
                    details="Empty rows array - data table contains no data"
                )

        except IndexError as e:
            raise ArtifactParseError(
                "data_table",
                details=f"Structure navigation failed - table may be corrupted or in unexpected format: {e}"
            ) from e

        # Extract headers and rows
        headers: list[str] = []
        rows: list[list[str]] = []
        skipped_rows = 0

        for i, row_section in enumerate(rows_array):
            # Validate row format: [start_pos, end_pos, [cell_array]]
            if not isinstance(row_section, list):
                skipped_rows += 1
                continue

            if len(row_section) < 3:
                skipped_rows += 1
                continue

            cell_array = row_section[2]
            if not isinstance(cell_array, list):
                skipped_rows += 1
                continue

            # Extract text from each cell
            row_values = [self._extract_cell_text(cell) for cell in cell_array]

            # First row is headers
            if i == 0:
                headers = row_values
                if not headers or all(not h for h in headers):
                    raise ArtifactParseError(
                        "data_table",
                        details="First row (headers) is empty - table must have column headers"
                    )
            else:
                # Validate column count if requested
                if validate_columns and len(row_values) != len(headers):
                    # Pad or truncate to match header length
                    if len(row_values) < len(headers):
                        row_values.extend([""] * (len(headers) - len(row_values)))
                    else:
                        row_values = row_values[:len(headers)]

                rows.append(row_values)

        # Final validation
        if not headers:
            raise ArtifactParseError(
                "data_table",
                details="Failed to extract headers - first row may be malformed"
            )

        if not rows:
            raise ArtifactParseError(
                "data_table",
                details=f"No data rows extracted (skipped {skipped_rows} malformed rows)"
            )

        return headers, rows

    def download_data_table(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
    ) -> str:
        """Download a data table as CSV.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the CSV file.
            artifact_id: Specific artifact ID, or uses first completed data table.

        Returns:
            The output path where the file was saved.
        """
        import csv

        artifacts = self._list_raw(notebook_id)

        # Filter for completed data tables (Type 9, Status 3)
        candidates = []
        for a in artifacts:
            if isinstance(a, list) and len(a) > 18:
                if a[2] == self.STUDIO_TYPE_DATA_TABLE and a[4] == 3:
                    candidates.append(a)

        if not candidates:
            raise ArtifactNotReadyError("data_table")

        target = None
        if artifact_id:
            target = next((a for a in candidates if a[0] == artifact_id), None)
            if not target:
                raise ArtifactNotReadyError("data_table", artifact_id)
        else:
            target = candidates[0]

        try:
            # Data is at index 18
            raw_data = target[18]
            headers, rows = self._parse_data_table(raw_data)

            # Write to CSV
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)

            with open(output, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerows(rows)

            return str(output)

        except (IndexError, TypeError, AttributeError) as e:
            raise ArtifactParseError("data_table", details=str(e)) from e
        
    def _get_artifact_content(self, notebook_id: str, artifact_id: str) -> str | None:
        """Fetch artifact HTML content for quiz/flashcard types.

        Args:
            notebook_id: The notebook ID.
            artifact_id: The artifact ID.

        Returns:
            HTML content string, or None if not found.

        Raises:
            ArtifactDownloadError: If API response structure is unexpected.
        """
        result = self._call_rpc(
            self.RPC_GET_INTERACTIVE_HTML,
            [artifact_id],
            f"/notebook/{notebook_id}"
        )

        if not result:
            logger.debug(f"Empty response for artifact {artifact_id}")
            return None

        # Response structure: result[0] contains artifact data
        # HTML content is at result[0][9][0]
        # This is reverse-engineered from the API and may change
        try:
            if not isinstance(result, list) or len(result) == 0:
                logger.warning(f"Unexpected response type for {artifact_id}: {type(result)}")
                return None

            data = result[0]
            if not isinstance(data, list):
                logger.warning(f"Unexpected artifact data type: {type(data)}")
                return None

            if len(data) <= 9:
                logger.warning(f"Artifact data too short (len={len(data)}), expected index 9")
                return None

            html_container = data[9]
            if not html_container or not isinstance(html_container, list) or len(html_container) == 0:
                logger.debug(f"No HTML content in artifact {artifact_id}")
                return None

            return html_container[0]

        except (IndexError, TypeError) as e:
            logger.error(f"Error parsing artifact content for {artifact_id}: {e}")
            # Log truncated response for debugging
            result_preview = str(result)[:500] if result else "None"
            logger.debug(f"Response preview: {result_preview}...")
            raise ArtifactDownloadError(
                "interactive",
                details=f"Unexpected API response structure: {e}"
            ) from e

    def _extract_app_data(self, html_content: str) -> dict:
        """Extract JSON app data from interactive HTML.

        Quiz and flashcard HTML contains embedded JSON in a data-app-data
        attribute with HTML-encoded content (&quot; for quotes).

        Tries multiple extraction patterns for robustness:
        1. data-app-data attribute (primary)
        2. <script id="application-data"> tag (fallback)
        3. Other common patterns

        Args:
            html_content: The HTML content string.

        Returns:
            Parsed JSON data as dict.

        Raises:
            ArtifactParseError: If data cannot be extracted or parsed.
        """
        import html as html_module
        import re

        # Pattern 1: data-app-data attribute (most common)
        # Handle both single and multiline with greedy matching
        match = re.search(r'data-app-data="([^"]*(?:\\"[^"]*)*)"', html_content, re.DOTALL)
        if match:
            encoded_json = match.group(1)
            decoded_json = html_module.unescape(encoded_json)

            try:
                data = json.loads(decoded_json)
                logger.debug("Extracted app data using data-app-data attribute")
                return data
            except json.JSONDecodeError as e:
                # Log the error but try other patterns
                logger.debug(f"Failed to parse data-app-data JSON: {e}")
                logger.debug(f"JSON preview: {decoded_json[:200]}...")

        # Pattern 2: <script id="application-data"> tag
        match = re.search(
            r'<script[^>]+id=["\']application-data["\'][^>]*>(.*?)</script>',
            html_content,
            re.DOTALL
        )
        if match:
            try:
                data = json.loads(match.group(1))
                logger.debug("Extracted app data using script tag")
                return data
            except json.JSONDecodeError as e:
                logger.debug(f"Failed to parse script tag JSON: {e}")

        # Pattern 3: data-state or data-config attributes (additional fallback)
        for attr in ['data-state', 'data-config', 'data-initial-state']:
            match = re.search(rf'{attr}="([^"]*(?:\\"[^"]*)*)"', html_content, re.DOTALL)
            if match:
                encoded_json = match.group(1)
                decoded_json = html_module.unescape(encoded_json)
                try:
                    data = json.loads(decoded_json)
                    logger.debug(f"Extracted app data using {attr} attribute")
                    return data
                except json.JSONDecodeError:
                    continue

        # No patterns matched - provide detailed error
        html_preview = html_content[:500] if html_content else "Empty"
        logger.error(f"Failed to extract app data. HTML preview: {html_preview}...")

        raise ArtifactParseError(
            "interactive",
            details=(
                "Could not extract JSON data from HTML. "
                "Tried: data-app-data, script#application-data, data-state, data-config"
            )
        )

    @staticmethod
    def _format_quiz_markdown(title: str, questions: list[dict]) -> str:
        """Format quiz as markdown.

        Args:
            title: Quiz title.
            questions: List of question dicts with 'question', 'answerOptions', 'hint'.

        Returns:
            Formatted markdown string.
        """
        lines = [f"# {title}", ""]

        for i, q in enumerate(questions, 1):
            lines.append(f"## Question {i}")
            lines.append(q.get("question", ""))
            lines.append("")

            for opt in q.get("answerOptions", []):
                marker = "[x]" if opt.get("isCorrect") else "[ ]"
                lines.append(f"- {marker} {opt.get('text', '')}")

            if q.get("hint"):
                lines.append("")
                lines.append(f"**Hint:** {q['hint']}")

            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _format_flashcards_markdown(title: str, cards: list[dict]) -> str:
        """Format flashcards as markdown.

        Args:
            title: Flashcard deck title.
            cards: List of card dicts with 'f' (front) and 'b' (back).

        Returns:
            Formatted markdown string.
        """
        lines = [f"# {title}", ""]

        for i, card in enumerate(cards, 1):
            front = card.get("f", "")
            back = card.get("b", "")

            lines.append(f"## Card {i}")
            lines.append("")
            lines.append(f"**Front:** {front}")
            lines.append("")
            lines.append(f"**Back:** {back}")
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def _format_interactive_content(
        self,
        app_data: dict,
        title: str,
        output_format: str,
        html_content: str,
        is_quiz: bool,
    ) -> str:
        """Format quiz or flashcard content for output.

        Args:
            app_data: Parsed JSON data from HTML.
            title: Artifact title.
            output_format: Output format - json, markdown, or html.
            html_content: Original HTML content.
            is_quiz: True for quiz, False for flashcards.

        Returns:
            Formatted content string.
        """
        if output_format == "html":
            return html_content

        if is_quiz:
            questions = app_data.get("quiz", [])
            if output_format == "markdown":
                return self._format_quiz_markdown(title, questions)
            return json.dumps({"title": title, "questions": questions}, indent=2)

        # Flashcards
        cards = app_data.get("flashcards", [])
        if output_format == "markdown":
            return self._format_flashcards_markdown(title, cards)

        # Normalize JSON format: {"f": "...", "b": "..."} -> {"front": "...", "back": "..."}
        normalized = [{"front": c.get("f", ""), "back": c.get("b", "")} for c in cards]
        return json.dumps({"title": title, "cards": normalized}, indent=2)

    async def _download_interactive_artifact(
        self,
        notebook_id: str,
        output_path: str,
        artifact_type: str,
        is_quiz: bool,
        artifact_id: str | None = None,
        output_format: str = "json",
    ) -> str:
        """Shared implementation for downloading quiz/flashcard artifacts.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the file.
            artifact_type: Human-readable type for error messages ("quiz" or "flashcards").
            is_quiz: True for quiz, False for flashcards.
            artifact_id: Specific artifact ID, or uses first completed artifact.
            output_format: Output format - json, markdown, or html.

        Returns:
            The output path where the file was saved.

        Raises:
            ValueError: If invalid output_format.
            ArtifactNotReadyError: If no completed artifact found.
            ArtifactParseError: If content parsing fails.
            ArtifactDownloadError: If content fetch fails.
        """
        # Validate format
        valid_formats = ("json", "markdown", "html")
        if output_format not in valid_formats:
            raise ValueError(
                f"Invalid output_format: {output_format!r}. "
                f"Use one of: {', '.join(valid_formats)}"
            )

        # Get all artifacts and filter for completed interactive artifacts
        artifacts = self._list_raw(notebook_id)

        # Type 4 (STUDIO_TYPE_FLASHCARDS) covers both quizzes and flashcards
        # Status 3 = completed
        candidates = [
            a for a in artifacts
            if isinstance(a, list) and len(a) > 4
            and a[2] == self.STUDIO_TYPE_FLASHCARDS
            and a[4] == 3
        ]

        if not candidates:
            raise ArtifactNotReadyError(artifact_type)

        # Select artifact by ID or use most recent
        if artifact_id:
            target = next((a for a in candidates if a[0] == artifact_id), None)
            if not target:
                raise ArtifactNotReadyError(artifact_type, artifact_id)
        else:
            target = candidates[0]  # Most recent

        # Fetch HTML content
        html_content = self._get_artifact_content(notebook_id, target[0])
        if not html_content:
            raise ArtifactDownloadError(
                artifact_type,
                details="Failed to fetch HTML content from API"
            )

        # Extract and parse embedded JSON
        try:
            app_data = self._extract_app_data(html_content)
        except ArtifactParseError:
            raise  # Re-raise as-is
        except (ValueError, json.JSONDecodeError) as e:
            raise ArtifactParseError(artifact_type, details=str(e)) from e

        # Get title from artifact metadata
        default_title = f"Untitled {artifact_type.title()}"
        title = target[1] if len(target) > 1 and target[1] else default_title

        # Format content
        content = self._format_interactive_content(
            app_data, title, output_format, html_content, is_quiz
        )

        # Write to file
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")

        logger.info(f"Downloaded {artifact_type} to {output} ({output_format} format)")
        return str(output)

    async def download_quiz(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        output_format: str = "json",
    ) -> str:
        """Download quiz artifact.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the file.
            artifact_id: Specific artifact ID, or uses first completed quiz.
            output_format: Output format - json, markdown, or html (default: json).

        Returns:
            The output path where the file was saved.

        Raises:
            ValueError: If invalid output_format.
            ArtifactNotReadyError: If no completed quiz found.
            ArtifactParseError: If content parsing fails.
        """
        return await self._download_interactive_artifact(
            notebook_id=notebook_id,
            output_path=output_path,
            artifact_type="quiz",
            is_quiz=True,
            artifact_id=artifact_id,
            output_format=output_format,
        )

    async def download_flashcards(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        output_format: str = "json",
    ) -> str:
        """Download flashcard deck artifact.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the file.
            artifact_id: Specific artifact ID, or uses first completed flashcard deck.
            output_format: Output format - json, markdown, or html (default: json).

        Returns:
            The output path where the file was saved.

        Raises:
            ValueError: If invalid output_format.
            ArtifactNotReadyError: If no completed flashcards found.
            ArtifactParseError: If content parsing fails.
        """
        return await self._download_interactive_artifact(
            notebook_id=notebook_id,
            output_path=output_path,
            artifact_type="flashcards",
            is_quiz=False,
            artifact_id=artifact_id,
            output_format=output_format,
        )

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

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
