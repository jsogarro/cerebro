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


async def test_the_report_page_keeps_the_most_severe_matches_when_it_is_cut(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    from src.repositories.similarity_repository import MatchRecord

    target = await make_document(factory)
    check_id, _ = await run_check(factory, target)

    def record(attribution: str, tokens: int, source_id: str) -> MatchRecord:
        return MatchRecord(
            "verbatim",
            attribution,
            False,
            0,
            5,
            tokens,
            "openalex",
            None,
            source_id,
            f"t-{source_id}",
            None,
            {},
            "full_text",
            0,
            5,
            None,
        )

    async with factory() as session:
        repository = SimilarityRepository(session, document_cipher())
        await repository.add_matches(
            check_id=check_id,
            organization_id=ORG_A,
            user_id=USER_A,
            matches=[
                record("quoted_and_cited", 90, "W1"),
                record("cited_unquoted", 80, "W2"),
                record("unattributed", 12, "W3"),
            ],
        )
        await session.commit()
        view = await repository.get(
            check_id, organization_id=ORG_A, user_id=USER_A, limit=1
        )
    assert view is not None and [m.source_id for m in view.matches] == ["W3"]
    assert view.match_total == 3


async def test_matches_page_without_gaps_or_repeats_when_their_keys_tie(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    from src.repositories.similarity_repository import MatchRecord

    target = await make_document(factory)
    check_id, _ = await run_check(factory, target)
    records = [
        MatchRecord(
            "verbatim",
            "unattributed",
            False,
            0,
            5,
            12,
            "openalex",
            None,
            f"W{i}",
            f"title-{i}",
            None,
            {},
            "full_text",
            0,
            5,
            None,
        )
        for i in range(7)
    ]
    async with factory() as session:
        repository = SimilarityRepository(session, document_cipher())
        await repository.add_matches(
            check_id=check_id, organization_id=ORG_A, user_id=USER_A, matches=records
        )
        await session.commit()
        pages = [
            await repository.get(
                check_id, organization_id=ORG_A, user_id=USER_A, offset=o, limit=3
            )
            for o in (0, 3, 6)
        ]
    ids = [m.id for page in pages if page for m in page.matches]
    assert len(ids) == 7 and len(set(ids)) == 7


async def test_a_pacer_whose_commit_fails_does_not_fail_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.exc import OperationalError

    class Session:
        async def __aenter__(self) -> "Session":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def scalar(self, *args: object) -> bool:
            return True

        async def commit(self) -> None:
            raise OperationalError("COMMIT", {}, Exception("connection lost"))

    pacer = service.PgHostPacer(Session, poll_s=0.01)  # type: ignore[arg-type]
    async with pacer.slot("arxiv.org"):
        ran = True
    assert ran


async def test_a_check_deleted_while_being_read_reads_as_missing(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import InvalidRequestError

    target = await make_document(factory)
    stale = datetime.now(UTC) - timedelta(seconds=SIMILARITY_DEADLINE_S + 600)
    async with factory() as session:
        row = await SimilarityRepository(session, document_cipher()).create(
            NewCheck(target, ORG_A, USER_A, {}, {})
        )
        row.created_at = stale
        await session.commit()
        check_id = row.id

    async def gone(*args: object, **kwargs: object) -> None:
        raise InvalidRequestError("row vanished")

    async with factory() as session:
        monkeypatch.setattr(session, "refresh", gone)
        view = await SimilarityRepository(session, document_cipher()).get(
            check_id, organization_id=ORG_A, user_id=USER_A
        )
    assert view is None


async def test_deleting_a_check_locks_its_row(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[object] = []
    real = SimilarityRepository._owned

    def spy(self: SimilarityRepository, query: Any, *args: Any) -> Any:
        seen.append(getattr(query, "_for_update_arg", None))
        return real(self, query, *args)

    monkeypatch.setattr(SimilarityRepository, "_owned", spy)
    target = await make_document(factory)
    check_id, _ = await run_check(factory, target)
    async with factory() as session:
        await SimilarityRepository(session, document_cipher()).delete(
            check_id, organization_id=ORG_A, user_id=USER_A
        )
        await session.commit()
    assert seen[-1] is not None


async def test_a_stale_queued_check_also_releases_its_open_source_ledger_row(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    async with factory() as session:
        primary = await admit(
            session,
            kind="similarity_check",
            organization_id=ORG_A,
            user_id=USER_A,
            units=1,
            limits=JobLimits(),
            count_active=active_similarity_checks,
        )
        secondary = await admit(
            session,
            kind="similarity_open_sources",
            organization_id=ORG_A,
            user_id=USER_A,
            units=1,
            limits=JobLimits(),
            count_active=active_similarity_checks,
        )
        row = await SimilarityRepository(session, document_cipher()).create(
            NewCheck(
                target,
                ORG_A,
                USER_A,
                {},
                {},
                ledger_id=primary,
                open_sources_ledger_id=secondary,
            )
        )
        row.created_at = datetime.now(UTC) - timedelta(
            seconds=SIMILARITY_DEADLINE_S + 600
        )
        await session.commit()
        check_id = row.id
    assert await count(factory, UsageLedger) == 2
    await read_check(factory, check_id)
    assert await count(factory, UsageLedger) == 0


async def test_an_excerpt_of_an_expired_source_document_is_no_longer_served(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    from src.models.db.uploaded_document import UploadedDocument

    target = await make_document(factory)
    source = await make_document(factory, text=other_text("zz"), filename="old.txt")
    check_id, view = await run_check(
        factory, target, options={"comparison_document_ids": [str(source)]}
    )
    assert view.matches[0].source_excerpt == SHARED
    async with factory() as session:
        row = await session.get(UploadedDocument, source)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    after = await read_check(factory, check_id)
    assert after.matches[0].source_excerpt is None
    assert "old.txt" not in after.matches[0].source_title


async def test_audit_survives_a_deleted_check_and_the_run_stops(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import IntegrityError

    real = SimilarityRepository.add_egress
    calls: list[uuid.UUID | None] = []

    async def fk_fails_for_a_linked_row(
        self: SimilarityRepository, event: Any, *, check_id: Any, **owner: Any
    ) -> None:
        calls.append(check_id)
        if check_id is not None:
            raise IntegrityError("INSERT", {}, Exception("check gone"))
        await real(self, event, check_id=None, **owner)

    monkeypatch.setattr(SimilarityRepository, "add_egress", fk_fails_for_a_linked_row)
    await seed_user(factory)
    sent = []

    async def retrieve(text: str, **kwargs: Any) -> Any:
        for _ in range(3):
            sent.append(1)
            await kwargs["record_egress"](EgressEvent("arxiv.org", "pdf", 0, 200, 1, 1))
        return await stub_retrieve()[0](text, **kwargs)

    target = await make_document(factory)
    check_id, go = await start_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    with pytest.raises(asyncio.CancelledError):
        await go()
    view = await read_check(factory, check_id)
    async with factory() as session:
        rows = (await session.execute(select(EgressRow))).scalars().all()
    assert len(sent) == 1
    assert [r.check_id for r in rows] == [None]
    assert view.status == "failed"


async def test_a_switched_off_feature_stops_a_queued_check_from_going_out(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import settings

    retrieve, calls = stub_retrieve()
    target = await make_document(factory)
    monkeypatch.setattr(settings, "SIMILARITY_OPEN_SOURCES_ENABLED", False)
    _, view = await run_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    assert calls == []
    assert view.coverage["sources_searched"]["openalex"]["failures"] == {
        "open_sources_disabled": 1
    }
    assert view.status == "partial"


async def test_a_check_keeps_at_most_the_total_match_cap(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "MAX_MATCHES_PER_CHECK", 3)
    target = await make_document(factory, text=" ".join(f"w{i}" for i in range(400)))
    chunks = [" ".join(f"w{i}" for i in range(j, j + 20)) for j in range(0, 400, 40)]
    retrieve, _ = stub_retrieve(
        tuple(candidate(f"W{i}", text=c) for i, c in enumerate(chunks)),
        openalex=SourceCoverage(candidates=len(chunks)),
    )
    _, view = await run_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    assert len(view.matches) == 3
    assert "too_many_matches" in view.coverage["partial_reasons"]


async def test_a_queued_run_that_fails_releases_every_ledger_row_it_reserved(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    from src.models.db.similarity_check import SIMILARITY_CHECKS
    from src.services.document_jobs import run_job

    target = await make_document(factory)
    old = datetime.now(UTC) - timedelta(seconds=SIMILARITY_DEADLINE_S + 60)
    async with factory() as session:
        first = await admit(
            session,
            kind="similarity_check",
            organization_id=ORG_A,
            user_id=USER_A,
            units=1,
            limits=JobLimits(),
            count_active=active_similarity_checks,
        )
        second = await admit(
            session,
            kind="similarity_open_sources",
            organization_id=ORG_A,
            user_id=USER_A,
            units=1,
            limits=JobLimits(),
            count_active=active_similarity_checks,
        )
        row = await SimilarityRepository(session, document_cipher()).create(
            NewCheck(
                target,
                ORG_A,
                USER_A,
                {},
                {},
                ledger_id=first,
                open_sources_ledger_id=second,
            )
        )
        row.created_at = old
        await session.commit()
        check_id = row.id
    ran: list[int] = []

    async def never_runs() -> None:
        ran.append(1)

    async def persist(session: AsyncSession, result: Any) -> Any:
        return "succeeded", {}

    await run_job(
        table=SIMILARITY_CHECKS,
        job_id=check_id,
        organization_id=ORG_A,
        created_at=old,
        deadline_s=SIMILARITY_DEADLINE_S,
        session_factory=factory,
        semaphore=asyncio.Semaphore(1),
        work=never_runs,
        persist=persist,
    )
    assert ran == []
    assert await count(factory, UsageLedger) == 0


async def test_open_sources_need_the_terms_date_at_run_time_too(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import settings

    retrieve, calls = stub_retrieve()
    target = await make_document(factory)
    monkeypatch.setattr(settings, "PROVIDER_TERMS_VERIFIED_ON", "")
    _, view = await run_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    assert calls == []
    assert view.coverage["sources_searched"]["openalex"]["failures"] == {
        "open_sources_disabled": 1
    }


async def test_the_total_cap_keeps_the_longest_matches_not_the_first(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "MAX_MATCHES_PER_CHECK", 1)
    short = " ".join(f"s{i}" for i in range(13))
    long = " ".join(f"l{i}" for i in range(30))
    target = await make_document(factory, text=f"{short} filler-word-here {long}")
    retrieve, _ = stub_retrieve(
        (candidate("W1", text=short), candidate("W2", text=long)),
        openalex=SourceCoverage(candidates=2),
    )
    _, view = await run_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    assert [m.tokens for m in view.matches] == [30]


async def test_a_source_deleted_mid_run_drops_only_its_matches(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    from src.models.db.uploaded_document import UploadedDocument

    target = await make_document(factory)
    gone = await make_document(factory, text=other_text("gg"), filename="gone.txt")
    kept = await make_document(factory, text=other_text("kk"), filename="kept.txt")

    def delete_after(work: Any) -> Any:
        async def run() -> Any:
            result = await work()
            async with factory() as session:
                row = await session.get(UploadedDocument, gone)
                assert row is not None
                await session.delete(row)
                await session.commit()
            return result

        return run

    _, view = await run_check(
        factory,
        target,
        options={"comparison_document_ids": [str(gone), str(kept)]},
        work_wrapper=delete_after,
    )
    assert [m.source_title for m in view.matches] == ["kept.txt"]
    assert view.status == "partial"
    assert "source_unavailable" in view.coverage["partial_reasons"]


async def test_an_audit_row_that_cannot_be_written_stops_the_run_and_logs_the_type(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    import structlog
    from sqlalchemy.exc import IntegrityError

    async def always(self: object, *args: object, **kwargs: object) -> None:
        raise IntegrityError("INSERT", {}, Exception("nope"))

    monkeypatch.setattr(SimilarityRepository, "add_egress", always)
    await seed_user(factory)
    retrieve, _ = stub_retrieve(events=(EgressEvent("arxiv.org", "pdf", 0, 200, 1, 1),))
    target = await make_document(factory)
    _check_id, go = await start_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    with (
        structlog.testing.capture_logs() as captured,
        pytest.raises(asyncio.CancelledError),
    ):
        await go()
    assert any(
        e.get("event") == "egress_audit_lost"
        and e.get("error_type") == "IntegrityError"
        for e in captured
    )


async def test_no_audit_row_is_recreated_for_an_erased_user(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import IntegrityError

    real = SimilarityRepository.add_egress

    async def check_gone(
        self: SimilarityRepository, event: Any, *, check_id: Any, **owner: Any
    ) -> None:
        if check_id is not None:
            raise IntegrityError("INSERT", {}, Exception("check gone"))
        await real(self, event, check_id=None, **owner)

    monkeypatch.setattr(SimilarityRepository, "add_egress", check_gone)
    retrieve, _ = stub_retrieve(events=(EgressEvent("arxiv.org", "pdf", 0, 200, 1, 1),))
    target = await make_document(factory)
    _, go = await start_check(
        factory, target, options={"open_sources": True}, retrieve=retrieve
    )
    with pytest.raises(asyncio.CancelledError):
        await go()
    async with factory() as session:
        rows = (await session.execute(select(EgressRow))).scalars().all()
    assert rows == []


async def seed_user(factory: async_sessionmaker[AsyncSession]) -> None:
    from src.models.db.user import User

    async with factory() as session:
        session.add(
            User(
                id=USER_A,
                email="a@example.com",
                username="user-a",
                hashed_password="x",
            )
        )
        await session.commit()
