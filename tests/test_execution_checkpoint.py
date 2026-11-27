"""
Tests for execution checkpoint and resume functionality.

Tests the checkpoint storage and resume capability in DirectExecutionService
with mocked repository to avoid requiring a live database.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
    ResumeUnsupportedError,
)
from src.repositories.checkpoint_repository import (
    CheckpointIdentityError,
    CheckpointRepository,
)

ORG = "11111111-1111-1111-1111-111111111111"


def _admitted_run(run_id: str = "run-abc") -> SimpleNamespace:
    """The durable run a checkpoint takes its identity from."""
    return SimpleNamespace(run_id=run_id, tenant_id=ORG)


@pytest.fixture(autouse=True)
def _project_in_caller_org():
    """The project lookup resume performs: a project owned by ``ORG``."""
    with patch(
        "src.api.services.direct_execution_service.ResearchRepository"
    ) as repo_cls:
        repo_cls.return_value.get = AsyncMock(
            return_value=MagicMock(organization_id=uuid.UUID(ORG))
        )
        yield repo_cls


@pytest.fixture
def mock_session_factory():
    """Create a mock async session factory."""

    class MockAsyncSession:
        def __init__(self):
            self.commit = AsyncMock()
            self.flush = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    def factory():
        return MockAsyncSession()

    return factory


@pytest.fixture
def mock_checkpoint_repository():
    """Create a mock CheckpointRepository."""
    repo = AsyncMock(spec=CheckpointRepository)
    repo.create_checkpoint = AsyncMock()
    repo.get_recovery_point = AsyncMock()
    repo.restore_from_checkpoint = AsyncMock()
    return repo


@pytest.fixture
def execution_service_with_checkpoint(mock_session_factory):
    """Create DirectExecutionService with mocked checkpoint dependencies."""
    return DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=AsyncMock(),
        event_publisher=AsyncMock(),
        session_factory=mock_session_factory,
    )


@pytest.mark.asyncio
async def test_checkpoint_with_repository_calls_create_checkpoint(
    execution_service_with_checkpoint, mock_session_factory
):
    """Test that _checkpoint calls create_checkpoint when repository is available."""
    execution_status = ExecutionStatus(
        execution_id="exec-123",
        organization_id=ORG,
        project_id=str(uuid.uuid4()),
        status="running",
        progress_percentage=50.0,
        current_phase="supervisor_execution",
    )
    execution_status._run = _admitted_run()

    with patch(
        "src.api.services.direct_execution_service.CheckpointRepository"
    ) as mock_repo_cls:
        mock_repo_instance = AsyncMock(spec=CheckpointRepository)
        mock_repo_cls.return_value = mock_repo_instance

        await execution_service_with_checkpoint._checkpoint(
            execution_status, "supervisor_execution"
        )

        # Verify create_checkpoint was called
        mock_repo_instance.create_checkpoint.assert_called_once()
        call_kwargs = mock_repo_instance.create_checkpoint.call_args.kwargs

        assert call_kwargs["workflow_id"] == "exec-123"
        assert call_kwargs["phase"] == "supervisor_execution"
        assert call_kwargs["checkpoint_type"] == "automatic"
        assert "checkpoint_data" in call_kwargs
        assert call_kwargs["checkpoint_data"]["status"] == "running"
        assert call_kwargs["checkpoint_data"]["progress_percentage"] == 50.0


@pytest.mark.asyncio
async def test_checkpoint_without_repository_is_noop(execution_service_with_checkpoint):
    """Test that _checkpoint gracefully skips when no repository is available."""
    # Remove session_factory
    execution_service_with_checkpoint.session_factory = None

    execution_status = ExecutionStatus(
        execution_id="exec-123",
        organization_id=ORG,
        project_id=str(uuid.uuid4()),
        status="running",
        progress_percentage=50.0,
        current_phase="supervisor_execution",
    )

    # Should not raise, just return silently
    await execution_service_with_checkpoint._checkpoint(
        execution_status, "supervisor_execution"
    )


@pytest.mark.asyncio
async def test_checkpoint_on_repository_error_degrades_gracefully(
    execution_service_with_checkpoint,
):
    """Test that _checkpoint logs warning but doesn't raise on repository error."""
    execution_status = ExecutionStatus(
        execution_id="exec-123",
        organization_id=ORG,
        project_id=str(uuid.uuid4()),
        status="running",
        progress_percentage=50.0,
        current_phase="supervisor_execution",
    )
    execution_status._run = _admitted_run()

    with patch(
        "src.api.services.direct_execution_service.CheckpointRepository"
    ) as mock_repo_cls:
        mock_repo_instance = AsyncMock(spec=CheckpointRepository)
        mock_repo_instance.create_checkpoint.side_effect = OperationalError(
            "INSERT", {}, Exception("DB connection failed")
        )
        mock_repo_cls.return_value = mock_repo_instance

        # Should not raise, just log warning
        await execution_service_with_checkpoint._checkpoint(
            execution_status, "supervisor_execution"
        )


