"""Frozen-schema characterization for the Wave 4 provenance/capability tables.

Mirrors ``test_durable_lifecycle_schema.py``'s SQLite-backed approach: these
tests prove what SQLAlchemy declares — table names, contract field alignment,
CHECK-constraint value domains, uniqueness scopes, and ORM-level append-only
enforcement — fast and without Docker. They cannot prove what a real Postgres
server enforces at the trigger level; that is
``tests/integration/test_wave4_persistence_migration.py``'s job.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, Table, UniqueConstraint, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.core.contracts import (
    AbsentEvidenceReason,
    ArtifactStatus,
    ClaimSupportStatus,
    ProducerKind,
    SensitivityClass,
    ToolInvocationStatus,
    TrustClassification,
)
from src.models.db.append_only import AppendOnlyViolationError
from src.models.db.artifact import AgentArtifact
from src.models.db.base import Base
from src.models.db.capability import AgentCapabilityApproval, AgentCapabilityGrant
from src.models.db.claim_support import AgentClaimSupport
from src.models.db.evidence import AgentEvidence
from src.models.db.run_lifecycle import AgentRun, AgentRunTask, AgentTaskAttempt
from src.models.db.tool_invocation import AgentToolInvocation

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(seconds=30)

WAVE4_TABLES = (
    AgentCapabilityGrant,
    AgentCapabilityApproval,
    AgentArtifact,
    AgentToolInvocation,
    AgentEvidence,
    AgentClaimSupport,
)
ALL_TABLES = (AgentRun, AgentRunTask, AgentTaskAttempt, *WAVE4_TABLES)


@pytest.fixture(name="engine")
def engine_fixture() -> Iterator[Engine]:
    """An isolated database holding the run lifecycle plus Wave 4 tables."""
    engine = create_engine("sqlite://")
    tables = [model.__table__ for model in ALL_TABLES]
    Base.metadata.create_all(engine, tables=tables)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(name="session")
def session_fixture(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        yield session


def _unique_scopes(table: Table) -> set[frozenset[str]]:
    return {
        frozenset(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


# --- fixture builders --------------------------------------------------------


def _make_run(**overrides: object) -> AgentRun:
    values: dict[str, object] = {
        "run_id": "run-1",
        "tenant_id": "tenant-1",
        "workflow_definition_id": "research",
        "workflow_definition_version": "1",
        "routing_policy_id": "default",
        "routing_policy_version": "1",
        "idempotency_key": "submit-1",
        "requested_by": "user-1",
        "status": "created",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return AgentRun(**values)


def _make_task(**overrides: object) -> AgentRunTask:
    values: dict[str, object] = {
        "task_id": "task-1",
        "run_id": "run-1",
        "task_key": "literature-review",
        "task_type": "research",
        "objective": "Survey the field",
        "idempotency_key": "task-key-1",
        "status": "pending",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return AgentRunTask(**values)


def _make_attempt(**overrides: object) -> AgentTaskAttempt:
    values: dict[str, object] = {
        "attempt_id": "attempt-1",
        "task_id": "task-1",
        "run_id": "run-1",
        "ordinal": 1,
        "idempotency_key": "attempt-key-1",
        "status": "created",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return AgentTaskAttempt(**values)


def _seed_run_task_attempt(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_task())
    session.add(_make_attempt())
    session.flush()


def _make_grant(**overrides: object) -> AgentCapabilityGrant:
    values: dict[str, object] = {
        "grant_id": "grant-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "capability_scope": "search",
        "tool_name": "academic_search",
        "tool_versions": ["1.0"],
        "sensitivity": SensitivityClass.READ_ONLY.value,
        "max_input_trust": TrustClassification.USER_SUPPLIED.value,
        "requires_approval": False,
        "issued_at": NOW,
        "expires_at": LATER,
    }
    values.update(overrides)
    return AgentCapabilityGrant(**values)


def _make_approval(**overrides: object) -> AgentCapabilityApproval:
    values: dict[str, object] = {
        "approval_id": "approval-1",
        "grant_id": "grant-1",
        "request_fingerprint": "a" * 64,
        "approved_by": "user-1",
        "approved_at": NOW,
        "expires_at": LATER,
    }
    values.update(overrides)
    return AgentCapabilityApproval(**values)


def _make_artifact(**overrides: object) -> AgentArtifact:
    values: dict[str, object] = {
        "artifact_id": "artifact-1",
        "run_id": "run-1",
        "kind": "source_snapshot",
        "media_type": "text/html",
        "storage_uri": "s3://bucket/key",
        "content_sha256": "a" * 64,
        "status": ArtifactStatus.FINAL.value,
        "trust": TrustClassification.EXTERNAL_UNTRUSTED.value,
        "producer": "acquisition-tool",
        "metadata_": {},
        "producer_kind": ProducerKind.SYSTEM.value,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return AgentArtifact(**values)


def _make_invocation(**overrides: object) -> AgentToolInvocation:
    values: dict[str, object] = {
        "tool_invocation_id": "invocation-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "tool_name": "academic_search",
        "tool_version": "1.0",
        "status": ToolInvocationStatus.SUCCEEDED.value,
        "capability_scope": "search",
        "idempotency_key": "invocation-key-1",
        "input": {"query": "graph neural networks"},
        "input_trust": TrustClassification.USER_SUPPLIED.value,
        "output": {"results": []},
        "output_trust": TrustClassification.EXTERNAL_UNTRUSTED.value,
        "capability_decision_effect": "allow",
        "producer_kind": ProducerKind.SYSTEM.value,
        "requested_at": NOW,
        "completed_at": LATER,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return AgentToolInvocation(**values)


def _make_evidence(**overrides: object) -> AgentEvidence:
    values: dict[str, object] = {
        "evidence_id": "evidence-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "source_type": "web_page",
        "source_uri": "https://example.org/paper",
        "snapshot_artifact_id": "artifact-1",
        "content_sha256": "a" * 64,
        "locator": "char:0-120",
        "trust": TrustClassification.EXTERNAL_UNTRUSTED.value,
        "prompt_id": "prompt-1",
        "prompt_version": "1.0",
        "template_sha256": "b" * 64,
        "rendered_sha256": "c" * 64,
        "parent_evidence_ids": [],
        "acquired_at": NOW,
    }
    values.update(overrides)
    return AgentEvidence(**values)


def _make_claim_support(**overrides: object) -> AgentClaimSupport:
    values: dict[str, object] = {
        "claim_support_id": "claim-support-1",
        "run_id": "run-1",
        "artifact_id": "artifact-1",
        "claim_id": "claim-1",
        "claim_text": "The model improves accuracy by 5%.",
        "status": ClaimSupportStatus.SUPPORTED.value,
        "evidence_ids": ["evidence-1"],
        "evidence_count": 1,
        "absent_evidence_reason": None,
        "evaluator_id": "evaluator-1",
        "evaluator_version": "1.0",
        "prompt_id": "prompt-2",
        "prompt_version": "1.0",
        "template_sha256": "d" * 64,
        "rendered_sha256": "e" * 64,
        "explanation": "Table 3 reports a 5% accuracy gain.",
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return AgentClaimSupport(**values)


# --- table identity -----------------------------------------------------------


def test_wave4_table_names_are_frozen() -> None:
    assert [model.__tablename__ for model in WAVE4_TABLES] == [
        "agent_capability_grants",
        "agent_capability_approvals",
        "agent_artifacts",
        "agent_tool_invocations",
        "agent_evidence",
        "agent_claim_supports",
    ]


@pytest.mark.parametrize(
    "model",
    WAVE4_TABLES,
    ids=[m.__tablename__ for m in WAVE4_TABLES],
)
def test_every_wave4_table_carries_the_tenant_boundary_column(model: type) -> None:
    column = model.__table__.c.organization_id
    assert column.nullable is True


# --- capability grants ---------------------------------------------------------


def test_a_grant_persists(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_task())
    session.flush()
    session.add(_make_grant())
    session.flush()


def test_grant_sensitivity_domain_is_frozen(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_task())
    session.flush()
    session.add(_make_grant(sensitivity="teleported"))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize(
    "sensitivity",
    [SensitivityClass.EXTERNAL_WRITE.value, SensitivityClass.EXFILTRATION.value],
)
def test_a_sensitive_grant_cannot_waive_approval(
    session: Session, sensitivity: str
) -> None:
    session.add(_make_run())
    session.add(_make_task())
    session.flush()
    session.add(_make_grant(sensitivity=sensitivity, requires_approval=False))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize(
    "sensitivity",
    [SensitivityClass.EXTERNAL_WRITE.value, SensitivityClass.EXFILTRATION.value],
)
def test_a_sensitive_grant_with_approval_required_is_accepted(
    session: Session, sensitivity: str
) -> None:
    session.add(_make_run())
    session.add(_make_task())
    session.flush()
    session.add(_make_grant(sensitivity=sensitivity, requires_approval=True))
    session.flush()


def test_grant_expiry_must_be_after_issuance(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_task())
    session.flush()
    session.add(_make_grant(issued_at=LATER, expires_at=NOW))
    with pytest.raises(IntegrityError):
        session.flush()


# --- capability approvals -------------------------------------------------------


def test_an_approval_persists(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_task())
    session.flush()
    session.add(_make_grant())
    session.flush()
    session.add(_make_approval())
    session.flush()


def test_approval_uniqueness_scope_is_grant_and_fingerprint() -> None:
    assert frozenset({"grant_id", "request_fingerprint"}) in _unique_scopes(
        AgentCapabilityApproval.__table__
    )


def test_the_same_grant_cannot_reuse_a_request_fingerprint(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_task())
    session.flush()
    session.add(_make_grant())
    session.flush()
    session.add(_make_approval())
    session.flush()
    session.add(_make_approval(approval_id="approval-2"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_different_grant_may_reuse_a_request_fingerprint(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_task())
    session.flush()
    session.add(_make_grant())
    session.add(_make_grant(grant_id="grant-2"))
    session.flush()
    session.add(_make_approval())
    session.add(_make_approval(approval_id="approval-2", grant_id="grant-2"))
    session.flush()


# --- artifacts -------------------------------------------------------------------


def test_an_artifact_persists(session: Session) -> None:
    session.add(_make_run())
    session.flush()
    session.add(_make_artifact())
    session.flush()


def test_artifact_status_domain_is_frozen(session: Session) -> None:
    session.add(_make_run())
    session.flush()
    session.add(_make_artifact(status="teleported"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_artifact_digest_must_be_64_hex_characters(session: Session) -> None:
    session.add(_make_run())
    session.flush()
    session.add(_make_artifact(content_sha256="short"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_an_artifact_is_mutable(session: Session) -> None:
    session.add(_make_run())
    session.flush()
    artifact = _make_artifact(status=ArtifactStatus.DRAFT.value)
    session.add(artifact)
    session.flush()

    artifact.status = ArtifactStatus.FINAL.value
    session.flush()

    assert artifact.status == ArtifactStatus.FINAL.value


# --- prompt binding: all-or-nothing + producer_kind biconditional --------------


@pytest.mark.parametrize(
    "model,factory",
    [(AgentArtifact, _make_artifact), (AgentToolInvocation, _make_invocation)],
    ids=["artifact", "tool_invocation"],
)
def test_prompt_binding_columns_are_all_or_nothing(
    session: Session, model: type, factory: object
) -> None:
    _seed_run_task_attempt(session)
    row = factory(  # type: ignore[operator]
        prompt_id="prompt-x",
        prompt_version=None,
        template_sha256=None,
        rendered_sha256=None,
        producer_kind=ProducerKind.MODEL_TURN.value,
    )
    session.add(row)
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize(
    "model,factory",
    [(AgentArtifact, _make_artifact), (AgentToolInvocation, _make_invocation)],
    ids=["artifact", "tool_invocation"],
)
def test_a_model_turn_row_must_name_its_prompt(
    session: Session, model: type, factory: object
) -> None:
    _seed_run_task_attempt(session)
    row = factory(producer_kind=ProducerKind.MODEL_TURN.value)  # type: ignore[operator]
    session.add(row)
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize(
    "model,factory",
    [(AgentArtifact, _make_artifact), (AgentToolInvocation, _make_invocation)],
    ids=["artifact", "tool_invocation"],
)
def test_a_system_row_must_not_carry_a_prompt(
    session: Session, model: type, factory: object
) -> None:
    _seed_run_task_attempt(session)
    row = factory(  # type: ignore[operator]
        producer_kind=ProducerKind.SYSTEM.value,
        prompt_id="prompt-x",
        prompt_version="1.0",
        template_sha256="b" * 64,
        rendered_sha256="c" * 64,
    )
    session.add(row)
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize(
    "model,factory",
    [(AgentArtifact, _make_artifact), (AgentToolInvocation, _make_invocation)],
    ids=["artifact", "tool_invocation"],
)
def test_a_complete_model_turn_binding_is_accepted(
    session: Session, model: type, factory: object
) -> None:
    _seed_run_task_attempt(session)
    row = factory(  # type: ignore[operator]
        producer_kind=ProducerKind.MODEL_TURN.value,
        prompt_id="prompt-x",
        prompt_version="1.0",
        template_sha256="b" * 64,
        rendered_sha256="c" * 64,
    )
    session.add(row)
    session.flush()


# --- tool invocations ---------------------------------------------------------


def test_a_tool_invocation_persists(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_invocation())
    session.flush()


def test_tool_invocation_idempotency_scope_is_frozen() -> None:
    assert frozenset({"attempt_id", "idempotency_key"}) in _unique_scopes(
        AgentToolInvocation.__table__
    )


def test_an_attempt_cannot_reuse_an_invocation_idempotency_key(
    session: Session,
) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_invocation())
    session.flush()
    session.add(_make_invocation(tool_invocation_id="invocation-2"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_capability_decision_effect_domain_is_frozen(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_invocation(capability_decision_effect="teleported"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_denied_decision_requires_a_denied_invocation_status(
    session: Session,
) -> None:
    _seed_run_task_attempt(session)
    session.add(
        _make_invocation(
            capability_decision_effect="deny",
            status=ToolInvocationStatus.FAILED.value,
            error_code="denied",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_denied_decision_with_a_denied_status_is_accepted(
    session: Session,
) -> None:
    _seed_run_task_attempt(session)
    session.add(
        _make_invocation(
            capability_decision_effect="deny",
            status=ToolInvocationStatus.DENIED.value,
            error_code="capability_denied",
            output=None,
            output_trust=None,
        )
    )
    session.flush()


def test_an_allowed_decision_permits_any_status(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(
        _make_invocation(
            capability_decision_effect="allow",
            status=ToolInvocationStatus.SUCCEEDED.value,
        )
    )
    session.flush()


def test_output_trust_may_be_absent(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(
        _make_invocation(
            status=ToolInvocationStatus.RUNNING.value,
            output=None,
            output_trust=None,
            completed_at=None,
            error_code=None,
        )
    )
    session.flush()


def test_output_trust_domain_is_frozen_when_present(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_invocation(output_trust="teleported"))
    with pytest.raises(IntegrityError):
        session.flush()


# --- evidence: append-only + span uniqueness -----------------------------------


def test_evidence_persists(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    session.add(_make_evidence())
    session.flush()


def test_evidence_span_uniqueness_scope_is_frozen() -> None:
    assert frozenset({"run_id", "snapshot_artifact_id", "locator"}) in _unique_scopes(
        AgentEvidence.__table__
    )


def test_the_same_span_cannot_be_recorded_twice(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    session.add(_make_evidence())
    session.flush()
    session.add(_make_evidence(evidence_id="evidence-2"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_different_locator_on_the_same_artifact_is_a_distinct_span(
    session: Session,
) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    session.add(_make_evidence())
    session.add(_make_evidence(evidence_id="evidence-2", locator="char:120-240"))
    session.flush()


def test_evidence_digest_must_be_64_hex_characters(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    session.add(_make_evidence(content_sha256="short"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_evidence_cannot_be_updated(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    evidence = _make_evidence()
    session.add(evidence)
    session.flush()

    evidence.locator = "char:0-999"
    with pytest.raises(AppendOnlyViolationError):
        session.flush()


def test_evidence_cannot_be_deleted(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    evidence = _make_evidence()
    session.add(evidence)
    session.flush()

    session.delete(evidence)
    with pytest.raises(AppendOnlyViolationError):
        session.flush()


# --- claim supports: the seven CHECK constraints -------------------------------


def test_a_supported_claim_persists(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    session.add(_make_claim_support())
    session.flush()


def test_claim_support_status_domain_is_frozen(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    session.add(_make_claim_support(status="teleported"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_absent_evidence_reason_domain_is_frozen(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    session.add(
        _make_claim_support(
            status=ClaimSupportStatus.UNSUPPORTED.value,
            evidence_ids=[],
            evidence_count=0,
            absent_evidence_reason="teleported",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("reason", list(AbsentEvidenceReason))
def test_every_typed_absent_reason_is_accepted(
    session: Session, reason: AbsentEvidenceReason
) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    session.add(
        _make_claim_support(
            claim_support_id=f"claim-support-{reason.value}",
            status=ClaimSupportStatus.UNSUPPORTED.value,
            evidence_ids=[],
            evidence_count=0,
            absent_evidence_reason=reason.value,
        )
    )
    session.flush()


def test_evidence_count_cannot_be_negative(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    session.add(_make_claim_support(evidence_count=-1))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize(
    "status",
    [
        ClaimSupportStatus.SUPPORTED.value,
        ClaimSupportStatus.PARTIALLY_SUPPORTED.value,
        ClaimSupportStatus.DISPUTED.value,
    ],
)
def test_an_evidence_bearing_status_requires_evidence(
    session: Session, status: str
) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    session.add(_make_claim_support(status=status, evidence_ids=[], evidence_count=0))
    with pytest.raises(IntegrityError):
        session.flush()


def test_unsupported_with_no_evidence_requires_a_reason(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    session.add(
        _make_claim_support(
            status=ClaimSupportStatus.UNSUPPORTED.value,
            evidence_ids=[],
            evidence_count=0,
            absent_evidence_reason=None,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_unsupported_may_cite_evidence_without_a_reason(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    session.add(
        _make_claim_support(
            status=ClaimSupportStatus.UNSUPPORTED.value,
            evidence_ids=["evidence-1"],
            evidence_count=1,
            absent_evidence_reason=None,
        )
    )
    session.flush()


def test_a_reason_cannot_accompany_cited_evidence(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    session.add(
        _make_claim_support(
            status=ClaimSupportStatus.UNSUPPORTED.value,
            evidence_ids=["evidence-1"],
            evidence_count=1,
            absent_evidence_reason=AbsentEvidenceReason.NOT_ATTEMPTED.value,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_reason_cannot_accompany_a_supported_status(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    # Contrived: evidence_count=0 with an evidence-bearing status is already
    # rejected by constraint (4); this proves constraint (7) still blocks the
    # reason-on-a-non-unsupported-row case that (4)+(6) alone leave to (1)'s
    # exhaustiveness rather than a direct rule. See the model's docstring.
    session.add(
        _make_claim_support(
            status=ClaimSupportStatus.SUPPORTED.value,
            evidence_ids=["evidence-1"],
            evidence_count=1,
            absent_evidence_reason=AbsentEvidenceReason.NOT_ATTEMPTED.value,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_claim_support_cannot_be_updated(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    claim = _make_claim_support()
    session.add(claim)
    session.flush()

    claim.status = ClaimSupportStatus.DISPUTED.value
    with pytest.raises(AppendOnlyViolationError):
        session.flush()


def test_claim_support_cannot_be_deleted(session: Session) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    claim = _make_claim_support()
    session.add(claim)
    session.flush()

    session.delete(claim)
    with pytest.raises(AppendOnlyViolationError):
        session.flush()


def test_a_reevaluation_appends_a_new_row_rather_than_replacing(
    session: Session,
) -> None:
    _seed_run_task_attempt(session)
    session.add(_make_artifact())
    session.flush()
    session.add(_make_claim_support())
    session.add(
        _make_claim_support(
            claim_support_id="claim-support-2",
            status=ClaimSupportStatus.DISPUTED.value,
            evaluated_at=LATER,
        )
    )
    session.flush()

    rows = (
        session.query(AgentClaimSupport)
        .filter(AgentClaimSupport.claim_id == "claim-1")
        .all()
    )
    assert len(rows) == 2
