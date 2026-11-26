"""The similarity runner: sources, coverage, licence gate, status, lifecycle."""

import asyncio
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.memory_encryption import document_cipher
from src.models.db.similarity_check import EgressEvent as EgressRow
from src.models.db.similarity_check import SimilarityCheck, SimilarityMatch
from src.models.db.uploaded_document import UsageLedger
from src.repositories.similarity_repository import (
    SIMILARITY_DEADLINE_S,
    NewCheck,
    SimilarityRepository,
    active_similarity_checks,
)
from src.services import similarity_service as service
from src.services.document_jobs import JobLimits, admit
from src.services.scholarly_retrieval import EgressEvent, SourceCoverage
from src.services.text_reuse.report import NOT_COVERED, render_report
from src.services.text_reuse.types import DECLARED_AUTHOR_NOTE
from tests.similarity_support import (
    ORG_A,
    ORG_B,
    SHARED,
    TEXT,
    USER_A,
    USER_B,
    candidate,
    count,
    factory_fixture,  # noqa: F401
    make_document,
    read_check,
    run_check,
    start_check,
    stub_retrieve,
)

BIDI_MARK = chr(0x202E)


def other_text(tag: str, shared: str = SHARED) -> str:
    return " ".join(f"{tag}{i}" for i in range(30)) + " " + shared + " end-" + tag


# ---- own uploads and comparison documents ------------------------------------


