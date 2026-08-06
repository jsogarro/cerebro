"""The claim-support write path, against real Postgres.

The unit tests prove a resolution cannot be incomplete. These prove the
completeness survives persistence: one row per material claim, the
never-evaluated claims among them, nothing committed behind the caller's back,
and no path by which ``evidence_count`` could disagree with ``evidence_ids``.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.claims import build_inventory
from src.core.claims.recording import ClaimSupportRecorder
from src.core.claims.resolution import ClaimSupportResolver, ClaimVerdict
from src.core.contracts import (
    AbsentEvidenceReason,
    ClaimSupportStatus,
    ProducerKind,
    PromptBinding,
)
from src.models.db.claim_support import AgentClaimSupport
from src.repositories.claim_support_repository import ClaimSupportRepository
from tests.integration.wave4_helpers import seed_artifact, seed_run_task_attempt

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 6, tzinfo=UTC)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
RUN_ID = "run-1"
ARTIFACT_ID = "artifact-1"

TWO_CLAIM_SOURCE = (
    "The model improves accuracy by five percent.\n"
    "Latency fell from 210ms to 180ms in the same run.\n"
)

EVALUATOR_BINDING = PromptBinding(
    prompt_id="entailment-check",
    prompt_version="1.0",
    template_sha256="a" * 64,
    rendered_sha256="b" * 64,
)


@pytest_asyncio.fixture(name="recorder")
async def recorder_fixture(db_session: AsyncSession) -> ClaimSupportRecorder:
    return ClaimSupportRecorder(ClaimSupportRepository(db_session))


@pytest_asyncio.fixture(name="seeded", autouse=True)
async def seeded_fixture(db_session: AsyncSession) -> None:
    await seed_run_task_attempt(db_session, organization_id=ORG_ID)
    await seed_artifact(db_session, organization_id=ORG_ID)


def _inventory(source: str = TWO_CLAIM_SOURCE):  # type: ignore[no-untyped-def]
    return build_inventory(
        artifact_id=ARTIFACT_ID, artifact_kind="research_report", source=source
    )


def _resolve(source: str = TWO_CLAIM_SOURCE, verdicts=()):  # type: ignore[no-untyped-def]
    counter = iter(range(1, 1000))
    resolver = ClaimSupportResolver(id_factory=lambda: f"claim-support-{next(counter)}")
    return resolver.resolve(
        inventory=_inventory(source),
        run_id=RUN_ID,
        verdicts=verdicts,
        resolved_at=NOW,
    )


@pytest.mark.asyncio
async def test_one_row_lands_for_every_material_claim(
    recorder: ClaimSupportRecorder, db_session: AsyncSession
) -> None:
    resolution = _resolve()
    assert resolution.material_claim_count == 2

    await recorder.record(resolution, organization_id=ORG_ID)

    stored = await db_session.execute(
        select(AgentClaimSupport.claim_id).where(AgentClaimSupport.run_id == RUN_ID)
    )
    assert sorted(stored.scalars().all()) == sorted(
        claim.claim_id for claim in resolution.inventory.claims
    )


@pytest.mark.asyncio
async def test_a_claim_no_evaluator_reached_is_durable_and_says_why(
    recorder: ClaimSupportRecorder, db_session: AsyncSession
) -> None:
    """The gap has to survive the round trip, not just the resolver.

    ``not_attempted`` is the state Wave 7 must be able to separate from a
    genuine unsupported finding; if it were lost at the write path, the two
    would arrive at the gate indistinguishable and both counted the same.
    """
    inventory = _inventory()
    evaluated = ClaimVerdict(
        claim_id=inventory.claims[0].claim_id,
        status=ClaimSupportStatus.UNSUPPORTED,
        evidence_ids=(),
        absent_evidence_reason=AbsentEvidenceReason.NO_SOURCE_FOUND,
        explanation="Searched the corpus; nothing addresses this figure.",
        evaluator_id="entailment-evaluator",
        evaluator_version="1.0",
        producer_kind=ProducerKind.MODEL_TURN,
        prompt_binding=EVALUATOR_BINDING,
        evaluated_at=NOW,
    )

    await recorder.record(_resolve(verdicts=[evaluated]), organization_id=ORG_ID)

    rows = (
        (
            await db_session.execute(
                select(AgentClaimSupport).where(AgentClaimSupport.run_id == RUN_ID)
            )
        )
        .scalars()
        .all()
    )
    reasons = {row.claim_id: row.absent_evidence_reason for row in rows}
    assert reasons[inventory.claims[0].claim_id] == "no_source_found"
    assert reasons[inventory.claims[1].claim_id] == "not_attempted"
    assert {row.status for row in rows} == {"unsupported"}


@pytest.mark.asyncio
async def test_evidence_count_agrees_with_evidence_ids_on_every_row(
    recorder: ClaimSupportRecorder, db_session: AsyncSession
) -> None:
    """SQL cannot check this, so the guarantee is that no caller supplies it.

    The recorder passes the contract straight through; the count is derived
    inside the repository. There is no argument on this path that could carry
    a different number.
    """
    inventory = _inventory()
    supported = ClaimVerdict(
        claim_id=inventory.claims[0].claim_id,
        status=ClaimSupportStatus.SUPPORTED,
        evidence_ids=("evidence-1", "evidence-2"),
        explanation="Table 3 reports the same figure.",
        evaluator_id="entailment-evaluator",
        evaluator_version="1.0",
        producer_kind=ProducerKind.MODEL_TURN,
        prompt_binding=EVALUATOR_BINDING,
        evaluated_at=NOW,
    )

    rows = await recorder.record(_resolve(verdicts=[supported]), organization_id=ORG_ID)

    counts = {row.evidence_count for row in rows}
    assert counts == {2, 0}
    for row in rows:
        assert row.evidence_count == len(row.evidence_ids)


@pytest.mark.asyncio
async def test_the_recorder_leaves_committing_to_the_caller(
    recorder: ClaimSupportRecorder, db_session: AsyncSession
) -> None:
    """Half a resolution on disk is worse than none, so it is one transaction.

    If the recorder committed per row, a failure partway through would leave an
    artifact whose claims are partly accounted for — and every downstream count
    would read that as a complete, healthier picture.
    """
    await recorder.record(_resolve(), organization_id=ORG_ID)

    await db_session.rollback()

    remaining = await db_session.execute(
        select(AgentClaimSupport.claim_id).where(AgentClaimSupport.run_id == RUN_ID)
    )
    assert remaining.scalars().all() == []


@pytest.mark.asyncio
async def test_a_fully_evaluated_resolution_persists_today(
    recorder: ClaimSupportRecorder, db_session: AsyncSession
) -> None:
    """Covers the all-evaluated path, where no row is the resolver's own.

    The tests above all persist at least one fill-in — ``producer_kind=system``
    with no prompt binding — so they exercise the nullable branch of the
    repository's prompt-binding write. This one exercises the other branch:
    every row carries a binding. The two together are what the biconditional
    means at the persistence layer, and a regression in either is invisible
    from the other.
    """
    inventory = _inventory()
    verdicts = [
        ClaimVerdict(
            claim_id=claim.claim_id,
            status=ClaimSupportStatus.SUPPORTED,
            evidence_ids=("evidence-1",),
            explanation="Table 3 reports the same figure.",
            evaluator_id="entailment-evaluator",
            evaluator_version="1.0",
            producer_kind=ProducerKind.MODEL_TURN,
            prompt_binding=EVALUATOR_BINDING,
            evaluated_at=NOW,
        )
        for claim in inventory.claims
    ]

    rows = await recorder.record(_resolve(verdicts=verdicts), organization_id=ORG_ID)

    assert len(rows) == inventory.material_claim_count
    stored = await db_session.execute(
        select(AgentClaimSupport.claim_id).where(AgentClaimSupport.run_id == RUN_ID)
    )
    assert sorted(stored.scalars().all()) == sorted(
        claim.claim_id for claim in inventory.claims
    )


@pytest.mark.asyncio
async def test_a_bare_collection_of_supports_is_refused(
    recorder: ClaimSupportRecorder,
) -> None:
    """The completeness check is skippable only by not going through it.

    Handing the recorder the rows themselves would do exactly that, so the
    parameter's type is enforced at run time rather than relied on statically:
    the call sites this protects against are the ones written later.
    """
    resolution = _resolve()
    supports = resolution.supports
    assert len(supports) == 2, "the tuple must be a plausible stand-in"

    with pytest.raises(TypeError):
        await recorder.record(supports, organization_id=ORG_ID)  # type: ignore[arg-type]
