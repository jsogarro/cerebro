"""Repository for ``agent_artifacts``.

Follows the Wave 3 pattern: writes take the canonical ``Artifact`` contract
that already validated the producer/prompt-binding invariant — this
repository records its result, it never re-implements
``_validate_producer_binding``. Rows are mutable (``BaseModel``): unlike
``EvidenceRepository``/``ClaimSupportRepository``, a status transition
updates the existing row rather than appending a new one, mirroring
``RunLifecycleRepository``.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import Artifact, ArtifactStatus
from src.models.db.artifact import AgentArtifact
from src.repositories.tenant_scope import (
    TenantMismatchError,
    normalize_organization_id,
    scope_to_organization,
)

OrganizationId = uuid.UUID | str | None


class ArtifactRepository:
    """Durable persistence for ``agent_artifacts``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_artifact(
        self, artifact: Artifact, *, organization_id: OrganizationId
    ) -> AgentArtifact:
        """Insert a new artifact row from an admitted ``Artifact`` contract.

        Args:
            artifact: The validated artifact contract.
            organization_id: The authenticated tenant boundary.

        Returns:
            The persisted row.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        binding = artifact.prompt_binding
        # `JsonObject`'s `AfterValidator` freezes recursively into
        # `MappingProxyType` at every nesting level, not just the top one, so
        # `dict(artifact.metadata)` only thaws the top level -- any nested
        # mapping stays a `mappingproxy`, which asyncpg cannot serialize into
        # the JSON column. `model_dump()` runs the field's `PlainSerializer`,
        # which recursively produces plain dicts/lists at every level.
        metadata = artifact.model_dump()["metadata"]
        row = AgentArtifact(
            artifact_id=artifact.artifact_id,
            run_id=artifact.run_id,
            task_id=artifact.task_id,
            attempt_id=artifact.attempt_id,
            organization_id=normalized_organization_id,
            kind=artifact.kind,
            media_type=artifact.media_type,
            storage_uri=artifact.storage_uri,
            content_sha256=artifact.content_sha256,
            status=artifact.status.value,
            trust=artifact.trust.value,
            producer=artifact.producer,
            metadata_=metadata,
            producer_kind=artifact.producer_kind.value,
            prompt_id=binding.prompt_id if binding else None,
            prompt_version=binding.prompt_version if binding else None,
            template_sha256=binding.template_sha256 if binding else None,
            rendered_sha256=binding.rendered_sha256 if binding else None,
            created_at=artifact.created_at,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get_artifact(
        self, artifact_id: str, *, organization_id: OrganizationId = None
    ) -> AgentArtifact | None:
        """Fetch one artifact row, optionally scoped to an organization."""
        return await self._get_artifact_row(
            artifact_id, organization_id=organization_id
        )

    async def list_artifacts_for_run(
        self, run_id: str, *, organization_id: OrganizationId = None
    ) -> list[AgentArtifact]:
        """Return every artifact for a run, in creation order."""
        query = (
            select(AgentArtifact)
            .where(AgentArtifact.run_id == run_id)
            .order_by(AgentArtifact.created_at)
        )
        query = scope_to_organization(
            query, AgentArtifact.organization_id, organization_id
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def record_status_transition(
        self,
        artifact_id: str,
        *,
        organization_id: OrganizationId,
        status: ArtifactStatus,
        metadata: dict[str, Any] | None = None,
    ) -> AgentArtifact:
        """Move an artifact to a new ``ArtifactStatus`` on its existing row.

        Args:
            artifact_id: The artifact to transition.
            organization_id: The authenticated tenant boundary; must match
                the row's stored organization.
            status: The new status.
            metadata: Replacement metadata, if the transition carries new
                information (e.g. an invalidation reason). Left unchanged
                when omitted.

        Returns:
            The updated row.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
            TenantMismatchError: The org context disagrees with the row's
                stored organization.
            ValueError: No row exists for ``artifact_id``.
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        row = await self._get_artifact_row(artifact_id)
        if row is None:
            raise ValueError(f"artifact {artifact_id!r} does not exist")
        if row.organization_id != normalized_organization_id:
            raise TenantMismatchError(
                f"artifact {artifact_id!r} does not belong to organization "
                f"{normalized_organization_id}"
            )
        row.status = status.value
        if metadata is not None:
            row.metadata_ = dict(metadata)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def _get_artifact_row(
        self, artifact_id: str, *, organization_id: OrganizationId = None
    ) -> AgentArtifact | None:
        query = select(AgentArtifact).where(AgentArtifact.artifact_id == artifact_id)
        query = scope_to_organization(
            query, AgentArtifact.organization_id, organization_id
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()


__all__ = ["ArtifactRepository"]
