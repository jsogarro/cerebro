"""Read-only, tenant-scoped projections of durable runs.

Every read starts from the caller's organization. A run that does not exist and
a run another organization owns raise the same ``RunNotFoundError``, so run ids
cannot be probed across tenants. Projections carry what a client may see; they
never hand back ORM rows, storage locations or an unscoped id.
"""

import base64
import binascii
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import RunEventCursor
from src.core.evaluation.registry import build_default_registry, evaluation_states
from src.repositories.artifact_repository import ArtifactRepository
from src.repositories.evaluation_result_repository import EvaluationResultRepository
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository

MAX_PAGE = 200
DEFAULT_PAGE = 50
# ponytail: states are computed over at most this many canonical rows; a run
# with more per-claim rows than this needs a SQL aggregate instead.
MAX_STATE_ROWS = 5000

# The writers record fixed reasons and, on one failure path, ``str(exception)``,
# which can carry SQL, parameters and provider URLs. Only these fixed strings
# leave the service; anything else reads as the generic failure.
_PUBLIC_REASONS = frozenset(
    {
        "heartbeat crashed",
        "renewal refused",
        "lease unconfirmed",
        "PROCESS_INTERRUPTED",
        "interrupted by restart",
        "Execution cancelled by request",
        "worker outcomes did not satisfy the plan",
        "execution failed",
    }
)
_GENERIC_FAILURE = "execution failed"
_FREE_TEXT_KEYS = frozenset({"error", "errors", "details", "detail", "exception"})


def public_reason(reason: str | None) -> str | None:
    if reason is None or reason in _PUBLIC_REASONS:
        return reason
    return _GENERIC_FAILURE


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (
            public_reason(value)
            if key == "reason"
            else _GENERIC_FAILURE
            if key in _FREE_TEXT_KEYS
            else value
        )
        for key, value in payload.items()
    }


class RunNotFoundError(LookupError):
    """No run with this id is visible to the caller."""


class InvalidCursorError(ValueError):
    """The cursor is malformed or was issued for a different request."""


class RunSummary(BaseModel):
    run_id: str
    workflow_id: str
    workflow_version: str
    routing_policy_id: str
    routing_policy_version: str
    status: str
    status_reason: str | None
    objective: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancellation_requested_at: datetime | None


class RunPage(BaseModel):
    items: list[RunSummary]
    next_cursor: str | None


class AttemptView(BaseModel):
    attempt_id: str
    ordinal: int
    status: str
    status_reason: str | None
    started_at: datetime | None
    completed_at: datetime | None


class TaskView(BaseModel):
    task_id: str
    run_id: str
    task_key: str
    task_type: str
    phase: str
    objective: str
    dependency_ids: list[str]
    status: str
    status_reason: str | None
    started_at: datetime | None
    completed_at: datetime | None
    attempts: list[AttemptView]


class ArtifactView(BaseModel):
    artifact_id: str
    run_id: str
    task_id: str | None
    type: str
    schema_version: str
    workflow_id: str
    workflow_version: str
    media_type: str
    content_sha256: str
    status: str
    trust: str
    producer: str
    created_at: datetime


class EvaluationView(BaseModel):
    evaluation_id: str
    run_id: str
    task_id: str | None
    artifact_id: str | None
    evaluator_id: str
    evaluator_version: str
    dimension: str
    status: str
    method: str
    score: float | None
    error_code: str | None
    rationale: str | None
    trigger: str
    completed_at: datetime


class EvaluatorState(BaseModel):
    evaluator_id: str
    evaluator_version: str
    dimension: str
    status: str


class EventView(BaseModel):
    event_id: str
    run_id: str
    sequence: int
    event_type: str
    version: str
    occurred_at: datetime
    payload: dict[str, Any]


_T = TypeVar("_T")


class Collection(BaseModel, Generic[_T]):
    """A bounded child collection; ``truncated`` says rows were left out."""

    items: list[_T]
    truncated: bool


class EvaluationCollection(Collection[EvaluationView]):
    """Stored results plus one state per registered evaluator, so a dimension
    that never ran reads ``not_attempted`` instead of being absent."""

    evaluator_states: list[EvaluatorState]


class EventPage(BaseModel):
    """``next_cursor`` is always the position after ``items``, so a client that
    has read everything can resume from it; ``has_more`` says a further page
    exists now."""

    items: list[EventView]
    next_cursor: str
    has_more: bool


