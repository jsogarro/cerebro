"""
Repository pattern implementation for data access.

This package provides clean data access through repository pattern.
"""

from src.repositories.api_key_repository import APIKeyRepository
from src.repositories.base import BaseRepository
from src.repositories.checkpoint_repository import CheckpointRepository
from src.repositories.research_repository import ResearchRepository
from src.repositories.result_repository import ResultRepository
from src.repositories.run_config_snapshot_repository import (
    RunConfigSnapshotRepository,
    hash_configuration,
)
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from src.repositories.task_repository import TaskRepository
from src.repositories.tenant_scope import (
    MissingOrganizationContextError,
    TenantMismatchError,
    enforce_tenant_identity,
    normalize_organization_id,
)
from src.repositories.user_repository import UserRepository

__all__ = [
    "APIKeyRepository",
    "BaseRepository",
    "CheckpointRepository",
    "MissingOrganizationContextError",
    "ResearchRepository",
    "ResultRepository",
    "RunConfigSnapshotRepository",
    "RunEventRepository",
    "RunLifecycleRepository",
    "TaskRepository",
    "TenantMismatchError",
    "UserRepository",
    "enforce_tenant_identity",
    "hash_configuration",
    "normalize_organization_id",
]
