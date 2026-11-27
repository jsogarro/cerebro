"""Global audit-event and security-alert reads, superuser only.

Scoped per organization later; until audit rows carry an organization these
are deliberately global and therefore not available to tenant users.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.middleware.auth_middleware import get_current_active_user, require_superuser
from src.models.db.audit_log import AuditEventType, AuditLog
from src.models.db.security_alert import AlertStatus, AlertType, SecurityAlert
from src.models.db.session import get_session
from src.models.db.user import User

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(require_superuser())]
)


@router.get("/audit-events")
async def list_audit_events(
    event_type: AuditEventType | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    query = select(AuditLog).order_by(AuditLog.created_at.desc())
    if event_type is not None:
        query = query.where(AuditLog.event_type == event_type)
    rows = (await db.execute(query.limit(limit).offset(offset))).scalars().all()
    return {"items": [row.to_dict() for row in rows], "limit": limit, "offset": offset}


@router.get("/alerts")
async def list_security_alerts(
    alert_type: AlertType | None = None,
    status: AlertStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    query = select(SecurityAlert).order_by(SecurityAlert.created_at.desc())
    if alert_type is not None:
        query = query.where(SecurityAlert.alert_type == alert_type)
    if status is not None:
        query = query.where(SecurityAlert.status == status)
    rows = (await db.execute(query.limit(limit).offset(offset))).scalars().all()
    return {"items": [row.to_dict() for row in rows], "limit": limit, "offset": offset}


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_security_alert(
    alert_id: UUID,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_active_user),
) -> dict[str, Any]:
    alert = await db.get(SecurityAlert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Security alert not found")
    alert.acknowledge(str(user.id))
    # get_session commits only after the response is sent; commit here so a 200
    # means the row is durable, and a write that matches no row fails the call.
    await db.commit()
    return alert.to_dict()
