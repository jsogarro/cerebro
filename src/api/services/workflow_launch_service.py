"""Launch request contract and workflow selection.

One canonical request serves every launch adapter. Identity never comes from
it: the requester is the authenticated token subject, so ownership fields are
reserved and rejected. Selection only picks among catalogue entries and checks
the request against them; it makes no provider call and writes nothing.

HTTP mapping for ``WorkflowLaunchRejectedError.http_status``:
400 missing idempotency key, 404 unknown or disabled workflow, 422 anything
the catalogue does not allow (mode, provider policy, budget, reserved keys).
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.api.services.workflow_catalog import (
    ExecutableWorkflowCatalog,
    WorkflowCatalogEntry,
)
from src.core.contracts import (
    COLLABORATION_MODE_SUPPORT,
    CollaborationMode,
    CollaborationModeSupport,
    ExecutionBudget,
)
from src.core.contracts.base import IdempotencyKey

# Keys a caller could use to claim identity, authority or capability.
RESERVED_LAUNCH_KEYS = frozenset(
    {
        "user_id",
        "requested_by",
        "tenant_id",
        "organization_id",
        "authority_id",
        "authority_version",
        "authority_reference",
        "binding",
        "plan",
        "execution_plan",
        "providers",
        "tools",
        "tool_allowlist",
        "permission_scopes",
        "fixtures",
    }
)

_MAX_INPUT_BYTES = 64 * 1024


class WorkflowLaunchRejectedError(Exception):
    """A launch refused before any run, provider call or write."""

    def __init__(self, code: str, http_status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.message = message


class BudgetHint(BaseModel):
    """A request may only narrow the catalogue ceiling."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_cost_usd: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_total_tokens: int | None = Field(default=None, ge=1)


class WorkflowLaunchRequest(BaseModel):
    """The canonical launch payload; unknown or reserved fields are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: Annotated[str, Field(min_length=1, max_length=255)]
    workflow_version: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    objective: Annotated[str, Field(min_length=1, max_length=10_000)]
    inputs: dict[str, object] = Field(default_factory=dict)
    # Recorded with the run; a dry run still traverses admission.
    dry_run: bool = False
    # A collaboration-mode hint; it can only confirm the catalogue's mode.
    mode: str | None = None
    budget: BudgetHint | None = None
    provider_policy_id: str | None = None
    parent_run_id: str | None = None

    @model_validator(mode="after")
    def reject_reserved_inputs(self) -> WorkflowLaunchRequest:
        reserved = RESERVED_LAUNCH_KEYS & self.inputs.keys()
        if reserved:
            raise ValueError(f"reserved launch keys: {sorted(reserved)}")
        if len(json.dumps(self.inputs, default=str)) > _MAX_INPUT_BYTES:
            raise ValueError("inputs exceed the size limit")
        return self


class WorkflowLaunchSelection(BaseModel):
    """The catalogue entry a request resolved to and what pins the launch."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    entry: WorkflowCatalogEntry
    budget: ExecutionBudget
    dry_run: bool
    idempotency_key: str
    launch_digest: str
    catalog_hash: str


def _reject(code: str, status: int, message: str) -> WorkflowLaunchRejectedError:
    return WorkflowLaunchRejectedError(code, status, message)


class WorkflowLaunchService:
    """Resolve a launch request against the trusted catalogue."""

    def __init__(self, catalog: ExecutableWorkflowCatalog) -> None:
        self._catalog = catalog

    def select(
        self,
        request: WorkflowLaunchRequest,
        *,
        requester: str,
        idempotency_key: IdempotencyKey | None,
    ) -> WorkflowLaunchSelection:
        if not idempotency_key:
            raise _reject(
                "IDEMPOTENCY_KEY_REQUIRED", 400, "Idempotency-Key header is required"
            )
        entry = self._catalog.resolve(request.workflow_id, request.workflow_version)
        if entry is None:
            raise _reject("WORKFLOW_NOT_FOUND", 404, "workflow is not available")
        policy = entry.routing_policy
        if request.mode is not None:
            self._check_mode(request.mode, policy.collaboration_mode)
        if request.provider_policy_id not in (None, policy.routing_policy_id):
            raise _reject(
                "PROVIDER_POLICY_NOT_ALLOWED", 422, "provider policy is not authorized"
            )
        budget = self._narrow(entry.budget_ceiling, request.budget)
        digest = hashlib.sha256(
            json.dumps(
                {
                    "requester": requester,
                    "workflow": entry.key,
                    "objective": request.objective,
                    "inputs": request.inputs,
                    "dry_run": request.dry_run,
                    "budget": budget.model_dump(mode="json"),
                    "provider_policy_id": policy.routing_policy_id,
                    "parent_run_id": request.parent_run_id,
                },
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        return WorkflowLaunchSelection(
            entry=entry,
            budget=budget,
            dry_run=request.dry_run,
            idempotency_key=idempotency_key,
            launch_digest=digest,
            catalog_hash=self._catalog.catalog_hash,
        )

    @staticmethod
    def _check_mode(requested: str, catalogue_mode: str) -> None:
        try:
            mode = CollaborationMode(requested)
        except ValueError:
            raise _reject("MODE_UNSUPPORTED", 422, "unknown mode") from None
        if COLLABORATION_MODE_SUPPORT[mode] is not CollaborationModeSupport.IMPLEMENTED:
            raise _reject(
                "MODE_UNSUPPORTED", 422, f"mode {requested!r} is not supported"
            )
        if requested != catalogue_mode:
            raise _reject(
                "MODE_UNSUPPORTED", 422, "mode differs from the workflow's mode"
            )

    @staticmethod
    def _narrow(ceiling: ExecutionBudget, hint: BudgetHint | None) -> ExecutionBudget:
        if hint is None:
            return ceiling
        updates: dict[str, object] = {}
        if hint.max_cost_usd is not None:
            updates["max_cost_usd"] = hint.max_cost_usd
        if hint.max_total_tokens is not None:
            updates["max_total_tokens"] = hint.max_total_tokens
        narrowed = ceiling.model_copy(update=updates)
        if (
            narrowed.max_cost_usd > ceiling.max_cost_usd
            or narrowed.max_total_tokens > ceiling.max_total_tokens
        ):
            raise _reject(
                "BUDGET_EXPANSION", 422, "budget exceeds the catalogue ceiling"
            )
        return narrowed
