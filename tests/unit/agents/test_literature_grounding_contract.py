"""Selected evidence reaches the literature-review prompt; failure is honest."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.literature_review_agent import LiteratureReviewAgent
from src.agents.models import AgentTask
from src.core.acquisition.resolution import locate

TITLE = "Zebra-Quartz inflation graphs"
SENTINEL = "SENTINEL-the-answer-is-seven-percent."
ABSTRACT = ("Filler sentence. " * 80) + SENTINEL
EVIDENCE_ID = "evidence-abc123"
SOURCE = {
    "title": TITLE,
    "abstract": ABSTRACT,
    "url": "https://arxiv.org/abs/2401.00001",
}
LOCATOR = locate("bytes", 0, len(f"{TITLE}\n\n{ABSTRACT}".encode()))
ANALYSIS = json.dumps(
    {
        "literature_analysis": {
            "key_findings": ["GNNs forecast inflation"],
            "research_gaps": ["No out-of-sample test"],
            "methodologies_used": [],
            "quality_assessment": "One retrieved source",
        }
    }
)
CANNED = ("significant patterns", "good methodological rigor", "Mock", "Stand-in")


def _search_result(**overrides: Any) -> dict[str, Any]:
    return {
        "success": True,
        "data_source": "mcp_tools",
        "sources": [SOURCE],
        "total_found": 1,
        "source_evidence": {
            "recorded": [EVIDENCE_ID],
            "failed": [],
            "sources": [
                {"index": 0, "disposition": "recorded", "evidence_id": EVIDENCE_ID}
            ],
        },
        **overrides,
    }


def _agent(search: Any) -> LiteratureReviewAgent:
    mcp = MagicMock()
    mcp.search_academic_sources = (
        AsyncMock(side_effect=search)
        if isinstance(search, BaseException)
        else AsyncMock(return_value=search)
    )
    mcp.build_knowledge_graph = AsyncMock(return_value={"entities": []})
    mcp.format_citations = AsyncMock(return_value={"citations": []})
    return LiteratureReviewAgent(
        gemini_service=AsyncMock(), config={"mcp_integration": mcp}
    )


def _task() -> AgentTask:
    return AgentTask(
        id="lit-1", agent_type="literature_review", input_data={"query": "inflation"}
    )


@pytest.mark.asyncio
async def test_selected_evidence_reaches_the_model_prompt() -> None:
    agent = _agent(_search_result())
    model = AsyncMock(return_value=(ANALYSIS, 0.9))
    agent._generate_with_routing = model

    result = await agent.execute(_task())

    assert result.status == "success"
    prompt = model.await_args.args[0]
    for expected in (TITLE, SENTINEL, EVIDENCE_ID, LOCATOR):
        assert expected in prompt
    assert result.output["analysis_status"] == "grounded"
    assert result.output["source_evidence"]["recorded"] == [EVIDENCE_ID]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "search",
    [
        None,
        "{not json",
        ConnectionError("mcp down"),
        {
            "success": False,
            "error_code": "tool_error",
            "sources": [{"title": "Stand-in"}],
        },
    ],
    ids=["none", "malformed", "raises", "degraded"],
)
async def test_a_failed_search_fails_with_no_canned_findings(search: Any) -> None:
    agent = _agent(search)
    model = AsyncMock()
    agent._generate_with_routing = model

    result = await agent.execute(_task())

    assert result.status == "failed"
    assert result.output["error_code"] == "INSUFFICIENT_EVIDENCE"
    assert result.output["analysis_status"] == "no_evidence"
    assert result.output["reason"]
    assert result.output["sources_found"] == []
    assert result.output["key_findings"] == []
    assert result.output["research_gaps"] == []
    model.assert_not_awaited()
    agent.gemini_service.generate_structured_content.assert_not_awaited()
    assert not any(text in json.dumps(result.output) for text in CANNED)


@pytest.mark.asyncio
async def test_no_search_tool_is_no_evidence_not_model_named_sources() -> None:
    agent = LiteratureReviewAgent()
    model = AsyncMock(return_value=('{"sources": [{"title": "Invented"}]}', 0.8))
    agent._generate_with_routing = model

    result = await agent.execute(_task())

    assert result.status == "failed"
    assert result.output["error_code"] == "INSUFFICIENT_EVIDENCE"
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_sources_none_of_which_became_evidence_are_insufficient() -> None:
    agent = _agent(
        _search_result(
            source_evidence={
                "recorded": [],
                "failed": [{"source_uri": SOURCE["url"], "reason": "OSError"}],
                "sources": [{"index": 0, "disposition": "failed"}],
            }
        )
    )
    model = AsyncMock()
    agent._generate_with_routing = model

    result = await agent.execute(_task())

    assert result.status == "failed"
    assert result.output["error_code"] == "INSUFFICIENT_EVIDENCE"
    assert result.output["source_evidence"]["failed"]
    model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_result", "reason"),
    [
        ((None, 0.0), "no_model_output"),
        (("not json at all", 0.5), "unparseable_model_output"),
    ],
)
async def test_unusable_model_output_fails_keeping_the_retrieved_sources(
    model_result: Any, reason: str
) -> None:
    agent = _agent(_search_result())
    agent._generate_with_routing = AsyncMock(return_value=model_result)

    result = await agent.execute(_task())

    assert result.status == "failed"
    assert result.output["error_code"] == "ANALYSIS_FAILED"
    assert result.output["reason"] == reason
    assert [s["title"] for s in result.output["sources_found"]] == [TITLE]
    assert result.output["key_findings"] == []
    assert not any(text in json.dumps(result.output) for text in CANNED)


@pytest.mark.asyncio
async def test_a_raising_model_call_fails_with_no_canned_findings() -> None:
    agent = _agent(_search_result())
    agent._generate_with_routing = AsyncMock(side_effect=RuntimeError("provider down"))

    result = await agent.execute(_task())

    assert result.status == "failed"
    assert "provider down" in result.output["error"]
    assert "key_findings" not in result.output
