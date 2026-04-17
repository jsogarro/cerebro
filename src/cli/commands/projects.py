"""
Research project commands for CLI.
"""


import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

import click
from click.core import Context
from click.exceptions import Exit
from rich.console import Console
from rich.live import Live
from rich.panel import Panel

from src.cli.client import APIError, ResearchAPIClient
from src.cli.config import config
from src.cli.formatters import (
    create_spinner,
    print_error,
    print_info,
    print_success,
    print_warning,
)
from src.cli.utils import (
    load_projects_from_file,
    parse_key_value_pairs,
    prompt_for_project_details,
    save_results_to_file,
    validate_domains,
    validate_uuid,
)
from src.cli.websocket_client import stream_project_progress, test_websocket_connection
from src.models.research_project import ResearchProgress, ResearchProject


@click.group(name="projects")
@click.pass_context
def projects_group(ctx: Context) -> None:
    """Manage research projects."""
    pass


@projects_group.command(name="create")
@click.option("--title", "-t", help="Project title")
@click.option("--query", "-q", help="Research query text")
@click.option("--domains", "-d", help="Research domains (comma-separated)")
@click.option("--user-id", "-u", default="cli-user", help="User ID")
@click.option(
    "--depth",
    type=click.Choice(["survey", "comprehensive", "exhaustive"]),
    default="comprehensive",
    help="Research depth level",
)
@click.option("--scope", "-s", multiple=True, help="Scope parameters (key=value)")
@click.option("--interactive", "-i", is_flag=True, help="Interactive mode")
@click.option("--file", "-f", type=click.Path(exists=True), help="Load from file")
@click.pass_context
def create_project(
    ctx: Context,
    title: str | None,
    query: str | None,
    domains: str | None,
    user_id: str,
    depth: str,
    scope: tuple[str, ...],
    interactive: bool,
    file: str | None,
) -> None:
    """Create a new research project."""
    formatter = ctx.obj["formatter"]
    verbose = ctx.obj["verbose"]

    async def _create_single_project(project_data: dict[str, Any]) -> ResearchProject:
        """Create a single project."""
        async with ResearchAPIClient(verbose=verbose) as client:
            try:
                with create_spinner("Creating research project..."):
                    project = await client.create_project(
                        title=project_data["title"],
                        query_text=project_data["query_text"],
                        domains=project_data["domains"],
                        user_id=project_data["user_id"],
                        depth_level=project_data.get("depth_level", "comprehensive"),
                        scope=project_data.get("scope"),
                    )

                print_success(f"Created project: {project.id}")
                output = formatter.format_project(project)
                if output:
                    click.echo(output)

                return project

            except APIError as e:
                print_error(f"Failed to create project: {e.detail}")
                raise Exit(1) from e

    async def _create_projects() -> None:
        """Create projects based on input."""
        # Capture outer variables
        nonlocal title, query, domains, user_id, depth, scope, interactive, file

        if file:
            # Batch mode from file
            file_path = Path(file)
            projects_data = load_projects_from_file(file_path)

            print_info(f"Creating {len(projects_data)} project(s) from {file_path}")

            created = []
            failed = []

            for i, project_data in enumerate(projects_data, 1):
                print_info(
                    f"Creating project {i}/{len(projects_data)}: {project_data['title']}"
                )
                try:
                    project = await _create_single_project(project_data)
                    created.append(project)
                except Exception as e:
                    failed.append((project_data["title"], str(e)))

            # Summary
            print_info(f"\nSummary: {len(created)} created, {len(failed)} failed")
            if failed:
                print_warning("Failed projects:")
                for proj_title, error in failed:
                    click.echo(f"  - {proj_title}: {error}")

        elif interactive:
            # Interactive mode
            project_data = prompt_for_project_details()
            await _create_single_project(project_data)

        else:
            # Command line mode
            if not title or not query or not domains:
                print_error(
                    "Missing required arguments. Use --interactive or provide --title, --query, and --domains"
                )
                raise Exit(1)

            project_data = {
                "title": title,
                "query_text": query,
                "domains": validate_domains(domains),
                "user_id": user_id,
                "depth_level": depth,
                "scope": parse_key_value_pairs(list(scope)) if scope else None,
            }

            await _create_single_project(project_data)

    asyncio.run(_create_projects())


