"""The verification QA gate must fail closed (issue #139).

A verdict that is missing, unparseable, empty, or lost to a verifier error must
never come back as ``pass``. Cases run through the real
``BaseSupervisor._run_verification`` with a stubbed verification agent.
"""

from unittest.mock import MagicMock, create_autospec

import pytest

from src.agents.models import AgentResult
from src.agents.supervisors.research_quality_validator import (
    ResearchQualityValidator,
)
from src.agents.verification_agent import VerificationAgent
from tests.test_verification_revision_loop import (
    MockSupervisor,
    _registry_with_verification_agent,
    _registry_with_verification_component,
)

CASES = [
    ("VERDICT: PASS\nISSUES: None", "pass"),
    ("**VERDICT:** PASS\nISSUES: None", "pass"),
    ("verdict: pass", "pass"),
    ("VERDICT: REVISE\nISSUES:\n1. bad", "revise"),
    ("**VERDICT:** REVISE", "revise"),
    ("Verdict: REVISE", "revise"),
    ("  VERDICT: REVISE", "revise"),
    ("## **Verdict**: revise", "revise"),
    ("Looks fine to me, no verdict line.", "revise"),
    ("VERDICT: unclear", "revise"),
    ("", "revise"),
]


def _agent_returning(text: str) -> MagicMock:
    agent = create_autospec(VerificationAgent, instance=True, spec_set=True)
    agent.execute.return_value = AgentResult(
        task_id="t",
        status="success",
        output={"content": text},
        confidence=0.9,
        execution_time=1.0,
    )
    return agent


@pytest.fixture
def supervisor() -> MockSupervisor:
    return MockSupervisor(
        supervisor_type="test",
        domain="test",
        gemini_service=MagicMock(),
        cache_client=None,
        config={"quality_threshold": 0.85},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("reply", "expected"), CASES)
async def test_supervisor_verdict_parsing(supervisor, reply, expected):
    supervisor.component_registry = _registry_with_verification_agent(
        _agent_returning(reply)
    )
    result = await supervisor._run_verification("content")
    assert result["verdict"] == expected


@pytest.mark.asyncio
async def test_supervisor_verifier_exception_is_not_pass(supervisor):
    component = create_autospec(VerificationAgent, spec_set=True)
    component.side_effect = Exception("provider down")
    supervisor.component_registry = _registry_with_verification_component(component)
    result = await supervisor._run_verification("content")
    assert result["verdict"] == "revise"
    assert "Verification error" in result["report"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "expected"),
    [("**VERDICT:** REVISE", "revise"), ("no verdict here", "revise"), ("", "revise")],
)
async def test_research_validator_verdict_parsing(reply, expected):
    validator = ResearchQualityValidator(
        gemini_service=MagicMock(),
        communication_protocol=MagicMock(),
        get_agent_type=lambda: "research",
        quality_threshold=0.85,
        component_registry=_registry_with_verification_agent(_agent_returning(reply)),
    )
    result = await validator._run_verification_via_supervisor("content", {})
    assert result["verdict"] == expected


@pytest.mark.asyncio
async def test_research_validator_exception_is_not_pass():
    component = create_autospec(VerificationAgent, spec_set=True)
    component.side_effect = Exception("provider down")
    validator = ResearchQualityValidator(
        gemini_service=MagicMock(),
        communication_protocol=MagicMock(),
        get_agent_type=lambda: "research",
        quality_threshold=0.85,
        component_registry=_registry_with_verification_component(component),
    )
    result = await validator._run_verification_via_supervisor("content", {})
    assert result["verdict"] == "revise"
