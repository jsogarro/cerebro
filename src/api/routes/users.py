"""User privacy and account data routes."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai_brain.memory.multi_tier_memory import MultiTierMemorySystem
from src.auth.jwt_service import JWTService, JWTServiceUnavailableError
from src.middleware.auth_middleware import get_current_active_user, get_jwt_service
from src.models.db.audit_log import AuditEventType
from src.models.db.generated_report import GeneratedReport
from src.models.db.research_project import ResearchProject
from src.models.db.research_result import ResearchResult
from src.models.db.session import SessionDep
from src.models.db.similarity_check import EgressEvent
from src.models.db.uploaded_document import UploadedDocument, UsageLedger
from src.models.db.user import User
from src.models.db.workflow_checkpoint import WorkflowCheckpoint
from src.security.audit_logger import AuditLogger, AuditPersistenceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


async def get_memory_system(request: Request) -> MultiTierMemorySystem | None:
    """Return the app-level memory system when one is attached."""

    memory_system = getattr(request.app.state, "memory_system", None)
    if isinstance(memory_system, MultiTierMemorySystem):
        return memory_system
    return None


async def get_audit_logger(request: Request) -> AuditLogger | None:
    """Return the app-level audit logger when one is attached."""

    audit_logger = getattr(request.app.state, "audit_logger", None)
    return audit_logger if isinstance(audit_logger, AuditLogger) else None


# Stores that hold the user's identifier but are not touched by this route.
_RETAINED = {
    "audit_logs": "retained: the audit trail keeps denormalized username/email and the subject id; user_id is nulled",
    "agent_runs": "retained: run records keep requested_by",
}


@router.delete("/{user_id}/gdpr")
async def delete_user_gdpr(
    user_id: UUID,
    db: AsyncSession = SessionDep,
    memory_system: MultiTierMemorySystem | None = Depends(get_memory_system),
    audit_logger: AuditLogger | None = Depends(get_audit_logger),
    current_user: User = Depends(get_current_active_user),
    jwt_service: JWTService = Depends(get_jwt_service),
) -> dict[str, Any]:
    """Delete user data from database records and memory tiers.

    Only the user themselves or a superuser may erase an account's data.
    """

    if current_user.id != user_id and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to delete another user's data",
        )

    user_id_text = str(user_id)

    # Revoke first: if the store is down nothing has been deleted, so a
    # retry is safe and no erased user's token stays valid.
    try:
        await jwt_service.revoke_all_user_tokens(user_id_text)
    except JWTServiceUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token revocation unavailable; erasure not performed",
        ) from exc

    # Core deletes bypass ORM cascades, and these FKs have no ON DELETE, so
    # delete dependents first. report_formats go via the DB-level cascade.
    project_ids = select(ResearchProject.id).where(
        ResearchProject.user_id == user_id_text
    )
    report_result = await db.execute(
        delete(GeneratedReport).where(
            or_(
                GeneratedReport.user_id == user_id,
                GeneratedReport.project_id.in_(project_ids),
            )
        )
    )
    result_result = await db.execute(
        delete(ResearchResult).where(ResearchResult.project_id.in_(project_ids))
    )
    checkpoint_result = await db.execute(
        delete(WorkflowCheckpoint).where(WorkflowCheckpoint.project_id.in_(project_ids))
    )
    project_result = await db.execute(
        delete(ResearchProject).where(ResearchProject.user_id == user_id_text)
    )
    document_result = await db.execute(
        delete(UploadedDocument).where(UploadedDocument.user_id == user_id)
    )
    ledger_result = await db.execute(
        delete(UsageLedger).where(UsageLedger.user_id == user_id)
    )
    # Checks and matches cascade from the documents; audit rows only detach from a check.
    egress_result = await db.execute(
        delete(EgressEvent).where(EgressEvent.user_id == user_id)
    )
    user_result = await db.execute(delete(User).where(User.id == user_id))

    # With no memory system attached nothing was purged, so say that rather
    # than report zero deletions as a completed erase.
    memory_deleted = (
        await memory_system.purge_user_data(user_id_text)
        if memory_system is not None
        else None
    )

    # Record the erasure only once it is durable, so the audit trail never
    # reports a deletion that rolled back. An audit failure after the commit
    # must not turn a completed erasure into an error response.
    await db.commit()
    audit_recorded = False
    if audit_logger is not None:
        await audit_logger.log_event(
            event_type=AuditEventType.ACCOUNT_DELETED,
            action="account_deleted",
            user_id=user_id,
            resource_type="user",
            resource_id=user_id_text,
            metadata={"erased_by": str(current_user.id)},
        )
        try:
            await audit_logger.flush_buffer()
            audit_recorded = True
        except AuditPersistenceError:
            logger.exception("ACCOUNT_DELETED audit event not persisted")

    not_erased = dict(_RETAINED)
    if memory_system is None:
        not_erased["memory"] = "not_configured: no memory backend is attached"

    return {
        "user_id": user_id_text,
        "not_erased": not_erased,
        "audit_recorded": audit_recorded,
        "memory_purged": memory_system is not None,
        "deleted": {
            # SQLAlchemy 2.x: Result.rowcount is valid for DELETE/UPDATE; mypy stub gap.
            "users": user_result.rowcount or 0,  # type: ignore[attr-defined]
            "research_projects": project_result.rowcount or 0,  # type: ignore[attr-defined]
            "generated_reports": report_result.rowcount or 0,  # type: ignore[attr-defined]
            "research_results": result_result.rowcount or 0,  # type: ignore[attr-defined]
            "workflow_checkpoints": checkpoint_result.rowcount or 0,  # type: ignore[attr-defined]
            "uploaded_documents": document_result.rowcount or 0,  # type: ignore[attr-defined]
            "usage_ledger": ledger_result.rowcount or 0,  # type: ignore[attr-defined]
            "egress_events": egress_result.rowcount or 0,  # type: ignore[attr-defined]
            "memory": memory_deleted,
        },
    }


__all__ = ["get_audit_logger", "get_memory_system", "router"]
