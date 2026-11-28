"""Post-terminal containment verdicts, appended through the evaluation dispatcher."""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.api.services.claim_support_evaluation_service import (
    ClaimSupportEvaluationService,
)
from src.core.acquisition.snapshots import FilesystemSnapshotStore
from src.core.claims.citation_validation import FixtureCorpus
from src.core.claims.materiality import build_inventory
from src.core.claims.recording import ClaimSupportRecorder
from src.core.claims.resolution import ClaimSupportResolver
from src.core.claims.run_citations import bind_run_citations
from src.core.contracts import (
    Artifact,
    ArtifactStatus,
    ClaimSupportStatus,
    Evidence,
    ProducerKind,
    TrustClassification,
)
from src.core.contracts.redaction import snapshot_digest
from src.core.evaluation.citation_checks import citation_check_evaluators
from src.core.evaluation.dispatcher import EvaluationDispatcher
from src.core.evaluation.fixture_support import FixtureManifest
from src.core.evaluation.registry import EvaluationContext, build_default_registry
from src.repositories.artifact_repository import ArtifactRepository
from src.repositories.claim_support_repository import ClaimSupportRepository
from src.repositories.evaluation_result_repository import EvaluationResultRepository
from src.repositories.evidence_repository import EvidenceRepository
from tests.integration.wave4_helpers import seed_run_task_attempt

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 11, 27, tzinfo=UTC)
ORG = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
SNAPSHOT = b"Aspirin reduced the risk of stroke in the trial cohort by 18 percent."
REPORT = (
    "Aspirin reduced stroke risk by 18 percent. "
    "Aspirin did not reduce stroke risk by 18 percent. "
    "Zinc cures every cold in every patient."
)
TAMPERED = "a span the snapshot never contained"
QUOTE = "reduced the risk of stroke in the trial cohort by 18 percent"


def _manifest(tampered_quote: str) -> FixtureManifest:
    claims = [
        c.text
        for c in build_inventory(
            artifact_id="report-1", artifact_kind="research_report", source=REPORT
        ).claims
    ]
    digest = snapshot_digest(SNAPSHOT)
    entries = [
        {
            "claim_text": claims[0],
            "status": "supported",
            "quote": QUOTE,
            "authors": ["Smith"],
            "year": "2020",
            "doi": "10.1000/xyz",
        },
        {"claim_text": claims[2], "status": "supported", "quote": tampered_quote},
    ]
    text = json.dumps(
        {
            "manifest_id": "fx",
            "entries": [
                {**e, "evidence_sha256": digest, "explanation": "Authored."}
                for e in entries
            ],
        }
    )
    return FixtureManifest.from_json(text, hashlib.sha256(text.encode()).hexdigest())


@pytest_asyncio.fixture(name="env")
async def env_fixture(
    test_engine: AsyncEngine, db_session: AsyncSession, tmp_path: Path
):
    store = FilesystemSnapshotStore(root=tmp_path)
    await seed_run_task_attempt(db_session, organization_id=ORG)
    artifacts = ArtifactRepository(db_session)
    snapshot = Artifact(
        artifact_id="snap-1",
        run_id="run-1",
        kind="source_snapshot",
        media_type="text/plain",
        storage_uri=store.put(SNAPSHOT),
        content_sha256=snapshot_digest(SNAPSHOT),
        status=ArtifactStatus.FINAL,
        trust=TrustClassification.EXTERNAL_UNTRUSTED,
        producer="acquisition-tool",
        created_at=NOW,
    )
    await artifacts.create_artifact(snapshot, organization_id=ORG)
    await EvidenceRepository(db_session).record_evidence(
        Evidence(
            evidence_id="ev-1",
            run_id="run-1",
            task_id="task-1",
            source_type="web_page",
            source_uri="https://example.org/trial",
            snapshot_artifact_id="snap-1",
            content_sha256=snapshot_digest(SNAPSHOT),
            locator=f"char:0-{len(SNAPSHOT)}",
            trust=TrustClassification.EXTERNAL_UNTRUSTED,
            producer_kind=ProducerKind.SYSTEM,
            acquired_at=NOW,
        ),
        organization_id=ORG,
    )
    await artifacts.create_artifact(
        Artifact(
            artifact_id="report-1",
            run_id="run-1",
            kind="research_report",
            media_type="application/json",
            storage_uri="inline://report-1",
            content_sha256=hashlib.sha256(REPORT.encode()).hexdigest(),
            status=ArtifactStatus.FINAL,
            trust=TrustClassification.DERIVED_UNTRUSTED,
            producer="direct_execution_service",
            producer_kind=ProducerKind.SYSTEM,
            created_at=NOW,
            metadata={"content": REPORT},
        ),
        organization_id=ORG,
    )
    inventory = build_inventory(
        artifact_id="report-1", artifact_kind="research_report", source=REPORT
    )
    counter = iter(range(1, 100))
    resolution = ClaimSupportResolver(
        id_factory=lambda: f"report-1:support-{next(counter)}"
    ).resolve(inventory=inventory, run_id="run-1", verdicts=(), resolved_at=NOW)
    await ClaimSupportRecorder(ClaimSupportRepository(db_session)).record(
        resolution, organization_id=ORG
    )
    await db_session.commit()
    return async_sessionmaker(test_engine, expire_on_commit=False), store


