"""
Audit logger service.

Provides comprehensive audit logging for security events, user actions,
and system activities with async database persistence.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID

import structlog
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.db.audit_log import AuditEventType, AuditLog, AuditSeverity
from src.models.db.security_alert import AlertSeverity, AlertType, SecurityAlert
from src.services.cache.cache_manager import CacheManager


class AuditPersistenceError(RuntimeError):
    """Raised when a buffered audit event cannot reach durable storage."""


def _json_safe_value(value: Any) -> Any:
    """Convert nested audit evidence into values accepted by PostgreSQL JSON."""
    if isinstance(value, Enum):
        return _json_safe_value(value.value)
    if isinstance(value, (datetime, UUID)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class AuditLogger:
    """
    Centralized audit logging service.

    Handles security event logging, compliance tracking, and
    forensic analysis support with async database operations.
    """

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        cache_manager: CacheManager | None = None,
        buffer_size: int = 1000,
        flush_interval: int = 5,
        alert_threshold: dict[str, int] | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ):
        """
        Initialize audit logger.

        Args:
            db_session: Async database session
            cache_manager: Cache manager for performance
            buffer_size: Size of log buffer before flush
            flush_interval: Seconds between buffer flushes
            alert_threshold: Thresholds for generating alerts
            session_factory: Factory used to open a fresh session per flush.
                Application-wide loggers must use this instead of sharing one
                ``AsyncSession`` across concurrent requests.
        """
        self.db_session = db_session
        self.session_factory = session_factory
        self.cache = cache_manager
        self.buffer_size = buffer_size
        self.flush_interval = flush_interval
        self.alert_threshold = alert_threshold or self._default_alert_thresholds()

        # Structured logger
        self.logger = structlog.get_logger(__name__)

        # In-memory buffer for batch writes
        self.log_buffer: list[dict[str, Any]] = []
        self.buffer_lock = threading.Lock()

        # Background task for periodic flushing
        self.flush_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()

        # Metrics
        self.metrics = {
            "events_logged": 0,
            "events_flushed": 0,
            "alerts_generated": 0,
            "errors": 0,
        }

    def _default_alert_thresholds(self) -> dict[str, int]:
        """Get default alert thresholds."""
        return {
            "failed_login_attempts": 5,  # Alert after 5 failed logins
            "rate_limit_exceeded": 10,  # Alert after 10 rate limit hits
            "suspicious_activity": 3,  # Alert after 3 suspicious events
            "unauthorized_access": 1,  # Alert immediately
            "data_exfiltration": 1,  # Alert immediately
            "sql_injection_attempt": 1,  # Alert immediately
            "xss_attempt": 1,  # Alert immediately
        }

    async def start(self) -> None:
        """Start the audit logger background tasks."""
        if not self.flush_task:
            self.flush_task = asyncio.create_task(self._periodic_flush())
            self.logger.info("Audit logger started")

    async def stop(self) -> None:
        """Stop the audit logger and flush remaining logs."""
        if self.flush_task:
            self.flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.flush_task

        # Final flush
        await self.flush_buffer()
        self.logger.info("Audit logger stopped", metrics=self.metrics)

    async def log_event(
        self,
        event_type: AuditEventType,
        action: str,
        request: Request | None = None,
        user_id: str | None = None,
        username: str | None = None,
        email: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        result: str = "success",
        severity: AuditSeverity = AuditSeverity.INFO,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Log an audit event.

        Args:
            event_type: Type of event
            action: Action performed
            request: FastAPI request object
            user_id: User ID if applicable
            username: Username
            email: User email
            resource_type: Type of resource affected
            resource_id: ID of resource affected
            result: Result of action
            severity: Event severity
            description: Event description
            metadata: Additional metadata
            **kwargs: Additional fields

        Returns:
            Event ID
        """
        # Extract request information
        ip_address = None
        user_agent = None
        request_id = None

        request_metadata: dict[str, Any] = {}

        if request:
            ip_address = request.client.host if request.client else None
            user_agent = request.headers.get("User-Agent")
            request_id = request.headers.get("X-Request-ID")

            # Extract user info from request state if available
            request_user = getattr(request.state, "user", None)
            if request_user is not None:
                user_id = user_id or getattr(request_user, "id", None)
                username = username or getattr(request_user, "username", None)
                email = email or getattr(request_user, "email", None)

            # Auth middleware initializes ``organization_id`` and
            # ``token_payload`` on request state. Dependencies may populate
            # either value later, so prefer the resolved state and fall back
            # to the authenticated token/user conventions used elsewhere.
            organization_id = getattr(request.state, "organization_id", None)
            if organization_id is None:
                token_payload = getattr(request.state, "token_payload", None)
                organization_id = getattr(token_payload, "organization_id", None)
            if organization_id is None and request_user is not None:
                organization_id = getattr(request_user, "organization_id", None)
            if organization_id is not None:
                request_metadata["organization_id"] = str(organization_id)

        event_metadata = dict(metadata or {})
        event_metadata.update(
            {
                key: value
                for key, value in request_metadata.items()
                if key not in event_metadata
            }
        )

        # Create audit log entry
        log_entry = {
            "event_type": event_type,
            "action": action,
            "user_id": user_id,
            "username": username,
            "email": email,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "result": result,
            "severity": severity,
            "description": description,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "request_id": request_id,
            "metadata": event_metadata,
            "created_at": datetime.now(UTC),
            **kwargs,
        }

        # Add to buffer
        with self.buffer_lock:
            self.log_buffer.append(log_entry)
            self.metrics["events_logged"] += 1

            # Flush if buffer is full
            if len(self.log_buffer) >= self.buffer_size:
                task = asyncio.create_task(self._flush_in_background())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

        # Log to structured logger as well
        self.logger.info(
            f"Audit event: {event_type.value}",
            event_type=event_type.value,
            action=action,
            user_id=user_id,
            result=result,
            severity=severity.value,
        )

        # Check if alert should be generated
        await self._check_alert_conditions(log_entry)

        # Generate and return event ID
        event_id = f"{event_type.value}_{datetime.now(UTC).timestamp()}"
        return event_id

    async def flush_buffer(self) -> None:
        """Flush buffered events to durable storage.

        A logger without a session or session factory cannot persist an audit
        event. It therefore raises an explicit error instead of returning as
        though the buffer had been written. Failed writes are restored to the
        buffer so a later retry can recover them.
        """
        if self.session_factory is None and self.db_session is None:
            with self.buffer_lock:
                pending = len(self.log_buffer)
            if pending:
                self.metrics["errors"] += 1
                message = (
                    "Audit persistence unavailable: no database session factory "
                    f"is configured for {pending} buffered event(s)"
                )
                self.logger.error(message, buffered_events=pending)
                raise AuditPersistenceError(message)
            return

        # Get logs to flush
        with self.buffer_lock:
            if not self.log_buffer:
                return

            logs_to_flush = self.log_buffer.copy()
            self.log_buffer.clear()

        try:
            if self.session_factory is not None:
                # The application-wide logger is shared across requests;
                # AsyncSession is not safe to share, so each flush owns a
                # fresh session from the injected factory.
                async with self.session_factory() as session:
                    for log_entry in logs_to_flush:
                        session.add(AuditLog(**self._as_columns(log_entry)))
                    await session.commit()
            else:
                assert self.db_session is not None
                for log_entry in logs_to_flush:
                    self.db_session.add(AuditLog(**self._as_columns(log_entry)))
                await self.db_session.commit()

            self.metrics["events_flushed"] += len(logs_to_flush)

            self.logger.debug(f"Flushed {len(logs_to_flush)} audit logs to database")

        except Exception as e:
            self.metrics["errors"] += 1
            self.logger.error(f"Error flushing audit logs: {e!s}", exc_info=True)

            # Try to restore logs to buffer on error
            with self.buffer_lock:
                self.log_buffer.extend(logs_to_flush)

            raise AuditPersistenceError("Failed to persist audit events") from e

    async def _flush_in_background(self) -> None:
        """Flush a full buffer without leaking a task exception."""
        try:
            await self.flush_buffer()
        except AuditPersistenceError:
            # The failure is already counted and logged by ``flush_buffer``.
            return

    @staticmethod
    def _as_columns(log_entry: dict[str, Any]) -> dict[str, Any]:
        """Map the public metadata argument to the ORM attribute name."""
        entry = dict(log_entry)
        if "metadata" in entry:
            entry["event_metadata"] = _json_safe_value(entry.pop("metadata"))
        return entry

    async def _periodic_flush(self) -> None:
        """Periodically flush log buffer."""
        while True:
            try:
                await asyncio.sleep(self.flush_interval)
                await self.flush_buffer()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in periodic flush: {e!s}")

    async def _check_alert_conditions(self, log_entry: dict[str, Any]) -> None:
        """
        Check if log entry should trigger an alert.

        Args:
            log_entry: Audit log entry
        """
        event_type = log_entry.get("event_type")
        _severity = log_entry.get("severity")
        user_id = log_entry.get("user_id")

        # Check for immediate alert conditions
        immediate_alert_events = [
            AuditEventType.SYSTEM_BREACH,
            AuditEventType.DATA_EXFILTRATION,
            AuditEventType.SQL_INJECTION_ATTEMPT,
            AuditEventType.XSS_ATTEMPT,
            AuditEventType.UNAUTHORIZED_ACCESS,
        ]

        if event_type in immediate_alert_events:
            await self._generate_security_alert(log_entry, AlertSeverity.CRITICAL)
            return

        # Check for threshold-based alerts
        if event_type == AuditEventType.LOGIN_FAILED and user_id:
            # Count recent failed login attempts
            count = await self._count_recent_events(
                user_id, AuditEventType.LOGIN_FAILED, minutes=15
            )

            if count >= self.alert_threshold.get("failed_login_attempts", 5):
                await self._generate_security_alert(
                    log_entry,
                    AlertSeverity.HIGH,
                    alert_type=AlertType.FAILED_LOGIN_ATTEMPTS,
                )

        elif event_type == AuditEventType.RATE_LIMIT_EXCEEDED:
            # Count recent rate limit hits
            identifier = user_id or log_entry.get("ip_address")
            if not identifier:
                return
            count = await self._count_recent_events(
                str(identifier),
                AuditEventType.RATE_LIMIT_EXCEEDED,
                minutes=5,
            )

            if count >= self.alert_threshold.get("rate_limit_exceeded", 10):
                await self._generate_security_alert(
                    log_entry, AlertSeverity.MEDIUM, alert_type=AlertType.API_RATE_LIMIT
                )

    async def _count_recent_events(
        self, identifier: str, event_type: AuditEventType, minutes: int = 15
    ) -> int:
        """
        Count recent events for threshold checking.

        Args:
            identifier: User ID or IP address
            event_type: Type of event to count
            minutes: Time window in minutes

        Returns:
            Event count
        """
        # Check cache first
        cache_key = f"audit:count:{identifier}:{event_type.value}"
        if self.cache:
            cached_count = await self.cache.get(cache_key)
            if cached_count is not None and isinstance(cached_count, dict):
                return int(cached_count.get("count", 0))

        if self.db_session or self.session_factory:
            from sqlalchemy import func, select

            since = datetime.now(UTC) - timedelta(minutes=minutes)

            query = select(func.count(AuditLog.id)).where(
                AuditLog.event_type == event_type, AuditLog.created_at >= since
            )

            if "@" in str(identifier):  # Email
                query = query.where(AuditLog.email == identifier)
            elif "-" in str(identifier):  # UUID
                query = query.where(AuditLog.user_id == identifier)
            else:  # IP address
                query = query.where(AuditLog.ip_address == identifier)

            if self.db_session:
                result = await self.db_session.execute(query)
                count = result.scalar() or 0
            else:
                assert self.session_factory is not None
                async with self.session_factory() as session:
                    result = await session.execute(query)
                    count = result.scalar() or 0

            # Cache the result
            if self.cache:
                await self.cache.set(cache_key, {"count": count})

            return count

        return 0

    async def _generate_security_alert(
        self,
        log_entry: dict[str, Any],
        severity: AlertSeverity,
        alert_type: AlertType | None = None,
    ) -> None:
        """
        Generate a security alert from audit log.

        Args:
            log_entry: Audit log entry
            severity: Alert severity
            alert_type: Specific alert type
        """
        if self.session_factory is None and self.db_session is None:
            message = (
                "Audit persistence unavailable: cannot generate a security "
                "alert without a database session factory"
            )
            self.metrics["errors"] += 1
            self.logger.error(message)
            raise AuditPersistenceError(message)

        # Map audit event to alert type if not specified
        if not alert_type:
            event_type_raw = log_entry.get("event_type")
            alert_type_map: dict[AuditEventType, AlertType] = {
                AuditEventType.LOGIN_FAILED: AlertType.FAILED_LOGIN_ATTEMPTS,
                AuditEventType.SUSPICIOUS_ACTIVITY: AlertType.SUSPICIOUS_LOGIN,
                AuditEventType.RATE_LIMIT_EXCEEDED: AlertType.API_RATE_LIMIT,
                AuditEventType.SQL_INJECTION_ATTEMPT: AlertType.SQL_INJECTION_ATTEMPT,
                AuditEventType.XSS_ATTEMPT: AlertType.XSS_ATTEMPT,
                AuditEventType.UNAUTHORIZED_ACCESS: AlertType.UNAUTHORIZED_API_ACCESS,
                AuditEventType.DATA_EXFILTRATION: AlertType.DATA_EXFILTRATION,
            }
            if isinstance(event_type_raw, AuditEventType):
                alert_type = alert_type_map.get(
                    event_type_raw, AlertType.UNUSUAL_ACTIVITY
                )
            else:
                alert_type = AlertType.UNUSUAL_ACTIVITY

        # Create alert
        alert = SecurityAlert.create_alert(
            alert_type=alert_type,
            title=f"{alert_type.value.replace('_', ' ').title()} Detected",
            description=log_entry.get("description")
            or f"Security event: {alert_type.value}",
            severity=severity,
            user_id=log_entry.get("user_id"),
            ip_address=log_entry.get("ip_address"),
            evidence={"audit_log": _json_safe_value(log_entry)},
            auto_remediate=severity == AlertSeverity.CRITICAL,
        )

        if self.session_factory is not None:
            async with self.session_factory() as session:
                session.add(alert)
                await session.commit()
        else:
            assert self.db_session is not None
            self.db_session.add(alert)
            await self.db_session.commit()

        self.metrics["alerts_generated"] += 1

        self.logger.warning(
            f"Security alert generated: {alert_type.value}",
            alert_type=alert_type.value,
            severity=severity.value,
            user_id=log_entry.get("user_id"),
        )

    async def query_logs(
        self,
        user_id: str | None = None,
        event_types: list[AuditEventType] | None = None,
        severity: AuditSeverity | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        resource_type: str | None = None,
        result: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        """
        Query audit logs with filters.

        Args:
            user_id: Filter by user
            event_types: Filter by event types
            severity: Filter by severity
            start_date: Start date filter
            end_date: End date filter
            resource_type: Filter by resource type
            result: Filter by result
            limit: Maximum results
            offset: Result offset

        Returns:
            List of audit logs
        """
        from sqlalchemy import select

        query = select(AuditLog)

        # Apply filters
        if user_id:
            query = query.where(AuditLog.user_id == user_id)

        if event_types:
            query = query.where(AuditLog.event_type.in_(event_types))

        if severity:
            query = query.where(AuditLog.severity == severity)

        if start_date:
            query = query.where(AuditLog.created_at >= start_date)

        if end_date:
            query = query.where(AuditLog.created_at <= end_date)

        if resource_type:
            query = query.where(AuditLog.resource_type == resource_type)

        if result:
            query = query.where(AuditLog.result == result)

        # Apply ordering and pagination
        query = query.order_by(AuditLog.created_at.desc())
        query = query.limit(limit).offset(offset)

        if self.db_session:
            query_result = await self.db_session.execute(query)
            return list(query_result.scalars().all())

        if self.session_factory:
            async with self.session_factory() as session:
                query_result = await session.execute(query)
                return list(query_result.scalars().all())

        return []

    async def generate_compliance_report(
        self, start_date: datetime, end_date: datetime, compliance_type: str = "gdpr"
    ) -> dict[str, Any]:
        """
        Generate compliance report from audit logs.

        Args:
            start_date: Report start date
            end_date: Report end date
            compliance_type: Type of compliance (gdpr, hipaa, sox, etc.)

        Returns:
            Compliance report dictionary
        """
        report: dict[str, Any] = {
            "compliance_type": compliance_type,
            "period": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "summary": {},
            "details": [],
            "violations": [],
            "recommendations": [],
        }

        # Query relevant logs based on compliance type
        if compliance_type == "gdpr":
            # GDPR compliance checks
            relevant_events = [
                AuditEventType.DATA_ACCESSED,
                AuditEventType.DATA_MODIFIED,
                AuditEventType.DATA_DELETED,
                AuditEventType.DATA_EXPORTED,
                AuditEventType.ACCOUNT_DELETED,
            ]

            logs = await self.query_logs(
                event_types=relevant_events,
                start_date=start_date,
                end_date=end_date,
                limit=10000,
            )

            # Analyze logs for GDPR compliance
            report["summary"]["data_access_events"] = len(
                [
                    entry
                    for entry in logs
                    if entry.event_type == AuditEventType.DATA_ACCESSED
                ]
            )
            report["summary"]["data_modifications"] = len(
                [
                    entry
                    for entry in logs
                    if entry.event_type == AuditEventType.DATA_MODIFIED
                ]
            )
            report["summary"]["data_deletions"] = len(
                [
                    entry
                    for entry in logs
                    if entry.event_type == AuditEventType.DATA_DELETED
                ]
            )
            report["summary"]["data_exports"] = len(
                [
                    entry
                    for entry in logs
                    if entry.event_type == AuditEventType.DATA_EXPORTED
                ]
            )
            report["summary"]["account_deletions"] = len(
                [
                    entry
                    for entry in logs
                    if entry.event_type == AuditEventType.ACCOUNT_DELETED
                ]
            )

            # Check for violations
            for log in logs:
                if (
                    log.event_type == AuditEventType.DATA_EXPORTED
                    and log.event_metadata
                    and not log.event_metadata.get("consent_verified")
                ):
                    report["violations"].append(
                        {
                            "type": "data_export_without_consent",
                            "event_id": str(log.id),
                            "timestamp": log.created_at.isoformat(),
                            "user_id": str(log.user_id) if log.user_id else None,
                        }
                    )

        # Add recommendations based on findings
        if report["violations"]:
            report["recommendations"].append(
                "Implement consent verification for all data exports"
            )
            report["recommendations"].append("Review data handling procedures")

        return report

    def get_metrics(self) -> dict[str, Any]:
        """Get audit logger metrics."""
        return {
            **self.metrics,
            "buffer_size": len(self.log_buffer),
            "alert_thresholds": self.alert_threshold,
        }
