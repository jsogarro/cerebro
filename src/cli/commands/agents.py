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
@click.pass_context
def query_command(ctx: Context, query_text: str, domains: tuple[str, ...], type: str) -> None:
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

                with console.status(f"[bold green]Executing {type} query via MASR router..."):
                    result = await client.post(endpoint, payload)

                print_success("Query completed successfully")
                console.print("\n[bold]Result:[/bold]")
                console.print(result.get("output", result))

            except APIError as e:
                print_error(f"Query failed: {e.detail}")
                raise Exit(1)

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

                table.add_row("Selected Model", result.get("selected_model", "N/A"))
                table.add_row("Estimated Cost", f"${result.get('estimated_cost', 0):.4f}")
                table.add_row("Strategy", result.get("strategy", "N/A"))
                table.add_row("Agent Chain", ", ".join(result.get("agent_chain", [])))

                console.print(table)

            except APIError as e:
                print_error(f"Routing failed: {e.detail}")
                raise Exit(1)

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

                table.add_row("Model Inference", f"${result.get('model_cost', 0):.4f}")
                table.add_row("Agent Coordination", f"${result.get('coordination_cost', 0):.4f}")
                table.add_row("[bold]Total Estimate[/bold]", f"[bold]${result.get('total_cost', 0):.4f}[/bold]")

                console.print(table)

            except APIError as e:
                print_error(f"Estimation failed: {e.detail}")
                raise Exit(1)

    asyncio.run(_estimate())


@agents_group.command(name="execute")
@click.argument("agent_type")
@click.argument("query_text")
@click.option("--max-sources", type=int, help="Maximum sources (for literature-review)")
@click.pass_context
def execute_command(ctx: Context, agent_type: str, query_text: str, max_sources: int | None) -> None:
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

                with console.status(f"[bold green]Executing {agent_type} agent..."):
                    result = await client.post(endpoint, payload)

                print_success(f"Agent {agent_type} completed successfully")
                console.print("\n[bold]Result:[/bold]")
                console.print(result.get("output", result))

            except APIError as e:
                print_error(f"Agent execution failed: {e.detail}")
                raise Exit(1)

    asyncio.run(_execute())


@agents_group.command(name="chain")
@click.argument("query_text")
@click.option("--agents", "-a", multiple=True, required=True, help="Agent chain (in order)")
@click.pass_context
def chain_command(ctx: Context, query_text: str, agents: tuple[str, ...]) -> None:
    """Execute Chain-of-Agents workflow."""
    client_verbose: bool = ctx.obj["verbose"]
    console: Any = ctx.obj["console"]

    async def _chain() -> None:
        async with ResearchAPIClient(verbose=client_verbose) as client:
            try:
                payload = {"query": query_text, "agent_chain": list(agents)}

                with console.status("[bold green]Executing agent chain..."):
                    result = await client.post("/api/v1/agents/chain", payload)

                print_success("Agent chain completed successfully")
                console.print("\n[bold]Result:[/bold]")
                console.print(result.get("output", result))

            except APIError as e:
                print_error(f"Chain execution failed: {e.detail}")
                raise Exit(1)

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
                table.add_row("Total Queries", str(result.get("total_queries", 0)))
                table.add_row("Avg Response Time", f"{result.get('avg_response_time', 0):.2f}s")
                table.add_row("Cost Savings", f"${result.get('cost_savings', 0):.2f}")

                console.print(table)

            except APIError as e:
                print_error(f"Status check failed: {e.detail}")
                raise Exit(1)

    asyncio.run(_status())