def _filter_binding(organization_id: uuid.UUID, **filters: str | None) -> str:
    material = json.dumps([str(organization_id), sorted(filters.items())])
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def _encode_run_cursor(created_at: datetime, run_id: str, binding: str) -> str:
    # SQLite returns naive datetimes even for timezone-aware columns; stamp UTC
    # so every issued cursor carries an offset and passes _decode_run_cursor.
    timestamp = (
        created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)
    )
    raw = json.dumps({"t": timestamp.isoformat(), "r": run_id, "f": binding})
    return base64.urlsafe_b64encode(raw.encode()).decode("ascii")


def _decode_run_cursor(token: str, binding: str) -> tuple[datetime, str]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(token.encode("ascii")))
        if not isinstance(raw, dict) or not isinstance(raw.get("t"), str):
            raise ValueError("cursor payload is malformed")
        created_at = datetime.fromisoformat(raw["t"])
        run_id = raw["r"]
        issued_for = raw["f"]
    except (binascii.Error, ValueError, KeyError, TypeError) as error:
        raise InvalidCursorError("cursor is not valid") from error
    if created_at.tzinfo is None or not isinstance(run_id, str):
        raise InvalidCursorError("cursor is not valid")
    if issued_for != binding:
        raise InvalidCursorError("cursor does not belong to this request")
    return (created_at, run_id)


def _limit(limit: int | None) -> int:
    return min(max(limit or DEFAULT_PAGE, 1), MAX_PAGE)


