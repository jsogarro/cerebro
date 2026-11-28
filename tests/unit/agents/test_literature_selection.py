"""Deterministic deduplication, ranking, and bounded excerpt selection tests."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from src.core.acquisition.resolution import resolve
from src.core.acquisition.search_result import (
    SelectionReason,
    canonical_doi,
    canonical_url,
    select_bounded_sources,
    source_text,
)
from src.core.budget import BudgetLedger, estimate_prompt_tokens
from src.core.contracts import ExecutionBudget

FIXTURES_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "literature_retrieval_fixtures.json"
)


def _load_cases() -> list[dict[str, Any]]:
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    return list(data["cases"])


def test_canonical_doi_and_url_normalization() -> None:
    assert canonical_doi("10.1000/ABC-1") == "10.1000/abc-1"
    assert canonical_doi("https://doi.org/10.1000/ABC-1") == "10.1000/abc-1"
    assert canonical_doi("http://dx.doi.org/10.1000/ABC-1") == "10.1000/abc-1"
    assert canonical_doi("doi:10.1000/ABC-1") == "10.1000/abc-1"
    assert canonical_doi("not-a-doi") is None

    assert (
        canonical_url("http://ArXiv.org/abs/2401.00001/#top")
        == "https://arxiv.org/abs/2401.00001"
    )
    assert canonical_url("https://user:secret@example.org/p") is None


def test_duplicate_doi_collapses_once_and_retains_provenance() -> None:
    sources = [
        {
            "id": "2401.00001",
            "source": "arxiv",
            "title": "Inflation GNNs",
            "abstract": "Brief.",
            "url": "https://arxiv.org/abs/2401.00001",
            "doi": "10.1000/XYZ",
        },
        {
            "id": "s2-1",
            "source": "semantic_scholar",
            "title": "Inflation GNNs",
            "abstract": (
                "Detailed abstract explaining how graph neural networks cut "
                "inflation forecast error by 14 percent."
            ),
            "url": "https://www.semanticscholar.org/paper/s2-1",
            "doi": "https://doi.org/10.1000/xyz",
        },
    ]

    result = select_bounded_sources(
        sources,
        query="inflation forecast error",
        evidence_budget_tokens=4000,
    )

    assert len(result.eligible_sources) == 1
    assert len(result.prompt_entries) == 1
    canonical = result.eligible_sources[0]
    assert canonical["ref"] == "doi:10.1000/xyz"
    assert "14 percent" in canonical["abstract"]
    providers = [entry.get("provider") for entry in canonical["provenance"]]
    assert set(providers) == {"arxiv", "semantic_scholar"}
    assert result.diagnostics.reason_counts[SelectionReason.DUPLICATE_DOI] == 1
    assert result.diagnostics.omitted_count == 1


def test_distinct_dois_with_shared_url_are_never_merged() -> None:
    sources = [
        {
            "title": "Paper One",
            "abstract": "First study on solar cells.",
            "url": "https://journal.example.org/issue/1",
            "doi": "10.1000/paper-1",
        },
        {
            "title": "Paper Two",
            "abstract": "Second study on solar cells.",
            "url": "https://journal.example.org/issue/1",
            "doi": "10.1000/paper-2",
        },
    ]

    result = select_bounded_sources(
        sources, query="solar cells", evidence_budget_tokens=4000
    )

    assert len(result.eligible_sources) == 2


def test_answer_span_after_char_200_survives_with_resolvable_locator_and_digest() -> (
    None
):
    filler = "Introductory context about unrelated benchmark setup. " * 12
    assert len(filler) > 400
    answer = "Our cryogenic qubit coherence reached 942 microseconds at 12 mK."
    source = {
        "title": "Superconducting Qubit Coherence",
        "abstract": f"{filler}{answer} Further closing remarks follow.",
        "url": "https://arxiv.org/abs/2406.00001",
        "doi": "10.1000/qubit-942",
    }

    result = select_bounded_sources(
        [source],
        query="cryogenic qubit coherence microseconds",
        evidence_budget_tokens=120,
    )

    assert len(result.prompt_entries) == 1
    entry = result.prompt_entries[0]
    assert entry.truncated is True
    assert answer in entry.excerpt
    assert entry.locator is not None
    snapshot_bytes = source_text(source).encode("utf-8")
    resolved_bytes = resolve(entry.locator, snapshot_bytes)
    assert resolved_bytes.decode("utf-8") == entry.excerpt
    assert entry.excerpt_sha256 == hashlib.sha256(resolved_bytes).hexdigest()
    assert estimate_prompt_tokens(entry.rendered_block) <= 120
    assert result.diagnostics.truncated_count == 1
    assert result.diagnostics.reason_counts[SelectionReason.EXCERPT_TRUNCATED] == 1


def test_source_cap_and_context_budget_omissions_are_recorded_explicitly() -> None:
    sources = [
        {
            "title": f"Paper {i} on plasma confinement",
            "abstract": (
                f"Paper {i} studies tokamak plasma confinement stability "
                + ("filler text " * 40)
            ),
            "doi": f"10.1000/plasma-{i}",
        }
        for i in range(12)
    ]

    capped = select_bounded_sources(
        sources,
        query="tokamak plasma confinement",
        evidence_budget_tokens=50_000,
        max_prompt_sources=10,
    )
    assert len(capped.eligible_sources) == 12
    assert len(capped.prompt_entries) == 10
    assert capped.diagnostics.reason_counts[SelectionReason.SOURCE_CAP] == 2

    tight = select_bounded_sources(
        sources,
        query="tokamak plasma confinement",
        evidence_budget_tokens=180,
        max_prompt_sources=10,
    )
    assert 1 <= len(tight.prompt_entries) < 10
    assert tight.diagnostics.reason_counts[SelectionReason.CONTEXT_BUDGET] > 0
    assert tight.diagnostics.evidence_tokens_used <= 180


def test_budget_ledger_reports_remaining_tokens() -> None:
    from decimal import Decimal

    ledger = BudgetLedger(
        ExecutionBudget(
            max_cost_usd=Decimal("1.00"),
            max_total_tokens=5000,
            max_tool_invocations=5,
            max_parallel_tasks=1,
            max_attempts_per_task=1,
            task_timeout_seconds=30,
        )
    )
    assert ledger.remaining_tokens == 5000
    reservation = ledger.reserve_call(estimated_tokens=1200)
    assert ledger.remaining_tokens == 3800
    ledger.release(reservation)
    assert ledger.remaining_tokens == 5000


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
def test_retrieval_only_fixture_case(case: dict[str, Any]) -> None:
    """Measure retrieval recall, ranking, and answer-span survival without an LLM."""
    result = select_bounded_sources(
        case["sources"],
        query=case["query"],
        evidence_budget_tokens=case["evidence_budget_tokens"],
        max_prompt_sources=case["max_prompt_sources"],
        year_start=case["year_start"],
        year_end=case["year_end"],
    )
    expected = case["expected"]

    assert result.diagnostics.deduped_count == expected["deduped_count"]
    assert result.diagnostics.selected_count == expected["selected_count"]
    assert result.prompt_entries[0].ref == expected["top_ref"]

    combined_excerpts = "\n".join(entry.excerpt for entry in result.prompt_entries)
    for span in expected["must_survive_spans"]:
        assert span in combined_excerpts

    if "duplicate_count" in expected:
        dup_reasons = (
            SelectionReason.DUPLICATE_DOI,
            SelectionReason.DUPLICATE_PROVIDER_ID,
            SelectionReason.DUPLICATE_URL,
        )
        observed_dups = sum(
            result.diagnostics.reason_counts.get(reason, 0) for reason in dup_reasons
        )
        assert observed_dups == expected["duplicate_count"]

    if "truncated_count" in expected:
        assert result.diagnostics.truncated_count == expected["truncated_count"]

    for reason_name, expected_count in expected.get("omitted_reasons", {}).items():
        assert result.diagnostics.reason_counts.get(reason_name, 0) == expected_count
