"""Tests for VerificationAgent integration into supervisor quality gates."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.communication.talkhier_message import TalkHierContent
from src.agents.models import AgentResult, AgentTask
from src.agents.supervisors.analytics_supervisor import AnalyticsSupervisor
from src.agents.supervisors.base_supervisor import SupervisionState
from src.agents.supervisors.content_supervisor import ContentSupervisor
from src.agents.supervisors.finance_supervisor import FinanceSupervisor


@pytest.fixture
def mock_gemini_service():
    """Mock Gemini service for testing."""
    service = MagicMock()
    service.generate_content = AsyncMock(return_value="VERDICT: PASS\nISSUES: None")
    service.generate_structured_content = AsyncMock()
    return service


@pytest.fixture
def mock_cache_client():
    """Mock cache client."""
    return MagicMock()


class TestContentSupervisorVerification:
    """Test verification QA gate in ContentSupervisor."""

    @pytest.mark.asyncio
    async def test_quality_phase_with_verification_pass(
        self, mock_gemini_service, mock_cache_client,
    ):
        """Test that PASS verdict does not reduce quality score."""
        supervisor = ContentSupervisor(
            gemini_service=mock_gemini_service,
            cache_client=mock_cache_client,
            config={},
        )

        # Mock worker results
        state = SupervisionState(
            task_id="test-123",
            original_query="Write a blog post",
            allocated_workers=["drafting", "editing"],
        )
        state.worker_results = {
            "drafting": TalkHierContent(
                content="Draft content here",
                background="",
                intermediate_outputs={},
            ),
            "editing": TalkHierContent(
                content="Edited content here",
                background="",
                intermediate_outputs={},
            ),
        }

        langgraph_state = {
            "supervision_state": state,
            "original_task": AgentTask(
                id="test-123",
                agent_type="content",
                input_data={"query": "Write a blog post"},
            ),
        }

        # Mock verification agent to return PASS
        with patch("src.agents.factory.AgentFactory.create_agent") as mock_create_agent:
            mock_verif_agent = MagicMock()
            mock_verif_agent.execute = AsyncMock(
                return_value=AgentResult(
                    task_id="verif-1",
                    status="completed",
                    output={"content": "VERDICT: PASS\nISSUES: None"},
                    confidence=0.9,
                    execution_time=1.0,
                ),
            )
            mock_create_agent.return_value = mock_verif_agent

            # Execute quality evaluation phase
            result_state = await supervisor._evaluate_quality_phase(langgraph_state)

        state = result_state["supervision_state"]

        # Verify that verification was recorded
        assert "verification" in state.worker_results
        assert state.worker_results["verification"]["verdict"] == "pass"

        # Verify that quality score was NOT reduced (PASS verdict)
        # Base score: 2 workers (excluding verification) = 0.5 + 0.2 = 0.7
        assert state.quality_score == pytest.approx(0.7, abs=0.01)

    @pytest.mark.asyncio
    async def test_quality_phase_with_verification_revise(
        self, mock_gemini_service, mock_cache_client,
    ):
        """Test that REVISE verdict reduces quality score."""
        supervisor = ContentSupervisor(
            gemini_service=mock_gemini_service,
            cache_client=mock_cache_client,
            config={},
        )

        state = SupervisionState(
            task_id="test-123",
            original_query="Write a blog post",
            allocated_workers=["drafting", "editing"],
        )
        state.worker_results = {
            "drafting": TalkHierContent(
                content="Draft with errors",
                background="",
                intermediate_outputs={},
            ),
            "editing": TalkHierContent(
                content="Edited but still has issues",
                background="",
                intermediate_outputs={},
            ),
        }

        langgraph_state = {
            "supervision_state": state,
            "original_task": AgentTask(
                id="test-123",
                agent_type="content",
                input_data={"query": "Write a blog post"},
            ),
        }

        # Mock verification agent to return REVISE
        with patch("src.agents.factory.AgentFactory.create_agent") as mock_create_agent:
            mock_verif_agent = MagicMock()
            mock_verif_agent.execute = AsyncMock(
                return_value=AgentResult(
                    task_id="verif-1",
                    status="completed",
                    output={
                        "content": "VERDICT: REVISE\nISSUES:\n1. Factual error in paragraph 2",
                    },
                    confidence=0.9,
                    execution_time=1.0,
                ),
            )
            mock_create_agent.return_value = mock_verif_agent

            result_state = await supervisor._evaluate_quality_phase(langgraph_state)

        state = result_state["supervision_state"]

        # Verify that verification was recorded
        assert "verification" in state.worker_results
        assert state.worker_results["verification"]["verdict"] == "revise"

        # Verify that quality score WAS reduced (REVISE verdict)
        # Base score: 0.7, reduced by 0.85 = 0.595
        assert state.quality_score == pytest.approx(0.7 * 0.85, abs=0.01)

    @pytest.mark.asyncio
    async def test_quality_phase_empty_content_graceful_degradation(
        self, mock_gemini_service, mock_cache_client,
    ):
        """Test graceful degradation when content is empty."""
        supervisor = ContentSupervisor(
            gemini_service=mock_gemini_service,
            cache_client=mock_cache_client,
            config={},
        )

        state = SupervisionState(
            task_id="test-123",
            original_query="Write a blog post",
            allocated_workers=[],
        )
        state.worker_results = {}  # Empty results

        langgraph_state = {
            "supervision_state": state,
            "original_task": AgentTask(
                id="test-123",
                agent_type="content",
                input_data={"query": "Write a blog post"},
            ),
        }

        result_state = await supervisor._evaluate_quality_phase(langgraph_state)
        state = result_state["supervision_state"]

        # Verify that verification returned neutral result
        assert "verification" in state.worker_results
        assert state.worker_results["verification"]["verdict"] == "pass"
        assert "No content to verify" in state.worker_results["verification"]["report"]


class TestAnalyticsSupervisorVerification:
    """Test verification QA gate in AnalyticsSupervisor."""

    @pytest.mark.asyncio
    async def test_confidence_phase_with_verification_pass(
        self, mock_gemini_service, mock_cache_client,
    ):
        """Test that PASS verdict does not reduce quality score in analytics."""
        supervisor = AnalyticsSupervisor(
            gemini_service=mock_gemini_service,
            cache_client=mock_cache_client,
            config={},
        )

        state = SupervisionState(
            task_id="test-456",
            original_query="Analyze sales data",
            allocated_workers=["data_analysis", "statistical_modeling"],
        )
        state.worker_results = {
            "data_analysis": TalkHierContent(
                content="Statistical analysis results",
                background="",
                intermediate_outputs={},
            ),
            "statistical_modeling": TalkHierContent(
                content="Model results: R²=0.85",
                background="",
                intermediate_outputs={},
            ),
        }

        langgraph_state = {
            "supervision_state": state,
            "original_task": AgentTask(
                id="test-456",
                agent_type="analytics",
                input_data={"query": "Analyze sales data"},
            ),
        }

        with patch("src.agents.factory.AgentFactory.create_agent") as mock_create_agent:
            mock_verif_agent = MagicMock()
            mock_verif_agent.execute = AsyncMock(
                return_value=AgentResult(
                    task_id="verif-2",
                    status="completed",
                    output={"content": "VERDICT: PASS\nISSUES: None"},
                    confidence=0.9,
                    execution_time=1.0,
                ),
            )
            mock_create_agent.return_value = mock_verif_agent

            result_state = await supervisor._evaluate_confidence_phase(langgraph_state)

        state = result_state["supervision_state"]

        assert "verification" in state.worker_results
        assert state.worker_results["verification"]["verdict"] == "pass"
        # Base: 2 workers = 0.6 + 0.22 = 0.82
        assert state.quality_score == pytest.approx(0.82, abs=0.01)


class TestFinanceSupervisorVerification:
    """Test verification QA gate in FinanceSupervisor."""

    @pytest.mark.asyncio
    async def test_quality_phase_with_verification_revise(
        self, mock_gemini_service, mock_cache_client,
    ):
        """Test that REVISE verdict reduces quality score in finance."""
        supervisor = FinanceSupervisor(
            gemini_service=mock_gemini_service,
            cache_client=mock_cache_client,
            config={},
        )

        state = SupervisionState(
            task_id="test-789",
            original_query="Financial analysis",
            allocated_workers=["financial_analysis", "valuation"],
        )
        state.worker_results = {
            "financial_analysis": TalkHierContent(
                content="Ratio analysis with arithmetic error",
                background="",
                intermediate_outputs={},
            ),
            "valuation": TalkHierContent(
                content="DCF valuation",
                background="",
                intermediate_outputs={},
            ),
        }

        langgraph_state = {
            "supervision_state": state,
            "original_task": AgentTask(
                id="test-789",
                agent_type="finance",
                input_data={"query": "Financial analysis"},
            ),
        }

        with patch("src.agents.factory.AgentFactory.create_agent") as mock_create_agent:
            mock_verif_agent = MagicMock()
            mock_verif_agent.execute = AsyncMock(
                return_value=AgentResult(
                    task_id="verif-3",
                    status="completed",
                    output={
                        "content": "VERDICT: REVISE\nISSUES:\n1. Arithmetic mistake in ROE calculation",
                    },
                    confidence=0.9,
                    execution_time=1.0,
                ),
            )
            mock_create_agent.return_value = mock_verif_agent

            result_state = await supervisor._evaluate_quality_phase(langgraph_state)

        state = result_state["supervision_state"]

        assert "verification" in state.worker_results
        assert state.worker_results["verification"]["verdict"] == "revise"
        # Base: 2 workers = 0.5 + 0.2 = 0.7, reduced by 0.85 = 0.595
        assert state.quality_score == pytest.approx(0.7 * 0.85, abs=0.01)


class TestVerificationAgentError:
    """Test error handling in verification."""

    @pytest.mark.asyncio
    async def test_verification_agent_error_graceful_degradation(
        self, mock_gemini_service, mock_cache_client,
    ):
        """Test that verification errors don't break the workflow."""
        supervisor = ContentSupervisor(
            gemini_service=mock_gemini_service,
            cache_client=mock_cache_client,
            config={},
        )

        state = SupervisionState(
            task_id="test-error",
            original_query="Write content",
            allocated_workers=["drafting"],
        )
        state.worker_results = {
            "drafting": TalkHierContent(
                content="Some content",
                background="",
                intermediate_outputs={},
            ),
        }

        langgraph_state = {
            "supervision_state": state,
            "original_task": AgentTask(
                id="test-error",
                agent_type="content",
                input_data={"query": "Write content"},
            ),
        }

        # Mock verification agent to throw an error
        with patch("src.agents.factory.AgentFactory.create_agent") as mock_create_agent:
            mock_verif_agent = MagicMock()
            mock_verif_agent.execute = AsyncMock(
                side_effect=Exception("Verification service unavailable"),
            )
            mock_create_agent.return_value = mock_verif_agent

            # Should not raise exception
            result_state = await supervisor._evaluate_quality_phase(langgraph_state)

        state = result_state["supervision_state"]

        # Verify graceful degradation - neutral verdict
        assert "verification" in state.worker_results
        assert state.worker_results["verification"]["verdict"] == "pass"
        assert "error" in state.worker_results["verification"]["report"].lower()
        # Quality score should be calculated without reduction
        assert state.quality_score > 0