class RunReadService:
    def __init__(self, session: AsyncSession, organization_id: str) -> None:
        self._org = uuid.UUID(organization_id)
        self._runs = RunLifecycleRepository(session)
        self._artifacts = ArtifactRepository(session)
        self._evaluations = EvaluationResultRepository(session)
        self._events = RunEventRepository(session)

    async def _require_run(self, run_id: str) -> Any:
        run = await self._runs.get_run(run_id, organization_id=self._org)
        if run is None:
            raise RunNotFoundError(run_id)
        return run

    async def _summaries(self, rows: list[Any]) -> list[RunSummary]:
        objectives = (
            await self._runs.first_task_objectives(
                [row.run_id for row in rows], organization_id=self._org
            )
            if rows
            else {}
        )
        return [
            RunSummary(
                run_id=row.run_id,
                workflow_id=row.workflow_definition_id,
                workflow_version=row.workflow_definition_version,
                routing_policy_id=row.routing_policy_id,
                routing_policy_version=row.routing_policy_version,
                status=row.status,
                status_reason=public_reason(row.status_reason),
                objective=objectives.get(row.run_id),
                created_at=row.created_at,
                updated_at=row.updated_at,
                started_at=row.started_at,
                completed_at=row.completed_at,
                cancellation_requested_at=row.cancellation_requested_at,
            )
            for row in rows
        ]

    async def list_runs(
        self,
        *,
        limit: int | None,
        cursor: str | None,
        workflow_id: str | None,
        status: str | None,
    ) -> RunPage:
        size = _limit(limit)
        binding = _filter_binding(self._org, workflow_id=workflow_id, status=status)
        after = _decode_run_cursor(cursor, binding) if cursor else None
        rows = await self._runs.list_runs_page(
            organization_id=self._org,
            limit=size + 1,
            workflow_id=workflow_id,
            status=status,
            after=after,
        )
        page, more = rows[:size], len(rows) > size
        next_cursor = (
            _encode_run_cursor(page[-1].created_at, page[-1].run_id, binding)
            if more
            else None
        )
        return RunPage(items=await self._summaries(page), next_cursor=next_cursor)

    async def get_run(self, run_id: str) -> RunSummary:
        return (await self._summaries([await self._require_run(run_id)]))[0]

    async def tasks(self, run_id: str, *, limit: int | None) -> Collection[TaskView]:
        await self._require_run(run_id)
        size = _limit(limit)
        fetched = await self._runs.get_tasks_for_run(
            run_id, organization_id=self._org, limit=size + 1
        )
        rows, more = fetched[:size], len(fetched) > size
        attempts: dict[str, list[AttemptView]] = {}
        for attempt in await self._runs.get_attempts_for_run(
            run_id, organization_id=self._org, task_ids=[t.task_id for t in rows]
        ):
            attempts.setdefault(attempt.task_id, []).append(
                AttemptView(
                    attempt_id=attempt.attempt_id,
                    ordinal=attempt.ordinal,
                    status=attempt.status,
                    status_reason=public_reason(attempt.status_reason),
                    started_at=attempt.started_at,
                    completed_at=attempt.completed_at,
                )
            )
        return Collection(
            items=[
                TaskView(
                    task_id=task.task_id,
                    run_id=task.run_id,
                    task_key=task.task_key,
                    task_type=task.task_type,
                    phase=task.task_type,
                    objective=task.objective,
                    dependency_ids=list(task.dependency_ids),
                    status=task.status,
                    status_reason=public_reason(task.status_reason),
                    started_at=task.started_at,
                    completed_at=task.completed_at,
                    attempts=attempts.get(task.task_id, []),
                )
                for task in rows
            ],
            truncated=more,
        )

    async def artifacts(
        self, run_id: str, *, limit: int | None
    ) -> Collection[ArtifactView]:
        run = await self._require_run(run_id)
        size = _limit(limit)
        rows = await self._artifacts.list_artifacts_for_run(
            run_id, organization_id=self._org, limit=size + 1
        )
        return Collection(
            items=[
                ArtifactView(
                    artifact_id=row.artifact_id,
                    run_id=row.run_id,
                    task_id=row.task_id,
                    type=row.kind,
                    schema_version=row.contract_schema_version,
                    workflow_id=run.workflow_definition_id,
                    workflow_version=run.workflow_definition_version,
                    media_type=row.media_type,
                    content_sha256=row.content_sha256,
                    status=row.status,
                    trust=row.trust,
                    producer=row.producer,
                    created_at=row.created_at,
                )
                for row in rows[:size]
            ],
            truncated=len(rows) > size,
        )

    async def evaluations(
        self, run_id: str, *, limit: int | None
    ) -> EvaluationCollection:
        await self._require_run(run_id)
        size = _limit(limit)
        # States come from every canonical row, not the page: a later failure
        # beyond ``limit`` must still read as failed.
        canonical = await self._evaluations.list_evaluations_for_run(
            run_id,
            organization_id=self._org,
            latest_only=True,
            limit=MAX_STATE_ROWS,
        )
        rows = canonical[:size]
        states = evaluation_states(build_default_registry(), canonical)
        return EvaluationCollection(
            items=[
                EvaluationView(
                    evaluation_id=row.evaluation_result_id,
                    run_id=row.run_id,
                    task_id=row.task_id,
                    artifact_id=row.artifact_id,
                    evaluator_id=row.evaluator_id,
                    evaluator_version=row.evaluator_version,
                    dimension=row.dimension,
                    status=row.status,
                    method="deterministic" if row.deterministic else "model-assisted",
                    score=row.score,
                    error_code=row.error_code,
                    rationale=row.rationale,
                    trigger=row.trigger,
                    completed_at=row.completed_at,
                )
                for row in rows
            ],
            truncated=len(canonical) > size,
            evaluator_states=[
                EvaluatorState(
                    evaluator_id=evaluator,
                    evaluator_version=version,
                    dimension=dimension,
                    status=status,
                )
                for (evaluator, version, dimension), status in states.items()
            ],
        )

    async def event_history(
        self, run_id: str, *, limit: int | None, cursor: str | None
    ) -> EventPage:
        await self._require_run(run_id)
        after = 0
        if cursor:
            try:
                position = RunEventCursor.decode(cursor)
            except ValueError as error:
                raise InvalidCursorError("cursor is not valid") from error
            if position.run_id != run_id:
                raise InvalidCursorError("cursor does not belong to this run")
            after = position.last_sequence
        size = _limit(limit)
        rows = await self._events.read_events_after(
            run_id, after_sequence=after, limit=size + 1, organization_id=self._org
        )
        page = rows[:size]
        last = page[-1].sequence if page else after
        return EventPage(
            items=[
                EventView(
                    event_id=row.event_id,
                    run_id=row.run_id,
                    sequence=row.sequence,
                    event_type=row.event_type,
                    version=row.event_type_version,
                    occurred_at=row.occurred_at,
                    payload=_public_payload(row.payload),
                )
                for row in page
            ],
            next_cursor=RunEventCursor(run_id=run_id, last_sequence=last).encode(),
            has_more=len(rows) > size,
        )
