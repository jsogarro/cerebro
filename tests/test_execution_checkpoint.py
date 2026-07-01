"""
Tests for execution checkpoint and resume functionality.

Tests the checkpoint storage and resume capability in DirectExecutionService
with mocked repository to avoid requiring a live database.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
)
from src.models.db.workflow_checkpoint import WorkflowCheckpoint
from src.repositories.checkpoint_repository import CheckpointRepository


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
    service = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=AsyncMock(),
        event_publisher=AsyncMock(),
        session_factory=mock_session_factory,
    )
    return service


@pytest.mark.asyncio
async def test_checkpoint_with_repository_calls_create_checkpoint(
    execution_service_with_checkpoint, mock_session_factory
):
    """Test that _checkpoint calls create_checkpoint when repository is available."""
    execution_status = ExecutionStatus(
        execution_id="exec-123",
        project_id=str(uuid.uuid4()),
        status="running",
        progress_percentage=50.0,
        current_phase="supervisor_execution",
    )

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
        project_id=str(uuid.uuid4()),
        status="running",
        progress_percentage=50.0,
        current_phase="supervisor_execution",
    )

    with patch(
        "src.api.services.direct_execution_service.CheckpointRepository"
    ) as mock_repo_cls:
        mock_repo_instance = AsyncMock(spec=CheckpointRepository)
        mock_repo_instance.create_checkpoint.side_effect = Exception(
            "DB connection failed"
        )
        mock_repo_cls.return_value = mock_repo_instance

        # Should not raise, just log warning
        await execution_service_with_checkpoint._checkpoint(
            execution_status, "supervisor_execution"
        )


@pytest.mark.asyncio
async def test_resume_execution_restores_from_checkpoint(
    execution_service_with_checkpoint, mock_session_factory
):
    """Test that resume_execution restores ExecutionStatus from checkpoint."""
    project_id = uuid.uuid4()
    execution_id = "exec-restored-123"

    # Mock checkpoint data
    checkpoint_data = {
        "status": "running",
        "progress_percentage": 75.0,
        "current_phase": "result_processing",
        "routing_decision": {"supervisor": "research"},
        "supervisor_type": "research",
        "agent_results": {"key": "value"},
        "quality_scores": {"overall": 0.9},
        "final_output": None,
        "workers_used": 3,
        "errors": [],
        "warnings": [],
        "retry_count": 1,
    }

    restored_data = {
        "checkpoint_id": str(uuid.uuid4()),
        "workflow_id": execution_id,
        "project_id": str(project_id),
        "phase": "result_processing",
        "checkpoint_data": checkpoint_data,
        "recovery_metadata": {},
        "created_at": datetime.now(UTC).isoformat(),
    }

    mock_checkpoint = MagicMock(spec=WorkflowCheckpoint)
    mock_checkpoint.id = uuid.uuid4()

    with patch(
        "src.api.services.direct_execution_service.CheckpointRepository"
    ) as mock_repo_cls:
        mock_repo_instance = AsyncMock(spec=CheckpointRepository)
        mock_repo_instance.get_recovery_point.return_value = mock_checkpoint
        mock_repo_instance.restore_from_checkpoint.return_value = restored_data
        mock_repo_cls.return_value = mock_repo_instance

        result_execution_id = await execution_service_with_checkpoint.resume_execution(
            project_id
        )

        # Verify execution was resumed
        assert result_execution_id == execution_id
        assert execution_id in execution_service_with_checkpoint.active_executions

        # Verify ExecutionStatus was rebuilt correctly
        restored_status = execution_service_with_checkpoint.active_executions[
            execution_id
        ]
        assert restored_status.execution_id == execution_id
        assert restored_status.project_id == str(project_id)
        assert restored_status.status == "running"
        assert restored_status.progress_percentage == 75.0
        assert restored_status.current_phase == "result_processing"
        assert restored_status.supervisor_type == "research"
        assert restored_status.workers_used == 3
        assert restored_status.retry_count == 1


@pytest.mark.asyncio
async def test_resume_execution_returns_none_when_no_checkpoint(
    execution_service_with_checkpoint,
):
    """Test that resume_execution returns None when no checkpoint exists."""
    project_id = uuid.uuid4()

    with patch(
        "src.api.services.direct_execution_service.CheckpointRepository"
    ) as mock_repo_cls:
        mock_repo_instance = AsyncMock(spec=CheckpointRepository)
        mock_repo_instance.get_recovery_point.return_value = None
        mock_repo_cls.return_value = mock_repo_instance

        result = await execution_service_with_checkpoint.resume_execution(project_id)

        assert result is None


@pytest.mark.asyncio
async def test_resume_execution_returns_none_without_database(
    execution_service_with_checkpoint,
):
    """Test that resume_execution returns None when database is unavailable."""
    # Remove session_factory
    execution_service_with_checkpoint.session_factory = None

    project_id = uuid.uuid4()
    result = await execution_service_with_checkpoint.resume_execution(project_id)

    assert result is None


@pytest.mark.asyncio
async def test_resume_execution_handles_restore_failure(
    execution_service_with_checkpoint,
):
    """Test that resume_execution handles restore_from_checkpoint returning None."""
    project_id = uuid.uuid4()

    mock_checkpoint = MagicMock(spec=WorkflowCheckpoint)
    mock_checkpoint.id = uuid.uuid4()

    with patch(
        "src.api.services.direct_execution_service.CheckpointRepository"
    ) as mock_repo_cls:
        mock_repo_instance = AsyncMock(spec=CheckpointRepository)
        mock_repo_instance.get_recovery_point.return_value = mock_checkpoint
        mock_repo_instance.restore_from_checkpoint.return_value = None
        mock_repo_cls.return_value = mock_repo_instance

        result = await execution_service_with_checkpoint.resume_execution(project_id)

        assert result is None


@pytest.mark.asyncio
async def test_checkpoint_captures_all_execution_state(
    execution_service_with_checkpoint,
):
    """Test that checkpoint captures complete execution state."""
    execution_status = ExecutionStatus(
        execution_id="exec-full-123",
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
