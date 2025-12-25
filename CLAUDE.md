# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**NotebookLM Consumer MCP Server** - Provides programmatic access to Consumer NotebookLM (notebooklm.google.com) using reverse-engineered internal APIs.

**IMPORTANT:** This is for the **Consumer/Free tier** of NotebookLM, NOT NotebookLM Enterprise (Vertex AI). These are completely separate systems with different notebooks, different APIs, and different authentication.

| Aspect | Consumer (this project) | Enterprise |
|--------|------------------------|------------|
| URL | notebooklm.google.com | vertexaisearch.cloud.google.com |
| Auth | Browser cookies + CSRF | Google Cloud ADC |
| API | Internal batchexecute RPC | Discovery Engine API |
| Stability | Undocumented, may break | Official, documented |

## Development Commands

```bash
# Install dependencies
uv tool install .

# Reinstall after code changes (ALWAYS clean cache first)
uv cache clean && uv tool install --force .

# Run the MCP server
notebooklm-consumer-mcp

# Run tests
uv run pytest

# Run a single test
uv run pytest tests/test_file.py::test_function -v
```

**Python requirement:** >=3.11

## Authentication (SIMPLIFIED!)

**You only need to provide COOKIES!** The CSRF token and session ID are now **automatically extracted** when needed.

### Method 1: Chrome DevTools MCP (Recommended)

```python
# 1. Navigate to NotebookLM page
navigate_page(url="https://notebooklm.google.com/")

# 2. Get cookies from any network request:
get_network_request(reqid=<any_batchexecute_request>)  # Copy cookie header

# 3. Save cookies (CSRF and session ID are auto-extracted!)
save_auth_tokens(cookies=<cookie_header>)
```

That's it! No more manual CSRF token or session ID extraction.

### Method 2: Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `NOTEBOOKLM_COOKIES` | Yes | Full cookie header from Chrome DevTools |
| `NOTEBOOKLM_CSRF_TOKEN` | No | (DEPRECATED - auto-extracted) |
| `NOTEBOOKLM_SESSION_ID` | No | (DEPRECATED - auto-extracted) |

### How Auto-Refresh Works

When the client is initialized without a CSRF token:
1. It fetches `notebooklm.google.com` using the stored cookies
2. Extracts `SNlM0e` (CSRF) and `FdrFJe` (session ID) from the page HTML
3. Uses these tokens for all subsequent API calls

This happens automatically - users never need to think about ephemeral tokens.

### Essential Cookies

The MCP needs these cookies (automatically filtered from the full cookie header):

| Cookie | Purpose |
|--------|---------|
| `SID`, `HSID`, `SSID`, `APISID`, `SAPISID` | Core auth (required) |
| `__Secure-1PSID`, `__Secure-3PSID` | Secure session variants |
| `__Secure-1PAPISID`, `__Secure-3PAPISID` | Secure API variants |
| `OSID`, `__Secure-OSID` | Origin-bound session |
| `__Secure-1PSIDTS`, `__Secure-3PSIDTS` | Timestamp tokens |
| `SIDCC`, `__Secure-1PSIDCC`, `__Secure-3PSIDCC` | Session cookies |

**Important:** Some cookies (PSIDTS, SIDCC, PSIDCC) rotate frequently. Always get fresh cookies from an active Chrome session.

### Token Expiration

- **Cookies**: Stable for weeks, but some rotate on each request
- **CSRF token**: Auto-refreshed on each client initialization
- **Session ID**: Auto-refreshed on each client initialization

When API calls fail with auth errors, re-extract fresh cookies from Chrome DevTools.

## Architecture

```
src/notebooklm_consumer_mcp/
├── __init__.py      # Package version
├── server.py        # FastMCP server with tool definitions
├── api_client.py    # Internal API client (reverse-engineered)
├── auth.py          # Token caching and validation
└── auth_cli.py      # CLI for Chrome-based auth (notebooklm-consumer-auth)
```

**Executables:**
- `notebooklm-consumer-mcp` - The MCP server
- `notebooklm-consumer-auth` - CLI for extracting tokens (requires closing Chrome)

## API Discovery Documentation

This section documents everything discovered about the internal NotebookLM API through reverse engineering.

### Base Endpoint

```
POST https://notebooklm.google.com/_/LabsTailwindUi/data/batchexecute
```

### Request Format

```
Content-Type: application/x-www-form-urlencoded

f.req=<URL-encoded JSON>&at=<CSRF token>
```

The `f.req` structure:
```json
[[["<RPC_ID>", "<params_json>", null, "generic"]]]
```

### URL Query Parameters

