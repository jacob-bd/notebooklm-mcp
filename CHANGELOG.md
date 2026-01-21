# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.14] - 2026-01-17

### Fixed
- **Critical Research Stability**:
  - `poll_research` now accepts status code `6` (Imported) as success, fixing "hanging" Fast Research.
  - Added `target_task_id` filtering to `poll_research` to ensure the correct research task is returned (essential for Deep Research).
  - Updated `research_status` and `research_import` to use task ID filtering.
  - `research_status` tool now accepts an optional `task_id` parameter.
- **Missing Source Constants**:
  - Included the code changes for `SOURCE_TYPE_UPLOADED_FILE`, `SOURCE_TYPE_IMAGE`, and `SOURCE_TYPE_WORD_DOC` that were omitted in v0.1.13.

## [0.1.13] - 2026-01-17

### Added
- **Source type constants** for proper identification of additional source types:
  - `SOURCE_TYPE_UPLOADED_FILE` (11): Direct file uploads (e.g., .docx uploaded directly)
  - `SOURCE_TYPE_IMAGE` (13): Image files (GIF, JPEG, PNG)
  - `SOURCE_TYPE_WORD_DOC` (14): Word documents via Google Drive
- Updated `SOURCE_TYPES` CodeMapper with `uploaded_file`, `image`, and `word_doc` mappings

## [0.1.12] - 2026-01-16

### Fixed
- **Standardized source timeouts** (supersedes #9)
  - Renamed `DRIVE_SOURCE_TIMEOUT` to `SOURCE_ADD_TIMEOUT` (120s)
  - Applied to all source additions: Drive, URL (websites/YouTube), and Text
  - Added graceful timeout handling to `add_url_source` and `add_text_source`
  - Prevents timeout errors when importing large websites or documents

## [0.1.11] - 2026-01-16

### Fixed
- **Close Chrome after interactive authentication** - Chrome is now properly terminated after `notebooklm-mcp-auth` completes, releasing the profile lock and enabling headless auth for automatic token refresh
- **Improve token reload from disk** - Removed the 5-minute timeout when reloading tokens during auth recovery. Previously, cached tokens older than 5 minutes were ignored even if the user had just run `notebooklm-mcp-auth`

These fixes resolve "Authentication expired" errors that occurred even after users re-authenticated.

## [0.1.10] - 2026-01-15

### Fixed
- **Timeout when adding large Drive sources** (fixes #9)
  - Extended timeout from 30s to 120s for Drive source operations
  - Large Google Slides (100+ slides) now add successfully
  - Returns `status: "timeout"` instead of error when timeout occurs, indicating operation may have succeeded
  - Added `DRIVE_SOURCE_TIMEOUT` constant in `api_client.py`

## [0.1.9] - 2026-01-11


### Added
- **Automatic re-authentication** - Server now survives token expirations without restart
  - Three-layer recovery: CSRF refresh → disk reload → headless Chrome auth
  - Works with long-running MCP sessions (e.g., MCP Super Assistant proxy)
- `refresh_auth` MCP tool for explicit token reload
- `run_headless_auth()` function for background authentication (if Chrome profile has saved login)
- `has_chrome_profile()` helper to check if profile exists

### Changed
- `launch_chrome()` now returns `subprocess.Popen` handle instead of `bool` for cleanup control
- `_call_rpc()` enhanced with `_deep_retry` parameter for multi-layer auth recovery

## [0.1.8] - 2026-01-10

### Added
- `constants.py` module as single source of truth for all API code-name mappings
- `CodeMapper` class with bidirectional lookup (name→code, code→name)
- Dynamic error messages now show valid options from `CodeMapper`

### Changed
- **BREAKING:** `quiz_create` now accepts `difficulty: str` ("easy"|"medium"|"hard") instead of `int` (1|2|3)
- All MCP tools now use `constants.CodeMapper` for input validation
- All API client output now uses `constants.CodeMapper` for human-readable names
- Removed ~10 static `_get_*_name` helper methods from `api_client.py`
- Removed duplicate `*_codes` dictionaries from `server.py` tool functions

### Fixed
- Removed duplicate code block in research status parsing

## [0.1.7] - 2026-01-10

### Fixed
- Fixed URL source retrieval by implementing correct metadata parsing in `get_notebook_sources_with_types`
- Added fallback for finding source type name in `get_notebook_sources_with_types`

## [0.1.6] - 2026-01-10

### Added
- `studio_status` now includes mind maps alongside audio/video/slides
- `delete_mind_map()` method with two-step RPC deletion
- `RPC_DELETE_MIND_MAP` constant for mind map deletion
- Unit tests for authentication retry logic

### Fixed
- Mind map deletion now works via `studio_delete` (fixes #7)
- `notebook_query` now accepts `source_ids` as JSON string for compatibility with some AI clients (fixes #5)
- Deleted/tombstone mind maps are now filtered from `list_mind_maps` responses
- Token expiration handling with auto-retry on RPC Error 16 and HTTP 401/403

### Changed
- Updated `bl` version to `boq_labs-tailwind-frontend_20260108.06_p0`
- `delete_studio_artifact` now accepts optional `notebook_id` for mind map fallback

## [0.1.5] - 2026-01-09

### Fixed
- Improved LLM guidance for authentication errors

## [0.1.4] - 2026-01-09

### Added
- `source_get_content` tool for raw text extraction from sources

## [0.1.3] - 2026-01-08

### Fixed
- Chrome detection on Linux distros

## [0.1.2] - 2026-01-07

### Fixed
- YouTube URL handling - use correct array position

## [0.1.1] - 2026-01-06

### Changed
- Improved research tool descriptions for better AI selection

## [0.1.0] - 2026-01-05

### Added
- Initial release
- Full NotebookLM API client with 31 MCP tools
- Authentication via Chrome DevTools or manual cookie extraction
- Notebook, source, query, and studio management
- Research (web/Drive) with source import
- Audio/Video overview generation
- Report, flashcard, quiz, infographic, slide deck creation
- Mind map generation
