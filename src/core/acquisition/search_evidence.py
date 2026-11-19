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

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlsplit

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog import get_logger

from src.core.contracts.provenance import ToolInvocation
from src.core.tools.secrets import NullSecretProvider, SecretProvider
from src.repositories.artifact_repository import ArtifactRepository
from src.repositories.evidence_repository import EvidenceRepository

from .service import SourceAcquisitionService, retrieved_source_evidence_id
from .snapshots import SnapshotStore
from .sources import SourceType

__all__ = ["SearchEvidenceRecorder", "source_text", "source_uri"]

logger = get_logger()

MAX_SOURCE_URI_LENGTH: Final[int] = 2048
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")


def source_text(source: Mapping[str, Any]) -> str:
    """The text a search record contributes: its title and abstract."""

    abstract = str(source.get("abstract") or "").strip()
    if not abstract:
        return ""
    title = str(source.get("title") or "").strip()
    return f"{title}\n\n{abstract}" if title else abstract


def source_uri(source: Mapping[str, Any]) -> str | None:
    """Name the record's source from an http(s) URL or a DOI, else ``None``.

    Provider-supplied strings are untrusted: anything else, or anything over
    the length cap, is refused rather than normalized.
    """

    url = str(source.get("url") or "").strip()
    if url:
        parts = urlsplit(url)
        valid = (
            parts.scheme in ("http", "https")
            and bool(parts.netloc)
            and "@" not in parts.netloc  # userinfo can carry credentials
        )
        return url if valid and len(url) <= MAX_SOURCE_URI_LENGTH else None
    doi = str(source.get("doi") or "").strip()
    if _DOI.match(doi) and len(doi) <= MAX_SOURCE_URI_LENGTH - 16:
        return f"https://doi.org/{doi}"
    return None


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
        already_recorded = skipped = 0
        for source in sources:
            text, uri = source_text(source), source_uri(source)
            if not text or not uri:
                skipped += 1
                continue
            content = text.encode("utf-8")
            try:
                evidence = await self._record(invocation, organization_id, uri, content)
            except IntegrityError:
                # A concurrent writer committed the same source first.
                if await self._exists(invocation, organization_id, uri, content):
                    already_recorded += 1
                    continue
                failed.append({"source_uri": uri, "reason": "IntegrityError"})
                continue
            except Exception as error:
                logger.warning(
                    "search_evidence_not_recorded",
                    source_uri=uri,
                    error=type(error).__name__,
                    exc_info=True,
                )
                failed.append({"source_uri": uri, "reason": type(error).__name__})
                continue
            if evidence is None:
                already_recorded += 1
            else:
                recorded.append(evidence)
        return {
            "recorded": recorded,
            "already_recorded": already_recorded,
            "skipped": skipped,
            "failed": failed,
        }

    async def _record(
        self,
        invocation: ToolInvocation,
        organization_id: str | None,
        uri: str,
        content: bytes,
    ) -> str | None:
        async with self._session_factory() as session:
            evidence = await self._service(session).record_retrieved_source(
                invocation=invocation,
                organization_id=organization_id,
                source_uri=uri,
                source_type=SourceType.SEARCH_RESULT,
                content=content,
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
