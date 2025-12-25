"""NotebookLM Consumer MCP Server."""

from typing import Any

from fastmcp import FastMCP

from .api_client import ConsumerNotebookLMClient, extract_cookies_from_chrome_export, parse_timestamp

# Initialize MCP server
mcp = FastMCP(
    name="notebooklm-consumer",
    instructions="""NotebookLM Consumer MCP Server.

**IMPORTANT: This is for Consumer NotebookLM (notebooklm.google.com), NOT Enterprise.**

Consumer NotebookLM is the free/personal tier at notebooklm.google.com.
Enterprise NotebookLM is at vertexaisearch.cloud.google.com (different system, different notebooks).

This MCP uses reverse-engineered internal APIs and requires browser cookie authentication.

## Authentication (SIMPLIFIED!)

You only need to extract COOKIES - the CSRF token and session ID are now auto-extracted!

Using Chrome DevTools MCP:
1. Navigate to notebooklm.google.com
2. Get cookies from any network request: get_network_request(reqid=<any_request>)
3. Call save_auth_tokens(cookies=<cookie_header>)

That's it! Cookies are stable for weeks. The CSRF token and session ID are automatically
extracted from the page when needed.

## Available Tools

- notebook_list: List all notebooks
- notebook_create: Create a new notebook
- notebook_get: Get notebook details with sources
- notebook_rename: Rename a notebook
- chat_configure: Configure chat goal/style and response length
- notebook_delete: Delete a notebook (REQUIRES user confirmation)
- notebook_add_url: Add URL/YouTube as source
- notebook_add_text: Add pasted text as source
- notebook_add_drive: Add Google Drive document as source
- notebook_query: Ask questions about notebook sources
- source_list_drive: List all sources with types and check Drive freshness
- source_sync_drive: Sync stale Drive sources (REQUIRES user confirmation)
- research_start: Start Web or Drive research to discover sources
- research_status: Check research progress and get results
- research_import: Import discovered sources into notebook
- audio_overview_create: Generate audio overviews (podcasts) from sources
- video_overview_create: Generate video overviews from sources
- studio_status: Check audio/video generation status
- studio_delete: Delete audio/video overviews (REQUIRES user confirmation)
- save_auth_tokens: Save cookies for authentication

## Research Feature

To discover and import sources automatically:
1. Call research_start(query, source, mode) - starts research
   - source: "web" or "drive"
   - mode: "fast" (~10 sources, 30s) or "deep" (~40 sources, 3-5min, web only)
2. Call research_status(notebook_id) - poll until status="completed"
3. Call research_import(notebook_id, task_id) - import all or selected sources

## Syncing Drive Sources

To sync outdated Google Drive sources:
1. Call source_list_drive(notebook_id) to see all sources and their freshness
2. Show the user which Drive sources are stale (needs_sync=True)
3. Ask the user to confirm which sources to sync
4. Call source_sync_drive(source_ids, confirm=True) with the confirmed source IDs

## Chat Configuration

To customize how the AI responds to queries:
- chat_configure(notebook_id, goal, custom_prompt, response_length)

Goals: "default" (research/brainstorming), "learning_guide" (educational), "custom" (with custom_prompt)
Response lengths: "default", "longer", "shorter"

Example: chat_configure(notebook_id, goal="custom", custom_prompt="Respond as a PhD researcher", response_length="longer")

## Studio Features (Audio/Video Overviews)

To generate audio or video overviews:
1. Call audio_overview_create or video_overview_create with notebook_id and options (confirm=False)
2. Show the user the proposed settings and ask for confirmation
3. Call again with confirm=True to start generation
4. Generation takes several minutes - the tool returns immediately with artifact_id
5. Call studio_status(notebook_id) to check progress and get URLs when complete

Audio formats: deep_dive, brief, critique, debate
Audio lengths: short, default, long
Video formats: explainer, brief
Video styles: auto_select, classic, whiteboard, kawaii, anime, watercolor, retro_print, heritage, paper_craft

## IMPORTANT: Confirmation Required Operations

For audio_overview_create, video_overview_create, notebook_delete, studio_delete, and source_sync_drive, you MUST:
1. Warn the user about the operation
2. Ask the user explicitly to confirm before proceeding
3. Only set confirm=True after the user approves

## Known Limitations

- Cookies expire after several weeks - re-extract when API calls fail with auth errors
- API is undocumented and may change without notice
- Rate limits apply (~50 queries/day on free tier)
""",
)

# Global state
_client: ConsumerNotebookLMClient | None = None


def get_client() -> ConsumerNotebookLMClient:
    """Get or create the API client.

    Tries environment variables first, falls back to cached tokens from auth CLI.
    """
    global _client
    if _client is None:
        import os

        from .auth import load_cached_tokens

        cookie_header = os.environ.get("NOTEBOOKLM_COOKIES", "")
        csrf_token = os.environ.get("NOTEBOOKLM_CSRF_TOKEN", "")
        session_id = os.environ.get("NOTEBOOKLM_SESSION_ID", "")

        if cookie_header:
            # Use environment variables
            cookies = extract_cookies_from_chrome_export(cookie_header)
        else:
            # Try cached tokens from auth CLI
            cached = load_cached_tokens()
            if cached:
                cookies = cached.cookies
                csrf_token = csrf_token or cached.csrf_token
                session_id = session_id or cached.session_id
            else:
                raise ValueError(
                    "No authentication found. Either:\n"
                    "1. Run 'notebooklm-consumer-auth' to authenticate via Chrome, or\n"
                    "2. Set NOTEBOOKLM_COOKIES environment variable manually"
                )

        _client = ConsumerNotebookLMClient(
            cookies=cookies,
            csrf_token=csrf_token,
            session_id=session_id,
        )
    return _client


