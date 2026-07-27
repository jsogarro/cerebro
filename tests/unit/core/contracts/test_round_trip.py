"""Deterministic serialization tests for the canonical agent-system contracts."""

import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from pydantic import ValidationError

from src.core.contracts import (
    Artifact,
    ArtifactStatus,
    Attempt,
    ClaimSupport,
    ClaimSupportStatus,
    EvaluationResult,
    EvaluationStatus,
    Evidence,
    RoutingPolicy,
    Run,
    RunEvent,
    Task,
    ToolInvocation,
    ToolInvocationStatus,
    TrustClassification,
    WorkflowControlMode,
    WorkflowDefinition,
)
from src.core.contracts.base import ContractModel

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
SHA256 = "a" * 64


def _contract_examples() -> tuple[ContractModel, ...]:
    return (
        WorkflowDefinition(
            workflow_definition_id="workflow.comparative-research",
            workflow_version="1.2.0",
            name="Comparative research",
            description="Compare named subjects using source-grounded research.",
            control_mode=WorkflowControlMode.PREDEFINED,
            task_types=("source_discovery", "synthesis"),
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            default_routing_policy_id="routing.research-default",
            default_routing_policy_version="1.0.0",
            created_at=NOW,
        ),
        RoutingPolicy(
            routing_policy_id="routing.research-default",
            routing_policy_version="1.0.0",
            strategy="quality_focused",
            collaboration_mode="hierarchical",
            worker_types=("literature_review", "synthesis"),
            max_parallel_tasks=4,
            max_attempts_per_task=2,
            task_timeout_seconds=300,
            provider_allowlist=("openrouter",),
            model_allowlist=("openai/gpt-5",),
        ),
        Run(
            run_id="run-001",
            tenant_id="tenant-001",
            workflow_definition_id="workflow.comparative-research",
            workflow_definition_version="1.2.0",
            routing_policy_id="routing.research-default",
            routing_policy_version="1.0.0",
            idempotency_key="request-001",
            requested_by="user-001",
            created_at=NOW,
            updated_at=NOW,
        ),
        Task(
            task_id="task-001",
            run_id="run-001",
            task_key="discover-sources",
            task_type="source_discovery",
            objective="Find primary sources.",
            idempotency_key="run-001:discover-sources",
            assigned_worker_type="literature_review",
            input={"query": "alpha versus beta"},
            created_at=NOW,
            updated_at=NOW,
        ),
        Attempt(
            attempt_id="attempt-001",
            task_id="task-001",
            ordinal=1,
            idempotency_key="task-001:1",
            executor_id="worker-001",
            created_at=NOW,
            updated_at=NOW,
        ),
        ToolInvocation(
            tool_invocation_id="tool-001",
            run_id="run-001",
            task_id="task-001",
            attempt_id="attempt-001",
            tool_name="web-search",
            tool_version="1.0.0",
            status=ToolInvocationStatus.REQUESTED,
            capability_scope="research:read",
            idempotency_key="attempt-001:web-search:001",
            input={"query": "primary source"},
            input_trust=TrustClassification.USER_SUPPLIED,
            requested_at=NOW,
        ),
        Artifact(
            artifact_id="artifact-001",
            run_id="run-001",
            task_id="task-001",
            attempt_id="attempt-001",
            kind="source_snapshot",
            media_type="text/html",
            storage_uri="postgres://artifacts/artifact-001",
            content_sha256=SHA256,
            status=ArtifactStatus.FINAL,
            trust=TrustClassification.EXTERNAL_UNTRUSTED,
            producer="tool:web-search",
            created_at=NOW,
        ),
        Evidence(
            evidence_id="evidence-001",
            run_id="run-001",
            task_id="task-001",
            source_type="web",
            source_uri="https://example.test/source",
            snapshot_artifact_id="artifact-001",
            content_sha256=SHA256,
            locator="paragraph=4",
            trust=TrustClassification.EXTERNAL_UNTRUSTED,
            producer_tool_invocation_id="tool-001",
            acquired_at=NOW,
        ),
        ClaimSupport(
            claim_support_id="support-001",
            run_id="run-001",
            artifact_id="artifact-report-001",
            claim_id="claim-001",
            claim_text="Alpha has the larger documented value.",
            status=ClaimSupportStatus.SUPPORTED,
            evidence_ids=("evidence-001",),
            evaluator_id="claim-entailment",
            evaluator_version="1.0.0",
            explanation="The source states the compared values.",
            evaluated_at=NOW,
        ),
        EvaluationResult(
            evaluation_result_id="evaluation-001",
            run_id="run-001",
            artifact_id="artifact-report-001",
            evaluator_id="citation-resolution",
            evaluator_version="1.0.0",
            dimension="citation_resolution",
            status=EvaluationStatus.PASSED,
            deterministic=True,
            score=1.0,
            details={"resolved": 4, "total": 4},
            completed_at=NOW,
        ),
        RunEvent(
            event_id="event-001",
            run_id="run-001",
            aggregate_type="task",
            aggregate_id="task-001",
            sequence=1,
            event_type="task.created",
            event_type_version="1.0",
            occurred_at=NOW,
            producer="research-kernel",
            deduplication_key="task-001:created",
            correlation_id="request-001",
            payload={"task_type": "source_discovery"},
        ),
    )


