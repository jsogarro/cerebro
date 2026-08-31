"""Request-level audit trail for the FastAPI application."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from src.api.middleware.error_envelope import build_error_payload
from src.models.db.audit_log import AuditEventType, AuditSeverity
from src.security.audit_logger import AuditLogger

logger = structlog.get_logger(__name__)

_UNAUDITED_PATHS = frozenset(
    {"/health", "/health/", "/ready", "/live", "/metrics", "/favicon.ico"}
)


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
    def _event_type(request: Request, status_code: int) -> AuditEventType:
        """Map the request to an existing audit event type."""
        path = request.url.path.rstrip("/")
        if path.endswith("/auth/login"):
            return (
                AuditEventType.LOGIN_SUCCESS
                if status_code < 400
                else AuditEventType.LOGIN_FAILED
            )
        if path.endswith("/auth/logout"):
            return AuditEventType.LOGOUT
        if status_code in (401, 403):
            return AuditEventType.UNAUTHORIZED_ACCESS
        if status_code == 429:
            return AuditEventType.RATE_LIMIT_EXCEEDED
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
        status_code: int,
    ) -> None:
        """Record and durably flush one request event."""
        await audit_logger.log_event(
            event_type=self._event_type(request, status_code),
            action=f"{request.method} {request.url.path}",
            request=request,
            result="success" if status_code < 400 else "failure",
            severity=self._severity(status_code),
            metadata={
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
            },
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
        """Audit the response while preserving the endpoint's own behavior."""
        response = await call_next(request)

        if request.url.path in _UNAUDITED_PATHS:
            return response

        audit_logger = self._logger_for(request)
        if audit_logger is None:
            # A request made before lifespan startup (for example, a direct
            # ASGI test client) has no logger. Do not pretend it was audited;
            # make the missing control observable to that caller instead.
            response.headers["X-Audit-Status"] = "unavailable"
            logger.error(
                "audit_logger_unavailable",
                path=request.url.path,
                method=request.method,
            )
            return response

        try:
            await self._record(audit_logger, request, response.status_code)
        except Exception as exc:
            logger.error(
                "audit_event_persistence_failed",
                path=request.url.path,
                method=request.method,
                error=type(exc).__name__,
            )
            return self._audit_failure_response()

        response.headers["X-Audit-Status"] = "persisted"
        return response


__all__ = ["AuditTrailMiddleware"]