| Param | Description |
|-------|-------------|
| `rpcids` | The RPC ID being called |
| `source-path` | Current page path (e.g., `/notebook/<id>`) |
| `bl` | Build/version string (e.g., `boq_labs-tailwind-frontend_20251217.10_p0`) |
| `f.sid` | Session ID |
| `hl` | Language code (e.g., `en`) |
| `_reqid` | Request counter |
| `rt` | Response type (`c`) |

### Response Format

```
)]}'
<byte_count>
<json_array>
```

- Starts with `)]}'` (anti-XSSI prefix) - MUST be stripped
- Followed by byte count, then JSON
- Multiple chunks may be present

### Known RPC IDs

| RPC ID | Purpose | Params Structure |
|--------|---------|------------------|
| `wXbhsf` | List notebooks | `[null, 1, null, [2]]` |
| `rLM1Ne` | Get notebook details | `[notebook_id, null, [2], null, 0]` |
| `CCqFvf` | Create notebook | `[title, null, null, [2], [1,null,null,null,null,null,null,null,null,null,[1]]]` |
| `s0tc2d` | Rename notebook / Configure chat | See s0tc2d section below |
| `WWINqb` | Delete notebook | `[[notebook_id], [2]]` |
| `izAoDd` | Add source (unified) | See source types below |
| `hizoJc` | Get source details | `[["source_id"], [2], [2]]` |
| `yR9Yof` | Check source freshness | `[null, ["source_id"], [2]]` → returns `false` if stale |
| `FLmJqe` | Sync Drive source | `[null, ["source_id"], [2]]` |
| `hPTbtc` | Get conversation IDs | `[notebook_id]` |
| `hT54vc` | User preferences | - |
| `ZwVcOc` | Settings | - |
| `ozz5Z` | Subscription info | - |
| `Ljjv0c` | Start Fast Research | `[["query", source_type], null, 1, "notebook_id"]` |
| `QA9ei` | Start Deep Research | `[null, [1], ["query", source_type], 5, "notebook_id"]` |
| `e3bVqc` | Poll Research Results | `[null, null, "notebook_id"]` |
| `LBwxtb` | Import Research Sources | `[null, [1], "task_id", "notebook_id", [sources]]` |
| `R7cb6c` | Create Studio Content | See Studio RPCs section |
| `gArtLc` | Poll Studio Status | `[[2], notebook_id, 'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"']` |
| `V5N4be` | Delete Studio Content | `[[2], "artifact_id"]` |
| `yyryJe` | Generate Mind Map | See Mind Map RPCs section |
| `CYK0Xb` | Save Mind Map | See Mind Map RPCs section |
| `cFji9` | List Mind Maps | `[notebook_id]` |
| `ciyUvf` | Get Suggested Report Formats | `[[2], notebook_id, [[source_id1], ...]]` |
| `VfAZjd` | Get Report Suggestions | `[notebook_id, [2]]` |

### `s0tc2d` - Notebook Update RPC

This RPC handles multiple notebook update operations based on which array position is populated.

#### Rename Notebook

Updates the notebook title.

```python
# Request params
[notebook_id, [[null, null, null, [null, "New Title"]]]]

# Example
["549e31df-1234-5678-90ab-cdef01234567", [[null, null, null, [null, "My New Notebook Name"]]]]

# Response
# Returns updated notebook info
```

#### Configure Chat Settings

Configures the notebook's chat behavior - goal/style and response length.

```python
# Request params
[notebook_id, [[null, null, null, null, null, null, null, [[goal_code, custom_prompt?], [response_length_code]]]]]

# chat_settings is at position 7 in the nested array
# Format: [[goal_code, custom_prompt_if_custom], [response_length_code]]

# Example - Default goal + Longer response:
["549e31df-...", [[null, null, null, null, null, null, null, [[1], [4]]]]]

# Example - Custom goal + Default response:
["549e31df-...", [[null, null, null, null, null, null, null, [[2, "You are an expert..."], [1]]]]]

# Example - Learning Guide + Shorter response:
["549e31df-...", [[null, null, null, null, null, null, null, [[3], [5]]]]]
```

#### Goal/Style Codes

| Code | Goal | Description |
|------|------|-------------|
| 1 | Default | General purpose research and brainstorming |
| 2 | Custom | Custom prompt (up to 10,000 characters) |
| 3 | Learning Guide | Educational focus with learning-oriented responses |

#### Response Length Codes

| Code | Length | Description |
|------|--------|-------------|
| 1 | Default | Standard response length |
| 4 | Longer | Verbose, detailed responses |
| 5 | Shorter | Concise, brief responses |

### Source Types (via `izAoDd` RPC)

All source types use the same RPC but with different param structures:

#### URL/YouTube Source
```python
source_data = [
    None,
    None,
    [url],  # URL at position 2
    None, None, None, None, None, None, None,
    1
]
params = [[[source_data]], notebook_id, [2], settings]
```

