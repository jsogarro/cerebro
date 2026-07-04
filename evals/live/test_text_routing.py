"""Live text-generation routing: one worker per domain, simple tier.

Asserts behavior, not text: the OpenRouter payload carries the simple-tier
model, output is non-empty, and ZERO Gemini fallbacks occur.
"""

import asyncio

import pytest

from src.agents.factory import AgentFactory
from src.agents.models import AgentTask
from src.core.config import settings

pytestmark = pytest.mark.live_eval

DOMAIN_WORKERS = [
    (
        "research",
        "methodology",
        "In one paragraph, outline a sound methodology to study remote-work productivity.",
    ),
    ("content", "drafting", "Write a 3-sentence product blurb for a note-taking app."),
    (
        "analytics",
        "data_analysis",
        "Given the series [1, 2, 3, 5, 8, 13], describe the growth pattern in two sentences.",
    ),
    (
        "finance",
        "financial_analysis",
        "In two sentences, explain what a rising debt-to-equity ratio signals.",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("domain", "factory_key", "prompt"), DOMAIN_WORKERS)
async def test_text_routing_per_domain(
    domain, factory_key, prompt, openrouter_spy, gemini_fallback_guard, live_eval_report
) -> None:
    worker = AgentFactory().create_agent(factory_key)
    task = AgentTask(
        id=f"live-eval-{domain}",
        agent_type=factory_key,
        input_data={"query": prompt, "complexity_score": 0.1},
    )

    content, confidence = await asyncio.wait_for(
        worker._generate_with_routing(prompt, task), timeout=120
    )

    simple_model = settings.OPENROUTER_TIER_MAPPING["simple"]
    models = [c["model"] for c in openrouter_spy]
    ok = (
        bool(content)
        and len(content) > 20
        and simple_model in models
        and gemini_fallback_guard["text"] == 0
        and gemini_fallback_guard["structured"] == 0
    )
    live_eval_report.add_check_result(
        f"text_routing.{domain}",
        "passed" if ok else "failed",
        {
            "models": models,
            "content_len": len(content or ""),
            "confidence": confidence,
            "gemini_fallbacks": dict(gemini_fallback_guard),
        },
    )

    assert content, "empty generation"
    assert len(content) > 20
    assert simple_model in models, f"expected simple-tier model, saw {models}"
    assert gemini_fallback_guard["text"] == 0, "silent Gemini text fallback"
    assert gemini_fallback_guard["structured"] == 0, "silent Gemini structured fallback"
