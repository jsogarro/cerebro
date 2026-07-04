"""Live multi-domain merge checks: concat (free) and llm (one balanced call).

Drives DirectExecutionService._merge_domain_results directly - no HTTP server
needed - with realistic per-domain outputs.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.api.services.direct_execution_service import DirectExecutionService
from src.core.config import get_settings

pytestmark = pytest.mark.live_eval


def _domain_results() -> list[dict]:
    return [
        {
            "domain": "research",
            "status": "completed",
            "output": {
                "summary": (
                    "Renewable adoption grew 18% YoY driven by falling storage "
                    "costs and sustained policy support across major markets. "
                )
                * 15
            },
        },
        {
            "domain": "analytics",
            "status": "completed",
            "output": {
                "summary": (
                    "Panel regression shows subsidy levels correlate with "
                    "installation growth (r=0.72, p<0.01) across regions. "
                )
                * 15
            },
        },
    ]


def _service() -> DirectExecutionService:
    return DirectExecutionService(
        masr_router=AsyncMock(), supervisor_bridge=AsyncMock(), gemini_service=None
    )


def _settings_with(strategy: str):
    s = get_settings().model_copy(deep=True)
    s.MULTI_DOMAIN_MERGE_STRATEGY = strategy
    return s


@pytest.mark.asyncio
async def test_multi_domain_merge_concat_strategy(live_eval_report) -> None:
    with patch(
        "src.api.services.direct_execution_service.get_settings",
        return_value=_settings_with("concat"),
    ):
        merged = await _service()._merge_domain_results(_domain_results())

    out = merged["output"]
    meta = out.get("_multi_domain_metadata", {})
    ok = (
        meta.get("merge_strategy") == "concat"
        and "research" in out
        and "analytics" in out
        and set(merged["succeeded_domains"]) == {"research", "analytics"}
    )
    live_eval_report.add_check_result(
        "multi_domain.concat",
        "passed" if ok else "failed",
        {
            "strategy": meta.get("merge_strategy"),
            "domains": list(merged["succeeded_domains"]),
        },
    )
    assert ok


@pytest.mark.asyncio
async def test_multi_domain_merge_llm_strategy(
    openrouter_spy, gemini_fallback_guard, live_eval_report
) -> None:
    with patch(
        "src.api.services.direct_execution_service.get_settings",
        return_value=_settings_with("llm"),
    ):
        merged = await asyncio.wait_for(
            _service()._merge_domain_results(_domain_results()), timeout=180
        )

    out = merged["output"]
    meta = out.get("_multi_domain_metadata", {})
    synthesis = out.get("synthesis", "") or ""
    ok = (
        meta.get("merge_strategy") == "llm"
        and len(synthesis) > 200
        and gemini_fallback_guard["structured"] == 0
        and gemini_fallback_guard["text"] == 0
    )
    live_eval_report.add_check_result(
        "multi_domain.llm",
        "passed" if ok else "failed",
        {
            "strategy": meta.get("merge_strategy"),
            "synthesis_len": len(synthesis),
            "models": [c["model"] for c in openrouter_spy],
            "gemini_fallbacks": dict(gemini_fallback_guard),
        },
    )
    assert meta.get("merge_strategy") == "llm", (
        f"fell back to {meta.get('merge_strategy')}"
    )
    assert len(synthesis) > 200, "LLM synthesis empty/short - silent degradation"
    assert gemini_fallback_guard["structured"] == 0
