# NotebookLM Consumer MCP Server

An MCP server for **Consumer NotebookLM** (notebooklm.google.com) - the free/personal tier.

> **Note:** This is NOT for NotebookLM Enterprise (Vertex AI). Those are completely separate systems.

## Features

| Tool | Description |
|------|-------------|
| `notebook_list` | List all notebooks |
| `notebook_create` | Create a new notebook |
| `notebook_get` | Get notebook details with sources |
| `notebook_describe` | Get AI-generated summary of notebook content |
| `source_describe` | Get AI-generated summary and keywords for a source |
| `notebook_rename` | Rename a notebook |
| `chat_configure` | Configure chat goal/style and response length |
| `notebook_delete` | Delete a notebook (requires confirmation) |
| `notebook_add_url` | Add URL/YouTube as source |
| `notebook_add_text` | Add pasted text as source |
| `notebook_add_drive` | Add Google Drive document as source |
| `notebook_query` | Ask questions and get AI answers |
| `source_list_drive` | List sources with freshness status |
| `source_sync_drive` | Sync stale Drive sources (requires confirmation) |
| `source_delete` | Delete a source from notebook (requires confirmation) |
| `research_start` | Start Web or Drive research to discover sources |
| `research_status` | Poll research progress with built-in wait |
| `research_import` | Import discovered sources into notebook |
| `audio_overview_create` | Generate audio podcasts (requires confirmation) |
| `video_overview_create` | Generate video overviews (requires confirmation) |
| `infographic_create` | Generate infographics (requires confirmation) |
| `slide_deck_create` | Generate slide decks (requires confirmation) |
| `studio_status` | Check studio artifact generation status |
| `studio_delete` | Delete studio artifacts (requires confirmation) |
| `save_auth_tokens` | Save cookies for authentication |

## Important Disclaimer

This MCP uses **reverse-engineered internal APIs** that:
- Are undocumented and may change without notice
- Require cookie extraction from your browser (I have a tool for that!)

Use at your own risk for personal/experimental purposes.

## Installation

```bash
# Clone the repository
git clone https://github.com/jacob-bd/notebooklm-consumer-mcp.git
cd notebooklm-consumer-mcp

# Install with uv
uv tool install .
```

## Upgrading

To update to the latest version:

```bash
cd notebooklm-consumer-mcp

# Pull latest changes
git pull

# Clean cache and reinstall (IMPORTANT: cache clean prevents stale code)
uv cache clean && uv tool install --force .
```

After upgrading, restart your AI tool to reconnect to the updated MCP server:

- **Claude Code:** Restart the application, or use `/mcp` to reconnect
- **Cursor:** Restart the application
- **Gemini CLI:** Restart the CLI session

## Authentication

Before using the MCP, you need to authenticate with NotebookLM. Run:

```bash
# Recommended: Auto mode (launches Chrome, you log in)
notebooklm-consumer-auth

# Alternative: File mode (manual cookie extraction)
notebooklm-consumer-auth --file
```

**Auto mode** launches a dedicated Chrome profile, you log in to Google, and cookies are extracted automatically. Your login persists for future auth refreshes.

**File mode** shows instructions for manually extracting cookies from Chrome DevTools and saving them to a file.

After successful auth, add the MCP to your AI tool and restart.

For detailed instructions, troubleshooting, and how the authentication system works, see **[docs/AUTHENTICATION.md](docs/AUTHENTICATION.md)**.

## MCP Configuration

> **⚠️ Context Window Warning:** This MCP provides **31 tools** which consume a significant portion of your context window. It's recommended to **disable the MCP when not actively using NotebookLM** to preserve context for your other work. In Claude Code, use `@notebooklm-consumer` to toggle it on/off, or use `/mcp` command.

No environment variables needed - the MCP uses cached tokens from `~/.notebooklm-consumer/auth.json`.

### Claude Code (Recommended CLI Method)

Use the built-in CLI command to add the MCP server:

**Add for all projects (recommended):**
```bash
claude mcp add --scope user notebooklm-consumer notebooklm-consumer-mcp
```

**Or add for current project only:**
```bash
claude mcp add notebooklm-consumer notebooklm-consumer-mcp
```

That's it! Restart Claude Code to use the MCP tools.

**Verify installation:**
```bash
claude mcp list
```

<details>
<summary>Alternative: Manual JSON Configuration</summary>

If you prefer to edit the config file manually, add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "notebooklm-consumer": {
      "command": "notebooklm-consumer-mcp"
    }
  }
}
```

Restart Claude Code after editing.
</details>

### Cursor

**Step 1:** Find where the MCP binary is installed:

```bash
# On macOS/Linux:
which notebooklm-consumer-mcp

# On Windows:
where notebooklm-consumer-mcp

