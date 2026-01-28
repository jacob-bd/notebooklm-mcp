"""Main CLI application for NotebookLM Tools."""

from typing import Optional

import typer
from rich.console import Console

from notebooklm_tools import __version__
from notebooklm_tools.cli.commands.auth import app as auth_app
from notebooklm_tools.cli.commands.chat import app as chat_app
from notebooklm_tools.cli.commands.notebook import app as notebook_app
from notebooklm_tools.cli.commands.research import app as research_app
from notebooklm_tools.cli.commands.source import app as source_app
from notebooklm_tools.cli.commands.alias import app as alias_app
from notebooklm_tools.cli.commands.config import app as config_app
from notebooklm_tools.cli.commands.studio import (
    app as studio_app,
    audio_app,
    data_table_app,
    flashcards_app,
    infographic_app,
    mindmap_app,
    quiz_app,
    report_app,
    slides_app,
    video_app,
)
from notebooklm_tools.cli.commands.download import app as download_app
from notebooklm_tools.cli.commands.share import app as share_app
from notebooklm_tools.cli.commands.verbs import (
    create_app,
    list_app,
    get_app,
    delete_app,
    add_app,
    rename_app,
    status_app,
    describe_app,
    query_app,
    sync_app,
    content_app,
    stale_app,
    research_app as research_verb_app,
    configure_app,
    download_app as download_verb_app,
    set_app,
    show_app,
)

console = Console()