#### Pasted Text Source
```python
source_data = [
    None,
    [title, text_content],  # Title and content at position 1
    None,
    2,  # Type indicator at position 3
    None, None, None, None, None, None,
    1
]
params = [[[source_data]], notebook_id, [2], settings]
```

#### Google Drive Source
```python
source_data = [
    [document_id, mime_type, 1, title],  # Drive doc at position 0
    None, None, None, None, None, None, None, None, None,
    1
]
params = [[[source_data]], notebook_id, [2], settings]
```

**MIME Types:**
- `application/vnd.google-apps.document` - Google Docs
- `application/vnd.google-apps.presentation` - Google Slides
- `application/vnd.google-apps.spreadsheet` - Google Sheets
- `application/pdf` - PDF files

### Query Endpoint (Streaming)

Queries use a **different endpoint** - NOT batchexecute!

```
POST /_/LabsTailwindUi/data/google.internal.labs.tailwind.orchestration.v1.LabsTailwindOrchestrationService/GenerateFreeFormStreamed
```

#### Query Request Structure
```python
params = [
    [  # Source IDs - each in nested array
        [[["source_id_1"]]],
        [[["source_id_2"]]],
    ],
    "Your question here",  # Query text
    None,
    [2, None, [1]],  # Config
    "conversation-uuid"  # For follow-up questions
]

f_req = [None, json.dumps(params)]
```

#### Query Response
Streaming JSON with multiple chunks:
1. **Thinking steps** - "Understanding...", "Exploring...", etc.
2. **Final answer** - Markdown formatted with citations
3. **Source references** - Links to specific passages in sources

### Research RPCs (Source Discovery)

NotebookLM's "Research" feature discovers and suggests sources based on a query. It supports two source types (Web and Google Drive) and two research modes (Fast and Deep).

#### Source Types
| Type | Value | Description |
|------|-------|-------------|
| Web | `1` | Searches the public web for relevant sources |
| Google Drive | `2` | Searches user's Google Drive for relevant documents |

#### Research Modes
| Mode | Description | Duration | Can Leave Page |
|------|-------------|----------|----------------|
| Fast Research | Quick search, ~10 sources | ~10-30 seconds | No |
| Deep Research | Extended research with AI report, ~40+ sources | 3-5 minutes | Yes |

#### `Ljjv0c` - Start Fast Research

Initiates a Fast Research session for either Web or Drive sources.

```python
# Request params
[["query", source_type], null, 1, "notebook_id"]

# source_type: 1 = Web, 2 = Google Drive
# Example (Web):  [["What is OpenShift", 1], null, 1, "549e31df-..."]
# Example (Drive): [["sales strategy documents", 2], null, 1, "549e31df-..."]

# Response
["task_id"]
# Example: ["6837228d-d832-4e5c-89d3-b9aa33ff7815"]
```

#### `QA9ei` - Start Deep Research (Web Only)

Initiates a Deep Research session with extended web crawling and AI-generated report.

```python
# Request params
[null, [1], ["query", source_type], 5, "notebook_id"]

# The `5` indicates Deep Research mode
# source_type: 1 = Web (Drive not supported for Deep Research)
# Example: [null, [1], ["enterprise kubernetes trends 2025", 1], 5, "549e31df-..."]

# Response
["task_id", "report_id"]
# Example: ["a02dd39b-94c0-443e-b9e4-9c15ab9016c5", null]
```

#### `e3bVqc` - Poll Research Results

Polls for research completion and retrieves results. Call repeatedly until status = 2.

```python
# Request params
[null, null, "notebook_id"]

# Response structure (when completed)
[[[
  "task_id",
  [
    "notebook_id",
    ["query", source_type],
    research_mode,  # 1 = Fast, 5 = Deep
    [
      # Array of discovered sources
      [
        "url",           # Web URL or Drive URL
        "title",         # Source title
        "description",   # AI-generated description
        result_type      # 1 = Web, 2 = Google Doc, 3 = Slides, 8 = Sheets
      ],
      # ... more sources
    ],
    "summary"  # AI-generated summary of sources
  ],
  status  # 1 = in progress, 2 = completed
],
[end_timestamp, nanos],
[start_timestamp, nanos]
]]

# Deep Research also includes a report in the results (long markdown document)
```

**Result Types (in poll response):**
| Type | Meaning |
|------|---------|
| 1 | Web URL |
| 2 | Google Doc |
| 3 | Google Slides |
| 5 | Deep Research Report |
| 8 | Google Sheets |

#### `LBwxtb` - Import Research Sources

Imports selected sources from research results into the notebook.

