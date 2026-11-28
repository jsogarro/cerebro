"""Stateless Streamable HTTP MCP server exposing Cerebro research tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from structlog import get_logger

from src.api.services.direct_execution_service import (
    RunAdmissionError,
    RunParamsConflictError,
    research_query_sha256,
)
from src.api.services.evidence_service import (
    DEFAULT_EVIDENCE_LIMIT,
    InvalidEvidenceCursorError,
    RunNotFoundError,
    list_run_evidence_with_factory,
)
from src.api.services.execution_authority_resolver import (
    ExecutionAuthorityError,
    ExecutionAuthorityUnavailableError,
)
from src.api.services.research_kernel import (
    ApplicationResearchKernel,
    build_research_results_payload,
    execution_status_payload,
    get_kernel_execution_status,
    start_intelligent_research,
)
from src.auth.models import (
    PAT_SCOPE_READ,
    PAT_SCOPE_START,
    PersonalAccessTokenPayload,
    TokenPayload,
)
from src.middleware.tenant_context import TenantContext, require_organization_id
from src.models.execution_authority import ExecutionAuthorityReference
from src.repositories.run_lifecycle_repository import RunCapExceededError
from src.security.audit_logger import AuditEventType, AuditLogger

logger = get_logger()

_READ_ONLY_ANNOTATIONS: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
}

_START_RESEARCH_ANNOTATIONS: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


class StartResearchInput(BaseModel):
    """Closed input model for ``start_research``; deliberately omits ``context``."""

    model_config = ConfigDict(extra="forbid")

    template_authority_id: str = Field(..., min_length=1)
    template_authority_version: str = Field(..., min_length=1)
    request_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    domains: list[str] = Field(default_factory=list)

    @field_validator(
        "template_authority_id",
        "template_authority_version",
        "request_id",
        "query",
    )
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


@dataclass(frozen=True, slots=True)
class _CallerIdentity:
    """Authenticated caller context preserved across scope/tenant denials."""

    tenant: TenantContext | None
    request: Any
    pat_id: str | None
    scopes: tuple[str, ...] = ()
    user_id: str | None = None
    organization_id: str | None = None


class _CallerAuthError(ToolError):
    """Auth/scope denial that retains resolved caller identity for audit."""

    def __init__(self, message: str, *, caller: _CallerIdentity) -> None:
        super().__init__(message)
        self.caller = caller


def _caller(*, required_scope: str) -> _CallerIdentity:
    """Resolve authenticated tenant context and enforce PAT scope.

    ``required_scope`` has no default so a tool cannot omit it and fail open.
    Denials attach whatever identity was already verified (including ``pat_id``,
    ``user_id``, ``organization_id``, and ``scopes``) so failure audits never
    drop the caller's PAT attribution.
    """
    try:
        request = get_http_request()
    except RuntimeError as exc:
        raise ToolError("Authentication required: MCP tools require HTTP") from exc

    payload = getattr(request.state, "token_payload", None)
    if not isinstance(payload, (TokenPayload, PersonalAccessTokenPayload)):
        raise _CallerAuthError(
            "Authentication required",
            caller=_CallerIdentity(tenant=None, request=request, pat_id=None),
        )

    pat_id: str | None = None
    scopes: tuple[str, ...] = ()
    if isinstance(payload, PersonalAccessTokenPayload):
        pat_id = payload.pat_id
        scopes = tuple(payload.scopes)

    try:
        organization_id = require_organization_id(payload)
    except HTTPException as exc:
        raise _CallerAuthError(
            str(exc.detail),
            caller=_CallerIdentity(
                tenant=None,
                request=request,
                pat_id=pat_id,
                scopes=scopes,
                user_id=payload.sub,
                organization_id=None,
            ),
        ) from exc

    tenant = TenantContext(user_id=payload.sub, organization_id=organization_id)
    identity = _CallerIdentity(
        tenant=tenant,
        request=request,
        pat_id=pat_id,
        scopes=scopes,
        user_id=tenant.user_id,
        organization_id=tenant.organization_id,
    )

    if isinstance(payload, PersonalAccessTokenPayload) and not payload.has_scope(
        required_scope
    ):
        raise _CallerAuthError(
            f"Personal access token missing required scope: {required_scope}",
            caller=identity,
        )

    return identity


def _resolve_authority_resolver(
    kernel: ApplicationResearchKernel,
    authority_resolver: Any | None,
) -> Any:
    resolver = authority_resolver
    if resolver is None:
        backend = getattr(kernel.executor, "backend", None)
        resolver = getattr(backend, "execution_authority_resolver", None)
    if resolver is None or not callable(
        getattr(resolver, "mint_authority_from_template", None)
    ):
        raise ExecutionAuthorityUnavailableError("execution authority is unavailable")
    return resolver


def build_mcp_server(
    *,
    kernel: ApplicationResearchKernel,
    audit_logger: AuditLogger | Any,
    session_factory: Callable[[], Any] | None = None,
    authority_resolver: Any | None = None,
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
        caller: _CallerIdentity | None = None,
        detail: str | None = None,
        event_type: AuditEventType = AuditEventType.DATA_ACCESSED,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        metadata: dict[str, Any] = {"tool": tool_name}
        if caller is not None:
            if caller.organization_id is not None:
                metadata["organization_id"] = caller.organization_id
            if caller.pat_id is not None:
                metadata["pat_id"] = caller.pat_id
            if caller.scopes:
                metadata["scopes"] = list(caller.scopes)
        if detail is not None:
            metadata["detail"] = detail
        if extra_metadata:
            metadata.update(extra_metadata)
        await audit_logger.log_event(
            event_type=event_type,
            action=f"mcp.tool:{tool_name}",
            request=caller.request if caller is not None else None,
            user_id=caller.user_id if caller is not None else None,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            metadata=metadata,
        )

    @mcp.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def get_research_status(execution_id: str) -> dict[str, Any]:
        """Get the status and progress of a research execution."""
        caller: _CallerIdentity | None = None
        try:
            caller = _caller(required_scope=PAT_SCOPE_READ)
            assert caller.tenant is not None
            exec_status = await get_kernel_execution_status(
                kernel,
                execution_id,
                organization_id=caller.tenant.organization_id,
            )
            if not exec_status:
                raise ToolError(f"Execution {execution_id} not found")
            payload = execution_status_payload(execution_id, exec_status)
        except ToolError as exc:
            if caller is None and isinstance(exc, _CallerAuthError):
                caller = exc.caller
            await _audit(
                tool_name="get_research_status",
                resource_type="research_execution",
                resource_id=execution_id,
                result="failure",
                caller=caller,
                detail=str(exc),
            )
            raise

        await _audit(
            tool_name="get_research_status",
            resource_type="research_execution",
            resource_id=execution_id,
            result="success",
            caller=caller,
        )
        return payload

    @mcp.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def get_research_results(execution_id: str) -> dict[str, Any]:
        """Get completed research results with trust labels and claim verdicts."""
        caller: _CallerIdentity | None = None
        try:
            caller = _caller(required_scope=PAT_SCOPE_READ)
            assert caller.tenant is not None
            payload = await build_research_results_payload(
                kernel,
                execution_id,
                organization_id=caller.tenant.organization_id,
                session_factory=session_factory,
            )
            if payload is None:
                raise ToolError(f"Execution {execution_id} not found or not completed")
        except ToolError as exc:
            if caller is None and isinstance(exc, _CallerAuthError):
                caller = exc.caller
            await _audit(
                tool_name="get_research_results",
                resource_type="research_execution",
                resource_id=execution_id,
                result="failure",
                caller=caller,
                detail=str(exc),
            )
            raise

        await _audit(
            tool_name="get_research_results",
            resource_type="research_execution",
            resource_id=execution_id,
            result="success",
            caller=caller,
        )
        return payload

    @mcp.tool(annotations=_READ_ONLY_ANNOTATIONS)
    async def get_research_evidence(
        run_id: str,
        cursor: str | None = None,
        limit: int = DEFAULT_EVIDENCE_LIMIT,
    ) -> dict[str, Any]:
        """List metadata-only evidence records for a research run."""
        caller: _CallerIdentity | None = None
        try:
            caller = _caller(required_scope=PAT_SCOPE_READ)
            assert caller.tenant is not None
            if session_factory is None:
                raise ToolError("Evidence storage is unavailable")
            try:
                page = await list_run_evidence_with_factory(
                    session_factory,
                    run_id,
                    organization_id=caller.tenant.organization_id,
                    cursor=cursor,
                    limit=limit,
                )
            except (RunNotFoundError, InvalidEvidenceCursorError, ValueError) as exc:
                raise ToolError(str(exc)) from exc
            payload = page.model_dump()
        except ToolError as exc:
            if caller is None and isinstance(exc, _CallerAuthError):
                caller = exc.caller
            await _audit(
                tool_name="get_research_evidence",
                resource_type="research_run",
                resource_id=run_id,
                result="failure",
                caller=caller,
                detail=str(exc),
            )
            raise

        await _audit(
            tool_name="get_research_evidence",
            resource_type="research_run",
            resource_id=run_id,
            result="success",
            caller=caller,
        )
        return payload

    @mcp.tool(annotations=_START_RESEARCH_ANNOTATIONS)
    async def start_research(
        template_authority_id: str,
        template_authority_version: str,
        request_id: str,
        query: str,
        domains: list[str] | None = None,
    ) -> dict[str, Any]:
        """Start or idempotently replay a research run from an authority template."""
        caller: _CallerIdentity | None = None
        query_hash = research_query_sha256(query)
        query_length = len(query)
        audit_extra: dict[str, Any] = {
            "query_sha256": query_hash,
            "query_length": query_length,
            "authority_id": template_authority_id,
            "authority_version": template_authority_version,
        }
        try:
            caller = _caller(required_scope=PAT_SCOPE_START)
            assert caller.tenant is not None
            try:
                validated = StartResearchInput.model_validate(
                    {
                        "template_authority_id": template_authority_id,
                        "template_authority_version": template_authority_version,
                        "request_id": request_id,
                        "query": query,
                        "domains": [] if domains is None else domains,
                    }
                )
            except ValidationError as exc:
                raise ToolError("INVALID_INPUT") from exc

            resolver = _resolve_authority_resolver(kernel, authority_resolver)
            minted = await resolver.mint_authority_from_template(
                ExecutionAuthorityReference(
                    authority_id=validated.template_authority_id,
                    authority_version=validated.template_authority_version,
                ),
                organization_id=caller.tenant.organization_id,
                user_id=caller.tenant.user_id,
                request_id=validated.request_id,
            )
            audit_extra["authority_id"] = minted.authority_id
            audit_extra["authority_version"] = minted.authority_version

            start_result = await start_intelligent_research(
                kernel,
                tenant=caller.tenant,
                query=validated.query,
                domains=validated.domains,
                authority_reference=ExecutionAuthorityReference(
                    authority_id=minted.authority_id,
                    authority_version=minted.authority_version,
                ),
            )
        except ToolError as exc:
            if caller is None and isinstance(exc, _CallerAuthError):
                caller = exc.caller
            await _audit(
                tool_name="start_research",
                resource_type="research_execution",
                resource_id=request_id,
                result="failure",
                caller=caller,
                detail=str(exc),
                event_type=AuditEventType.DATA_MODIFIED,
                extra_metadata=audit_extra,
            )
            raise
        except (
            ExecutionAuthorityError,
            RunCapExceededError,
            RunParamsConflictError,
        ) as exc:
            await _audit(
                tool_name="start_research",
                resource_type="research_execution",
                resource_id=request_id,
                result="failure",
                caller=caller,
                detail=exc.code,
                event_type=AuditEventType.DATA_MODIFIED,
                extra_metadata=audit_extra,
            )
            raise ToolError(exc.code) from exc
        except RunAdmissionError as exc:
            await _audit(
                tool_name="start_research",
                resource_type="research_execution",
                resource_id=request_id,
                result="failure",
                caller=caller,
                detail="RUN_ADMISSION_FAILED",
                event_type=AuditEventType.DATA_MODIFIED,
                extra_metadata=audit_extra,
            )
            raise ToolError("RUN_ADMISSION_FAILED") from exc
        except Exception as exc:
            logger.error(
                "mcp_start_research_failed",
                error=type(exc).__name__,
                request_id=request_id,
                exc_info=exc,
            )
            await _audit(
                tool_name="start_research",
                resource_type="research_execution",
                resource_id=request_id,
                result="failure",
                caller=caller,
                detail="internal error",
                event_type=AuditEventType.DATA_MODIFIED,
                extra_metadata=audit_extra,
            )
            raise ToolError("internal error") from exc

        audit_extra.update(
            {
                "execution_id": start_result.execution_id,
                "run_id": start_result.run_id,
                "replayed": start_result.replayed,
            }
        )
        await _audit(
            tool_name="start_research",
            resource_type="research_execution",
            resource_id=start_result.execution_id,
            result="success",
            caller=caller,
            event_type=AuditEventType.DATA_MODIFIED,
            extra_metadata=audit_extra,
        )
        return {
            "execution_id": start_result.execution_id,
            "run_id": start_result.run_id,
            "status": start_result.status,
            "replayed": start_result.replayed,
        }

    return mcp


__all__ = ["StartResearchInput", "build_mcp_server"]
