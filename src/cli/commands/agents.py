"""
Agent Framework CLI commands.
"""

import asyncio
from typing import Any

import click
from click.core import Context
from click.exceptions import Exit
from rich.table import Table

from src.cli.client import APIError, ResearchAPIClient
from src.cli.formatters import print_error, print_success


@click.group(name="agents")
def agents_group() -> None:
    """Agent Framework API commands."""
    pass


@agents_group.command(name="query")
@click.argument("query_text")
@click.option("--domains", "-d", multiple=True, help="Research domains")
@click.option(
    "--type",
    "-t",
    type=click.Choice(["research", "analyze", "synthesize"]),
    default="research",
    help="Query type",
)
@click.option("--authority-id", help="Trusted execution authority identifier")
@click.option("--authority-version", help="Trusted execution authority version")
@click.pass_context
def query_command(
    ctx: Context,
    query_text: str,
    domains: tuple[str, ...],
    type: str,
    authority_id: str | None,
    authority_version: str | None,
) -> None:
    """Execute intelligent MASR-routed query."""
    client_verbose: bool = ctx.obj["verbose"]
    console: Any = ctx.obj["console"]

    async def _query() -> None:
        async with ResearchAPIClient(verbose=client_verbose) as client:
            try:
                endpoint = f"/api/v1/query/{type}"
                payload: dict[str, Any] = {"query": query_text}
                if domains:
                    payload["domains"] = list(domains)
                if authority_id is not None or authority_version is not None:
                    if not authority_id or not authority_version:
                        raise click.UsageError(
                            "--authority-id and --authority-version must be supplied together"
                        )
                    payload["authority_reference"] = {
                        "authority_id": authority_id,
                        "authority_version": authority_version,
                    }

                with console.status(
                    f"[bold green]Executing {type} query via MASR router..."
                ):
                    result = await client.post(endpoint, payload)

                print_success("Query completed successfully")
                console.print("\n[bold]Result:[/bold]")
                console.print(result.get("results", result))

            except APIError as e:
                print_error(f"Query failed: {e.detail}")
                raise Exit(1) from e

    asyncio.run(_query())


@agents_group.command(name="route")
@click.argument("query_text")
@click.option(
    "--strategy",
    "-s",
    type=click.Choice(["cost_efficient", "quality_focused", "balanced"]),
    default="balanced",
    help="Routing strategy",
)
@click.pass_context
def route_command(ctx: Context, query_text: str, strategy: str) -> None:
    """Get MASR routing decision with cost optimization."""
    client_verbose: bool = ctx.obj["verbose"]
    console: Any = ctx.obj["console"]

    async def _route() -> None:
        async with ResearchAPIClient(verbose=client_verbose) as client:
            try:
                payload = {"query": query_text, "strategy": strategy}

                with console.status("[bold green]Computing routing decision..."):
                    result = await client.post("/api/v1/masr/route", payload)

                print_success("Routing decision computed")

                table = Table(title="MASR Routing Decision")
                table.add_column("Property", style="cyan")
                table.add_column("Value", style="green")

                models = ", ".join(
                    m["model_id"] for m in result.get("selected_models", [])
                )
                table.add_row("Selected Models", models or "N/A")
                table.add_row(
                    "Estimated Cost", f"${result.get('estimated_cost', 0):.4f}"
                )
                table.add_row("Strategy", result.get("strategy", "N/A"))
                supervisors = ", ".join(
                    a["supervisor_type"]
                    for a in result.get("supervisor_allocations", [])
                )
                table.add_row("Supervisors", supervisors)

                console.print(table)

            except APIError as e:
                print_error(f"Routing failed: {e.detail}")
                raise Exit(1) from e

    asyncio.run(_route())


@agents_group.command(name="estimate")
@click.argument("query_text")
@click.option("--domains", "-d", multiple=True, help="Research domains")
@click.pass_context
def estimate_command(ctx: Context, query_text: str, domains: tuple[str, ...]) -> None:
    """Estimate execution cost with detailed breakdown."""
    client_verbose: bool = ctx.obj["verbose"]
    console: Any = ctx.obj["console"]

    async def _estimate() -> None:
        async with ResearchAPIClient(verbose=client_verbose) as client:
            try:
                payload: dict[str, Any] = {"query": query_text}
                if domains:
                    payload["domains"] = list(domains)

                with console.status("[bold green]Estimating cost..."):
                    result = await client.post("/api/v1/masr/estimate-cost", payload)

                print_success("Cost estimation completed")

                table = Table(title="Cost Estimation")
                table.add_column("Component", style="cyan")
                table.add_column("Cost", style="green", justify="right")

                breakdown = result.get("breakdown") or {}
                table.add_row(
                    "Model Inference", f"${breakdown.get('model_costs', 0):.4f}"
                )
                table.add_row(
                    "Agent Coordination",
                    f"${breakdown.get('coordination_overhead', 0):.4f}",
                )
                table.add_row(
                    "[bold]Total Estimate[/bold]",
                    f"[bold]${result.get('estimated_cost', 0):.4f}[/bold]",
                )

                console.print(table)

            except APIError as e:
                print_error(f"Estimation failed: {e.detail}")
                raise Exit(1) from e

    asyncio.run(_estimate())


