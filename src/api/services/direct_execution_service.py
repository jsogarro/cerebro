"""
Direct Execution Service

Replaces Temporal workflows with direct MASR routing and supervisor execution.
Provides the same functionality as Temporal workflows but with a simpler,
more responsive architecture optimized for interactive AI queries.

Key Features:
- Direct MASR routing without workflow serialization overhead
- Real-time progress tracking via WebSocket events
- Simplified error handling and retry logic
- Integration with hierarchical supervisor/worker coordination
- State management via LangGraph workflows
"""

import asyncio
import copy
import hashlib
import json
import os
import socket
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from structlog import get_logger
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.agents.llm_worker_base import PlanProviderUnavailableError
from src.core.budget import (
    BUDGET_LEDGER_CONTEXT_KEY,
    BudgetExceededError,
    BudgetLedger,
)
from src.core.config import settings
from src.core.constants import DEFAULT_AGENT_TIMEOUT, MAX_RETRY_ATTEMPTS
from src.core.observability import (
    run_cancellation_delay_seconds,
    run_ownership_expired_total,
    run_reconciliation_failures_total,
)
from src.core.telemetry import count_tokens, telemetry_enabled
from src.models.db.run_event import EVENT_ENVELOPE_VERSION
from src.models.db.session import tenant_session
from src.repositories.artifact_repository import ArtifactRepository
from src.repositories.capability_repository import CapabilityRepository
from src.repositories.checkpoint_repository import (
    CheckpointIdentityError,
    CheckpointRepository,
)
from src.repositories.claim_support_repository import ClaimSupportRepository
from src.repositories.model_call_reservation_repository import (
    ModelCallReservationRepository,
    OwnerLostError,
)
from src.repositories.research_repository import ResearchRepository
from src.repositories.run_config_snapshot_repository import (
    RunConfigSnapshotRepository,
)
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import (
    ResearchRunJournalOwner,
    RunCapExceededError,
    RunLifecycleRepository,
    StaleFenceError,
)
from src.services.model_invocation import (
    MODEL_INVOCATION_CONTEXT_KEY,
    ModelInvocationContext,
)

from ...agents.models import AgentTask
from ...agents.supervisors.base_supervisor import BaseSupervisor
from ...agents.supervisors.supervisor_factory import SupervisorFactory
from ...agents.tools.mediation import (
    ATTEMPT_ID_CONTEXT_KEY,
    ORGANIZATION_ID_CONTEXT_KEY,
    RUN_ID_CONTEXT_KEY,
    TASK_ID_CONTEXT_KEY,
)
from ...ai_brain.integration.execution_plan_topology import (
    PlanExecutionResult,
    WorkerOutcomeStatus,
)
from ...ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
from ...ai_brain.router.execution_plan_compiler import ExecutionPlanCompiler
from ...ai_brain.router.masr import MASRouter
from ...ai_brain.router.masr import RoutingDecision as MASRRoutingDecision
from ...ai_brain.router.outcome_recorder import (
    RoutingOutcomeRecorder,
)
from ...ai_brain.router.routing_types import (
    RoutingExecutionPolicy,
)
from ...core.capabilities import CAPABILITY_GRANTS_CONTEXT_KEY, PlanCapabilityIssuer
from ...core.claims import build_inventory
from ...core.claims.recording import ClaimSupportRecorder
from ...core.claims.resolution import ClaimSupportResolution, ClaimSupportResolver
from ...core.contracts import (
    Artifact,
    ArtifactStatus,
    Attempt,
    AttemptStatus,
    CapabilityGrant,
    ExecutionPlan,
    InvalidTransitionError,
    PinnedVersions,
    ProducerKind,
    Run,
    RunStatus,
    Task,
    TaskStatus,
    TrustClassification,
)
from ...core.contracts.states import TERMINAL_ATTEMPT_STATUSES, TERMINAL_RUN_STATUSES
from ...core.kernel import (
    TypedRegistry,
    UnknownRegistryKeyError,
)
from ...core.kernel.component_keys import SUPERVISOR_KEYS
from ...models.execution_authority import (
    ExecutionAuthorityBinding,
    ExecutionAuthorityReference,
)
from ...models.research_project import ResearchProject
from ...models.websocket_messages import ProgressUpdate
from .event_publisher import EventPublisher
from .execution_authority_resolver import (
    ExecutionAuthorityRequiredError,
    ExecutionAuthorityResolver,
    ExecutionAuthorityUnavailableError,
    PersistedExecutionAuthorityResolver,
    serialize_execution_authority_binding,
    tenant_owns_authority,
)

if TYPE_CHECKING:
    from ...ai_brain.providers.openrouter_provider import OpenRouterProvider

logger = get_logger()


# Cold-start rehydration maps a durable ``RunStatus`` back onto the legacy
# ``ExecutionStatus.status`` string vocabulary the rest of this module (and
# ``src/api/routes/research.py``'s direct dict reads) already understands.
# ``CANCELLING`` maps to "running" rather than a new string so a
# not-yet-finalized cancellation still counts as active in
# ``list_active_executions``/``get_service_stats``.
_RUN_STATUS_TO_EXECUTION_STATUS: dict[str, str] = {
    RunStatus.CREATED.value: "pending",
    RunStatus.QUEUED.value: "pending",
    RunStatus.RUNNING.value: "running",
    RunStatus.CANCELLING.value: "running",
    RunStatus.SUCCEEDED.value: "completed",
    RunStatus.FAILED.value: "failed",
    RunStatus.CANCELLED.value: "cancelled",
}


# How many times ``_execute_research_workflow`` runs before its failure is
# the run's outcome. Declared once because two things depend on agreeing
# about it: the ``@retry`` decorator, and the failure path's decision about
# whether a terminal FAILED may be persisted yet.
INTERRUPTED_BY_RESTART = "interrupted by restart"
PROCESS_INTERRUPTED = "PROCESS_INTERRUPTED"
RESTORE_DEADLINE_MARGIN = timedelta(minutes=5)
_WORKFLOW_MAX_ATTEMPTS = 3


_FINAL_REPORT_ARTIFACT_KIND = "research_report"
_FINAL_REPORT_MEDIA_TYPE = "application/json"
_FINAL_REPORT_STORAGE_URI_PREFIX = "memory://artifacts"
_FINAL_REPORT_CONTENT_METADATA_KEY = "content"


