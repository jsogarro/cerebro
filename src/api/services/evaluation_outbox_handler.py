"""Outbox handler that runs the registered evaluators after a run succeeds.

The relay connects as the maintenance role, which has no policy on
``agent_evaluation_results``. Everything the evaluators read or write therefore
goes through a tenant-scoped application session for the run's own
organization, so a delivery can never touch another tenant's rows.

A failure here surfaces as a failed delivery (backoff, then dead letter). It
cannot affect the run: the run, its events and its report were committed before
this row was ever claimed, and nothing here writes to them.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.core.evaluation.dispatcher import EvaluationDispatcher
from src.core.evaluation.events import EVALUATION_TRIGGER_EVENT
from src.core.evaluation.registry import EvaluatorRegistry
from src.models.db.run_event import AgentRunEventOutbox
from src.models.db.session import tenant_session
from src.repositories.artifact_repository import ArtifactRepository

logger = get_logger()

# Mirrors the kind ``DirectExecutionService`` stores its final report under.
_REPORT_KIND = "research_report"


class EvaluationOutboxHandler:
    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        registry: EvaluatorRegistry,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry

    async def __call__(self, row: AgentRunEventOutbox) -> bool:
        envelope = row.payload or {}
        if envelope.get("event_type") != EVALUATION_TRIGGER_EVENT:
            return True
        if row.organization_id is None:
            return False
        organization = row.organization_id

        def scoped() -> AbstractAsyncContextManager[AsyncSession]:
            return tenant_session(self._session_factory, organization)

        async with tenant_session(self._session_factory, organization) as session:
            artifacts = await ArtifactRepository(session).list_artifacts_for_run(
                row.run_id, organization_id=organization
            )
        reports = [a for a in artifacts if a.kind == _REPORT_KIND]
        dispatcher = EvaluationDispatcher(
            session_factory=scoped,
            registry=self._registry,
        )
        await dispatcher.evaluate_run(
            run_id=row.run_id,
            organization_id=organization,
            trigger=EVALUATION_TRIGGER_EVENT,
            artifact_id=reports[-1].artifact_id if reports else None,
        )
        return True
