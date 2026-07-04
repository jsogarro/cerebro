"""
Output formatting utilities for Research Platform CLI.
"""

import json
from datetime import datetime

import yaml  # type: ignore[import-untyped]
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from tabulate import tabulate  # type: ignore[import-untyped]

from src.models.research_project import (
    ResearchProgress,
    ResearchProject,
    ResearchStatus,
)

console = Console()


class OutputFormatter:
    """Format output for different display modes."""

    def __init__(self, format_type: str = "table", color: bool = True):
        """Initialize formatter."""
        self.format_type = format_type
        self.color = color
        console.no_color = not color

    def format_project(self, project: ResearchProject) -> str:
        """Format a single project."""
        if self.format_type == "json":
            return json.dumps(project.model_dump(), indent=2, default=str)
        elif self.format_type == "yaml":
            result: str = yaml.dump(
                project.model_dump(), default_flow_style=False, default=str
            )
            return result
        elif self.format_type == "csv":
            return self._project_to_csv(project)
        else:  # table
            return self._project_to_table(project)

    def format_projects_list(self, projects: list[ResearchProject]) -> str:
        """Format a list of projects."""
        if not projects:
            return "No projects found."

        if self.format_type == "json":
            return json.dumps(
                [p.model_dump() for p in projects],
                indent=2,
                default=str,
            )
        elif self.format_type == "yaml":
            result: str = yaml.dump(
                [p.model_dump() for p in projects],
                default_flow_style=False,
                default=str,
            )
            return result
        elif self.format_type == "csv":
            return self._projects_to_csv(projects)
        else:  # table
            return self._projects_to_list_table(projects)

    def format_progress(self, progress: ResearchProgress) -> str:
        """Format project progress."""
        if self.format_type == "json":
            return json.dumps(progress.model_dump(), indent=2, default=str)
        elif self.format_type == "yaml":
            result: str = yaml.dump(
                progress.model_dump(), default_flow_style=False, default=str
            )
            return result
        else:  # table or csv
            return self._progress_to_display(progress)

    def format_error(self, error: Exception) -> str:
        """Format error message."""
        if self.format_type == "json":
            return json.dumps({"error": str(error)}, indent=2)
        else:
            return f"[red]Error: {error}[/red]" if self.color else f"Error: {error}"

    def _project_to_table(self, project: ResearchProject) -> str:
        """Convert project to rich table display."""
        table = Table(show_header=False, box=None)
        table.add_column("Field", style="cyan", width=20)
        table.add_column("Value")

        # Add project details
        table.add_row("ID", str(project.id))
        table.add_row("Title", project.title)
        table.add_row("Status", self._format_status(project.status))
        table.add_row("User ID", project.user_id)
        table.add_row("Created", self._format_datetime(project.created_at))

        if project.started_at:
            table.add_row("Started", self._format_datetime(project.started_at))

        if project.completed_at:
            table.add_row("Completed", self._format_datetime(project.completed_at))

        # Query information
        table.add_row("Query", project.query.text)
        table.add_row("Domains", ", ".join(project.query.domains))
        table.add_row("Depth Level", project.query.depth_level)

        # Progress information
        if project.plan:
            table.add_row("Tasks", str(len(project.plan.tasks)))

        console.print(Panel(table, title="Research Project", border_style="blue"))
        return ""

    def _projects_to_list_table(self, projects: list[ResearchProject]) -> str:
        """Convert projects list to rich table."""
        table = Table(title="Research Projects")

        table.add_column("ID", style="cyan", width=36)
        table.add_column("Title", style="magenta")
        table.add_column("Status", width=12)
        table.add_column("Created", width=19)
        table.add_column("User", width=15)

        for project in projects:
            table.add_row(
                str(project.id),
                (
                    project.title[:50] + "..."
                    if len(project.title) > 50
                    else project.title
                ),
                self._format_status(project.status),
                self._format_datetime(project.created_at),
                (
                    project.user_id[:12] + "..."
                    if len(project.user_id) > 12
                    else project.user_id
                ),
            )

        console.print(table)
        return ""

    def _progress_to_display(self, progress: ResearchProgress) -> str:
        """Convert progress to visual display."""
        # Create progress bar
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress_bar:
            _task = progress_bar.add_task(
                f"Research Progress: {progress.progress_percentage:.1f}%",
                total=progress.total_tasks,
                completed=progress.completed_tasks,
            )

        # Create stats table
        table = Table(show_header=False, box=None)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="yellow")

        table.add_row("Total Tasks", str(progress.total_tasks))
        table.add_row("Completed", str(progress.completed_tasks))
        table.add_row("In Progress", str(progress.in_progress_tasks))
        table.add_row("Pending", str(progress.pending_tasks))
        table.add_row("Failed", str(progress.failed_tasks))
        table.add_row("Progress", f"{progress.progress_percentage:.1f}%")

        if progress.estimated_time_remaining_seconds:
            minutes = progress.estimated_time_remaining_seconds // 60
            table.add_row("Est. Time Remaining", f"{minutes} minutes")

        console.print(Panel(table, title="Progress Details", border_style="green"))

        # Show current activities
        if progress.current_agent_activities:
            console.print("\n[bold]Current Agent Activities:[/bold]")
            for activity in progress.current_agent_activities:
                console.print(
                    f"  • {activity.get('agent', 'Unknown')}: {activity.get('task', 'Working...')}"
                )

        return ""

    def _project_to_csv(self, project: ResearchProject) -> str:
        """Convert project to CSV format."""
        headers = ["ID", "Title", "Status", "User ID", "Created", "Query", "Domains"]
        values = [
            str(project.id),
            project.title,
            project.status.value,
            project.user_id,
            self._format_datetime(project.created_at),
            project.query.text,
            ";".join(project.query.domains),
        ]

        result: str = tabulate([values], headers=headers, tablefmt="csv")
        return result

    def _projects_to_csv(self, projects: list[ResearchProject]) -> str:
        """Convert projects list to CSV format."""
        headers = ["ID", "Title", "Status", "User ID", "Created", "Query", "Domains"]
        rows = []

        for project in projects:
            rows.append(
                [
                    str(project.id),
                    project.title,
                    project.status.value,
                    project.user_id,
                    self._format_datetime(project.created_at),
                    project.query.text,
                    ";".join(project.query.domains),
                ]
            )

        result: str = tabulate(rows, headers=headers, tablefmt="csv")
        return result

    def _format_status(self, status: ResearchStatus) -> str:
        """Format status with color."""
        if not self.color:
            return status.value

        color_map = {
            ResearchStatus.PENDING: "yellow",
            ResearchStatus.PLANNING: "cyan",
            ResearchStatus.IN_PROGRESS: "blue",
            ResearchStatus.COMPLETED: "green",
            ResearchStatus.FAILED: "red",
            ResearchStatus.CANCELLED: "dim",
        }

        color = color_map.get(status, "white")
        return f"[{color}]{status.value}[/{color}]"

    def _format_datetime(self, dt: datetime) -> str:
        """Format datetime for display."""
        return dt.strftime("%Y-%m-%d %H:%M:%S")


def create_spinner(message: str) -> Progress:
    """Create a spinner for long-running operations."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    )


def print_success(message: str) -> None:
    """Print success message."""
    console.print(f"[green]✓[/green] {message}")


def print_error(message: str) -> None:
    """Print error message."""
    console.print(f"[red]✗[/red] {message}")


def print_warning(message: str) -> None:
    """Print warning message."""
    console.print(f"[yellow]⚠[/yellow] {message}")


def print_info(message: str) -> None:
    """Print info message."""
    console.print(f"[blue]ℹ[/blue] {message}")  # noqa: RUF001
