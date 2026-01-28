"""AI-friendly documentation output for the --ai flag."""

from notebooklm_tools import __version__

AI_DOCS = """# NLM CLI - AI Assistant Guide

You are interacting with `nlm`, a command-line interface for Google NotebookLM.
This documentation teaches you how to use the tool effectively.

## Version

nlm version {version}

---

## CRITICAL: Authentication

**Sessions last approximately 20 minutes.** Before ANY operation, you MUST ensure the user is authenticated.

### First-Time Setup / Re-Authentication
```bash
nlm login
```
This opens NotebookLM in Chrome and extracts cookies automatically.
Output on success: `✓ Successfully authenticated!`

### Check If Already Authenticated
```bash
nlm auth status
```
Validates credentials by making a real API call (lists notebooks).
Shows: `✓ Authenticated` with notebook count, or error if expired.

### Auto-Authentication Recovery (Automatic)
The CLI includes 3-layer automatic recovery:
1. **CSRF/Session Refresh**: Automatically refreshes tokens on 401 errors
2. **Token Reload**: Reloads tokens from disk if updated externally (e.g., by another session)
3. **Headless Auth**: If Chrome profile has saved login, attempts headless authentication

This means most session expirations are handled automatically. You only need to manually run `nlm login` if all recovery layers fail.

### Session Expired?
If ANY command returns:
- "Cookies have expired"
- "authentication may have expired"

Run:
```bash
nlm login
```

---

## Command Structure: Noun-First vs Verb-First

The CLI supports **TWO command styles** - use whichever feels more natural:

### Noun-First (Resource-Oriented)
```bash
nlm notebook create "Title"
nlm notebook list
nlm source add <notebook> --url <url>
nlm studio status <notebook>
```

### Verb-First (Action-Oriented)
```bash
nlm create notebook "Title"
nlm list notebooks
nlm add url <notebook> <url>
nlm status artifacts <notebook>
```

**Both styles call the same functions.** Choose based on preference. This guide shows both.

---

## Quick Reference

### All Top-Level Commands (Noun-First)

| Command | Description |
|---------|-------------|
| `nlm login` | Authenticate with NotebookLM (**START HERE**) |
| `nlm auth` | Check authentication status (status, list, delete) |
| `nlm config` | View/edit configuration (show, get, set) |
| `nlm notebook` | Manage notebooks (list, create, get, describe, rename, delete, query) |
| `nlm source` | Manage sources (list, add, get, describe, content, delete, stale, sync) |
| `nlm chat` | Chat with notebooks (start, configure) |
| `nlm studio` | Manage artifacts (status, delete) |
| `nlm research` | Research and discover sources (start, status, import) |
| `nlm alias` | Manage ID shortcuts (set, get, list, delete) |
| `nlm download` | Download artifacts (audio, video, report, mind-map, slides, infographic, data-table) |
| `nlm audio` | Create audio overviews/podcasts (create) |
| `nlm report` | Create reports (create) |
| `nlm quiz` | Create quizzes (create) |
| `nlm flashcards` | Create flashcards (create) |
| `nlm mindmap` | Create mind maps (create) |
| `nlm slides` | Create slide decks (create) |
| `nlm infographic` | Create infographics (create) |
| `nlm video` | Create video overviews (create) |
| `nlm data-table` | Create data tables (create) |
| `nlm share` | Manage notebook sharing (status, public, private, invite) |

### All Verb-First Commands

| Command | Description |
|---------|-------------|
| `nlm create` | Create resources (notebook, audio, video, report, infographic, slides, quiz, flashcards, data-table, mindmap) |
| `nlm list` | List resources (notebooks, sources, artifacts, aliases, stale-sources) |
| `nlm get` | Get details (notebook, source, config, alias) |
| `nlm delete` | Delete resources (notebook, source, artifact, alias) |
| `nlm add` | Add sources (url, text, drive) |
| `nlm describe` | Get AI summaries (notebook, source) |
| `nlm query` | Chat with sources (notebook) |
| `nlm sync` | Sync Drive sources |
| `nlm content` | Get raw source content |
| `nlm stale` | List stale Drive sources |
| `nlm rename` | Rename resources (notebook) |
| `nlm status` | Check status (artifacts, research) |
| `nlm configure` | Configure settings (chat) |
| `nlm set` | Set values (alias, config) |
| `nlm show` | Show information (config, aliases) |
| `nlm download-verb` | Download artifacts (audio, video, report, mind-map, slides, infographic, data-table) |
| `nlm research-verb` | Research commands (start, import) |

---

## Alias System (Shortcuts for UUIDs)

Create memorable names for long UUIDs:

```bash
# IMPORTANT: Always check existing aliases before creating new ones
nlm alias list

# Set an alias (type is auto-detected)
nlm alias set myproject abc123-def456-...

# Use aliases anywhere an ID is expected
nlm notebook get myproject
nlm source list myproject  
nlm audio create myproject --confirm

# Manage aliases
nlm alias list                    # List all
nlm alias get myproject           # Resolve to UUID
nlm alias delete myproject        # Remove
```

---

## Complete Command Reference

### Login & Auth

```bash
nlm login                              # Authenticate (opens browser)
nlm login --profile work               # Named profile
nlm login --manual --file <path>       # Import cookies from file
nlm login --check                      # Only check if auth valid

nlm auth status                        # Check current auth
nlm auth status --profile work         # Check specific profile
nlm auth list                          # List all profiles
nlm auth delete work --confirm         # Delete a profile
```


### Notebook Commands

**Noun-First:**
```bash
nlm notebook list                      # List all notebooks
nlm notebook list --json               # JSON output
nlm notebook list --quiet              # IDs only
nlm notebook list --title              # "ID: Title" format
nlm notebook list --full               # All columns

nlm notebook create "Title"            # Create new notebook
nlm notebook get <id>                  # Get notebook details
nlm notebook describe <id>             # AI summary with topics
nlm notebook rename <id> "New Title"   # Rename notebook
nlm notebook delete <id> --confirm     # Delete permanently
nlm notebook query <id> "question"     # Chat with sources
nlm notebook query <id> "follow up" --conversation-id <cid>
nlm notebook query <id> "question" --source-ids <id1,id2>
```

**Verb-First:**
```bash
nlm list notebooks                     # List all notebooks
nlm create notebook "Title"            # Create new notebook
nlm get notebook <id>                  # Get notebook details
nlm describe notebook <id>             # AI summary with topics
nlm rename notebook <id> "New Title"   # Rename notebook
nlm delete notebook <id> --confirm     # Delete permanently
nlm query notebook <id> "question"     # Chat with sources
```

### Source Commands

**Noun-First:**
```bash
nlm source list <notebook-id>          # List sources
nlm source list <notebook-id> --full   # Full details
nlm source list <notebook-id> --url    # "ID: URL" format
nlm source list <notebook-id> --drive  # Show Drive sources with freshness
nlm source list <notebook-id> --drive --skip-freshness  # Faster, skip freshness checks

nlm source add <notebook-id> --url "https://..."           # Add URL
nlm source add <notebook-id> --url "https://..." --wait    # Add URL and wait until processed
nlm source add <notebook-id> --url "https://youtube.com/..." # Add YouTube
nlm source add <notebook-id> --text "content" --title "Title"  # Add text
nlm source add <notebook-id> --file /path/to/doc.pdf        # Upload local file
nlm source add <notebook-id> --file doc.pdf --wait          # Upload and wait until processed
nlm source add <notebook-id> --drive <doc-id>              # Add Drive doc
nlm source add <notebook-id> --drive <doc-id> --type slides  # Add Drive slides
# Types: doc, slides, sheets, pdf
# Supported file types: PDF, TXT, MP3, WAV, M4A

nlm source get <source-id>             # Get source metadata
nlm source describe <source-id>        # AI summary + keywords
nlm source content <source-id>         # Raw text content
nlm source content <source-id> --output file.txt  # Export to file
nlm source delete <source-id> --confirm  # Delete source
nlm source stale <notebook-id>         # List stale Drive sources
nlm source sync <notebook-id> --confirm  # Sync all stale
nlm source sync <notebook-id> --source-ids <ids> --confirm  # Sync specific
```

**Verb-First:**
```bash
nlm list sources <notebook-id>         # List sources
nlm add url <notebook-id> <url>        # Add URL source
nlm add url <notebook-id> <url> --wait # Add URL and wait until processed
nlm add text <notebook-id> "content" --title "Title"  # Add text source
nlm add drive <notebook-id> <doc-id>   # Add Drive source
nlm get source <source-id>             # Get source metadata
nlm describe source <source-id>        # AI summary + keywords
nlm content source <source-id>         # Raw text content
nlm delete source <source-id> --confirm  # Delete source
nlm list stale-sources <notebook-id>   # List stale Drive sources
nlm stale sources <notebook-id>        # Alternative: list stale sources
nlm sync sources <notebook-id> --confirm  # Sync all stale sources
```

### Chat Commands

**Noun-First:**
```bash
# Interactive REPL (multi-turn conversation)
nlm chat start <notebook-id>           # Start interactive session
# In REPL:
#   /sources - List sources
#   /clear   - Reset conversation
#   /help    - Show commands
#   /exit    - Exit

# Configure chat behavior
nlm chat configure <notebook-id> --goal default
nlm chat configure <notebook-id> --goal learning_guide
nlm chat configure <notebook-id> --goal custom --prompt "Act as a tutor..."
nlm chat configure <notebook-id> --response-length longer   # longer, default, shorter
```

**Verb-First:**
```bash
nlm configure chat <notebook-id> --goal default            # Configure chat
nlm configure chat <notebook-id> --style conversational    # Set chat style
nlm configure chat <notebook-id> --length longer           # Set response length
```

### Research Commands

**Noun-First:**
```bash
# Start research (--notebook-id is REQUIRED)
nlm research start "query" --notebook-id <id>                    # Fast web (default)
nlm research start "query" --notebook-id <id> --mode deep        # Deep web (~5min)
nlm research start "query" --notebook-id <id> --source drive     # Fast drive
nlm research start "query" --notebook-id <id> --force            # Override pending

# Check progress
nlm research status <notebook-id>                    # Poll until done (5min max)
nlm research status <notebook-id> --max-wait 0       # Single check
nlm research status <notebook-id> --task-id <tid>    # Specific task
nlm research status <notebook-id> --full             # Full details

# Import discovered sources
nlm research import <notebook-id> <task-id>              # Import all
nlm research import <notebook-id> <task-id> --indices 0,2,5  # Import specific
```

**Verb-First:**
```bash
nlm research-verb start "query" --notebook-id <id>       # Start research
nlm research-verb start "query" --notebook-id <id> --mode deep
nlm status research <notebook-id>                        # Check progress
nlm research-verb import <notebook-id> <task-id>         # Import sources
```

**Research Modes:**
- `fast`: ~30 seconds, ~10 sources (web or drive)
- `deep`: ~5 minutes, ~40-80 sources (web only)

### Generation Commands (Studio)

**All generation commands support:**
- `--confirm` or `-y`: Skip confirmation (REQUIRED for automation)
- `--source-ids <id1,id2>`: Limit to specific sources
- `--language <code>`: BCP-47 code (en, es, fr, de, ja)
- `--profile <name>`: Use specific auth profile

#### Audio (Podcast)

**Noun-First:**
```bash
nlm audio create <notebook-id> --confirm
nlm audio create <notebook-id> --format deep_dive --length default --confirm
nlm audio create <notebook-id> --format brief --focus "key topic" --confirm
# Formats: deep_dive, brief, critique, debate
# Lengths: short, default, long
```

**Verb-First:**
```bash
nlm create audio <notebook-id> --confirm
nlm create audio <notebook-id> --length medium --format mp3 --confirm
```

#### Report

**Noun-First:**
```bash
nlm report create <notebook-id> --confirm
nlm report create <notebook-id> --format "Study Guide" --confirm
nlm report create <notebook-id> --format "Create Your Own" --prompt "Summary..." --confirm
# Formats: "Briefing Doc", "Study Guide", "Blog Post", "Create Your Own"
```

**Verb-First:**
```bash
nlm create report <notebook-id> --confirm
nlm create report <notebook-id> --type study-guide --confirm
```

#### Quiz

**Noun-First:**
```bash
nlm quiz create <notebook-id> --confirm
nlm quiz create <notebook-id> --count 5 --difficulty 3 --confirm
# Count: number of questions (default: 2)
# Difficulty: 1-5 (1=easy, 5=hard, default: 2)
```

**Verb-First:**
```bash
nlm create quiz <notebook-id> --confirm
nlm create quiz <notebook-id> --difficulty medium --quantity 10 --confirm
```

#### Flashcards

**Noun-First:**
```bash
nlm flashcards create <notebook-id> --confirm
nlm flashcards create <notebook-id> --difficulty hard --confirm
# Difficulty: easy, medium, hard (default: medium)
```

**Verb-First:**
```bash
nlm create flashcards <notebook-id> --confirm
nlm create flashcards <notebook-id> --difficulty hard --confirm
```

#### Mind Map

**Noun-First:**
```bash
nlm mindmap create <notebook-id> --confirm
nlm mindmap create <notebook-id> --title "Topic Overview" --confirm
```

**Verb-First:**
```bash
nlm create mindmap <notebook-id> --confirm
nlm create mindmap <notebook-id> --title "Overview" --confirm
```

#### Slides

**Noun-First:**
```bash
nlm slides create <notebook-id> --confirm
nlm slides create <notebook-id> --format presenter --length short --confirm
# Formats: detailed, presenter (default: detailed)
# Lengths: short, default
```

**Verb-First:**
```bash
nlm create slides <notebook-id> --confirm
nlm create slides <notebook-id> --length short --format pdf --confirm
```

#### Infographic

**Noun-First:**
```bash
nlm infographic create <notebook-id> --confirm
nlm infographic create <notebook-id> --orientation portrait --detail detailed --confirm
# Orientations: landscape, portrait, square (default: landscape)
# Detail: concise, standard, detailed (default: standard)
```

**Verb-First:**
```bash
nlm create infographic <notebook-id> --confirm
nlm create infographic <notebook-id> --orientation portrait --detail detailed --confirm
```

#### Video

**Noun-First:**
```bash
nlm video create <notebook-id> --confirm
nlm video create <notebook-id> --format brief --style whiteboard --confirm
# Formats: explainer, brief (default: explainer)
# Styles: auto_select, classic, whiteboard, kawaii, anime, watercolor, retro_print, heritage, paper_craft
```

**Verb-First:**
```bash
nlm create video <notebook-id> --confirm
nlm create video <notebook-id> --style whiteboard --format mp4 --confirm
```

#### Data Table

**Noun-First:**
```bash
nlm data-table create <notebook-id> "Extract all dates and events" --confirm
# DESCRIPTION is REQUIRED as second argument
```

**Verb-First:**
```bash
nlm create data-table <notebook-id> "Extract all dates and events" --confirm
# DESCRIPTION is REQUIRED as second argument
```

### Studio Commands (Artifact Management)

**Noun-First:**
```bash
nlm studio status <notebook-id>                    # List all artifacts + status
nlm studio status <notebook-id> --json             # JSON output
nlm studio status <notebook-id> --full             # All details
nlm studio delete <notebook-id> <artifact-id> --confirm  # Delete artifact
```

**Verb-First:**
```bash
nlm status artifacts <notebook-id>                 # List all artifacts + status
nlm status artifacts <notebook-id> --full          # All details
nlm delete artifact <notebook-id> <artifact-id> --confirm  # Delete artifact
```

### Download Commands (Get Artifact Files)

**Download generated artifacts to local files.** All artifacts are streamed efficiently to avoid memory issues.

**Noun-First:**
```bash
nlm download audio <notebook-id> <artifact-id>              # Download audio (mp3)
nlm download audio <notebook-id> <artifact-id> --output podcast.mp3
nlm download video <notebook-id> <artifact-id>              # Download video (mp4)
nlm download report <notebook-id> <artifact-id>             # Download report (txt/md)
nlm download mind-map <notebook-id> <artifact-id>           # Download mind map (txt)
nlm download slide-deck <notebook-id> <artifact-id>         # Download slides (txt)
nlm download infographic <notebook-id> <artifact-id>        # Download infographic (png)
nlm download data-table <notebook-id> <artifact-id>         # Download data table (csv)
```

**Verb-First:**
```bash
nlm download-verb audio <notebook-id> <artifact-id>         # Download audio
nlm download-verb video <notebook-id> <artifact-id>         # Download video
nlm download-verb report <notebook-id> <artifact-id>        # Download report
nlm download-verb mind-map <notebook-id> <artifact-id>      # Download mind map
nlm download-verb slides <notebook-id> <artifact-id>        # Download slides
nlm download-verb infographic <notebook-id> <artifact-id>   # Download infographic
nlm download-verb data-table <notebook-id> <artifact-id>    # Download data table
```

**Download Workflow:**
1. Generate artifact: `nlm audio create <notebook> --confirm`
2. Check status: `nlm studio status <notebook>` (wait for "completed")
3. Get artifact ID from status output
4. Download: `nlm download audio <notebook> <artifact-id>`

**Supported Formats:**
- Audio: `.mp3` (Deep Dive, Brief, Critique, Debate)
- Video: `.mp4` (Explainer, Brief with various styles)
- Report: `.txt` or `.md` (Briefing Doc, Study Guide, Blog Post)
- Mind Map: `.txt` (node structure)
- Slide Deck: `.txt` (slide content)
- Infographic: `.png` (visual)
- Data Table: `.csv` (tabular data)

#### Interactive Artifact Downloads (Quiz, Flashcards)

**Download with format conversion:**
```bash
nlm download quiz <notebook-id> <artifact-id>                    # JSON (default)
nlm download quiz <notebook-id> <artifact-id> --format json      # Structured JSON
nlm download quiz <notebook-id> <artifact-id> --format markdown  # Markdown format
nlm download quiz <notebook-id> <artifact-id> --format html      # Interactive HTML

nlm download flashcards <notebook-id> <artifact-id>                    # JSON (default)
nlm download flashcards <notebook-id> <artifact-id> --format markdown  # Markdown format
nlm download flashcards <notebook-id> <artifact-id> --format html      # Interactive HTML
```

**Format Options:**
- `json`: Structured data (for programmatic use)
- `markdown`: Human-readable format
- `html`: Interactive browser-based quiz/flashcards with scoring

### Export Commands (to Google Docs/Sheets)

```bash
nlm export docs <notebook-id> <artifact-id>              # Export report to Google Docs
nlm export docs <notebook-id> <artifact-id> --title "My Doc"  # With custom title
nlm export sheets <notebook-id> <artifact-id>            # Export data table to Google Sheets
```

**Exportable Types:**
- Reports (Briefing Doc, Study Guide, Blog Post) → Google Docs
- Data Tables → Google Sheets

### Alias Commands

**Noun-First:**
```bash
nlm alias set <name> <uuid>     # Create/update alias (auto-detects type)
nlm alias get <name>            # Get UUID for alias
nlm alias list                  # List all aliases
nlm alias delete <name>         # Remove (no --confirm needed)
```

**Verb-First:**
```bash
nlm set alias <name> <uuid>     # Create/update alias
nlm get alias <name>            # Get UUID for alias
nlm list aliases                # List all aliases
nlm show aliases                # Alternative: show all aliases
nlm delete alias <name>         # Remove alias
```

### Share Commands

**Noun-First:**
```bash
nlm share status <notebook-id>              # View sharing settings + collaborators
nlm share status <notebook-id> --json       # JSON output
nlm share public <notebook-id>              # Enable public link access
nlm share private <notebook-id>             # Disable public link access
nlm share invite <notebook-id> <email>      # Invite as viewer (default)
nlm share invite <notebook-id> <email> --role editor  # Invite as editor
```

**Verb-First:**
```bash
nlm share status <notebook-id>              # View sharing settings (same as noun-first)
nlm share public <notebook-id>              # Enable public access
nlm share private <notebook-id>             # Disable public access
nlm share invite <notebook-id> <email> --role viewer  # Invite collaborator
```

### Config Commands

**Noun-First:**
```bash
nlm config show                 # Display current config (TOML)
nlm config show --json          # Display as JSON
nlm config get <key>            # Get specific setting
nlm config set <key> <value>    # Update setting
```

**Verb-First:**
```bash
nlm show config                 # Display current config
nlm show config --json          # Display as JSON
nlm get config <key>            # Get specific setting
nlm set config <key> <value>    # Update setting
```

---

## Output Formats

List commands support multiple formats:

| Flag | Description |
|------|-------------|
| (none) | Rich table (human-readable) |
| `--json` | JSON output (for parsing) |
| `--quiet` | IDs only (for piping) |
| `--title` | "ID: Title" format |
| `--url` | "ID: URL" format (sources only) |
| `--full` | All columns/details |

---

## Error Handling

| Error Message | Cause | Solution |
|--------------|-------|----------|
| "Cookies have expired" | Session expired | Run `nlm login` |
| "authentication may have expired" | Session expired | Run `nlm login` |
| "Notebook not found" | Invalid ID | Run `nlm notebook list` |
| "Source not found" | Invalid ID | Run `nlm source list <notebook-id>` |
| "Rate limit exceeded" | Too many API calls | Wait 30 seconds, retry |
| "Research already in progress" | Pending research | Use `--force` or import first |

---

## Complete Task Sequences

### Sequence 1: Research → Podcast → Download (Noun-First)

```bash
# 1. Authenticate
nlm login

# 2. Create notebook
nlm notebook create "AI Research 2026"
# ID: abc123...

# 3. Set alias for convenience
nlm alias set ai abc123...

# 4. Start deep research
nlm research start "agentic AI trends 2026" --notebook-id ai --mode deep
# Task ID: task456...

# 5. Wait for completion
nlm research status ai --max-wait 300

# 6. Import all sources
nlm research import ai task456...

# 7. Generate podcast
nlm audio create ai --format deep_dive --confirm

# 8. Check status until completed
nlm studio status ai
# Note artifact ID: audio789...

# 9. Download when ready
nlm download audio ai audio789... --output podcast.mp3
```

### Sequence 1 Alternative: Research → Podcast → Download (Verb-First)

```bash
# 1. Authenticate
nlm login

# 2. Create notebook
nlm create notebook "AI Research 2026"
# ID: abc123...

# 3. Set alias
nlm set alias ai abc123...

# 4. Start research
nlm research-verb start "agentic AI trends 2026" --notebook-id ai --mode deep

# 5. Check status
nlm status research ai --max-wait 300

# 6. Import sources
nlm research-verb import ai task456...

# 7. Create podcast
nlm create audio ai --confirm

# 8. Check status
nlm status artifacts ai

# 9. Download
nlm download-verb audio ai audio789... --output podcast.mp3
```

### Sequence 2: Quick Source Ingestion

**Noun-First:**
```bash
nlm source add <notebook-id> --url "https://example1.com"
nlm source add <notebook-id> --url "https://example2.com"
nlm source add <notebook-id> --text "My notes here" --title "Notes"
nlm source list <notebook-id>
```

**Verb-First:**
```bash
nlm add url <notebook-id> "https://example1.com"
nlm add url <notebook-id> "https://example2.com"
nlm add text <notebook-id> "My notes here" --title "Notes"
nlm list sources <notebook-id>
```

### Sequence 3: Generate Study Materials

**Noun-First:**
```bash
nlm quiz create <notebook-id> --count 10 --difficulty 3 --confirm
nlm flashcards create <notebook-id> --difficulty hard --confirm
nlm report create <notebook-id> --format "Study Guide" --confirm
```

**Verb-First:**
```bash
nlm create quiz <notebook-id> --difficulty medium --quantity 10 --confirm
nlm create flashcards <notebook-id> --difficulty hard --confirm
nlm create report <notebook-id> --type study-guide --confirm
```

### Sequence 4: Complete Content Generation Pipeline

```bash
# Create all content types at once
nlm create audio <notebook-id> --confirm
nlm create video <notebook-id> --confirm
nlm create report <notebook-id> --confirm
nlm create quiz <notebook-id> --confirm
nlm create flashcards <notebook-id> --confirm
nlm create mindmap <notebook-id> --confirm
nlm create slides <notebook-id> --confirm
nlm create infographic <notebook-id> --confirm

# Check all statuses
nlm status artifacts <notebook-id> --full

# Download all when ready (replace <artifact-ids> with actual IDs)
nlm download-verb audio <notebook-id> <audio-id>
nlm download-verb video <notebook-id> <video-id>
nlm download-verb report <notebook-id> <report-id>
nlm download-verb mind-map <notebook-id> <mindmap-id>
nlm download-verb slides <notebook-id> <slides-id>
nlm download-verb infographic <notebook-id> <infographic-id>
```

---

## Tips for AI Assistants

1. **Always run `nlm login` first** if any auth error occurs
2. **Use `--confirm` for all generation/delete commands** to avoid blocking prompts
3. **Capture IDs from create outputs** - you'll need them for subsequent operations
4. **Use aliases** for frequently-used notebooks to simplify commands
5. **Poll for long operations** - audio/video takes 1-5 minutes; use `nlm studio status` or `nlm status artifacts`
6. **Research requires `--notebook-id`** - the flag is mandatory
7. **Session lifetime is ~20 minutes** - re-login if operations start failing
8. **Use `--max-wait 0`** for single status poll instead of blocking
9. **⚠️ ALWAYS ask user before delete** - Before running ANY delete command, ask the user for explicit confirmation. Deletions are IRREVERSIBLE. Show what will be deleted and warn about permanent data loss.
10. **Check aliases before creating** - Run `nlm alias list` or `nlm list aliases` before creating a new alias to avoid conflicts with existing names.
11. **DO NOT launch REPL** - Never use `nlm chat start` - it opens an interactive REPL that AI tools cannot control. Use `nlm notebook query` or `nlm query notebook` for one-shot Q&A instead.
12. **Choose output format wisely** - Default output (no flags) is compact and token-efficient—use it for status checks. Use `--quiet` to capture IDs for piping. Only use `--json` when you need to parse specific fields programmatically.
13. **Verb-first vs Noun-first** - Both command styles work identically. Use whichever is more natural for the context. Noun-first groups by resource (notebook, source), verb-first groups by action (create, list, delete).
14. **Download workflow** - Always wait for artifact completion before downloading. Check status with `nlm studio status <notebook>`, get the artifact ID, then download with `nlm download <type> <notebook> <artifact-id>`.
15. **Artifact generation takes time** - Audio/video: 1-5 minutes. Reports/quizzes: 30-60 seconds. Always poll status before attempting download.
16. **Download output files** - If no `--output` specified, files are saved with default names (e.g., `audio_<id>.mp3`, `video_<id>.mp4`, `report_<id>.txt`). Use `--output` to specify custom filenames.
17. **Streaming downloads** - All downloads use efficient streaming to handle large files without memory issues. This is automatic.
18. **Drive source sync** - Use `nlm source stale <notebook>` or `nlm list stale-sources <notebook>` to check which Drive sources need syncing before running sync commands.
19. **Use --wait for blocking source adds** - When adding sources before querying, use `nlm source add ... --wait` to block until processing completes. This ensures the source is ready for queries.
20. **Export to Google Docs/Sheets** - Reports can be exported to Google Docs, Data Tables to Google Sheets. Use `nlm export docs/sheets <notebook> <artifact-id>`.
"""


def print_ai_docs() -> None:
    """Print the AI-friendly documentation."""
    print(AI_DOCS.format(version=__version__))
