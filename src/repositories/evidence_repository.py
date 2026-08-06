"""Repository for the append-only ``agent_evidence`` table.

Follows the Wave 3 pattern used for ``agent_run_events``: ``record_evidence``
is the only write path, nothing here ever updates or deletes a row (the
append-only trigger installed by this wave's migration enforces that at the
database level too), and every write copies its tenant boundary from the
run it belongs to rather than re-deriving it.

**The digest cross-check.** 4A's contract freeze named this as its own
non-guarantee: ``Evidence.content_sha256`` is not cross-checked against its
``snapshot_artifact_id``'s digest by the Pydantic contract, because a
cross-table invariant cannot be expressed as a single-model validator, and
it cannot be a database CHECK either (a CHECK constraint can only see columns
on its own table). This repository is the only place both values are ever in
hand together at write time, so it is where the check belongs:
``record_evidence`` fetches the referenced artifact and raises before the
row is added if the digests disagree.

**Locator denormalization.** ``AgentEvidence.locator_scheme``/``locator_start``/
``locator_end`` are parsed out of ``evidence.locator`` here via
``parse_locator`` and written alongside the raw string, so the grammar's own
invariants become database CHECKs rather than only a Pydantic validator a
raw-SQL write path could bypass. The ``Evidence`` contract already guarantees
``evidence.locator`` parses (``validate_provenance_chain`` calls
``parse_locator`` itself), so this re-parse cannot fail here — it exists to
project the already-validated value onto queryable columns.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import Evidence
from src.core.contracts.locators import parse_locator
from src.models.db.artifact import AgentArtifact
from src.models.db.evidence import AgentEvidence
from src.models.db.run_lifecycle import AgentRun
from src.repositories.tenant_scope import (
    TenantMismatchError,
    normalize_organization_id,
)

OrganizationId = uuid.UUID | str | None


class EvidenceDigestMismatchError(ValueError):
    """Raised when ``Evidence.content_sha256`` disagrees with the snapshot
    artifact it cites.

    This is the guarantee 4A could not express as a contract validator or a
    database CHECK: it depends on two rows, and this repository is the one
    place that has both in hand at write time.
    """


class EvidenceRepository:
    """Durable, append-only persistence for ``agent_evidence``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_evidence(
        self, evidence: Evidence, *, organization_id: OrganizationId
    ) -> AgentEvidence:
        """Append one evidence row from an admitted ``Evidence`` contract.

        Args:
            evidence: The validated evidence contract.
            organization_id: The authenticated tenant boundary; must match
                the parent run's stored organization.

        Returns:
            The persisted row.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
            TenantMismatchError: The org context disagrees with the parent
                run's organization.
            ValueError: The parent run, or the referenced snapshot artifact,
                does not exist.
            EvidenceDigestMismatchError: ``evidence.content_sha256`` disagrees
                with the referenced artifact's ``content_sha256``.
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        run_organization_id = await self._get_run_organization_id(evidence.run_id)
        if run_organization_id is None:
            raise ValueError(f"run {evidence.run_id!r} does not exist")
        if run_organization_id != normalized_organization_id:
            raise TenantMismatchError(
                f"run {evidence.run_id!r} does not belong to organization "
                f"{normalized_organization_id}"
            )

        artifact = await self._get_artifact_row(evidence.snapshot_artifact_id)
        if artifact is None:
            raise ValueError(
                f"snapshot artifact {evidence.snapshot_artifact_id!r} does not exist"
            )
        if artifact.content_sha256 != evidence.content_sha256:
            raise EvidenceDigestMismatchError(
                f"evidence {evidence.evidence_id!r} content_sha256 "
                f"({evidence.content_sha256!r}) disagrees with snapshot artifact "
                f"{evidence.snapshot_artifact_id!r} content_sha256 "
                f"({artifact.content_sha256!r})"
            )

        binding = evidence.prompt_binding
        canonical_span = parse_locator(evidence.locator).canonical
        row = AgentEvidence(
            evidence_id=evidence.evidence_id,
            run_id=evidence.run_id,
            task_id=evidence.task_id,
            organization_id=normalized_organization_id,
            source_type=evidence.source_type,
            source_uri=evidence.source_uri,
            snapshot_artifact_id=evidence.snapshot_artifact_id,
            content_sha256=evidence.content_sha256,
            locator=evidence.locator,
            locator_scheme=canonical_span.scheme,
            locator_start=canonical_span.start,
            locator_end=canonical_span.end,
            trust=evidence.trust.value,
            producer_kind=evidence.producer_kind.value,
            prompt_id=binding.prompt_id if binding else None,
            prompt_version=binding.prompt_version if binding else None,
            template_sha256=binding.template_sha256 if binding else None,
            rendered_sha256=binding.rendered_sha256 if binding else None,
            producer_tool_invocation_id=evidence.producer_tool_invocation_id,
            parent_evidence_ids=list(evidence.parent_evidence_ids),
            acquired_at=evidence.acquired_at,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get_evidence(
        self, evidence_id: str, *, organization_id: OrganizationId = None
    ) -> AgentEvidence | None:
        """Fetch one evidence row, optionally scoped to an organization."""
        query = select(AgentEvidence).where(AgentEvidence.evidence_id == evidence_id)
        if organization_id is not None:
            query = query.where(
                AgentEvidence.organization_id
                == normalize_organization_id(organization_id)
            )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_evidence_for_run(
        self, run_id: str, *, organization_id: OrganizationId = None
    ) -> list[AgentEvidence]:
        """Return every evidence row for a run, in acquisition order."""
        query = (
            select(AgentEvidence)
            .where(AgentEvidence.run_id == run_id)
            .order_by(AgentEvidence.acquired_at)
        )
        if organization_id is not None:
            query = query.where(
                AgentEvidence.organization_id
                == normalize_organization_id(organization_id)
            )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def _get_run_organization_id(self, run_id: str) -> uuid.UUID | None:
        query = select(AgentRun.organization_id).where(AgentRun.run_id == run_id)
        result = await self.session.execute(query)
        row = result.first()
        if row is None:
            return None
        organization_id: uuid.UUID | None = row[0]
        return organization_id

    async def _get_artifact_row(self, artifact_id: str) -> AgentArtifact | None:
        query = select(AgentArtifact).where(AgentArtifact.artifact_id == artifact_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()


__all__ = ["EvidenceDigestMismatchError", "EvidenceRepository"]