@projects_group.command(name="get")
@click.argument("project_id", callback=validate_uuid)
@click.pass_context
def get_project(ctx: Context, project_id: UUID) -> None:
    """Get project details."""
    formatter = ctx.obj["formatter"]
    verbose = ctx.obj["verbose"]

    async def _get_project() -> None:
        async with ResearchAPIClient(verbose=verbose) as client:
            try:
                with create_spinner(f"Fetching project {project_id}..."):
                    project = await client.get_project(project_id)

                output = formatter.format_project(project)
                if output:
                    click.echo(output)

            except APIError as e:
                if e.status_code == 404:
                    print_error(f"Project not found: {project_id}")
                else:
                    print_error(f"Failed to fetch project: {e.detail}")
                raise Exit(1) from e

    asyncio.run(_get_project())


@projects_group.command(name="list")
@click.option("--user-id", "-u", help="Filter by user ID")
@click.option("--status", "-s", help="Filter by status")
@click.option("--limit", "-l", default=10, help="Maximum number of results")
@click.option("--offset", "-o", default=0, help="Offset for pagination")
@click.pass_context
def list_projects(
    ctx: Context,
    user_id: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> None:
    """List research projects."""
    formatter = ctx.obj["formatter"]
    verbose = ctx.obj["verbose"]

    async def _list_projects() -> None:
        async with ResearchAPIClient(verbose=verbose) as client:
            try:
                with create_spinner("Fetching projects..."):
                    projects = await client.list_projects(
                        user_id=user_id,
                        status=status,
                        limit=limit,
                        offset=offset,
                    )

                if not projects:
                    print_info("No projects found")
                    return

                output = formatter.format_projects_list(projects)
                if output:
                    click.echo(output)

                if len(projects) == limit:
                    print_info(f"Showing {limit} results. Use --offset to see more.")

            except APIError as e:
                print_error(f"Failed to list projects: {e.detail}")
                raise Exit(1) from e

    asyncio.run(_list_projects())


@projects_group.command(name="progress")
@click.argument("project_id", callback=validate_uuid)
@click.option(
    "--watch", "-w", is_flag=True, help="Watch progress in real-time (polling mode)"
)
@click.option(
    "--stream", "-s", is_flag=True, help="Stream progress via WebSocket (real-time)"
)
@click.option(
    "--interval", "-i", default=5, help="Update interval in seconds (polling mode only)"
)
@click.pass_context
def get_progress(ctx: Context, project_id: UUID, watch: bool, stream: bool, interval: int) -> None:
    """Get project progress."""
    formatter = ctx.obj["formatter"]
    verbose = ctx.obj["verbose"]
    console = Console()

    # Validate options
    if watch and stream:
        print_error("Cannot use both --watch and --stream options. Choose one.")
        raise Exit(1)

    async def _get_progress_once() -> ResearchProgress:
        """Get progress once."""
        async with ResearchAPIClient(verbose=verbose) as client:
            try:
                progress = await client.get_project_progress(project_id)
                output = formatter.format_progress(progress)
                if output:
                    click.echo(output)
                return progress
            except APIError as e:
                if e.status_code == 404:
                    print_error(f"Project not found: {project_id}")
                else:
                    print_error(f"Failed to fetch progress: {e.detail}")
                raise Exit(1) from e

    async def _stream_progress() -> None:
        """Stream progress via WebSocket."""
        try:
            # Test WebSocket connection first
            if verbose:
                print_info("Testing WebSocket connection...")
                connection_ok = await test_websocket_connection(
                    token=config.auth_token,
                    verbose=verbose,
                )
                if not connection_ok:
                    print_warning(
                        "WebSocket connection test failed, falling back to polling mode"
                    )
                    await _watch_progress()
                    return

            # Stream progress via WebSocket
            success = await stream_project_progress(
                project_id=project_id,
                formatter=formatter,
                token=config.auth_token,
                verbose=verbose,
            )

            if not success:
                print_warning(
                    "WebSocket streaming failed, falling back to polling mode"
                )
                await _watch_progress()

        except Exception as e:
            if verbose:
                print_error(f"WebSocket streaming error: {e}")
            print_warning("Falling back to polling mode")
            await _watch_progress()

    async def _watch_progress() -> None:
        """Watch progress in real-time (polling mode)."""
        async with ResearchAPIClient(verbose=verbose) as client:
            print_info(f"Watching progress for project {project_id} (Ctrl+C to stop)")
            if not stream:  # Only show this if not falling back from streaming
                print_info("💡 Tip: Use --stream for real-time WebSocket updates")

            with Live(console=console, refresh_per_second=1) as live:
                while True:
                    try:
                        progress = await client.get_project_progress(project_id)

                        # Create progress display
                        output = formatter.format_progress(progress)
                        live.update(
                            Panel(
                                (
                                    output
                                    if output
                                    else f"Progress: {progress.progress_percentage:.1f}%"
                                ),
                                title=f"Project {project_id}",
                                border_style="green",
                            )
                        )

                        # Check if completed
                        if progress.progress_percentage >= 100:
                            print_success("Project completed!")
                            break

                        await asyncio.sleep(interval)

                    except KeyboardInterrupt:
                        print_info("Stopped watching")
                        break
                    except APIError as e:
                        print_error(f"Error fetching progress: {e.detail}")
                        await asyncio.sleep(interval)

    # Execute based on options
    if stream:
        asyncio.run(_stream_progress())
    elif watch:
        asyncio.run(_watch_progress())
    else:
        asyncio.run(_get_progress_once())


@projects_group.command(name="cancel")
@click.argument("project_id", callback=validate_uuid)
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
@click.pass_context
def cancel_project(ctx: Context, project_id: UUID, force: bool) -> None:
    """Cancel a research project."""
    verbose = ctx.obj["verbose"]

    if not force and not click.confirm(f"Are you sure you want to cancel project {project_id}?"):
        print_info("Cancelled")
        return

    async def _cancel_project() -> None:
        async with ResearchAPIClient(verbose=verbose) as client:
            try:
                with create_spinner(f"Cancelling project {project_id}..."):
                    await client.cancel_project(project_id)

                print_success(f"Project {project_id} cancelled")

            except APIError as e:
                if e.status_code == 404:
                    print_error(f"Project not found: {project_id}")
                else:
                    print_error(f"Failed to cancel project: {e.detail}")
                raise Exit(1) from e

    asyncio.run(_cancel_project())


@projects_group.command(name="results")
@click.argument("project_id", callback=validate_uuid)
@click.option("--output", "-o", type=click.Path(), help="Save results to file")
@click.pass_context
def get_results(ctx: Context, project_id: UUID, output: str | None) -> None:
    """Get project results."""
    formatter = ctx.obj["formatter"]
    verbose = ctx.obj["verbose"]

    async def _get_results() -> None:
        async with ResearchAPIClient(verbose=verbose) as client:
            try:
                with create_spinner(f"Fetching results for project {project_id}..."):
                    results = await client.get_project_results(project_id)

                if output:
                    output_path = Path(output)
                    save_results_to_file(
                        results,
                        output_path,
                        format_type=formatter.format_type,
                    )
                    print_success(f"Results saved to {output_path}")
                else:
                    import json

                    click.echo(json.dumps(results, indent=2, default=str))

            except APIError as e:
                if e.status_code == 404:
                    print_error(f"Results not found for project: {project_id}")
                else:
                    print_error(f"Failed to fetch results: {e.detail}")
                raise Exit(1) from e

    asyncio.run(_get_results())


@projects_group.command(name="refine")
@click.argument("project_id", callback=validate_uuid)
@click.option("--scope", "-s", multiple=True, help="Scope parameters (key=value)")
@click.option("--max-sources", type=int, help="Maximum number of sources")
@click.option("--languages", help="Languages (comma-separated)")
@click.pass_context
def refine_scope(
    ctx: Context,
    project_id: UUID,
    scope: tuple[str, ...],
    max_sources: int | None,
    languages: str | None,
) -> None:
    """Refine project scope."""
    formatter = ctx.obj["formatter"]
    verbose = ctx.obj["verbose"]

    # Build scope dict
    scope_dict = parse_key_value_pairs(list(scope)) if scope else {}

    if max_sources:
        scope_dict["max_sources"] = max_sources

    if languages:
        scope_dict["languages"] = [l.strip() for l in languages.split(",")]

    if not scope_dict:
        print_error("No scope parameters provided")
        raise Exit(1)

    async def _refine_scope() -> None:
        async with ResearchAPIClient(verbose=verbose) as client:
            try:
                with create_spinner(f"Refining scope for project {project_id}..."):
                    project = await client.refine_project_scope(project_id, scope_dict)

                print_success(f"Scope refined for project {project_id}")
                output = formatter.format_project(project)
                if output:
                    click.echo(output)

            except APIError as e:
                print_error(f"Failed to refine scope: {e.detail}")
                raise Exit(1) from e

    asyncio.run(_refine_scope())
