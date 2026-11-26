"""Request-level audit trail for the FastAPI application."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from src.api.client_ip import client_ip
from src.api.middleware.error_envelope import build_error_payload
from src.models.db.audit_log import AuditEventType, AuditSeverity
from src.security.audit_logger import AuditLogger

logger = structlog.get_logger(__name__)

_UNAUDITED_PATHS = frozenset(
    {"/health", "/health/", "/ready", "/live", "/metrics", "/favicon.ico"}
)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Public GET routes that change state (the emailed verification link).
_MUTATING_GET_PATHS = frozenset({"/api/v1/auth/verify-email"})
_REJECTION_STATUSES = frozenset({401, 403, 429})


class AuditTrailMiddleware:
    """Record requests through the application-owned ``AuditLogger``.

    The logger is created during the application lifespan because that is where
    the injected database session factory is available. Reading it from
    ``request.app.state`` keeps this middleware mounted for the whole process
    without sharing a request-scoped ``AsyncSession``.
    """

    def _logger_for(self, request: Request) -> Any | None:
        """Return the configured logger, accepting test/application doubles."""
        audit_logger = getattr(request.app.state, "audit_logger", None)
        if audit_logger is None:
            return None
        if not callable(getattr(audit_logger, "log_event", None)):
            return None
        if not callable(getattr(audit_logger, "flush_buffer", None)):
            return None
        return audit_logger

    @staticmethod
    def _event_type(request: Request, status_code: int | None) -> AuditEventType:
        """Map the request to an existing audit event type."""
        path = request.url.path.rstrip("/")
        # The login throttle's lockout is a rate-limit hit, not a failed guess.
        if status_code == 429:
            return AuditEventType.RATE_LIMIT_EXCEEDED
        if path.endswith("/auth/login"):
            if status_code is None:
                return AuditEventType.DATA_ACCESSED
            return (
                AuditEventType.LOGIN_SUCCESS
                if status_code < 400
                else AuditEventType.LOGIN_FAILED
            )
        if path.endswith("/auth/logout"):
            return AuditEventType.LOGOUT
        if status_code in (401, 403):
            return AuditEventType.UNAUTHORIZED_ACCESS
        if request.method == "DELETE":
            return AuditEventType.DATA_DELETED
        if request.method in {"POST", "PUT", "PATCH"}:
            return AuditEventType.DATA_MODIFIED
        return AuditEventType.DATA_ACCESSED

    @staticmethod
    def _severity(status_code: int) -> AuditSeverity:
        """Choose the severity for the resulting HTTP status."""
        if status_code >= 500:
            return AuditSeverity.ERROR
        if status_code in {401, 403, 429}:
            return AuditSeverity.WARNING
        return AuditSeverity.INFO

    async def _record(
        self,
        audit_logger: AuditLogger,
        request: Request,
        *,
        phase: str,
        status_code: int | None = None,
    ) -> None:
        """Record and durably flush one request lifecycle event."""
        metadata: dict[str, Any] = {
            "method": request.method,
            "path": request.url.path,
            "phase": phase,
        }
        if status_code is not None:
            metadata["status_code"] = status_code

        await audit_logger.log_event(
            event_type=self._event_type(request, status_code),
            action=f"{request.method} {request.url.path}",
            request=request,
            # Set by the login route for a failure against a real account.
            user_id=getattr(request.state, "audit_subject_id", None),
            result=(
                ("admitted" if phase == "admission" else "success")
                if status_code is None or status_code < 400
                else "failure"
            ),
            severity=(
                AuditSeverity.INFO
                if status_code is None
                else self._severity(status_code)
            ),
            metadata=metadata,
        )
        await audit_logger.flush_buffer()

    @staticmethod
    def _audit_failure_response() -> JSONResponse:
        """Return an explicit response when a configured audit store fails."""
        return JSONResponse(
            status_code=503,
            content=build_error_payload(
                code="AUDIT_PERSISTENCE_UNAVAILABLE",
                message="Audit logging is temporarily unavailable",
            ),
            headers={"X-Audit-Status": "unavailable"},
        )

    async def __call__(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Audit a request that authentication and rate limiting admitted.

        Mounted inside authentication and rate limiting, so requests those
        layers reject never reach it (see ``log_rejected_requests``).

        A mutating request records and flushes its admission before the
        endpoint runs, so no side effect happens without a durable record of
        the intent; if that write fails the request is refused (fail closed).
        Its outcome is flushed too, but an outcome failure is not turned into
        an error response: the intent is already durable, and a 503 after the
        side effect would report a completed mutation as failed.

        A safe request has no side effect to guard. It records its outcome
        after the response with the single flush; if the audit store is down
        the response is still returned and flagged ``X-Audit-Status:
        unavailable``, because withholding a response already produced does
        not undo the access and would turn an audit outage into a read outage.
        """
        if request.url.path in _UNAUDITED_PATHS:
            return await call_next(request)

        mutating = (
            request.method not in _SAFE_METHODS
            or request.url.path.rstrip("/") in _MUTATING_GET_PATHS
        )
        audit_logger = self._logger_for(request)
        if audit_logger is None:
            # A request made before lifespan startup (for example, a direct
            # ASGI test client) has no logger.
            logger.error(
                "audit_logger_unavailable",
                path=request.url.path,
                method=request.method,
            )
            if mutating:
                return self._audit_failure_response()
            response = await call_next(request)
            response.headers["X-Audit-Status"] = "unavailable"
            return response

        if mutating:
            try:
                await self._record(audit_logger, request, phase="admission")
            except Exception as exc:
                logger.error(
                    "audit_admission_persistence_failed",
                    path=request.url.path,
                    method=request.method,
                    error=type(exc).__name__,
                )
                return self._audit_failure_response()

        response = await call_next(request)

        try:
            await self._record(
                audit_logger,
                request,
                phase="outcome",
                status_code=response.status_code,
            )
        except Exception as exc:
            logger.error(
                "audit_event_persistence_failed",
                path=request.url.path,
                method=request.method,
                error=type(exc).__name__,
            )
            response.headers["X-Audit-Status"] = "unavailable"
            return response

        response.headers["X-Audit-Status"] = "persisted"
        return response


async def log_rejected_requests(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Leave a log line, not a database write, for 401/403/429 responses.

    Mounted outside authentication and rate limiting. Rejections are the
    cheapest requests an unauthenticated caller can send, so each one must
    cost no durable write.
    """
    response = await call_next(request)
    if response.status_code in _REJECTION_STATUSES:
        logger.warning(
            "request_rejected",
            status_code=response.status_code,
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else None,
        )
    return response


def audit_rejection(request: Request, event_type: AuditEventType, reason: str) -> None:
    """Hand a rejection to the app's audit logger without touching the store.

    Authentication and rate limiting call this on their reject path; see
    ``AuditLogger.record_rejection`` for why it never blocks or writes.
    """
    audit_logger = getattr(request.app.state, "audit_logger", None)
    record = getattr(audit_logger, "record_rejection", None)
    if not callable(record):
        return
    subject = getattr(request.state, "user_id", None) or client_ip(request)
    record(
        event_type,
        request,
        key=str(subject),
        action=f"{request.method} {request.url.path} ({reason})",
    )


__all__ = ["AuditTrailMiddleware", "audit_rejection", "log_rejected_requests"]
