"""Focused validation tests for identity, provenance, and ordering invariants."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from src.core.contracts import (
    EVIDENCE_BEARING_STATUSES,
    ClaimSupport,
    ClaimSupportStatus,
    Evidence,
    PromptBinding,
    Run,
    RunEvent,
    Task,
    ToolInvocation,
    ToolInvocationStatus,
    TrustClassification,
)

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
PROMPT_BINDING = PromptBinding.for_rendered(
    prompt_id="agent.citation",
    prompt_version="1.0.0",
    template_source="Judge {claim}.",
    rendered_text="Judge the compared values.",
)


def _run_payload() -> dict[str, Any]:
    return {
        "run_id": "run-001",
        "tenant_id": "tenant-001",
        "workflow_definition_id": "workflow.research",
        "workflow_definition_version": "1.0.0",
        "routing_policy_id": "routing.default",
        "routing_policy_version": "1.0.0",
        "idempotency_key": "request-001",
        "requested_by": "user-001",
        "created_at": NOW,
        "updated_at": NOW,
    }


def test_contracts_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Run(**_run_payload(), runtime_only_guess=True)


@pytest.mark.parametrize("field", ["run_id", "idempotency_key", "tenant_id"])
def test_stable_run_identifiers_must_be_non_empty(field: str) -> None:
    payload = _run_payload()
    payload[field] = ""

    with pytest.raises(ValidationError, match="at least 1 character"):
        Run(**payload)


def test_lifecycle_timestamps_must_be_timezone_aware() -> None:
    payload = _run_payload()
    payload["created_at"] = datetime(2026, 7, 26, 12, 0)

    with pytest.raises(ValidationError, match="timezone"):
        Run(**payload)


def test_task_rejects_self_and_duplicate_dependencies() -> None:
    base: dict[str, Any] = {
        "task_id": "task-001",
        "run_id": "run-001",
        "task_key": "research",
        "task_type": "research",
        "objective": "Research the question.",
        "idempotency_key": "run-001:research",
        "created_at": NOW,
        "updated_at": NOW,
    }

    with pytest.raises(ValidationError, match="depend on itself"):
        Task(**base, dependency_ids=("task-001",))

    with pytest.raises(ValidationError, match="unique"):
        Task(**base, dependency_ids=("task-000", "task-000"))


def test_evidence_requires_a_sha256_content_hash() -> None:
    with pytest.raises(ValidationError, match="String should match pattern"):
        Evidence(
            evidence_id="evidence-001",
            run_id="run-001",
            task_id="task-001",
            source_type="web",
            source_uri="https://example.test/source",
            snapshot_artifact_id="artifact-001",
            content_sha256="not-a-hash",
            locator="bytes:0-512",
            trust=TrustClassification.EXTERNAL_UNTRUSTED,
            prompt_binding=PROMPT_BINDING,
            acquired_at=NOW,
        )


@pytest.mark.parametrize("status", sorted(EVIDENCE_BEARING_STATUSES))
def test_material_claim_judgments_require_evidence(
    status: ClaimSupportStatus,
) -> None:
    """Every verdict but ``unsupported`` is meaningless without evidence.

    Derived from ``EVIDENCE_BEARING_STATUSES`` rather than listed literally, so
    a future state added to the enum cannot quietly escape this invariant.
    """
    with pytest.raises(ValidationError, match="evidence"):
        ClaimSupport(
            claim_support_id="support-001",
            run_id="run-001",
            artifact_id="artifact-001",
            claim_id="claim-001",
            claim_text="A material claim.",
            status=status,
            evidence_ids=(),
            evaluator_id="claim-evaluator",
            evaluator_version="1.0.0",
            prompt_binding=PROMPT_BINDING,
            explanation="No evidence supplied.",
            evaluated_at=NOW,
        )


def test_run_event_sequence_is_positive_and_stably_deduplicated() -> None:
    base: dict[str, Any] = {
        "event_id": "event-001",
        "run_id": "run-001",
        "aggregate_type": "run",
        "aggregate_id": "run-001",
        "event_type": "run.created",
        "event_type_version": "1.0",
        "occurred_at": NOW,
        "producer": "research-kernel",
        "deduplication_key": "run-001:created",
    }

    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        RunEvent(**base, sequence=0)

    empty_key = base | {"deduplication_key": ""}
    with pytest.raises(ValidationError, match="at least 1 character"):
        RunEvent(**empty_key, sequence=1)


def _completed_tool_invocation(*, completed_at: datetime) -> ToolInvocation:
    return ToolInvocation(
        tool_invocation_id="tool-001",
        run_id="run-001",
        task_id="task-001",
        attempt_id="attempt-001",
        tool_name="web-search",
        tool_version="1.0.0",
        status=ToolInvocationStatus.SUCCEEDED,
        capability_scope="research:read",
        idempotency_key="attempt-001:web-search:001",
        input={"query": "primary source"},
        input_trust=TrustClassification.USER_SUPPLIED,
        output={"results": []},
        output_trust=TrustClassification.EXTERNAL_UNTRUSTED,
        requested_at=NOW,
        completed_at=completed_at,
    )


def test_tool_invocation_allows_completion_at_the_request_boundary() -> None:
    invocation = _completed_tool_invocation(completed_at=NOW)

    assert invocation.completed_at == invocation.requested_at


def test_tool_invocation_rejects_completion_before_request() -> None:
    with pytest.raises(ValidationError, match="cannot precede requested_at"):
        _completed_tool_invocation(completed_at=NOW - timedelta(microseconds=1))