def _canonical_json(value: Any) -> str:
    """Serialize a report value deterministically, or fail closed."""

    try:
        serialized = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        # ``ensure_ascii=False`` can leave lone surrogates in the returned
        # string. Verify UTF-8 encodability before the digest is constructed.
        serialized.encode("utf-8")
        return serialized
    except (OverflowError, RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise ValueError(
            "final_output cannot be persisted as a JSON final report"
        ) from exc


def _journalable_value(value: Any) -> Any:
    """Return a JSON-safe journal value, or ``None`` for an unreadable value."""

    try:
        return json.loads(_canonical_json(value))
    except (TypeError, ValueError):
        return None


# Observable ``current_phase`` for an execution whose real outcome is known
# but whose durable write has not landed. It is deliberately not a
# ``status`` value: ``status`` stays non-terminal precisely because no
# terminal claim is backed by the database yet.
AWAITING_DURABLE_CONFIRMATION_PHASE = "awaiting_durable_confirmation"


class RunAdmissionError(RuntimeError):
    """Admission could not be made durable, so no execution was started.

    Raised only when persistence is configured and failed. A service with no
    session factory at all is a different case — there is no durable record
    to contradict, so memory-only execution stays supported and this is not
    raised.
    """


class RunAlreadyAdmittedError(RuntimeError):
    """A concurrent, cross-process admission already durably won this run.

    Raised only from :meth:`DirectExecutionService._admit_run`, and only when
    its ``create_run`` INSERT is refused by ``uq_agent_run_tenant_idempotency``
    — the run's idempotency key already has a row, written by another process
    between this process's pre-admission durable lookup and its own INSERT.
    That is not an admission *failure*: the run really was admitted, just not
    by this process. Carries the execution id
    :meth:`DirectExecutionService._durably_recorded_execution_id` found for
    that row, so the caller coalesces onto it exactly as an in-process
    concurrent duplicate already does via ``_admissions_in_flight`` — instead
    of surfacing an ordinary cross-replica race as a 500.
    """

    def __init__(self, execution_id: str) -> None:
        super().__init__(
            f"run already admitted by another process; coalescing onto "
            f"execution {execution_id!r}"
        )
        self.execution_id = execution_id


class ResumeUnsupportedError(RuntimeError):
    """A checkpoint cannot be resumed; the caller starts a new run instead."""

    code = "RESUME_UNSUPPORTED"

    def __init__(self, project_id: uuid.UUID) -> None:
        super().__init__(
            f"project {project_id} cannot be resumed from a checkpoint; start a new run"
        )


class DurableWriteOutcome(StrEnum):
    """What the durable layer did with a lifecycle write.

    ``RECORDED`` also covers memory-only mode (no session factory, or an
    execution that was never admitted): there is nothing to record, so
    nothing was lost. ``REJECTED`` means the write can never succeed as
    formulated — the contract refused the transition because the durable
    record already moved past it — so retrying is pointless. ``UNAVAILABLE``
    means the write itself never landed and the durable record still holds
    the previous state; the same write can succeed later.
    """

    RECORDED = "recorded"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class _FinalReport:
    """The immutable report contract and exact text it stores for claims."""

    artifact: Artifact
    text: str


@dataclass
class _DurableTransition:
    """One fully planned lifecycle write, ready to commit or re-commit.

    Planning is pure (contract ``transition_to`` calls) and happens once, so
    a retry re-commits the *same* facts rather than re-deriving them from
    state that may have moved on. ``occurred_at`` is the moment the outcome
    happened, not the moment the write finally succeeded.
    """

    run: Run
    task: Task | None
    attempt: Attempt | None
    event_type: str
    payload: dict[str, Any]
    occurred_at: datetime
    event_id: str
    journaled_result: dict[str, Any] | None = None
    final_report: _FinalReport | None = None
    # The writer's lease. A write from a process whose lease ended is refused,
    # so a worker that lost its run cannot record an outcome for it.
    fencing_token: int | None = None
    # Runs in the commit's own transaction after the fence is proven, so work
    # that must land with the transition (settling a dead owner's open model
    # calls) cannot commit without it, or the reverse. Must be idempotent: an
    # ambiguous commit re-runs it.
    in_transaction: Callable[[Any], Awaitable[None]] | None = None


@dataclass
class _PendingTerminalOutcome:
    """A terminal outcome the database has not accepted yet.

    Held in process memory only. While it sits here the execution's
    observable ``status`` stays non-terminal, so nothing reports a terminal
    state the durable layer never took. If the process dies before
    reconciliation lands the write, the outcome is lost — but the durable
    record still shows a non-terminal run, which recovery surfaces as
    unfinished work rather than as fabricated success.
    """

    transition: _DurableTransition
    execution_status: "ExecutionStatus"
    observable_status: str
    observable_phase: str
    observable_progress: float | None
    # Monotonic instant after which this outcome is given up on, so each one
    # has its own retry window however late it was parked.
    expires_at: float


class _BorrowedSession:
    """Adapter letting ``tenant_session`` scope a session already opened once."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *_exc: Any) -> None:
        return None


def _discard_unretrieved_admission_error(future: "asyncio.Future[str]") -> None:
    """Mark a failed admission claim's exception as seen.

    A claim nobody waited on still carries its exception, and asyncio logs
    "Future exception was never retrieved" when it is garbage collected. The
    caller that actually made the admission already raised it; reading it
    here keeps a routine failure from also producing a spurious error log.
    """
    if not future.cancelled():
        future.exception()


def _validate_supervisor_registry(registry: TypedRegistry) -> None:
    """Reject invalid bridge composition before background execution starts."""

    registry.resolve(SUPERVISOR_KEYS["research"])
    for key in SUPERVISOR_KEYS.values():
        try:
            supervisor_class = registry.resolve(key)
        except UnknownRegistryKeyError:
            continue
        if not isinstance(supervisor_class, type) or not issubclass(
            supervisor_class, BaseSupervisor
        ):
            raise TypeError(
                f"Registry component {key.qualified_name} must be a BaseSupervisor "
                f"subclass; got {type(supervisor_class).__name__}"
            )


@dataclass
class ExecutionStatus:
    """Status of direct execution."""

    execution_id: str
    project_id: str
    status: str  # pending, running, completed, failed
    progress_percentage: float = 0.0
    current_phase: str = "initialization"

    # Rehydrated from the database; this process runs no task for it.
    restored: bool = False

    # The tenant that owns this execution, so a reader can be scoped to it.
    # ``active_executions`` is one process-wide dictionary holding every
    # tenant's runs, including the outputs materialized from persistence;
    # without an owner recorded here a read by execution id has nothing to
    # filter on. ``None`` means the owner is unknown, which every scoped
    # reader must treat as not visible rather than as visible to all.
    organization_id: str | None = None

    # Results
    agent_results: dict[str, Any] = field(default_factory=dict)
    quality_scores: dict[str, float] = field(default_factory=dict)
    final_output: dict[str, Any] | None = None

    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    execution_time_seconds: float = 0.0

    # MASR routing information
    routing_decision: dict[str, Any] | None = None
    execution_plan: ExecutionPlan | None = None
    sub_routing_decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    supervisor_type: str | None = None
    workers_used: int = 0

    # Error handling
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    retry_count: int = 0
    # Machine-readable cause of a failure that has one (e.g. BUDGET_EXCEEDED).
    failure_code: str | None = None

    # The run's spend against its ExecutionBudget, shared by every attempt.
    _budget_ledger: BudgetLedger | None = field(default=None, repr=False)

    # Process-local sequence used to distinguish genuine allocation
    # re-executions from delivery retries of one completed observation. It
    # survives Tenacity re-entry because the same ExecutionStatus instance is
    # reused, but is deliberately excluded from persisted/product contracts.
    _allocation_attempt_sequence: int = field(default=0, repr=False)

    # Durable identity of the admitted run, when persistence is configured.
    # Populated by ``_admit_run``; ``None`` for executions started without a
    # session factory (most unit tests) or whose admission write failed.
    run_id: str | None = None

    # Capability contracts issued from the immutable admitted plan and
    # persisted with the lifecycle rows. Kept as contracts so a later tool
    # boundary can consume exactly what admission wrote, rather than
    # reconstructing authority from invocation data.
    capability_grants: tuple[CapabilityGrant, ...] = field(
        default_factory=tuple, repr=False
    )

    # The terminal status this execution really reached, set only while the
    # durable layer has not accepted it yet. ``status`` deliberately stays
    # non-terminal until then, so a reader is never told the run finished on
    # the strength of process memory alone. Cleared when the write lands.
    unconfirmed_terminal_status: str | None = None

    # In-memory copies of the durable Run/Task/Attempt contracts this
    # execution is writing through. They track the *last successfully
    # persisted* state so each subsequent transition advances from the real
    # row, not from the original admission-time snapshot. Never serialized —
    # a caller wanting durable state reads it from the repositories.
    _run: Run | None = field(default=None, repr=False)
    _task: Task | None = field(default=None, repr=False)
    _attempt: Attempt | None = field(default=None, repr=False)

    # This process's lease on the durable run. ``_lease_confirmed_until`` is a
    # monotonic instant taken from when a renewal was *sent*, so a slow
    # database can only make the local view stricter than the database's.
    _fencing_token: int | None = field(default=None, repr=False)
    _lease_confirmed_until: float = field(default=0.0, repr=False)
    _lease_lost: bool = field(default=False, repr=False)
    # Monotonic instant the workflow task ended, so its lease can outlive it
    # while a terminal outcome the database refused is still waiting to land.
    _workflow_finished_at: float | None = field(default=None, repr=False)


class DirectExecutionService:
    """
    Direct execution service using MASR routing and supervisor coordination.

    Replaces Temporal workflows with simplified direct execution that provides
    the same functionality with better performance and easier debugging.
    """

    def __init__(
        self,
        masr_router: MASRouter | None = None,
        supervisor_bridge: MASRSupervisorBridge | None = None,
        supervisor_factory: SupervisorFactory | None = None,
        event_publisher: EventPublisher | None = None,
        gemini_service: Any | None = None,
        session_factory: Any | None = None,
        outcome_recorder: RoutingOutcomeRecorder | None = None,
        component_registry: TypedRegistry | None = None,
        supervisor_registry: TypedRegistry | None = None,
        execution_authority_resolver: ExecutionAuthorityResolver | None = None,
        execution_plan_compiler: ExecutionPlanCompiler | None = None,
        capability_issuer: PlanCapabilityIssuer | None = None,
    ):
        """Initialize direct execution service."""

        if component_registry is not None and supervisor_registry is not None:
            raise TypeError(
                "Pass component_registry or supervisor_registry compatibility alias, "
                "not both"
            )
        if component_registry is None:
            component_registry = supervisor_registry
        if component_registry is None:
            from .component_catalog import get_default_component_registry

            component_registry = get_default_component_registry()
        self.component_registry = component_registry
        _validate_supervisor_registry(self.component_registry)
        for collaborator_name, collaborator in (
            ("MASRSupervisorBridge", supervisor_bridge),
            ("SupervisorFactory", supervisor_factory),
        ):
            collaborator_registry = getattr(
                collaborator,
                "component_registry",
                None,
            )
            if (
                isinstance(collaborator_registry, TypedRegistry)
                and collaborator_registry is not self.component_registry
            ):
                raise TypeError(
                    f"{collaborator_name} and DirectExecutionService must share "
                    "one component registry"
                )

        # Initialize components (would be injected in production)
        self.gemini_service = gemini_service
        self.session_factory = session_factory
        self.masr_router = masr_router or MASRouter()
        self.outcome_recorder = outcome_recorder or RoutingOutcomeRecorder(
            self.masr_router
        )
        self._owns_supervisor_bridge = supervisor_bridge is None
        self.supervisor_bridge = (
            MASRSupervisorBridge(
                gemini_service=gemini_service,
                component_registry=self.component_registry,
                session_factory=self.session_factory,
            )
            if supervisor_bridge is None
            else supervisor_bridge
        )
        self.supervisor_factory = supervisor_factory or SupervisorFactory(
            component_registry=self.component_registry,
        )
        self.event_publisher = event_publisher
        self.execution_authority_resolver = execution_authority_resolver
        self.execution_plan_compiler = (
            execution_plan_compiler or ExecutionPlanCompiler()
        )
        self.capability_issuer = capability_issuer or PlanCapabilityIssuer()

        # Execution tracking
        self.active_executions: dict[str, ExecutionStatus] = {}

        # Service configuration
        self.max_concurrent_executions = 50
        self.default_timeout_seconds = DEFAULT_AGENT_TIMEOUT
        self.enable_retry = True
        self.max_retries = MAX_RETRY_ATTEMPTS

        # Durable-write resilience. A lifecycle write is retried a bounded
        # number of times before it is treated as unavailable; a terminal
        # write that still has not landed is parked and reconciled in the
        # background for a bounded window rather than being dropped.
        self.durable_write_max_attempts = 3
        self.durable_write_retry_seconds = 0.1
        self.durable_reconcile_interval_seconds = 1.0
        self.durable_reconcile_max_seconds = 300.0

        # Execution ownership. A run is leased to one process at a time; the
        # lease is renewed well inside its lifetime, and the same loop polls
        # for a cancellation another process recorded.
        self.owner_instance_id = (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self.lease_ttl_seconds = 30.0
        self.lease_renew_seconds = 5.0
        self.cancel_poll_seconds = 1.0
        self.expired_run_scan_seconds = 15.0
        self.loop_restart_seconds = 5.0
        self._pending_terminal_outcomes: dict[str, _PendingTerminalOutcome] = {}
        self._terminal_reconciler: asyncio.Task[None] | None = None

        # Durable run identity -> execution_id, for the executions this
        # process admitted or restored. A replayed admission of an already
        # admitted run is coalesced onto the existing execution through this
        # index instead of starting a second one.
        self._execution_ids_by_run_id: dict[str, str] = {}

        # Durable run identity -> the admission currently claiming it. A
        # duplicate arriving while the first admission is still awaiting the
        # router or the database waits on this claim instead of racing it;
        # the entry exists from before the first await until the execution
        # id is known. Cross-process duplicates are caught instead by
        # ``agent_runs``' uniqueness on (tenant_id, idempotency_key).
        self._admissions_in_flight: dict[str, asyncio.Future[str]] = {}

        # Store background task references to prevent GC
        self._background_tasks: set[asyncio.Task[Any]] = set()
        # Cancellation writes shielded from the cancellation they record, so
        # ``close`` can wait for them instead of leaving a transaction running.
        self._shielded_writes: set[asyncio.Future[Any]] = set()
        self._workflow_tasks: dict[str, asyncio.Task[None]] = {}
        self._fast_path_provider: OpenRouterProvider | None = None
        self.closed = False
        # Performance metrics
        self.execution_stats = {
            "total_executions": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "average_execution_time": 0.0,
            "concurrent_executions": 0,
        }

    @asynccontextmanager
    async def _tenant_session(
        self, organization_id: str | uuid.UUID | None
    ) -> AsyncIterator[Any]:
        """Open a session whose every transaction sets Postgres RLS tenant context.

        Delegates to ``src.models.db.session.tenant_session`` so multi-transaction
        sessions (such as lease renewal followed by cancellation polling) retain
        ``app.current_org_id`` after each commit. Falls back to the raw session
        when ``organization_id`` is ``None`` or a test mock lacks ``sync_session``.
        """
        assert self.session_factory is not None
        if organization_id is None:
            async with self.session_factory() as session:
                yield session
            return
        async with self.session_factory() as session:
            if not hasattr(session, "sync_session"):
                yield session
                return

            def _borrow() -> Any:
                return _BorrowedSession(session)

            async with tenant_session(_borrow, organization_id) as scoped:
                yield scoped

    @property
    def supervisor_registry(self) -> TypedRegistry:
        """Retain the legacy name as a read-only view of the kernel catalog."""

        return self.component_registry

    def _measure_domain_output_tokens(
        self, domain: str, output: str, label: str = "domain_output"
    ) -> int:
        """Measure token count of a pre-stringified domain output using tiktoken.

        Callers must pass an already-stringified value; this method does not
        call str() again to avoid double-conversion (fix #9).

        Args:
            domain: Domain name for logging context
            output: Pre-stringified output content
            label: Log label (e.g., "before_truncation", "after_truncation")

        Returns:
            Token count (0 if telemetry disabled, tiktoken unavailable, or on error)
        """
        if not telemetry_enabled():
            return 0

        token_count = count_tokens(output)

        logger.info(
            "domain_output_token_measurement",
            domain=domain,
            label=label,
            token_count=token_count,
            content_length_chars=len(output),
        )

        return token_count

    async def _checkpoint(self, execution_status: ExecutionStatus, phase: str) -> None:
        """
        Create a checkpoint for the current execution state.

        Gracefully degrades when DB is unavailable — logs and continues.
        """
        if not self.session_factory:
            return
        run = execution_status._run
        if run is None:
            # A checkpoint's identity is its durable run's; the caller-visible
            # owner on the status is not authority.
            logger.warning(
                "Skipping checkpoint for an execution with no durable run",
                execution_id=execution_status.execution_id,
            )
            return

        try:
            async with self._tenant_session(run.tenant_id) as session:
                if execution_status._fencing_token is not None:
                    # A checkpoint carries the run's output, so a worker that
                    # lost its lease must not be able to publish it here.
                    await RunLifecycleRepository(session).require_fence(
                        run.run_id, execution_status._fencing_token
                    )
                repository = CheckpointRepository(session)

                checkpoint_data = {
                    "status": execution_status.status,
                    "progress_percentage": execution_status.progress_percentage,
                    "current_phase": execution_status.current_phase,
                    "routing_decision": execution_status.routing_decision,
                    "sub_routing_decisions": execution_status.sub_routing_decisions,
                    "supervisor_type": execution_status.supervisor_type,
                    "agent_results": execution_status.agent_results,
                    "quality_scores": execution_status.quality_scores,
                    "final_output": execution_status.final_output,
                    "workers_used": execution_status.workers_used,
                    "errors": execution_status.errors,
                    "warnings": execution_status.warnings,
                    "retry_count": execution_status.retry_count,
                }

                execution_metrics = {
                    "started_at": execution_status.started_at.isoformat(),
                    "execution_time_seconds": execution_status.execution_time_seconds,
                }

                await repository.create_checkpoint(
                    workflow_id=execution_status.execution_id,
                    project_id=uuid.UUID(execution_status.project_id),
                    checkpoint_data=checkpoint_data,
                    phase=phase,
                    checkpoint_type="automatic",
                    execution_metrics=execution_metrics,
                    run_id=run.run_id,
                    organization_id=run.tenant_id,
                )
                await session.commit()

                logger.debug(
                    "Created checkpoint",
                    execution_id=execution_status.execution_id,
                    phase=phase,
                )

        except StaleFenceError as e:
            logger.warning(
                "checkpoint_refused_after_lease_loss",
                execution_id=execution_status.execution_id,
                phase=phase,
                error=str(e),
            )
        except CheckpointIdentityError as e:
            logger.error(
                "checkpoint_identity_refused",
                execution_id=execution_status.execution_id,
                phase=phase,
                error=str(e),
            )
        except (SQLAlchemyError, OSError) as e:
            logger.warning(
                "Failed to create checkpoint (degrading gracefully)",
                execution_id=execution_status.execution_id,
                phase=phase,
                error=str(e),
            )

    # --- durable run/task/attempt persistence -----------------------------
    #
    # These methods make the persisted ``agent_runs``/``agent_run_tasks``/
    # ``agent_task_attempts``/``agent_run_events`` rows the authority for a
    # run's lifecycle, replacing ``active_executions`` as the thing a status
    # read trusts after a restart. Every write is best-effort — matching the
    # graceful-degradation posture ``_checkpoint`` already established — so a
    # missing session factory, an unpersistable tenant (e.g. a test fixture's
    # non-UUID ``tenant_id``), or a transient DB failure logs a warning and
    # lets execution continue in memory-only mode rather than crashing the
    # request. Only ONE task and ONE attempt is persisted per execution,
    # representing the whole compiled plan as a single unit of work — this
    # does not track the individual sub-agent tasks the supervisor bridge
    # dispatches internally, which is a coarser grain than the frozen
    # Task/Attempt contracts ultimately support and a natural next step, not
    # something this packet's scope covers.
    #
    # The event appended alongside each transition and its outbox row are
    # written in the same transaction as the row update (see
    # ``RunEventRepository.append_event``), so an outbox row is never visible
    # for a state change that didn't durably land — that is the whole of
    # "persist before publish" for the durable event stream. It says nothing
    # about the pre-existing ad hoc WebSocket progress broadcast
    # (``_publish_progress_update``), which is a separate, older delivery
    # path this packet does not gate or remove.

    def _new_task_and_attempt(
        self,
        run: Run,
        *,
        execution_id: str,
        project: ResearchProject,
        now: datetime,
    ) -> tuple[Task, Attempt]:
        """Build the one durable Task/Attempt representing this execution.

        ``task_key`` is set to ``execution_id`` so a cold-start rehydration
        (:meth:`restore_active_executions`) can recover the in-memory
        execution identity from the durable row. ``input`` carries the
        project binding — ``project_id`` in particular — since ``AgentRun``
        has no project column of its own; project-scoped reads
        (``src/api/routes/research.py``) key off it after a restart.
        """
        objective = (project.query.text or "").strip()[:2000] or "research execution"
        task = Task(
            task_id=str(uuid.uuid4()),
            run_id=run.run_id,
            task_key=execution_id,
            task_type="execution_plan",
            objective=objective,
            idempotency_key=f"{run.idempotency_key}:task",
            input={
                "project_id": str(project.id),
                "execution_id": execution_id,
                "domains": list(project.query.domains or []),
            },
            created_at=now,
            updated_at=now,
        )
        attempt = Attempt(
            attempt_id=str(uuid.uuid4()),
            task_id=task.task_id,
            ordinal=1,
            idempotency_key=f"{task.idempotency_key}:attempt-1",
            created_at=now,
            updated_at=now,
        )
        return task, attempt

    @staticmethod
    def _durable_agent_task_context(
        execution_status: ExecutionStatus,
    ) -> dict[str, Any]:
        """Return the complete lifecycle identity admitted for this execution.

        The plan-facing ``AgentTask`` has a process-local id of its own.  Tool
        callers must instead receive the contracts cached after admission, and
        memory-only executions must remain explicitly unscoped rather than
        receiving identifiers made up at this boundary.
        """
        if (
            execution_status._run is None
            or execution_status._task is None
            or execution_status._attempt is None
        ):
            return {}
        context: dict[str, Any] = {
            RUN_ID_CONTEXT_KEY: execution_status._run.run_id,
            TASK_ID_CONTEXT_KEY: execution_status._task.task_id,
            ATTEMPT_ID_CONTEXT_KEY: execution_status._attempt.attempt_id,
            ORGANIZATION_ID_CONTEXT_KEY: execution_status._run.tenant_id,
        }
        context[CAPABILITY_GRANTS_CONTEXT_KEY] = execution_status.capability_grants
        return context

    async def _admit_run(
        self,
        execution_status: ExecutionStatus,
        binding: ExecutionAuthorityBinding,
        project: ResearchProject,
    ) -> None:
        """Persist the admitted run, its task, its attempt, and its config snapshot.

        Advances the run/task to their post-admission resting states
        (``QUEUED``/``READY``) so exactly one further transition
        (``QUEUED``/``READY`` -> ``RUNNING``) is needed once execution
        actually starts. On success, caches the persisted contracts on
        ``execution_status`` so later phases advance from real state.

        The configuration snapshot is written in the same transaction as the
        run row, which is what makes a run *pinned*: the exact workflow,
        routing policy, worker assignments, provider policy, and budget it
        was admitted under survive a deployment that changes any of them.

        Raises:
            RunAdmissionError: Persistence is configured but the admission
                write did not land. Nothing is started — a run whose
                admission the database never accepted has no durable record
                for any later transition to advance, and continuing would
                mean reporting outcomes nothing backs. A service with no
                session factory has no such record to contradict and returns
                normally.
            RunAlreadyAdmittedError: The write failed because a concurrent,
                cross-process admission already durably won this exact run —
                not a database problem, so not treated as one. Carries the
                execution id the caller should coalesce onto.
            RunCapExceededError: The organization's concurrent or 24-hour run
                admission cap was exceeded; the transaction was rolled back and
                no row remains.
        """
        if not self.session_factory:
            return
        try:
            now = datetime.now(UTC)
            task, attempt = self._new_task_and_attempt(
                binding.run,
                execution_id=execution_status.execution_id,
                project=project,
                now=now,
            )
            queued_run = binding.run.transition_to(RunStatus.QUEUED, at=now)
            ready_task = task.transition_to(TaskStatus.READY, at=now)
            lease_sent_at = time.monotonic()

            async with self._tenant_session(binding.run.tenant_id) as session:
                try:
                    repo = RunLifecycleRepository(session)
                    await repo.create_run(
                        binding.run, organization_id=binding.run.tenant_id
                    )
                    await repo.enforce_admission_caps(
                        binding.run.tenant_id,
                        max_concurrent_per_org=settings.RUN_MAX_CONCURRENT_PER_ORG,
                        max_daily_per_org=settings.RUN_MAX_DAILY_PER_ORG,
                    )
                    await repo.record_run_transition(
                        queued_run, organization_id=binding.run.tenant_id
                    )
                    await repo.create_task(task, organization_id=binding.run.tenant_id)
                    await repo.record_task_transition(
                        ready_task, organization_id=binding.run.tenant_id
                    )
                    await repo.create_attempt(
                        attempt,
                        run_id=binding.run.run_id,
                        organization_id=binding.run.tenant_id,
                    )
                    # Owned from admission, in the same transaction, so there
                    # is no instant at which the run is running unowned. Leases
                    # use Postgres time; the SQLite development mode runs
                    # unleased and unfenced.
                    fencing_token = None
                    if session.get_bind().dialect.name == "postgresql":
                        fencing_token = await repo.acquire_lease(
                            binding.run.run_id,
                            owner=self.owner_instance_id,
                            ttl_seconds=self.lease_ttl_seconds,
                            organization_id=binding.run.tenant_id,
                        )
                        if fencing_token is None:
                            raise RuntimeError(
                                "a run admitted just now was already owned"
                            )
                    capability_grants = self.capability_issuer.issue(
                        binding,
                        run_id=queued_run.run_id,
                        task_id=task.task_id,
                        issued_at=now,
                    )
                    capability_repo = CapabilityRepository(session)
                    for grant in capability_grants:
                        await capability_repo.create_grant(
                            grant,
                            organization_id=queued_run.tenant_id,
                        )
                    await self._persist_config_snapshot(session, binding)
                    await self._append_event(
                        session,
                        run=queued_run,
                        event_type="run.admitted",
                        payload={
                            "execution_id": execution_status.execution_id,
                            "task_id": task.task_id,
                            "attempt_id": attempt.attempt_id,
                        },
                        occurred_at=now,
                    )
                    await session.commit()
                except BaseException:
                    await session.rollback()
                    raise

            execution_status.run_id = queued_run.run_id
            execution_status._fencing_token = fencing_token
            execution_status._lease_confirmed_until = (
                lease_sent_at + self.lease_ttl_seconds
            )
            execution_status._run = queued_run
            execution_status._task = ready_task
            execution_status._attempt = attempt
            execution_status.capability_grants = capability_grants
            self._execution_ids_by_run_id[queued_run.run_id] = (
                execution_status.execution_id
            )
        except IntegrityError as exc:
            # A cross-process duplicate admission of the exact same run
            # conflicts on ``uq_agent_run_tenant_idempotency`` — the run
            # really was admitted, just not by this process. Re-run the same
            # durable lookup a replayed admission uses (a fresh session: this
            # one is aborted by the constraint violation) and coalesce onto
            # whatever it finds. If nothing is found the conflict was not
            # this run after all, and the failure is a genuine one — fail
            # closed exactly as before rather than guessing.
            winner_execution_id = await self._durably_recorded_execution_id(
                binding.run.run_id, organization_id=binding.run.tenant_id
            )
            if winner_execution_id is not None:
                logger.info(
                    "cross_process_admission_coalesced",
                    execution_id=execution_status.execution_id,
                    run_id=binding.run.run_id,
                    winner_execution_id=winner_execution_id,
                )
                raise RunAlreadyAdmittedError(winner_execution_id) from exc
            logger.warning(
                "run_admission_persistence_failed",
                execution_id=execution_status.execution_id,
                run_id=binding.run.run_id,
                error=str(exc),
            )
            raise RunAdmissionError(
                f"run {binding.run.run_id!r} could not be admitted durably"
            ) from exc
        except RunCapExceededError:
            raise
        except Exception as exc:
            logger.warning(
                "run_admission_persistence_failed",
                execution_id=execution_status.execution_id,
                run_id=binding.run.run_id,
                error=str(exc),
            )
            raise RunAdmissionError(
                f"run {binding.run.run_id!r} could not be admitted durably"
            ) from exc

    async def _persist_config_snapshot(
        self, session: Any, binding: ExecutionAuthorityBinding
    ) -> None:
        """Freeze the configuration this run is pinned to, inside ``session``.

        Only the versions the authority binding actually carries are pinned
        as structured ``PinnedVersions``; the rest of the pinned semantics
        live in the serialized configuration blob, which is the complete
        compiled binding. Prompts, evaluators, and artifact schemas are not
        individually versioned by any component today, so nothing invents a
        version for them.

        A resolver that can persist (the production
        ``PersistedExecutionAuthorityResolver``) writes through its own
        ``register`` so its in-process cache and the durable row are warmed
        together; any other resolver still gets the durable row.
        """
        pinned_versions = PinnedVersions(
            workflow_definition_id=binding.run.workflow_definition_id,
            workflow_definition_version=binding.run.workflow_definition_version,
            routing_policy_id=binding.run.routing_policy_id,
            routing_policy_version=binding.run.routing_policy_version,
            event_envelope_version=EVENT_ENVELOPE_VERSION,
        )
        config_snapshot_id = f"{binding.run.run_id}:config"
        register = getattr(self.execution_authority_resolver, "register", None)
        if callable(register):
            await register(
                session,
                binding=binding,
                organization_id=binding.run.tenant_id,
                config_snapshot_id=config_snapshot_id,
                pinned_versions=pinned_versions,
            )
            return
        await RunConfigSnapshotRepository(session).create_snapshot(
            run=binding.run,
            organization_id=binding.run.tenant_id,
            config_snapshot_id=config_snapshot_id,
            authority_id=binding.authority_id,
            authority_version=binding.authority_version,
            pinned_versions=pinned_versions,
            configuration=serialize_execution_authority_binding(binding),
        )

    async def _append_event(
        self,
        session: Any,
        *,
        run: Run,
        event_id: str | None = None,
        event_type: str,
        payload: dict[str, Any],
        occurred_at: datetime,
        fencing_token: int | None = None,
    ) -> None:
        """Append one durable event and its outbox row within ``session``.

        With a ``fencing_token`` the append is refused unless that token still
        owns the run.

        The relay's only wired destination today is ``redis`` — a
        project-scoped WebSocket derivation from this persisted stream is
        Wave 3 Packet 3D's scope. Broadcasting an unscoped ``websocket``
        destination here would leak run payloads across tenants through
        ``ConnectionManager.broadcast_to_all``, so this deliberately does not
        request one.
        """
        if event_id is None:
            event_id = str(uuid.uuid4())
        if fencing_token is not None:
            await RunLifecycleRepository(session).require_fence(
                run.run_id, fencing_token
            )
        await RunEventRepository(session).append_event(
            run_id=run.run_id,
            organization_id=run.tenant_id,
            event_id=event_id,
            aggregate_type="run",
            aggregate_id=run.run_id,
            event_type=event_type,
            event_type_version="1",
            occurred_at=occurred_at,
            producer="direct_execution_service",
            deduplication_key=event_id,
            payload=payload,
            destinations=("redis",),
        )

    @staticmethod
    def _final_report_artifact_id(run: Run, task: Task, attempt: Attempt) -> str:
        """Derive one stable artifact identity for this terminal attempt."""
        scope = "\x00".join(
            (
                run.tenant_id,
                run.run_id,
                task.task_id,
                attempt.attempt_id,
                _FINAL_REPORT_ARTIFACT_KIND,
            )
        )
        return "final-report-" + hashlib.sha256(scope.encode("utf-8")).hexdigest()

    @staticmethod
    def _claim_support_id_factory(artifact_id: str) -> Callable[[], str]:
        """Return a deterministic id allocator for one inventory."""
        sequence = 0

        def allocate() -> str:
            nonlocal sequence
            sequence += 1
            return f"{artifact_id}:support-{sequence}"

        return allocate

    def _build_final_report(
        self,
        execution_status: ExecutionStatus,
        *,
        run: Run,
        task: Task | None,
        attempt: Attempt | None,
        occurred_at: datetime,
    ) -> _FinalReport:
        """Build a durable, inspectable report without writing anything."""
        if execution_status.final_output is None:
            raise ValueError("cannot persist a successful run without final_output")
        if task is None or attempt is None:
            raise ValueError(
                "cannot persist a final report without task and attempt identity"
            )

        text = _canonical_json(execution_status.final_output)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        artifact_id = self._final_report_artifact_id(run, task, attempt)
        artifact = Artifact(
            artifact_id=artifact_id,
            run_id=run.run_id,
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            kind=_FINAL_REPORT_ARTIFACT_KIND,
            media_type=_FINAL_REPORT_MEDIA_TYPE,
            storage_uri=f"{_FINAL_REPORT_STORAGE_URI_PREFIX}/{artifact_id}",
            content_sha256=digest,
            status=ArtifactStatus.FINAL,
            trust=TrustClassification.DERIVED_UNTRUSTED,
            producer="direct_execution_service",
            producer_kind=ProducerKind.SYSTEM,
            created_at=occurred_at,
            metadata={
                _FINAL_REPORT_CONTENT_METADATA_KEY: text,
                "content_encoding": "utf-8",
                "serialization": "canonical_json",
                "source": "final_output",
                "tenant_id": run.tenant_id,
                "run_id": run.run_id,
                "task_id": task.task_id,
                "attempt_id": attempt.attempt_id,
            },
        )
        return _FinalReport(artifact=artifact, text=text)

    @staticmethod
    def _validate_persisted_final_report(
        artifact: Any,
        *,
        final_report: _FinalReport,
        tenant_id: str,
    ) -> str:
        """Verify the database row still names and contains this report."""
        expected = final_report.artifact
        if (
            artifact.organization_id is None
            or str(artifact.organization_id) != tenant_id
            or artifact.artifact_id != expected.artifact_id
            or artifact.run_id != expected.run_id
            or artifact.task_id != expected.task_id
            or artifact.attempt_id != expected.attempt_id
            or artifact.kind != expected.kind
            or artifact.media_type != expected.media_type
            or artifact.storage_uri != expected.storage_uri
        ):
            raise ValueError("persisted final report identity does not match")

        report_text = artifact.metadata_.get(_FINAL_REPORT_CONTENT_METADATA_KEY)
        if not isinstance(report_text, str):
            raise ValueError("persisted final report has no inspectable text")
        if report_text != final_report.text:
            raise ValueError(
                "persisted final report text does not match the "
                "final_output serialization"
            )
        if (
            hashlib.sha256(report_text.encode("utf-8")).hexdigest()
            != expected.content_sha256
            or artifact.content_sha256 != expected.content_sha256
        ):
            raise ValueError(
                "persisted final report text does not match its content digest"
            )
        return report_text

    def _resolve_final_report_claims(
        self,
        artifact: Any,
        *,
        final_report: _FinalReport,
        run: Run,
        resolved_at: datetime,
    ) -> ClaimSupportResolution:
        """Resolve every material claim from the persisted report text."""
        report_text = self._validate_persisted_final_report(
            artifact, final_report=final_report, tenant_id=run.tenant_id
        )
        inventory = build_inventory(
            artifact_id=artifact.artifact_id,
            artifact_kind=artifact.kind,
            source=report_text,
        )
        return ClaimSupportResolver(
            id_factory=self._claim_support_id_factory(artifact.artifact_id)
        ).resolve(
            inventory=inventory,
            run_id=run.run_id,
            verdicts=(),
            resolved_at=resolved_at,
        )

    @staticmethod
    def _transition_event_id(
        *,
        run: Run,
        task: Task | None,
        attempt: Attempt | None,
        event_type: str,
    ) -> str:
        """Derive one event identity for one logical lifecycle transition.

        The identity describes the transition target rather than the
        wall-clock write attempt. Re-planning the same transition after an
        ambiguous write therefore refers to the same append-only event.
        """
        identity = json.dumps(
            {
                "tenant_id": run.tenant_id,
                "run_id": run.run_id,
                "run_status": run.status.value,
                "task_id": task.task_id if task is not None else None,
                "task_status": task.status.value if task is not None else None,
                "attempt_id": attempt.attempt_id if attempt is not None else None,
                "attempt_status": (
                    attempt.status.value if attempt is not None else None
                ),
                "event_type": event_type,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"direct-transition:{identity}"))

    @staticmethod
    def _transition_time(execution_status: ExecutionStatus) -> datetime:
        """Now, but never before a row's ``updated_at``.

        Those are stamped by the database's clock, which can run ahead of this
        process's; a transition earlier than its row's last update would be
        refused by the contract for a reason that is not the run's state.
        """
        stamps = [
            c.updated_at
            for c in (
                execution_status._run,
                execution_status._task,
                execution_status._attempt,
            )
            if c is not None
        ]
        return max([datetime.now(UTC), *stamps])

    async def _persist_transition(
        self,
        execution_status: ExecutionStatus,
        *,
        run_target: RunStatus,
        task_target: TaskStatus | None,
        attempt_target: AttemptStatus | None,
        event_type: str,
        payload: dict[str, Any],
        reason: str | None = None,
        fenced: bool = True,
        in_transaction: Callable[[Any], Awaitable[None]] | None = None,
    ) -> DurableWriteOutcome:
        """Advance the durable run/task/attempt and append their event.

        Returns what the durable layer did, so a caller can decide whether an
        observable state change is backed by anything. A rejected or
        unavailable write degrades the durability of this one transition, not
        the execution; an unreadable successful output fails closed before any
        terminal rows are written.

        ``RECORDED`` is also the answer when there is nothing to persist (no
        session factory, or this execution was never admitted — e.g. tests
        that drive ``_execute_research_workflow`` directly without going
        through ``start_research_execution``): memory-only mode has no
        durable state to contradict.

        A transient failure is retried a bounded number of times before the
        write is called unavailable, and an unavailable *terminal* write is
        parked for reconciliation rather than dropped — the outcome is the
        one thing a run cannot afford to lose.

        The write carries this process's fencing token unless ``fenced`` is
        false (a reconciler taking over a dead owner's run proves its right to
        write inside ``in_transaction`` instead). A write the durable record
        refuses, because the lease was lost or a cancellation got there first,
        is ``REJECTED``: the in-memory view is rebuilt from the durable rows,
        and a success that lost to a recorded cancellation finishes that
        cancellation rather than leaving the run cancelling forever.
        """
        if not self.session_factory or execution_status._run is None:
            return DurableWriteOutcome.RECORDED

        now = self._transition_time(execution_status)
        try:
            run = execution_status._run.transition_to(run_target, at=now, reason=reason)
            task = execution_status._task
            if task is not None and task_target is not None:
                task = task.transition_to(task_target, at=now, reason=reason)
            attempt = execution_status._attempt
            if attempt is not None and attempt_target is not None:
                attempt = attempt.transition_to(attempt_target, at=now, reason=reason)
        except ValueError as exc:
            # The durable record already moved past this transition (a
            # retried workflow re-entering a phase it left). Retrying cannot
            # help — the same write would be refused every time.
            logger.warning(
                "run_transition_rejected_by_contract",
                execution_id=execution_status.execution_id,
                run_id=execution_status._run.run_id,
                event_type=event_type,
                error=str(exc),
            )
            await self._finish_cancellation_that_won(execution_status, run_target)
            return DurableWriteOutcome.REJECTED

        final_report = None
        if run.status is RunStatus.SUCCEEDED:
            final_report = self._build_final_report(
                execution_status,
                run=run,
                task=task,
                attempt=attempt,
                occurred_at=now,
            )

        journaled_result = None
        if attempt is not None and attempt.status in TERMINAL_ATTEMPT_STATUSES:
            journaled_result = {
                "final_output": _journalable_value(execution_status.final_output),
                "agent_results": _journalable_value(execution_status.agent_results),
                "errors": list(execution_status.errors),
            }
        transition = _DurableTransition(
            run=run,
            task=task,
            attempt=attempt,
            event_type=event_type,
            payload=payload,
            occurred_at=now,
            event_id=self._transition_event_id(
                run=run,
                task=task,
                attempt=attempt,
                event_type=event_type,
            ),
            journaled_result=journaled_result,
            final_report=final_report,
            fencing_token=execution_status._fencing_token if fenced else None,
            in_transaction=in_transaction,
        )

        committed = await self._commit_transition(transition)
        if committed is DurableWriteOutcome.REJECTED:
            await self._adopt_durable_state(execution_status)
            await self._finish_cancellation_that_won(execution_status, run_target)
            return DurableWriteOutcome.REJECTED
        if committed is DurableWriteOutcome.RECORDED:
            execution_status._run = run
            execution_status._task = task
            execution_status._attempt = attempt
            # A landed write supersedes any earlier outcome still waiting to
            # be reconciled for this execution.
            self._pending_terminal_outcomes.pop(execution_status.execution_id, None)
            return DurableWriteOutcome.RECORDED

        if run.status in TERMINAL_RUN_STATUSES:
            self._park_terminal_outcome(execution_status, transition)
        return DurableWriteOutcome.UNAVAILABLE

    async def _adopt_durable_state(self, execution_status: ExecutionStatus) -> None:
        """Rebuild the in-memory run/task/attempt from the durable rows.

        After a refused write this process's copy is stale by definition: the
        durable record moved (a reconciliation failed the run, a cancellation
        was requested elsewhere). Best effort: if the database cannot be read
        the stale copy stays, and the next write is refused the same way.
        """
        if not self.session_factory or execution_status.run_id is None:
            return
        try:
            async with self._tenant_session(
                execution_status.organization_id
            ) as session:
                repo = RunLifecycleRepository(session)
                run_row = await repo.get_run(
                    execution_status.run_id,
                    organization_id=execution_status.organization_id,
                )
                if run_row is None:
                    return
                fresh = await self._rehydrate_execution(repo, run_row)
        except (SQLAlchemyError, OSError) as exc:
            logger.warning(
                "durable_state_refresh_failed",
                execution_id=execution_status.execution_id,
                error=str(exc),
            )
            return
        if fresh is not None:
            execution_status._run = fresh._run
            execution_status._task = fresh._task
            execution_status._attempt = fresh._attempt

    async def _finish_cancellation_that_won(
        self, execution_status: ExecutionStatus, refused_target: RunStatus
    ) -> None:
        """Turn a refused success into the cancellation that beat it.

        A cancellation request and a success racing end in exactly one
        terminal state. When the request committed first the success is
        refused, and nothing else would ever record CANCELLED for it: the
        workflow task has no pending cancellation left to react to.
        """
        run = execution_status._run
        if (
            refused_target is RunStatus.SUCCEEDED
            and run is not None
            and run.status is RunStatus.CANCELLING
        ):
            execution_status.status = "cancelled"
            await self._record_requested_cancellation(execution_status)

    def _lose_lease(self, execution_status: ExecutionStatus, *, reason: str) -> None:
        """Stop this process's work on a run it no longer owns."""
        if execution_status._lease_lost:
            return
        execution_status._lease_lost = True
        logger.error(
            "run_lease_lost",
            execution_id=execution_status.execution_id,
            run_id=execution_status.run_id,
            reason=reason,
        )
        workflow_task = self._workflow_tasks.get(execution_status.execution_id)
        if workflow_task is not None:
            workflow_task.cancel()

    def _ownership_guard(self, execution_status: ExecutionStatus) -> Callable[[], None]:
        """The check made immediately before every model call is admitted."""

        def guard() -> None:
            if (
                execution_status._lease_lost
                or time.monotonic() >= execution_status._lease_confirmed_until
            ):
                raise OwnerLostError("this process no longer confirms its run lease")
            if execution_status.status == "cancelled":
                raise asyncio.CancelledError("the run was cancelled")

        return guard

    def _model_call_journal(
        self, execution_status: ExecutionStatus
    ) -> ModelCallReservationRepository | None:
        """The durable journal of this run's model calls, or ``None`` in memory mode."""
        run = execution_status._run
        if not self.session_factory or run is None:
            return None
        plan = execution_status.execution_plan
        budget = plan.routing_decision.budget if plan is not None else None
        return ModelCallReservationRepository(
            self.session_factory,
            organization_id=run.tenant_id,
            # Reconciliation reads no ceilings, so a run rebuilt from the
            # database (no plan) can still settle its open calls.
            owner=ResearchRunJournalOwner(
                run.run_id,
                max_tokens=budget.max_total_tokens if budget else 0,
                max_cost_usd=budget.max_cost_usd if budget else Decimal(0),
            ),
        )

    def _invocation_context_entry(
        self, execution_status: ExecutionStatus
    ) -> dict[str, Any]:
        """The owner's context for governed model calls, once the run is leased.

        It carries the lease's fencing token and attempt, so a call made after
        the lease was lost is refused before it is reserved, and a dead
        owner's open calls can be settled from the journal.
        """
        journal = self._model_call_journal(execution_status)
        if (
            journal is None
            or execution_status._fencing_token is None
            or execution_status._attempt is None
        ):
            return {}

        return {
            MODEL_INVOCATION_CONTEXT_KEY: ModelInvocationContext(
                ledger=execution_status._budget_ledger,
                journal=journal,
                guard=self._ownership_guard(execution_status),
                fencing_token=execution_status._fencing_token,
                attempt_ref=execution_status._attempt.attempt_id,
                max_attempts=settings.MODEL_ROUTER_MAX_RETRIES,
            )
        }

    def _start_lease_heartbeat(
        self, execution_status: ExecutionStatus, workflow_task: "asyncio.Task[None]"
    ) -> "asyncio.Task[None]":
        """Renew the run's lease for as long as its workflow (or its outcome) needs it."""
        lease_task = asyncio.create_task(self._hold_lease(execution_status))
        self._background_tasks.add(lease_task)
        lease_task.add_done_callback(self._background_tasks.discard)
        lease_task.add_done_callback(
            lambda t: self._heartbeat_ended(execution_status, t)
        )

        def workflow_ended(_task: "asyncio.Task[None]") -> None:
            execution_status._workflow_finished_at = time.monotonic()
            # A terminal outcome the database refused is still owed to the
            # durable record, and recording it needs the lease.
            if execution_status.execution_id not in self._pending_terminal_outcomes:
                lease_task.cancel()

        workflow_task.add_done_callback(workflow_ended)
        return lease_task

    def _heartbeat_ended(
        self, execution_status: ExecutionStatus, task: "asyncio.Task[None]"
    ) -> None:
        """A heartbeat that crashed is a lease nobody renews: stop the work."""
        if task.cancelled() or task.exception() is None:
            return
        logger.error(
            "run_heartbeat_crashed",
            execution_id=execution_status.execution_id,
            exc_info=task.exception(),
        )
        self._lose_lease(execution_status, reason="heartbeat crashed")

    async def _renew_lease_now(self, execution_status: ExecutionStatus) -> None:
        """Renew once before a parked terminal write, so it is not refused as stale."""
        token = execution_status._fencing_token
        if token is None or execution_status.run_id is None or not self.session_factory:
            return
        try:
            async with self._tenant_session(
                execution_status.organization_id
            ) as session:
                await RunLifecycleRepository(session).renew_lease(
                    execution_status.run_id,
                    owner=self.owner_instance_id,
                    fencing_token=token,
                    ttl_seconds=self.lease_ttl_seconds,
                )
                await session.commit()
        except (SQLAlchemyError, OSError) as exc:
            logger.warning(
                "run_lease_renewal_unconfirmed",
                run_id=execution_status.run_id,
                error=str(exc),
            )

    async def _release_leases(self) -> None:
        """Expire this process's leases so its runs are reconciled at the next scan.

        Called once the work has stopped. A graceful shutdown leaves a run the
        next process should fail as interrupted; waiting out the lease would
        only delay that.
        """
        if not self.session_factory:
            return
        for execution in list(self.active_executions.values()):
            token, run = execution._fencing_token, execution._run
            if (
                token is None
                or run is None
                or execution._lease_lost
                or execution.restored
                or run.status in TERMINAL_RUN_STATUSES
            ):
                continue
            try:
                async with self._tenant_session(run.tenant_id) as session:
                    await RunLifecycleRepository(session).release_lease(
                        run.run_id, owner=self.owner_instance_id, fencing_token=token
                    )
                    await session.commit()
            except (SQLAlchemyError, OSError) as exc:
                logger.warning(
                    "run_lease_release_failed", run_id=run.run_id, error=str(exc)
                )

    async def _hold_lease(self, execution_status: ExecutionStatus) -> None:
        """Keep the run's lease alive and watch for a cancellation recorded elsewhere.

        Runs until cancelled. A renewal the database refuses means ownership
        was lost; a renewal that merely cannot be confirmed is retried until
        the last confirmed lease would have expired.
        """
        token = execution_status._fencing_token
        run_id = execution_status.run_id
        if self.session_factory is None or token is None or run_id is None:
            return
        last_renewed = time.monotonic()
        while True:
            await asyncio.sleep(self.cancel_poll_seconds)
            finished_at = execution_status._workflow_finished_at
            if (
                finished_at is not None
                and execution_status.execution_id not in self._pending_terminal_outcomes
            ):
                return
            sent_at = time.monotonic()
            refused = False
            try:
                async with self._tenant_session(
                    execution_status.organization_id
                ) as session:
                    repo = RunLifecycleRepository(session)
                    if sent_at - last_renewed >= self.lease_renew_seconds:
                        refused = not await repo.renew_lease(
                            run_id,
                            owner=self.owner_instance_id,
                            fencing_token=token,
                            ttl_seconds=self.lease_ttl_seconds,
                        )
                        if not refused:
                            await session.commit()
                            last_renewed = sent_at
                            execution_status._lease_confirmed_until = (
                                sent_at + self.lease_ttl_seconds
                            )
                    if (
                        not refused
                        and execution_status.status != "cancelled"
                        and await repo.cancellation_requested(run_id)
                    ):
                        await self._adopt_remote_cancellation(execution_status)
            except (SQLAlchemyError, OSError) as exc:
                logger.warning(
                    "run_lease_renewal_unconfirmed",
                    run_id=run_id,
                    error=str(exc),
                )
            # Outside the session block: cancelling the workflow ends this
            # task's own cancel-on-finish callback race with session cleanup.
            if refused:
                self._lose_lease(execution_status, reason="renewal refused")
                return
            if (
                finished_at is None
                and time.monotonic() >= execution_status._lease_confirmed_until
            ):
                self._lose_lease(execution_status, reason="lease unconfirmed")
                return

    async def _adopt_remote_cancellation(
        self, execution_status: ExecutionStatus
    ) -> None:
        """Stop local work for a cancellation another process durably requested."""
        await self._adopt_durable_state(execution_status)
        run = execution_status._run
        if run is None or run.status is not RunStatus.CANCELLING:
            return
        execution_status.status = "cancelled"
        execution_status.current_phase = "cancelled"
        workflow_task = self._workflow_tasks.get(execution_status.execution_id)
        if workflow_task is not None:
            workflow_task.cancel()

    async def reconcile_expired_runs(self) -> int:
        """Fail every run whose owner's lease expired, settling its open model calls.

        A run whose lease is live, or that has no owner at all, is left alone;
        a lease that is renewed while this runs wins. Nothing is redispatched:
        an interrupted run is a failed run, and running it again is an explicit
        new run.

        Returns:
            The number of runs reconciled on this pass.
        """
        if not self.session_factory:
            return 0
        try:
            async with self.session_factory() as session:
                repo = RunLifecycleRepository(session)
                interrupted = []
                parked = {
                    p.transition.run.run_id
                    for p in self._pending_terminal_outcomes.values()
                }
                for run_row in await repo.list_expired_runs():
                    if run_row.run_id in parked:
                        # This process is still trying to record its outcome.
                        continue
                    rebuilt = await self._rehydrate_execution(repo, run_row)
                    if rebuilt is not None:
                        interrupted.append(rebuilt)
        except (SQLAlchemyError, OSError) as exc:
            run_reconciliation_failures_total.inc()
            logger.warning("expired_run_scan_failed", error=str(exc))
            return 0
        return sum([await self._reconcile_interrupted(es) for es in interrupted])

    async def _reconcile_interrupted(self, execution_status: ExecutionStatus) -> int:
        """Fail one dead owner's run and settle its open model calls, atomically."""
        run = execution_status._run
        if run is None:
            return 0
        journal = self._model_call_journal(execution_status)
        payload: dict[str, Any] = {"reason": PROCESS_INTERRUPTED}

        async def take_over_and_settle(session: Any) -> None:
            await RunLifecycleRepository(session).take_over_expired_run(run.run_id)
            if journal is None:
                return
            settled = await journal.reconcile_owner(session=session)
            totals = await journal.totals(session=session)
            payload["model_calls"] = {
                "released_unsent": settled.released_unsent,
                "settled_unknown": settled.settled_unknown,
                "uncertain_tokens": totals.uncertain_tokens,
                "uncertain_cost_usd": str(totals.uncertain_cost_usd),
                "known_tokens": totals.known_tokens,
                "known_cost_usd": str(totals.known_cost_usd),
            }

        execution_status.failure_code = PROCESS_INTERRUPTED
        outcome = await self._persist_transition(
            execution_status,
            run_target=RunStatus.FAILED,
            task_target=TaskStatus.FAILED,
            attempt_target=AttemptStatus.FAILED,
            event_type="run.interrupted",
            payload=payload,
            reason=PROCESS_INTERRUPTED,
            fenced=False,
            in_transaction=take_over_and_settle,
        )
        if outcome is DurableWriteOutcome.UNAVAILABLE:
            run_reconciliation_failures_total.inc()
            return 0
        if outcome is DurableWriteOutcome.REJECTED:
            # A live lease won, or another reconciler got there first.
            return 0
        run_ownership_expired_total.inc()
        self._settle_terminal_state(
            execution_status, outcome=outcome, status="failed", phase="failed"
        )
        self.active_executions[execution_status.execution_id] = execution_status
        self._execution_ids_by_run_id[run.run_id] = execution_status.execution_id
        return 1

    def start_expired_run_reconciliation(self) -> None:
        """Periodically reconcile runs whose owners died after this process started."""
        if self.closed or any(
            t.get_name() == "expired-run-reconciler" and not t.done()
            for t in self._background_tasks
        ):
            return
        self._spawn_reconciler(delay=0.0)

    def _spawn_reconciler(self, *, delay: float) -> None:
        task = asyncio.create_task(
            self._reconcile_forever(delay), name="expired-run-reconciler"
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(self._restart_reconciler)

    async def _reconcile_forever(self, delay: float) -> None:
        await asyncio.sleep(delay)
        while True:
            await asyncio.sleep(self.expired_run_scan_seconds)
            await self.reconcile_expired_runs()

    def _restart_reconciler(self, task: "asyncio.Task[None]") -> None:
        # A bug in one pass must not end reconciliation for the life of the
        # process, so a crashed loop is logged, counted and respawned.
        if self.closed or task.cancelled():
            return
        run_reconciliation_failures_total.inc()
        logger.error("expired_run_reconciler_crashed", exc_info=task.exception())
        self._spawn_reconciler(delay=self.loop_restart_seconds)

    async def _record_attempt_failure(
        self, execution_status: ExecutionStatus, *, error: str
    ) -> None:
        """Append a durable event for a failure another attempt will follow.

        Deliberately no status transition: the run is still RUNNING and its
        one attempt row is still the live attempt. This is the audit trail
        for a transient failure, not the run's outcome, so a database that
        refuses it degrades the history rather than the execution.
        """
        if not self.session_factory or execution_status._run is None:
            return
        try:
            async with self._tenant_session(execution_status._run.tenant_id) as session:
                await self._append_event(
                    session,
                    run=execution_status._run,
                    event_type="run.attempt_failed",
                    payload={"error": error, "attempt": execution_status.retry_count},
                    occurred_at=datetime.now(UTC),
                    fencing_token=execution_status._fencing_token,
                )
                await session.commit()
        except (SQLAlchemyError, OSError, StaleFenceError) as exc:
            logger.warning(
                "attempt_failure_event_not_recorded",
                execution_id=execution_status.execution_id,
                run_id=execution_status._run.run_id,
                error=str(exc),
            )

    async def _terminal_transition_already_committed(
        self, session: Any, transition: _DurableTransition
    ) -> bool:
        """Recognize a complete lifecycle transaction after an ambiguous commit."""
        run = transition.run
        lifecycle_repo = RunLifecycleRepository(session)
        run_row = await lifecycle_repo.get_run(
            run.run_id, organization_id=run.tenant_id
        )
        if (
            run_row is None
            or run_row.run_id != run.run_id
            or run_row.tenant_id != run.tenant_id
            or run_row.organization_id is None
            or str(run_row.organization_id) != run.tenant_id
            or run_row.status != run.status.value
        ):
            return False

        task_row: Any | None = None
        if transition.task is not None:
            task_rows = await lifecycle_repo.get_tasks_for_run(
                run.run_id, organization_id=run.tenant_id
            )
            matching_task_rows = [
                row
                for row in task_rows
                if (
                    row.task_id == transition.task.task_id
                    and row.run_id == run.run_id
                    and row.organization_id is not None
                    and str(row.organization_id) == run.tenant_id
                    and row.status == transition.task.status.value
                )
            ]
            if len(matching_task_rows) != 1:
                return False
            task_row = matching_task_rows[0]

        attempt_row: Any | None = None
        if transition.attempt is not None:
            attempt_rows = await lifecycle_repo.get_attempts_for_task(
                transition.attempt.task_id, organization_id=run.tenant_id
            )
            matching_attempt_rows = [
                row
                for row in attempt_rows
                if (
                    row.attempt_id == transition.attempt.attempt_id
                    and row.task_id == transition.attempt.task_id
                    and row.run_id == run.run_id
                    and row.organization_id is not None
                    and str(row.organization_id) == run.tenant_id
                    and row.status == transition.attempt.status.value
                )
            ]
            if len(matching_attempt_rows) != 1:
                return False
            attempt_row = matching_attempt_rows[0]
            if task_row is not None and attempt_row.task_id != task_row.task_id:
                return False
            if (
                transition.journaled_result is not None
                and attempt_row.journaled_result != transition.journaled_result
            ):
                return False

        events = await RunEventRepository(session).read_events_after(
            run.run_id,
            after_sequence=0,
            limit=100_000,
            organization_id=run.tenant_id,
        )
        matching_events = [
            event for event in events if event.event_id == transition.event_id
        ]
        if not matching_events:
            return False
        if len(matching_events) != 1:
            raise ValueError(
                "persisted lifecycle transition has duplicate event identities"
            )
        event = matching_events[0]
        if (
            event.organization_id is None
            or str(event.organization_id) != run.tenant_id
            or event.run_id != run.run_id
            or event.aggregate_type != "run"
            or event.aggregate_id != run.run_id
            or event.deduplication_key != transition.event_id
            or event.event_type != transition.event_type
            or event.payload != transition.payload
        ):
            # Same transition identity, different content: another process
            # recorded this transition first (two reconcilers, for one).
            raise InvalidTransitionError(
                "the durable record already holds this transition as "
                "recorded by another writer"
            )

        final_report = transition.final_report
        if final_report is not None:
            artifact = await ArtifactRepository(session).get_artifact(
                final_report.artifact.artifact_id, organization_id=run.tenant_id
            )
            if artifact is None:
                return False

            resolution = self._resolve_final_report_claims(
                artifact,
                final_report=final_report,
                run=run,
                resolved_at=transition.occurred_at,
            )
            claim_repo = ClaimSupportRepository(session)
            existing_rows: list[Any] = []
            for support in resolution.supports:
                row = await claim_repo.get_claim_support(
                    support.claim_support_id, organization_id=run.tenant_id
                )
                if row is None:
                    if existing_rows:
                        raise ValueError(
                            "persisted final report has a partial claim-support resolution"
                        )
                    return False
                existing_rows.append(row)

            for expected, actual in zip(
                resolution.supports, existing_rows, strict=True
            ):
                if (
                    actual.organization_id is None
                    or str(actual.organization_id) != run.tenant_id
                    or actual.claim_support_id != expected.claim_support_id
                    or actual.run_id != expected.run_id
                    or actual.artifact_id != expected.artifact_id
                    or actual.claim_id != expected.claim_id
                    or actual.claim_text != expected.claim_text
                    or actual.status != expected.status.value
                    or tuple(actual.evidence_ids or ()) != expected.evidence_ids
                    or actual.absent_evidence_reason
                    != (
                        expected.absent_evidence_reason.value
                        if expected.absent_evidence_reason
                        else None
                    )
                    or actual.evaluator_id != expected.evaluator_id
                    or actual.evaluator_version != expected.evaluator_version
                    or actual.producer_kind != expected.producer_kind.value
                    or actual.explanation != expected.explanation
                ):
                    raise ValueError(
                        "persisted claim support does not match resolution"
                    )

        return True

    async def _commit_transition(
        self, transition: _DurableTransition
    ) -> DurableWriteOutcome:
        """Commit one planned transition, retrying transient failures.

        All writes share a single transaction and a single commit, so a
        failure anywhere inside leaves the durable record exactly where it
        was — which is what makes a retry safe to attempt at all.

        A lost lease or a status the durable row cannot leave that way is not
        transient, so it is ``REJECTED`` at once instead of being retried.
        """
        if not self.session_factory:
            return DurableWriteOutcome.UNAVAILABLE
        run = transition.run
        last_error: Exception | None = None
        for attempt_number in range(1, self.durable_write_max_attempts + 1):
            try:
                async with self._tenant_session(run.tenant_id) as session:
                    try:
                        if await self._terminal_transition_already_committed(
                            session, transition
                        ):
                            return DurableWriteOutcome.RECORDED

                        repo = RunLifecycleRepository(session)
                        fence = transition.fencing_token
                        if fence is not None:
                            await repo.require_fence(run.run_id, fence)
                        if transition.in_transaction is not None:
                            await transition.in_transaction(session)
                        await repo.record_run_transition(
                            run, organization_id=run.tenant_id, fencing_token=fence
                        )
                        if transition.task is not None:
                            await repo.record_task_transition(
                                transition.task,
                                organization_id=run.tenant_id,
                                fencing_token=fence,
                            )
                        if transition.attempt is not None:
                            await repo.record_attempt_transition(
                                transition.attempt,
                                organization_id=run.tenant_id,
                                fencing_token=fence,
                            )
                            if transition.journaled_result is not None:
                                await repo.write_journaled_result(
                                    transition.attempt.attempt_id,
                                    organization_id=run.tenant_id,
                                    result=transition.journaled_result,
                                    fencing_token=fence,
                                )

                        if transition.final_report is not None:
                            artifact = await ArtifactRepository(
                                session
                            ).create_artifact(
                                transition.final_report.artifact,
                                organization_id=run.tenant_id,
                            )
                            resolution = self._resolve_final_report_claims(
                                artifact,
                                final_report=transition.final_report,
                                run=run,
                                resolved_at=transition.occurred_at,
                            )
                            await ClaimSupportRecorder(
                                ClaimSupportRepository(session)
                            ).record(resolution, organization_id=run.tenant_id)

                        await self._append_event(
                            session,
                            run=run,
                            event_id=transition.event_id,
                            event_type=transition.event_type,
                            payload=transition.payload,
                            occurred_at=transition.occurred_at,
                        )
                        await session.commit()
                    except BaseException:
                        await session.rollback()
                        raise
                return DurableWriteOutcome.RECORDED
            except (StaleFenceError, InvalidTransitionError) as exc:
                logger.warning(
                    "run_transition_refused_by_durable_record",
                    run_id=run.run_id,
                    event_type=transition.event_type,
                    error=str(exc),
                )
                return DurableWriteOutcome.REJECTED
            except Exception as exc:
                last_error = exc
                if attempt_number < self.durable_write_max_attempts:
                    await asyncio.sleep(
                        self.durable_write_retry_seconds * 2 ** (attempt_number - 1)
                    )
        logger.warning(
            "run_transition_persistence_failed",
            run_id=run.run_id,
            event_type=transition.event_type,
            attempts=self.durable_write_max_attempts,
            error=str(last_error),
        )
        return DurableWriteOutcome.UNAVAILABLE

    def _park_terminal_outcome(
        self, execution_status: ExecutionStatus, transition: _DurableTransition
    ) -> None:
        """Hold a terminal outcome the database refused, and keep retrying it.

        The observable status is left alone here; :meth:`_settle_terminal_state`
        is what decides what a caller gets to see, and it withholds the
        terminal flip for exactly this case.
        """
        self._pending_terminal_outcomes[execution_status.execution_id] = (
            _PendingTerminalOutcome(
                transition=transition,
                execution_status=execution_status,
                observable_status=execution_status.status,
                observable_phase=execution_status.current_phase,
                observable_progress=None,
                expires_at=time.monotonic() + self.durable_reconcile_max_seconds,
            )
        )
        logger.error(
            "terminal_outcome_not_durably_recorded",
            execution_id=execution_status.execution_id,
            run_id=transition.run.run_id,
            run_status=transition.run.status.value,
        )
        self._schedule_terminal_reconciliation()

    def _settle_terminal_state(
        self,
        execution_status: ExecutionStatus,
        *,
        outcome: DurableWriteOutcome,
        status: str,
        phase: str,
        progress: float | None = None,
    ) -> None:
        """Publish a terminal status only if the durable layer accepted it.

        Three answers, three behaviours:

        - ``RECORDED`` — the database holds this outcome, so publish it.
        - ``UNAVAILABLE`` — the write never landed. The flip is withheld: the
          execution keeps a non-terminal status, records which outcome is
          waiting on the database, and reconciliation performs the flip once
          the write lands.
        - ``REJECTED`` — the durable record already moved somewhere this
          transition cannot reach, so the requested status is one the
          database contradicts. Publishing it anyway is how a run came to
          read "completed" while its durable row said "failed"; instead the
          in-memory view is realigned onto the status the durable record
          actually holds.
        """
        if outcome is DurableWriteOutcome.REJECTED:
            self._realign_with_durable_record(execution_status, refused_status=status)
            return
        if outcome is DurableWriteOutcome.UNAVAILABLE:
            pending = self._pending_terminal_outcomes.get(execution_status.execution_id)
            if pending is not None:
                pending.observable_status = status
                pending.observable_phase = phase
                pending.observable_progress = progress
            execution_status.unconfirmed_terminal_status = status
            execution_status.current_phase = AWAITING_DURABLE_CONFIRMATION_PHASE
            logger.warning(
                "terminal_status_withheld_pending_durable_write",
                execution_id=execution_status.execution_id,
                run_id=execution_status.run_id,
                withheld_status=status,
            )
            return
        execution_status.status = status
        execution_status.current_phase = phase
        if progress is not None:
            execution_status.progress_percentage = progress
        execution_status.unconfirmed_terminal_status = None

    def _realign_with_durable_record(
        self, execution_status: ExecutionStatus, *, refused_status: str
    ) -> None:
        """Adopt the status the durable record holds after a refused write.

        ``execution_status._run`` tracks the last state the database
        accepted, so it — not the outcome this process wanted to publish —
        is what a caller is allowed to be told.
        """
        run = execution_status._run
        if run is None:  # pragma: no cover - REJECTED implies an admitted run
            return
        durable_status = _RUN_STATUS_TO_EXECUTION_STATUS.get(
            run.status.value, run.status.value
        )
        logger.warning(
            "run_status_realigned_with_durable_record",
            execution_id=execution_status.execution_id,
            run_id=run.run_id,
            refused_status=refused_status,
            durable_status=durable_status,
        )
        execution_status.status = durable_status
        execution_status.current_phase = run.status.value
        execution_status.unconfirmed_terminal_status = None
        if run.status is RunStatus.SUCCEEDED:
            execution_status.progress_percentage = 100.0

    async def reconcile_pending_terminal_transitions(self) -> int:
        """Retry terminal outcomes the database refused, and publish the ones
        that land.

        Safe to call at any time; it is what turns "the outcome is known but
        unrecorded" back into "the outcome is recorded", and it is the only
        thing that flips an execution parked in
        ``awaiting_durable_confirmation`` to its real terminal status.

        Returns:
            The number of outcomes durably recorded on this pass.
        """
        reconciled = 0
        for execution_id, pending in list(self._pending_terminal_outcomes.items()):
            if time.monotonic() >= pending.expires_at:
                # Dropping it also ends the renewal of its lease.
                self._pending_terminal_outcomes.pop(execution_id, None)
                logger.error(
                    "terminal_outcome_reconciliation_abandoned",
                    execution_id=execution_id,
                    run_id=pending.transition.run.run_id,
                    elapsed_seconds=self.durable_reconcile_max_seconds,
                )
                continue
            await self._renew_lease_now(pending.execution_status)
            outcome = await self._commit_transition(pending.transition)
            if outcome is DurableWriteOutcome.UNAVAILABLE:
                continue
            self._pending_terminal_outcomes.pop(execution_id, None)
            if outcome is DurableWriteOutcome.REJECTED:
                # The durable record moved on while this outcome waited (a
                # reconciler took the run over): drop it rather than retry a
                # write that can never land.
                await self._adopt_durable_state(pending.execution_status)
                self._realign_with_durable_record(
                    pending.execution_status,
                    refused_status=pending.observable_status,
                )
                continue
            execution_status = pending.execution_status
            execution_status._run = pending.transition.run
            execution_status._task = pending.transition.task
            execution_status._attempt = pending.transition.attempt
            self._settle_terminal_state(
                execution_status,
                outcome=DurableWriteOutcome.RECORDED,
                status=pending.observable_status,
                phase=pending.observable_phase,
                progress=pending.observable_progress,
            )
            reconciled += 1
            logger.info(
                "terminal_outcome_reconciled",
                execution_id=execution_id,
                run_id=pending.transition.run.run_id,
                run_status=pending.transition.run.status.value,
            )
        return reconciled

    def _schedule_terminal_reconciliation(self) -> None:
        """Ensure a background reconciler is draining the parked outcomes."""

        if self.closed:
            return
        task = self._terminal_reconciler
        if task is not None and not task.done():
            return
        self._terminal_reconciler = asyncio.create_task(self._reconcile_until_drained())
        self._background_tasks.add(self._terminal_reconciler)
        self._terminal_reconciler.add_done_callback(self._background_tasks.discard)

    async def _reconcile_until_drained(self) -> None:
        """Retry parked terminal outcomes until each lands or its own window ends."""
        while self._pending_terminal_outcomes:
            await asyncio.sleep(self.durable_reconcile_interval_seconds)
            try:
                await self.reconcile_pending_terminal_transitions()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("terminal_outcome_reconciliation_failed", error=str(exc))

    async def _persist_cancellation(
        self, execution_status: ExecutionStatus
    ) -> DurableWriteOutcome:
        """Persist a cancellation request onto the run/task/attempt.

        This records ``CANCELLING`` for a running record (via
        :meth:`Run.request_cancellation`); ``CANCELLED`` is recorded only by
        the workflow once its task has actually stopped.

        Returns:
            What the durable layer did with the request, so
            :meth:`cancel_execution` can decide whether it is entitled to
            tell the caller the cancellation took. ``RECORDED`` also covers
            memory-only mode, where there is no durable record to disagree.
        """
        if not self.session_factory or execution_status._run is None:
            return DurableWriteOutcome.RECORDED
        try:
            now = self._transition_time(execution_status)
            reason = "Execution cancelled by request"
            run = execution_status._run.request_cancellation(at=now, reason=reason)
            task = execution_status._task
            if task is not None:
                task = task.request_cancellation(at=now, reason=reason)
            attempt = execution_status._attempt
            if attempt is not None:
                attempt = attempt.request_cancellation(at=now, reason=reason)

            async with self._tenant_session(run.tenant_id) as session:
                repo = RunLifecycleRepository(session)
                await repo.record_run_transition(run, organization_id=run.tenant_id)
                if task is not None:
                    await repo.record_task_transition(
                        task, organization_id=run.tenant_id
                    )
                if attempt is not None:
                    await repo.record_attempt_transition(
                        attempt, organization_id=run.tenant_id
                    )
                await self._append_event(
                    session,
                    run=run,
                    event_type="run.cancellation_requested",
                    payload={"reason": reason},
                    occurred_at=now,
                )
                await session.commit()

            execution_status._run = run
            execution_status._task = task
            execution_status._attempt = attempt
            return DurableWriteOutcome.RECORDED
        except InvalidTransitionError as exc:
            # The durable record is already past cancelling — nothing to
            # request, and nothing a retry could change.
            logger.warning(
                "run_cancellation_rejected_by_contract",
                execution_id=execution_status.execution_id,
                run_id=execution_status._run.run_id if execution_status._run else None,
                error=str(exc),
            )
            return DurableWriteOutcome.REJECTED
        except (SQLAlchemyError, OSError) as exc:
            logger.warning(
                "run_cancellation_persistence_failed",
                execution_id=execution_status.execution_id,
                run_id=execution_status._run.run_id if execution_status._run else None,
                error=str(exc),
            )
            return DurableWriteOutcome.UNAVAILABLE

    async def restore_active_executions(self) -> int:
        """Rehydrate in-flight runs from persistence at startup.

        ``active_executions`` is process memory; without this, every
        non-terminal run admitted before a restart appears to have vanished
        to the many call sites that read the dict directly rather than
        through :meth:`get_execution_status`
        (``src/api/routes/research.py``'s project status/results/cancel
        endpoints all iterate ``active_executions.values()``). Call once at
        startup, before the app serves traffic — mirrors
        ``PersistedExecutionAuthorityResolver.warm_from_snapshots`` for the
        authority cache.

        Scans every tenant (``organization_id=None``) because this runs
        before any authenticated request context exists. Only non-terminal
        runs are restored; a run that reached a terminal status before the
        restart already has its outcome durably recorded and does not need
        an in-memory placeholder. A run whose owner's lease has expired is
        reconciled as ``PROCESS_INTERRUPTED``; one under a live lease belongs
        to another replica and is left alone, without counting against this
        process's concurrency cap. A run no fenced process ever owned (a row
        from before ownership existed) cannot be proven dead by a lease, so it
        is failed only once it is past its own deadline plus a margin.

        Returns:
            The number of executions rehydrated.
        """
        if not self.session_factory:
            return 0
        restored = 0
        try:
            async with self.session_factory() as session:
                lifecycle_repo = RunLifecycleRepository(session)
                runs = await lifecycle_repo.list_non_terminal_runs(organization_id=None)
                expired = {
                    row.run_id for row in await lifecycle_repo.list_expired_runs()
                }
                for run_row in runs:
                    execution_status = await self._rehydrate_execution(
                        lifecycle_repo, run_row
                    )
                    if execution_status is None:
                        continue
                    execution_id = execution_status.execution_id
                    if execution_id in self.active_executions:
                        continue
                    if run_row.run_id in expired:
                        if await self._reconcile_interrupted(execution_status):
                            restored += 1
                        continue
                    # Another replica may still be running this work, and
                    # nothing here resumes it (issue #296). A leased run is
                    # live until its lease expires; only a run with no owner
                    # on record falls back to its own deadline.
                    if (
                        run_row.owner_instance_id is None
                        and await self._is_past_deadline(session, run_row)
                    ):
                        outcome = await self._persist_transition(
                            execution_status,
                            run_target=RunStatus.FAILED,
                            task_target=TaskStatus.FAILED,
                            attempt_target=AttemptStatus.FAILED,
                            event_type="run.interrupted",
                            payload={"reason": INTERRUPTED_BY_RESTART},
                            reason=INTERRUPTED_BY_RESTART,
                        )
                        self._settle_terminal_state(
                            execution_status,
                            outcome=outcome,
                            status="failed",
                            phase="failed",
                        )
                    else:
                        execution_status.restored = True
                    self.active_executions[execution_id] = execution_status
                    self._execution_ids_by_run_id[run_row.run_id] = execution_id
                    restored += 1
        except (SQLAlchemyError, OSError) as exc:
            logger.warning("active_execution_restore_failed", error=str(exc))
        return restored

    async def _is_past_deadline(self, session: Any, run_row: Any) -> bool:
        """Whether the run's own snapshot deadline, plus a margin, has passed.

        A run with no readable snapshot cannot be proven dead, so it is not.
        """
        snapshot = await RunConfigSnapshotRepository(session).get_by_run(
            run_row.run_id, organization_id=run_row.organization_id
        )
        if snapshot is None:
            return False
        deadline = datetime.fromisoformat(snapshot.configuration["deadline"])
        return datetime.now(UTC) > deadline + RESTORE_DEADLINE_MARGIN

    async def _rehydrate_execution(
        self, lifecycle_repo: RunLifecycleRepository, run_row: Any
    ) -> ExecutionStatus | None:
        """Rebuild the in-memory view of one durably recorded run.

        The durable row is the authority for everything here: status, phase,
        timings, and — from the attempt's journaled result — the output the
        run actually produced. Nothing is inferred from process memory,
        because there may be none: this is what a fresh process and a
        replayed admission both read.

        Returns:
            The rebuilt status, or ``None`` for a run with no task row (there
            is no execution identity to recover without one).
        """
        organization_id = run_row.organization_id
        tasks = await lifecycle_repo.get_tasks_for_run(
            run_row.run_id, organization_id=run_row.organization_id
        )
        if not tasks:
            return None
        task_row = tasks[0]
        task_input = task_row.input or {}

        attempts = await lifecycle_repo.get_attempts_for_task(
            task_row.task_id, organization_id=run_row.organization_id
        )
        if not attempts:
            return None
        attempt_row = attempts[-1]

        capability_rows = await CapabilityRepository(
            lifecycle_repo.session
        ).list_grants_for_task(
            run_row.run_id,
            task_row.task_id,
            organization_id=organization_id,
        )

        def aware(value: datetime | None) -> datetime | None:
            if value is None or value.tzinfo is not None:
                return value
            return value.replace(tzinfo=UTC)

        def created(row: Any) -> datetime | None:
            # ``created_at`` is the database's clock and the other timestamps
            # are this process's, so a database clock a few milliseconds ahead
            # would make a valid row fail the contract's ordering checks.
            stamps = [
                aware(getattr(row, name))
                for name in (
                    "created_at",
                    "started_at",
                    "completed_at",
                    "cancellation_requested_at",
                )
            ]
            return min((s for s in stamps if s is not None), default=None)

        try:
            run = Run.model_validate(
                {
                    "run_id": run_row.run_id,
                    "tenant_id": run_row.tenant_id,
                    "workflow_definition_id": run_row.workflow_definition_id,
                    "workflow_definition_version": run_row.workflow_definition_version,
                    "routing_policy_id": run_row.routing_policy_id,
                    "routing_policy_version": run_row.routing_policy_version,
                    "idempotency_key": run_row.idempotency_key,
                    "requested_by": run_row.requested_by,
                    "status": run_row.status,
                    "created_at": created(run_row),
                    "updated_at": aware(run_row.updated_at),
                    "started_at": aware(run_row.started_at),
                    "completed_at": aware(run_row.completed_at),
                    "status_reason": run_row.status_reason,
                    "cancellation_requested_at": aware(
                        run_row.cancellation_requested_at
                    ),
                    "cancellation_reason": run_row.cancellation_reason,
                }
            )
            task = Task.model_validate(
                {
                    "task_id": task_row.task_id,
                    "run_id": task_row.run_id,
                    "task_key": task_row.task_key,
                    "task_type": task_row.task_type,
                    "objective": task_row.objective,
                    "idempotency_key": task_row.idempotency_key,
                    "dependency_ids": tuple(task_row.dependency_ids or ()),
                    "assigned_worker_type": task_row.assigned_worker_type,
                    "input": task_input,
                    "status": task_row.status,
                    "created_at": created(task_row),
                    "updated_at": aware(task_row.updated_at),
                    "started_at": aware(task_row.started_at),
                    "completed_at": aware(task_row.completed_at),
                    "status_reason": task_row.status_reason,
                    "cancellation_requested_at": aware(
                        task_row.cancellation_requested_at
                    ),
                    "cancellation_reason": task_row.cancellation_reason,
                }
            )
            attempt = Attempt.model_validate(
                {
                    "attempt_id": attempt_row.attempt_id,
                    "task_id": attempt_row.task_id,
                    "ordinal": attempt_row.ordinal,
                    "idempotency_key": attempt_row.idempotency_key,
                    "executor_id": attempt_row.executor_id,
                    "status": attempt_row.status,
                    "created_at": created(attempt_row),
                    "updated_at": aware(attempt_row.updated_at),
                    "started_at": aware(attempt_row.started_at),
                    "completed_at": aware(attempt_row.completed_at),
                    "status_reason": attempt_row.status_reason,
                    "cancellation_requested_at": aware(
                        attempt_row.cancellation_requested_at
                    ),
                    "cancellation_reason": attempt_row.cancellation_reason,
                }
            )
            capability_grants = tuple(
                CapabilityGrant.model_validate(
                    {
                        "grant_id": row.grant_id,
                        "run_id": row.run_id,
                        "task_id": row.task_id,
                        "capability_scope": row.capability_scope,
                        "tool_name": row.tool_name,
                        "tool_versions": tuple(row.tool_versions or ()),
                        "sensitivity": row.sensitivity,
                        "max_input_trust": row.max_input_trust,
                        "requires_approval": row.requires_approval,
                        "issued_at": aware(row.issued_at),
                        "expires_at": aware(row.expires_at),
                    }
                )
                for row in capability_rows
            )
        except (TypeError, ValueError) as exc:
            logger.warning(
                "run_rehydration_failed", run_id=run_row.run_id, error=str(exc)
            )
            return None

        journaled = attempt_row.journaled_result
        started_at = aware(run_row.started_at) or aware(run_row.created_at)
        if started_at is None:
            return None

        execution_status = ExecutionStatus(
            execution_id=task_row.task_key,
            project_id=task_input.get("project_id", ""),
            status=_RUN_STATUS_TO_EXECUTION_STATUS.get(run_row.status, run_row.status),
            current_phase=run_row.status,
            started_at=started_at,
            completed_at=aware(run_row.completed_at),
            run_id=run_row.run_id,
            organization_id=(
                str(run_row.organization_id) if run_row.organization_id else None
            ),
        )
        execution_status._run = run
        execution_status._task = task
        execution_status._attempt = attempt
        execution_status.capability_grants = capability_grants
        if run_row.status == RunStatus.SUCCEEDED.value:
            execution_status.progress_percentage = 100.0
        if journaled:
            execution_status.final_output = journaled.get("final_output")
            execution_status.agent_results = journaled.get("agent_results") or {}
            execution_status.errors = list(journaled.get("errors") or [])
        return execution_status

    def _replayed_execution_id(self, run_id: str) -> str | None:
        """Return the execution already handling ``run_id``, if any.

        A run is admitted once: ``agent_runs`` is unique on
        ``(tenant_id, idempotency_key)``, and the authority binding fixes
        ``run_id`` before the caller ever gets a handle. So a second
        admission of the same run is a replay — a client retry, not a second
        piece of work — and the execution already running (or already
        finished) it is the answer.

        Restored runs are in this index too, so a replay that arrives after a
        restart still finds the execution the durable record says exists.
        """
        execution_id = self._execution_ids_by_run_id.get(run_id)
        if execution_id is None:
            return None
        if execution_id not in self.active_executions:
            # The index outlived what it pointed at; treat the run as
            # unclaimed rather than handing back a dangling handle.
            del self._execution_ids_by_run_id[run_id]
            return None
        return execution_id

    async def start_research_execution(
        self,
        project: ResearchProject,
        context: dict[str, Any] | None = None,
        *,
        execution_policy: RoutingExecutionPolicy | None = None,
        fixture_result: dict[str, Any] | None = None,
        authority_reference: ExecutionAuthorityReference | None = None,
        organization_id: str | None = None,
    ) -> str:
        """
        Start direct research execution using MASR routing.

        Admission is idempotent per run: submitting the same execution
        authority twice — an ordinary client network retry — returns the
        handle of the execution already running that run instead of starting
        a second one. The caller sees one execution id, and reads status and
        results for it exactly as if it had only asked once. Nothing about
        the replay reaches the router, the supervisor bridge, or the durable
        record.

        That holds however the duplicate arrives: while the first admission
        is still in flight (concurrent retries), after it finished (a replay
        of a completed run), and after a restart that emptied this process's
        memory entirely. See :meth:`_locally_known_execution_id` and
        :meth:`_durably_recorded_execution_id`.

        Args:
            project: Research project to execute
            context: Additional execution context
            organization_id: The caller's authenticated tenant. Required to
                resolve any authority; a caller without one can start
                nothing.

        Returns:
            Execution ID for tracking progress

        Raises:
            ExecutionAuthorityUnavailableError: The caller's organization
                does not own the referenced authority, or supplied none.
            RunAdmissionError: Persistence is configured and the admission
                write did not land; no execution was started.
            RunCapExceededError: The organization's concurrent or 24-hour run
                admission cap was exceeded; no execution was started.
        """
        if self.closed:
            raise RuntimeError("Direct execution service is closed")

        if authority_reference is not None and isinstance(
            self.execution_authority_resolver, PersistedExecutionAuthorityResolver
        ):
            await self.execution_authority_resolver.load_authority(
                authority_reference, organization_id=organization_id
            )
        binding = self._resolve_execution_authority(
            authority_reference, organization_id=organization_id
        )
        run_id = binding.run.run_id

        locally_known_execution_id = self._locally_known_execution_id(run_id)
        if locally_known_execution_id is not None:
            # Shielded: this future may be another caller's admission claim,
            # and a client that disconnects while waiting on it must not
            # cancel the admission that is doing the actual work.
            return await asyncio.shield(locally_known_execution_id)

        # Claim the run before the first await. Everything below yields to
        # the event loop — including the durable lookup — and until this
        # claim exists a concurrent duplicate would sail past every check
        # above and admit the same run a second time.
        reservation: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        reservation.add_done_callback(_discard_unretrieved_admission_error)
        self._admissions_in_flight[run_id] = reservation
        try:
            execution_id = await self._durably_recorded_execution_id(
                run_id, organization_id=binding.run.tenant_id
            )
            if execution_id is None:
                execution_id = await self._admit_new_execution(
                    project,
                    binding,
                    context=context,
                    execution_policy=execution_policy,
                    fixture_result=fixture_result,
                )
        except BaseException as exc:
            # Whoever was waiting on this claim was waiting for a handle that
            # will never exist; they get the same failure the first caller
            # got rather than a hang or a fabricated id.
            if not reservation.done():
                reservation.set_exception(exc)
            raise
        finally:
            self._admissions_in_flight.pop(run_id, None)
        if not reservation.done():
            reservation.set_result(execution_id)
        return execution_id

    def _locally_known_execution_id(self, run_id: str) -> "asyncio.Future[str] | None":
        """Return an awaitable for the execution this process already has.

        A run is admitted once: ``agent_runs`` is unique on
        ``(tenant_id, idempotency_key)`` and the authority binding fixes
        ``run_id`` before the caller ever gets a handle, so a second
        admission is a replay rather than a second piece of work.

        Deliberately synchronous, and deliberately returns a future rather
        than awaiting one: the caller must be able to decide, without ever
        yielding to the event loop, whether this run is already claimed.
        Two answers live in process memory — an admission still in flight
        (the concurrent-retry case, where the run is claimed but not yet
        admitted) and the replay index (a live or restored execution). The
        third, the durable record, requires I/O and is consulted by
        :meth:`_durably_recorded_execution_id` under the claim.
        """
        in_flight = self._admissions_in_flight.get(run_id)
        if in_flight is not None:
            logger.info("concurrent_admission_coalesced", run_id=run_id)
            return in_flight

        replayed_execution_id = self._replayed_execution_id(run_id)
        if replayed_execution_id is None:
            return None
        logger.info(
            "replayed_admission_coalesced",
            run_id=run_id,
            execution_id=replayed_execution_id,
        )
        settled: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        settled.set_result(replayed_execution_id)
        return settled

    async def _durably_recorded_execution_id(
        self, run_id: str, *, organization_id: str
    ) -> str | None:
        """Rehydrate an already-admitted run from persistence, if one exists.

        Unlike :meth:`restore_active_executions` this deliberately includes
        terminal runs: the whole point is to answer a replay with the outcome
        the database recorded instead of running the work again.

        ``organization_id`` is the tenant the admitting authority belongs to.
        A durable run recorded under a different organization is treated as
        no run at all: this method materializes ``final_output`` and
        ``agent_results`` into ``active_executions``, so coalescing onto a
        foreign run would publish another tenant's output under a handle this
        caller holds.
        """
        if not self.session_factory:
            return None
        try:
            async with self._tenant_session(organization_id) as session:
                lifecycle_repo = RunLifecycleRepository(session)
                run_row = await lifecycle_repo.get_run(run_id)
                if run_row is None:
                    return None
                if not tenant_owns_authority(
                    organization_id, str(run_row.organization_id)
                ):
                    logger.warning(
                        "durable_admission_lookup_cross_tenant",
                        run_id=run_id,
                    )
                    return None
                execution_status = await self._rehydrate_execution(
                    lifecycle_repo, run_row
                )
        except Exception as exc:
            # An unreadable database cannot prove the run is new, and
            # admitting it again on that assumption is exactly the double
            # execution this guards against.
            logger.warning(
                "durable_admission_lookup_failed", run_id=run_id, error=str(exc)
            )
            raise RunAdmissionError(
                f"cannot determine whether run {run_id!r} was already admitted"
            ) from exc
        if execution_status is None:
            return None
        self.active_executions[execution_status.execution_id] = execution_status
        self._execution_ids_by_run_id[run_id] = execution_status.execution_id
        logger.info(
            "durably_recorded_admission_coalesced",
            run_id=run_id,
            execution_id=execution_status.execution_id,
            run_status=execution_status.status,
        )
        return execution_status.execution_id

    async def _admit_new_execution(
        self,
        project: ResearchProject,
        binding: ExecutionAuthorityBinding,
        *,
        context: dict[str, Any] | None,
        execution_policy: RoutingExecutionPolicy | None,
        fixture_result: dict[str, Any] | None,
    ) -> str:
        """Route, admit, and dispatch one genuinely new run.

        Only ever reached while holding the in-flight claim for this run, so
        it may assume it is the only admission of ``binding.run`` in this
        process — but not the only admission across *every* process: another
        replica can win the same run's durable INSERT between this caller's
        own pre-admission lookup and its own. See
        :meth:`_admit_run`/``RunAlreadyAdmittedError``.
        """
        routing_decision = await self._propose_routing(
            project,
            context=context,
            execution_policy=execution_policy,
        )
        execution_plan = self.execution_plan_compiler.compile(
            routing_decision,
            binding,
        )
        self.supervisor_bridge.admit_execution_plan(execution_plan)

        execution_id = str(uuid.uuid4())

        # Check capacity. ``active_executions`` also retains finished runs so
        # their status and results stay readable; only in-flight ones count.
        in_flight = sum(
            1
            for execution in self.active_executions.values()
            if execution.status in ("pending", "running") and not execution.restored
        )
        if in_flight >= self.max_concurrent_executions:
            raise RuntimeError(
                f"Maximum concurrent executions ({self.max_concurrent_executions}) reached"
            )

        execution_status = ExecutionStatus(
            execution_id=execution_id,
            project_id=str(project.id),
            status="pending",
            current_phase="initialization",
            execution_plan=execution_plan,
            organization_id=binding.run.tenant_id,
        )

        try:
            await self._admit_run(execution_status, binding, project)
        except RunAlreadyAdmittedError as exc:
            # Another replica's write already won this run durably. Nothing
            # was started for it here — no task row, no attempt row, no
            # background workflow task — so there is nothing to unwind;
            # coalesce onto the winner exactly like a replayed admission
            # does (``_durably_recorded_execution_id`` already registered it
            # in ``active_executions``/``_execution_ids_by_run_id`` before
            # raising).
            return exc.execution_id

        self.active_executions[execution_id] = execution_status
        # ``_admit_run`` indexes the run when it has a durable identity to
        # index. Memory-only mode deliberately does not: with no durable
        # record there is nothing to coalesce a later replay against, and
        # its behaviour is unchanged from Packet 3G.
        self.execution_stats["total_executions"] += 1
        self.execution_stats["concurrent_executions"] += 1

        # Start execution asynchronously
        task = asyncio.create_task(
            self._execute_research_workflow(
                project,
                execution_status,
                context,
                execution_policy,
                fixture_result,
                routing_decision,
                execution_plan,
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        self._workflow_tasks[execution_id] = task
        task.add_done_callback(lambda _t: self._workflow_tasks.pop(execution_id, None))
        if execution_status._fencing_token is not None:
            self._start_lease_heartbeat(execution_status, task)

        logger.info(f"Started direct execution {execution_id} for project {project.id}")

        return execution_id

    def _resolve_execution_authority(
        self,
        authority_reference: ExecutionAuthorityReference | None,
        *,
        organization_id: str | None,
    ) -> ExecutionAuthorityBinding:
        """Resolve authority before MASR, state mutation, or task creation.

        ``organization_id`` is the caller's authenticated tenant and is
        required: every durable write below takes its organization from
        ``binding.run.tenant_id``, so resolving an authority the caller does
        not own would run — and persist — work as another tenant.
        """

        if authority_reference is None:
            raise ExecutionAuthorityRequiredError("execution authority is required")
        if self.execution_authority_resolver is None:
            raise ExecutionAuthorityUnavailableError(
                "execution authority is unavailable"
            )
        return self.execution_authority_resolver.resolve(
            authority_reference, organization_id=organization_id
        )

    async def _propose_routing(
        self,
        project: ResearchProject,
        *,
        context: dict[str, Any] | None,
        execution_policy: RoutingExecutionPolicy | None,
    ) -> MASRRoutingDecision:
        """Ask MASR for a proposal; it cannot fill execution authority."""

        routing_context = {
            "query": project.query.text,
            "domains": project.query.domains,
            "project_id": project.id,
            "user_id": str(project.user_id) if project.user_id else None,
        }
        if context:
            routing_context.update(context)
        if execution_policy is not None and execution_policy.fixture_mode:
            return await self.masr_router.route(
                query=project.query.text,
                context=routing_context,
                execution_policy=execution_policy,
            )
        return await self.masr_router.route(
            query=project.query.text,
            context=routing_context,
        )

    @staticmethod
    def _terminal_payload(
        execution_status: ExecutionStatus, **fields: Any
    ) -> dict[str, Any]:
        """Return a terminal event payload, with the run's spend when it has a ledger."""
        ledger = execution_status._budget_ledger
        if ledger is None:
            return fields
        return {**fields, "usage": ledger.usage_payload()}

    @retry(
        stop=stop_after_attempt(_WORKFLOW_MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        # An exhausted budget stays exhausted, a fail-closed provider error is
        # one attempt (a retry would spend again on a provider the plan cannot
        # replace), and a cancelled run must not be restarted by the wrapper.
        retry=retry_if_not_exception_type(
            (BudgetExceededError, PlanProviderUnavailableError, asyncio.CancelledError)
        ),
    )
    async def _execute_research_workflow(
        self,
        project: ResearchProject,
        execution_status: ExecutionStatus,
        context: dict[str, Any] | None = None,
        execution_policy: RoutingExecutionPolicy | None = None,
        fixture_result: dict[str, Any] | None = None,
        routing_decision: MASRRoutingDecision | None = None,
        execution_plan: ExecutionPlan | None = None,
    ) -> None:
        """
        Execute research workflow with retry logic.

        Args:
            project: Research project to execute
            execution_status: Execution status tracker
            context: Additional context
        """

        try:
            # Persist the durable transition before flipping the in-memory
            # flag other code (including every caller polling
            # ``active_executions``) treats as observable truth — otherwise a
            # reader could see "running" before the row backing it exists.
            await self._persist_transition(
                execution_status,
                run_target=RunStatus.RUNNING,
                task_target=TaskStatus.RUNNING,
                attempt_target=AttemptStatus.RUNNING,
                event_type="run.started",
                payload={"phase": "masr_routing"},
            )
            execution_status.status = "running"
            execution_status.current_phase = "masr_routing"

            await self._publish_progress_update(execution_status)

            if routing_decision is None or execution_plan is None:
                raise RuntimeError("execution plan must be compiled before dispatch")
            execution_policy = execution_policy or RoutingExecutionPolicy()
            routing_context = {
                "query": project.query.text,
                "domains": project.query.domains,
                "project_id": project.id,
                "user_id": str(project.user_id) if project.user_id else None,
            }
            if context:
                routing_context.update(context)

            execution_status.routing_decision = asdict(routing_decision)
            execution_status.execution_plan = execution_plan
            execution_status.supervisor_type = (
                routing_decision.agent_allocation.supervisor_type
            )
            execution_status.current_phase = "supervisor_execution"
            execution_status.progress_percentage = 20.0

            await self._publish_progress_update(execution_status)
            await self._checkpoint(execution_status, "masr_routing")

            if execution_policy.fixture_mode:
                if not isinstance(fixture_result, dict):
                    raise ValueError(
                        "fixture execution requires an explicit fixture_result object"
                    )
                execution_status.final_output = copy.deepcopy(fixture_result)
                execution_status.agent_results = copy.deepcopy(fixture_result)
                execution_status.workers_used = 0
                self.execution_stats["successful_executions"] += 1
                # Journaling reads final_output/agent_results off
                # execution_status, so those must already be set (above)
                # before this call — but the terminal status flip below must
                # come after it, so nothing observes "completed" ahead of
                # the durable row, and only if the durable row exists.
                outcome = await self._persist_transition(
                    execution_status,
                    run_target=RunStatus.SUCCEEDED,
                    task_target=TaskStatus.SUCCEEDED,
                    attempt_target=AttemptStatus.SUCCEEDED,
                    event_type="run.succeeded",
                    payload={"fixture_mode": True},
                )
                self._settle_terminal_state(
                    execution_status,
                    outcome=outcome,
                    status="completed",
                    phase="completed",
                    progress=100.0,
                )
                await self._checkpoint(execution_status, "fixture_completed")
                logger.info(
                    "fixture_execution_completed",
                    execution_id=execution_status.execution_id,
                    adaptive_routing="fixture_off",
                    memory_routing="fixture_off",
                )
                return

            # One ledger per execution: workflow retries draw on the budget
            # already spent.
            if execution_status._budget_ledger is None:
                execution_status._budget_ledger = BudgetLedger(
                    execution_plan.routing_decision.budget
                )
            plan_task = AgentTask(
                id=f"research_{project.id}_{execution_status.execution_id}",
                agent_type="execution_plan",
                context={
                    **self._durable_agent_task_context(execution_status),
                    BUDGET_LEDGER_CONTEXT_KEY: execution_status._budget_ledger,
                    **self._invocation_context_entry(execution_status),
                },
                input_data={
                    "query": project.query.text,
                    "domains": list(execution_plan.routing_decision.domains),
                    "context": routing_context,
                    "project_data": {
                        "title": project.title,
                        "scope": project.scope.model_dump() if project.scope else {},
                        "query": project.query.model_dump(),
                    },
                },
            )
            execution_status.current_phase = "plan_topology_execution"
            execution_status.progress_percentage = 40.0
            await self._publish_progress_update(execution_status)
            await self._checkpoint(execution_status, "plan_topology_execution")
            plan_result = await self.supervisor_bridge.execute_execution_plan(
                execution_plan,
                plan_task,
            )
            execution_status.agent_results = plan_result.output
            execution_status.workers_used = plan_result.workers_used
            if not plan_result.succeeded:
                await self._fail_run_on_worker_outcomes(execution_status, plan_result)
                return
            execution_status.final_output = plan_result.output
            outcome = await self._persist_transition(
                execution_status,
                run_target=RunStatus.SUCCEEDED,
                task_target=TaskStatus.SUCCEEDED,
                attempt_target=AttemptStatus.SUCCEEDED,
                event_type="run.succeeded",
                payload=self._terminal_payload(
                    execution_status, workers_used=execution_status.workers_used
                ),
            )
            if outcome is DurableWriteOutcome.RECORDED:
                self.execution_stats["successful_executions"] += 1
            self._settle_terminal_state(
                execution_status,
                outcome=outcome,
                status="completed",
                phase="completed",
                progress=100.0,
            )
            await self._checkpoint(execution_status, "completed")
            return
        except asyncio.CancelledError:
            await self._record_requested_cancellation(execution_status)
            raise
        except Exception as e:
            logger.error(
                f"Direct execution {execution_status.execution_id} failed with exception: {e}"
            )

            execution_status.errors.append(str(e))
            execution_status.retry_count += 1

            self.execution_stats["failed_executions"] += 1

            budget_error = e if isinstance(e, BudgetExceededError) else None
            provider_failed = isinstance(e, PlanProviderUnavailableError)
            if (
                budget_error is None
                and not provider_failed
                and execution_status.retry_count < _WORKFLOW_MAX_ATTEMPTS
            ):
                # This attempt failed; the run has not. Recording FAILED here
                # would make the run durably terminal and immutable, so every
                # transition the retry attempts would be refused — and a
                # retry that then succeeded would leave the caller reading
                # "completed" against a durable record that says "failed".
                # The attempt failure is recorded as an event; the run stays
                # RUNNING, which is what it is.
                await self._record_attempt_failure(execution_status, error=str(e))
                raise

            event_type = "run.failed"
            payload = self._terminal_payload(execution_status, error=str(e))
            if budget_error:
                # A budget error can only come from a run that has a ledger.
                assert execution_status._budget_ledger is not None
                event_type = "run.budget_exceeded"
                payload |= {
                    **budget_error.as_payload(),
                    "usage": execution_status._budget_ledger.usage_payload(),
                }
                execution_status.failure_code = budget_error.code
            elif provider_failed:
                execution_status.failure_code = "PLAN_PROVIDER_UNAVAILABLE"
            outcome = await self._persist_transition(
                execution_status,
                run_target=RunStatus.FAILED,
                task_target=TaskStatus.FAILED,
                attempt_target=AttemptStatus.FAILED,
                event_type=event_type,
                payload=payload,
                reason=(str(e).strip() or "execution failed")[:500],
            )
            self._settle_terminal_state(
                execution_status,
                outcome=outcome,
                status="failed",
                phase="failed",
            )

            # Re-raise for retry logic
            raise

        finally:
            # Update completion time and metrics. Use timezone-aware UTC to
            # match ``started_at`` (``datetime.now(UTC)``); a naive
            # ``datetime.now()`` here raised "can't subtract offset-naive and
            # offset-aware datetimes" from this finally block on every run,
            # which — because it propagates even after the body succeeds —
            # tripped the @retry wrapper and re-ran the whole workflow.
            execution_status.completed_at = datetime.now(UTC)
            execution_status.execution_time_seconds = (
                execution_status.completed_at - execution_status.started_at
            ).total_seconds()

            if self.execution_stats["total_executions"] > 0:
                current_avg = self.execution_stats["average_execution_time"]
                total_executions = self.execution_stats["total_executions"]
                new_avg = (
                    current_avg * (total_executions - 1)
                    + execution_status.execution_time_seconds
                ) / total_executions
                self.execution_stats["average_execution_time"] = new_avg

            self.execution_stats["concurrent_executions"] -= 1

            # Final progress update
            await self._publish_progress_update(execution_status)

    async def _fail_run_on_worker_outcomes(
        self, execution_status: ExecutionStatus, plan_result: PlanExecutionResult
    ) -> None:
        """Record a semantic worker failure as terminal; retrying cannot fix it."""
        failures = plan_result.failures
        timed_out = next(
            (
                o
                for o in plan_result.outcomes.values()
                if o.status is WorkerOutcomeStatus.TIMED_OUT
            ),
            None,
        )
        execution_status.failure_code = (
            timed_out.code if timed_out and timed_out.code else "WORKER_FAILED"
        )
        execution_status.errors.extend(
            f"{f['worker_id']}: {f['status']} ({f['code']})" for f in failures
        )
        self.execution_stats["failed_executions"] += 1
        outcome = await self._persist_transition(
            execution_status,
            run_target=RunStatus.FAILED,
            task_target=TaskStatus.FAILED,
            attempt_target=(
                AttemptStatus.TIMED_OUT if timed_out else AttemptStatus.FAILED
            ),
            event_type="run.failed",
            payload=self._terminal_payload(
                execution_status,
                error="worker outcomes did not satisfy the plan",
                failure_code=execution_status.failure_code,
                failures=failures,
                # A failed worker's error payload is not a partial result.
                partial_worker_ids=sorted(
                    worker_id
                    for worker_id, outcome in plan_result.outcomes.items()
                    if outcome.succeeded
                ),
            ),
            reason="worker outcomes did not satisfy the plan",
        )
        self._settle_terminal_state(
            execution_status, outcome=outcome, status="failed", phase="failed"
        )

    async def _record_requested_cancellation(
        self, execution_status: ExecutionStatus
    ) -> None:
        """Finish a cancellation the caller asked for once the work has stopped.

        Only a cancellation requested through :meth:`cancel_execution` is
        recorded; a task cancelled by shutdown leaves the run as it was, so
        the next process sees it still in flight.
        """
        run = execution_status._run
        if execution_status.status != "cancelled" or run is None:
            return
        if run.status is not RunStatus.CANCELLING:
            return
        write = asyncio.ensure_future(
            self._persist_transition(
                execution_status,
                run_target=RunStatus.CANCELLED,
                task_target=TaskStatus.CANCELLED,
                attempt_target=AttemptStatus.CANCELLED,
                event_type="run.cancelled",
                payload={},
                reason="Execution cancelled by request",
                in_transaction=self._settle_open_model_calls(execution_status),
            )
        )
        self._shielded_writes.add(write)
        write.add_done_callback(self._shielded_writes.discard)
        outcome = await asyncio.shield(write)
        if (
            outcome is DurableWriteOutcome.RECORDED
            and run.cancellation_requested_at is not None
        ):
            run_cancellation_delay_seconds.observe(
                max(
                    0.0,
                    (datetime.now(UTC) - run.cancellation_requested_at).total_seconds(),
                )
            )
        self._settle_terminal_state(
            execution_status,
            outcome=outcome,
            status="cancelled",
            phase="cancelled",
        )

    def _settle_open_model_calls(
        self, execution_status: ExecutionStatus
    ) -> Callable[[Any], Awaitable[None]] | None:
        """Close any model call the cancelled work left open, with the terminal write.

        Each call settles itself as the work unwinds; this is the backstop for
        a settlement that was lost, so a cancelled run never ends with a
        reservation still counted against it.
        """
        journal = self._model_call_journal(execution_status)
        if journal is None:
            return None

        async def settle(session: Any) -> None:
            await journal.reconcile_owner(session=session)

        return settle

    def _visible_execution(
        self, execution_id: str, organization_id: str | None
    ) -> ExecutionStatus | None:
        """Return an execution only when the caller's tenant owns it.

        ``active_executions`` holds every tenant's runs in one process-wide
        dictionary, and an execution id is the only thing a reader supplies,
        so the owner check is the whole boundary. Missing tenant context, an
        execution with no recorded owner, and an execution owned by another
        tenant are all reported identically as "not found", so the id space
        cannot be probed.
        """
        execution = self.active_executions.get(execution_id)
        if execution is None:
            return None
        if organization_id is None or execution.organization_id is None:
            return None
        if not tenant_owns_authority(execution.organization_id, organization_id):
            return None
        return execution

    async def get_execution_status(
        self, execution_id: str, *, organization_id: str | None = None
    ) -> ExecutionStatus | None:
        """Get current status of an execution inside the caller's tenant."""
        return self._visible_execution(execution_id, organization_id)

    async def get_execution_results(
        self, execution_id: str, *, organization_id: str | None = None
    ) -> dict[str, Any] | None:
        """Get results of a completed execution inside the caller's tenant."""

        execution = self._visible_execution(execution_id, organization_id)
        if not execution:
            return None

        if execution.status == "completed":
            return execution.final_output
        elif execution.status == "failed":
            return {"error": "Execution failed", "details": execution.errors}
        else:
            return {
                "status": execution.status,
                "progress": execution.progress_percentage,
            }

    async def resume_execution(
        self, project_id: uuid.UUID, *, organization_id: str | None = None
    ) -> None:
        """Refuse to resume: checkpoints are diagnostic snapshots, not resumable state.

        Nothing in this service re-dispatches work, so reviving a checkpoint
        would only register an execution no task is running. A project the
        caller cannot see (no session factory, no tenant, another
        organization's, or missing) returns ``None`` exactly as before, so the
        refusal does not reveal that a project exists.

        Raises:
            ResumeUnsupportedError: The project is the caller's. Start a new
                run instead.
        """
        if not self.session_factory or organization_id is None:
            return None
        async with self._tenant_session(organization_id) as session:
            project = await ResearchRepository(session).get(
                project_id, organization_id=organization_id
            )
        if project is None or project.organization_id is None:
            return None
        raise ResumeUnsupportedError(project_id)

    async def cancel_execution(self, execution_id: str) -> bool:
        """Cancel active execution.

        Reports success only when the cancellation request is backed by the
        durable record. A database that cannot record the request leaves the
        run reading exactly what the database still holds and returns False,
        so the caller learns the cancellation did not take and can retry —
        rather than being told a run was cancelled that the durable record
        (and any other process reading it) still considers live.

        The durable run moves to CANCELLING here and the running workflow task
        is cancelled; the workflow records CANCELLED once it has actually
        stopped. See :meth:`_record_requested_cancellation`.

        Returns:
            True if the cancellation was accepted and durably requested.
        """

        execution = self.active_executions.get(execution_id)
        if not execution:
            return False

        if execution.status in ["pending", "running"]:
            outcome = await self._persist_cancellation(execution)
            if outcome is not DurableWriteOutcome.RECORDED:
                logger.warning(
                    "cancellation_not_durably_requested",
                    execution_id=execution_id,
                    run_id=execution.run_id,
                    outcome=outcome.value,
                )
                return False
            execution.status = "cancelled"
            execution.current_phase = "cancelled"
            workflow_task = self._workflow_tasks.get(execution_id)
            if workflow_task is not None:
                workflow_task.cancel()

            await self._publish_progress_update(execution)

            logger.info(f"Cancelled execution {execution_id}")
            return True

        return False

    async def list_active_executions(self) -> list[ExecutionStatus]:
        return [
            execution
            for execution in self.active_executions.values()
            if execution.status in ["pending", "running"]
        ]

    async def cleanup_completed_executions(self, max_age_hours: int = 24) -> int:
        """Clean up old completed executions."""

        cutoff_time = datetime.now(UTC) - timedelta(hours=max_age_hours)

        executions_to_remove = [
            execution_id
            for execution_id, execution in self.active_executions.items()
            if (
                execution.status in ["completed", "failed", "cancelled"]
                and execution.completed_at
                and execution.completed_at < cutoff_time
            )
        ]

        for execution_id in executions_to_remove:
            del self.active_executions[execution_id]

        logger.info(f"Cleaned up {len(executions_to_remove)} old executions")

        return len(executions_to_remove)

    async def _publish_progress_update(self, execution_status: ExecutionStatus) -> None:
        """Broadcast a progress update to subscribed WebSocket clients.

        Uses the typed ``EventPublisher.publish_progress_update`` path, which
        fans out over WebSocket via ``connection_manager.broadcast_to_project``.
        """

        if not self.event_publisher:
            return

        # ``broadcast_to_project`` keys subscriptions by ``UUID``; the execution
        # project_id is stored as a string, so coerce it and skip the update if
        # it is not a valid UUID rather than crashing the execution loop.
        try:
            project_uuid = uuid.UUID(str(execution_status.project_id))
        except (ValueError, TypeError):
            logger.warning(
                "Skipping progress broadcast for non-UUID project_id",
                project_id=execution_status.project_id,
                execution_id=execution_status.execution_id,
            )
            return

        progress = ProgressUpdate(
            progress_percentage=execution_status.progress_percentage,
            current_phase=execution_status.current_phase,
            current_agent=execution_status.supervisor_type,
        )

        await self.event_publisher.publish_progress_update(project_uuid, progress)

    async def get_service_stats(self) -> dict[str, Any]:
        """Get service statistics."""

        return {
            "execution_stats": self.execution_stats.copy(),
            "active_executions": len(self.active_executions),
            "active_execution_details": [
                {
                    "execution_id": execution.execution_id,
                    "project_id": execution.project_id,
                    "status": execution.status,
                    "progress": execution.progress_percentage,
                    "current_phase": execution.current_phase,
                    "duration": (
                        datetime.now(UTC) - execution.started_at
                    ).total_seconds(),
                }
                for execution in self.active_executions.values()
                if execution.status in ["pending", "running"]
            ],
            "component_health": {
                "masr_router": "healthy" if self.masr_router else "unavailable",
                "supervisor_bridge": (
                    "healthy" if self.supervisor_bridge else "unavailable"
                ),
                "supervisor_factory": (
                    "healthy" if self.supervisor_factory else "unavailable"
                ),
            },
        }

    async def health_check(self) -> dict[str, Any]:
        """Perform health check on service components."""

        components: dict[str, str] = {}

        if self.masr_router:
            try:
                masr_health = await self.masr_router.health_check()
                components["masr_router"] = str(masr_health.get("status", "unknown"))
            except Exception as e:
                components["masr_router"] = f"unhealthy: {e}"
        else:
            components["masr_router"] = "unavailable"

        if self.supervisor_bridge:
            try:
                bridge_health = await self.supervisor_bridge.health_check()
                components["supervisor_bridge"] = str(
                    bridge_health.get("status", "unknown")
                )
            except Exception as e:
                components["supervisor_bridge"] = f"unhealthy: {e}"
        else:
            components["supervisor_bridge"] = "unavailable"

        if self.supervisor_factory:
            try:
                factory_health = await self.supervisor_factory.health_check()
                components["supervisor_factory"] = str(
                    factory_health.get("status", "unknown")
                )
            except Exception as e:
                components["supervisor_factory"] = f"unhealthy: {e}"
        else:
            components["supervisor_factory"] = "unavailable"

        # Determine overall health
        component_statuses = list(components.values())
        if all("healthy" in status for status in component_statuses):
            overall_status = "healthy"
        elif any("unhealthy" in status for status in component_statuses):
            overall_status = "degraded"
        else:
            overall_status = "unknown"

        return {
            "status": overall_status,
            "components": components,
            "active_executions": len(self.active_executions),
            "service_stats": self.execution_stats,
        }

    async def close(self) -> None:
        """Cancel background work owned by this exact service instance."""

        if self.closed:
            return
        self.closed = True
        if self._pending_terminal_outcomes:
            # Last chance to record an outcome this process is about to
            # forget. Best-effort: if it still fails, the durable record
            # keeps showing a non-terminal run, which is the honest reading.
            try:
                await self.reconcile_pending_terminal_transitions()
            except Exception as exc:
                logger.warning(
                    "terminal_outcome_shutdown_reconciliation_failed", error=str(exc)
                )
        tasks = tuple(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        await asyncio.gather(*self._shielded_writes, return_exceptions=True)
        await self._release_leases()

        provider = self._fast_path_provider
        self._fast_path_provider = None
        if provider is not None:
            try:
                await provider.close()
            except Exception as exc:
                logger.warning(
                    "direct_execution_fast_path_provider_shutdown_failed",
                    error=type(exc).__name__,
                )
        if self._owns_supervisor_bridge:
            try:
                await self.supervisor_bridge.cleanup()
            except Exception as exc:
                logger.warning(
                    "direct_execution_supervisor_bridge_shutdown_failed",
                    error=type(exc).__name__,
                )


# Legacy compatibility functions for migration
async def create_research_plan(project_data: dict[str, Any]) -> dict[str, Any]:
    """Legacy function for compatibility during migration."""
    logger.warning(
        "Using legacy create_research_plan - should migrate to direct execution"
    )

    return {
        "plan_created": True,
        "project_id": project_data.get("id"),
        "note": "Migrated from Temporal to direct execution",
        "timestamp": datetime.now().isoformat(),
    }


async def execute_agent_task(agent_task_data: dict[str, Any]) -> dict[str, Any]:
    """Legacy function for compatibility during migration."""
    logger.warning("Using legacy execute_agent_task - should use supervisor execution")

    return {
        "task_completed": True,
        "agent_type": agent_task_data.get("agent_type", "unknown"),
        "note": "Migrated from Temporal activity to supervisor execution",
        "timestamp": datetime.now().isoformat(),
    }


async def aggregate_results(results_data: dict[str, Any]) -> dict[str, Any]:
    """Legacy function for compatibility during migration."""
    logger.warning("Using legacy aggregate_results - handled by supervisors now")

    return {
        "aggregation_completed": True,
        "results_count": len(results_data.get("results", [])),
        "note": "Migrated from Temporal activity to supervisor result aggregation",
        "timestamp": datetime.now().isoformat(),
    }


# Global service instance (would be properly injected in production)
_direct_execution_service: DirectExecutionService | None = None


def get_direct_execution_service(
    gemini_service: Any | None = None,
    masr_router: MASRouter | None = None,
) -> DirectExecutionService:
    """Get global direct execution service instance."""
    global _direct_execution_service

    if _direct_execution_service is None:
        _direct_execution_service = DirectExecutionService(
            gemini_service=gemini_service,
            masr_router=masr_router,
        )

    return _direct_execution_service


def configure_direct_execution_service(
    *,
    masr_router: MASRouter,
    gemini_service: Any | None = None,
    component_registry: TypedRegistry | None = None,
    session_factory: Any | None = None,
    event_publisher: EventPublisher | None = None,
) -> DirectExecutionService:
    """Create an application-owned service without changing legacy globals.

    ``session_factory``/``event_publisher`` were previously never threaded
    through from the application lifespan, which made checkpointing, run
    persistence, and WebSocket progress publication silent no-ops in
    production. Both are optional here (default ``None``) so every existing
    caller that omits them keeps today's in-memory-only behavior.
    """

    return DirectExecutionService(
        gemini_service=gemini_service,
        masr_router=masr_router,
        component_registry=component_registry,
        session_factory=session_factory,
        event_publisher=event_publisher,
    )


def get_application_direct_execution_service(
    request: Request,
) -> DirectExecutionService:
    """Resolve the service owned by the current FastAPI application."""

    service = getattr(request.app.state, "direct_execution_service", None)
    if not isinstance(service, DirectExecutionService) or service.closed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Direct execution runtime is unavailable",
        )
    return service


async def close_direct_execution_service(
    service: DirectExecutionService | None = None,
) -> None:
    """Close only the supplied owner, or the legacy singleton when omitted."""

    global _direct_execution_service
    if service is None:
        service = _direct_execution_service
        _direct_execution_service = None
    elif _direct_execution_service is service:
        _direct_execution_service = None
    if service is None:
        return
    await service.close()


__all__ = [
    "AWAITING_DURABLE_CONFIRMATION_PHASE",
    "DirectExecutionService",
    "DurableWriteOutcome",
    "ExecutionStatus",
    "aggregate_results",
    "close_direct_execution_service",
    "configure_direct_execution_service",
    # Legacy compatibility exports
    "create_research_plan",
    "execute_agent_task",
    "get_application_direct_execution_service",
    "get_direct_execution_service",
]