@pytest.mark.asyncio
async def test_resume_execution_refuses_and_revives_nothing(
    execution_service_with_checkpoint,
):
    """A checkpoint is a diagnostic snapshot: resume raises and registers nothing."""
    with (
        patch(
            "src.api.services.direct_execution_service.CheckpointRepository"
        ) as mock_repo_cls,
        pytest.raises(ResumeUnsupportedError) as refused,
    ):
        await execution_service_with_checkpoint.resume_execution(
            uuid.uuid4(), organization_id=ORG
        )

    assert refused.value.code == "RESUME_UNSUPPORTED"
    mock_repo_cls.assert_not_called()
    assert execution_service_with_checkpoint.active_executions == {}


@pytest.mark.asyncio
async def test_resume_execution_returns_none_without_database(
    execution_service_with_checkpoint,
):
    """Test that resume_execution returns None when database is unavailable."""
    # Remove session_factory
    execution_service_with_checkpoint.session_factory = None

    project_id = uuid.uuid4()
    result = await execution_service_with_checkpoint.resume_execution(
        project_id, organization_id=ORG
    )

    assert result is None


@pytest.mark.asyncio
async def test_checkpoint_captures_all_execution_state(
    execution_service_with_checkpoint,
):
    """Test that checkpoint captures complete execution state."""
    execution_status = ExecutionStatus(
        execution_id="exec-full-123",
        organization_id=ORG,
        project_id=str(uuid.uuid4()),
        status="running",
        progress_percentage=60.0,
        current_phase="hierarchical_coordination",
        routing_decision={"strategy": "quality_focused"},
        supervisor_type="analytics",
        agent_results={"preliminary": "data"},
        quality_scores={"consensus": 0.85},
        final_output=None,
        workers_used=5,
        errors=["minor error"],
        warnings=["performance warning"],
        retry_count=2,
    )
    execution_status._run = _admitted_run()

    with patch(
        "src.api.services.direct_execution_service.CheckpointRepository"
    ) as mock_repo_cls:
        mock_repo_instance = AsyncMock(spec=CheckpointRepository)
        mock_repo_cls.return_value = mock_repo_instance

        await execution_service_with_checkpoint._checkpoint(
            execution_status, "hierarchical_coordination"
        )

        call_kwargs = mock_repo_instance.create_checkpoint.call_args.kwargs
        checkpoint_data = call_kwargs["checkpoint_data"]

        # Verify all state is captured
        assert checkpoint_data["status"] == "running"
        assert checkpoint_data["progress_percentage"] == 60.0
        assert checkpoint_data["current_phase"] == "hierarchical_coordination"
        assert checkpoint_data["routing_decision"] == {"strategy": "quality_focused"}
        assert checkpoint_data["supervisor_type"] == "analytics"
        assert checkpoint_data["agent_results"] == {"preliminary": "data"}
        assert checkpoint_data["quality_scores"] == {"consensus": 0.85}
        assert checkpoint_data["workers_used"] == 5
        assert checkpoint_data["errors"] == ["minor error"]
        assert checkpoint_data["warnings"] == ["performance warning"]
        assert checkpoint_data["retry_count"] == 2


