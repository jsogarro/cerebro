"""
Database models package.

This package contains all SQLAlchemy ORM models for the
Multi-Agent Research Platform.
"""

from src.models.db.append_only import AppendOnlyViolationError
from src.models.db.artifact import AgentArtifact
from src.models.db.audit_log import AuditEventType, AuditLog, AuditSeverity
from src.models.db.base import Base, BaseModel
from src.models.db.capability import AgentCapabilityApproval, AgentCapabilityGrant
from src.models.db.claim_support import AgentClaimSupport
from src.models.db.document_critique import CritiqueFinding, DocumentCritique
from src.models.db.evaluation_result import AgentEvaluationResult
from src.models.db.evidence import AgentEvidence
from src.models.db.execution_authority import AgentExecutionAuthority
from src.models.db.experiment import (
    Experiment,
    ExperimentAnalysis,
    ExperimentAssignment,
    ExperimentResult,
    ExperimentVariant,
)
from src.models.db.generated_report import GeneratedReport, ReportFormat
from src.models.db.model_call_reservation import ModelCallReservation
from src.models.db.oauth_account import OAuthAccount, OAuthProvider
from src.models.db.personal_access_token import PersonalAccessToken
from src.models.db.research_project import ResearchProject
from src.models.db.research_result import ResearchResult
from src.models.db.run_config_snapshot import AgentRunConfigSnapshot
from src.models.db.run_event import (
    AgentRunEvent,
    AgentRunEventOutbox,
    EventDeliveryStatus,
)
from src.models.db.run_lifecycle import AgentRun, AgentRunTask, AgentTaskAttempt
from src.models.db.security_alert import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    SecurityAlert,
)
from src.models.db.similarity_check import (
    EgressEvent,
    SimilarityCheck,
    SimilarityMatch,
)
from src.models.db.tool_invocation import AgentToolInvocation
from src.models.db.uploaded_document import UploadedDocument, UsageLedger
from src.models.db.user import User
from src.models.db.workflow_checkpoint import WorkflowCheckpoint

__all__ = [
    "AgentArtifact",
    "AgentCapabilityApproval",
    "AgentCapabilityGrant",
    "AgentClaimSupport",
    "AgentEvaluationResult",
    "AgentEvidence",
    "AgentExecutionAuthority",
    "AgentRun",
    "AgentRunConfigSnapshot",
    "AgentRunEvent",
    "AgentRunEventOutbox",
    "AgentRunTask",
    "AgentTaskAttempt",
    "AgentToolInvocation",
    "AlertSeverity",
    "AlertStatus",
    "AlertType",
    "AppendOnlyViolationError",
    "AuditEventType",
    "AuditLog",
    "AuditSeverity",
    "Base",
    "BaseModel",
    "CritiqueFinding",
    "DocumentCritique",
    "EgressEvent",
    "EventDeliveryStatus",
    "Experiment",
    "ExperimentAnalysis",
    "ExperimentAssignment",
    "ExperimentResult",
    "ExperimentVariant",
    "GeneratedReport",
    "ModelCallReservation",
    "OAuthAccount",
    "OAuthProvider",
    "PersonalAccessToken",
    "ReportFormat",
    "ResearchProject",
    "ResearchResult",
    "SecurityAlert",
    "SimilarityCheck",
    "SimilarityMatch",
    "UploadedDocument",
    "UsageLedger",
    "User",
    "WorkflowCheckpoint",
]
