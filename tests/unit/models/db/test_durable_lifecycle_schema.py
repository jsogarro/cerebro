"""Frozen-schema characterization for the durable run lifecycle tables.

These tests pin the persistence contract that the rest of the durable
lifecycle work is written against: table names, contract field alignment,
idempotency-key uniqueness scope, per-run event sequence monotonicity,
append-only enforcement, config-snapshot immutability, and version pinning.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, Table, UniqueConstraint, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.core.contracts import (
    Attempt,
    AttemptStatus,
    PinnedComponentKind,
    PinnedComponentVersion,
    PinnedVersions,
    Run,
    RunEvent,
    RunStatus,
    Task,
    TaskStatus,
)
from src.models.db.append_only import AppendOnlyViolationError
from src.models.db.base import Base
from src.models.db.run_config_snapshot import AgentRunConfigSnapshot
from src.models.db.run_event import (
    AgentRunEvent,
    AgentRunEventOutbox,
    EventDeliveryStatus,
)
from src.models.db.run_lifecycle import AgentRun, AgentRunTask, AgentTaskAttempt
from src.models.db.workflow_checkpoint import WorkflowCheckpoint

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(seconds=30)

DURABLE_TABLES = (
    AgentRun,
    AgentRunTask,
    AgentTaskAttempt,
    AgentRunConfigSnapshot,
    AgentRunEvent,
    AgentRunEventOutbox,
)

PINNED_VERSIONS = PinnedVersions(
    workflow_definition_id="research",
    workflow_definition_version="1",
    routing_policy_id="default",
    routing_policy_version="1",
    event_envelope_version="1.0",
    components=(
        PinnedComponentVersion(
            kind=PinnedComponentKind.PROMPT,
            component_id="research.synthesis",
            version="3",
        ),
    ),
)


@pytest.fixture(name="engine")
def engine_fixture() -> Iterator[Engine]:
    """An isolated database holding only the durable lifecycle tables."""
    engine = create_engine("sqlite://")
    tables = [model.__table__ for model in DURABLE_TABLES]
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
        "status": RunStatus.CREATED.value,
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
        "status": TaskStatus.PENDING.value,
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
        "status": AttemptStatus.CREATED.value,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return AgentTaskAttempt(**values)


def _make_event(**overrides: object) -> AgentRunEvent:
    values: dict[str, object] = {
        "event_id": "event-1",
        "run_id": "run-1",
        "aggregate_type": "run",
        "aggregate_id": "run-1",
        "sequence": 1,
        "event_type": "run.created",
        "event_type_version": "1",
        "occurred_at": NOW,
        "producer": "direct-execution-service",
        "deduplication_key": "run-1:1:run.created",
        "payload": {"status": "created"},
    }
    values.update(overrides)
    return AgentRunEvent(**values)


def _make_snapshot(**overrides: object) -> AgentRunConfigSnapshot:
    values: dict[str, object] = {
        "config_snapshot_id": "snapshot-1",
        "run_id": "run-1",
        "tenant_id": "tenant-1",
        "authority_id": "authority-1",
        "authority_version": "1",
        "workflow_definition_id": "research",
        "workflow_definition_version": "1",
        "routing_policy_id": "default",
        "routing_policy_version": "1",
        "pinned_versions": PINNED_VERSIONS.model_dump(mode="json"),
        "configuration": {"domains": ["research"]},
        "content_sha256": "a" * 64,
    }
    values.update(overrides)
    return AgentRunConfigSnapshot(**values)


def _make_outbox(**overrides: object) -> AgentRunEventOutbox:
    values: dict[str, object] = {
        "event_id": "event-1",
        "run_id": "run-1",
        "destination": "websocket",
        "idempotency_key": "websocket:run-1:1:run.created",
        "payload": {"event_type": "run.created"},
        "status": EventDeliveryStatus.PENDING.value,
        "available_at": NOW,
    }
    values.update(overrides)
    return AgentRunEventOutbox(**values)


# --- table identity ---------------------------------------------------------


def test_durable_table_names_are_frozen() -> None:
    assert [model.__tablename__ for model in DURABLE_TABLES] == [
        "agent_runs",
        "agent_run_tasks",
        "agent_task_attempts",
        "agent_run_config_snapshots",
        "agent_run_events",
        "agent_run_event_outbox",
    ]


# --- contract alignment -----------------------------------------------------


@pytest.mark.parametrize(
    ("contract", "model", "renamed"),
    [
        (Run, AgentRun, {"schema_version": "contract_schema_version"}),
        (Task, AgentRunTask, {"schema_version": "contract_schema_version"}),
        (Attempt, AgentTaskAttempt, {"schema_version": "contract_schema_version"}),
        (RunEvent, AgentRunEvent, {"schema_version": "envelope_schema_version"}),
    ],
)
def test_every_contract_field_has_a_persisted_column(
    contract: type, model: type, renamed: dict[str, str]
) -> None:
    columns = set(model.__table__.columns.keys())
    expected = {renamed.get(name, name) for name in contract.model_fields}

    assert expected <= columns, expected - columns


@pytest.mark.parametrize(
    ("model", "statuses"),
    [
        (AgentRun, RunStatus),
        (AgentRunTask, TaskStatus),
        (AgentTaskAttempt, AttemptStatus),
    ],
)
def test_persisted_status_domain_matches_the_contract_state_machine(
    session: Session, model: type, statuses: type
) -> None:
    factory = {
        AgentRun: _make_run,
        AgentRunTask: _make_task,
        AgentTaskAttempt: _make_attempt,
    }[model]

    session.add(factory(status="not-a-status"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    for index, status in enumerate(statuses):
        session.add(factory(status=status.value, **_identity_for(model, index)))
    session.flush()


def _identity_for(model: type, index: int) -> dict[str, object]:
    if model is AgentRun:
        return {"run_id": f"run-{index}", "idempotency_key": f"submit-{index}"}
    if model is AgentRunTask:
        return {
            "task_id": f"task-{index}",
            "task_key": f"key-{index}",
            "idempotency_key": f"task-key-{index}",
        }
    return {
        "attempt_id": f"attempt-{index}",
        "ordinal": index + 1,
        "idempotency_key": f"attempt-key-{index}",
    }


# --- idempotency-key uniqueness scope ---------------------------------------


def test_idempotency_key_uniqueness_scopes_are_frozen() -> None:
    assert frozenset({"tenant_id", "idempotency_key"}) in _unique_scopes(
        AgentRun.__table__
    )
    assert frozenset({"run_id", "idempotency_key"}) in _unique_scopes(
        AgentRunTask.__table__
    )
    assert frozenset({"run_id", "task_key"}) in _unique_scopes(AgentRunTask.__table__)
    assert frozenset({"task_id", "idempotency_key"}) in _unique_scopes(
        AgentTaskAttempt.__table__
    )
    assert frozenset({"task_id", "ordinal"}) in _unique_scopes(
        AgentTaskAttempt.__table__
    )
    assert frozenset({"event_id", "destination"}) in _unique_scopes(
        AgentRunEventOutbox.__table__
    )


def test_the_same_submission_key_is_reusable_across_tenants(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_run(run_id="run-2", tenant_id="tenant-2"))

    session.flush()


def test_a_tenant_cannot_reuse_a_run_submission_key(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_run(run_id="run-2"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_run_cannot_reuse_a_task_idempotency_key(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_task())
    session.add(_make_task(task_id="task-2", task_key="other"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_task_cannot_reuse_an_attempt_ordinal(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_task())
    session.add(_make_attempt())
    session.add(_make_attempt(attempt_id="attempt-2", idempotency_key="attempt-key-2"))

    with pytest.raises(IntegrityError):
        session.flush()


# --- event stream: ordering and duplicate suppression -----------------------


def test_event_sequence_is_unique_per_run(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_event())
    session.add(
        _make_event(
            event_id="event-2",
            deduplication_key="run-1:1:run.queued",
            event_type="run.queued",
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_the_same_sequence_may_be_reused_by_a_different_run(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_run(run_id="run-2", idempotency_key="submit-2"))
    session.add(_make_event())
    session.add(
        _make_event(
            event_id="event-2",
            run_id="run-2",
            aggregate_id="run-2",
            deduplication_key="run-2:1:run.created",
        )
    )

    session.flush()


def test_event_deduplication_key_is_unique_per_run(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_event())
    session.add(_make_event(event_id="event-2", sequence=2))

    with pytest.raises(IntegrityError):
        session.flush()


def test_event_sequence_must_start_at_one(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_event(sequence=0))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_run_carries_the_sequence_high_water_mark(session: Session) -> None:
    run = _make_run()
    session.add(run)
    session.flush()

    assert run.last_event_sequence == 0


# --- append-only enforcement ------------------------------------------------


@pytest.mark.parametrize(
    "model", [AgentRunEvent, AgentRunConfigSnapshot], ids=["event", "config_snapshot"]
)
def test_append_only_records_expose_no_soft_delete_or_mutation_helpers(
    model: type,
) -> None:
    columns = set(model.__table__.columns.keys())

    assert "deleted_at" not in columns
    assert "updated_at" not in columns
    assert not hasattr(model, "soft_delete")
    assert not hasattr(model, "restore")


def test_a_recorded_event_cannot_be_updated(session: Session) -> None:
    session.add(_make_run())
    event = _make_event()
    session.add(event)
    session.flush()

    event.payload = {"status": "tampered"}
    with pytest.raises(AppendOnlyViolationError):
        session.flush()


def test_a_recorded_event_cannot_be_deleted(session: Session) -> None:
    session.add(_make_run())
    event = _make_event()
    session.add(event)
    session.flush()

    session.delete(event)
    with pytest.raises(AppendOnlyViolationError):
        session.flush()


def test_a_config_snapshot_cannot_be_updated(session: Session) -> None:
    session.add(_make_run())
    snapshot = _make_snapshot()
    session.add(snapshot)
    session.flush()

    snapshot.configuration = {"domains": ["tampered"]}
    with pytest.raises(AppendOnlyViolationError):
        session.flush()


def test_a_config_snapshot_cannot_be_deleted(session: Session) -> None:
    session.add(_make_run())
    snapshot = _make_snapshot()
    session.add(snapshot)
    session.flush()

    session.delete(snapshot)
    with pytest.raises(AppendOnlyViolationError):
        session.flush()


def test_a_run_has_at_most_one_config_snapshot(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_snapshot())
    session.add(_make_snapshot(config_snapshot_id="snapshot-2"))

    with pytest.raises(IntegrityError):
        session.flush()


# --- version pinning --------------------------------------------------------


def test_a_run_pins_its_workflow_and_routing_policy_versions(session: Session) -> None:
    run = _make_run()
    session.add(run)
    session.flush()

    assert run.workflow_definition_version == "1"
    assert run.routing_policy_version == "1"
    assert run.contract_schema_version == "1.0"


def test_a_config_snapshot_stores_typed_pinned_versions(session: Session) -> None:
    session.add(_make_run())
    snapshot = _make_snapshot()
    session.add(snapshot)
    session.flush()

    restored = PinnedVersions.model_validate(snapshot.pinned_versions)

    assert restored == PINNED_VERSIONS
    assert restored.version_of(PinnedComponentKind.PROMPT, "research.synthesis") == "3"


def test_a_config_snapshot_pins_the_authority_it_was_compiled_from(
    session: Session,
) -> None:
    session.add(_make_run())
    snapshot = _make_snapshot()
    session.add(snapshot)
    session.flush()

    assert snapshot.authority_id == "authority-1"
    assert snapshot.authority_version == "1"


# --- outbox -----------------------------------------------------------------


def test_an_event_is_queued_once_per_destination(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_event())
    session.add(_make_outbox())
    session.add(_make_outbox(destination="redis_pubsub", idempotency_key="redis:1"))
    session.flush()

    session.add(_make_outbox(idempotency_key="websocket:duplicate"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_outbox_rows_start_pending_with_no_delivery_attempts(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_event())
    row = _make_outbox()
    session.add(row)
    session.flush()

    assert row.status == EventDeliveryStatus.PENDING.value
    assert row.attempts == 0
    assert row.delivered_at is None


def test_outbox_delivery_status_domain_is_frozen(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_event())
    session.add(_make_outbox(status="teleported"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_delivered_outbox_row_records_when_it_was_delivered(
    session: Session,
) -> None:
    session.add(_make_run())
    session.add(_make_event())
    session.add(
        _make_outbox(status=EventDeliveryStatus.DELIVERED.value, delivered_at=None)
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_outbox_delivery_is_at_least_once_and_retriable(session: Session) -> None:
    session.add(_make_run())
    session.add(_make_event())
    row = _make_outbox()
    session.add(row)
    session.flush()

    row.attempts = 2
    row.available_at = LATER
    session.flush()

    assert row.attempts == 2


# --- tenant boundary --------------------------------------------------------


@pytest.mark.parametrize("model", DURABLE_TABLES)
def test_every_durable_table_carries_the_tenant_boundary_column(model: type) -> None:
    column = model.__table__.c.organization_id

    assert column.nullable is True


# --- checkpoint reuse -------------------------------------------------------


def test_workflow_checkpoints_can_be_linked_to_a_durable_run() -> None:
    column = WorkflowCheckpoint.__table__.c.run_id

    assert column.nullable is True
    assert {fk.column.table.name for fk in column.foreign_keys} == {"agent_runs"}
