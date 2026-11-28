"""Bind a finished run's report claims to citations over that run's own evidence.

The binder wants draft prose and character spans; the stored report is
canonical JSON whose offsets mean nothing in prose. So the draft is rebuilt
from the inventory's own claim texts, one per paragraph, and each claim's span
in that draft is recorded here. A citation therefore always names a claim and
evidence the run stored, and a report can render nothing else.

Metadata comes only from what the caller supplies for an evidence id (the
binder refuses anything it cannot trace); with none, every citation stays
``incomplete`` and says which field is missing.
"""

import hashlib
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.claims.citation_binding import bind_citations
from src.core.claims.materiality import build_inventory
from src.core.contracts import (
    AbsentEvidenceReason,
    ClaimSupport,
    ClaimSupportStatus,
    Evidence,
    ProducerKind,
    PromptBinding,
    TrustClassification,
)
from src.core.contracts.citation import (
    CitationBindingResult,
    CitationRecord,
    CitationStatus,
    ClaimSpan,
    MetadataCandidate,
)
from src.models.db.claim_support import AgentClaimSupport
from src.models.db.evidence import AgentEvidence
from src.repositories.artifact_repository import ArtifactRepository
from src.repositories.claim_support_repository import ClaimSupportRepository
from src.repositories.evidence_repository import EvidenceRepository

REPORT_KIND: Final = "research_report"
_CONTENT_KEY: Final = "content"
_MINTED = frozenset({CitationStatus.MINTED, CitationStatus.MINTED_PARTIAL})

MetadataSource = Callable[
    [Mapping[str, str]], Mapping[str, Sequence[MetadataCandidate]]
]
"""Maps ``{evidence_id: content_sha256}`` to the metadata offered for each."""


@dataclass(frozen=True)
class BoundEvidence:
    evidence_id: str
    content_sha256: str
    locator: str
    source_uri: str


@dataclass(frozen=True)
class RunCitations:
    artifact_id: str
    result: CitationBindingResult
    evidence: Mapping[str, BoundEvidence]

    @property
    def minted(self) -> tuple[CitationRecord, ...]:
        return tuple(r for r in self.result.records if r.status in _MINTED)


async def bind_run_citations(
    session: AsyncSession,
    *,
    run_id: str,
    organization_id: uuid.UUID | str,
    artifact_id: str | None = None,
    metadata_source: MetadataSource | None = None,
) -> RunCitations | None:
    """Return the run's citations, or ``None`` when it has no verifiable report."""
    artifacts = ArtifactRepository(session)
    if artifact_id is None:
        reports = [
            a
            for a in await artifacts.list_artifacts_for_run(
                run_id, organization_id=organization_id
            )
            if a.kind == REPORT_KIND
        ]
        if not reports:
            return None
        artifact_id = reports[-1].artifact_id
    artifact = await artifacts.get_artifact(
        artifact_id, organization_id=organization_id
    )
    text = (artifact.metadata_ or {}).get(_CONTENT_KEY) if artifact else None
    if (
        artifact is None
        or artifact.run_id != run_id
        or not isinstance(text, str)
        or hashlib.sha256(text.encode("utf-8")).hexdigest() != artifact.content_sha256
    ):
        return None

    inventory = build_inventory(
        artifact_id=artifact_id, artifact_kind=artifact.kind, source=text
    )
    support_rows = {
        row.claim_id: row
        for row in await ClaimSupportRepository(session).list_claim_supports_for_run(
            run_id, organization_id=organization_id, latest_per_claim=True
        )
        if row.artifact_id == artifact_id
    }
    evidence_rows = await EvidenceRepository(session).list_evidence_for_run(
        run_id, organization_id=organization_id
    )
    evidence = {row.evidence_id: _evidence(row) for row in evidence_rows}

    draft: list[str] = []
    spans: list[ClaimSpan] = []
    support: dict[str, ClaimSupport] = {}
    cursor = 0
    for claim in inventory.claims:
        row = support_rows.get(claim.claim_id)
        spans.append(
            ClaimSpan(
                claim_id=claim.claim_id,
                start=cursor,
                end=cursor + len(claim.text),
                evidence_ids=tuple(row.evidence_ids) if row else (),
            )
        )
        if row is not None:
            support[claim.claim_id] = _support(row)
        draft.append(claim.text)
        cursor += len(claim.text) + 2

    digests = {eid: e.content_sha256 for eid, e in evidence.items()}
    result = bind_citations(
        "\n\n".join(draft),
        spans,
        evidence=evidence,
        support=support,
        metadata=metadata_source(digests) if metadata_source else {},
    )
    return RunCitations(
        artifact_id=artifact_id,
        result=result,
        evidence={
            row.evidence_id: BoundEvidence(
                row.evidence_id, row.content_sha256, row.locator, row.source_uri
            )
            for row in evidence_rows
        },
    )


def _binding(row: AgentEvidence | AgentClaimSupport) -> PromptBinding | None:
    if (
        row.prompt_id is None
        or row.prompt_version is None
        or row.template_sha256 is None
        or row.rendered_sha256 is None
    ):
        return None
    return PromptBinding(
        prompt_id=row.prompt_id,
        prompt_version=row.prompt_version,
        template_sha256=row.template_sha256,
        rendered_sha256=row.rendered_sha256,
    )


def _evidence(row: AgentEvidence) -> Evidence:
    return Evidence(
        evidence_id=row.evidence_id,
        run_id=row.run_id,
        task_id=row.task_id,
        source_type=row.source_type,
        source_uri=row.source_uri,
        snapshot_artifact_id=row.snapshot_artifact_id,
        content_sha256=row.content_sha256,
        locator=row.locator,
        trust=TrustClassification(row.trust),
        producer_kind=ProducerKind(row.producer_kind),
        prompt_binding=_binding(row),
        producer_tool_invocation_id=row.producer_tool_invocation_id,
        parent_evidence_ids=tuple(row.parent_evidence_ids or ()),
        acquired_at=row.acquired_at,
    )


def _support(row: AgentClaimSupport) -> ClaimSupport:
    return ClaimSupport(
        claim_support_id=row.claim_support_id,
        run_id=row.run_id,
        artifact_id=row.artifact_id,
        claim_id=row.claim_id,
        claim_text=row.claim_text,
        status=ClaimSupportStatus(row.status),
        evidence_ids=tuple(row.evidence_ids or ()),
        absent_evidence_reason=(
            AbsentEvidenceReason(row.absent_evidence_reason)
            if row.absent_evidence_reason
            else None
        ),
        evaluator_id=row.evaluator_id,
        evaluator_version=row.evaluator_version,
        producer_kind=ProducerKind(row.producer_kind),
        prompt_binding=_binding(row),
        explanation=row.explanation,
        evaluated_at=row.evaluated_at,
    )