async def test_own_uploads_sharing_a_passage_match_with_provenance(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    other = await make_document(factory, text=other_text("zz"), filename="notes.txt")
    _, view = await run_check(factory, target, options={"own_uploads": True})
    assert view.status == "succeeded"
    (match,) = view.matches
    assert (match.kind, match.from_user_upload) == ("verbatim", True)
    assert match.source_origin == "own_upload" and match.source_document_id == other
    assert match.tokens == 40 and match.source_title == "notes.txt"


async def test_a_named_comparison_document_is_recorded_as_comparison(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    paper = await make_document(
        factory, text=other_text("qq"), filename="third-party.pdf"
    )
    _, view = await run_check(
        factory, target, options={"comparison_document_ids": [str(paper)]}
    )
    (match,) = view.matches
    assert match.source_origin == "comparison" and match.from_user_upload is True
    report = render_report(view, TEXT)
    assert "matches your uploaded document third-party.pdf" in report
    assert not re.search(r"self[- ]?reuse", report, re.IGNORECASE)


async def test_another_users_documents_are_never_compared(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    await make_document(factory, org=ORG_B, user=USER_B, text=other_text("bb"))
    await make_document(factory, user=uuid.uuid4(), text=other_text("cc"))
    _, view = await run_check(factory, target, options={"own_uploads": True})
    assert view.matches == ()


async def test_expired_and_deleted_documents_are_not_compared(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    expired = await make_document(
        factory, text=other_text("ex"), expires_in=timedelta(seconds=-1)
    )
    _, view = await run_check(
        factory,
        target,
        options={"own_uploads": True, "comparison_document_ids": [str(expired)]},
    )
    assert view.matches == ()
    assert view.coverage["unavailable_sources"] == 1
    assert view.status == "partial"


async def test_own_uploads_are_off_unless_requested(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    await make_document(factory, text=other_text("zz"))
    _, view = await run_check(factory, target)
    assert view.matches == ()


async def test_at_most_ten_other_uploads_are_compared(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    for i in range(12):
        await make_document(factory, text=other_text(f"d{i}"))
    _, view = await run_check(factory, target, options={"own_uploads": True})
    assert view.coverage["sources_searched"]["own_upload"]["candidates"] == 10


async def test_candidates_beyond_the_char_budget_are_skipped_and_recorded(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    target = await make_document(factory)
    await make_document(factory, text=other_text("aa"))
    await make_document(factory, text=other_text("bb"))
    monkeypatch.setattr(service, "CANDIDATE_CHAR_BUDGET", len(other_text("aa")) + 10)
    _, view = await run_check(factory, target, options={"own_uploads": True})
    row = view.coverage["sources_searched"]["own_upload"]
    assert row["skipped_budget"] == 1 and row["candidates"] == 1
    assert view.status == "partial"
    assert "skipped_budget" in view.coverage["partial_reasons"]


async def test_matcher_budget_exhaustion_also_makes_the_check_partial(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.services.text_reuse.types import ReuseBudget

    real = service.find_verbatim_reuse

    def tight(*args: Any, **kwargs: Any) -> Any:
        return real(*args, **{**kwargs, "budget": ReuseBudget(max_total_chars=1)})

    monkeypatch.setattr(service, "find_verbatim_reuse", tight)
    target = await make_document(factory)
    await make_document(factory, text=other_text("aa"))
    await make_document(factory, text=other_text("bb"))
    _, view = await run_check(factory, target, options={"own_uploads": True})
    assert view.status == "partial"
    assert "budget_exhausted" in view.coverage["partial_reasons"]


# ---- open sources ------------------------------------------------------------


async def test_coverage_equals_the_stubbed_ground_truth(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    cands = (
        candidate("W1", full_text=True, text=SHARED),
        candidate("W2", full_text=True, text="x " * 30),
        candidate("W3", full_text=False, text="y " * 30),
    )
    retrieve, calls = stub_retrieve(
        cands,
        openalex=SourceCoverage(
            candidates=3,
            abstract_only_count=1,
            queries_sent=4,
            failures={"http_429": 1},
        ),
        arxiv=SourceCoverage(full_text_count=2, candidates=2, queries_sent=2),
    )
    _, view = await run_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    row = view.coverage["sources_searched"]["openalex"]
    assert {
        k: row[k] for k in ("candidates", "full_text", "abstract_only", "failures")
    } == {
        "candidates": 3,
        "full_text": 2,
        "abstract_only": 1,
        "failures": {"http_429": 1},
    }
    assert row["queries_sent"] == 4 and len(calls) == 1
    assert (
        view.status == "partial"
        and "failed_sources" in view.coverage["partial_reasons"]
    )


async def test_a_passage_the_sources_never_returned_gives_a_zero_match_report(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    retrieve, _ = stub_retrieve(
        (candidate("W1", text="unrelated " * 50),),
        openalex=SourceCoverage(candidates=1),
    )
    _, view = await run_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    report = render_report(view, TEXT)
    assert "No matching text was found in the 1 sources searched." in report
    for item in NOT_COVERED:
        assert item in report


async def test_the_licence_gate_decides_whether_an_excerpt_is_stored(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    cands = (
        candidate("W1", licence="cc-by", title="Licensed"),
        candidate("W2", licence=None, title="Undeclared"),
        candidate("W3", licence="all-rights-reserved", title="Closed"),
    )
    retrieve, _ = stub_retrieve(cands, openalex=SourceCoverage(candidates=3))
    _, view = await run_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    excerpts = {m.source_title: m.source_excerpt for m in view.matches}
    assert excerpts["Licensed"] == SHARED
    assert excerpts["Undeclared"] is None and excerpts["Closed"] is None
    report = render_report(view, TEXT)
    assert 'source licence "undeclared" does not permit display' in report
    assert "source licence all-rights-reserved does not permit display" in report


async def test_an_own_upload_always_stores_its_excerpt(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    await make_document(factory, text=other_text("zz"))
    _, view = await run_check(factory, target, options={"own_uploads": True})
    assert view.matches[0].source_excerpt == SHARED


async def test_source_titles_are_sanitized_and_capped(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    hostile = (
        "![x](https://evil.example/?q=SENTINEL)<script>alert(1)</script>"
        + BIDI_MARK
        + "BIDI"
        + "z" * 500
    )
    target = await make_document(factory)
    retrieve, _ = stub_retrieve(
        (candidate("W1", title=hostile[:300]),), openalex=SourceCoverage(candidates=1)
    )
    _, view = await run_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    title = view.matches[0].source_title
    assert len(title) <= 200
    for forbidden in ("http", "<script", BIDI_MARK, "SENTINEL"):
        assert forbidden not in title
    report = render_report(view, TEXT)
    assert set(re.findall(r"https?://\S+", report)) == {"https://openalex.org/W1"}


async def test_declared_authors_note_the_overlap_but_never_change_provenance_or_order(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory, declared_authors=["Doe, Jane"])
    retrieve, _ = stub_retrieve(
        (candidate("W1", authors=("Jane Doe",)),), openalex=SourceCoverage(candidates=1)
    )
    _, view = await run_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    (match,) = view.matches
    assert match.declared_author_overlap is True and match.from_user_upload is False
    assert DECLARED_AUTHOR_NOTE in render_report(view, TEXT)


async def test_egress_events_are_persisted_without_terms_urls_or_keys(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    events = tuple(
        EgressEvent("api.openalex.org", "search", 3, 200, 100 * i, 20) for i in range(3)
    )
    retrieve, _ = stub_retrieve(events=events)
    check_id, view = await run_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    async with factory() as session:
        rows = (await session.execute(select(EgressRow))).scalars().all()
    assert len(rows) == 3 and {r.check_id for r in rows} == {check_id}
    blob = repr([vars(r) for r in rows])
    for secret in ("lead", "shared", "http", "openalex.org/"):
        assert secret not in blob.replace("api.openalex.org", "")
    assert view.status == "succeeded"


# ---- status ------------------------------------------------------------------


async def test_a_retrieval_deadline_is_partial_with_the_reason_in_coverage(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    retrieve, _ = stub_retrieve(openalex=SourceCoverage(deadline_reached=True))
    _, view = await run_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    assert view.status == "partial" and view.status_reason is None
    assert "deadline_reached" in view.coverage["partial_reasons"]


async def test_semantic_matching_is_recorded_as_unavailable_and_partial(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    _, view = await run_check(factory, target, options={"semantic": True})
    run = view.coverage["comparisons_run"]
    assert run["paraphrase"] == "not_run (unavailable)"
    assert run["translation"] == "not_run (unavailable)"
    assert view.status == "partial"


async def test_a_local_check_with_nothing_wrong_succeeds(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    _, view = await run_check(factory, target)
    assert view.status == "succeeded" and view.coverage["partial_reasons"] == []


async def test_a_matcher_that_raises_fails_the_check(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("matcher failed")

    monkeypatch.setattr(service, "find_verbatim_reuse", boom)
    target = await make_document(factory)
    _, view = await run_check(factory, target)
    assert (view.status, view.status_reason) == ("failed", "internal_error")


async def test_the_runner_sets_the_tenant_context_for_every_session(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    async def spy(session: AsyncSession, organization_id: str) -> None:
        seen.append(organization_id)

    monkeypatch.setattr(service, "set_postgres_tenant_context", spy)
    target = await make_document(factory)
    await run_check(factory, target)
    assert seen and set(seen) == {str(ORG_A)}


# ---- lifecycle wiring ----------------------------------------------------------


async def test_a_stale_running_check_reads_as_failed(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    old = datetime.now(UTC) - timedelta(seconds=SIMILARITY_DEADLINE_S + 600)
    async with factory() as session:
        row = await SimilarityRepository(session, document_cipher()).create(
            NewCheck(target, ORG_A, USER_A, {}, {})
        )
        row.created_at, row.status = old, "running"
        await session.commit()
        check_id = row.id
    view = await read_check(factory, check_id)
    assert (view.status, view.status_reason) == ("failed", "deadline_exceeded")


async def test_deleting_the_check_mid_run_discards_the_result_without_error(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    await make_document(factory, text=other_text("zz"))
    entered, release = asyncio.Event(), asyncio.Event()

    def gated(work: Any) -> Any:
        async def run() -> Any:
            entered.set()
            await release.wait()
            return await work()

        return run

    check_id, go = await start_check(
        factory, target, options={"own_uploads": True}, work_wrapper=gated
    )
    task = asyncio.create_task(go())
    await entered.wait()
    async with factory() as session:
        assert await SimilarityRepository(session, document_cipher()).delete(
            check_id, organization_id=ORG_A, user_id=USER_A
        )
        await session.commit()
    release.set()
    await task
    assert await count(factory, SimilarityMatch) == 0
    assert await count(factory, SimilarityCheck) == 0


async def test_a_run_that_never_started_releases_its_ledger_row(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    async with factory() as session:
        ledger_id = await admit(
            session,
            kind="similarity_check",
            organization_id=ORG_A,
            user_id=USER_A,
            units=1,
            limits=JobLimits(),
            count_active=active_similarity_checks,
        )
        row = await SimilarityRepository(session, document_cipher()).create(
            NewCheck(target, ORG_A, USER_A, {}, {}, ledger_id=ledger_id)
        )
        row.created_at = datetime.now(UTC) - timedelta(
            seconds=SIMILARITY_DEADLINE_S + 600
        )
        await session.commit()
        check_id = row.id
    assert await count(factory, UsageLedger) == 1
    view = await read_check(factory, check_id)
    assert view.status == "failed"
    assert await count(factory, UsageLedger) == 0


async def test_a_stored_excerpt_is_capped(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    long_passage = " ".join(f"long{i}" for i in range(600))
    target = await make_document(factory, text=f"{TEXT} {long_passage}")
    await make_document(factory, text=other_text("zz", long_passage))
    _, view = await run_check(factory, target, options={"own_uploads": True})
    match = max(view.matches, key=lambda m: m.tokens)
    assert match.tokens >= 600
    assert len(match.source_excerpt or "") == service.MAX_EXCERPT_CHARS


async def test_a_document_is_never_compared_with_itself(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    _, view = await run_check(
        factory, target, options={"comparison_document_ids": [str(target)]}
    )
    assert view.matches == ()


async def test_a_hostile_licence_is_bounded_before_it_is_stored(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    retrieve, _ = stub_retrieve(
        (candidate("W1", licence="x" * 5000),), openalex=SourceCoverage(candidates=1)
    )
    _, view = await run_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    assert len(view.matches[0].source_licence["identifier"]) == 64