def _dispatcher(env, quote: str = "x") -> EvaluationDispatcher:
    factory, store = env
    registry = build_default_registry()
    registry.register(ClaimSupportEvaluationService(_manifest(quote), store))
    return EvaluationDispatcher(
        session_factory=factory, registry=registry, clock=lambda: NOW
    )


async def _run(dispatcher: EvaluationDispatcher, trigger: str = "run.succeeded") -> int:
    return await dispatcher.evaluate_run(
        run_id="run-1", organization_id=ORG, trigger=trigger, artifact_id="report-1"
    )


async def _supports(env) -> list:
    async with env[0]() as session:
        return await ClaimSupportRepository(session).list_claim_supports_for_run(
            "run-1", organization_id=ORG
        )


async def _latest(env) -> dict[str, str]:
    async with env[0]() as session:
        rows = await ClaimSupportRepository(session).list_claim_supports_for_run(
            "run-1", organization_id=ORG, latest_per_claim=True
        )
    return {r.claim_text[:12]: r.status for r in rows}


async def test_a_terminal_run_gets_an_appended_containment_pass(env) -> None:
    before = await _supports(env)
    initial = {r.claim_support_id: r.status for r in before}

    await _run(_dispatcher(env, QUOTE))

    after = await _supports(env)
    assert {
        r.claim_support_id: r.status for r in after if r.claim_support_id in initial
    } == initial
    appended = [r for r in after if r.claim_support_id not in initial]
    assert len(appended) == len(before)
    judged = [r for r in appended if r.evaluator_id == "fixture.claim_support"]
    assert {r.producer_kind for r in judged} == {"system"}
    assert all("method=containment" in r.explanation for r in judged)
    assert (await _latest(env))["Aspirin redu"] == ClaimSupportStatus.SUPPORTED.value


async def test_a_negated_or_tampered_claim_is_never_supported(env) -> None:
    await _run(_dispatcher(env, "a span the snapshot never contained"))

    latest = await _latest(env)
    assert latest["Aspirin did "] != ClaimSupportStatus.SUPPORTED.value
    assert latest["Zinc cures e"] == ClaimSupportStatus.UNSUPPORTED.value


async def test_a_redelivered_trigger_adds_no_rows(env) -> None:
    dispatcher = _dispatcher(env, "a span the snapshot never contained")
    await _run(dispatcher)
    count = len(await _supports(env))

    assert await _run(dispatcher) == 0

    assert len(await _supports(env)) == count


async def test_evaluating_the_same_trigger_twice_directly_adds_no_rows(env) -> None:
    evaluator = ClaimSupportEvaluationService(_manifest(QUOTE), env[1])
    for _ in range(2):
        async with env[0]() as session:
            ctx = EvaluationContext(
                run_id="run-1",
                organization_id=ORG,
                task_id=None,
                artifact_id="report-1",
                claims=(),
                trigger="direct",
                completed_at=NOW,
                session=session,
            )
            await evaluator.evaluate(ctx)
            await session.commit()

    assert len(await _supports(env)) == 6


async def test_coverage_is_judged_after_the_support_pass_not_before(env) -> None:
    await _run(_dispatcher(env, "a span the snapshot never contained"))

    async with env[0]() as session:
        rows = await EvaluationResultRepository(session).list_evaluations_for_run(
            "run-1", organization_id=ORG, dimension="claims.coverage"
        )
    (row,) = rows
    assert row.error_code != "claim_support_not_attempted"
    assert row.details["attempted"] == 2


async def _bound(env, manifest: FixtureManifest):
    async with env[0]() as session:
        return await bind_run_citations(
            session,
            run_id="run-1",
            organization_id=ORG,
            metadata_source=manifest.metadata_candidates,
        )


