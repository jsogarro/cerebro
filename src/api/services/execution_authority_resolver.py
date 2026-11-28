"""Trusted lookup of immutable execution authority bindings."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol, cast

from sqlalchemy.exc import IntegrityError

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


class RequestIdExpiredError(ExecutionAuthorityError):
    """Raised when retrying a request_id whose orphan minted authority expired."""

    code = "REQUEST_ID_EXPIRED"


MINTED_AUTHORITY_VERSION_PREFIX = "mint:"
_MINT_AUTHORITY_VERSION_NAMESPACE = uuid.UUID("7f4c9a12-8b3d-4e56-9a10-2c3d4e5f6a70")
_MINT_RUN_ID_NAMESPACE = uuid.UUID("3a8e1b45-6c9f-4d21-8b70-9e0f1a2b3c4d")


def is_minted_authority_version(authority_version: str) -> bool:
    """Return whether ``authority_version`` identifies a minted per-run binding."""
    return authority_version.startswith(MINTED_AUTHORITY_VERSION_PREFIX)


def _normalize_org_for_mint(tenant_id: str) -> str:
    try:
        return str(uuid.UUID(tenant_id))
    except (ValueError, AttributeError, TypeError):
        return tenant_id


def mint_run_binding(
    template: ExecutionAuthorityBinding,
    *,
    user_id: str,
    request_id: str,
    now: datetime,
) -> ExecutionAuthorityBinding:
    """Deterministically derive a single-run binding from an operator template.

    Policy, budget, workers (including outbound host allowlists), workflow,
    and tenant ownership are copied verbatim from ``template``. Only the run
    identity, requester, timestamps, and relative deadline are derived.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id must be a non-empty string")
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id must be a non-empty string")
    if template.deadline.tzinfo is None or template.deadline.utcoffset() is None:
        raise ValueError("template deadline must be timezone-aware")
    delta = template.deadline - template.run.created_at
    if delta <= timedelta(0):
        raise ValueError("template deadline must follow run.created_at")

    org = _normalize_org_for_mint(template.run.tenant_id)
    canonical = json.dumps(
        [
            org,
            template.authority_id,
            template.authority_version,
            user_id,
            request_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    authority_version = (
        f"{MINTED_AUTHORITY_VERSION_PREFIX}"
        f"{uuid.uuid5(_MINT_AUTHORITY_VERSION_NAMESPACE, canonical)}"
    )
    run_id = str(uuid.uuid5(_MINT_RUN_ID_NAMESPACE, canonical))
    idempotency_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    minted_run = Run.model_validate(
        {
            **template.run.model_dump(mode="python"),
            "run_id": run_id,
            "requested_by": user_id,
            "idempotency_key": idempotency_key,
            "created_at": now,
            "updated_at": now,
        }
    )
    return dataclasses.replace(
        template,
        authority_version=authority_version,
        run=minted_run,
        deadline=now + delta,
    )


def tenant_owns_authority(tenant_id: str, organization_id: str) -> bool:
    """Return whether an authority's tenant is the caller's organization.

    Both sides are normalized through ``uuid.UUID(...)`` when both parse, so
    formatting differences (case, missing dashes) are not a false mismatch.
    The accepted tenant identity decision keeps ``Run.tenant_id`` a plain
    contract string rather than a UUID type, so a non-UUID tenant is compared
    exactly instead of being rejected outright — two distinct tenants never
    compare equal either way.
    """
    try:
        return uuid.UUID(tenant_id) == uuid.UUID(organization_id)
    except (ValueError, AttributeError, TypeError):
        return tenant_id == organization_id


def enforce_authority_tenant(
    binding: ExecutionAuthorityBinding,
    organization_id: str | None,
) -> ExecutionAuthorityBinding:
    """Return ``binding`` only if it belongs to the caller's organization.

    An authority reference is a client-supplied ``(authority_id,
    authority_version)`` pair and nothing more, so resolution has to be
    scoped by the caller's tenant or knowing another tenant's pair is enough
    to execute as that tenant — the durable write path takes its
    ``organization_id`` from ``binding.run.tenant_id``.

    Fails closed with no org context. A cross-tenant reference raises the
    same ``ExecutionAuthorityUnavailableError`` as a reference that does not
    exist, so the resolver cannot be used to probe for other tenants'
    authorities.
    """
    if organization_id is None or not tenant_owns_authority(
        binding.run.tenant_id, organization_id
    ):
        raise ExecutionAuthorityUnavailableError("execution authority is unavailable")
    return binding


class ExecutionAuthorityResolver(Protocol):
    """Resolves only a reference supplied by an execution adapter."""

    def resolve(
        self,
        reference: ExecutionAuthorityReference,
        *,
        organization_id: str | None,
    ) -> ExecutionAuthorityBinding:
        """Return the binding for one reference inside one tenant.

        ``organization_id`` is the caller's authenticated tenant. There is no
        unscoped resolution: implementations must reject a missing or
        mismatched organization.
        """


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
        self,
        reference: ExecutionAuthorityReference,
        *,
        organization_id: str | None,
    ) -> ExecutionAuthorityBinding:
        try:
            binding = self._bindings[
                (reference.authority_id, reference.authority_version)
            ]
        except KeyError as exc:
            raise ExecutionAuthorityUnavailableError(
                "execution authority is unavailable"
            ) from exc
        return enforce_authority_tenant(binding, organization_id)


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

    ``resolve`` itself never queries: a miss raises
    ``ExecutionAuthorityUnavailableError`` even if the binding exists in the
    database. Async callers that should see rows written after startup (by
    another replica) call :meth:`load_authority` first when a
    ``session_factory`` was supplied.

    The cache spans every tenant, because a cold start has to load whatever
    is durable without a caller to scope it to. The tenant boundary is
    therefore applied on the way *out*: ``resolve`` returns a binding only to
    a caller whose organization owns it.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        plan_id_factory: Callable[[], str] | None = None,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]]
        | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._bindings: dict[tuple[str, str], ExecutionAuthorityBinding] = {}
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._plan_id_factory: Callable[[], str] = plan_id_factory or (
            lambda: str(uuid.uuid4())
        )

    def resolve(
        self,
        reference: ExecutionAuthorityReference,
        *,
        organization_id: str | None,
    ) -> ExecutionAuthorityBinding:
        try:
            binding = self._bindings[
                (reference.authority_id, reference.authority_version)
            ]
        except KeyError as exc:
            raise ExecutionAuthorityUnavailableError(
                "execution authority is unavailable"
            ) from exc
        return enforce_authority_tenant(binding, organization_id)

    def cache_binding(self, binding: ExecutionAuthorityBinding) -> None:
        """Populate the in-process cache without touching persistence."""
        self._bindings[(binding.authority_id, binding.authority_version)] = binding

    async def load_authority(
        self,
        reference: ExecutionAuthorityReference,
        *,
        organization_id: str | None = None,
    ) -> ExecutionAuthorityBinding | None:
        """Load the referenced authority from the database on a cache miss.

        Operator-registered authorities are cached in-process; minted per-run
        bindings are returned directly without populating ``_bindings`` so
        memory does not grow O(runs) per replica. Tenant scoping still happens
        on resolution.
        """
        key = (reference.authority_id, reference.authority_version)
        if key in self._bindings:
            return self._bindings[key]
        if self._session_factory is None:
            return None
        from src.middleware.tenant_context import set_postgres_tenant_context
        from src.repositories.execution_authority_repository import (
            ExecutionAuthorityRepository,
        )
        from src.repositories.run_config_snapshot_repository import (
            RunConfigSnapshotRepository,
        )

        async with self._session_factory() as session:
            row: Any = None
            if organization_id is not None:
                if hasattr(session, "get_bind"):
                    await set_postgres_tenant_context(session, organization_id)
                row = await ExecutionAuthorityRepository(session).get(
                    authority_id=reference.authority_id,
                    authority_version=reference.authority_version,
                    organization_id=organization_id,
                )
            if row is None:
                snapshots = await RunConfigSnapshotRepository(
                    session
                ).find_by_authority(
                    authority_id=reference.authority_id,
                    authority_version=reference.authority_version,
                )
                row = snapshots[0] if snapshots else None
        if row is None:
            return None
        binding = deserialize_execution_authority_binding(
            row.configuration,
            authority_id=row.authority_id,
            authority_version=row.authority_version,
            clock=self._clock,
            plan_id_factory=self._plan_id_factory,
        )
        if not is_minted_authority_version(binding.authority_version):
            self.cache_binding(binding)
        return binding

    async def mint_authority_from_template(
        self,
        template_reference: ExecutionAuthorityReference,
        *,
        organization_id: str | None,
        user_id: str,
        request_id: str,
        now: datetime | None = None,
    ) -> ExecutionAuthorityBinding:
        """Mint and durably persist a single-run authority from an org template.

        Commits the minted ``is_template=False`` row in its own transaction so
        subsequent admission sessions can read it. On a duplicate retry,
        reloads the existing org-scoped row and rejects expired orphan rows
        (no ``AgentRun`` yet) with :class:`RequestIdExpiredError`. Never
        inserts minted bindings into ``_bindings``.
        """
        if self._session_factory is None or organization_id is None:
            raise ExecutionAuthorityUnavailableError(
                "execution authority is unavailable"
            )
        from src.models.db.session import tenant_session
        from src.repositories.execution_authority_repository import (
            ExecutionAuthorityRepository,
        )
        from src.repositories.run_lifecycle_repository import RunLifecycleRepository
        from src.repositories.tenant_scope import (
            MissingOrganizationContextError,
            normalize_organization_id,
        )

        try:
            normalized_org = normalize_organization_id(organization_id)
        except (MissingOrganizationContextError, ValueError, TypeError) as exc:
            raise ExecutionAuthorityUnavailableError(
                "execution authority is unavailable"
            ) from exc

        mint_now = now if now is not None else self._clock()
        normalized_org_str = str(normalized_org)
        session_factory = cast("Callable[[], AsyncSession]", self._session_factory)

        async with tenant_session(session_factory, normalized_org) as session:
            template_row = await ExecutionAuthorityRepository(session).get_template(
                authority_id=template_reference.authority_id,
                authority_version=template_reference.authority_version,
                organization_id=normalized_org,
            )
            if template_row is None:
                raise ExecutionAuthorityUnavailableError(
                    "execution authority is unavailable"
                )
            template_binding = deserialize_execution_authority_binding(
                template_row.configuration,
                authority_id=template_row.authority_id,
                authority_version=template_row.authority_version,
                clock=self._clock,
                plan_id_factory=self._plan_id_factory,
            )
            enforce_authority_tenant(template_binding, normalized_org_str)
            minted = mint_run_binding(
                template_binding,
                user_id=user_id,
                request_id=request_id,
                now=mint_now,
            )
            try:
                await ExecutionAuthorityRepository(session).create(
                    minted,
                    organization_id=normalized_org,
                    is_template=False,
                )
                await session.commit()
                return minted
            except IntegrityError:
                await session.rollback()

        async with tenant_session(session_factory, normalized_org) as reload_session:
            existing_row = await ExecutionAuthorityRepository(reload_session).get(
                authority_id=minted.authority_id,
                authority_version=minted.authority_version,
                organization_id=normalized_org,
            )
            if existing_row is None:
                raise ExecutionAuthorityUnavailableError(
                    "execution authority is unavailable"
                )
            reloaded = deserialize_execution_authority_binding(
                existing_row.configuration,
                authority_id=existing_row.authority_id,
                authority_version=existing_row.authority_version,
                clock=self._clock,
                plan_id_factory=self._plan_id_factory,
            )
            enforce_authority_tenant(reloaded, normalized_org_str)
            if reloaded.deadline <= mint_now:
                existing_run = await RunLifecycleRepository(reload_session).get_run(
                    reloaded.run.run_id,
                    organization_id=normalized_org,
                )
                if existing_run is None:
                    raise RequestIdExpiredError(
                        "request_id has expired; send a new request_id"
                    )
            return reloaded

    async def register(
        self,
        session: AsyncSession,
        *,
        binding: ExecutionAuthorityBinding,
        organization_id: uuid.UUID | str,
        config_snapshot_id: str,
        pinned_versions: PinnedVersions,
    ) -> AgentRunConfigSnapshot:
        """Persist a binding's config snapshot and warm the cache if non-minted.

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
        if not is_minted_authority_version(binding.authority_version):
            self.cache_binding(binding)
        return snapshot

    async def warm_from_snapshots(
        self, session: AsyncSession, *, limit: int | None = None
    ) -> int:
        """Load persisted non-minted authority bindings into the cache.

        Intended for a cold start: called once before the resolver starts
        serving ``resolve`` calls, so a process restart does not present as
        every authority being unavailable. Minted per-run rows are skipped so
        cache memory stays bounded by operator authorities.

        Returns:
            The number of snapshots loaded into the cache.
        """
        from src.repositories.run_config_snapshot_repository import (
            RunConfigSnapshotRepository,
        )

        snapshots = await RunConfigSnapshotRepository(session).list_all(limit=limit)
        loaded = 0
        for snapshot in snapshots:
            if is_minted_authority_version(snapshot.authority_version):
                continue
            binding = deserialize_execution_authority_binding(
                snapshot.configuration,
                authority_id=snapshot.authority_id,
                authority_version=snapshot.authority_version,
                clock=self._clock,
                plan_id_factory=self._plan_id_factory,
            )
            self.cache_binding(binding)
            loaded += 1
        return loaded