@mcp.tool()
def notebook_list(max_results: int = 100) -> dict[str, Any]:
    """List all notebooks.

    Args:
        max_results: Maximum number of notebooks to return (default: 100)

    Returns:
        Dictionary with status and list of notebooks
    """
    try:
        client = get_client()
        notebooks = client.list_notebooks()

        # Count owned vs shared notebooks
        owned_count = sum(1 for nb in notebooks if nb.is_owned)
        shared_count = len(notebooks) - owned_count
        
        # Count notebooks shared by me (owned + is_shared=True)
        shared_by_me_count = sum(1 for nb in notebooks if nb.is_owned and nb.is_shared)

        return {
            "status": "success",
            "count": len(notebooks),
            "owned_count": owned_count,
            "shared_count": shared_count,
            "shared_by_me_count": shared_by_me_count,
            "notebooks": [
                {
                    "id": nb.id,
                    "title": nb.title,
                    "source_count": nb.source_count,
                    "url": nb.url,
                    "ownership": nb.ownership,
                    "is_shared": nb.is_shared,
                    "created_at": nb.created_at,
                    "modified_at": nb.modified_at,
                }
                for nb in notebooks[:max_results]
            ],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_create(title: str = "") -> dict[str, Any]:
    """Create a new notebook.

    Args:
        title: Optional title for the notebook

    Returns:
        Dictionary with status and notebook details
    """
    try:
        client = get_client()
        notebook = client.create_notebook(title=title)

        if notebook:
            return {
                "status": "success",
                "notebook": {
                    "id": notebook.id,
                    "title": notebook.title,
                    "url": notebook.url,
                },
            }
        return {"status": "error", "error": "Failed to create notebook"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_get(notebook_id: str) -> dict[str, Any]:
    """Get details of a notebook.

    Args:
        notebook_id: The notebook UUID

    Returns:
        Dictionary with status and notebook details including timestamps
    """
    try:
        client = get_client()
        result = client.get_notebook(notebook_id)

        # Extract timestamps from metadata if available
        # Result structure: [title, sources, id, emoji, null, metadata, ...]
        # metadata[5] = modified_at, metadata[8] = created_at
        created_at = None
        modified_at = None
        if result and isinstance(result, list) and len(result) > 5:
            metadata = result[5]
            if isinstance(metadata, list):
                if len(metadata) > 5:
                    modified_at = parse_timestamp(metadata[5])
                if len(metadata) > 8:
                    created_at = parse_timestamp(metadata[8])

        return {
            "status": "success",
            "notebook": result,
            "created_at": created_at,
            "modified_at": modified_at,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_add_url(notebook_id: str, url: str) -> dict[str, Any]:
    """Add a URL (website or YouTube) as a source to a notebook.

    Args:
        notebook_id: The notebook UUID
        url: The URL to add

    Returns:
        Dictionary with status and source details
    """
    try:
        client = get_client()
        result = client.add_url_source(notebook_id, url=url)

        if result:
            return {
                "status": "success",
                "source": result,
            }
        return {"status": "error", "error": "Failed to add URL source"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_add_text(
    notebook_id: str,
    text: str,
    title: str = "Pasted Text",
) -> dict[str, Any]:
    """Add pasted text as a source to a notebook.

    Args:
        notebook_id: The notebook UUID
        text: The text content to add
        title: Optional title for the source

    Returns:
        Dictionary with status and source details
    """
    try:
        client = get_client()
        result = client.add_text_source(notebook_id, text=text, title=title)

        if result:
            return {
                "status": "success",
                "source": result,
            }
        return {"status": "error", "error": "Failed to add text source"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_add_drive(
    notebook_id: str,
    document_id: str,
    title: str,
    doc_type: str = "doc",
) -> dict[str, Any]:
    """Add a Google Drive document as a source to a notebook.

    Args:
        notebook_id: The notebook UUID
        document_id: The Google Drive document ID (from the URL)
        title: The document title to display
        doc_type: Type of document - "doc", "slides", "sheets", or "pdf"

    Returns:
        Dictionary with status and source details
    """
    try:
        mime_types = {
            "doc": "application/vnd.google-apps.document",
            "docs": "application/vnd.google-apps.document",
            "slides": "application/vnd.google-apps.presentation",
            "sheets": "application/vnd.google-apps.spreadsheet",
            "pdf": "application/pdf",
        }

        mime_type = mime_types.get(doc_type.lower())
        if not mime_type:
            return {
                "status": "error",
                "error": f"Unknown doc_type '{doc_type}'. Use 'doc', 'slides', 'sheets', or 'pdf'.",
            }

        client = get_client()
        result = client.add_drive_source(
            notebook_id,
            document_id=document_id,
            title=title,
            mime_type=mime_type,
        )

        if result:
            return {
                "status": "success",
                "source": result,
            }
        return {"status": "error", "error": "Failed to add Drive source"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_query(
    notebook_id: str,
    query: str,
    source_ids: list[str] | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Ask a question about the notebook sources.

    Args:
        notebook_id: The notebook UUID
        query: The question to ask
        source_ids: Optional list of source IDs to query (default: all sources)
        conversation_id: Optional conversation ID for follow-up questions

    Returns:
        Dictionary with answer, citations, and conversation_id for follow-ups
    """
    try:
        client = get_client()
        result = client.query(
            notebook_id,
            query_text=query,
            source_ids=source_ids,
            conversation_id=conversation_id,
        )

        if result:
            return {
                "status": "success",
                "answer": result.get("answer", ""),
                "conversation_id": result.get("conversation_id"),
            }
        return {"status": "error", "error": "Failed to query notebook"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_delete(
    notebook_id: str,
    confirm: bool = False,
) -> dict[str, Any]:
    """Delete a notebook permanently.

    WARNING: This action is IRREVERSIBLE. The notebook and all its sources,
    notes, and generated content will be permanently deleted.

    IMPORTANT: You MUST ask the user for confirmation before calling this tool.
    The confirm parameter must be explicitly set to True.

    Args:
        notebook_id: The notebook UUID to delete
        confirm: Must be True to proceed. Set to False by default as a safety measure.

    Returns:
        Dictionary with status
    """
    if not confirm:
        return {
            "status": "error",
            "error": "Deletion not confirmed. You must ask the user to confirm "
                     "before deleting. Set confirm=True only after user approval.",
            "warning": "This action is IRREVERSIBLE. The notebook and all its "
                       "sources will be permanently deleted.",
        }

    try:
        client = get_client()
        result = client.delete_notebook(notebook_id)

        if result:
            return {
                "status": "success",
                "message": f"Notebook {notebook_id} has been permanently deleted.",
            }
        return {"status": "error", "error": "Failed to delete notebook"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def notebook_rename(
    notebook_id: str,
    new_title: str,
) -> dict[str, Any]:
    """Rename a notebook.

    Args:
        notebook_id: The notebook UUID
        new_title: The new title for the notebook

    Returns:
        Dictionary with status and updated notebook info
    """
    try:
        client = get_client()
        result = client.rename_notebook(notebook_id, new_title)

        if result:
            return {
                "status": "success",
                "notebook": {
                    "id": notebook_id,
                    "title": new_title,
                },
            }
        return {"status": "error", "error": "Failed to rename notebook"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def chat_configure(
    notebook_id: str,
    goal: str = "default",
    custom_prompt: str | None = None,
    response_length: str = "default",
) -> dict[str, Any]:
    """Configure the chat settings for a notebook.

    This sets the conversational goal/style and response length for the notebook's
    AI chat. These settings affect how the AI responds to queries.

    Args:
        notebook_id: The notebook UUID
        goal: The conversational goal/style. One of:
            - "default": General purpose research and brainstorming
            - "learning_guide": Educational focus, helps grasp new concepts
            - "custom": Use a custom prompt (requires custom_prompt)
        custom_prompt: Custom prompt text when goal="custom" (up to 10000 chars).
            Examples: "respond at a PhD student level", "pretend to be a game host"
        response_length: Response length preference. One of:
            - "default": Balanced response length
            - "longer": Verbose, more detailed responses
            - "shorter": Concise, brief responses

    Returns:
        Dictionary with status and updated settings
    """
    try:
        client = get_client()
        result = client.configure_chat(
            notebook_id=notebook_id,
            goal=goal,
            custom_prompt=custom_prompt,
            response_length=response_length,
        )
        return result
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def source_list_drive(notebook_id: str) -> dict[str, Any]:
    """List all sources in a notebook with their types and freshness status.

    This tool identifies which sources can be synced with Google Drive:
    - Type 2 (drive): User-added Google Drive documents
    - Type 1 (gemini_notes): Gemini-generated meeting notes (also stored in Drive)

    Use this before source_sync_drive to see which sources are stale.

    Args:
        notebook_id: The notebook UUID

    Returns:
        Dictionary with sources grouped by syncability and freshness status
    """
    try:
        client = get_client()
        sources = client.get_notebook_sources_with_types(notebook_id)

        # Separate sources by syncability
        syncable_sources = []
        other_sources = []

        for src in sources:
            if src.get("can_sync"):
                # Check freshness for syncable sources (Drive docs and Gemini Notes)
                is_fresh = client.check_source_freshness(src["id"])
                src["is_fresh"] = is_fresh
                src["needs_sync"] = is_fresh is False
                syncable_sources.append(src)
            else:
                other_sources.append(src)

        # Count stale sources
        stale_count = sum(1 for s in syncable_sources if s.get("needs_sync"))

        return {
            "status": "success",
            "notebook_id": notebook_id,
            "summary": {
                "total_sources": len(sources),
                "syncable_sources": len(syncable_sources),
                "stale_sources": stale_count,
                "other_sources": len(other_sources),
            },
            "syncable_sources": syncable_sources,
            "other_sources": [
                {"id": s["id"], "title": s["title"], "type": s["source_type_name"]}
                for s in other_sources
            ],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def source_sync_drive(
    source_ids: list[str],
    confirm: bool = False,
) -> dict[str, Any]:
    """Sync specified Google Drive sources with their latest content.

    IMPORTANT: Before calling this tool, you MUST:
    1. Call source_list_drive to identify stale sources
    2. Show the user which sources will be synced
    3. Ask the user to confirm they want to sync
    4. Only set confirm=True after user approval

    Args:
        source_ids: List of source UUIDs to sync (get these from source_list_drive)
        confirm: Must be True to proceed. Set to False by default as a safety measure.

    Returns:
        Dictionary with sync results for each source
    """
    if not confirm:
        return {
            "status": "error",
            "error": "Sync not confirmed. You must ask the user to confirm "
                     "before syncing. Set confirm=True only after user approval.",
            "hint": "First call source_list_drive to show stale sources, "
                    "then ask user to confirm before syncing.",
        }

    if not source_ids:
        return {
            "status": "error",
            "error": "No source_ids provided. Use source_list_drive to get source IDs.",
        }

    try:
        client = get_client()
        results = []
        synced_count = 0
        failed_count = 0

        for source_id in source_ids:
            try:
                result = client.sync_drive_source(source_id)
                if result:
                    results.append({
                        "source_id": source_id,
                        "status": "synced",
                        "title": result.get("title"),
                    })
                    synced_count += 1
                else:
                    results.append({
                        "source_id": source_id,
                        "status": "failed",
                        "error": "Sync returned no result",
                    })
                    failed_count += 1
            except Exception as e:
                results.append({
                    "source_id": source_id,
                    "status": "failed",
                    "error": str(e),
                })
                failed_count += 1

        return {
            "status": "success" if failed_count == 0 else "partial",
            "summary": {
                "total": len(source_ids),
                "synced": synced_count,
                "failed": failed_count,
            },
            "results": results,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def research_start(
    query: str,
    source: str = "web",
    mode: str = "fast",
    notebook_id: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Start a research session to discover sources.

    This tool searches the web or your Google Drive for relevant sources
    based on a query. Use research_status to check progress and get results.

    Args:
        query: The search query (e.g., "kubernetes best practices 2025")
        source: Where to search - "web" (public internet) or "drive" (your Google Drive)
        mode: Research depth - "fast" (~10 sources, 30 seconds) or "deep" (~40 sources, 3-5 minutes)
              Note: "deep" mode only works with source="web"
        notebook_id: Optional existing notebook ID. If not provided, a new notebook is created.
        title: Optional title for new notebook. Used only if notebook_id is not provided.
               If neither notebook_id nor title is provided, notebook is titled "Research: <query>"

    Returns:
        Dictionary with task_id, notebook_id, and research info
    """
    try:
        client = get_client()

        # Validate mode + source combination early
        if mode.lower() == "deep" and source.lower() == "drive":
            return {
                "status": "error",
                "error": "Deep Research only supports Web sources. Use mode='fast' for Drive.",
            }

        # Create notebook if needed
        if not notebook_id:
            notebook_title = title or f"Research: {query[:50]}"
            notebook = client.create_notebook(title=notebook_title)
            if not notebook:
                return {"status": "error", "error": "Failed to create notebook"}
            notebook_id = notebook.id
            created_notebook = True
        else:
            created_notebook = False

        # Start research
        result = client.start_research(
            notebook_id=notebook_id,
            query=query,
            source=source,
            mode=mode,
        )

        if result:
            response = {
                "status": "success",
                "task_id": result["task_id"],
                "notebook_id": notebook_id,
                "notebook_url": f"https://notebooklm.google.com/notebook/{notebook_id}",
                "query": query,
                "source": result["source"],
                "mode": result["mode"],
                "created_notebook": created_notebook,
            }

            # Add helpful message based on mode
            if result["mode"] == "deep":
                response["message"] = (
                    "Deep Research started. This takes 3-5 minutes. "
                    "Call research_status to check progress."
                )
            else:
                response["message"] = (
                    "Fast Research started. This takes about 30 seconds. "
                    "Call research_status to check progress."
                )

            return response

        return {"status": "error", "error": "Failed to start research"}
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def research_status(
    notebook_id: str,
    poll_interval: int = 30,
    max_wait: int = 300,
) -> dict[str, Any]:
    """Check research progress and get results.

    Call this after research_start to check if research is complete
    and to get the list of discovered sources.

    This tool has built-in polling with sleep to reduce API token usage.
    By default, it will poll every 30 seconds for up to 5 minutes.

    Args:
        notebook_id: The notebook UUID (from research_start response)
        poll_interval: Seconds to wait between polls (default: 30)
        max_wait: Maximum seconds to wait for completion (default: 300 = 5 minutes).
                  Set to 0 for a single immediate poll without waiting.

    Returns:
        Dictionary with status, sources list, and summary when complete
    """
    import time

    try:
        client = get_client()
        start_time = time.time()
        polls = 0

        while True:
            polls += 1
            result = client.poll_research(notebook_id)

            if not result:
                return {"status": "error", "error": "Failed to poll research status"}

            # If completed or no research found, return immediately
            if result.get("status") in ("completed", "no_research"):
                result["polls_made"] = polls
                result["wait_time_seconds"] = round(time.time() - start_time, 1)
                return {
                    "status": "success",
                    "research": result,
                }

            # Check if we should stop waiting
            elapsed = time.time() - start_time
            if max_wait == 0 or elapsed >= max_wait:
                result["polls_made"] = polls
                result["wait_time_seconds"] = round(elapsed, 1)
                result["message"] = (
                    f"Research still in progress after {round(elapsed, 1)}s. "
                    f"Call research_status again to continue waiting."
                )
                return {
                    "status": "success",
                    "research": result,
                }

            # Wait before next poll
            time.sleep(poll_interval)

    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def research_import(
    notebook_id: str,
    task_id: str,
    source_indices: list[int] | None = None,
) -> dict[str, Any]:
    """Import discovered sources into the notebook.

    Call this after research_status shows status="completed" to import
    the discovered sources.

    Args:
        notebook_id: The notebook UUID
        task_id: The research task ID (from research_start or research_status)
        source_indices: Optional list of source indices to import (0-based).
                       If not provided, imports ALL discovered sources.

    Returns:
        Dictionary with imported source count and IDs
    """
    try:
        client = get_client()

        # First, get the current research results to get source details
        poll_result = client.poll_research(notebook_id)

        if not poll_result or poll_result.get("status") == "no_research":
            return {
                "status": "error",
                "error": "No research found for this notebook. Run research_start first.",
            }

        if poll_result.get("status") != "completed":
            return {
                "status": "error",
                "error": f"Research is still in progress (status: {poll_result.get('status')}). "
                         "Wait for completion before importing.",
            }

        # Get sources from poll result
        all_sources = poll_result.get("sources", [])

        if not all_sources:
            return {
                "status": "error",
                "error": "No sources found in research results.",
            }

        # Filter sources by indices if specified
        if source_indices is not None:
            sources_to_import = []
            invalid_indices = []
            for idx in source_indices:
                if 0 <= idx < len(all_sources):
                    sources_to_import.append(all_sources[idx])
                else:
                    invalid_indices.append(idx)

            if invalid_indices:
                return {
                    "status": "error",
                    "error": f"Invalid source indices: {invalid_indices}. "
                             f"Valid range is 0-{len(all_sources)-1}.",
                }
        else:
            sources_to_import = all_sources

        # Import the sources
        imported = client.import_research_sources(
            notebook_id=notebook_id,
            task_id=task_id,
            sources=sources_to_import,
        )

        return {
            "status": "success",
            "imported_count": len(imported),
            "total_available": len(all_sources),
            "sources": imported,
            "notebook_url": f"https://notebooklm.google.com/notebook/{notebook_id}",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def audio_overview_create(
    notebook_id: str,
    source_ids: list[str] | None = None,
    format: str = "deep_dive",
    length: str = "default",
    language: str = "en",
    focus_prompt: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """Generate an audio overview (podcast) from notebook sources.

    Generation takes several minutes. Use studio_status to check progress.

    IMPORTANT: Before calling this tool, you MUST:
    1. Show the user the settings (format, length, language, focus_prompt)
    2. Ask the user to confirm they want to proceed
    3. Only set confirm=True after user approval

    Args:
        notebook_id: The notebook UUID
        source_ids: Optional list of source IDs to include (default: all sources)
        format: Audio format - "deep_dive" (default), "brief", "critique", or "debate"
            - deep_dive: Lively conversation between two hosts, unpacking topics
            - brief: Bite-sized overview to grasp core ideas quickly
            - critique: Expert review offering constructive feedback
            - debate: Thoughtful debate illuminating different perspectives
        length: Length - "short", "default", or "long"
        language: BCP-47 language code (e.g., "en", "es", "fr", "de", "ja")
        focus_prompt: Optional text describing what AI should focus on
        confirm: Must be True to proceed. Show settings and get user confirmation first.

    Returns:
        Dictionary with artifact_id and status. Call studio_status to check progress.
    """
    if not confirm:
        return {
            "status": "pending_confirmation",
            "message": "Please confirm these settings before creating the audio overview:",
            "settings": {
                "notebook_id": notebook_id,
                "format": format,
                "length": length,
                "language": language,
                "focus_prompt": focus_prompt or "(none)",
                "source_ids": source_ids or "all sources",
            },
            "note": "Set confirm=True after user approves these settings.",
        }

    try:
        client = get_client()

        # Map format string to code
        format_codes = {
            "deep_dive": 1,
            "brief": 2,
            "critique": 3,
            "debate": 4,
        }
        format_code = format_codes.get(format.lower())
        if format_code is None:
            return {
                "status": "error",
                "error": f"Unknown format '{format}'. Use: deep_dive, brief, critique, or debate.",
            }

        # Map length string to code
        length_codes = {
            "short": 1,
            "default": 2,
            "long": 3,
        }
        length_code = length_codes.get(length.lower())
        if length_code is None:
            return {
                "status": "error",
                "error": f"Unknown length '{length}'. Use: short, default, or long.",
            }

        # Get source IDs if not provided
        if source_ids is None:
            sources = client.get_notebook_sources_with_types(notebook_id)
            source_ids = [s["id"] for s in sources if s["id"]]

        if not source_ids:
            return {
                "status": "error",
                "error": "No sources found in notebook. Add sources before creating audio overview.",
            }

        result = client.create_audio_overview(
            notebook_id=notebook_id,
            source_ids=source_ids,
            format_code=format_code,
            length_code=length_code,
            language=language,
            focus_prompt=focus_prompt,
        )

        if result:
            return {
                "status": "success",
                "artifact_id": result["artifact_id"],
                "type": "audio",
                "format": result["format"],
                "length": result["length"],
                "language": result["language"],
                "generation_status": result["status"],
                "message": "Audio generation started. Use studio_status to check progress.",
                "notebook_url": f"https://notebooklm.google.com/notebook/{notebook_id}",
            }
        return {"status": "error", "error": "Failed to create audio overview"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def video_overview_create(
    notebook_id: str,
    source_ids: list[str] | None = None,
    format: str = "explainer",
    visual_style: str = "auto_select",
    language: str = "en",
    focus_prompt: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """Generate a video overview from notebook sources.

    Generation takes several minutes. Use studio_status to check progress.

    IMPORTANT: Before calling this tool, you MUST:
    1. Show the user the settings (format, visual_style, language, focus_prompt)
    2. Ask the user to confirm they want to proceed
    3. Only set confirm=True after user approval

    Args:
        notebook_id: The notebook UUID
        source_ids: Optional list of source IDs to include (default: all sources)
        format: Video format - "explainer" (default) or "brief"
            - explainer: Structured, comprehensive overview
            - brief: Bite-sized overview of core ideas
        visual_style: Visual style for the video:
            - auto_select (default): AI chooses best style
            - classic: Traditional educational style
            - whiteboard: Hand-drawn whiteboard style
            - kawaii: Cute, colorful style
            - anime: Japanese animation style
            - watercolor: Artistic watercolor style
            - retro_print: Vintage print style
            - heritage: Classic historical style
            - paper_craft: Papercut/origami style
        language: BCP-47 language code (e.g., "en", "es", "fr", "de", "ja")
        focus_prompt: Optional text describing what AI should focus on
        confirm: Must be True to proceed. Show settings and get user confirmation first.

    Returns:
        Dictionary with artifact_id and status. Call studio_status to check progress.
    """
    if not confirm:
        return {
            "status": "pending_confirmation",
            "message": "Please confirm these settings before creating the video overview:",
            "settings": {
                "notebook_id": notebook_id,
                "format": format,
                "visual_style": visual_style,
                "language": language,
                "focus_prompt": focus_prompt or "(none)",
                "source_ids": source_ids or "all sources",
            },
            "note": "Set confirm=True after user approves these settings.",
        }

    try:
        client = get_client()

        # Map format string to code
        format_codes = {
            "explainer": 1,
            "brief": 2,
        }
        format_code = format_codes.get(format.lower())
        if format_code is None:
            return {
                "status": "error",
                "error": f"Unknown format '{format}'. Use: explainer or brief.",
            }

        # Map style string to code
        style_codes = {
            "auto_select": 1,
            "custom": 2,
            "classic": 3,
            "whiteboard": 4,
            "kawaii": 5,
            "anime": 6,
            "watercolor": 7,
            "retro_print": 8,
            "heritage": 9,
            "paper_craft": 10,
        }
        style_code = style_codes.get(visual_style.lower())
        if style_code is None:
            valid_styles = ", ".join(style_codes.keys())
            return {
                "status": "error",
                "error": f"Unknown visual_style '{visual_style}'. Use: {valid_styles}",
            }

        # Get source IDs if not provided
        if source_ids is None:
            sources = client.get_notebook_sources_with_types(notebook_id)
            source_ids = [s["id"] for s in sources if s["id"]]

        if not source_ids:
            return {
                "status": "error",
                "error": "No sources found in notebook. Add sources before creating video overview.",
            }

        result = client.create_video_overview(
            notebook_id=notebook_id,
            source_ids=source_ids,
            format_code=format_code,
            visual_style_code=style_code,
            language=language,
            focus_prompt=focus_prompt,
        )

        if result:
            return {
                "status": "success",
                "artifact_id": result["artifact_id"],
                "type": "video",
                "format": result["format"],
                "visual_style": result["visual_style"],
                "language": result["language"],
                "generation_status": result["status"],
                "message": "Video generation started. Use studio_status to check progress.",
                "notebook_url": f"https://notebooklm.google.com/notebook/{notebook_id}",
            }
        return {"status": "error", "error": "Failed to create video overview"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def studio_status(notebook_id: str) -> dict[str, Any]:
    """Check the status of studio content generation.

    Call this after creating any studio content (audio, video, infographic,
    slide deck) to check if generation is complete and get URLs.

    Args:
        notebook_id: The notebook UUID

    Returns:
        Dictionary with list of artifacts and their status/URLs.
        Each artifact includes type-specific URL fields:
        - audio_url: For audio overviews
        - video_url: For video overviews
        - infographic_url: For infographics
        - slide_deck_url: For slide decks
    """
    try:
        client = get_client()
        artifacts = client.poll_studio_status(notebook_id)

        # Separate by status
        completed = [a for a in artifacts if a["status"] == "completed"]
        in_progress = [a for a in artifacts if a["status"] == "in_progress"]

        return {
            "status": "success",
            "notebook_id": notebook_id,
            "summary": {
                "total": len(artifacts),
                "completed": len(completed),
                "in_progress": len(in_progress),
            },
            "artifacts": artifacts,
            "notebook_url": f"https://notebooklm.google.com/notebook/{notebook_id}",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def studio_delete(
    notebook_id: str,
    artifact_id: str,
    confirm: bool = False,
) -> dict[str, Any]:
    """Delete a studio artifact (audio, video, infographic, or slide deck).

    WARNING: This action is IRREVERSIBLE. The artifact will be permanently deleted.

    IMPORTANT: Before calling this tool, you MUST:
    1. Call studio_status to list available artifacts
    2. Show the user which artifact will be deleted (title, type)
    3. Ask the user to confirm they want to delete it
    4. Only set confirm=True after user approval

    Args:
        notebook_id: The notebook UUID (for reference/validation)
        artifact_id: The artifact UUID to delete (from studio_status)
        confirm: Must be True to proceed. Set to False by default as a safety measure.

    Returns:
        Dictionary with deletion status
    """
    if not confirm:
        return {
            "status": "error",
            "error": "Deletion not confirmed. You must ask the user to confirm "
                     "before deleting. Set confirm=True only after user approval.",
            "warning": "This action is IRREVERSIBLE. The artifact will be permanently deleted.",
            "hint": "First call studio_status to list artifacts with their IDs and titles.",
        }

    try:
        client = get_client()
        result = client.delete_studio_artifact(artifact_id)

        if result:
            return {
                "status": "success",
                "message": f"Artifact {artifact_id} has been permanently deleted.",
                "notebook_id": notebook_id,
            }
        return {"status": "error", "error": "Failed to delete artifact"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def infographic_create(
    notebook_id: str,
    source_ids: list[str] | None = None,
    orientation: str = "landscape",
    detail_level: str = "standard",
    language: str = "en",
    focus_prompt: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """Generate an infographic from notebook sources.

    Generation takes a few minutes. Use studio_status to check progress.

    IMPORTANT: Before calling this tool, you MUST:
    1. Show the user the settings (orientation, detail_level, language, focus_prompt)
    2. Ask the user to confirm they want to proceed
    3. Only set confirm=True after user approval

    Args:
        notebook_id: The notebook UUID
        source_ids: Optional list of source IDs to include (default: all sources)
        orientation: Infographic orientation:
            - landscape (default): Wide format (16:9)
            - portrait: Tall format (9:16)
            - square: Square format (1:1)
        detail_level: Level of detail:
            - concise: Minimal text, key points only
            - standard (default): Balanced detail
            - detailed: Comprehensive with more information (BETA)
        language: BCP-47 language code (e.g., "en", "es", "fr", "de", "ja")
        focus_prompt: Optional text describing what AI should focus on
        confirm: Must be True to proceed. Show settings and get user confirmation first.

    Returns:
        Dictionary with artifact_id and status. Call studio_status to check progress.
    """
    if not confirm:
        return {
            "status": "pending_confirmation",
            "message": "Please confirm these settings before creating the infographic:",
            "settings": {
                "notebook_id": notebook_id,
                "orientation": orientation,
                "detail_level": detail_level,
                "language": language,
                "focus_prompt": focus_prompt or "(none)",
                "source_ids": source_ids or "all sources",
            },
            "note": "Set confirm=True after user approves these settings.",
        }

    try:
        client = get_client()

        # Map orientation string to code
        orientation_codes = {
            "landscape": 1,
            "portrait": 2,
            "square": 3,
        }
        orientation_code = orientation_codes.get(orientation.lower())
        if orientation_code is None:
            return {
                "status": "error",
                "error": f"Unknown orientation '{orientation}'. Use: landscape, portrait, or square.",
            }

        # Map detail_level string to code
        detail_codes = {
            "concise": 1,
            "standard": 2,
            "detailed": 3,
        }
        detail_code = detail_codes.get(detail_level.lower())
        if detail_code is None:
            return {
                "status": "error",
                "error": f"Unknown detail_level '{detail_level}'. Use: concise, standard, or detailed.",
            }

        # Get source IDs if not provided
        if source_ids is None:
            sources = client.get_notebook_sources_with_types(notebook_id)
            source_ids = [s["id"] for s in sources if s["id"]]

        if not source_ids:
            return {
                "status": "error",
                "error": "No sources found in notebook. Add sources before creating infographic.",
            }

        result = client.create_infographic(
            notebook_id=notebook_id,
            source_ids=source_ids,
            orientation_code=orientation_code,
            detail_level_code=detail_code,
            language=language,
            focus_prompt=focus_prompt,
        )

        if result:
            return {
                "status": "success",
                "artifact_id": result["artifact_id"],
                "type": "infographic",
                "orientation": result["orientation"],
                "detail_level": result["detail_level"],
                "language": result["language"],
                "generation_status": result["status"],
                "message": "Infographic generation started. Use studio_status to check progress.",
                "notebook_url": f"https://notebooklm.google.com/notebook/{notebook_id}",
            }
        return {"status": "error", "error": "Failed to create infographic"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def slide_deck_create(
    notebook_id: str,
    source_ids: list[str] | None = None,
    format: str = "detailed_deck",
    length: str = "default",
    language: str = "en",
    focus_prompt: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """Generate a slide deck from notebook sources.

    Generation takes a few minutes. Use studio_status to check progress.

    IMPORTANT: Before calling this tool, you MUST:
    1. Show the user the settings (format, length, language, focus_prompt)
    2. Ask the user to confirm they want to proceed
    3. Only set confirm=True after user approval

    Args:
        notebook_id: The notebook UUID
        source_ids: Optional list of source IDs to include (default: all sources)
        format: Slide deck format:
            - detailed_deck (default): Comprehensive deck with full text and details
            - presenter_slides: Clean visual slides with key talking points
        length: Deck length:
            - short: Fewer slides, key points only
            - default: Standard length
        language: BCP-47 language code (e.g., "en", "es", "fr", "de", "ja")
        focus_prompt: Optional text describing what AI should focus on
        confirm: Must be True to proceed. Show settings and get user confirmation first.

    Returns:
        Dictionary with artifact_id and status. Call studio_status to check progress.
    """
    if not confirm:
        return {
            "status": "pending_confirmation",
            "message": "Please confirm these settings before creating the slide deck:",
            "settings": {
                "notebook_id": notebook_id,
                "format": format,
                "length": length,
                "language": language,
                "focus_prompt": focus_prompt or "(none)",
                "source_ids": source_ids or "all sources",
            },
            "note": "Set confirm=True after user approves these settings.",
        }

    try:
        client = get_client()

        # Map format string to code
        format_codes = {
            "detailed_deck": 1,
            "presenter_slides": 2,
        }
        format_code = format_codes.get(format.lower())
        if format_code is None:
            return {
                "status": "error",
                "error": f"Unknown format '{format}'. Use: detailed_deck or presenter_slides.",
            }

        # Map length string to code
        length_codes = {
            "short": 1,
            "default": 3,
        }
        length_code = length_codes.get(length.lower())
        if length_code is None:
            return {
                "status": "error",
                "error": f"Unknown length '{length}'. Use: short or default.",
            }

        # Get source IDs if not provided
        if source_ids is None:
            sources = client.get_notebook_sources_with_types(notebook_id)
            source_ids = [s["id"] for s in sources if s["id"]]

        if not source_ids:
            return {
                "status": "error",
                "error": "No sources found in notebook. Add sources before creating slide deck.",
            }

        result = client.create_slide_deck(
            notebook_id=notebook_id,
            source_ids=source_ids,
            format_code=format_code,
            length_code=length_code,
            language=language,
            focus_prompt=focus_prompt,
        )

        if result:
            return {
                "status": "success",
                "artifact_id": result["artifact_id"],
                "type": "slide_deck",
                "format": result["format"],
                "length": result["length"],
                "language": result["language"],
                "generation_status": result["status"],
                "message": "Slide deck generation started. Use studio_status to check progress.",
                "notebook_url": f"https://notebooklm.google.com/notebook/{notebook_id}",
            }
        return {"status": "error", "error": "Failed to create slide deck"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def report_create(
    notebook_id: str,
    source_ids: list[str] | None = None,
    report_format: str = "Briefing Doc",
    custom_prompt: str = "",
    language: str = "en",
    confirm: bool = False,
) -> dict[str, Any]:
    """Generate a report from notebook sources.

    Generation takes a few minutes. Use studio_status to check progress.

    IMPORTANT: Before calling this tool, you MUST:
    1. Show the user the settings (report_format, language, custom_prompt)
    2. Ask the user to confirm they want to proceed
    3. Only set confirm=True after user approval

    Args:
        notebook_id: The notebook UUID
        source_ids: Optional list of source IDs to include (default: all sources)
        report_format: Report format - one of:
            - "Briefing Doc": Key insights and important quotes (default)
            - "Study Guide": Short-answer quiz, essay questions, glossary
            - "Blog Post": Insightful takeaways in readable article format
            - "Create Your Own": Custom format with user-defined prompt
        custom_prompt: Custom prompt when report_format="Create Your Own"
        language: BCP-47 language code (e.g., "en", "es", "fr", "de", "ja")
        confirm: Must be True to proceed. Show settings and get user confirmation first.

    Returns:
        Dictionary with artifact_id and status. Call studio_status to check progress.
    """
    if not confirm:
        return {
            "status": "pending_confirmation",
            "message": "Please confirm these settings before creating the report:",
            "settings": {
                "notebook_id": notebook_id,
                "report_format": report_format,
                "language": language,
                "custom_prompt": custom_prompt or "(none)",
                "source_ids": source_ids or "all sources",
            },
            "note": "Set confirm=True after user approves these settings.",
        }

    try:
        client = get_client()

        # Get source IDs if not provided
        if not source_ids:
            sources = client.get_notebook_sources_with_types(notebook_id)
            source_ids = [s["id"] for s in sources if s.get("id")]

        result = client.create_report(
            notebook_id=notebook_id,
            source_ids=source_ids,
            report_format=report_format,
            custom_prompt=custom_prompt,
            language=language,
        )

        if result:
            return {
                "status": "success",
                "artifact_id": result["artifact_id"],
                "type": "report",
                "format": result["format"],
                "language": result["language"],
                "generation_status": result["status"],
                "message": "Report generation started. Use studio_status to check progress.",
                "notebook_url": f"https://notebooklm.google.com/notebook/{notebook_id}",
            }
        return {"status": "error", "error": "Failed to create report"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def flashcards_create(
    notebook_id: str,
    source_ids: list[str] | None = None,
    difficulty: str = "medium",
    confirm: bool = False,
) -> dict[str, Any]:
    """Generate flashcards from notebook sources.

    Generation takes a few minutes. Use studio_status to check progress.

    IMPORTANT: Before calling this tool, you MUST:
    1. Show the user the settings (difficulty)
    2. Ask the user to confirm they want to proceed
    3. Only set confirm=True after user approval

    Args:
        notebook_id: The notebook UUID
        source_ids: Optional list of source IDs to include (default: all sources)
        difficulty: Difficulty level - "easy", "medium" (default), or "hard"
        confirm: Must be True to proceed. Show settings and get user confirmation first.

    Returns:
        Dictionary with artifact_id and status. Call studio_status to check progress.
    """
    if not confirm:
        return {
            "status": "pending_confirmation",
            "message": "Please confirm these settings before creating flashcards:",
            "settings": {
                "notebook_id": notebook_id,
                "difficulty": difficulty,
                "source_ids": source_ids or "all sources",
            },
            "note": "Set confirm=True after user approves these settings.",
        }

    try:
        client = get_client()

        # Get source IDs if not provided
        if not source_ids:
            sources = client.get_notebook_sources_with_types(notebook_id)
            source_ids = [s["id"] for s in sources if s.get("id")]

        result = client.create_flashcards(
            notebook_id=notebook_id,
            source_ids=source_ids,
            difficulty=difficulty,
        )

        if result:
            return {
                "status": "success",
                "artifact_id": result["artifact_id"],
                "type": "flashcards",
                "difficulty": result["difficulty"],
                "generation_status": result["status"],
                "message": "Flashcards generation started. Use studio_status to check progress.",
                "notebook_url": f"https://notebooklm.google.com/notebook/{notebook_id}",
            }
        return {"status": "error", "error": "Failed to create flashcards"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def mind_map_create(
    notebook_id: str,
    source_ids: list[str] | None = None,
    title: str = "Mind Map",
    confirm: bool = False,
) -> dict[str, Any]:
    """Generate and save a mind map from notebook sources.

    This tool generates a visual mind map from the sources and saves it to the notebook.
    Mind maps are generated immediately (not async like audio/video).

    IMPORTANT: Before calling this tool, you MUST:
    1. Show the user the settings (title, sources)
    2. Ask the user to confirm they want to proceed
    3. Only set confirm=True after user approval

    Args:
        notebook_id: The notebook UUID
        source_ids: Optional list of source IDs to include (default: all sources)
        title: Display title for the mind map
        confirm: Must be True to proceed. Show settings and get user confirmation first.

    Returns:
        Dictionary with mind_map_id and the generated mind map structure.
    """
    if not confirm:
        return {
            "status": "pending_confirmation",
            "message": "Please confirm these settings before creating the mind map:",
            "settings": {
                "notebook_id": notebook_id,
                "title": title,
                "source_ids": source_ids or "all sources",
            },
            "note": "Set confirm=True after user approves these settings.",
        }

    try:
        client = get_client()

        # Get source IDs if not provided
        if not source_ids:
            sources = client.get_notebook_sources_with_types(notebook_id)
            source_ids = [s["id"] for s in sources if s.get("id")]

        # Step 1: Generate the mind map
        gen_result = client.generate_mind_map(source_ids=source_ids)
        if not gen_result or not gen_result.get("mind_map_json"):
            return {"status": "error", "error": "Failed to generate mind map"}

        # Step 2: Save the mind map to the notebook
        save_result = client.save_mind_map(
            notebook_id=notebook_id,
            mind_map_json=gen_result["mind_map_json"],
            source_ids=source_ids,
            title=title,
        )

        if save_result:
            # Parse the JSON to get structure info
            import json
            try:
                mind_map_data = json.loads(save_result.get("mind_map_json", "{}"))
                root_name = mind_map_data.get("name", "Unknown")
                children_count = len(mind_map_data.get("children", []))
            except json.JSONDecodeError:
                root_name = "Unknown"
                children_count = 0

            return {
                "status": "success",
                "mind_map_id": save_result["mind_map_id"],
                "notebook_id": notebook_id,
                "title": save_result.get("title", title),
                "root_name": root_name,
                "children_count": children_count,
                "message": "Mind map created and saved successfully.",
                "notebook_url": f"https://notebooklm.google.com/notebook/{notebook_id}",
            }
        return {"status": "error", "error": "Failed to save mind map"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@mcp.tool()
def mind_map_list(notebook_id: str) -> dict[str, Any]:
    """List all mind maps in a notebook.

    Args:
        notebook_id: The notebook UUID

    Returns:
        Dictionary with list of mind maps, each containing id, title, and created_at.
    """
    try:
        client = get_client()
        mind_maps = client.list_mind_maps(notebook_id)

        return {
            "status": "success",
            "count": len(mind_maps),
            "mind_maps": [
                {
                    "mind_map_id": mm.get("mind_map_id"),
                    "title": mm.get("title", "Untitled"),
                    "created_at": mm.get("created_at"),
                }
                for mm in mind_maps
            ],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# Essential cookies for NotebookLM API authentication
# Only these are needed - no need to save all 20+ cookies from the browser
ESSENTIAL_COOKIES = [
    "SID", "HSID", "SSID", "APISID", "SAPISID",  # Core auth cookies
    "__Secure-1PSID", "__Secure-3PSID",  # Secure session variants
    "__Secure-1PAPISID", "__Secure-3PAPISID",  # Secure API variants
    "OSID", "__Secure-OSID",  # Origin-bound session
    "__Secure-1PSIDTS", "__Secure-3PSIDTS",  # Timestamp tokens (rotate frequently)
    "SIDCC", "__Secure-1PSIDCC", "__Secure-3PSIDCC",  # Session cookies (rotate frequently)
]


@mcp.tool()
def save_auth_tokens(
    cookies: str,
    csrf_token: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """Save authentication cookies for NotebookLM.

    SIMPLIFIED: You only need to provide cookies! The CSRF token and session ID
    are now automatically extracted when needed.

    To extract cookies using Chrome DevTools MCP:
    1. Navigate to notebooklm.google.com
    2. Get cookies from any network request (get_network_request)
    3. Call this tool with the cookie header

    Args:
        cookies: Full cookie header string from a NotebookLM network request
        csrf_token: (DEPRECATED - auto-extracted) CSRF token from page source
        session_id: (DEPRECATED - auto-extracted) Session ID from page source

    Returns:
        Dictionary with status and cache location
    """
    global _client

    try:
        import time
        from .auth import AuthTokens, save_tokens_to_cache

        # Parse cookie string to dict
        all_cookies = {}
        for part in cookies.split("; "):
            if "=" in part:
                key, value = part.split("=", 1)
                all_cookies[key] = value

        # Validate required cookies
        required = ["SID", "HSID", "SSID", "APISID", "SAPISID"]
        missing = [c for c in required if c not in all_cookies]
        if missing:
            return {
                "status": "error",
                "error": f"Missing required cookies: {missing}",
            }

        # Filter to only essential cookies (reduces noise significantly)
        cookie_dict = {k: v for k, v in all_cookies.items() if k in ESSENTIAL_COOKIES}

        # Create and save tokens
        # Note: csrf_token and session_id are now optional - they'll be auto-extracted
        tokens = AuthTokens(
            cookies=cookie_dict,
            csrf_token=csrf_token,  # May be empty - will be auto-extracted
            session_id=session_id,  # May be empty - will be auto-extracted
            extracted_at=time.time(),
        )
        save_tokens_to_cache(tokens)

        # Reset client so next call uses fresh tokens
        _client = None

        from .auth import get_cache_path
        return {
            "status": "success",
            "message": f"Saved {len(cookie_dict)} essential cookies (filtered from {len(all_cookies)}). "
                       f"CSRF token and session ID will be auto-extracted when needed.",
            "cache_path": str(get_cache_path()),
            "note": "You no longer need to extract CSRF token or session ID manually!",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main():
    """Run the MCP server."""
    import os

    from .auth import get_cache_path, load_cached_tokens

    # Check authentication sources
    has_env_auth = bool(os.environ.get("NOTEBOOKLM_COOKIES"))
    cached_tokens = load_cached_tokens()

    if not has_env_auth and not cached_tokens:
        print("WARNING: No authentication found.")
        print()
        print("Run 'notebooklm-consumer-auth' to authenticate via Chrome.")
        print("(It will launch Chrome automatically if needed)")
        print()
    elif cached_tokens and not has_env_auth:
        print(f"Using cached auth tokens from {get_cache_path()}")

    print("NotebookLM Consumer MCP Server starting...")
    mcp.run()
    return 0


if __name__ == "__main__":
    exit(main())
