"""The four deterministic citation checks, run over a run's bound citations.

Each dimension is its own evaluator because the dispatcher stores one
dimension per evaluator. They read claim support, so they run after any
support pass appended in the same delivery. A citation absent from the pinned
corpus abstains rather than fails, per ``validate_citations``.
"""

from collections.abc import Sequence
from typing import Any

from src.core.claims.citation_validation import (
    DIMENSIONS,
    FixtureCorpus,
    validate_citations,
)
from src.core.claims.run_citations import MetadataSource, bind_run_citations
from src.core.contracts import EvaluationResult, EvaluationStatus
from src.core.evaluation.registry import EvaluationContext

VERSION = "1"


class CitationCheckEvaluator:
    evaluator_version = VERSION
    reads_claim_support = True

    def __init__(
        self,
        dimension: str,
        corpus: FixtureCorpus,
        metadata_source: MetadataSource | None = None,
    ) -> None:
        self.dimension = dimension
        self.evaluator_id = dimension
        self._corpus = corpus
        self._metadata_source = metadata_source

    async def evaluate(self, ctx: EvaluationContext) -> Sequence[EvaluationResult]:
        bound = (
            await bind_run_citations(
                ctx.session,
                run_id=ctx.run_id,
                organization_id=ctx.organization_id,
                artifact_id=ctx.artifact_id,
                metadata_source=self._metadata_source,
            )
            if ctx.artifact_id is not None
            else None
        )
        status: EvaluationStatus
        code: str | None
        details: dict[str, Any]
        if bound is None:
            status, code, details = EvaluationStatus.ABSTAINED, "no_final_report", {}
        else:
            outcome = next(
                d
                for d in validate_citations(
                    [r.metadata for r in bound.minted], self._corpus
                )
                if d.dimension == self.dimension
            )
            status, code = outcome.status, outcome.error_code
            details = {**outcome.details, "advisory": outcome.advisory}
        return [
            EvaluationResult(
                evaluation_result_id=ctx.result_id(self),
                run_id=ctx.run_id,
                artifact_id=ctx.artifact_id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                dimension=self.dimension,
                status=status,
                deterministic=True,
                error_code=code,
                details=details,
                trigger=ctx.trigger,
                completed_at=ctx.completed_at,
            )
        ]


def citation_check_evaluators(
    corpus: FixtureCorpus, metadata_source: MetadataSource | None = None
) -> tuple[CitationCheckEvaluator, ...]:
    return tuple(CitationCheckEvaluator(d, corpus, metadata_source) for d in DIMENSIONS)