def test_all_canonical_contracts_round_trip_deterministically() -> None:
    examples = _contract_examples()

    assert {type(contract).__name__ for contract in examples} == {
        "WorkflowDefinition",
        "Run",
        "Task",
        "Attempt",
        "RoutingPolicy",
        "ToolInvocation",
        "Evidence",
        "ClaimSupport",
        "Artifact",
        "EvaluationResult",
        "RunEvent",
    }

    for contract in examples:
        serialized = contract.model_dump_json()
        restored = type(contract).model_validate_json(serialized)

        assert restored == contract
        assert restored.model_dump_json() == serialized


def test_routing_policy_nested_metadata_mappings_are_immutable() -> None:
    source = {"routing": {"quality": "high"}}
    policy = RoutingPolicy(
        routing_policy_id="routing.research-default",
        routing_policy_version="1.0.0",
        strategy="quality_focused",
        collaboration_mode="hierarchical",
        max_parallel_tasks=4,
        max_attempts_per_task=2,
        task_timeout_seconds=300,
        metadata=source,
    )
    nested = cast(Any, policy.metadata["routing"])

    with pytest.raises(TypeError):
        nested["quality"] = "low"

    source["routing"]["quality"] = "source-mutated"
    assert cast(Any, policy.metadata["routing"])["quality"] == "high"


def test_run_event_nested_payload_sequences_are_immutable() -> None:
    source = {"steps": [{"task": "discover"}, "synthesize"]}
    event = RunEvent(
        event_id="event-001",
        run_id="run-001",
        aggregate_type="run",
        aggregate_id="run-001",
        sequence=1,
        event_type="run.planned",
        event_type_version="1.0",
        occurred_at=NOW,
        producer="research-kernel",
        deduplication_key="run-001:planned",
        payload=source,
    )
    steps = cast(Any, event.payload["steps"])

    with pytest.raises(AttributeError):
        steps.append("publish")
    with pytest.raises(TypeError):
        steps[0]["task"] = "mutated"

    source["steps"].append("source-mutated")
    assert event.payload["steps"] == ({"task": "discover"}, "synthesize")


@pytest.mark.parametrize(
    "nonfinite_value",
    (float("nan"), float("inf"), float("-inf")),
    ids=("nan", "positive-infinity", "negative-infinity"),
)
def test_routing_policy_rejects_nonfinite_nested_metadata_numbers(
    nonfinite_value: float,
) -> None:
    payload = {
        "routing_policy_id": "routing.research-default",
        "routing_policy_version": "1.0.0",
        "strategy": "quality_focused",
        "collaboration_mode": "hierarchical",
        "max_parallel_tasks": 4,
        "max_attempts_per_task": 2,
        "task_timeout_seconds": 300,
        "metadata": {"routing": {"weight": nonfinite_value}},
    }

    with pytest.raises(ValidationError):
        RoutingPolicy.model_validate(payload)
    with pytest.raises(ValidationError):
        RoutingPolicy.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "nonfinite_value",
    (float("nan"), float("inf"), float("-inf")),
    ids=("nan", "positive-infinity", "negative-infinity"),
)
def test_run_event_rejects_nonfinite_nested_payload_numbers(
    nonfinite_value: float,
) -> None:
    payload = {
        "event_id": "event-001",
        "run_id": "run-001",
        "aggregate_type": "run",
        "aggregate_id": "run-001",
        "sequence": 1,
        "event_type": "run.planned",
        "event_type_version": "1.0",
        "occurred_at": NOW.isoformat(),
        "producer": "research-kernel",
        "deduplication_key": "run-001:planned",
        "payload": {"steps": [{"weight": nonfinite_value}]},
    }

    with pytest.raises(ValidationError):
        RunEvent.model_validate(payload)
    with pytest.raises(ValidationError):
        RunEvent.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "contract",
    [
        RoutingPolicy(
            routing_policy_id="routing.research-default",
            routing_policy_version="1.0.0",
            strategy="quality_focused",
            collaboration_mode="hierarchical",
            max_parallel_tasks=4,
            max_attempts_per_task=2,
            task_timeout_seconds=300,
            metadata={"routing": {"fallbacks": ["openrouter", "gemini"]}},
        ),
        RunEvent(
            event_id="event-001",
            run_id="run-001",
            aggregate_type="run",
            aggregate_id="run-001",
            sequence=1,
            event_type="run.planned",
            event_type_version="1.0",
            occurred_at=NOW,
            producer="research-kernel",
            deduplication_key="run-001:planned",
            payload={"steps": [{"task": "discover"}, "synthesize"]},
        ),
    ],
)
def test_nested_immutable_json_round_trips_to_the_same_json_shape(
    contract: ContractModel,
) -> None:
    serialized = contract.model_dump_json()
    restored = type(contract).model_validate_json(serialized)

    assert restored == contract
    assert restored.model_dump_json() == serialized
    assert isinstance(restored.model_dump(mode="json"), dict)


@pytest.mark.parametrize("contract", _contract_examples())
def test_contracts_reject_an_incompatible_schema_version(
    contract: ContractModel,
) -> None:
    payload = contract.model_dump(mode="json")
    payload["schema_version"] = "2.0"

    with pytest.raises(ValidationError, match=r"Input should be '1\.0'"):
        type(contract).model_validate(payload)