```python
# Request params
[null, [1], "task_id", "notebook_id", [source1, source2, ...]]

# Each source structure:
# Web source:
[null, null, ["url", "title"], null, null, null, null, null, null, null, 2]

# Drive source:
[["document_id", "mime_type", null, "title"], null, null, null, null, null, null, null, null, null, 1]

# Response
# Array of created source objects with source_id, title, metadata
[[source_id, title, metadata, [null, 2]], ...]
```

#### Research Flow Summary

```
1. Start Research
   ├── Fast: Ljjv0c with source_type (1=Web, 2=Drive)
   └── Deep: QA9ei with mode=5 (Web only)

2. Poll Results
   └── e3bVqc → repeat until status=2

3. Import Sources
   └── LBwxtb with selected sources

4. Sources appear in notebook → can query them
```

#### Important Notes

- **Only one active research per notebook**: Starting a new research cancels any pending results
- **Deep Research runs in background**: User can navigate away after initiation
- **Fast Research blocks navigation**: Must stay on page until complete
- **Drive URLs format**: `https://drive.google.com/a/redhat.com/open?id=<document_id>`
- **Web URLs**: Standard HTTP/HTTPS URLs

### Studio RPCs (Audio/Video Overviews)

NotebookLM's "Studio" feature generates audio podcasts and video overviews from notebook sources.

#### `R7cb6c` - Create Studio Content

Creates both Audio and Video Overviews using the same RPC, distinguished by type code.

##### Audio Overview Request
```python
params = [
    [2],                           # Config
    notebook_id,                   # Notebook UUID
    [
        None, None,
        1,                         # STUDIO_TYPE_AUDIO
        [[[source_id1]], [[source_id2]], ...],  # Source IDs (nested arrays)
        None, None,
        [
            None,
            [
                focus_prompt,      # Focus text (what AI should focus on)
                length_code,       # 1=Short, 2=Default, 3=Long
                None,
                [[source_id1], [source_id2], ...],  # Source IDs (simpler format)
                language_code,     # "en", "es", etc.
                None,
                format_code        # 1=Deep Dive, 2=Brief, 3=Critique, 4=Debate
            ]
        ]
    ]
]
```

##### Video Overview Request
```python
params = [
    [2],                           # Config
    notebook_id,                   # Notebook UUID
    [
        None, None,
        3,                         # STUDIO_TYPE_VIDEO
        [[[source_id1]], [[source_id2]], ...],  # Source IDs (nested arrays)
        None, None, None, None,
        [
            None, None,
            [
                [[source_id1], [source_id2], ...],  # Source IDs
                language_code,     # "en", "es", etc.
                focus_prompt,      # Focus text
                None,
                format_code,       # 1=Explainer, 2=Brief
                visual_style_code  # 1=Auto, 2=Custom, 3=Classic, etc.
            ]
        ]
    ]
]
```

##### Response Structure
```python
# Returns: [[artifact_id, title, type, sources, status, ...]]
# status: 1 = in_progress, 3 = completed
```

#### `gArtLc` - Poll Studio Status

Polls for audio/video generation status.

```python
# Request
params = [[2], notebook_id, 'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"']

# Response includes:
# - artifact_id (UUID of generated content)
# - type (1 = Audio, 3 = Video)
# - status (1 = in_progress, 3 = completed)
# - Audio/Video URLs when completed
# - Duration (for audio)
```

#### `V5N4be` - Delete Studio Content

Deletes an audio or video overview artifact permanently.

```python
# Request
params = [[2], "artifact_id"]

# Response
[]  # Empty array on success
```

**WARNING:** This action is IRREVERSIBLE. The artifact is permanently deleted.

#### Audio Options

| Option | Values |
|--------|--------|
| **Formats** | 1=Deep Dive (conversation), 2=Brief, 3=Critique, 4=Debate |
| **Lengths** | 1=Short, 2=Default, 3=Long |
| **Languages** | BCP-47 codes: "en", "es", "fr", "de", "ja", etc. |

#### Video Options

| Option | Values |
|--------|--------|
| **Formats** | 1=Explainer (comprehensive), 2=Brief |
| **Visual Styles** | 1=Auto-select, 2=Custom, 3=Classic, 4=Whiteboard, 5=Kawaii, 6=Anime, 7=Watercolor, 8=Retro print, 9=Heritage, 10=Paper-craft |
| **Languages** | BCP-47 codes: "en", "es", "fr", "de", "ja", etc. |

##### Infographic Request
```python
params = [
    [2],                           # Config
    notebook_id,                   # Notebook UUID
    [
        None, None,
        7,                         # STUDIO_TYPE_INFOGRAPHIC
        [[[source_id1]], [[source_id2]], ...],  # Source IDs (nested arrays)
        None, None, None, None, None, None, None, None, None, None,  # 10 nulls
        [[focus_prompt, language, None, orientation_code, detail_level_code]]  # Options at position 14
    ]
]
```

#### Infographic Options

