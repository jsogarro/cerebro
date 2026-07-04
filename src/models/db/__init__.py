"""
Database models package.

This package contains all SQLAlchemy ORM models for the
Multi-Agent Research Platform.
"""

from src.models.db.agent_task import AgentTask
from src.models.db.api_key import APIKey
from src.models.db.audit_log import AuditEventType, AuditLog, AuditSeverity
from src.models.db.base import Base, BaseModel
from src.models.db.mfa_settings import MFAMethod, MFASettings
from src.models.db.oauth_account import OAuthAccount, OAuthProvider
from src.models.db.password_history import PasswordHistory
from src.models.db.research_project import ResearchProject
from src.models.db.research_result import ResearchResult
from src.models.db.security_alert import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    SecurityAlert,
)
from src.models.db.user import User
from src.models.db.user_session import UserSession
from src.models.db.workflow_checkpoint import WorkflowCheckpoint

__all__ = [
    "APIKey",
    "AgentTask",
    "AlertSeverity",
    "AlertStatus",
    "AlertType",
    "AuditEventType",
    "AuditLog",
    "AuditSeverity",
    "Base",
    "BaseModel",
    "MFAMethod",
    "MFASettings",
    "OAuthAccount",
    "OAuthProvider",
    "PasswordHistory",
    "ResearchProject",
    "ResearchResult",
    "SecurityAlert",
    "User",
    "UserSession",
    "WorkflowCheckpoint",
]
