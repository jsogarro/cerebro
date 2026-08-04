"""Repository for the immutable per-run configuration snapshot.

``agent_run_config_snapshots`` holds exactly one row per run: the frozen
compiled configuration and version pins the run was admitted with. The table
is append-only (enforced by the ORM guard and, once migrated, a Postgres
trigger), so this repository exposes no update method — the only mutation
path is that there is none.

``configuration`` is also where the DB-backed execution authority resolver
gets its data: an authority binding is serialized into this JSON blob at
admission time and deserialized back out of it on resolve, keyed by the
``authority_id``/``authority_version`` columns this table indexes.
"""

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import PinnedVersions, Run
from src.models.db.run_config_snapshot import AgentRunConfigSnapshot
from src.repositories.tenant_scope import (
    enforce_tenant_identity,
    normalize_organization_id,
)

OrganizationId = uuid.UUID | str | None


def hash_configuration(configuration: dict[str, Any]) -> str:
    """Return the sha256 digest of a configuration blob's canonical form.

    Canonical here means deterministic key ordering and separators, so the
    same logical configuration always hashes to the same digest regardless of
    dict construction order.
    """
    canonical = json.dumps(
        configuration,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RunConfigSnapshotRepository:
    """Durable persistence for ``agent_run_config_snapshots``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_snapshot(
        self,
        *,
        run: Run,
        organization_id: OrganizationId,
        config_snapshot_id: str,
        authority_id: str,
        authority_version: str,
        pinned_versions: PinnedVersions,
        configuration: dict[str, Any],
    ) -> AgentRunConfigSnapshot:
        """Insert the one immutable configuration snapshot for a run.

        Args:
            run: The run this snapshot was admitted for. Its workflow and
                routing pins are copied onto the snapshot for self-describing
                lookups that don't need to join back to the run.
            organization_id: The authenticated tenant boundary. Must equal
                ``run.tenant_id`` once normalized.
            config_snapshot_id: A stable identifier for this snapshot.
            authority_id: The execution authority this run was admitted
                under — indexed, and the resolver's lookup key.
            authority_version: The exact pinned authority version.
            pinned_versions: The full component version pin set.
            configuration: The frozen compiled execution configuration. Its
                sha256 digest is computed and stored alongside it so
                tampering is detectable independently of the append-only
                enforcement.

        Returns:
            The persisted, immutable row.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
            TenantMismatchError: ``run.tenant_id`` disagrees with
                ``organization_id``.
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        enforce_tenant_identity(
            tenant_id=run.tenant_id, organization_id=normalized_organization_id
        )
        row = AgentRunConfigSnapshot(
            config_snapshot_id=config_snapshot_id,
            run_id=run.run_id,
            tenant_id=run.tenant_id,
            organization_id=normalized_organization_id,
            authority_id=authority_id,
            authority_version=authority_version,
            workflow_definition_id=run.workflow_definition_id,
            workflow_definition_version=run.workflow_definition_version,
            routing_policy_id=run.routing_policy_id,
            routing_policy_version=run.routing_policy_version,
            pinned_versions=pinned_versions.model_dump(mode="json"),
            configuration=configuration,
            content_sha256=hash_configuration(configuration),
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get_by_run(
        self, run_id: str, *, organization_id: OrganizationId = None
    ) -> AgentRunConfigSnapshot | None:
        """Fetch the one snapshot for a run, optionally scoped to an org."""
        query = select(AgentRunConfigSnapshot).where(
            AgentRunConfigSnapshot.run_id == run_id
        )
        if organization_id is not None:
            query = query.where(
                AgentRunConfigSnapshot.organization_id
                == normalize_organization_id(organization_id)
            )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def find_by_authority(
        self,
        *,
        authority_id: str,
        authority_version: str,
        limit: int = 1,
    ) -> list[AgentRunConfigSnapshot]:
        """Look up snapshots by the immutable authority they were admitted under.

        An authority binding is content-immutable and identified only by
        ``authority_id``/``authority_version`` — multiple runs may share one,
        so this returns the oldest matching snapshots first and defaults to
        the single row a resolver needs.
        """
        query = (
            select(AgentRunConfigSnapshot)
            .where(
                AgentRunConfigSnapshot.authority_id == authority_id,
                AgentRunConfigSnapshot.authority_version == authority_version,
            )
            .order_by(AgentRunConfigSnapshot.created_at)
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_all(
        self, *, limit: int | None = None
    ) -> list[AgentRunConfigSnapshot]:
        """Return every snapshot, oldest first.

        Used to warm an in-process authority cache (e.g. at startup) from
        every persisted binding.
        """
        query = select(AgentRunConfigSnapshot).order_by(
            AgentRunConfigSnapshot.created_at
        )
        if limit is not None:
            query = query.limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())


__all__ = ["RunConfigSnapshotRepository", "hash_configuration"]