async def test_only_a_supported_claim_mints_a_citation_bound_to_its_evidence(
    env,
) -> None:
    manifest = _manifest(TAMPERED)
    await _run(_dispatcher(env, TAMPERED))

    bound = await _bound(env, manifest)

    assert bound is not None
    (record,) = bound.minted
    assert record.evidence_id == "ev-1"
    assert record.citation_id is not None
    assert bound.evidence["ev-1"].content_sha256 == snapshot_digest(SNAPSHOT)
    others = [r for r in bound.result.records if r is not record]
    assert others
    assert all(r.citation_id is None for r in others)


async def test_nothing_is_minted_before_a_support_pass_exists(env) -> None:
    bound = await _bound(env, _manifest(QUOTE))

    assert bound is not None
    assert bound.minted == ()


async def test_citation_checks_run_after_the_support_pass_in_one_delivery(
    env,
) -> None:
    manifest = _manifest(QUOTE)
    corpus_text = json.dumps(
        [{"doi": "10.1000/xyz", "first_author": "Smith", "year": 2020}]
    )
    corpus = FixtureCorpus.from_json(
        corpus_text, hashlib.sha256(corpus_text.encode()).hexdigest()
    )
    registry = build_default_registry()
    registry.register(ClaimSupportEvaluationService(manifest, env[1]))
    for evaluator in citation_check_evaluators(corpus, manifest.metadata_candidates):
        registry.register(evaluator)

    await _run(
        EvaluationDispatcher(
            session_factory=env[0], registry=registry, clock=lambda: NOW
        )
    )

    async with env[0]() as session:
        rows = await EvaluationResultRepository(session).list_evaluations_for_run(
            "run-1", organization_id=ORG, limit=100
        )
    by_dimension = {r.dimension: r.status for r in rows}
    assert by_dimension["citation.reference_resolution"] == "passed"
    assert by_dimension["citation.retraction_status"] == "passed"


async def _overwrite_snapshot(env, data: bytes) -> None:
    digest = snapshot_digest(SNAPSHOT)
    path = env[1]._root / digest[:2] / digest
    path.chmod(0o644)
    path.write_bytes(data)


async def test_a_tampered_snapshot_gives_a_verdict_not_a_retry_loop(env) -> None:
    await _overwrite_snapshot(env, b"Aspirin did not reduce anything.")
    dispatcher = _dispatcher(env, QUOTE)

    await _run(dispatcher)
    count = len(await _supports(env))
    assert await _run(dispatcher) == 0

    assert len(await _supports(env)) == count
    async with env[0]() as session:
        (row,) = await EvaluationResultRepository(session).list_evaluations_for_run(
            "run-1", organization_id=ORG, dimension="claims.support"
        )
    assert (row.status, row.error_code) == ("failed", "fixture_evidence_integrity")
    latest = await _latest(env)
    assert latest["Aspirin redu"] == ClaimSupportStatus.UNSUPPORTED.value


async def test_racing_deliveries_record_one_pass_and_neither_raises(env) -> None:
    before = len(await _supports(env))

    results = await asyncio.gather(
        _run(_dispatcher(env, QUOTE)),
        _run(_dispatcher(env, QUOTE)),
        return_exceptions=True,
    )

    assert not [r for r in results if isinstance(r, Exception)]
    assert len(await _supports(env)) == before * 2


async def test_a_report_of_another_run_is_not_bound(env) -> None:
    async with env[0]() as session:
        bound = await bind_run_citations(
            session,
            run_id="some-other-run",
            organization_id=ORG,
            artifact_id="report-1",
        )

    assert bound is None


async def test_a_report_whose_text_does_not_match_its_digest_is_not_bound(env) -> None:
    async with env[0]() as session:
        await ArtifactRepository(session).create_artifact(
            Artifact(
                artifact_id="report-bad",
                run_id="run-1",
                kind="research_report",
                media_type="application/json",
                storage_uri="inline://report-bad",
                content_sha256="0" * 64,
                status=ArtifactStatus.FINAL,
                trust=TrustClassification.DERIVED_UNTRUSTED,
                producer="direct_execution_service",
                producer_kind=ProducerKind.SYSTEM,
                created_at=NOW,
                metadata={"content": REPORT},
            ),
            organization_id=ORG,
        )
        await session.commit()
    async with env[0]() as session:
        bound = await bind_run_citations(
            session,
            run_id="run-1",
            organization_id=ORG,
            artifact_id="report-bad",
        )

    assert bound is None