| Option | Values |
|--------|--------|
| **Orientations** | 1=Landscape (16:9), 2=Portrait (9:16), 3=Square (1:1) |
| **Detail Levels** | 1=Concise, 2=Standard, 3=Detailed (BETA) |
| **Languages** | BCP-47 codes: "en", "es", "fr", "de", "ja", etc. |

##### Slide Deck Request
```python
params = [
    [2],                           # Config
    notebook_id,                   # Notebook UUID
    [
        None, None,
        8,                         # STUDIO_TYPE_SLIDE_DECK
        [[[source_id1]], [[source_id2]], ...],  # Source IDs (nested arrays)
        None, None, None, None, None, None, None, None, None, None, None, None,  # 12 nulls
        [[focus_prompt, language, format_code, length_code]]  # Options at position 16
    ]
]
```

#### Slide Deck Options

| Option | Values |
|--------|--------|
| **Formats** | 1=Detailed Deck (comprehensive), 2=Presenter Slides (key points) |
| **Lengths** | 1=Short, 3=Default |
| **Languages** | BCP-47 codes: "en", "es", "fr", "de", "ja", etc. |

#### Studio Flow Summary

```
1. Create Studio Content
   ├── Audio: R7cb6c with type=1 and audio options
   ├── Video: R7cb6c with type=3 and video options
   ├── Infographic: R7cb6c with type=7 and infographic options
   └── Slide Deck: R7cb6c with type=8 and slide deck options

2. Returns immediately with artifact_id (status=in_progress)

3. Poll Status
   └── gArtLc → repeat until status=3 (completed)

4. When complete, response includes download URLs

5. Delete (optional)
   └── V5N4be with artifact_id → permanently removes content
```

### Report RPCs

Reports use the same `R7cb6c` RPC with **type code 2** (STUDIO_TYPE_REPORT).

#### Report Request Structure
```python
params = [
    [2],                           # Config
    notebook_id,                   # Notebook UUID
    [
        None, None,
        2,                         # STUDIO_TYPE_REPORT
        [[[source_id1]], [[source_id2]], ...],  # Source IDs (nested arrays)
        None, None, None,
        [
            None,
            [
                "Briefing Doc",           # Report title/format
                "Key insights and quotes", # Short description
                None,
                [[source_id1], [source_id2], ...],  # Source IDs (simpler format)
                "en",                      # Language code
                "Create a comprehensive...",  # Full prompt/instructions
                None,
                True                       # Unknown flag
            ]
        ]
    ]
]
```

#### Standard Report Formats

| Format | Description | Prompt Style |
|--------|-------------|--------------|
| **Briefing Doc** | Key insights and important quotes | Comprehensive briefing with Executive Summary |
| **Study Guide** | Short-answer quiz, essay questions, glossary | Educational focus with test prep materials |
| **Blog Post** | Insightful takeaways in readable article format | Engaging, accessible writing style |
| **Create Your Own** | Custom format with user-defined structure | User provides custom prompt |

#### `ciyUvf` - Get Suggested Report Formats

Returns AI-generated suggested report topics based on notebook sources.

```python
# Request params
params = [[2], notebook_id, [[source_id1], [source_id2], ...]]

# Response: Array of suggested reports with full prompts
[
    [
        "Strategy Briefing",           # Title
        "An analysis of...",           # Description
        None,
        [[source_ids]],                # Sources
        "Synthesize the provided...",  # Full AI prompt
        2                              # Audience level (1=beginner, 2=advanced)
    ],
    # ... more suggestions
]
```

#### `VfAZjd` - Get Report Suggestions (Summary)

Returns notebook summary and suggested report topics.

```python
# Request params
[notebook_id, [2]]

# Response includes summary and suggested topics
```

### Flashcard RPCs

Flashcards use the same `R7cb6c` RPC with **type code 4** (STUDIO_TYPE_FLASHCARDS).

#### Flashcard Request Structure
```python
params = [
    [2],                           # Config
    notebook_id,                   # Notebook UUID
    [
        None, None,
        4,                         # STUDIO_TYPE_FLASHCARDS
        [[[source_id1]], [[source_id2]], ...],  # Source IDs (nested arrays)
        None, None, None, None, None,  # 5 nulls (positions 4-8)
        [
            None,
            [
                1,                     # Unknown (possibly default count)
                None, None, None, None, None,
                [difficulty, card_count]  # [difficulty_code, card_count_code]
            ]
        ]
    ]
]
```

#### Flashcard Options

| Option | Values |
|--------|--------|
| **Difficulty** | 1=Easy, 2=Medium, 3=Hard (suspected) |
| **Card Count** | 2=Default (suspected) |

**Note:** Flashcard options are still being investigated. The basic creation works with default settings.

### Mind Map RPCs