# Example output: /Users/yourname/.local/bin/notebooklm-consumer-mcp
```

**Step 2:** Add to `~/.cursor/mcp.json` using the full path from Step 1:

```json
{
  "mcpServers": {
    "notebooklm-consumer": {
      "command": "/Users/yourname/.local/bin/notebooklm-consumer-mcp",
      "args": []
    }
  }
}
```

**Step 3:** Restart Cursor.

### Gemini CLI

**Step 1:** Find where the MCP binary is installed:

```bash
# On macOS/Linux:
which notebooklm-consumer-mcp

# On Windows:
where notebooklm-consumer-mcp

# Example output: /Users/yourname/.local/bin/notebooklm-consumer-mcp
```

**Step 2:** Add to `~/.gemini/settings.json` under `mcpServers` using the full path from Step 1:

```json
"notebooklm-consumer": {
  "command": "/Users/yourname/.local/bin/notebooklm-consumer-mcp",
  "args": []
}
```

**Step 3:** Restart Gemini CLI.

### Managing Context Window Usage

Since this MCP has 31 tools, it's good practice to disable it when not in use:

**Claude Code:**
```bash
# Toggle on/off by @-mentioning in chat
@notebooklm-consumer

# Or use the /mcp command to enable/disable
/mcp
```

**Cursor/Gemini CLI:**
- Comment out the server in your config file when not needed
- Or use your tool's MCP management features if available

## Usage Examples

### List Notebooks
```python
notebooks = notebook_list()
```

### Create and Query
```python
# Create a notebook
notebook = notebook_create(title="Research Project")

# Add sources
notebook_add_url(notebook_id, url="https://example.com/article")
notebook_add_text(notebook_id, text="My research notes...", title="Notes")

# Ask questions
result = notebook_query(notebook_id, query="What are the key points?")
print(result["answer"])
```

### Configure Chat Settings
```python
# Set a custom chat persona with longer responses
chat_configure(
    notebook_id=notebook_id,
    goal="custom",
    custom_prompt="You are an expert data analyst. Provide detailed statistical insights.",
    response_length="longer"
)

# Use learning guide mode with default length
chat_configure(
    notebook_id=notebook_id,
    goal="learning_guide",
    response_length="default"
)

# Reset to defaults with concise responses
chat_configure(
    notebook_id=notebook_id,
    goal="default",
    response_length="shorter"
)
```

**Goal Options:** default, custom (requires custom_prompt), learning_guide
**Response Lengths:** default, longer, shorter

### Get AI Summaries

```python
# Get AI-generated summary of what a notebook is about
summary = notebook_describe(notebook_id)
print(summary["summary"])  # Markdown with **bold** keywords
print(summary["suggested_topics"])  # Suggested report topics

# Get AI-generated summary of a specific source
source_info = source_describe(source_id)
print(source_info["summary"])  # AI summary with **bold** keywords
print(source_info["keywords"])  # Topic chips: ["Medical education", "AI tools", ...]
```

### Sync Stale Drive Sources
```python
# Check which sources need syncing
sources = source_list_drive(notebook_id)

# Sync stale sources (after user confirmation)
source_sync_drive(source_ids=["id1", "id2"], confirm=True)
```

### Delete Sources
```python
# Delete a source from notebook (after user confirmation)
source_delete(source_id="source-uuid", confirm=True)
```

### Research and Import Sources
```python
# Start web research (fast mode, ~30 seconds)
result = research_start(
    query="value of ISVs on cloud marketplaces",
    source="web",   # or "drive" for Google Drive
    mode="fast",    # or "deep" for extended research (web only)
    title="ISV Research"
)
notebook_id = result["notebook_id"]

# Poll until complete (built-in wait, polls every 30s for up to 5 min)
status = research_status(notebook_id)

# Import all discovered sources
research_import(
    notebook_id=notebook_id,
    task_id=status["research"]["task_id"]
)

# Or import specific sources by index
research_import(
    notebook_id=notebook_id,
    task_id=status["research"]["task_id"],
    source_indices=[0, 2, 5]  # Import only sources at indices 0, 2, and 5
)
```

**Research Modes:**
- `fast` + `web`: Quick web search, ~10 sources in ~30 seconds
- `deep` + `web`: Extended research with AI report, ~40 sources in 3-5 minutes
- `fast` + `drive`: Quick Google Drive search, ~10 sources in ~30 seconds

### Generate Audio/Video Overviews
```python
# Create an audio overview (podcast)
result = audio_overview_create(
    notebook_id=notebook_id,
    format="deep_dive",  # deep_dive, brief, critique, debate
    length="default",    # short, default, long
    language="en",
    confirm=True         # Required - show settings first, then confirm
)

