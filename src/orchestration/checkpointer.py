"""
Checkpointing and state persistence for LangGraph workflows.

This module provides mechanisms for saving and restoring workflow state,
enabling recovery from failures and resumption of long-running workflows.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

import redis.asyncio as redis

from src.orchestration.state import ResearchState, StateCheckpoint, WorkflowPhase

logger = logging.getLogger(__name__)


class CheckpointStorage:
    """Base class for checkpoint storage backends."""

    async def save(self, checkpoint_id: str, checkpoint: StateCheckpoint) -> bool:
        """Save a checkpoint."""
        raise NotImplementedError

    async def load(self, checkpoint_id: str) -> StateCheckpoint | None:
        """Load a checkpoint."""
        raise NotImplementedError

    async def list_checkpoints(self, workflow_id: str) -> list[str]:
        """List all checkpoints for a workflow."""
        raise NotImplementedError

    async def delete(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint."""
        raise NotImplementedError


class MemoryCheckpointStorage(CheckpointStorage):
    """In-memory checkpoint storage for testing."""

    def __init__(self):
        """Initialize memory storage."""
        self.checkpoints: dict[str, StateCheckpoint] = {}

    async def save(self, checkpoint_id: str, checkpoint: StateCheckpoint) -> bool:
        """Save checkpoint to memory."""
        self.checkpoints[checkpoint_id] = checkpoint
        return True

    async def load(self, checkpoint_id: str) -> StateCheckpoint | None:
        """Load checkpoint from memory."""
        return self.checkpoints.get(checkpoint_id)

    async def list_checkpoints(self, workflow_id: str) -> list[str]:
        """List checkpoints for workflow."""
        return [cid for cid in self.checkpoints.keys() if cid.startswith(workflow_id)]

    async def delete(self, checkpoint_id: str) -> bool:
        """Delete checkpoint from memory."""
        if checkpoint_id in self.checkpoints:
            del self.checkpoints[checkpoint_id]
            return True
        return False