# Main application
app = typer.Typer(
    name="nlm",
    help="NotebookLM Tools - Unified CLI for Google NotebookLM",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Register noun-first subcommands (existing structure)
app.add_typer(notebook_app, name="notebook", help="Manage notebooks")
app.add_typer(source_app, name="source", help="Manage sources")
app.add_typer(chat_app, name="chat", help="Configure chat settings")
app.add_typer(studio_app, name="studio", help="Manage studio artifacts")
app.add_typer(research_app, name="research", help="Research and discover sources")
app.add_typer(alias_app, name="alias", help="Manage ID aliases")
app.add_typer(config_app, name="config", help="Manage configuration")
app.add_typer(download_app, name="download", help="Download artifacts (audio, video, etc)")
app.add_typer(share_app, name="share", help="Manage notebook sharing")

# Generation commands as top-level
app.add_typer(audio_app, name="audio", help="Create audio overviews")
app.add_typer(report_app, name="report", help="Create reports")
app.add_typer(quiz_app, name="quiz", help="Create quizzes")
app.add_typer(flashcards_app, name="flashcards", help="Create flashcards")
app.add_typer(mindmap_app, name="mindmap", help="Create and manage mind maps")
app.add_typer(slides_app, name="slides", help="Create slide decks")
app.add_typer(infographic_app, name="infographic", help="Create infographics")
app.add_typer(video_app, name="video", help="Create video overviews")
app.add_typer(data_table_app, name="data-table", help="Create data tables")

# Auth commands at top level
app.add_typer(auth_app, name="auth", help="Authentication status")

# Register verb-first subcommands (alternative structure)
app.add_typer(create_app, name="create", help="Create resources (notebooks, audio, video, etc)")
app.add_typer(list_app, name="list", help="List resources (notebooks, sources, artifacts)")
app.add_typer(get_app, name="get", help="Get details about resources")
app.add_typer(delete_app, name="delete", help="Delete resources (notebooks, sources, artifacts)")
app.add_typer(add_app, name="add", help="Add resources (sources to notebooks)")
app.add_typer(rename_app, name="rename", help="Rename resources")
app.add_typer(status_app, name="status", help="Check status of resources")
app.add_typer(describe_app, name="describe", help="Get AI-generated descriptions and summaries")
app.add_typer(query_app, name="query", help="Chat with notebook sources")
app.add_typer(sync_app, name="sync", help="Sync resources (Drive sources)")
app.add_typer(content_app, name="content", help="Get raw content from sources")
app.add_typer(stale_app, name="stale", help="List stale resources that need syncing")
app.add_typer(research_verb_app, name="research-verb", help="Research and discover sources (verb-first)")
app.add_typer(configure_app, name="configure", help="Configure settings")
app.add_typer(download_verb_app, name="download-verb", help="Download studio artifacts (verb-first)")
app.add_typer(set_app, name="set", help="Set values (aliases, config)")
app.add_typer(show_app, name="show", help="Show information")


@app.command("login")
def login(
    manual: bool = typer.Option(
        False, "--manual", "-m",
        help="Manually provide cookies from a file",
    ),
    check: bool = typer.Option(
        False, "--check",
        help="Only check if current auth is valid",
    ),
    profile: str = typer.Option(
        "default", "--profile", "-p",
        help="Profile name to save credentials to",
    ),
    cookie_file: Optional[str] = typer.Option(
        None, "--file", "-f",
        help="Path to file containing cookies (for manual mode)",
    ),
) -> None:
    """
    Authenticate with NotebookLM.
    
    Default: Uses Chrome DevTools Protocol to extract cookies automatically.
    Use --manual to import cookies from a file.
    """
    from notebooklm_tools.core.auth import AuthManager
    from notebooklm_tools.core.exceptions import NLMError
    
    auth = AuthManager(profile)
    
    if check:
        # Check existing auth by making a real API call
        try:
            from notebooklm_tools.core.client import NotebookLMClient
            
            p = auth.load_profile()
            console.print(f"[dim]Checking credentials for profile: {p.name}...[/dim]")
            
            # Actually test the API
            with NotebookLMClient(profile=profile) as client:
                notebooks = client.list_notebooks()
            
            # Success! Update last validated
            auth.save_profile(
                cookies=p.cookies,
                csrf_token=p.csrf_token,
                session_id=p.session_id,
                email=p.email,
            )
            
            console.print(f"[green]✓[/green] Authentication valid!")
            console.print(f"  Profile: {p.name}")
            console.print(f"  Notebooks found: {len(notebooks)}")
            if p.email:
                console.print(f"  Account: {p.email}")
        except NLMError as e:
            console.print(f"[red]✗[/red] Authentication failed: {e.message}")
            if e.hint:
                console.print(f"[dim]{e.hint}[/dim]")
            raise typer.Exit(2)
        return
    
    if manual:
        # Manual mode - read from file
        if not cookie_file:
            cookie_file = typer.prompt(
                "Enter path to file containing cookies",
                default="~/.nlm/cookies.txt",
            )
        try:
            profile_obj = auth.login_with_file(cookie_file)
            console.print(f"[green]✓[/green] Successfully authenticated!")
            console.print(f"  Profile saved: {profile}")
            console.print(f"  Credentials saved to: {auth.profile_dir}")
        except NLMError as e:
            console.print(f"[red]Error:[/red] {e.message}")
            if e.hint:
                console.print(f"\n[dim]Hint: {e.hint}[/dim]")
            raise typer.Exit(1)
        return
    
    # Default: CDP mode - Chrome DevTools Protocol
    console.print("[bold]Launching Chrome for authentication...[/bold]")
    console.print("[dim]Using Chrome DevTools Protocol[/dim]\n")
    
    try:
        from notebooklm_tools.utils.cdp import extract_cookies_via_cdp, terminate_chrome
        
        console.print("Starting Chrome...")
        result = extract_cookies_via_cdp(
            auto_launch=True,
            wait_for_login=True,
            login_timeout=300,
        )
        
        cookies = result["cookies"]
        csrf_token = result.get("csrf_token", "")
        session_id = result.get("session_id", "")
        
        # Save to profile
        auth.save_profile(
            cookies=cookies,
            csrf_token=csrf_token,
            session_id=session_id,
        )
        
        # Close Chrome to release profile lock (enables headless auth later)
        console.print("[dim]Closing Chrome...[/dim]")
        terminate_chrome()
        
        console.print(f"\n[green]✓[/green] Successfully authenticated!")
        console.print(f"  Profile: {profile}")
        console.print(f"  Cookies: {len(cookies)} extracted")
        console.print(f"  CSRF Token: {'Yes' if csrf_token else 'No (will be auto-extracted)'}")
        console.print(f"  Credentials saved to: {auth.profile_dir}")
        
    except NLMError as e:
        console.print(f"\n[red]Error:[/red] {e.message}")
        if e.hint:
            console.print(f"\\n[dim]Hint: {e.hint}[/dim]")
        raise typer.Exit(1)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-v",
        help="Show version and exit",
    ),
    ai: bool = typer.Option(
        False, "--ai",
        help="Output AI-friendly documentation for this CLI",
    ),
) -> None:
    """
    NLM - Command-line interface for Google NotebookLM.
    
    Use 'nlm <command> --help' for help on specific commands.
    """
    if version:
        console.print(f"nlm version {__version__}")
        raise typer.Exit()
    
    if ai:
        from notebooklm_tools.cli.ai_docs import print_ai_docs
        print_ai_docs()
        raise typer.Exit()
    
    # Show help if no command provided
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


def cli_main():
    """Main CLI entry point with error handling."""
    try:
        app()
    except Exception as e:
        # Import here to avoid circular dependencies
        from notebooklm_tools.core.exceptions import (
            AuthenticationError,
            ClientAuthenticationError,
            NLMError,
        )

        # Handle authentication errors cleanly
        if isinstance(e, (AuthenticationError, ClientAuthenticationError)):
            console.print(f"\n[red]✗ Authentication Error[/red]")
            console.print(f"  {str(e)}")
            console.print(f"\n[yellow]→[/yellow] Run [cyan]nlm login[/cyan] or [cyan]notebooklm-mcp-auth[/cyan] to re-authenticate\n")
            raise typer.Exit(1)

        # Handle other NLM errors cleanly
        elif isinstance(e, NLMError):
            console.print(f"\n[red]✗ Error:[/red] {e.message}")
            if e.hint:
                console.print(f"[dim]{e.hint}[/dim]\n")
            raise typer.Exit(1)

        # For unexpected errors, show the traceback
        else:
            raise


if __name__ == "__main__":
    cli_main()
