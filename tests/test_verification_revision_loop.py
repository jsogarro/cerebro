"""
Tests for bounded verification revision loop in supervisors.

Validates that supervisors can:
1. Run workers and verify output with the VerificationAgent
2. Loop back on REVISE verdict with corrective feedback
3. Respect the MAX_VERIFICATION_REVISION_ROUNDS cap
4. Apply the ×0.85 penalty on terminal fallback
5. Pass through on first PASS without extra worker calls
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.communication.talkhier_message import (
    MessageType,
    TalkHierContent,
    TalkHierMessage,
)
from src.agents.models import AgentResult
from src.agents.supervisors.base_supervisor import (
    MAX_VERIFICATION_REVISION_ROUNDS,
    BaseSupervisor,
)


class MockSupervisor(BaseSupervisor):
    """Concrete supervisor for testing base functionality."""

    def _register_worker_types(self) -> None:
        pass

    def _build_workflow_graph(self) -> None:
        from langgraph.graph import END, StateGraph

        self.workflow_graph = StateGraph(dict)
        self.workflow_graph.add_node("start", lambda x: x)
        self.workflow_graph.set_entry_point("start")
        self.workflow_graph.add_edge("start", END)
        self.workflow_graph = self.workflow_graph.compile()

    async def _coordinate_workers(self, state, task):
        return state


@pytest.fixture
def mock_supervisor():
    """Create a mock supervisor instance."""
    return MockSupervisor(
        supervisor_type="test",
        domain="test",
        gemini_service=MagicMock(),
        cache_client=None,
        config={"quality_threshold": 0.85},
    )


@pytest.fixture
def mock_worker_response():
    """Create a mock worker response."""
    return TalkHierMessage(
        from_agent="test_worker",
        to_agent="test_supervisor",
        message_type=MessageType.WORKER_REPORT,
        content=TalkHierContent(
            content="Worker output that needs verification",
            confidence_score=0.85,
        ),
    )


class TestVerificationRevisionLoop:
    """Test suite for bounded verification revision loop."""

    @pytest.mark.asyncio
    async def test_pass_on_first_round_no_extra_calls(
        self, mock_supervisor, mock_worker_response
    ):
        """Test PASS on round 1 → assert workers execute once and score unchanged."""
        # Mock send_talkhier_message to return worker response
        mock_supervisor.send_talkhier_message = AsyncMock(
            return_value=mock_worker_response
        )

        # Mock _run_verification to return PASS on first call
        with patch.object(
            mock_supervisor,
            "_run_verification",
            return_value={"verdict": "pass", "report": "All good", "issues": []},
        ) as mock_verify:
            # Execute worker with verification loop
            (
                response,
                verification,
            ) = await mock_supervisor._run_worker_with_verification_loop(
                worker_type="test_worker",
                message_type=MessageType.SUPERVISOR_ASSIGNMENT,
                content="Test task",
            )

            # Assert worker executed exactly once
            assert mock_supervisor.send_talkhier_message.call_count == 1

            # Assert verification ran once
            assert mock_verify.call_count == 1

            # Assert PASS verdict
            assert verification["verdict"] == "pass"
            assert verification["rounds"] == 1

            # Assert response is the worker's output
            assert response == mock_worker_response

    @pytest.mark.asyncio
    async def test_revise_then_pass_two_rounds(
        self, mock_supervisor, mock_worker_response
    ):
        """Test REVISE then PASS → assert workers execute twice."""
        call_count = [0]

        async def mock_send_message(*args, **kwargs):
            call_count[0] += 1
            return mock_worker_response

        mock_supervisor.send_talkhier_message = mock_send_message

        # Mock verification: REVISE on first call, PASS on second
        verify_results = [
            {
                "verdict": "revise",
                "report": "Issue: clarity needed",
                "issues": ["1. Unclear section"],
            },
            {"verdict": "pass", "report": "Improved, all clear", "issues": []},
        ]
        verify_call_index = [0]

        async def mock_verify(content):
            result = verify_results[verify_call_index[0]]
            verify_call_index[0] += 1
            return result

        with patch.object(
            mock_supervisor, "_run_verification", side_effect=mock_verify
        ):
            _, verification = await mock_supervisor._run_worker_with_verification_loop(
                worker_type="test_worker",
                message_type=MessageType.SUPERVISOR_ASSIGNMENT,
                content="Test task",
            )

            # Assert worker executed twice
            assert call_count[0] == 2

            # Assert final verdict is PASS
            assert verification["verdict"] == "pass"
            assert verification["rounds"] == 2

    @pytest.mark.asyncio
    async def test_revise_on_every_round_stops_at_cap(
        self, mock_supervisor, mock_worker_response
    ):
        """Test REVISE on every round → assert stops at MAX_VERIFICATION_REVISION_ROUNDS."""
        call_count = [0]

        async def mock_send_message(*args, **kwargs):
            call_count[0] += 1
            return mock_worker_response

        mock_supervisor.send_talkhier_message = mock_send_message

        # Mock verification to always return REVISE
        with patch.object(
            mock_supervisor,
            "_run_verification",
            return_value={
                "verdict": "revise",
                "report": "Issues remain",
                "issues": ["1. Problem A", "2. Problem B"],
            },
        ):
            _, verification = await mock_supervisor._run_worker_with_verification_loop(
                worker_type="test_worker",
                message_type=MessageType.SUPERVISOR_ASSIGNMENT,
                content="Test task",
            )

            # Assert worker executed exactly MAX_VERIFICATION_REVISION_ROUNDS times
            assert call_count[0] == MAX_VERIFICATION_REVISION_ROUNDS

            # Assert final verdict is still REVISE (terminal fallback)
            assert verification["verdict"] == "revise"
            assert verification["rounds"] == MAX_VERIFICATION_REVISION_ROUNDS

    @pytest.mark.asyncio
    async def test_feedback_appended_to_content_on_revision(
        self, mock_supervisor, mock_worker_response
    ):
        """Test that verifier feedback is appended to worker prompt on REVISE."""
        sent_contents = []

        async def mock_send_message(worker_type, message_type, content, context):
            sent_contents.append(content)
            return mock_worker_response

        mock_supervisor.send_talkhier_message = mock_send_message

        # REVISE on round 1, PASS on round 2
        verify_results = [
            {
                "verdict": "revise",
                "report": "ISSUES:\n1. Missing citation\n2. Weak argument",
                "issues": ["1. Missing citation"],
            },
            {"verdict": "pass", "report": "Good now", "issues": []},
        ]
        verify_call_index = [0]

        async def mock_verify(content):
            result = verify_results[verify_call_index[0]]
            verify_call_index[0] += 1
            return result

        with patch.object(
            mock_supervisor, "_run_verification", side_effect=mock_verify
        ):
            await mock_supervisor._run_worker_with_verification_loop(
                worker_type="test_worker",
                message_type=MessageType.SUPERVISOR_ASSIGNMENT,
                content="Original task",
            )

            # Assert we sent content twice
            assert len(sent_contents) == 2

            # First call: original content
            first_content = sent_contents[0]
            assert isinstance(first_content, str) and first_content == "Original task"

            # Second call: should have feedback appended
            second_content = sent_contents[1]
            assert isinstance(second_content, TalkHierContent)
            assert "REVISION FEEDBACK" in second_content.content
            assert "Missing citation" in second_content.content

    @pytest.mark.asyncio
    async def test_no_worker_response_returns_neutral_fallback(self, mock_supervisor):
        """Test that if worker returns None, we get neutral PASS fallback."""
        # Mock send_talkhier_message to return None
        mock_supervisor.send_talkhier_message = AsyncMock(return_value=None)

        with patch.object(
            mock_supervisor,
            "_run_verification",
            return_value={"verdict": "pass", "report": "", "issues": []},
        ):
            (
                response,
                verification,
            ) = await mock_supervisor._run_worker_with_verification_loop(
                worker_type="test_worker",
                message_type=MessageType.SUPERVISOR_ASSIGNMENT,
                content="Test task",
            )

            # Assert response is None
            assert response is None

            # Assert we got a neutral pass
            assert verification["verdict"] == "pass"
            assert verification["rounds"] == 1


class TestVerificationAgent:
    """Test the _run_verification method directly."""

    @pytest.mark.asyncio
    async def test_run_verification_pass_verdict(self, mock_supervisor):
        """Test _run_verification extracts PASS verdict correctly."""
        mock_agent_result = AgentResult(
            task_id="test",
            status="success",
            output={
                "content": "VERDICT: PASS\nISSUES: None\n\nAll content looks good."
            },
            confidence=0.9,
            execution_time=1.0,
        )

        with patch("src.agents.factory.AgentFactory") as mock_factory:
            mock_agent = MagicMock()
            mock_agent.execute = AsyncMock(return_value=mock_agent_result)
            mock_factory.create_agent.return_value = mock_agent

            result = await mock_supervisor._run_verification("Test content to verify")

            assert result["verdict"] == "pass"
            assert (
                result["report"]
                == "VERDICT: PASS\nISSUES: None\n\nAll content looks good."
            )
            assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_run_verification_revise_verdict(self, mock_supervisor):
        """Test _run_verification extracts REVISE verdict and issues."""
        mock_agent_result = AgentResult(
            task_id="test",
            status="success",
            output={
                "content": "VERDICT: REVISE\nISSUES:\n1. Missing citation for claim X\n2. Arithmetic error in Table 1"
            },
            confidence=0.9,
            execution_time=1.0,
        )

        with patch("src.agents.factory.AgentFactory") as mock_factory:
            mock_agent = MagicMock()
            mock_agent.execute = AsyncMock(return_value=mock_agent_result)
            mock_factory.create_agent.return_value = mock_agent

            result = await mock_supervisor._run_verification("Test content with issues")

            assert result["verdict"] == "revise"
            assert len(result["issues"]) == 2
            assert "Missing citation" in result["issues"][0]
            assert "Arithmetic error" in result["issues"][1]

    @pytest.mark.asyncio
    async def test_run_verification_empty_content_returns_pass(self, mock_supervisor):
        """Test empty content returns neutral PASS without calling agent."""
        result = await mock_supervisor._run_verification("")

        assert result["verdict"] == "pass"
        assert result["report"] == "No content to verify."
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_run_verification_error_returns_neutral_pass(self, mock_supervisor):
        """Test verification error returns neutral PASS fallback."""
        with patch("src.agents.factory.AgentFactory") as mock_factory:
            mock_factory.create_agent.side_effect = Exception("Service unavailable")

            result = await mock_supervisor._run_verification("Content to verify")

            assert result["verdict"] == "pass"  # Neutral fallback
            assert "Verification error" in result["report"]
            assert result["issues"] == []


class TestIssueExtraction:
    """Test the _extract_issues_from_report helper."""

    def test_extract_numbered_issues(self, mock_supervisor):
        """Test extracting numbered issues from report."""
        report = """
