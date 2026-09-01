"""An MCP tool call cannot carry a field its specification never declared.

The shipped MCP input models inherited ``extra="allow"``. The boundary
serializes a validated model by iterating ``type(value).model_fields`` —
declared fields only — so a field admitted by ``extra="allow"`` lives in
``model_extra`` and is absent from ``redacted_input``, from ``input_sha256``,
from ``CapabilityRequest.fingerprint()``, and from the derived idempotency key.
``analyze_statistics`` then read ``params.model_extra`` and forwarded every
extra as a keyword argument to the outbound client.

That is worse than the non-guarantee this wave wrote down for ``extra="ignore"``
("smuggled fields are silently discarded and neither the audit record nor the
approval fingerprint covers what the caller actually submitted"). Under
``allow`` the fields are not discarded — they reach the network — while staying
invisible to the record and the fingerprint. The audit record denies material
that the call carried.

Each test here asserts its own precondition: the benign call is served and
reaches the client, so a refusal of the smuggled call cannot be explained by a
payload that was malformed for some unrelated reason.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ValidationError

from src.agents.integrations.mcp_integration import MCPIntegration
from src.agents.integrations.mcp_tool_specs import (
    TOOL_ANALYZE_STATISTICS,
    TOOL_VERSION,
    AcademicSearchInput,
    AnalyzeStatisticsInput,
    BuildKnowledgeGraphInput,
    FormatCitationsInput,
)
from src.agents.tools.mediation import InMemoryToolAuditStore, ToolCallIdentity
from src.core.contracts import CapabilityGrant, SensitivityClass, TrustClassification
from src.core.contracts.provenance import ToolInvocationStatus
from src.core.contracts.redaction import REDACTION_MARKER, boundary_digest

SMUGGLED_KEY = "sk-live-SMUGGLED-0123456789abcdef"
ATTACKER_ENDPOINT = "https://attacker.example/collect"
BENIGN: dict[str, Any] = {"operation": "mean", "data": [1, 2, 3]}


def _client() -> AsyncMock:
    client = AsyncMock()
    client.health_check.return_value = {"client": "healthy"}
    client.analyze_statistics.return_value = {
        "success": True,
        "result": {"mean": 2.0},
    }
    return client


def _identity(attempt: str) -> ToolCallIdentity:
    return ToolCallIdentity(
        run_id="run-1",
        task_id="task-1",
        attempt_id=f"attempt-{attempt}",
        organization_id="org-1",
    )


def _grant() -> CapabilityGrant:
    now = datetime.now(UTC)
    return CapabilityGrant(
        grant_id="grant-mcp-analyze-statistics",
        run_id="run-1",
        task_id="task-1",
        capability_scope="scope-mcp-analyze-statistics",
        tool_name=TOOL_ANALYZE_STATISTICS,
        tool_versions=(TOOL_VERSION,),
        sensitivity=SensitivityClass.READ_ONLY,
        max_input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
        requires_approval=False,
        issued_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=5),
    )


def _integration(
    *, client: AsyncMock, store: InMemoryToolAuditStore | None = None
) -> MCPIntegration:
    return MCPIntegration(mcp_client=client, audit_store=store, grants=(_grant(),))


def _dumped(store: InMemoryToolAuditStore) -> str:
    return str([record.model_dump(mode="json") for record in store.invocations])


class TestTheRecordDescribesTheCallThatWasMade:
    async def test_two_materially_different_calls_do_not_share_an_input_digest(
        self,
    ) -> None:
        store = InMemoryToolAuditStore()
        client = _client()
        integration = _integration(client=client, store=store)

        benign = await integration.analyze_statistics(
            **BENIGN, identity=_identity("benign")
        )
        # Precondition: the benign call is well-formed and was actually served.
        assert benign["success"] is True

        await integration.analyze_statistics(
            **BENIGN,
            api_key=SMUGGLED_KEY,
            endpoint_override=ATTACKER_ENDPOINT,
            identity=_identity("smuggled"),
        )

        recorded_benign = store.invocations[-2]
        recorded_smuggled = store.invocations[-1]
        assert boundary_digest(recorded_benign.input) != boundary_digest(
            recorded_smuggled.input
        )

    async def test_an_undeclared_field_is_visible_in_the_record(self) -> None:
        """Not merely dropped. An operator must be able to see it was sent."""

        store = InMemoryToolAuditStore()
        integration = _integration(client=_client(), store=store)

        await integration.analyze_statistics(
            **BENIGN,
            endpoint_override=ATTACKER_ENDPOINT,
            identity=_identity("smuggled"),
        )

        assert "endpoint_override" in store.invocations[-1].input

    async def test_the_refused_call_is_recorded_as_a_bad_input(self) -> None:
        store = InMemoryToolAuditStore()
        integration = _integration(client=_client(), store=store)

        await integration.analyze_statistics(
            **BENIGN,
            endpoint_override=ATTACKER_ENDPOINT,
            identity=_identity("smuggled"),
        )

        recorded = store.invocations[-1]
        assert recorded.status is ToolInvocationStatus.FAILED
        assert recorded.error_code == "invalid_input"


class TestAnUndeclaredCredentialCannotReachTheOutboundCall:
    async def test_the_client_never_sees_a_field_the_spec_did_not_declare(
        self,
    ) -> None:
        store = InMemoryToolAuditStore()
        client = _client()
        integration = _integration(client=client, store=store)

        benign = await integration.analyze_statistics(
            **BENIGN, identity=_identity("benign")
        )
        # Precondition: this exact payload reaches the client when it carries
        # nothing undeclared, so a later absence is the smuggling being
        # refused rather than the call failing for some other reason.
        assert benign["success"] is True
        assert client.analyze_statistics.await_count == 1

        await integration.analyze_statistics(
            **BENIGN,
            api_key=SMUGGLED_KEY,
            endpoint_override=ATTACKER_ENDPOINT,
            identity=_identity("smuggled"),
        )

        assert client.analyze_statistics.await_count == 1

    async def test_the_credential_is_recorded_as_redacted_rather_than_dropped(
        self,
    ) -> None:
        """Absence of the value alone is not the guarantee.

        At HEAD the value was absent from the record because the whole field
        was invisible — the same assertion an intact barrier produces, from the
        opposite cause. So the key must be *present* and redacted: the record
        says a credential-named field arrived and does not carry its value.
        """

        store = InMemoryToolAuditStore()
        integration = _integration(client=_client(), store=store)

        await integration.analyze_statistics(
            **BENIGN, api_key=SMUGGLED_KEY, identity=_identity("smuggled")
        )

        recorded = store.invocations[-1].input
        assert SMUGGLED_KEY not in _dumped(store)
        assert recorded["api_key"] == REDACTION_MARKER


class TestReplayCannotServeADifferentRequest:
    async def test_a_smuggled_call_is_not_served_the_benign_calls_result(
        self,
    ) -> None:
        """Same identity: at HEAD both derive the same idempotency key.

        The digest that keys replay is computed over declared fields only, so a
        call carrying undeclared material was indistinguishable from one that
        did not — and was answered with the earlier call's recorded output
        without its handler running.
        """

        store = InMemoryToolAuditStore()
        client = _client()
        integration = _integration(client=client, store=store)
        identity = _identity("shared")

        benign = await integration.analyze_statistics(**BENIGN, identity=identity)
        assert benign["success"] is True

        smuggled = await integration.analyze_statistics(
            **BENIGN,
            api_key=SMUGGLED_KEY,
            endpoint_override=ATTACKER_ENDPOINT,
            identity=identity,
        )

        assert smuggled["success"] is not True


class TestEveryShippedInputModelIsClosed:
    @pytest.mark.parametrize(
        ("model", "payload"),
        [
            (AcademicSearchInput, {"query": "q"}),
            (FormatCitationsInput, {"sources": [], "style": "APA"}),
            (AnalyzeStatisticsInput, {"operation": "mean", "data": [1, 2]}),
            (BuildKnowledgeGraphInput, {"text": "t"}),
        ],
    )
    def test_an_undeclared_field_is_a_validation_error(
        self, model: type[BaseModel], payload: dict[str, Any]
    ) -> None:
        # Precondition: the payload without the extra is accepted, so the
        # rejection below is attributable to the undeclared field alone.
        assert model.model_validate(payload)

        with pytest.raises(ValidationError):
            model.model_validate({**payload, "api_key": SMUGGLED_KEY})


class TestTheParametersTheUpstreamToolPublishesStillWork:
    """Closing the model must not close off a parameter that was in use.

    ``statistics_analyzer`` publishes a fixed parameter list — ``operation``,
    ``data``, ``group1``, ``group2``, ``x``, ``y``, ``plot_type``. Declaring
    them is what makes ``extra="forbid"`` a tightening rather than a
    regression: they now reach the client as declared fields, and they are in
    the record and the digest.
    """

    async def test_correlation_parameters_reach_the_client(self) -> None:
        client = _client()
        integration = _integration(client=client)

        await integration.analyze_statistics(
            "correlation", x=[1.0, 2.0], y=[3.0, 4.0], identity=_identity("corr")
        )

        kwargs = client.analyze_statistics.await_args.kwargs
        assert kwargs["x"] == [1.0, 2.0]
        assert kwargs["y"] == [3.0, 4.0]

    async def test_the_declared_parameters_are_in_the_record(self) -> None:
        store = InMemoryToolAuditStore()
        integration = _integration(client=_client(), store=store)

        await integration.analyze_statistics(
            "t_test",
            group1=[1.0, 2.0],
            group2=[3.0, 4.0],
            identity=_identity("ttest"),
        )

        # The record freezes sequences, so compare against the frozen form.
        recorded = store.invocations[-1].input
        assert recorded["group1"] == (1.0, 2.0)
        assert recorded["group2"] == (3.0, 4.0)
