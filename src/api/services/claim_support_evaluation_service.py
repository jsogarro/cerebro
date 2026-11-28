"""Post-terminal claim-support evaluation, run by the evaluation dispatcher.

The run's own terminal commit records every claim ``not_attempted``. This
evaluator runs afterwards, off the outbox, and appends a second pass: it reads
the run's evidence snapshots back at their cited digests, judges the report's
claims by fixture containment, and records one verdict per material claim. The
first pass is never edited, so a reader's "latest per claim" moves from
``not_attempted`` to the containment verdict and the history keeps both.

It writes through the dispatcher's tenant-scoped session, so the claim rows and
the evaluation summary commit together or not at all.
"""

import hashlib
from collections.abc import Sequence
from typing import Final

from src.core.acquisition.resolution import UnresolvableLocatorError, resolve
from src.core.acquisition.snapshots import SnapshotStore
from src.core.claims.materiality import build_inventory
from src.core.claims.recording import ClaimSupportRecorder
from src.core.claims.resolution import ClaimSupportResolver
from src.core.contracts import EvaluationResult, EvaluationStatus
from src.core.contracts.locators import InvalidLocatorError
from src.core.contracts.redaction import snapshot_digest
from src.core.evaluation.fixture_support import (
    FIXTURE_EVALUATOR_ID,
    FIXTURE_EVALUATOR_VERSION,
    METHOD,
    EvidenceExcerpt,
    FixtureManifest,
    judge,
)
from src.core.evaluation.registry import EvaluationContext
from src.models.db.evidence import AgentEvidence
from src.repositories.artifact_repository import ArtifactRepository
from src.repositories.claim_support_repository import ClaimSupportRepository
from src.repositories.evidence_repository import EvidenceRepository

# Mirrors the metadata key ``DirectExecutionService`` stores the report text under.
_CONTENT_KEY: Final = "content"


class ClaimSupportEvaluationService:
    evaluator_id = FIXTURE_EVALUATOR_ID
    evaluator_version = FIXTURE_EVALUATOR_VERSION
    dimension = "claims.support"
    reads_claim_support = False

    def __init__(self, manifest: FixtureManifest, store: SnapshotStore) -> None:
        self._manifest = manifest
        self._store = store

    async def evaluate(self, ctx: EvaluationContext) -> Sequence[EvaluationResult]:
        if ctx.artifact_id is None:
            return [self._result(ctx, EvaluationStatus.ABSTAINED, "no_final_report")]
        artifact = await ArtifactRepository(ctx.session).get_artifact(
            ctx.artifact_id, organization_id=ctx.organization_id
        )
        text = (artifact.metadata_ or {}).get(_CONTENT_KEY) if artifact else None
        if (
            artifact is None
            or not isinstance(text, str)
            or hashlib.sha256(text.encode("utf-8")).hexdigest()
            != artifact.content_sha256
        ):
            return [
                self._result(ctx, EvaluationStatus.FAILED, "final_report_unverifiable")
            ]

        inventory = build_inventory(
            artifact_id=artifact.artifact_id, artifact_kind=artifact.kind, source=text
        )
        evidence = await self._read_evidence(ctx)
        judgement = judge(
            self._manifest,
            [(c.claim_id, c.text) for c in inventory.claims],
            evidence,
            evaluated_at=ctx.completed_at,
        )
        details: dict[str, str | int] = {
            "method": METHOD,
            "mode": "fixture",
            "manifest_id": self._manifest.manifest_id,
            "manifest_sha256": self._manifest.sha256,
            "material_claims": inventory.material_claim_count,
            "judged": len(judgement.verdicts),
            "unmapped": judgement.unmapped,
            "integrity_failures": judgement.integrity_failures,
        }

        prefix = (
            f"{artifact.artifact_id}:{self.evaluator_id}:{self.evaluator_version}:"
            f"{hashlib.sha256(ctx.trigger.encode()).hexdigest()[:12]}"
        )
        repository = ClaimSupportRepository(ctx.session)
        # One pass per trigger: a second call under it finds its own rows.
        if not await repository.get_claim_support(
            f"{prefix}:support-1", organization_id=ctx.organization_id
        ):
            sequence = iter(range(1, len(inventory.claims) + 1))
            resolution = ClaimSupportResolver(
                id_factory=lambda: f"{prefix}:support-{next(sequence)}"
            ).resolve(
                inventory=inventory,
                run_id=ctx.run_id,
                verdicts=judgement.verdicts,
                resolved_at=ctx.completed_at,
            )
            await ClaimSupportRecorder(repository).record(
                resolution, organization_id=ctx.organization_id
            )

        if judgement.integrity_failures:
            status, code = EvaluationStatus.FAILED, "fixture_evidence_integrity"
        elif not judgement.verdicts:
            status, code = EvaluationStatus.ABSTAINED, "fixture_input_unmapped"
        else:
            status, code = EvaluationStatus.PASSED, None
        return [
            self._result(
                ctx,
                status,
                code,
                details=details,
                input_sha256=artifact.content_sha256,
            )
        ]

    async def _read_evidence(self, ctx: EvaluationContext) -> list[EvidenceExcerpt]:
        rows = await EvidenceRepository(ctx.session).list_evidence_for_run(
            ctx.run_id, organization_id=ctx.organization_id
        )
        artifacts = ArtifactRepository(ctx.session)
        out = []
        for row in rows:
            snapshot = await artifacts.get_artifact(
                row.snapshot_artifact_id, organization_id=ctx.organization_id
            )
            out.append(
                EvidenceExcerpt(
                    row.evidence_id,
                    row.content_sha256,
                    self._excerpt(row, snapshot.storage_uri) if snapshot else None,
                )
            )
        return out

    def _excerpt(self, row: AgentEvidence, storage_uri: str) -> str | None:
        """The cited span, or ``None`` if the snapshot no longer matches its digest."""
        try:
            data = self._store.get(storage_uri)
            if snapshot_digest(data) != row.content_sha256:
                return None
            return resolve(row.locator, data).decode("utf-8", errors="replace")
        except (OSError, LookupError, InvalidLocatorError, UnresolvableLocatorError):
            return None

    def _result(
        self,
        ctx: EvaluationContext,
        status: EvaluationStatus,
        code: str | None,
        *,
        details: dict[str, str | int] | None = None,
        input_sha256: str | None = None,
    ) -> EvaluationResult:
        return EvaluationResult(
            evaluation_result_id=ctx.result_id(self),
            run_id=ctx.run_id,
            artifact_id=ctx.artifact_id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            dimension=self.dimension,
            status=status,
            deterministic=True,
            error_code=code,
            details=details or {},
            dataset_version=(
                f"{self._manifest.manifest_id}@{self._manifest.sha256[:12]}"
            ),
            input_sha256=input_sha256,
            trigger=ctx.trigger,
            completed_at=ctx.completed_at,
        )


__all__ = ["ClaimSupportEvaluationService"]
