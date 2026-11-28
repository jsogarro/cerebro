"""The evaluator interface and the registry the dispatcher runs.

Production code registers evaluators here; nothing in this package imports
experiment tooling from ``src.evals``.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import EvaluationResult
from src.core.evaluation.events import derive_evaluation_result_id

NOT_ATTEMPTED = "not_attempted"
"""What a reader reports for a dimension with no stored row.

Deliberately not an ``EvaluationStatus``: it is the absence of a verdict, and
must never be countable as one.
"""


# A dimension judged per subject reads as its worst row, so a later pass on one
# claim cannot hide a failure on another.
_SEVERITY = {"passed": 0, "abstained": 1, "failed": 2, "error": 3}


@dataclass(frozen=True)
class ClaimView:
    """The latest support judgment for one claim of the evaluated artifact."""

    claim_id: str
    status: str
    absent_evidence_reason: str | None


@dataclass(frozen=True)
class EvaluationContext:
    run_id: str
    organization_id: uuid.UUID
    task_id: str | None
    artifact_id: str | None
    claims: tuple[ClaimView, ...]
    trigger: str
    completed_at: datetime
    # Read-only by convention: results are written by the dispatcher.
    session: AsyncSession

    def result_id(
        self,
        evaluator: "Evaluator",
        *,
        subject_id: str | None = None,
        discriminator: str = "",
    ) -> str:
        """Id for one of this evaluator's rows, separated by trigger and subject
        so a per-claim evaluator cannot forget to keep its rows apart."""
        fold = f"{self.trigger}|{discriminator}"
        if subject_id is not None:
            fold += f"|subject:{subject_id}"
        return derive_evaluation_result_id(
            organization_id=self.organization_id,
            run_id=self.run_id,
            evaluator_id=evaluator.evaluator_id,
            evaluator_version=evaluator.evaluator_version,
            dimension=evaluator.dimension,
            artifact_id=self.artifact_id,
            discriminator=fold,
        )


class Evaluator(Protocol):
    evaluator_id: str
    evaluator_version: str
    dimension: str
    reads_claim_support: bool
    """True when a claim-support pass can change this evaluator's verdict."""

    async def evaluate(self, ctx: EvaluationContext) -> Sequence[EvaluationResult]: ...


class EvaluatorRegistry:
    def __init__(self) -> None:
        self._evaluators: dict[tuple[str, str], Evaluator] = {}

    def register(self, evaluator: Evaluator) -> None:
        key = (evaluator.evaluator_id, evaluator.evaluator_version)
        if key in self._evaluators:
            raise ValueError(f"evaluator {key!r} is already registered")
        self._evaluators[key] = evaluator

    def evaluators(self) -> tuple[Evaluator, ...]:
        return tuple(self._evaluators[key] for key in sorted(self._evaluators))

    def claim_dependent_ids(self) -> frozenset[str]:
        return frozenset(
            e.evaluator_id for e in self._evaluators.values() if e.reads_claim_support
        )


def build_default_registry() -> EvaluatorRegistry:
    """Coverage always; the fixture support pass and citation checks only when
    their pinned manifest or corpus is configured, so a deployment without one
    records nothing it cannot stand behind."""
    from src.core.config import settings
    from src.core.evaluation.coverage import ClaimsCoverageEvaluator
    from src.core.evaluation.fixture_support import load_fixture_manifest

    registry = EvaluatorRegistry()
    registry.register(ClaimsCoverageEvaluator())

    manifest = load_fixture_manifest(
        settings.CLAIM_SUPPORT_FIXTURE_MANIFEST,
        settings.CLAIM_SUPPORT_FIXTURE_MANIFEST_SHA256,
    )
    if manifest:
        from src.api.services.claim_support_evaluation_service import (
            ClaimSupportEvaluationService,
        )
        from src.core.acquisition.snapshots import FilesystemSnapshotStore

        registry.register(
            ClaimSupportEvaluationService(
                manifest,
                FilesystemSnapshotStore(root=Path(settings.SOURCE_SNAPSHOT_DIR)),
            )
        )

    if settings.CITATION_CORPUS:
        from src.core.claims.citation_validation import FixtureCorpus
        from src.core.evaluation.citation_checks import citation_check_evaluators

        corpus = FixtureCorpus.from_json(
            Path(settings.CITATION_CORPUS).read_text(),
            settings.CITATION_CORPUS_SHA256 or "",
        )
        for evaluator in citation_check_evaluators(
            corpus, manifest.metadata_candidates if manifest else None
        ):
            registry.register(evaluator)
    return registry


def evaluation_states(
    registry: EvaluatorRegistry, canonical_rows: Iterable[object]
) -> Mapping[tuple[str, str, str], str]:
    """Status per registered ``(evaluator, version, dimension)``.

    ``canonical_rows`` is the latest-only view; several rows for one dimension
    (one per subject) reduce to the worst. A registered evaluator with no
    row reads ``not_attempted``, so an evaluation that never ran cannot be
    mistaken for one that passed.
    """
    worst: dict[tuple[str, str, str], str] = {}
    for row in canonical_rows:
        key = (row.evaluator_id, row.evaluator_version, row.dimension)  # type: ignore[attr-defined]
        status: str = row.status  # type: ignore[attr-defined]
        if key not in worst or _SEVERITY[status] > _SEVERITY[worst[key]]:
            worst[key] = status
    return {
        (e.evaluator_id, e.evaluator_version, e.dimension): worst.get(
            (e.evaluator_id, e.evaluator_version, e.dimension), NOT_ATTEMPTED
        )
        for e in registry.evaluators()
    }
