import typer
from typing import Optional
from pathlib import Path
from rich.console import Console
from rich.spinner import Spinner
from rich.live import Live
from notebooklm_tools.core.client import NotebookLMClient, ArtifactNotReadyError, ArtifactError
from notebooklm_tools.cli.utils import get_client, handle_error

app = typer.Typer(help="Download artifacts from notebooks.")
console = Console()


def download_with_spinner(
    download_func,
    description: str,
    show_spinner: bool = True
):
    """Wrapper to show spinner for downloads.

    Args:
        download_func: Function that performs the download (should return path)
        description: Description to show
        show_spinner: Whether to show spinner

    Returns:
        Path to downloaded file
    """
    if not show_spinner:
        return download_func()

    spinner = Spinner("dots", text=description)
    with Live(spinner, console=console, transient=True):
        result = download_func()

    return result

@app.command("audio")
def download_audio(
    notebook_id: str = typer.Argument(..., help="Notebook ID"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output path (default: ./{notebook_id}_audio.m4a)"),
    artifact_id: Optional[str] = typer.Option(None, "--id", help="Specific artifact ID"),
    no_spinner: bool = typer.Option(False, "--no-spinner", help="Disable download spinner")
):
    """Download Audio Overview."""
    client = get_client()
    try:
        path = output or f"{notebook_id}_audio.m4a"
        saved = download_with_spinner(
            lambda: client.download_audio(notebook_id, path, artifact_id),
            "Downloading audio overview...",
            show_spinner=not no_spinner
        )
        console.print(f"[green]✓[/green] Downloaded audio to: {saved}")
    except ArtifactNotReadyError:
        console.print("[red]Error:[/red] Audio Overview is not ready or does not exist.", err=True)
        raise typer.Exit(1)
    except Exception as e:
        handle_error(e)

@app.command("video")
def download_video(
    notebook_id: str = typer.Argument(..., help="Notebook ID"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output path (default: ./{notebook_id}_video.mp4)"),
    artifact_id: Optional[str] = typer.Option(None, "--id", help="Specific artifact ID"),
    no_spinner: bool = typer.Option(False, "--no-spinner", help="Disable download spinner")
):
    """Download Video Overview."""
    client = get_client()
    try:
        path = output or f"{notebook_id}_video.mp4"
        saved = download_with_spinner(
            lambda: client.download_video(notebook_id, path, artifact_id),
            "Downloading video overview...",
            show_spinner=not no_spinner
        )
        console.print(f"[green]✓[/green] Downloaded video to: {saved}")
    except ArtifactNotReadyError:
        console.print("[red]Error:[/red] Video Overview is not ready or does not exist.", err=True)
        raise typer.Exit(1)
    except Exception as e:
        handle_error(e)

@app.command("report")
def download_report(
    notebook_id: str = typer.Argument(..., help="Notebook ID"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output path (default: ./{notebook_id}_report.md)"),
    artifact_id: Optional[str] = typer.Option(None, "--id", help="Specific artifact ID")
):
    """Download Report (Markdown)."""
    client = get_client()
    try:
        path = output or f"{notebook_id}_report.md"
        saved = client.download_report(notebook_id, path, artifact_id)
        typer.echo(f"Downloaded report to: {saved}")
    except ArtifactNotReadyError:
        typer.echo("Error: Report is not ready or does not exist.", err=True)
        raise typer.Exit(1)
    except Exception as e:
        handle_error(e)

@app.command("mind-map")
def download_mind_map(
    notebook_id: str = typer.Argument(..., help="Notebook ID"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output path (default: ./{notebook_id}_mindmap.json)"),
    artifact_id: Optional[str] = typer.Option(None, "--id", help="Specific artifact ID (note ID)")
):
    """Download Mind Map (JSON)."""
    client = get_client()
    try:
        path = output or f"{notebook_id}_mindmap.json"
        saved = client.download_mind_map(notebook_id, path, artifact_id)
        typer.echo(f"Downloaded mind map to: {saved}")
    except ArtifactNotReadyError:
        typer.echo("Error: Mind map is not ready or does not exist.", err=True)
        raise typer.Exit(1)
    except Exception as e:
        handle_error(e)

@app.command("slide-deck")
def download_slide_deck(
    notebook_id: str = typer.Argument(..., help="Notebook ID"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output path (default: ./{notebook_id}_slides.pdf)"),
    artifact_id: Optional[str] = typer.Option(None, "--id", help="Specific artifact ID"),
    no_spinner: bool = typer.Option(False, "--no-spinner", help="Disable download spinner")
):
    """Download Slide Deck (PDF)."""
    client = get_client()
    try:
        path = output or f"{notebook_id}_slides.pdf"
        saved = download_with_spinner(
            lambda: client.download_slide_deck(notebook_id, path, artifact_id),
            "Downloading slide deck...",
            show_spinner=not no_spinner
        )
        console.print(f"[green]✓[/green] Downloaded slide deck to: {saved}")
    except ArtifactNotReadyError:
        console.print("[red]Error:[/red] Slide deck is not ready or does not exist.", err=True)
        raise typer.Exit(1)
    except Exception as e:
        handle_error(e)

@app.command("infographic")
def download_infographic(
    notebook_id: str = typer.Argument(..., help="Notebook ID"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output path (default: ./{notebook_id}_infographic.png)"),
    artifact_id: Optional[str] = typer.Option(None, "--id", help="Specific artifact ID"),
    no_spinner: bool = typer.Option(False, "--no-spinner", help="Disable download spinner")
):
    """Download Infographic (PNG)."""
    client = get_client()
    try:
        path = output or f"{notebook_id}_infographic.png"
        saved = download_with_spinner(
            lambda: client.download_infographic(notebook_id, path, artifact_id),
            "Downloading infographic...",
            show_spinner=not no_spinner
        )
        console.print(f"[green]✓[/green] Downloaded infographic to: {saved}")
    except ArtifactNotReadyError:
        console.print("[red]Error:[/red] Infographic is not ready or does not exist.", err=True)
        raise typer.Exit(1)
    except Exception as e:
        handle_error(e)

@app.command("data-table")
def download_data_table(
    notebook_id: str = typer.Argument(..., help="Notebook ID"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output path (default: ./{notebook_id}_table.csv)"),
    artifact_id: Optional[str] = typer.Option(None, "--id", help="Specific artifact ID")
):
    """Download Data Table (CSV)."""
    client = get_client()
    try:
        path = output or f"{notebook_id}_table.csv"
        saved = client.download_data_table(notebook_id, path, artifact_id)
        typer.echo(f"Downloaded data table to: {saved}")
    except ArtifactNotReadyError:
        typer.echo("Error: Data table is not ready or does not exist.", err=True)
        raise typer.Exit(1)
    except Exception as e:
        handle_error(e)
