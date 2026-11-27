"""Recording what an academic search returned as run-owned evidence.

``mcp.academic_search`` returns bibliographic records whose abstracts are the
source text a literature review reasons over. When that call ran through the
tool boundary and its durable invocation exists, each record with text is
snapshotted and recorded as evidence of that invocation. The recorder is only
ever called for a *succeeded* outcome, so degraded or fallback output never
reaches it.

**Nothing is synthesized.** A record with no abstract, or no valid way to name
its source, is skipped and counted. A write that fails is reported against its
source URI and writes no row. The caller gets the summary either way, so a
result is never read as better evidenced than it is.

Each source commits in its own session: one failed write cannot discard the
evidence of its siblings, and nothing here is published before it is durable.
Cancelling mid-loop leaves the sources already committed and no summary;
replaying the invocation records the rest (ids are deterministic).
ponytail: one commit per source; batch per invocation if search volume grows.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger

from src.core.contracts.provenance import ToolInvocation
from src.core.tools.secrets import NullSecretProvider, SecretProvider
from src.repositories.artifact_repository import ArtifactRepository
from src.repositories.evidence_repository import EvidenceRepository

from .search_result import (
    MAX_SOURCE_URI_LENGTH,
    RetrievedSource,
    SourceDisposition,
    SourceRecordingOutcome,
    source_text,
    source_uri,
)
from .service import SourceAcquisitionService, retrieved_source_evidence_id
from .snapshots import SnapshotStore
from .sources import SourceType

__all__ = [
    "MAX_SOURCE_URI_LENGTH",
    "SearchEvidenceRecorder",
    "source_text",
    "source_uri",
]

logger = get_logger()


def _outcome(
    index: int,
    source: RetrievedSource,
    uri: str | None,
    disposition: SourceDisposition,
    *,
    evidence_id: str | None = None,
    reason: str | None = None,
) -> SourceRecordingOutcome:
    return SourceRecordingOutcome(
        index=index,
        disposition=disposition,
        source_uri=uri,
        provider=source.provider,
        provider_id=source.provider_id,
        doi=source.doi,
        year=source.year,
        evidence_id=evidence_id,
        reason=reason,
    )


class SearchEvidenceRecorder:
    """Persists the sources of a durable search invocation as evidence."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        store: SnapshotStore,
        secret_provider: SecretProvider | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._store = store
        self._secret_provider = secret_provider or NullSecretProvider()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _service(self, session: AsyncSession) -> SourceAcquisitionService:
        return SourceAcquisitionService(
            boundary=None,
            store=self._store,
            artifacts=ArtifactRepository(session),
            evidence=EvidenceRepository(session),
            secret_provider=self._secret_provider,
            clock=self._clock,
        )

    async def __call__(
        self, invocation: ToolInvocation, organization_id: str | None
    ) -> dict[str, Any]:
        """Record every source ``invocation`` returned; never raises on a write."""

        output = invocation.output or {}
        raw_sources = output.get("sources") or ()
        sources = [
            item
            for item in (raw_sources if isinstance(raw_sources, Sequence) else ())
            if isinstance(item, Mapping)
        ]
        recorded: list[str] = []
        failed: list[dict[str, str]] = []
        outcomes: list[SourceRecordingOutcome] = []
        already_recorded = skipped = 0
        for index, raw in enumerate(sources):
            source = RetrievedSource.from_record(raw)
            text, uri = source.text(), source.uri()

            record_outcome = partial(_outcome, index, source, uri)

            if not text or not uri:
                skipped += 1
                outcomes.append(
                    record_outcome(
                        SourceDisposition.SKIPPED,
                        reason="no_text" if not text else "no_source_uri",
                    )
                )
                continue
            content = text.encode("utf-8")
            replay_id = retrieved_source_evidence_id(invocation, uri, content)
            try:
                evidence = await self._record(
                    invocation, organization_id, uri, content, source.bibliography()
                )
            except IntegrityError:
                # A concurrent writer committed the same source first.
                if await self._exists(invocation, organization_id, uri, content):
                    already_recorded += 1
                    outcomes.append(
                        record_outcome(
                            SourceDisposition.ALREADY_RECORDED, evidence_id=replay_id
                        )
                    )
                    continue
                failed.append({"source_uri": uri, "reason": "IntegrityError"})
                outcomes.append(
                    record_outcome(SourceDisposition.FAILED, reason="IntegrityError")
                )
                continue
            except Exception as error:
                logger.warning(
                    "search_evidence_not_recorded",
                    source_uri=uri,
                    error=type(error).__name__,
                    exc_info=True,
                )
                failed.append({"source_uri": uri, "reason": type(error).__name__})
                outcomes.append(
                    record_outcome(
                        SourceDisposition.FAILED, reason=type(error).__name__
                    )
                )
                continue
            if evidence is None:
                already_recorded += 1
                outcomes.append(
                    record_outcome(
                        SourceDisposition.ALREADY_RECORDED, evidence_id=replay_id
                    )
                )
            else:
                recorded.append(evidence)
                outcomes.append(
                    record_outcome(SourceDisposition.RECORDED, evidence_id=evidence)
                )
        return {
            "recorded": recorded,
            "already_recorded": already_recorded,
            "skipped": skipped,
            "failed": failed,
            "sources": [o.model_dump(mode="json") for o in outcomes],
        }

    async def _record(
        self,
        invocation: ToolInvocation,
        organization_id: str | None,
        uri: str,
        content: bytes,
        metadata: Mapping[str, Any],
    ) -> str | None:
        async with self._session_factory() as session:
            evidence = await self._service(session).record_retrieved_source(
                invocation=invocation,
                organization_id=organization_id,
                source_uri=uri,
                source_type=SourceType.SEARCH_RESULT,
                content=content,
                metadata=metadata,
            )
            await session.commit()
        return None if evidence is None else evidence.evidence_id

    async def _exists(
        self,
        invocation: ToolInvocation,
        organization_id: str | None,
        uri: str,
        content: bytes,
    ) -> bool:
        async with self._session_factory() as session:
            row = await EvidenceRepository(session).get_evidence(
                retrieved_source_evidence_id(invocation, uri, content),
                organization_id=organization_id,
            )
        return row is not None
