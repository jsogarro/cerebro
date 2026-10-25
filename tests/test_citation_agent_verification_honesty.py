"""CitationAgent must not report metadata-only sources as verified.

DOI resolution is not implemented, so nothing can currently be verified.
"""

from __future__ import annotations

import pytest

from src.agents.citation_agent import CitationAgent
from src.agents.models import AgentTask

NO_DOI = {"title": "Made up", "authors": ["Nobody"], "year": 2020}
FAKE_DOI = {**NO_DOI, "doi": "10.9999/does-not-exist"}


@pytest.fixture
def agent() -> CitationAgent:
    return CitationAgent()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [NO_DOI, FAKE_DOI])
async def test_single_source_with_only_metadata_is_unverified(agent, source):
    result = await agent._verify_single_source(source, "s0")

    assert result["verified"] is False
    assert result["status"] == "unverified"


@pytest.mark.asyncio
async def test_verified_count_is_zero_for_metadata_only_sources(agent):
    result = await agent._verify_sources_with_mcp([NO_DOI, FAKE_DOI])

    assert result["verified_count"] == 0


def test_fallback_formatter_does_not_treat_doi_presence_as_verified(agent):
    result = agent._generate_mock_citations([FAKE_DOI, NO_DOI], "APA")

    assert [v["verified"] for v in result["verification_status"]] == [False, False]
    assert result["verified_sources"] == 0


@pytest.mark.asyncio
async def test_execute_reports_zero_verified_sources(agent):
    task = AgentTask(
        id="t1",
        agent_type=agent.get_agent_type(),
        input_data={"sources": [NO_DOI, FAKE_DOI], "style": "APA"},
    )
    result = await agent.execute(task)

    assert result.output["verified_sources"] == 0
    assert all(not v["verified"] for v in result.output["verification_status"])
    assert result.confidence <= 0.5


@pytest.mark.asyncio
async def test_execute_defaults_verified_sources_to_zero_when_count_missing(agent):
    async def no_count(sources):
        return {}

    agent._verify_sources_with_mcp = no_count
    task = AgentTask(
        id="t2",
        agent_type=agent.get_agent_type(),
        input_data={"sources": [NO_DOI], "style": "APA"},
    )
    result = await agent.execute(task)

    assert result.output["verified_sources"] == 0
