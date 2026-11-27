"""Per-source recording outcomes and the bibliography kept with each snapshot."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.acquisition.search_evidence import SearchEvidenceRecorder
from src.core.acquisition.search_result import (
    RetrievedSource,
    SourceDisposition,
    SourceRecordingOutcome,
)
from src.core.acquisition.service import retrieved_source_evidence_id
from src.core.acquisition.snapshots import FilesystemSnapshotStore
from src.core.contracts.provenance import ToolInvocation, ToolInvocationStatus
from src.core.contracts.trust import TrustClassification

NOW = datetime(2026, 11, 26, tzinfo=UTC)
ORG = "00000000-0000-0000-0000-0000000000aa"
PAPER = {
    "id": "2301.00001",
    "title": "GNNs",
    "abstract": "We forecast inflation.",
    "authors": ["Ada", "Bo"],
    "year": 2023,
    "url": "https://arxiv.org/abs/2301.00001",
    "doi": "10.1/x",
    "source": "arxiv",
}


class _Artifacts:
    def __init__(self) -> None:
        self.created: list[Any] = []

    async def create_artifact(self, artifact: Any, *, organization_id: Any) -> Any:
        self.created.append(artifact)
        return artifact


class _Evidence:
    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}

    async def get_evidence(self, evidence_id: str, *, organization_id: Any) -> Any:
        return self.rows.get(evidence_id)

    async def record_evidence(self, evidence: Any, *, organization_id: Any) -> Any:
        self.rows[evidence.evidence_id] = evidence
        return evidence


class _Session:
    # tenant_session registers its per-transaction hook on the sync session.
    sync_session = Session()

    async def commit(self) -> None:
        return None


class _Recorder(SearchEvidenceRecorder):
    """The real recorder and service over in-memory repositories."""

    def __init__(self, store: FilesystemSnapshotStore) -> None:
        self.artifacts = _Artifacts()
        self.evidence = _Evidence()
        self.fail_for: set[str] = set()

        @asynccontextmanager
        async def factory() -> AsyncIterator[_Session]:
            yield _Session()

        super().__init__(session_factory=factory, store=store, clock=lambda: NOW)  # type: ignore[arg-type]

    def _service(self, session: Any) -> Any:
        service = super()._service(session)
        service._artifacts = self.artifacts
        service._evidence = self.evidence
        return service

    async def _exists(self, invocation, organization_id, uri, content):  # type: ignore[no-untyped-def]
        evidence_id = retrieved_source_evidence_id(invocation, uri, content)
        return evidence_id in self.evidence.rows

    async def _record(self, invocation, organization_id, uri, content, metadata):  # type: ignore[no-untyped-def]
        if uri in self.fail_for:
            raise RuntimeError("write failed")
        return await super()._record(
            invocation, organization_id, uri, content, metadata
        )


def _invocation(sources: list[dict[str, Any]]) -> ToolInvocation:
    return ToolInvocation(
        tool_invocation_id="inv-1",
        run_id="run-1",
        task_id="task-1",
        attempt_id="attempt-1",
        tool_name="mcp.academic_search",
        tool_version="1.0.0",
        status=ToolInvocationStatus.SUCCEEDED,
        capability_scope="mcp.academic_search",
        idempotency_key="key-1",
        input={},
        input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
        output={"sources": sources},
        output_trust=TrustClassification.EXTERNAL_UNTRUSTED,
        requested_at=NOW,
        completed_at=NOW,
    )


@pytest.fixture(name="recorder")
def recorder_fixture(tmp_path: Path) -> _Recorder:
    return _Recorder(FilesystemSnapshotStore(root=tmp_path / "snap"))


def test_a_retrieved_source_normalizes_identity_without_inventing_any() -> None:
    source = RetrievedSource.from_record({**PAPER, "year": "2023"})

    assert (source.provider, source.provider_id) == ("arxiv", "2301.00001")
    assert source.year == 2023
    assert source.uri() == "https://arxiv.org/abs/2301.00001"
    assert RetrievedSource.from_record({"title": "t", "year": "n.d."}).year is None
    assert RetrievedSource.from_record({"title": "t", "year": True}).year is None
    assert RetrievedSource.from_record({"title": "t"}).bibliography() == {"title": "t"}


async def test_every_source_gets_a_disposition_and_recorded_ones_an_id(
    recorder: _Recorder,
) -> None:
    sources = [PAPER, {"title": "no abstract"}, {"title": "t", "abstract": "a"}]

    summary = await recorder(_invocation(sources), ORG)

    dispositions = [o["disposition"] for o in summary["sources"]]
    assert dispositions == ["recorded", "skipped", "skipped"]
    assert [o["reason"] for o in summary["sources"][1:]] == ["no_text", "no_source_uri"]
    assert summary["sources"][0]["evidence_id"] == summary["recorded"][0]
    assert summary["sources"][0]["doi"] == "10.1/x"
    assert summary["skipped"] == 2  # aggregate fields keep their meaning
    for outcome in summary["sources"]:
        SourceRecordingOutcome.model_validate(outcome)


async def test_a_replay_returns_the_same_evidence_id(recorder: _Recorder) -> None:
    first = await recorder(_invocation([PAPER]), ORG)
    again = await recorder(_invocation([PAPER]), ORG)

    assert again["recorded"] == []
    assert again["already_recorded"] == 1
    (outcome,) = again["sources"]
    assert outcome["disposition"] == SourceDisposition.ALREADY_RECORDED
    assert outcome["evidence_id"] == first["recorded"][0]
    assert len(recorder.artifacts.created) == 1


async def test_a_failed_write_is_reported_against_its_source_only(
    recorder: _Recorder,
) -> None:
    other = {**PAPER, "id": "2", "url": "https://arxiv.org/abs/2"}
    recorder.fail_for = {PAPER["url"]}

    summary = await recorder(_invocation([PAPER, other]), ORG)

    first, second = summary["sources"]
    assert (first["disposition"], first["evidence_id"], first["reason"]) == (
        "failed",
        None,
        "RuntimeError",
    )
    assert second["disposition"] == "recorded"
    assert summary["failed"] == [{"source_uri": PAPER["url"], "reason": "RuntimeError"}]


async def test_the_snapshot_artifact_keeps_the_bibliography(
    recorder: _Recorder,
) -> None:
    await recorder(_invocation([PAPER]), ORG)

    (artifact,) = recorder.artifacts.created
    assert artifact.metadata["bibliography"] == {
        "provider": "arxiv",
        "provider_id": "2301.00001",
        "title": "GNNs",
        "authors": ("Ada", "Bo"),  # the contract model freezes lists
        "year": 2023,
        "url": "https://arxiv.org/abs/2301.00001",
        "doi": "10.1/x",
    }
    # system-owned keys are not overridable from provider data
    assert artifact.metadata["source_uri"] == PAPER["url"]


async def test_losing_a_concurrent_write_race_still_reports_the_stable_id(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await recorder(_invocation([PAPER]), ORG)

    async def _lost_race(*_: Any) -> None:
        raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(recorder, "_record", _lost_race)
    again = await recorder(_invocation([PAPER]), ORG)

    (outcome,) = again["sources"]
    assert outcome["disposition"] == "already_recorded"
    assert outcome["evidence_id"] == first["recorded"][0]