# Create a video overview
result = video_overview_create(
    notebook_id=notebook_id,
    format="explainer",      # explainer, brief
    visual_style="classic",  # auto_select, classic, whiteboard, kawaii, anime, etc.
    language="en",
    confirm=True
)

# Check generation status (takes several minutes)
status = studio_status(notebook_id)
for artifact in status["artifacts"]:
    print(f"{artifact['title']}: {artifact['status']}")
    if artifact["audio_url"]:
        print(f"  Audio: {artifact['audio_url']}")
    if artifact["video_url"]:
        print(f"  Video: {artifact['video_url']}")

# Delete an artifact (after user confirmation)
studio_delete(
    notebook_id=notebook_id,
    artifact_id="artifact-uuid",
    confirm=True
)
```

**Audio Formats:** deep_dive (conversation), brief, critique, debate
**Audio Lengths:** short, default, long
**Video Formats:** explainer, brief
**Video Styles:** auto_select, classic, whiteboard, kawaii, anime, watercolor, retro_print, heritage, paper_craft

## Consumer vs Enterprise

| Feature | Consumer | Enterprise |
|---------|----------|------------|
| URL | notebooklm.google.com | vertexaisearch.cloud.google.com |
| Auth | Browser cookies | Google Cloud ADC |
| API | Internal RPCs | Discovery Engine API |
| Notebooks | Personal | Separate system |
| Audio Overviews | Yes | Yes |
| Video Overviews | Yes | No |
| Mind Maps | Yes | No |
| Flashcards/Quizzes | Yes | No |

## Authentication Lifecycle

| Component | Duration | Refresh |
|-----------|----------|---------|
| Cookies | ~2-4 weeks | Re-extract from Chrome when expired |
| CSRF Token | Per MCP session | Auto-extracted on MCP start |
| Session ID | Per MCP session | Auto-extracted on MCP start |

When cookies expire, you'll see an auth error. Just extract fresh cookies and call `save_auth_tokens()` again.

## Troubleshooting

### Chrome DevTools MCP Not Working (Cursor/Gemini CLI)

If Chrome DevTools MCP shows "no tools, prompts or resources" or fails to start, it's likely due to a known `npx` bug with the puppeteer-core module.

**Symptoms:**
- Cursor/Gemini CLI shows MCP as connected but with "No tools, prompts, or resources"
- Process spawn errors in logs: `spawn pnpx ENOENT` or module not found errors
- Can't extract cookies for NotebookLM authentication

**Fix:**

1. **Install pnpm** (if not already installed):
   ```bash
   npm install -g pnpm
   ```

2. **Update Chrome DevTools MCP configuration:**

   **For Cursor** (`~/.cursor/mcp.json`):
   ```json
   "chrome-devtools": {
     "command": "pnpm",
     "args": ["dlx", "chrome-devtools-mcp@latest", "--browser-url=http://127.0.0.1:9222"]
   }
   ```

   **For Gemini CLI** (`~/.gemini/settings.json`):
   ```json
   "chrome-devtools": {
     "command": "pnpm",
     "args": ["dlx", "chrome-devtools-mcp@latest"]
   }
   ```

3. **Restart your IDE/CLI** for changes to take effect.

**Why this happens:** Chrome DevTools MCP uses `puppeteer-core` which changed its module path in v23+, but `npx` caching behavior causes module resolution failures. Using `pnpm dlx` avoids this issue.

**Related Issues:**
- [ChromeDevTools/chrome-devtools-mcp#160](https://github.com/ChromeDevTools/chrome-devtools-mcp/issues/160)
- [ChromeDevTools/chrome-devtools-mcp#111](https://github.com/ChromeDevTools/chrome-devtools-mcp/issues/111)
- [ChromeDevTools/chrome-devtools-mcp#221](https://github.com/ChromeDevTools/chrome-devtools-mcp/issues/221)

## Limitations

- **Rate limits**: Free tier has ~50 queries/day
- **No official support**: API may change without notice
- **Cookie expiration**: Need to re-extract cookies every few weeks

## Contributing

See [CLAUDE.md](CLAUDE.md) for detailed API documentation and how to add new features.

## Vibe Coding Alert

Full transparency: this project was built by a non-developer using AI coding assistants. If you're an experienced Python developer, you might look at this codebase and wince. That's okay.

The goal here was to scratch an itch - programmatic access to Consumer NotebookLM - and learn along the way. The code works, but it's likely missing patterns, optimizations, or elegance that only years of experience can provide.

**This is where you come in.** If you see something that makes you cringe, please consider contributing rather than just closing the tab. This is open source specifically because human expertise is irreplaceable. Whether it's refactoring, better error handling, type hints, or architectural guidance - PRs and issues are welcome.

Think of it as a chance to mentor an AI-assisted developer through code review. We all benefit when experienced developers share their knowledge.

## License

MIT License