@agents_group.command(name="execute")
@click.argument("agent_type")
@click.argument("query_text")
@click.option("--max-sources", type=int, help="Maximum sources (for literature-review)")
@click.option("--authority-id", help="Trusted execution authority identifier")
@click.option("--authority-version", help="Trusted execution authority version")
@click.pass_context
def execute_command(
    ctx: Context,
    agent_type: str,
    query_text: str,
    max_sources: int | None,
    authority_id: str | None,
    authority_version: str | None,
) -> None:
    """Direct agent execution (bypass MASR routing)."""
    client_verbose: bool = ctx.obj["verbose"]
    console: Any = ctx.obj["console"]

    async def _execute() -> None:
        async with ResearchAPIClient(verbose=client_verbose) as client:
            try:
                endpoint = f"/api/v1/agents/{agent_type}/execute"
                payload: dict[str, Any] = {"query": query_text, "parameters": {}}
                if max_sources:
                    payload["parameters"]["max_sources"] = max_sources
                if authority_id is not None or authority_version is not None:
                    if not authority_id or not authority_version:
                        raise click.UsageError(
                            "--authority-id and --authority-version must be supplied together"
                        )
                    payload["authority_reference"] = {
                        "authority_id": authority_id,
                        "authority_version": authority_version,
                    }

                with console.status(f"[bold green]Executing {agent_type} agent..."):
                    result = await client.post(endpoint, payload)

                print_success(f"Agent {agent_type} completed successfully")
                console.print("\n[bold]Result:[/bold]")
                console.print(result.get("results", result))

            except APIError as e:
                print_error(f"Agent execution failed: {e.detail}")
                raise Exit(1) from e

    asyncio.run(_execute())


@agents_group.command(name="chain")
@click.argument("query_text")
@click.option(
    "--agents", "-a", multiple=True, required=True, help="Agent chain (in order)"
)
@click.option("--authority-id", help="Trusted execution authority identifier")
@click.option("--authority-version", help="Trusted execution authority version")
@click.pass_context
def chain_command(
    ctx: Context,
    query_text: str,
    agents: tuple[str, ...],
    authority_id: str | None,
    authority_version: str | None,
) -> None:
    """Execute Chain-of-Agents workflow."""
    client_verbose: bool = ctx.obj["verbose"]
    console: Any = ctx.obj["console"]

    async def _chain() -> None:
        async with ResearchAPIClient(verbose=client_verbose) as client:
            try:
                payload: dict[str, Any] = {
                    "query": query_text,
                    "agent_chain": list(agents),
                }
                if authority_id is not None or authority_version is not None:
                    if not authority_id or not authority_version:
                        raise click.UsageError(
                            "--authority-id and --authority-version must be supplied together"
                        )
                    payload["authority_reference"] = {
                        "authority_id": authority_id,
                        "authority_version": authority_version,
                    }

                with console.status("[bold green]Executing agent chain..."):
                    result = await client.post("/api/v1/agents/chain", payload)

                print_success("Agent chain completed successfully")
                console.print("\n[bold]Result:[/bold]")
                console.print(result.get("results", result))

            except APIError as e:
                print_error(f"Chain execution failed: {e.detail}")
                raise Exit(1) from e

    asyncio.run(_chain())


@agents_group.command(name="status")
@click.pass_context
def status_command(ctx: Context) -> None:
    """Get MASR router health and performance metrics."""
    client_verbose: bool = ctx.obj["verbose"]
    console: Any = ctx.obj["console"]

    async def _status() -> None:
        async with ResearchAPIClient(verbose=client_verbose) as client:
            try:
                result = await client.get("/api/v1/masr/status")

                print_success("MASR router is operational")

                table = Table(title="MASR Router Status")
                table.add_column("Metric", style="cyan")
                table.add_column("Value", style="green")

                table.add_row("Status", result.get("status", "N/A"))
                table.add_row("Total Routes", str(result.get("total_routes", 0)))
                table.add_row(
                    "Avg Latency", f"{result.get('average_latency_ms', 0):.1f}ms"
                )
                table.add_row("Success Rate", f"{result.get('success_rate', 0):.1%}")

                console.print(table)

            except APIError as e:
                print_error(f"Status check failed: {e.detail}")
                raise Exit(1) from e

    asyncio.run(_status())
