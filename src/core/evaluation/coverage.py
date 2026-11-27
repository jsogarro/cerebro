"""``claims.coverage``: did claim support run for the claims, not whether it held.

A run with no claims, no report, or only unexamined claims abstains rather than
passes, so absence of work is never read as success.
"""

from collections.abc import Sequence

from src.core.contracts import AbsentEvidenceReason, EvaluationResult, EvaluationStatus
from src.core.evaluation.registry import ClaimView, EvaluationContext


def _is_not_attempted(claim: ClaimView) -> bool:
    return (
        claim.status == "unsupported"
        and claim.absent_evidence_reason == AbsentEvidenceReason.NOT_ATTEMPTED
    )


class ClaimsCoverageEvaluator:
    evaluator_id = "claims.coverage"
    evaluator_version = "1.0.0"
    dimension = "claims.coverage"
    reads_claim_support = True

    async def evaluate(self, ctx: EvaluationContext) -> Sequence[EvaluationResult]:
        total = len(ctx.claims)
        missing = sum(_is_not_attempted(claim) for claim in ctx.claims)
        status, code, score = self._verdict(ctx, total, missing)
        return [
            EvaluationResult(
                evaluation_result_id=ctx.result_id(self),
                run_id=ctx.run_id,
                task_id=ctx.task_id,
                artifact_id=ctx.artifact_id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                dimension=self.dimension,
                status=status,
                deterministic=True,
                score=score,
                error_code=code,
                details={
                    "material_claims": total,
                    "attempted": total - missing,
                    "not_attempted": missing,
                },
                trigger=ctx.trigger,
                completed_at=ctx.completed_at,
            )
        ]

    @staticmethod
    def _verdict(
        ctx: EvaluationContext, total: int, missing: int
    ) -> tuple[EvaluationStatus, str | None, float | None]:
        abstained = EvaluationStatus.ABSTAINED
        if ctx.artifact_id is None:
            return abstained, "no_final_report", None
        if total == 0:
            return abstained, "no_material_claims", None
        if missing == total:
            return abstained, "claim_support_not_attempted", None
        if missing:
            return EvaluationStatus.FAILED, "claim_coverage_gap", None
        return EvaluationStatus.PASSED, None, 1.0