Mind Maps use a **two-step process** with separate Generate and Save RPCs.

#### Step 1: `yyryJe` - Generate Mind Map

Generates the mind map JSON from sources.

```python
# Request params
params = [
    [[[source_id1]], [[source_id2]], ...],  # Source IDs (nested arrays)
    None, None, None, None,
    ["interactive_mindmap", [["[CONTEXT]", ""]], ""],  # Type identifier
    None,
    [2, None, [1]]  # Config
]

# Response
[
    json_mind_map_string,  # Hierarchical JSON with name/children structure
    None,
    [generation_id1, generation_id2, generation_number]
]
```

#### Step 2: `CYK0Xb` - Save Mind Map

Saves the generated mind map to the notebook.

```python
# Request params
params = [
    notebook_id,
    json_mind_map_string,  # The full JSON structure from step 1
    [2, None, None, 5, [[source_id1], [source_id2], ...]],  # Metadata with sources
    None,
    "Mind Map Title"  # Display title
]

# Response
[
    mind_map_id,           # UUID for the saved mind map
    json_mind_map_string,  # The saved JSON structure
    [2, version_id, [timestamp, nanos], 5, [[source_ids]]],  # Metadata
    None,
    "Generated Title"      # AI-generated title
]
```

#### `cFji9` - List Mind Maps

Retrieves all existing mind maps for a notebook.

```python
# Request params
[notebook_id]

# Response
[
    [
        [mind_map_id, [
            mind_map_id,
            json_mind_map_string,
            [2, version_id, [timestamp, nanos], 5, [[source_ids]]],
            None,
            "Mind Map Title"
        ]],
        # ... more mind maps
    ],
    [timestamp, nanos]  # Last updated
]
```

#### Mind Map JSON Structure

```json
{
  "name": "Root Topic",
  "children": [
    {
      "name": "Category 1",
      "children": [
        { "name": "Subcategory 1.1" },
        { "name": "Subcategory 1.2" }
      ]
    },
    {
      "name": "Category 2",
      "children": [
        { "name": "Subcategory 2.1" },
        {
          "name": "Subcategory 2.2",
          "children": [
            { "name": "Leaf Node" }
          ]
        }
      ]
    }
  ]
}
```

#### Mind Map Flow Summary

```
1. Generate Mind Map
   └── yyryJe with source IDs → returns JSON structure

2. Save Mind Map
   └── CYK0Xb with notebook_id, JSON, title → returns saved mind map with ID

3. List Mind Maps (optional)
   └── cFji9 with notebook_id → returns all mind maps
```

### Studio Type Codes Summary

| Type Code | Feature | RPC |
|-----------|---------|-----|
| 1 | Audio Overview | `R7cb6c` |
| 2 | Report | `R7cb6c` |
| 3 | Video Overview | `R7cb6c` |
| 4 | Flashcards | `R7cb6c` |
| 5 | Quiz | `R7cb6c` (not yet documented) |
| 6 | Data Table | `R7cb6c` (not yet documented) |
| 7 | Infographic | `R7cb6c` |
| 8 | Slide Deck | `R7cb6c` |
| N/A | Mind Map | `yyryJe` + `CYK0Xb` (separate RPCs) |

### Key Findings

1. **Filtering is client-side**: The `wXbhsf` RPC returns ALL notebooks. "My notebooks" vs "Shared with me" filtering happens in the browser.

2. **Unified source RPC**: All source types (URL, text, Drive) use the same `izAoDd` RPC with different param structures.

3. **Query is streaming**: The query endpoint streams the AI's thinking process before the final answer.

4. **Conversation support**: Pass a `conversation_id` for multi-turn conversations (follow-up questions).

5. **Rate limits**: Free tier has ~50 queries/day limit.

6. **Research uses same RPC for Web and Drive**: The `Ljjv0c` RPC handles both Web (source_type=1) and Drive (source_type=2) Fast Research. Only the source_type parameter differs.

7. **Deep Research is Web-only**: The `QA9ei` RPC only supports Web sources (source_type=1). Google Drive does not have a Deep Research equivalent.

## MCP Tools Provided

