"""Trusted lookup of immutable execution authority bindings."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from src.core.contracts import (
    ExecutionBudget,
    PinnedVersions,
    ProviderModelPolicy,
    RoutingEdge,
    RoutingPolicy,
    Run,
    WorkerAssignment,
    WorkflowDefinition,
)
from src.models.execution_authority import (
    ExecutionAuthorityBinding,
    ExecutionAuthorityReference,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.models.db.run_config_snapshot import AgentRunConfigSnapshot


class ExecutionAuthorityError(ValueError):
    """A fail-closed authority rejection suitable for an adapter to map to 422."""

    code: str


class ExecutionAuthorityRequiredError(ExecutionAuthorityError):
    """Raised when execution is requested without an authority reference."""

    code = "EXECUTION_AUTHORITY_REQUIRED"


class ExecutionAuthorityUnavailableError(ExecutionAuthorityError):
    """Raised when no trusted resolver can supply the referenced authority."""

    code = "EXECUTION_AUTHORITY_UNAVAILABLE"


class ExecutionAuthorityResolver(Protocol):
    """Resolves only a reference supplied by an execution adapter."""

    def resolve(
        self, reference: ExecutionAuthorityReference
    ) -> ExecutionAuthorityBinding:
        """Return the complete binding for one exact immutable reference."""


class MappingExecutionAuthorityResolver:
    """Immutable in-process resolver used by explicit trusted composition."""

    def __init__(
        self,
        bindings: Mapping[tuple[str, str], ExecutionAuthorityBinding],
    ) -> None:
        copied_bindings = dict(bindings)
        for (authority_id, authority_version), binding in copied_bindings.items():
            if (
                binding.authority_id != authority_id
                or binding.authority_version != authority_version
            ):
                raise ValueError("authority binding key must match binding identity")
        self._bindings: Mapping[tuple[str, str], ExecutionAuthorityBinding] = (
            MappingProxyType(copied_bindings)
        )

    def resolve(
        self, reference: ExecutionAuthorityReference
    ) -> ExecutionAuthorityBinding:
        try:
            return self._bindings[(reference.authority_id, reference.authority_version)]
        except KeyError as exc:
            raise ExecutionAuthorityUnavailableError(
                "execution authority is unavailable"
            ) from exc


def serialize_execution_authority_binding(
    binding: ExecutionAuthorityBinding,
) -> dict[str, Any]:
    """Return the JSON-safe parts of a binding for config-snapshot storage.

    ``plan_id_factory`` and ``clock`` are callables, not data — they are
    deliberately excluded. A resolver rebuilding a binding from this blob
    supplies its own real implementations of both; see
    :func:`deserialize_execution_authority_binding`.
    """
    return {
        "run": binding.run.model_dump(mode="json"),
        "workflow_definition": binding.workflow_definition.model_dump(mode="json"),
        "routing_policy": binding.routing_policy.model_dump(mode="json"),
        "domains": list(binding.domains),
        "supervisor_id": binding.supervisor_id,
        "supervisor_type": binding.supervisor_type,
        "workers": [worker.model_dump(mode="json") for worker in binding.workers],
        "edges": [edge.model_dump(mode="json") for edge in binding.edges],
        "provider_model_policy": binding.provider_model_policy.model_dump(mode="json"),
        "budget": binding.budget.model_dump(mode="json"),
        "stop_conditions": list(binding.stop_conditions),
        "evaluator_requirements": list(binding.evaluator_requirements),
        "deadline": binding.deadline.isoformat(),
    }


def deserialize_execution_authority_binding(
    configuration: Mapping[str, Any],
    *,
    authority_id: str,
    authority_version: str,
    clock: Callable[[], datetime],
    plan_id_factory: Callable[[], str],
) -> ExecutionAuthorityBinding:
    """Rebuild a binding from a config snapshot's stored ``configuration``.

    The inverse of :func:`serialize_execution_authority_binding`.
    ``authority_id``/``authority_version`` come from the snapshot's own
    indexed columns rather than the blob, since those columns are the
    resolver's frozen lookup key.
    """
    return ExecutionAuthorityBinding(
        authority_id=authority_id,
        authority_version=authority_version,
        run=Run.model_validate(configuration["run"]),
        workflow_definition=WorkflowDefinition.model_validate(
            configuration["workflow_definition"]
        ),
        routing_policy=RoutingPolicy.model_validate(configuration["routing_policy"]),
        domains=tuple(configuration["domains"]),
        supervisor_id=configuration["supervisor_id"],
        supervisor_type=configuration["supervisor_type"],
        workers=tuple(
            WorkerAssignment.model_validate(worker)
            for worker in configuration["workers"]
        ),
        edges=tuple(
            RoutingEdge.model_validate(edge) for edge in configuration["edges"]
        ),
        provider_model_policy=ProviderModelPolicy.model_validate(
            configuration["provider_model_policy"]
        ),
        budget=ExecutionBudget.model_validate(configuration["budget"]),
        stop_conditions=tuple(configuration["stop_conditions"]),
        evaluator_requirements=tuple(configuration["evaluator_requirements"]),
        deadline=datetime.fromisoformat(configuration["deadline"]),
        plan_id_factory=plan_id_factory,
        clock=clock,
    )


class PersistedExecutionAuthorityResolver:
    """Durable resolver backed by the config-snapshot table.

    ``resolve`` must stay synchronous to satisfy the frozen
    ``ExecutionAuthorityResolver`` protocol, so it can only ever read an
    in-process cache — it never touches the database itself. Two async
    methods keep that cache truthful against persistence:

    - :meth:`register` persists a newly admitted binding's config snapshot
      and caches it, for the write path at admission time.
    - :meth:`warm_from_snapshots` loads every persisted binding into the
      cache in one pass, for a cold start (a fresh process has an empty
      cache and no other way to learn what's durable).

    A ``resolve`` call that misses the cache raises
    ``ExecutionAuthorityUnavailableError`` even if the binding exists in the
    database — this resolver does not fall back to a live query. Callers
    that need that guarantee must warm the cache before serving requests.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        plan_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._bindings: dict[tuple[str, str], ExecutionAuthorityBinding] = {}
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._plan_id_factory: Callable[[], str] = plan_id_factory or (
            lambda: str(uuid.uuid4())
        )

    def resolve(
        self, reference: ExecutionAuthorityReference
    ) -> ExecutionAuthorityBinding:
        try:
            return self._bindings[(reference.authority_id, reference.authority_version)]
        except KeyError as exc:
            raise ExecutionAuthorityUnavailableError(
                "execution authority is unavailable"
            ) from exc

    def cache_binding(self, binding: ExecutionAuthorityBinding) -> None:
        """Populate the in-process cache without touching persistence."""
        self._bindings[(binding.authority_id, binding.authority_version)] = binding

    async def register(
        self,
        session: AsyncSession,
        *,
        binding: ExecutionAuthorityBinding,
        organization_id: uuid.UUID | str,
        config_snapshot_id: str,
        pinned_versions: PinnedVersions,
    ) -> AgentRunConfigSnapshot:
        """Persist a binding's config snapshot and warm the cache with it.

        Args:
            session: The active database session; this method flushes but
                does not commit, matching repository convention.
            binding: The complete admitted authority to make durable and
                resolvable.
            organization_id: The tenant boundary the snapshot is written
                under; must agree with ``binding.run.tenant_id``.
            config_snapshot_id: A stable identifier for the snapshot row.
            pinned_versions: The ``PinnedVersions`` contract for this run.

        Returns:
            The persisted, immutable snapshot row.
        """
        from src.repositories.run_config_snapshot_repository import (
            RunConfigSnapshotRepository,
        )

        configuration = serialize_execution_authority_binding(binding)
        snapshot = await RunConfigSnapshotRepository(session).create_snapshot(
            run=binding.run,
            organization_id=organization_id,
            config_snapshot_id=config_snapshot_id,
            authority_id=binding.authority_id,
            authority_version=binding.authority_version,
            pinned_versions=pinned_versions,
            configuration=configuration,
        )
        self.cache_binding(binding)
        return snapshot

    async def warm_from_snapshots(
        self, session: AsyncSession, *, limit: int | None = None
    ) -> int:
        """Load every persisted authority binding into the cache.

        Intended for a cold start: called once before the resolver starts
        serving ``resolve`` calls, so a process restart does not present as
        every authority being unavailable.

        Returns:
            The number of snapshots loaded.
        """
        from src.repositories.run_config_snapshot_repository import (
            RunConfigSnapshotRepository,
        )

        snapshots = await RunConfigSnapshotRepository(session).list_all(limit=limit)
        for snapshot in snapshots:
            binding = deserialize_execution_authority_binding(
                snapshot.configuration,
                authority_id=snapshot.authority_id,
                authority_version=snapshot.authority_version,
                clock=self._clock,
                plan_id_factory=self._plan_id_factory,
            )
            self.cache_binding(binding)
        return len(snapshots)
