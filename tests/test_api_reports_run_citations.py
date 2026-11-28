"""A report bound to a run renders only minted citations and shows their evidence."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.api.routes import reports as routes
from src.api.routes.reports import CreateReportRequest, ReportResponse
from src.core.claims.citation_binding import bind_citations
from src.core.claims.run_citations import BoundEvidence, RunCitations
from src.core.contracts import (
    AbsentEvidenceReason,
    ClaimSupport,
    ClaimSupportStatus,
    Evidence,
    ProducerKind,
    TrustClassification,
)
from src.core.contracts.citation import (
    CitationField,
    ClaimSpan,
    MetadataCandidate,
    MetadataProvenance,
)
from src.middleware.tenant_context import TenantContext
from src.models.db.base import Base
from src.models.db.generated_report import GeneratedReport, ReportFormat
from src.repositories.report_repository import ReportRepository
from src.services.report_config import ReportSettings

NOW = datetime(2026, 11, 27, tzinfo=UTC)
DIGEST = "a" * 64
RUN_ID = "run-1"
LOOSE = "LOOSE-UNBOUND-CITATION-SENTINEL"


@pytest.fixture
async def report_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[GeneratedReport.__table__, ReportFormat.__table__],
        )
    yield factory
    await engine.dispose()


def _run_citations() -> RunCitations:
    evidence = Evidence(
        evidence_id="ev-1",
        run_id=RUN_ID,
        task_id="task-1",
        source_type="web_page",
        source_uri="https://example.org/trial",
        snapshot_artifact_id="snap-1",
        content_sha256=DIGEST,
        locator="char:0-60",
        trust=TrustClassification.EXTERNAL_UNTRUSTED,
        producer_kind=ProducerKind.SYSTEM,
        acquired_at=NOW,
    )

    def support(claim_id: str, status: ClaimSupportStatus) -> ClaimSupport:
        return ClaimSupport(
            claim_support_id=f"s-{claim_id}",
            run_id=RUN_ID,
            artifact_id="report-1",
            claim_id=claim_id,
            claim_text="x",
            status=status,
            evidence_ids=("ev-1",) if status is ClaimSupportStatus.SUPPORTED else (),
            absent_evidence_reason=(
                None
                if status is ClaimSupportStatus.SUPPORTED
                else AbsentEvidenceReason.NOT_ATTEMPTED
            ),
            evaluator_id="fixture.claim_support",
            evaluator_version="1",
            producer_kind=ProducerKind.SYSTEM,
            explanation="x",
            evaluated_at=NOW,
        )

    text = "Aspirin reduced stroke risk.\n\nZinc cures every cold."
    result = bind_citations(
        text,
        [
            ClaimSpan(claim_id="c1", start=0, end=28, evidence_ids=("ev-1",)),
            ClaimSpan(claim_id="c2", start=30, end=52, evidence_ids=("ev-1",)),
        ],
        evidence={"ev-1": evidence},
        support={
            "c1": support("c1", ClaimSupportStatus.SUPPORTED),
            "c2": support("c2", ClaimSupportStatus.UNSUPPORTED),
        },
        metadata={
            "ev-1": [
                MetadataCandidate(
                    field=CitationField.AUTHOR,
                    value="Smith",
                    provenance=MetadataProvenance.USER_SUPPLIED,
                ),
                MetadataCandidate(
                    field=CitationField.YEAR,
                    value="2020",
                    provenance=MetadataProvenance.USER_SUPPLIED,
                ),
                MetadataCandidate(
                    field=CitationField.DOI,
                    value="10.1000/xyz",
                    provenance=MetadataProvenance.USER_SUPPLIED,
                ),
            ]
        },
    )
    return RunCitations(
        artifact_id="report-1",
        result=result,
        evidence={
            "ev-1": BoundEvidence(
                "ev-1", DIGEST, "char:0-60", "https://example.org/trial"
            )
        },
    )


def _request(**overrides) -> CreateReportRequest:
    overrides.setdefault("workflow_data", {})
    return CreateReportRequest(
        title="Bound", query="q", formats=["html", "json"], **overrides
    )


def _loose() -> dict:
    return {
        "aggregated_results": {
            "citations": [{"id": "loose", "title": LOOSE, "authors": ["Nobody"]}]
        }
    }


async def _generate(
    report_db, tmp_path, monkeypatch, request, *, owner=None, verifiable=True
):
    settings = ReportSettings(
        report_storage_path=str(tmp_path), template_path="src/templates/reports"
    )
    monkeypatch.setattr(routes, "create_report_settings", lambda: settings)
    monkeypatch.setattr(routes, "get_session_factory", lambda: report_db)
    user_id, org_id = uuid4(), uuid4()

    async def run_owner(_session, _run_id):
        return owner or org_id

    async def bound(*_args, **_kwargs):
        return _run_citations() if verifiable else None

    monkeypatch.setattr(routes, "get_run_organization_id", run_owner)
    monkeypatch.setattr(routes, "bind_run_citations", bound)
    async with report_db() as session:
        tasks = BackgroundTasks()
        response = await routes.generate_report(
            request, tasks, session, TenantContext(str(user_id), str(org_id))
        )
    for task in tasks.tasks:
        await task.func(*task.args, **task.kwargs)
    async with report_db() as session:
        stored = await ReportRepository(session).get_report_with_formats(
            response.id, organization_id=org_id, user_id=user_id
        )
    return response, stored, ReportResponse.from_db_report(stored)


async def test_each_citation_maps_to_a_stored_evidence_digest(
    report_db, tmp_path, monkeypatch
):
    _, _, response = await _generate(
        report_db, tmp_path, monkeypatch, _request(run_id=RUN_ID)
    )

    (citation,) = response.citations
    assert citation["evidence_id"] == "ev-1"
    assert citation["evidence_sha256"] == DIGEST
    assert citation["status"] == "minted"
    assert response.citation_binding == "run_bound"


async def test_a_claim_without_support_is_a_gap_not_a_citation(
    report_db, tmp_path, monkeypatch
):
    _, _, response = await _generate(
        report_db, tmp_path, monkeypatch, _request(run_id=RUN_ID)
    )

    assert {g["reason"] for g in response.citation_gaps} >= {"claim_unsupported"}
    assert all(c["claim_id"] != "c2" for c in response.citations)


async def test_the_rendered_report_holds_only_minted_citations(
    report_db, tmp_path, monkeypatch
):
    created, _, _ = await _generate(
        report_db, tmp_path, monkeypatch, _request(run_id=RUN_ID)
    )

    html = next((tmp_path / str(created.id)).glob("*.html")).read_text()
    assert "Smith" in html
    assert "https://example.org/trial" in html


@pytest.mark.parametrize("supplied", ["findings", "citations", "insights"])
async def test_a_run_bound_report_refuses_caller_supplied_content(
    report_db, tmp_path, monkeypatch, supplied
):
    with pytest.raises(HTTPException) as raised:
        await _generate(
            report_db,
            tmp_path,
            monkeypatch,
            _request(
                run_id=RUN_ID,
                workflow_data={"aggregated_results": {supplied: [LOOSE]}},
            ),
        )

    assert raised.value.status_code == 422


FORGED = {
    "citation_id": "forged-1",
    "claim_id": "c1",
    "evidence_id": "ev-victim",
    "evidence_sha256": "f" * 64,
    "locator": "char:0-1",
    "source_uri": "https://forged.example/FORGED",
    "status": "minted",
    "support": "supported",
    "in_text": "(Forger, 2099)",
    "missing_fields": [],
    "authors": ["Forger"],
    "year": "2099",
    "doi": None,
}


async def test_a_caller_cannot_forge_run_bound_citations(
    report_db, tmp_path, monkeypatch
):
    created, stored, response = await _generate(
        report_db,
        tmp_path,
        monkeypatch,
        _request(
            workflow_data={
                "citation_binding": "run_bound",
                "citation_run_id": "someone-elses-run",
                "bound_citations": [FORGED],
                "citation_gaps": [],
            }
        ),
    )

    html = next((tmp_path / str(created.id)).glob("*.html")).read_text()
    assert response.citation_binding == "unbound"
    assert response.citations == []
    assert stored.configuration["citation_run_id"] is None
    assert "FORGED" not in html


async def test_a_report_with_no_run_keeps_its_citations_unbound_and_lists_none(
    report_db, tmp_path, monkeypatch
):
    _, _, response = await _generate(
        report_db, tmp_path, monkeypatch, _request(workflow_data=_loose())
    )

    assert response.citations == []
    assert response.citation_binding == "unbound"


async def test_a_run_of_another_tenant_is_not_found(report_db, tmp_path, monkeypatch):
    with pytest.raises(HTTPException) as raised:
        await _generate(
            report_db,
            tmp_path,
            monkeypatch,
            _request(run_id=RUN_ID),
            owner=uuid4(),
        )

    assert raised.value.status_code == 404


async def test_a_run_without_a_verifiable_report_fails_the_report_visibly(
    report_db, tmp_path, monkeypatch
):
    _, stored, _ = await _generate(
        report_db, tmp_path, monkeypatch, _request(run_id=RUN_ID), verifiable=False
    )

    assert stored.generation_status == "failed"