class FileCheckpointStorage(CheckpointStorage):
    """File-based checkpoint storage."""

    def __init__(self, base_path: str = "/tmp/langgraph_checkpoints"):
        """
        Initialize file storage.

        Args:
            base_path: Base directory for checkpoints
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_checkpoint_path(self, checkpoint_id: str) -> Path:
        """Get file path for checkpoint."""
        return self.base_path / f"{checkpoint_id}.checkpoint"

    async def save(self, checkpoint_id: str, checkpoint: StateCheckpoint) -> bool:
        """Save checkpoint to file."""
        try:
            path = self._get_checkpoint_path(checkpoint_id)

            # Convert to dict for JSON serialization
            checkpoint_data = checkpoint.to_dict()

            # Write to file
            with open(path, "w") as f:
                json.dump(checkpoint_data, f, indent=2, default=str)

            logger.info(f"Saved checkpoint to {path}")
            return True

        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
            return False

    async def load(self, checkpoint_id: str) -> StateCheckpoint | None:
        """Load checkpoint from file."""
        try:
            path = self._get_checkpoint_path(checkpoint_id)

            if not path.exists():
                return None

            with open(path) as f:
                checkpoint_data = json.load(f)

            # Reconstruct StateCheckpoint
            # Note: This is simplified - full implementation would need proper deserialization
            return StateCheckpoint(
                checkpoint_id=checkpoint_data["checkpoint_id"],
                phase=WorkflowPhase(checkpoint_data["phase"]),
                timestamp=datetime.fromisoformat(checkpoint_data["timestamp"]),
                state_data=checkpoint_data["state_data"],
                agent_states=checkpoint_data["agent_states"],
                metadata=checkpoint_data.get("metadata", {}),
            )

        except Exception as e:
            logger.error(f"Failed to load checkpoint: {e}")
            return None

    async def list_checkpoints(self, workflow_id: str) -> list[str]:
        """List checkpoints for workflow."""
        checkpoints = []

        for path in self.base_path.glob(f"{workflow_id}*.checkpoint"):
            checkpoint_id = path.stem
            checkpoints.append(checkpoint_id)

        return sorted(checkpoints)

    async def delete(self, checkpoint_id: str) -> bool:
        """Delete checkpoint file."""
        try:
            path = self._get_checkpoint_path(checkpoint_id)

            if path.exists():
                path.unlink()
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to delete checkpoint: {e}")
            return False


class RedisCheckpointStorage(CheckpointStorage):
    """Redis-based checkpoint storage for distributed systems."""

    def __init__(self, redis_client: redis.Redis, ttl: int = 86400):
        """
        Initialize Redis storage.

        Args:
            redis_client: Redis client instance
            ttl: Time to live for checkpoints in seconds (default 24 hours)
        """
        self.redis_client = redis_client
        self.ttl = ttl
        self.prefix = "checkpoint:"

    def _get_key(self, checkpoint_id: str) -> str:
        """Get Redis key for checkpoint."""
        return f"{self.prefix}{checkpoint_id}"

    async def save(self, checkpoint_id: str, checkpoint: StateCheckpoint) -> bool:
        """Save checkpoint to Redis."""
        try:
            key = self._get_key(checkpoint_id)

            # Serialize checkpoint
            checkpoint_data = json.dumps(checkpoint.to_dict(), default=str)

            # Save with TTL
            await self.redis_client.setex(key, self.ttl, checkpoint_data)

            # Add to workflow index
            workflow_id = checkpoint_id.split("-")[0]
            index_key = f"{self.prefix}index:{workflow_id}"
            await self.redis_client.sadd(index_key, checkpoint_id)
            await self.redis_client.expire(index_key, self.ttl)

            logger.info(f"Saved checkpoint to Redis: {checkpoint_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to save checkpoint to Redis: {e}")
            return False

    async def load(self, checkpoint_id: str) -> StateCheckpoint | None:
        """Load checkpoint from Redis."""
        try:
            key = self._get_key(checkpoint_id)

            checkpoint_data = await self.redis_client.get(key)

            if not checkpoint_data:
                return None

            # Deserialize
            data = json.loads(checkpoint_data)

            # Reconstruct StateCheckpoint
            return StateCheckpoint(
                checkpoint_id=data["checkpoint_id"],
                phase=WorkflowPhase(data["phase"]),
                timestamp=datetime.fromisoformat(data["timestamp"]),
                state_data=data["state_data"],
                agent_states=data["agent_states"],
                metadata=data.get("metadata", {}),
            )

        except Exception as e:
            logger.error(f"Failed to load checkpoint from Redis: {e}")
            return None

    async def list_checkpoints(self, workflow_id: str) -> list[str]:
        """List checkpoints for workflow."""
        try:
            index_key = f"{self.prefix}index:{workflow_id}"
            checkpoints = await self.redis_client.smembers(index_key)
            return sorted(
                [cp.decode() if isinstance(cp, bytes) else cp for cp in checkpoints]
            )

        except Exception as e:
            logger.error(f"Failed to list checkpoints: {e}")
            return []

    async def delete(self, checkpoint_id: str) -> bool:
        """Delete checkpoint from Redis."""
        try:
            key = self._get_key(checkpoint_id)

            # Remove from storage
            result = await self.redis_client.delete(key)

            # Remove from index
            workflow_id = checkpoint_id.split("-")[0]
            index_key = f"{self.prefix}index:{workflow_id}"
            await self.redis_client.srem(index_key, checkpoint_id)

            return result > 0

        except Exception as e:
            logger.error(f"Failed to delete checkpoint: {e}")
            return False


class WorkflowCheckpointer:
    """
    Main checkpointer for workflow state management.

    Coordinates checkpoint creation, restoration, and cleanup.
    """

    def __init__(
        self,
        storage: CheckpointStorage,
        auto_checkpoint_interval: int = 5,
        max_checkpoints_per_workflow: int = 10,
    ):
        """
        Initialize workflow checkpointer.

        Args:
            storage: Checkpoint storage backend
            auto_checkpoint_interval: Auto-checkpoint every N nodes
            max_checkpoints_per_workflow: Maximum checkpoints to retain
        """
        self.storage = storage
        self.auto_checkpoint_interval = auto_checkpoint_interval
        self.max_checkpoints_per_workflow = max_checkpoints_per_workflow
        self._checkpoint_counter: dict[str, int] = {}

    async def create_checkpoint(self, state: ResearchState) -> str | None:
        """
        Create a checkpoint for the current state.

        Args:
            state: Current workflow state

        Returns:
            Checkpoint ID if successful
        """
        try:
            # Create checkpoint
            checkpoint = state.create_checkpoint()

            # Save to storage
            success = await self.storage.save(checkpoint.checkpoint_id, checkpoint)

            if success:
                # Update counter
                self._checkpoint_counter[state.workflow_id] = (
                    self._checkpoint_counter.get(state.workflow_id, 0) + 1
                )

                # Cleanup old checkpoints if needed
                await self._cleanup_old_checkpoints(state.workflow_id)

                logger.info(f"Created checkpoint: {checkpoint.checkpoint_id}")
                return checkpoint.checkpoint_id

            return None

        except Exception as e:
            logger.error(f"Failed to create checkpoint: {e}")
            return None

    async def restore_checkpoint(
        self, checkpoint_id: str, state: ResearchState
    ) -> bool:
        """
        Restore state from a checkpoint.

        Args:
            checkpoint_id: Checkpoint to restore
            state: State object to restore into

        Returns:
            True if restoration successful
        """
        try:
            checkpoint = await self.storage.load(checkpoint_id)

            if not checkpoint:
                logger.warning(f"Checkpoint not found: {checkpoint_id}")
                return False

            # Restore state
            state.restore_from_checkpoint(checkpoint)

            logger.info(f"Restored from checkpoint: {checkpoint_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to restore checkpoint: {e}")
            return False

    async def list_workflow_checkpoints(self, workflow_id: str) -> list[str]:
        """
        List all checkpoints for a workflow.

        Args:
            workflow_id: Workflow identifier

        Returns:
            List of checkpoint IDs
        """
        return await self.storage.list_checkpoints(workflow_id)

    async def get_latest_checkpoint(
        self, workflow_id: str
    ) -> StateCheckpoint | None:
        """
        Get the latest checkpoint for a workflow.

        Args:
            workflow_id: Workflow identifier

        Returns:
            Latest checkpoint or None
        """
        checkpoints = await self.list_workflow_checkpoints(workflow_id)

        if not checkpoints:
            return None

        # Get the latest (checkpoints are sorted)
        latest_id = checkpoints[-1]
        return await self.storage.load(latest_id)

    async def should_checkpoint(self, state: ResearchState) -> bool:
        """
        Determine if automatic checkpoint should be created.

        Args:
            state: Current workflow state

        Returns:
            True if checkpoint should be created
        """
        # Check if it's time for auto-checkpoint
        node_count = state.metadata.total_nodes_executed

        if node_count % self.auto_checkpoint_interval == 0:
            return True

        # Also checkpoint on phase transitions and errors
        return state.should_checkpoint()

    async def _cleanup_old_checkpoints(self, workflow_id: str):
        """
        Clean up old checkpoints to maintain storage limits.

        Args:
            workflow_id: Workflow identifier
        """
        try:
            checkpoints = await self.list_workflow_checkpoints(workflow_id)

            if len(checkpoints) > self.max_checkpoints_per_workflow:
                # Delete oldest checkpoints
                to_delete = checkpoints[: -self.max_checkpoints_per_workflow]

                for checkpoint_id in to_delete:
                    await self.storage.delete(checkpoint_id)
                    logger.debug(f"Deleted old checkpoint: {checkpoint_id}")

        except Exception as e:
            logger.error(f"Failed to cleanup checkpoints: {e}")

    async def cleanup_workflow(self, workflow_id: str):
        """
        Clean up all checkpoints for a workflow.

        Args:
            workflow_id: Workflow identifier
        """
        try:
            checkpoints = await self.list_workflow_checkpoints(workflow_id)

            for checkpoint_id in checkpoints:
                await self.storage.delete(checkpoint_id)

            # Clean up counter
            if workflow_id in self._checkpoint_counter:
                del self._checkpoint_counter[workflow_id]

            logger.info(
                f"Cleaned up {len(checkpoints)} checkpoints for workflow {workflow_id}"
            )

        except Exception as e:
            logger.error(f"Failed to cleanup workflow: {e}")


__all__ = [
    "CheckpointStorage",
    "FileCheckpointStorage",
    "MemoryCheckpointStorage",
    "RedisCheckpointStorage",
    "WorkflowCheckpointer",
]
