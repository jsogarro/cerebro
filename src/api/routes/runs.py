"""Read-only HTTP surface for durable runs.

Mounted next to ``run_stream`` (``/runs/{id}/events``, the live SSE feed);
``event-history`` is the same durable events as paginated JSON.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.services.run_read_service import (
    MAX_PAGE,
    ArtifactView,
    Collection,
    EvaluationCollection,
    EventPage,
    InvalidCursorError,
    RunNotFoundError,
    RunPage,
    RunReadService,
    RunSummary,
    TaskView,
)
from src.core.contracts.states import RunStatus
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.db.session import SessionDep

router = APIRouter(prefix="/runs")

_T = TypeVar("_T")
Limit = Annotated[int | None, Query(ge=1, le=MAX_PAGE)]


def _service(
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = SessionDep,
) -> RunReadService:
    return RunReadService(session, tenant_context.organization_id)


async def _read(call: Callable[[], Awaitable[_T]]) -> _T:
    try:
        return await call()
    except RunNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found") from error
    except InvalidCursorError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid cursor") from error


@router.get("", response_model=RunPage)
async def list_runs(
    limit: Limit = None,
    cursor: str | None = None,
    workflow_id: str | None = None,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    service: RunReadService = Depends(_service),
) -> RunPage:
    return await _read(
        lambda: service.list_runs(
            limit=limit,
            cursor=cursor,
            workflow_id=workflow_id,
            status=run_status.value if run_status else None,
        )
    )


@router.get("/{run_id}", response_model=RunSummary)
async def get_run(
    run_id: str, service: RunReadService = Depends(_service)
) -> RunSummary:
    return await _read(lambda: service.get_run(run_id))


@router.get("/{run_id}/tasks", response_model=Collection[TaskView])
async def run_tasks(
    run_id: str, limit: Limit = None, service: RunReadService = Depends(_service)
) -> Collection[TaskView]:
    return await _read(lambda: service.tasks(run_id, limit=limit))


@router.get("/{run_id}/artifacts", response_model=Collection[ArtifactView])
async def run_artifacts(
    run_id: str, limit: Limit = None, service: RunReadService = Depends(_service)
) -> Collection[ArtifactView]:
    return await _read(lambda: service.artifacts(run_id, limit=limit))


@router.get("/{run_id}/evaluations", response_model=EvaluationCollection)
async def run_evaluations(
    run_id: str, limit: Limit = None, service: RunReadService = Depends(_service)
) -> EvaluationCollection:
    return await _read(lambda: service.evaluations(run_id, limit=limit))


@router.get("/{run_id}/event-history", response_model=EventPage)
async def run_event_history(
    run_id: str,
    limit: Limit = None,
    cursor: str | None = None,
    service: RunReadService = Depends(_service),
) -> EventPage:
    return await _read(
        lambda: service.event_history(run_id, limit=limit, cursor=cursor)
    )
