"""Operator commands for execution authorities.

These write to the database directly, so they run where the application's
database settings are available, not against the HTTP API.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import click

from src.api.services.execution_authority_resolver import (
    deserialize_execution_authority_binding,
)
from src.cli.formatters import print_error, print_success
from src.models.execution_authority import ExecutionAuthorityBinding


@click.group(name="authority")
def authority_group() -> None:
    """Manage execution authorities (operator, direct database access)."""


def _load_binding(path: str) -> ExecutionAuthorityBinding:
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    return deserialize_execution_authority_binding(
        document["configuration"],
        authority_id=document["authority_id"],
        authority_version=document["authority_version"],
        clock=lambda: datetime.now(UTC),
        plan_id_factory=lambda: str(uuid.uuid4()),
    )


async def _store(
    binding: ExecutionAuthorityBinding,
    organization_id: str,
    *,
    is_template: bool = False,
) -> None:
    from src.middleware.tenant_context import set_postgres_tenant_context
    from src.models.db.session import close_db, get_session_factory, init_db
    from src.repositories.execution_authority_repository import (
        ExecutionAuthorityRepository,
    )

    await init_db()
    try:
        async with get_session_factory()() as session:
            await set_postgres_tenant_context(session, organization_id)
            await ExecutionAuthorityRepository(session).create(
                binding, organization_id=organization_id, is_template=is_template
            )
            await session.commit()
    finally:
        await close_db()


@authority_group.command(name="register")
@click.option(
    "--file",
    "path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="JSON with authority_id, authority_version and configuration",
)
@click.option("--organization-id", required=True, help="Tenant that owns the authority")
@click.option(
    "--template",
    "is_template",
    is_flag=True,
    default=False,
    help="Register as a reusable template for deterministic run minting",
)
def register_command(path: str, organization_id: str, is_template: bool) -> None:
    """Register an authority so runs can be admitted under it."""
    try:
        uuid.UUID(organization_id)
        binding = _load_binding(path)
        if is_template:
            if binding.deadline.tzinfo is None or binding.deadline.utcoffset() is None:
                raise ValueError("template deadline must be timezone-aware")
            if (binding.deadline - binding.run.created_at) <= timedelta(0):
                raise ValueError("template deadline must follow run.created_at")
    except (KeyError, ValueError, TypeError) as exc:
        print_error(f"Invalid authority: {exc}")
        raise click.exceptions.Exit(1) from exc
    asyncio.run(_store(binding, organization_id, is_template=is_template))
    print_success(f"Registered {binding.authority_id}@{binding.authority_version}")
