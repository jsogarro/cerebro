"""Live structured-output routing: schema-validated results via OpenRouter.

Two checks: a simple-tier citation call (cheap) and one synthesis-scale call
at the default structured budget (the class of call that once truncated at a
hardcoded token cap and silently degraded).
"""

import asyncio

import pytest

from src.agents.factory import AgentFactory
from src.agents.models import AgentTask
from src.agents.schemas import CitationSchema
from src.agents.schemas.synthesis import SynthesisSchema
from src.core.config import settings

pytestmark = pytest.mark.live_eval


@pytest.mark.asyncio
async def test_citation_simple_tier(
    openrouter_spy, gemini_fallback_guard, live_eval_report
) -> None:
    worker = AgentFactory().create_agent("citation")
    task = AgentTask(
        id="live-eval-citation",
        agent_type="citation",
        input_data={"query": "format citations", "complexity_score": 0.1},
    )
    prompt = (
        "Format these sources as APA citations. Return JSON with a 'citations' "
        "array of {citation_text, source_id}.\n"
        "1. 'Attention Is All You Need', Vaswani et al., 2017, NeurIPS.\n"
        "2. 'BERT: Pre-training of Deep Bidirectional Transformers', Devlin et al., 2019, NAACL."
    )

    result = await asyncio.wait_for(
        worker._generate_structured_with_routing(prompt, CitationSchema, task),
        timeout=120,
    )

    simple_model = settings.OPENROUTER_TIER_MAPPING["simple"]
    structured_calls = [
        c for c in openrouter_spy if c["response_format"] == "json_object"
    ]
    ok = (
        isinstance(result, CitationSchema)
        and len(result.citations) >= 1
        and any(c["model"] == simple_model for c in structured_calls)
        and gemini_fallback_guard["structured"] == 0
    )
    live_eval_report.add_check_result(
        "structured_routing.citation_simple_tier",
        "passed" if ok else "failed",
        {
            "type": type(result).__name__,
            "n_citations": len(getattr(result, "citations", [])),
            "structured_calls": structured_calls,
            "gemini_fallbacks": dict(gemini_fallback_guard),
        },
    )

    assert isinstance(result, CitationSchema)
    assert len(result.citations) >= 1
    assert any(c["model"] == simple_model for c in structured_calls), (
        f"citation structured call not on simple tier: {structured_calls}"
    )
    assert gemini_fallback_guard["structured"] == 0, "silent Gemini structured fallback"


@pytest.mark.asyncio
async def test_synthesis_scale_budget(
    openrouter_spy, gemini_fallback_guard, live_eval_report
) -> None:
    """Synthesis-scale structured call at the default budget must not degrade."""
    worker = AgentFactory().create_agent("synthesis")
    task = AgentTask(
        id="live-eval-synthesis",
        agent_type="synthesis",
        input_data={"query": "synthesize findings", "complexity_score": 0.5},
    )
    findings = (
        "Renewable adoption grew 18% YoY on falling storage costs and policy support. "
        "Panel regression shows subsidies correlate with installation growth (r=0.72). "
    ) * 20
    prompt = (
        "Synthesize the following findings into a coherent narrative with "
        "integrated findings and meta insights. Return JSON matching the schema.\n\n"
        + findings
    )

    result = await asyncio.wait_for(
        worker._generate_structured_with_routing(prompt, SynthesisSchema, task),
        timeout=180,
    )

    structured_calls = [
        c for c in openrouter_spy if c["response_format"] == "json_object"
    ]
    budgets = [c["max_tokens"] for c in structured_calls]
    narrative = getattr(result, "comprehensive_narrative", "") or ""
    ok = (
        isinstance(result, SynthesisSchema)
        and len(narrative) > 200
        and all(b >= 4000 for b in budgets)
        and gemini_fallback_guard["structured"] == 0
    )
    live_eval_report.add_check_result(
        "structured_routing.synthesis_scale",
        "passed" if ok else "failed",
        {
            "narrative_len": len(narrative),
            "budgets": budgets,
            "structured_calls": structured_calls,
            "gemini_fallbacks": dict(gemini_fallback_guard),
        },
    )

    assert isinstance(result, SynthesisSchema)
    assert len(narrative) > 200, "synthesis narrative suspiciously short/empty"
    assert all(b >= 4000 for b in budgets), f"structured budget regressed: {budgets}"
    assert gemini_fallback_guard["structured"] == 0, "silent Gemini structured fallback"