| Tool | Purpose |
|------|---------|
| `notebook_list` | List all notebooks |
| `notebook_create` | Create new notebook |
| `notebook_get` | Get notebook details |
| `notebook_rename` | Rename a notebook |
| `chat_configure` | Configure chat goal/style and response length |
| `notebook_delete` | Delete a notebook (REQUIRES confirmation) |
| `notebook_add_url` | Add URL/YouTube source |
| `notebook_add_text` | Add pasted text source |
| `notebook_add_drive` | Add Google Drive source |
| `notebook_query` | Ask questions (AI answers!) |
| `source_list_drive` | List sources with types, check Drive freshness |
| `source_sync_drive` | Sync stale Drive sources (REQUIRES confirmation) |
| `research_start` | Start Web or Drive research to discover sources |
| `research_status` | Check research progress and get results |
| `research_import` | Import discovered sources into notebook |
| `audio_overview_create` | Generate audio podcasts (REQUIRES confirmation) |
| `video_overview_create` | Generate video overviews (REQUIRES confirmation) |
| `infographic_create` | Generate infographics (REQUIRES confirmation) |
| `slide_deck_create` | Generate slide decks (REQUIRES confirmation) |
| `report_create` | Generate reports - Briefing Doc, Study Guide, Blog Post, Custom (REQUIRES confirmation) |
| `flashcards_create` | Generate flashcards with difficulty options (REQUIRES confirmation) |
| `mind_map_create` | Generate and save mind maps (REQUIRES confirmation) |
| `mind_map_list` | List all mind maps in a notebook |
| `studio_status` | Check studio artifact generation status |
| `studio_delete` | Delete studio artifacts (REQUIRES confirmation) |
| `save_auth_tokens` | Save tokens extracted via Chrome DevTools MCP |

**IMPORTANT - Operations Requiring Confirmation:**
- `notebook_delete` requires `confirm=True` - deletion is IRREVERSIBLE
- `source_sync_drive` requires `confirm=True` - always show stale sources first via `source_list_drive`
- `audio_overview_create` requires `confirm=True` - show settings and get user approval first
- `video_overview_create` requires `confirm=True` - show settings and get user approval first
- `infographic_create` requires `confirm=True` - show settings and get user approval first
- `slide_deck_create` requires `confirm=True` - show settings and get user approval first
- `report_create` requires `confirm=True` - show settings and get user approval first
- `flashcards_create` requires `confirm=True` - show settings and get user approval first
- `mind_map_create` requires `confirm=True` - show settings and get user approval first
- `studio_delete` requires `confirm=True` - list artifacts first via `studio_status`, deletion is IRREVERSIBLE

## Features NOT Yet Implemented

Consumer NotebookLM has many more features than Enterprise. To explore:

- [x] **Audio Overviews** - Generate podcast-style discussions (tools: `audio_overview_create`, `studio_status`, `studio_delete`)
- [x] **Video Overviews** - Generate explainer videos (tools: `video_overview_create`, `studio_status`, `studio_delete`)
- [x] **Mind Maps** - Visual knowledge maps (tools: `mind_map_create`, `mind_map_list`)
- [x] **Flashcards** - Study cards from sources (tools: `flashcards_create`, `studio_status`)
- [ ] **Quizzes** - Interactive quizzes
- [x] **Infographics** - Visual summaries (tools: `infographic_create`, `studio_status`, `studio_delete`)
- [x] **Slide Decks** - Presentation generation (tools: `slide_deck_create`, `studio_status`, `studio_delete`)
- [ ] **Data Tables** - Structured data extraction
- [x] **Reports** - Long-form reports (tools: `report_create`, `studio_status`)
- [ ] **Notes** - Save chat responses as notes
- [x] **Fast Research (Web)** - Quick web source discovery (tools: `research_start`, `research_status`, `research_import`)
- [x] **Fast Research (Drive)** - Quick Google Drive source discovery (tools: `research_start`, `research_status`, `research_import`)
- [x] **Deep Research** - Extended web research with AI report (tools: `research_start`, `research_status`, `research_import`)
- [x] **Delete notebook** - Remove notebooks (RPC: `WWINqb`)
- [x] **Rename notebook** - Change notebook title (RPC: `s0tc2d`)
- [x] **Configure chat** - Set chat goal/style and response length (tool: `chat_configure`, RPC: `s0tc2d`)
- [ ] **Delete source** - Remove sources
- [x] **Sync Drive sources** - Refresh Drive sources that changed (tools: `source_list_drive`, `source_sync_drive`)
- [ ] **Share notebook** - Collaboration features
- [ ] **Export** - Download content

## HIGH PRIORITY: Drive Source Sync Automation

**Problem:** NotebookLM doesn't auto-update Google Drive sources when the underlying document changes. Users must manually click each source → "Check freshness" → "Click to sync with Google Drive". For notebooks with many Drive sources, this is extremely tedious.

**Goal:** Automate syncing all Drive sources in a notebook with a single command.

### Discovery Checklist

- [x] **Identify Drive sources in get_notebook response** ✅
  - Source type is at **position 4** in the metadata array
  - See "Source Metadata Structure" below for complete documentation

- [x] **Capture "Check freshness" RPC** ✅
  - RPC ID: `yR9Yof`
  - Params: `[null, ["source_id"], [2]]`
  - Response: `[[null, false, ["source_id"]]]` where `false` = stale (needs sync), `true` = fresh

