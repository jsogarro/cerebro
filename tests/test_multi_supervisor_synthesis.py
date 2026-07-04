"""Regression tests for real multi-supervisor orchestration synthesis.

`_synthesize_results` previously delegated to the aggregator stub, which just
concatenated results into `"Synthesized result combining: ..."`. It now performs
a real LLM synthesis (with a deterministic fallback) and derives consensus from
quality-score agreement.
"""

from src.api.services.supervisor_coordination_service import (
    SupervisorCoordinationService,
)


class _FakeGemini:
    async def generate_content(self, prompt: str) -> str:
        # Echo a marker plus proof it received both supervisors' content.
        assert "research" in prompt and "analytics" in prompt
        return "Integrated cross-domain synthesis of the supervisor outputs."


def _results():
    return {
        "research": {"result": "research finding", "quality_score": 0.9},
        "analytics": {"result": "analytics finding", "quality_score": 0.88},
    }


async def test_synthesis_uses_llm_not_placeholder() -> None:
    service = SupervisorCoordinationService()
    service._gemini_service = _FakeGemini()

    synthesized, consensus = await service._synthesize_results(
        _results(), {"research": 2.0, "analytics": 1.0},
    )

    assert synthesized == "Integrated cross-domain synthesis of the supervisor outputs."
    assert "Synthesized result combining:" not in str(synthesized)
    # Quality scores 0.90 / 0.88 are within 0.15 -> consensus.
    assert consensus is True


async def test_consensus_false_when_quality_scores_diverge() -> None:
    service = SupervisorCoordinationService()
    service._gemini_service = _FakeGemini()

    results = {
        "research": {"result": "a", "quality_score": 0.95},
        "analytics": {"result": "b", "quality_score": 0.50},
    }
    _synth, consensus = await service._synthesize_results(results, {})
    assert consensus is False


async def test_empty_results_returns_none() -> None:
    service = SupervisorCoordinationService()
    synthesized, consensus = await service._synthesize_results({}, {})
    assert synthesized is None
    assert consensus is False