VERDICT: REVISE

ISSUES:
1. Missing citation in paragraph 3
2. Arithmetic error: 2+2=5 should be 4
3. Unsupported claim about market size

Other text here.
"""
        issues = mock_supervisor._extract_issues_from_report(report)

        assert len(issues) == 3
        assert "Missing citation" in issues[0]
        assert "Arithmetic error" in issues[1]
        assert "Unsupported claim" in issues[2]

    def test_extract_no_issues_on_pass(self, mock_supervisor):
        """Test ISSUES: None returns empty list."""
        report = """
VERDICT: PASS
ISSUES: None

Content is excellent.
"""
        issues = mock_supervisor._extract_issues_from_report(report)
        assert issues == []

    def test_extract_issues_with_dashes(self, mock_supervisor):
        """Test extracting issues starting with dashes or bullets."""
        report = """
ISSUES:
- First issue here
- Second issue here
* Third issue here
"""
        issues = mock_supervisor._extract_issues_from_report(report)
        assert len(issues) == 3

    def test_extract_issues_stops_at_next_section(self, mock_supervisor):
        """Test issue extraction stops at next section header."""
        report = """
ISSUES:
1. Issue one
2. Issue two

SUMMARY:
This is summary text, not an issue.
"""
        issues = mock_supervisor._extract_issues_from_report(report)
        assert len(issues) == 2