- [x] **Capture "Sync with Google Drive" RPC** ✅
  - RPC ID: `FLmJqe`
  - Params: `[null, ["source_id"], [2]]`
  - Response: Updated source info with new version hash and sync timestamp

- [x] **Capture "Get source details" RPC** ✅
  - RPC ID: `hizoJc`
  - Params: `[["source_id"], [2], [2]]`
  - Response: Full source details including Drive document ID, title, thumbnails

- [x] **Implement source type detection** ✅
  - Added `get_notebook_sources_with_types()` method in api_client.py
  - Returns source_type (1/2/4), source_type_name, and is_drive flag

- [x] **Implement sync_drive_sources tool** ✅
  - `source_list_drive`: Lists all sources, checks freshness for Drive sources
  - `source_sync_drive`: Syncs specified sources with confirmation

**Note:** MIME type (doc vs slides vs sheets) not available in notebook_get response.
Could be obtained via `hizoJc` RPC if needed in the future.

### Source Metadata Structure (from `rLM1Ne` response)

Each source in the notebook response has this structure:
```python
[
  [source_id],           # UUID for the source
  "Source Title",        # Display title
  [                      # Metadata array
    drive_doc_info,      # [0] null OR [doc_id, version_hash] for Drive/Gemini sources
    byte_count,          # [1] content size (0 for Drive, actual size for pasted text)
    [timestamp, nanos],  # [2] creation timestamp
    [version_uuid, [timestamp, nanos]],  # [3] last sync info
    source_type,         # [4] KEY FIELD: 1=Google Docs, 2=Slides/Sheets, 4=Pasted Text
    null,                # [5]
    null,                # [6]
    null,                # [7]
    content_bytes        # [8] actual byte count (for Drive sources after sync)
  ],
  [null, 2]              # Footer constant
]
```

**Source Types (metadata position 4):**
| Type | Meaning | Drive Doc Info | Can Sync |
|------|---------|----------------|----------|
| 1 | **Google Docs** (Documents, including Gemini Notes) | `[doc_id, version_hash]` | **Yes** |
| 2 | **Google Slides/Sheets** (Presentations & Spreadsheets) | `[doc_id, version_hash]` | **Yes** |
| 4 | Pasted text | `null` | No |

**Example - Type 2 (Slides/Sheets source that can be synced):**
```python
[
  ["627fceb0-b811-406d-a469-da584ea5a0dd"],
  "CY26 Commercial-Planning-Guide_Gold deck_WIP",
  [
    ["1uwEGv_nVyqf26K9MBnWAwztGL3q1ZsIk-CZItNkJQ7E", "QF3r1krI9fRXzA"],  # Drive doc ID + version
    0,
    [1766007264, 929458000],
    ["f78b157c-8732-41fc-a4d6-4db2353f7816", [1766377027, 620686000]],
    2,  # <-- SOURCE TYPE = Slides/Sheets
    ...
  ],
  [null, 2]
]
```

## How We Discovered This

### Method: Network Traffic Analysis

1. Used Chrome DevTools MCP to automate browser interactions
2. Captured network requests during each action
3. Decoded `f.req` body (URL-encoded JSON)
4. Analyzed response structures
5. Tested parameter variations

### Discovery Session Examples

**Creating a notebook:**
1. Clicked "Create notebook" button via Chrome DevTools
2. Captured POST to batchexecute with `rpcids=CCqFvf`
3. Decoded params: `["", null, null, [2], [1,null,...,[1]]]`
4. Response contained new notebook UUID at index 2

**Adding Drive source:**
1. Opened Add source > Drive picker
2. Double-clicked on a document
3. Captured POST with `rpcids=izAoDd`
4. Decoded: `[[[[doc_id, mime_type, 1, title], null,...,1]]]`
5. Different from URL/text which use different array positions

**Querying:**
1. Typed question in query box, clicked Submit
2. Found NEW endpoint: `GenerateFreeFormStreamed` (not batchexecute!)
3. Streaming response with thinking steps + final answer
4. Includes citations with source passage references

## Troubleshooting

### "401 Unauthorized" or "403 Forbidden"
- Cookies or CSRF token expired
- Re-extract from Chrome DevTools

### "Invalid CSRF token"
- The `at=` value expired
- Must match the current session

### Empty notebook list
- Session might be for a different Google account
- Verify you're logged into the correct account

### Rate limit errors
- Free tier: ~50 queries/day
- Wait until the next day or upgrade to Plus

## Contributing

When adding new features:

1. Use Chrome DevTools MCP to capture the network request
2. Document the RPC ID in this file
3. Add the param structure with comments
4. Update the api_client.py with the new method
5. Add corresponding tool in server.py
6. Update the "Features NOT Yet Implemented" checklist

## License

MIT License