@pytest.mark.asyncio
async def test_checkpoint_links_the_admitted_run(
    execution_service_with_checkpoint,
):
    execution_status = ExecutionStatus(
        execution_id="exec-123",
        organization_id=ORG,
        project_id=str(uuid.uuid4()),
        status="running",
        progress_percentage=50.0,
        current_phase="supervisor_execution",
    )
    execution_status._run = _admitted_run("run-abc")

    with patch(
        "src.api.services.direct_execution_service.CheckpointRepository"
    ) as mock_repo_cls:
        mock_repo_instance = AsyncMock(spec=CheckpointRepository)
        mock_repo_cls.return_value = mock_repo_instance

        await execution_service_with_checkpoint._checkpoint(
            execution_status, "supervisor_execution"
        )

    assert mock_repo_instance.create_checkpoint.call_args.kwargs["run_id"] == "run-abc"


@pytest.mark.asyncio
async def test_checkpoint_takes_its_organization_from_the_run_not_the_status(
    execution_service_with_checkpoint,
):
    execution_status = ExecutionStatus(
        execution_id="exec-org",
        project_id=str(uuid.uuid4()),
        organization_id="spoofed-owner",
        status="running",
        progress_percentage=1.0,
        current_phase="p",
    )
    execution_status._run = _admitted_run()

    with patch(
        "src.api.services.direct_execution_service.CheckpointRepository"
    ) as mock_repo_cls:
        mock_repo_instance = AsyncMock(spec=CheckpointRepository)
        mock_repo_cls.return_value = mock_repo_instance
        await execution_service_with_checkpoint._checkpoint(execution_status, "p")

    assert (
        mock_repo_instance.create_checkpoint.call_args.kwargs["organization_id"] == ORG
    )


@pytest.mark.asyncio
async def test_an_execution_with_no_durable_run_writes_no_checkpoint(
    execution_service_with_checkpoint,
):
    execution_status = ExecutionStatus(
        execution_id="exec-orphan",
        project_id=str(uuid.uuid4()),
        status="running",
        progress_percentage=1.0,
        current_phase="p",
    )

    with patch(
        "src.api.services.direct_execution_service.CheckpointRepository"
    ) as mock_repo_cls:
        mock_repo_instance = AsyncMock(spec=CheckpointRepository)
        mock_repo_cls.return_value = mock_repo_instance
        await execution_service_with_checkpoint._checkpoint(execution_status, "p")

    mock_repo_instance.create_checkpoint.assert_not_called()


@pytest.mark.asyncio
async def test_a_refused_checkpoint_does_not_fail_the_run(
    execution_service_with_checkpoint,
):
    execution_status = ExecutionStatus(
        execution_id="exec-refused",
        project_id=str(uuid.uuid4()),
        organization_id=ORG,
        status="running",
        progress_percentage=1.0,
        current_phase="p",
    )
    execution_status._run = _admitted_run()

    with patch(
        "src.api.services.direct_execution_service.CheckpointRepository"
    ) as mock_repo_cls:
        mock_repo_instance = AsyncMock(spec=CheckpointRepository)
        mock_repo_instance.create_checkpoint.side_effect = CheckpointIdentityError("x")
        mock_repo_cls.return_value = mock_repo_instance
        await execution_service_with_checkpoint._checkpoint(execution_status, "p")


@pytest.mark.asyncio
async def test_a_malformed_project_id_still_raises(execution_service_with_checkpoint):
    execution_status = ExecutionStatus(
        execution_id="exec-bad",
        project_id="not-a-uuid",
        organization_id=ORG,
        status="running",
        progress_percentage=1.0,
        current_phase="p",
    )
    execution_status._run = _admitted_run()

    with pytest.raises(ValueError):
        await execution_service_with_checkpoint._checkpoint(execution_status, "p")
