"""The evaluator interface and the registry the dispatcher runs.

Production code registers evaluators here; nothing in this package imports
experiment tooling from ``src.evals``.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import EvaluationResult
from src.core.evaluation.events import derive_evaluation_result_id

NOT_ATTEMPTED = "not_attempted"
"""What a reader reports for a dimension with no stored row.

Deliberately not an ``EvaluationStatus``: it is the absence of a verdict, and
must never be countable as one.
"""


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
    from src.core.evaluation.coverage import ClaimsCoverageEvaluator

    registry = EvaluatorRegistry()
    registry.register(ClaimsCoverageEvaluator())
    return registry


def evaluation_states(
    registry: EvaluatorRegistry, canonical_rows: Iterable[object]
) -> Mapping[tuple[str, str, str], str]:
    """Status per registered ``(evaluator, version, dimension)``.

    ``canonical_rows`` is the latest-only view. A registered evaluator with no
    row reads ``not_attempted``, so an evaluation that never ran cannot be
    mistaken for one that passed.
    """
    stored = {
        (row.evaluator_id, row.evaluator_version, row.dimension): row.status  # type: ignore[attr-defined]
        for row in canonical_rows
    }
    return {
        (e.evaluator_id, e.evaluator_version, e.dimension): stored.get(
            (e.evaluator_id, e.evaluator_version, e.dimension), NOT_ATTEMPTED
        )
        for e in registry.evaluators()
    }
