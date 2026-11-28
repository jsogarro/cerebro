"""Tenant-scoped storage for operator-registered execution authorities."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.services.execution_authority_resolver import (
    serialize_execution_authority_binding,
)
from src.models.db.execution_authority import AgentExecutionAuthority
from src.models.execution_authority import ExecutionAuthorityBinding
from src.repositories.run_config_snapshot_repository import hash_configuration
from src.repositories.tenant_scope import (
    enforce_tenant_identity,
    normalize_organization_id,
)


class ExecutionAuthorityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        binding: ExecutionAuthorityBinding,
        *,
        organization_id: uuid.UUID | str,
        is_template: bool = False,
    ) -> AgentExecutionAuthority:
        """Store a binding under the tenant that owns it; flushes, never commits."""
        organization = normalize_organization_id(organization_id)
        enforce_tenant_identity(
            tenant_id=binding.run.tenant_id, organization_id=organization
        )
        configuration = serialize_execution_authority_binding(binding)
        row = AgentExecutionAuthority(
            organization_id=organization,
            authority_id=binding.authority_id,
            authority_version=binding.authority_version,
            configuration=configuration,
            content_sha256=hash_configuration(configuration),
            is_template=is_template,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(
        self,
        *,
        authority_id: str,
        authority_version: str,
        organization_id: uuid.UUID | str,
        require_template: bool = False,
    ) -> AgentExecutionAuthority | None:
        query = select(AgentExecutionAuthority).where(
            AgentExecutionAuthority.authority_id == authority_id,
            AgentExecutionAuthority.authority_version == authority_version,
            AgentExecutionAuthority.organization_id
            == normalize_organization_id(organization_id),
        )
        if require_template:
            query = query.where(AgentExecutionAuthority.is_template.is_(True))
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_template(
        self,
        *,
        authority_id: str,
        authority_version: str,
        organization_id: uuid.UUID | str,
    ) -> AgentExecutionAuthority | None:
        return await self.get(
            authority_id=authority_id,
            authority_version=authority_version,
            organization_id=organization_id,
            require_template=True,
        )
