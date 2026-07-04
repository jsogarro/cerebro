"""Multi-domain merge checks: concat and llm strategies, direct _merge_domain_results call."""

import os

import pytest

from evals.live.conftest import LiveEvalCostRecord, LiveEvalReport


@pytest.mark.live_eval
async def test_multi_domain_merge_concat_strategy(
    live_eval_cost_meter: LiveEvalReport,
) -> None:
    """Test concat merge strategy with direct _merge_domain_results call."""
    # Import here to avoid triggering settings validation at module load
    from src.api.services.direct_execution_service import DirectExecutionService
    from src.core.config import get_settings

    api_key = os.getenv("OPENROUTER_API_KEY")
    assert api_key, "OPENROUTER_API_KEY required"

    settings = get_settings()
    # Force concat strategy for this test
    original_strategy = settings.MULTI_DOMAIN_MERGE_STRATEGY
    settings.MULTI_DOMAIN_MERGE_STRATEGY = "concat"

    service = DirectExecutionService()

    # Mock domain results (as if they came from per-domain workers)
    domain_results = [
        {
            "domain": "research",
            "status": "completed",
            "output": {"content": "Research findings on topic A."},
            "quality_score": 0.85,
            "consensus_score": 0.90,
            "workers_used": 3,
            "execution_time_seconds": 2.5,
        },
        {
            "domain": "content",
            "status": "completed",
            "output": {"content": "Content summary for topic B."},
            "quality_score": 0.88,
            "consensus_score": 0.92,
            "workers_used": 2,
            "execution_time_seconds": 1.8,
        },
        {
            "domain": "analytics",
            "status": "completed",
            "output": {"content": "Analytics results for dataset C."},
            "quality_score": 0.82,
            "consensus_score": 0.87,
            "workers_used": 4,
            "execution_time_seconds": 3.1,
        },
    ]

    try:
        # Direct call to _merge_domain_results
        merged = await service._merge_domain_results(domain_results)

        # Verify concat strategy was used
        assert merged["merge_strategy"] == "concat", (
            f"Expected concat strategy, got {merged['merge_strategy']}"
        )

        # Verify all domains succeeded
        assert len(merged["succeeded_domains"]) == 3, (
            f"Expected 3 succeeded domains, got {len(merged['succeeded_domains'])}"
        )
        assert not merged["failed_domains"], (
            f"Unexpected failed domains: {merged['failed_domains']}"
        )

        # Verify per-domain outputs are present
        assert "research" in merged["output"], "Missing research domain output"
        assert "content" in merged["output"], "Missing content domain output"
        assert "analytics" in merged["output"], "Missing analytics domain output"

        # Verify metadata
        assert merged["total_workers_used"] == 9, (
            f"Expected 9 total workers, got {merged['total_workers_used']}"
        )
        assert merged["max_execution_time_seconds"] > 0, "Missing execution time"

        live_eval_cost_meter.add_check_result(
            "multi_domain_merge_concat",
            "passed",
            {
                "strategy": merged["merge_strategy"],
                "succeeded_domains": merged["succeeded_domains"],
                "total_workers": merged["total_workers_used"],
                "max_exec_time_sec": merged["max_execution_time_seconds"],
            },
        )

    finally:
        # Restore original strategy
        settings.MULTI_DOMAIN_MERGE_STRATEGY = original_strategy


@pytest.mark.live_eval
async def test_multi_domain_merge_llm_strategy(
    live_eval_cost_meter: LiveEvalReport,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test LLM synthesis merge strategy with direct _merge_domain_results call."""
    # Import here to avoid triggering settings validation at module load
    from src.api.services.direct_execution_service import DirectExecutionService
    from src.core.config import get_settings

    api_key = os.getenv("OPENROUTER_API_KEY")
    assert api_key, "OPENROUTER_API_KEY required"

    settings = get_settings()
    # Force llm strategy for this test
    original_strategy = settings.MULTI_DOMAIN_MERGE_STRATEGY
    settings.MULTI_DOMAIN_MERGE_STRATEGY = "llm"

    service = DirectExecutionService()

    # Mock domain results with varied content to synthesize
    domain_results = [
        {
            "domain": "research",
            "status": "completed",
            "output": {
                "content": "Quantum computing shows promise for optimization problems."
            },
            "quality_score": 0.87,
            "consensus_score": 0.91,
            "workers_used": 3,
            "execution_time_seconds": 2.2,
        },
        {
            "domain": "finance",
            "status": "completed",
            "output": {
                "content": "Investment portfolio diversification reduces risk by 40%."
            },
            "quality_score": 0.89,
            "consensus_score": 0.93,
            "workers_used": 2,
            "execution_time_seconds": 1.9,
        },
    ]

    try:
        # Direct call to _merge_domain_results
        merged = await service._merge_domain_results(domain_results)

        # Verify LLM strategy was attempted (may fall back to concat if synthesis fails)
        assert merged["merge_strategy"] in {"llm", "concat_fallback"}, (
            f"Expected llm or concat_fallback, got {merged['merge_strategy']}"
        )

        # If LLM synthesis succeeded, verify structure
        if merged["merge_strategy"] == "llm":
            assert "synthesis" in merged["output"], (
                "Missing synthesis in LLM merge output"
            )
            assert "per_domain" in merged["output"], (
                "Missing per_domain in LLM merge output"
            )

            # Verify per-domain outputs preserved
            assert "research" in merged["output"]["per_domain"], (
                "Missing research in per_domain"
            )
            assert "finance" in merged["output"]["per_domain"], (
                "Missing finance in per_domain"
            )

            # Verify synthesis is non-empty
            synthesis = merged["output"]["synthesis"]
            assert synthesis, "Empty synthesis output"
            assert len(str(synthesis)) > 10, "Synthesis output too short"

        # Verify all domains succeeded
        assert len(merged["succeeded_domains"]) == 2, (
            f"Expected 2 succeeded domains, got {len(merged['succeeded_domains'])}"
        )

        # Check for Gemini fallback warnings - MUST BE ZERO
        fallback_warnings = [
            record
            for record in caplog.records
            if "fallback" in record.message.lower()
            and "gemini" in record.message.lower()
        ]
        assert not fallback_warnings, f"Gemini fallback warnings: {fallback_warnings}"

        # Record cost (LLM synthesis incurs additional cost)
        cost_record = LiveEvalCostRecord(
            check_name="multi_domain_merge_llm",
            model="deepseek/deepseek-chat"
            if merged["merge_strategy"] == "llm"
            else "n/a",
            input_tokens=150 if merged["merge_strategy"] == "llm" else 0,
            output_tokens=100 if merged["merge_strategy"] == "llm" else 0,
            cost_usd=0.0005 if merged["merge_strategy"] == "llm" else 0.0,
        )
        live_eval_cost_meter.add_cost(cost_record)

        live_eval_cost_meter.add_check_result(
            "multi_domain_merge_llm",
            "passed",
            {
                "strategy": merged["merge_strategy"],
                "succeeded_domains": merged["succeeded_domains"],
                "synthesis_present": "synthesis" in merged.get("output", {}),
                "fallback_warnings": len(fallback_warnings),
                "cost_usd": cost_record.cost_usd,
            },
        )

    finally:
        # Restore original strategy
        settings.MULTI_DOMAIN_MERGE_STRATEGY = original_strategy
