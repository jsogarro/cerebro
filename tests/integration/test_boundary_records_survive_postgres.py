"""The records the boundary now produces, written to real Postgres.

**Why this file exists.** Every other test of
:class:`~src.core.tools.boundary.ToolBoundary` runs against an in-memory audit
store that appends to Python lists and enforces no CHECK constraints. A record
shape Postgres would reject therefore passes the entire boundary suite. The
reordering that made a rejected input carry a capability decision changes
exactly what goes onto that record, so "the unit tests are green" is not
evidence that the row can be written.

**What is exercised.** A real ``ToolBoundary.invoke`` — not a hand-built
``ToolInvocation`` — through an adapter into a real
``ToolInvocationRepository`` against the Postgres container. Hand-constructing
the contract would test my belief about the shape the boundary produces rather
than the shape itself, and the belief is the thing under suspicion.

**The constraint that had to be checked**, per
``src/models/db/tool_invocation.py``::

    ck_agent_tool_invocation_capability_denial:
        capability_decision_effect = 'allow' OR status = 'denied'

An unauthorized caller sending an invalid payload used to produce
``effect=NULL, status='failed'``, which the constraint admitted only by
three-valued logic: ``NULL = 'allow'`` is NULL, ``'failed' = 'denied'`` is
FALSE, and ``NULL OR FALSE`` is NULL — a CHECK rejects only on FALSE. The row
was admitted for the wrong reason. Denial precedence now makes that same call
produce ``effect='deny', status='denied'``, which is ``FALSE OR TRUE`` — TRUE
on a real evaluation rather than admitted by a gap. ``test_the_old_shape_was_
admitted_only_by_null_propagation`` pins the old behaviour so the improvement
is visible rather than asserted.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import (
    Attempt,
    AttemptStatus,
    CapabilityDecision,
    CapabilityGrant,
    SensitivityClass,
    ToolInvocation,
    TrustClassification,
)
from src.core.tools import (
    MappingSecretProvider,
    NullEventPublisher,
    ToolAuditEvent,
    ToolBoundary,
    ToolCallContext,
    ToolOutcomeStatus,
    ToolSpec,
)
from src.repositories.capability_repository import CapabilityRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from src.repositories.tool_invocation_repository import ToolInvocationRepository
from tests.integration.wave4_helpers import seed_run_task_attempt

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 6, tzinfo=UTC)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
RUN_ID = "run-1"
TASK_ID = "task-1"
ATTEMPT_ID = "attempt-1"
ATTEMPT_ID_2 = "attempt-2"
SHARED_KEY = "victim-chosen-key"
GRANT_ID = "grant-1"
SCOPE = "search"
TOOL = "academic_search"
VERSION = "1.0"


class SearchInput(BaseModel):
    query: str


class SearchOutput(BaseModel):
    hits: int


async def search(args: SearchInput, context: ToolCallContext) -> dict[str, Any]:
    return {"hits": 1}


@dataclass(slots=True)
class PostgresAuditStore:
    """The adapter the boundary has never had, built here to exercise the DDL.

    Deliberately thin and deliberately not shipped: it exists so a real
    ``invoke`` reaches a real table. It does no dedup lookup — every test here
    is a single call, and a fake lookup would be the kind of accommodating
    stand-in this whole file is written against.
    """

    repository: ToolInvocationRepository
    decisions: list[CapabilityDecision | None] = field(default_factory=list)
    inserted: set[str] = field(default_factory=set)

    async def find_invocation(
        self, *, run_id: str, organization_id: str | None, idempotency_key: str
    ) -> ToolInvocation | None:
        return None

    async def persist(
        self,
        *,
        invocation: ToolInvocation,
        events: Sequence[ToolAuditEvent],
        organization_id: str | None,
        capability_decision: CapabilityDecision | None,
    ) -> None:
        """Dispatch between insert and transition, which the protocol does not.

        Found by writing this adapter rather than by reading the protocol. An
        allowed call persists **twice** — a ``REQUESTED`` row, then the terminal
        state — and ``ToolInvocationRepository`` splits those across
        ``create_tool_invocation`` and ``record_transition`` because they are one
        row (``UniqueConstraint(attempt_id, idempotency_key)``). But
        ``ToolAuditStore.persist`` is a *single* method with nothing in its
        signature or its docstring distinguishing the two, so the obvious
        adapter inserts twice and hits the unique violation. The denial and
        rejection paths hide this: they terminate before the ``REQUESTED``
        write and persist exactly once.
        """

        self.decisions.append(capability_decision)
        if invocation.tool_invocation_id in self.inserted:
            await self.repository.record_transition(
                invocation, organization_id=organization_id
            )
        else:
            await self.repository.create_tool_invocation(
                invocation,
                organization_id=organization_id,
                capability_decision=capability_decision,
            )
            self.inserted.add(invocation.tool_invocation_id)
        await self.repository.session.flush()


@pytest_asyncio.fixture(name="seeded", autouse=True)
async def seeded_fixture(db_session: AsyncSession) -> None:
    await seed_run_task_attempt(db_session, organization_id=ORG_ID)
    # A second attempt, seeded inline rather than by extending the shared
    # `wave4_helpers` -- four other packets' test modules import that file.
    await RunLifecycleRepository(db_session).create_attempt(
        Attempt(
            attempt_id=ATTEMPT_ID_2,
            task_id=TASK_ID,
            ordinal=2,
            idempotency_key=f"attempt-key-{ATTEMPT_ID_2}",
            status=AttemptStatus.CREATED,
            created_at=NOW,
            updated_at=NOW,
        ),
        run_id=RUN_ID,
        organization_id=ORG_ID,
    )
    await CapabilityRepository(db_session).create_grant(
        CapabilityGrant(
            grant_id=GRANT_ID,
            run_id=RUN_ID,
            task_id=TASK_ID,
            capability_scope=SCOPE,
            tool_name=TOOL,
            tool_versions=(VERSION,),
            sensitivity=SensitivityClass.READ_ONLY,
            max_input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
            requires_approval=False,
            issued_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(days=365),
        ),
        organization_id=ORG_ID,
    )


@pytest_asyncio.fixture(name="store")
async def store_fixture(db_session: AsyncSession) -> PostgresAuditStore:
    return PostgresAuditStore(repository=ToolInvocationRepository(db_session))


@pytest.fixture(name="boundary")
def boundary_fixture(store: PostgresAuditStore) -> ToolBoundary:
    built = ToolBoundary(
        secret_provider=MappingSecretProvider({}),
        audit_store=store,
        event_publisher=NullEventPublisher(),
        clock=lambda: NOW,
    )
    built.register(
        ToolSpec(
            name=TOOL,
            version=VERSION,
            sensitivity=SensitivityClass.READ_ONLY,
            input_model=SearchInput,
            output_model=SearchOutput,
            timeout_seconds=5.0,
            handler=search,  # type: ignore[arg-type]
        )
    )
    return built


def call_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tool_name": TOOL,
        "run_id": RUN_ID,
        "task_id": TASK_ID,
        "attempt_id": ATTEMPT_ID,
        "organization_id": str(ORG_ID),
        "capability_scope": SCOPE,
        "arguments": {"query": "graph neural networks"},
        "input_trust": TrustClassification.USER_SUPPLIED,
        "grants": [],
    }
    base.update(overrides)
    return base


def granted() -> list[CapabilityGrant]:
    return [
        CapabilityGrant(
            grant_id=GRANT_ID,
            run_id=RUN_ID,
            task_id=TASK_ID,
            capability_scope=SCOPE,
            tool_name=TOOL,
            tool_versions=(VERSION,),
            sensitivity=SensitivityClass.READ_ONLY,
            max_input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
            requires_approval=False,
            issued_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(days=365),
        )
    ]


class TestTheNewRecordShapesAreWritable:
    @pytest.mark.asyncio
    async def test_an_authorized_invalid_payload_writes_to_postgres(
        self,
        boundary: ToolBoundary,
        store: PostgresAuditStore,
        db_session: AsyncSession,
    ) -> None:
        """``effect='allow'`` paired with ``status='failed'``.

        This is the row the reordering created: a rejection that now carries a
        decision. Against ``ck_agent_tool_invocation_capability_denial`` it is
        ``TRUE OR FALSE``, and against
        ``ck_agent_tool_invocation_decision_pair_or_invalid_input`` it satisfies
        the *first* arm — both columns non-NULL — where the old shape satisfied
        the second.
        """

        outcome = await boundary.invoke(
            **call_kwargs(arguments={"wrong_field": 1}, grants=granted())
        )

        assert outcome.status is ToolOutcomeStatus.INVALID_INPUT

        row = await ToolInvocationRepository(db_session).get_tool_invocation(
            outcome.invocation.tool_invocation_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == "failed"
        assert row.error_code == "invalid_input"
        assert row.capability_decision_effect == "allow"
        assert row.capability_grant_id == GRANT_ID
        assert row.request_fingerprint is not None
        assert len(row.request_fingerprint) == 64

    @pytest.mark.asyncio
    async def test_an_unauthorized_invalid_payload_writes_to_postgres(
        self,
        boundary: ToolBoundary,
        db_session: AsyncSession,
    ) -> None:
        """The row the lead asked about specifically, and it passes.

        With no grant and an unparseable payload the outcome is now ``DENIED``,
        so the row is ``effect='deny', status='denied'`` — ``FALSE OR TRUE``
        against ``ck_agent_tool_invocation_capability_denial``. Had denial
        precedence not been part of the fix, this would have been
        ``effect='deny', status='failed'``: ``FALSE OR FALSE``, and Postgres
        would have rejected the row outright.
        """

        outcome = await boundary.invoke(**call_kwargs(arguments={"wrong_field": 1}))

        assert outcome.status is ToolOutcomeStatus.DENIED

        row = await ToolInvocationRepository(db_session).get_tool_invocation(
            outcome.invocation.tool_invocation_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == "denied"
        assert row.error_code == "capability_denied"
        assert row.capability_decision_effect == "deny"
        assert row.capability_denial_reason == "no_matching_grant"
        assert row.request_fingerprint is not None

    @pytest.mark.asyncio
    async def test_an_unauthorized_unrepresentable_payload_writes_to_postgres(
        self, boundary: ToolBoundary, db_session: AsyncSession
    ) -> None:
        """The second pre-decision path, which fails during serialization.

        It carries the ``{"unprocessable_input": True}`` placeholder into the
        JSON column and a digest of that placeholder into ``input_sha256``,
        which has its own ``length(input_sha256) = 64`` CHECK.
        """

        deep: Any = "leaf"
        for _ in range(5000):
            deep = {"n": deep}

        outcome = await boundary.invoke(**call_kwargs(arguments={"query": deep}))

        assert outcome.status is ToolOutcomeStatus.DENIED

        row = await ToolInvocationRepository(db_session).get_tool_invocation(
            outcome.invocation.tool_invocation_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.input == {"unprocessable_input": True}
        assert len(row.input_sha256) == 64

    @pytest.mark.asyncio
    async def test_a_successful_call_still_writes(
        self, boundary: ToolBoundary, db_session: AsyncSession
    ) -> None:
        """The control. Without it, a fix that made every row unwritable would
        pass the three tests above by never reaching the insert."""

        outcome = await boundary.invoke(**call_kwargs(grants=granted()))

        assert outcome.succeeded

        row = await ToolInvocationRepository(db_session).get_tool_invocation(
            outcome.invocation.tool_invocation_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == "succeeded"
        assert row.capability_decision_effect == "allow"

    @pytest.mark.asyncio
    async def test_no_row_the_boundary_writes_omits_its_decision(
        self, boundary: ToolBoundary, store: PostgresAuditStore
    ) -> None:
        """Why the second arm of the decision-pair CHECK is now dead code.

        ``create_tool_invocation`` writes NULL into
        ``capability_decision_effect`` and ``request_fingerprint`` only when it
        is handed ``capability_decision=None``. The boundary no longer hands it
        one on any path, so the ``(effect IS NULL AND fingerprint IS NULL AND
        error_code = 'invalid_input')`` arm cannot be produced by a mediated
        call. It stays reachable by calling the repository directly, which is
        what ``test_an_invalid_input_row_persists_with_no_capability_decision``
        does — so the arm is not removable without also deciding that case is
        illegal, and the three-valued-logic comment above it must survive either
        way.
        """

        await boundary.invoke(**call_kwargs(grants=granted()))
        await boundary.invoke(**call_kwargs(arguments={"wrong": 1}, grants=granted()))
        # A distinct attempt, because the derived idempotency key does not
        # depend on `grants` -- see `test_a_repeated_refusal_collides_on_the_
        # idempotency_key`, which records why reusing `attempt-1` here would
        # fail for a reason that has nothing to do with this test's subject.
        await boundary.invoke(
            **call_kwargs(arguments={"wrong": 1}, attempt_id=ATTEMPT_ID_2)
        )

        assert store.decisions
        assert all(decision is not None for decision in store.decisions)


class TestTheConstraintCanActuallyReject:
    """Absence of an error is only evidence if the check could have fired."""

    @pytest.mark.asyncio
    async def test_deny_paired_with_a_non_denied_status_is_rejected(
        self, db_session: AsyncSession
    ) -> None:
        """The positive control for
        ``ck_agent_tool_invocation_capability_denial``.

        This is the shape the fix would have produced had denial precedence
        been left out — ``effect='deny'`` on a ``status='failed'`` row. Inserted
        directly, bypassing the repository, the way the existing DDL tests reach
        their backstops. Postgres rejects it, which is what makes the passing
        tests above mean something.
        """

        with pytest.raises(IntegrityError) as caught:
            await db_session.execute(
                text(
                    "INSERT INTO agent_tool_invocations ("
                    "id, tool_invocation_id, run_id, task_id, attempt_id, "
                    "organization_id, tool_name, tool_version, status, "
                    "capability_scope, idempotency_key, input, input_trust, "
                    "input_sha256, capability_decision_effect, "
                    "capability_denial_reason, request_fingerprint, "
                    "producer_kind, error_code, requested_at) VALUES ("
                    "gen_random_uuid(), 'bypass-1', :run, :task, :attempt, :org, :tool, :version, "
                    "'failed', :scope, 'bypass-key-1', '{}', 'user_supplied', "
                    ":digest, 'deny', 'no_matching_grant', :digest, 'system', "
                    "'invalid_input', :now)"
                ),
                {
                    "run": RUN_ID,
                    "task": TASK_ID,
                    "attempt": ATTEMPT_ID,
                    "org": ORG_ID,
                    "tool": TOOL,
                    "version": VERSION,
                    "scope": SCOPE,
                    "digest": "a" * 64,
                    "now": NOW,
                },
            )
            await db_session.flush()

        assert "capability_denial" in str(caught.value)

    @pytest.mark.asyncio
    async def test_the_old_shape_was_admitted_only_by_null_propagation(
        self, db_session: AsyncSession
    ) -> None:
        """The pre-fix row, pinned so the improvement is visible.

        ``effect=NULL, status='failed'`` evaluates the denial CHECK to
        ``NULL OR FALSE`` = NULL, and a CHECK rejects only on FALSE. The row was
        admitted, but not because the constraint approved of it — because the
        constraint could not reach a verdict. That is the same three-valued-logic
        gap the comment above
        ``ck_agent_tool_invocation_decision_pair_or_invalid_input`` warns about,
        and it is why the second arm of that constraint had to exist. The
        boundary no longer produces this shape.
        """

        await db_session.execute(
            text(
                "INSERT INTO agent_tool_invocations ("
                "id, tool_invocation_id, run_id, task_id, attempt_id, "
                "organization_id, tool_name, tool_version, status, "
                "capability_scope, idempotency_key, input, input_trust, "
                "input_sha256, producer_kind, error_code, requested_at) VALUES ("
                "gen_random_uuid(), 'legacy-1', :run, :task, :attempt, :org, :tool, :version, "
                "'failed', :scope, 'legacy-key-1', '{}', 'user_supplied', "
                ":digest, 'system', 'invalid_input', :now)"
            ),
            {
                "run": RUN_ID,
                "task": TASK_ID,
                "attempt": ATTEMPT_ID,
                "org": ORG_ID,
                "tool": TOOL,
                "version": VERSION,
                "scope": SCOPE,
                "digest": "a" * 64,
                "now": NOW,
            },
        )
        await db_session.flush()

        stored = await db_session.execute(
            text(
                "SELECT capability_decision_effect, status FROM "
                "agent_tool_invocations WHERE tool_invocation_id = 'legacy-1'"
            )
        )
        assert stored.one() == (None, "failed")


class TestADefectThisFixDidNotIntroduceAndDoesNotFix:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "PROVEN DEFECT, PRE-EXISTING at 477d77a, reproduced against real "
            "Postgres. A refused call terminates BEFORE the dedup lookup -- "
            "deliberately, since denial precedence is the disclosure control -- "
            "so it persists unconditionally. The derived idempotency key does "
            "not depend on `grants`, and DENIED is excluded from "
            "_REPLAYABLE_RECORD_STATUSES, so a second identical refusal in the "
            "same attempt inserts a second row under the same "
            "(attempt_id, idempotency_key) and violates "
            "uq_agent_tool_invocation_idempotency. Calling ANY tool twice with "
            "no grant is enough. Verified pre-existing by checking out "
            "477d77a's boundary.py (positive control: _reject_input present, "
            "_RejectedInput absent) -- pre-fix the colliding pair is "
            "invalid_input/invalid_input, post-fix it is denied/denied. Only "
            "the status changed; the collision did not. Fixing it means "
            "deciding whether a repeated refusal is one durable event or many, "
            "which is 4B's DDL question, not this packet's ordering one."
        ),
    )
    @pytest.mark.asyncio
    async def test_a_repeated_refusal_collides_on_the_idempotency_key(
        self, boundary: ToolBoundary
    ) -> None:
        """Two identical ungranted calls. The second cannot be written.

        Recorded rather than fixed, and recorded as a *test* rather than a note
        so that whoever resolves it finds a red marker instead of prose.
        """

        first = await boundary.invoke(**call_kwargs())
        assert first.status is ToolOutcomeStatus.DENIED

        second = await boundary.invoke(**call_kwargs())
        assert second.status is ToolOutcomeStatus.DENIED

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "PROVEN DEFECT, PRE-EXISTING at 477d77a, reproduced against real "
            "Postgres. A DENIED row occupies its idempotency key but is excluded "
            "from _REPLAYABLE_RECORD_STATUSES, so a later AUTHORIZED call under "
            "that key is neither served from the record nor able to write its "
            "own: the replay branch is skipped, the call executes, and the "
            "REQUESTED insert violates uq_agent_tool_invocation_idempotency "
            "(attempt_id, idempotency_key). The IntegrityError surfaces at the "
            "legitimate caller, who also gets no record of their own call. "
            "Verified pre-existing by checking out 477d77a's boundary.py with a "
            "positive control on the checkout (_reject_input present, "
            "_RejectedInput absent): identical IntegrityError before and after. "
            "The exclusion of DENIED is correct and must stay -- one caller's "
            "refusal must never become another's answer. What is missing is a "
            "rule for a key whose only occupant is a non-answer. That is 4B's "
            "DDL question plus the store adapter, not this packet's ordering "
            "one, and fixing it here would mean either serving a denial as a "
            "result or letting an unaudited second row exist."
        ),
    )
    @pytest.mark.asyncio
    async def test_a_denial_bricks_the_key_for_the_authorized_caller(
        self, boundary: ToolBoundary
    ) -> None:
        """The sequence the in-memory store cannot fail on.

        ``RecordingAuditStore.persist`` assigns into a dict, so the second write
        silently overwrites the first and
        ``test_an_authorized_caller_is_not_served_an_earlier_denial`` passes --
        a control that cannot fail, because the constraint that would fail it is
        not in the loop. That unit test still passes; this one raises.
        """

        denied = await boundary.invoke(**call_kwargs(idempotency_key=SHARED_KEY))
        assert denied.status is ToolOutcomeStatus.DENIED

        allowed = await boundary.invoke(
            **call_kwargs(idempotency_key=SHARED_KEY, grants=granted())
        )

        assert allowed.succeeded
