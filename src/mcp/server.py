"""Stateless Streamable HTTP MCP server exposing read-only research tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request

from src.api.services.evidence_service import (
    DEFAULT_EVIDENCE_LIMIT,
    InvalidEvidenceCursorError,
    RunNotFoundError,
    list_run_evidence_with_factory,
)
from src.api.services.research_kernel import (
    ApplicationResearchKernel,
    build_research_results_payload,
    execution_status_payload,
    get_kernel_execution_status,
)
from src.auth.models import (
    PAT_SCOPE_READ,
    PersonalAccessTokenPayload,
    TokenPayload,
)
from src.middleware.tenant_context import TenantContext, require_organization_id
from src.security.audit_logger import AuditEventType, AuditLogger

_READ_ONLY_ANNOTATIONS: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
}


def _caller(
    *, required_scope: str | None = None
) -> tuple[TenantContext, Any, str | None]:
    """Resolve authenticated tenant context from the active HTTP request."""
    try:
        request = get_http_request()
    except RuntimeError as exc:
        raise ToolError("Authentication required: MCP tools require HTTP") from exc

    payload = getattr(request.state, "token_payload", None)
    if not isinstance(payload, (TokenPayload, PersonalAccessTokenPayload)):
        raise ToolError("Authentication required")

    pat_id: str | None = None
    if isinstance(payload, PersonalAccessTokenPayload):
        pat_id = payload.pat_id
        if required_scope and not payload.has_scope(required_scope):
            raise ToolError(
                f"Personal access token missing required scope: {required_scope}"
            )

    try:
        organization_id = require_organization_id(payload)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc

    return (
        TenantContext(user_id=payload.sub, organization_id=organization_id),
        request,
        pat_id,
    )


def build_mcp_server(
    *,
    kernel: ApplicationResearchKernel,
    audit_logger: AuditLogger | Any,
    session_factory: Callable[[], Any] | None = None,
) -> FastMCP:
    """Build a FastMCP server bound to explicit application dependencies.

    Dependencies are injected rather than read off ``request.app.state`` because
    Starlette rewrites ``scope["app"]`` to the mounted sub-app.
    """
    mcp = FastMCP("cerebro")

    async def _audit(
        *,
        tool_name: str,
        resource_type: str,
        resource_id: str,
        result: str,
        tenant: TenantContext | None = None,
        request: Any = None,
        pat_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        metadata: dict[str, Any] = {"tool": tool_name}
        if tenant is not None:
            metadata["organization_id"] = tenant.organization_id
        if pat_id is not None:
            metadata["pat_id"] = pat_id
        if detail is not None:
            metadata["detail"] = detail
        await audit_logger.log_event(
            event_type=AuditEventType.DATA_ACCESSED,
            action=f"mcp.tool:{tool_name}",
            request=request,
            user_id=tenant.user_id if tenant is not None else None,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            metadata=metadata,
        )

    @mcp.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def get_research_status(execution_id: str) -> dict[str, Any]:
        """Get the status and progress of a research execution."""
        tenant: TenantContext | None = None
        request: Any = None
        pat_id: str | None = None
        try:
            tenant, request, pat_id = _caller(required_scope=PAT_SCOPE_READ)
            exec_status = await get_kernel_execution_status(
                kernel,
                execution_id,
                organization_id=tenant.organization_id,
            )
            if not exec_status:
                raise ToolError(f"Execution {execution_id} not found")
            payload = execution_status_payload(execution_id, exec_status)
        except ToolError as exc:
            await _audit(
                tool_name="get_research_status",
                resource_type="research_execution",
                resource_id=execution_id,
                result="failure",
                tenant=tenant,
                request=request,
                pat_id=pat_id,
                detail=str(exc),
            )
            raise

        await _audit(
            tool_name="get_research_status",
            resource_type="research_execution",
            resource_id=execution_id,
            result="success",
            tenant=tenant,
            request=request,
            pat_id=pat_id,
        )
        return payload

    @mcp.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def get_research_results(execution_id: str) -> dict[str, Any]:
        """Get completed research results with trust labels and claim verdicts."""
        tenant: TenantContext | None = None
        request: Any = None
        pat_id: str | None = None
        try:
            tenant, request, pat_id = _caller(required_scope=PAT_SCOPE_READ)
            payload = await build_research_results_payload(
                kernel,
                execution_id,
                organization_id=tenant.organization_id,
                session_factory=session_factory,
            )
            if payload is None:
                raise ToolError(f"Execution {execution_id} not found or not completed")
        except ToolError as exc:
            await _audit(
                tool_name="get_research_results",
                resource_type="research_execution",
                resource_id=execution_id,
                result="failure",
                tenant=tenant,
                request=request,
                pat_id=pat_id,
                detail=str(exc),
            )
            raise

        await _audit(
            tool_name="get_research_results",
            resource_type="research_execution",
            resource_id=execution_id,
            result="success",
            tenant=tenant,
            request=request,
            pat_id=pat_id,
        )
        return payload

    @mcp.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def get_research_evidence(
        run_id: str,
        cursor: str | None = None,
        limit: int = DEFAULT_EVIDENCE_LIMIT,
    ) -> dict[str, Any]:
        """List metadata-only evidence records for a research run."""
        tenant: TenantContext | None = None
        request: Any = None
        pat_id: str | None = None
        try:
            tenant, request, pat_id = _caller(required_scope=PAT_SCOPE_READ)
            if session_factory is None:
                raise ToolError("Evidence storage is unavailable")
            try:
                page = await list_run_evidence_with_factory(
                    session_factory,
                    run_id,
                    organization_id=tenant.organization_id,
                    cursor=cursor,
                    limit=limit,
                )
            except (RunNotFoundError, InvalidEvidenceCursorError, ValueError) as exc:
                raise ToolError(str(exc)) from exc
            payload = page.model_dump()
        except ToolError as exc:
            await _audit(
                tool_name="get_research_evidence",
                resource_type="research_run",
                resource_id=run_id,
                result="failure",
                tenant=tenant,
                request=request,
                pat_id=pat_id,
                detail=str(exc),
            )
            raise

        await _audit(
            tool_name="get_research_evidence",
            resource_type="research_run",
            resource_id=run_id,
            result="success",
            tenant=tenant,
            request=request,
            pat_id=pat_id,
        )
        return payload

    return mcp


__all__ = ["build_mcp_server"]
