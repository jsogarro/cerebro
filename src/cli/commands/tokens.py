"""Personal access token management commands for the CLI."""

from __future__ import annotations

import asyncio

import click
from click.core import Context
from click.exceptions import Exit

from src.auth.models import PAT_ALLOWED_SCOPES, PAT_MAX_LIFETIME_DAYS, PAT_SCOPE_READ
from src.cli.client import APIError, ResearchAPIClient
from src.cli.formatters import print_error, print_success


@click.group(name="tokens")
def tokens_group() -> None:
    """Manage personal access tokens for MCP clients."""


@tokens_group.command(name="create")
@click.option("--name", required=True, help="Human-readable label for the token")
@click.option(
    "--expires-in-days",
    type=click.IntRange(1, PAT_MAX_LIFETIME_DAYS),
    required=True,
    help=f"Lifetime in days (1-{PAT_MAX_LIFETIME_DAYS})",
)
@click.option(
    "--scope",
    "scopes",
    type=click.Choice(sorted(PAT_ALLOWED_SCOPES)),
    multiple=True,
    help="Token scopes (defaults to mcp:read)",
)
@click.pass_context
def create_token_command(
    ctx: Context,
    name: str,
    expires_in_days: int,
    scopes: tuple[str, ...],
) -> None:
    """Create a personal access token and display its secret once."""
    verbose = ctx.obj["verbose"]
    selected_scopes = list(scopes) if scopes else [PAT_SCOPE_READ]

    async def _run() -> None:
        async with ResearchAPIClient(verbose=verbose) as client:
            try:
                created = await client.create_personal_access_token(
                    name=name,
                    expires_in_days=expires_in_days,
                    scopes=selected_scopes,
                )
            except APIError as exc:
                print_error(f"Failed to create token: {exc.detail}")
                raise Exit(1) from exc
        print_success(f"Created personal access token {created['id']}")
        click.echo(f"Token (shown once): {created['token']}")
        click.echo(f"Expires at: {created['expires_at']}")
        click.echo(f"Scopes: {', '.join(created['scopes'])}")

    asyncio.run(_run())


@tokens_group.command(name="list")
@click.pass_context
def list_tokens_command(ctx: Context) -> None:
    """List personal access tokens without secrets."""
    verbose = ctx.obj["verbose"]

    async def _run() -> None:
        async with ResearchAPIClient(verbose=verbose) as client:
            try:
                tokens = await client.list_personal_access_tokens()
            except APIError as exc:
                print_error(f"Failed to list tokens: {exc.detail}")
                raise Exit(1) from exc
        if not tokens:
            click.echo("No personal access tokens found.")
            return
        for item in tokens:
            status_label = "revoked" if item.get("revoked_at") else "active"
            scopes_label = ",".join(item.get("scopes", []))
            click.echo(
                f"{item['id']}  {item['name']}  {item['token_prefix']}...  "
                f"[{scopes_label}]  expires={item['expires_at']}  ({status_label})"
            )

    asyncio.run(_run())


@tokens_group.command(name="revoke")
@click.argument("pat_id")
@click.pass_context
def revoke_token_command(ctx: Context, pat_id: str) -> None:
    """Revoke a personal access token by ID."""
    verbose = ctx.obj["verbose"]

    async def _run() -> None:
        async with ResearchAPIClient(verbose=verbose) as client:
            try:
                await client.revoke_personal_access_token(pat_id)
            except APIError as exc:
                print_error(f"Failed to revoke token: {exc.detail}")
                raise Exit(1) from exc
        print_success(f"Revoked personal access token {pat_id}")

    asyncio.run(_run())


__all__ = ["tokens_group"]
