"""Coverage accounting and record fields of the similarity runner, field by field."""

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.services import similarity_service as service
from src.services.scholarly_retrieval import SourceCoverage
from tests.similarity_support import (
    SHARED,
    TEXT,
    candidate,
    factory_fixture,  # noqa: F401
    make_document,
    run_check,
    stub_retrieve,
)


def other_text(tag: str, shared: str = SHARED) -> str:
    return " ".join(f"{tag}{i}" for i in range(30)) + " " + shared + " end-" + tag


async def check_with(
    factory: async_sessionmaker[AsyncSession], retrieve: Any, **options: Any
) -> Any:
    target = await make_document(factory)
    _, view = await run_check(
        factory, target, options={"open_sources": True, **options}, retrieve=retrieve
    )
    return view


async def test_each_coverage_row_comes_from_its_own_provider(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    retrieve, _ = stub_retrieve(
        (candidate("W1", text="a " * 30),),
        openalex=SourceCoverage(
            candidates=7, abstract_only_count=3, queries_sent=5, failures={"timeout": 2}
        ),
        arxiv=SourceCoverage(
            candidates=4, full_text_count=2, queries_sent=3, failures={"redirect": 1}
        ),
    )
    view = await check_with(factory, retrieve)
    rows = view.coverage["sources_searched"]
    assert rows["openalex"] == {
        "candidates": 7,
        "full_text": 2,
        "abstract_only": 3,
        "skipped_budget": 0,
        "failures": {"timeout": 2},
        "queries_sent": 5,
    }
    assert rows["arxiv"] == {
        "candidates": 4,
        "full_text": 2,
        "abstract_only": 0,
        "skipped_budget": 0,
        "failures": {"redirect": 1},
        "queries_sent": 3,
    }


@pytest.mark.parametrize("side", ["openalex", "arxiv"])
async def test_a_deadline_on_either_provider_makes_the_check_partial(
    factory: async_sessionmaker[AsyncSession], side: str
) -> None:
    coverage = {side: SourceCoverage(deadline_reached=True)}
    retrieve, _ = stub_retrieve(**coverage)
    view = await check_with(factory, retrieve)
    assert "deadline_reached" in view.coverage["partial_reasons"]


async def test_dropped_non_ascii_terms_are_reported(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    retrieve, _ = stub_retrieve(openalex=SourceCoverage(terms_dropped_non_ascii=7))
    view = await check_with(factory, retrieve)
    assert view.coverage["terms_dropped_non_ascii"] == 7


async def test_an_oversized_open_source_candidate_is_skipped_and_counted(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "CANDIDATE_CHAR_BUDGET", 100)
    retrieve, _ = stub_retrieve(
        (candidate("W1", text="b " * 400),), openalex=SourceCoverage(candidates=1)
    )
    view = await check_with(factory, retrieve)
    assert view.coverage["sources_searched"]["openalex"]["skipped_budget"] == 1
    assert "skipped_budget" in view.coverage["partial_reasons"]


async def test_candidates_that_exactly_fill_the_budget_are_kept(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = other_text("fit")
    monkeypatch.setattr(service, "CANDIDATE_CHAR_BUDGET", len(body))
    target = await make_document(factory)
    await make_document(factory, text=body)
    _, view = await run_check(factory, target, options={"own_uploads": True})
    row = view.coverage["sources_searched"]["own_upload"]
    assert (row["candidates"], row["skipped_budget"]) == (1, 0)


async def test_counters_add_up_across_several_candidates_of_one_origin(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    bodies = [other_text(f"d{i}") for i in range(4)]
    monkeypatch.setattr(service, "CANDIDATE_CHAR_BUDGET", len(bodies[0]) * 2 + 5)
    target = await make_document(factory)
    for body in bodies:
        await make_document(factory, text=body)
    _, view = await run_check(factory, target, options={"own_uploads": True})
    row = view.coverage["sources_searched"]["own_upload"]
    assert (row["candidates"], row["full_text"], row["skipped_budget"]) == (2, 2, 2)


async def test_a_named_document_is_not_also_compared_as_an_own_upload(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    named = await make_document(factory, text=other_text("nm"))
    _, view = await run_check(
        factory,
        target,
        options={"own_uploads": True, "comparison_document_ids": [str(named)]},
    )
    assert len(view.matches) == 1
    assert view.coverage["sources_searched"]["comparison"]["candidates"] == 1
    assert "own_upload" not in view.coverage["sources_searched"]


async def test_the_tenth_own_upload_is_included_when_eleven_exist(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    target = await make_document(factory)
    for i in range(service.MAX_OWN_UPLOADS + 1):
        await make_document(factory, text=other_text(f"o{i}"))
    _, view = await run_check(factory, target, options={"own_uploads": True})
    row = view.coverage["sources_searched"]["own_upload"]
    assert row["candidates"] == service.MAX_OWN_UPLOADS


async def test_a_missing_target_document_fails_the_run(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    _, view = await run_check(factory, uuid.uuid4())
    assert (view.status, view.status_reason) == ("failed", "internal_error")


async def test_arxiv_origin_needs_both_an_arxiv_id_and_full_text(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    cands = (
        candidate("W1", arxiv_id="2101.00001", title="Upgraded"),
        candidate("W2", arxiv_id="2101.00002", full_text=False, title="AbstractOnly"),
        candidate("W3", title="PlainOpenAlex"),
    )
    retrieve, _ = stub_retrieve(cands, openalex=SourceCoverage(candidates=3))
    view = await check_with(factory, retrieve)
    by_title = {m.source_title: m for m in view.matches}
    assert (by_title["Upgraded"].source_origin, by_title["Upgraded"].source_id) == (
        "arxiv",
        "2101.00001",
    )
    assert by_title["AbstractOnly"].source_origin == "openalex"
    assert by_title["AbstractOnly"].source_id == "W2"
    assert by_title["PlainOpenAlex"].source_origin == "openalex"


async def test_stored_matches_carry_the_source_year_text_kind_and_spans(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    retrieve, _ = stub_retrieve(
        (candidate("W1", year=2015, text="pre " + SHARED),),
        openalex=SourceCoverage(candidates=1),
    )
    view = await check_with(factory, retrieve)
    (match,) = view.matches
    assert (match.source_year, match.source_text_kind) == (2015, "full_text")
    assert match.tokens == 40 and match.section == "body"
    assert (match.src_start, match.src_end) == (len("pre "), len("pre " + SHARED))
    assert match.normalizations_applied == ()


async def test_normalizations_are_stored_sorted(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    curly = "“" + SHARED.replace("shared3 ", "shared3  ") + "”"
    retrieve, _ = stub_retrieve(
        (candidate("W1", text=curly),), openalex=SourceCoverage(candidates=1)
    )
    view = await check_with(factory, retrieve)
    stored = view.matches[0].normalizations_applied
    assert stored == tuple(sorted(stored)) and "whitespace_collapsed" in stored


async def test_the_reference_exclusion_is_recorded_with_its_token_count(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    text = f"{TEXT}\n\nReferences\n\n[1] alpha beta gamma delta epsilon.\n"
    target = await make_document(factory, text=text)
    _, view = await run_check(factory, target)
    region = view.coverage["excluded_regions"][0]
    assert view.coverage["reference_heading_found"] is True
    assert region["kind"] == "references" and region["tokens"] >= 7
    assert region["start"] == text.index("References")


async def test_without_open_sources_nothing_is_retrieved_and_no_web_rows_appear(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    retrieve, calls = stub_retrieve()
    target = await make_document(factory)
    _, view = await run_check(factory, target, retrieve=retrieve)
    assert calls == []
    assert set(view.coverage["sources_searched"]) <= {"own_upload", "comparison"}


async def test_the_retriever_gets_the_budget_the_pacer_and_the_audit_callback(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    seen: dict[str, Any] = {}

    async def retrieve(text: str, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return await stub_retrieve()[0](text, **kwargs)

    view = await check_with(factory, retrieve)
    assert seen["budget"].deadline_s == service.RETRIEVAL_DEADLINE_S
    assert isinstance(seen["pacer"], service.PgHostPacer)
    assert callable(seen["record_egress"]) and view.status == "succeeded"


async def test_matching_runs_under_its_own_deadline(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    real = service.find_verbatim_reuse

    def spy(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "find_verbatim_reuse", spy)
    target = await make_document(factory)
    await run_check(factory, target)
    assert captured["budget"].deadline_s == service.MATCHING_DEADLINE_S
