"""
Tests for Parallel Worker Execution in Supervisors

Tests that SupervisionMode.PARALLEL actually executes workers concurrently
with proper failure isolation, partial results, and semaphore bounds.
"""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from src.agents.communication.talkhier_message import (
    MessageType,
    TalkHierContent,
    TalkHierMessage,
)
from src.agents.supervisors.base_supervisor import SupervisionMode
from src.agents.supervisors.content_supervisor import ContentSupervisor


class TestParallelWorkerExecution:
    """Test suite for parallel worker execution within supervisors."""

    @pytest.fixture
    def mock_supervisor(self):
        """Create a supervisor with mocked send_talkhier_message."""
        supervisor = ContentSupervisor(config={"max_parallel_workers": 2})
        supervisor.send_talkhier_message = AsyncMock()
        return supervisor

    @pytest.mark.asyncio
    async def test_parallel_mode_concurrent_execution(self, mock_supervisor):
        """Test PARALLEL mode executes workers concurrently (wall-clock < sum)."""

        # Mock responses with delays
        async def mock_send_with_delay(*args, **kwargs):
            await asyncio.sleep(0.1)  # 100ms per worker
            return TalkHierMessage(
                from_agent="test_worker",
                to_agent="supervisor",
                message_type=MessageType.SUPERVISOR_ASSIGNMENT,
                content=TalkHierContent(content="mock response"),
            )

        mock_supervisor.send_talkhier_message = mock_send_with_delay

        worker_specs = [
            ("worker1", MessageType.SUPERVISOR_ASSIGNMENT, "task1", None),
            ("worker2", MessageType.SUPERVISOR_ASSIGNMENT, "task2", None),
            ("worker3", MessageType.SUPERVISOR_ASSIGNMENT, "task3", None),
            ("worker4", MessageType.SUPERVISOR_ASSIGNMENT, "task4", None),
        ]

        start = time.perf_counter()
        results = await mock_supervisor.execute_workers_parallel(
            worker_specs, SupervisionMode.PARALLEL
        )
        elapsed = time.perf_counter() - start

        # With max_parallel_workers=2 and 4 workers @ 100ms each:
        # Sequential would take ~400ms
        # Parallel with bound=2 takes ~200ms (2 batches of 2)
        assert len(results) == 4
        assert elapsed < 0.3  # Should be ~200ms, allow 300ms tolerance
        assert all(results.values())  # All succeeded

    @pytest.mark.asyncio
    async def test_parallel_mode_results_aggregation(self, mock_supervisor):
        """Test PARALLEL mode aggregates results correctly by worker type."""
        mock_supervisor.send_talkhier_message = AsyncMock(
            side_effect=lambda worker_type, *args, **kwargs: TalkHierMessage(
                from_agent=worker_type,
                to_agent="supervisor",
                message_type=MessageType.SUPERVISOR_ASSIGNMENT,
                content=TalkHierContent(content=f"response from {worker_type}"),
            )
        )

        worker_specs = [
            ("worker1", MessageType.SUPERVISOR_ASSIGNMENT, "task1", None),
            ("worker2", MessageType.SUPERVISOR_ASSIGNMENT, "task2", None),
            ("worker3", MessageType.SUPERVISOR_ASSIGNMENT, "task3", None),
        ]

        results = await mock_supervisor.execute_workers_parallel(
            worker_specs, SupervisionMode.PARALLEL
        )

        assert len(results) == 3
        assert set(results.keys()) == {"worker1", "worker2", "worker3"}
        assert results["worker1"].talkhier_content.content == "response from worker1"
        assert results["worker2"].talkhier_content.content == "response from worker2"
        assert results["worker3"].talkhier_content.content == "response from worker3"

    @pytest.mark.asyncio
    async def test_partial_failure_one_worker_fails(self, mock_supervisor):
        """Test partial failure: 1 of 4 workers fails, 3 succeed."""

        async def mock_send_with_one_failure(worker_type, *args, **kwargs):
            if worker_type == "worker2":
                raise RuntimeError("Worker 2 failed")
            return TalkHierMessage(
                from_agent=worker_type,
                to_agent="supervisor",
                message_type=MessageType.SUPERVISOR_ASSIGNMENT,
                content=TalkHierContent(content=f"success from {worker_type}"),
            )

        mock_supervisor.send_talkhier_message = mock_send_with_one_failure

        worker_specs = [
            ("worker1", MessageType.SUPERVISOR_ASSIGNMENT, "task1", None),
            ("worker2", MessageType.SUPERVISOR_ASSIGNMENT, "task2", None),
            ("worker3", MessageType.SUPERVISOR_ASSIGNMENT, "task3", None),
            ("worker4", MessageType.SUPERVISOR_ASSIGNMENT, "task4", None),
        ]

        results = await mock_supervisor.execute_workers_parallel(
            worker_specs, SupervisionMode.PARALLEL
        )

        # Should have 3 successful results, worker2 excluded
        assert len(results) == 3
        assert "worker1" in results
        assert "worker2" not in results  # Failed worker excluded
        assert "worker3" in results
        assert "worker4" in results

    @pytest.mark.asyncio
    async def test_all_workers_fail(self, mock_supervisor):
        """Test all workers fail: graceful degradation returns empty dict."""
        mock_supervisor.send_talkhier_message = AsyncMock(
            side_effect=RuntimeError("All workers fail")
        )

        worker_specs = [
            ("worker1", MessageType.SUPERVISOR_ASSIGNMENT, "task1", None),
            ("worker2", MessageType.SUPERVISOR_ASSIGNMENT, "task2", None),
        ]

        results = await mock_supervisor.execute_workers_parallel(
            worker_specs, SupervisionMode.PARALLEL
        )

        # Graceful degradation: empty dict
        assert results == {}

    @pytest.mark.asyncio
    async def test_semaphore_bound_respected(self, mock_supervisor):
        """Test max_parallel_workers bound is respected."""
        # Track concurrent execution
        concurrent_count = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        async def mock_send_with_tracking(*args, **kwargs):
            nonlocal concurrent_count, max_concurrent
            async with lock:
                concurrent_count += 1
                max_concurrent = max(max_concurrent, concurrent_count)

            await asyncio.sleep(0.05)  # Simulate work

            async with lock:
                concurrent_count -= 1

            return TalkHierMessage(
                from_agent="test",
                to_agent="supervisor",
                message_type=MessageType.SUPERVISOR_ASSIGNMENT,
                content=TalkHierContent(content="success"),
            )

        mock_supervisor.send_talkhier_message = mock_send_with_tracking

        worker_specs = [
            (f"worker{i}", MessageType.SUPERVISOR_ASSIGNMENT, f"task{i}", None)
            for i in range(4)
        ]

        # max_parallel_workers=2, so max concurrent should be <= 2
        await mock_supervisor.execute_workers_parallel(
            worker_specs, SupervisionMode.PARALLEL
        )

        assert max_concurrent <= 2

    @pytest.mark.asyncio
    async def test_sequential_mode_unchanged(self, mock_supervisor):
        """Test SEQUENTIAL mode runs workers one at a time."""
        call_order = []

        async def mock_send_ordered(worker_type, *args, **kwargs):
            call_order.append(worker_type)
            await asyncio.sleep(0.01)  # Small delay
            return TalkHierMessage(
                from_agent=worker_type,
                to_agent="supervisor",
                message_type=MessageType.SUPERVISOR_ASSIGNMENT,
                content=TalkHierContent(content="success"),
            )

        mock_supervisor.send_talkhier_message = mock_send_ordered

        worker_specs = [
            ("worker1", MessageType.SUPERVISOR_ASSIGNMENT, "task1", None),
            ("worker2", MessageType.SUPERVISOR_ASSIGNMENT, "task2", None),
            ("worker3", MessageType.SUPERVISOR_ASSIGNMENT, "task3", None),
        ]

        results = await mock_supervisor.execute_workers_parallel(
            worker_specs, SupervisionMode.SEQUENTIAL
        )

        # Sequential execution preserves order
        assert call_order == ["worker1", "worker2", "worker3"]
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_sequential_mode_handles_failure(self, mock_supervisor):
        """Test SEQUENTIAL mode sets None for failed workers."""

        async def mock_send_with_failure(worker_type, *args, **kwargs):
            if worker_type == "worker2":
                raise RuntimeError("Worker 2 fails")
            return TalkHierMessage(
                from_agent=worker_type,
                to_agent="supervisor",
                message_type=MessageType.SUPERVISOR_ASSIGNMENT,
                content=TalkHierContent(content="success"),
            )

        mock_supervisor.send_talkhier_message = mock_send_with_failure

        worker_specs = [
            ("worker1", MessageType.SUPERVISOR_ASSIGNMENT, "task1", None),
            ("worker2", MessageType.SUPERVISOR_ASSIGNMENT, "task2", None),
            ("worker3", MessageType.SUPERVISOR_ASSIGNMENT, "task3", None),
        ]

        results = await mock_supervisor.execute_workers_parallel(
            worker_specs, SupervisionMode.SEQUENTIAL
        )

        # Sequential mode includes all workers, failed one has None
        assert len(results) == 3
        assert results["worker1"] is not None
        assert results["worker2"] is None  # Failed worker
        assert results["worker3"] is not None

    @pytest.mark.asyncio
    async def test_revision_loop_compatibility_parallel(self, mock_supervisor):
        """Test verification re-run with PARALLEL mode also runs parallel."""
        # This tests that the revision loop respects the parallel mode
        # We simulate a re-run by calling execute_workers_parallel twice

        call_counts = {"round1": 0, "round2": 0}

        async def mock_send_round_tracker(*args, **kwargs):
            # Track which round we're in based on call count
            total = call_counts["round1"] + call_counts["round2"]
            if total < 3:
                call_counts["round1"] += 1
                round_key = "round1"
            else:
                call_counts["round2"] += 1
                round_key = "round2"

            await asyncio.sleep(0.01)
            return TalkHierMessage(
                from_agent="worker",
                to_agent="supervisor",
                message_type=MessageType.SUPERVISOR_ASSIGNMENT,
                content=TalkHierContent(content=f"{round_key} response"),
            )

        mock_supervisor.send_talkhier_message = mock_send_round_tracker

        worker_specs = [
            ("worker1", MessageType.SUPERVISOR_ASSIGNMENT, "task1", None),
            ("worker2", MessageType.SUPERVISOR_ASSIGNMENT, "task2", None),
            ("worker3", MessageType.SUPERVISOR_ASSIGNMENT, "task3", None),
        ]

        # First round (initial execution)
        results_r1 = await mock_supervisor.execute_workers_parallel(
            worker_specs, SupervisionMode.PARALLEL
        )

        # Second round (after REVISE verdict)
        results_r2 = await mock_supervisor.execute_workers_parallel(
            worker_specs, SupervisionMode.PARALLEL
        )

        # Both rounds should execute all workers in parallel
        assert len(results_r1) == 3
        assert len(results_r2) == 3
        assert call_counts["round1"] == 3
        assert call_counts["round2"] == 3
