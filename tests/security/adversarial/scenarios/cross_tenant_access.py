"""Class 5 — cross-tenant access.

Reading or writing another organization's data. Wave 3 built the "one
identity, two representations" tenant enforcement
(src/repositories/tenant_scope.py: enforce_tenant_identity,
normalize_organization_id, MissingOrganizationContextError,
TenantMismatchError) for the run/task/attempt/event tables. This class
probes whether that same discipline extends to Wave 4's tool/evidence
tables, and separately documents the already-known unscoped-read bug
(`verify_project_access`) that packet 4-Sec fixes ahead of the wave PR.
"""

from __future__ import annotations

from ..types import (
    AttackClass,
    EnforcementStrength,
    EntryChannel,
    ExpectedOutcome,
    GuaranteeKind,
    Scenario,
    ScenarioGroup,
)

_CLASS = AttackClass.CROSS_TENANT_ACCESS


def _client_supplied_organization_override() -> dict[str, object]:
    return {
        "authenticated_organization_id": "11111111-1111-1111-1111-111111111111",
        "request_body_organization_id": "22222222-2222-2222-2222-222222222222",
        "action": "write_tool_invocation",
    }


def _cross_tenant_evidence_artifact_link() -> dict[str, object]:
    return {
        "evidence_run_id": "run-tenant-A-001",
        "evidence_organization_id": "11111111-1111-1111-1111-111111111111",
        "referenced_snapshot_artifact_id": "artifact-tenant-B-999",
        "artifact_organization_id": "22222222-2222-2222-2222-222222222222",
    }


def _unscoped_read_by_id() -> dict[str, object]:
    return {
        "authenticated_organization_id": "11111111-1111-1111-1111-111111111111",
        "requested_project_id": "project-owned-by-tenant-B",
        "endpoint": "verify_project_access / resolve()",
    }


def _cross_tenant_ws_subscription() -> dict[str, object]:
    return {
        "authenticated_organization_id": "11111111-1111-1111-1111-111111111111",
        "subscribe_run_id": "run-owned-by-tenant-B",
        "channel": "ws_sse_resume_cursor",
    }


def _idempotency_key_collision_across_tenants() -> dict[str, object]:
    return {
        "tenant_a_organization_id": "11111111-1111-1111-1111-111111111111",
        "tenant_b_organization_id": "22222222-2222-2222-2222-222222222222",
        "shared_idempotency_key": "template-generated-key-shared-by-both",
        "tenant_a_input": {"template": "standard-search", "version": 1},
        "tenant_b_input": {"template": "standard-search", "version": 1},
    }


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        scenario_id="crosstenant-01-client-supplied-organization-override",
        attack_class=_CLASS,
        title="Request body organization_id overrides authenticated tenant",
        entry_channel=EntryChannel.HTTP_REQUEST,
        entry_path=(
            "request body -> tool/evidence write path -> "
            "src/repositories/tenant_scope.py enforce_tenant_identity"
        ),
        payload=_client_supplied_organization_override,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.TENANT_ISOLATION_ENFORCED,
            statement=(
                "organization_id is derived exclusively from the "
                "authenticated context and normalized via "
                "normalize_organization_id; enforce_tenant_identity "
                "rejects any write whose contract tenant_id disagrees with "
                "it. A client-supplied organization_id in the request body "
                "is never consulted for authorization, so this write is "
                "scoped to tenant A regardless of the body's claim."
            ),
            on_missing_enforcement=(
                "If a tool/evidence write path does not call "
                "enforce_tenant_identity, the write must still fail "
                "closed (MissingOrganizationContextError or "
                "TenantMismatchError) rather than trusting body content."
            ),
        ),
        invariant=(
            "Tenant identity for a write is a property of the "
            "authenticated session, never of request body content — the "
            "same rule Wave 3 proved for run/task/event tables extended to "
            "every future write path."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason=(
            "No tool/evidence tables or write paths exist yet (packet 4B); "
            "tenant_scope.py's enforcement functions exist but have no "
            "Wave 4 caller."
        ),
    ),
    Scenario(
        scenario_id="crosstenant-02-cross-tenant-evidence-artifact-link",
        attack_class=_CLASS,
        title="Evidence row for tenant A references tenant B's artifact",
        entry_channel=EntryChannel.TOOL_OUTPUT,
        entry_path="Evidence write path -> Artifact tenant agreement check",
        payload=_cross_tenant_evidence_artifact_link,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.TENANT_ISOLATION_ENFORCED,
            statement=(
                "The Evidence write path resolves the referenced "
                "snapshot_artifact_id's owning organization and rejects "
                "the write when it does not match the Evidence row's own "
                "organization_id, mirroring enforce_tenant_identity's "
                "agreement check for run/task rows."
            ),
            on_missing_enforcement=(
                "Absent this check, the write must still fail rather than "
                "create a cross-tenant evidence chain that lets tenant A's "
                "claim inherit legitimacy from tenant B's artifact."
            ),
        ),
        invariant=(
            "Every provenance edge (Evidence -> Artifact, ClaimSupport -> "
            "Evidence) stays within one tenant boundary; cross-tenant "
            "linkage is a structural write-time rejection, not a read-time "
            "filter applied after the fact."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason="No Evidence/Artifact tables or write paths exist yet.",
    ),
    Scenario(
        scenario_id="crosstenant-03-unscoped-read-by-id",
        attack_class=_CLASS,
        title="Authenticated tenant A reads tenant B's project by direct ID",
        entry_channel=EntryChannel.HTTP_REQUEST,
        entry_path="src/api/websocket/auth.py verify_project_access / resolve()",
        payload=_unscoped_read_by_id,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.TENANT_ISOLATION_ENFORCED,
            statement=(
                "A read for a resource by ID is additionally filtered by "
                "the authenticated organization_id; a request from tenant "
                "A for a resource owned by tenant B returns not-found "
                "rather than the resource, regardless of whether the ID "
                "itself is a valid, existing identifier."
            ),
            on_missing_enforcement=(
                "Absent org-scoped filtering, the read must still be "
                "denied by default rather than falling through to "
                "'authenticated therefore authorized.'"
            ),
        ),
        invariant=(
            "Authentication proves who is asking; it does not by itself "
            "authorize access to a specific resource — that requires an "
            "explicit ownership/scope check on every read, not only on "
            "writes."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        notes=(
            "This is not a hypothetical: the plan records "
            "verify_project_access() as verbatim 'return user_id is not "
            "None' plus an unscoped resolve(), with live-confirmed "
            "unauthenticated access on related endpoints. Packet 4-Sec "
            "fixes this ahead of and independent of the Wave 4 PR; this "
            "scenario exists so the corpus documents the exact gap being "
            "closed and a harness can re-run it post-4-Sec-merge as a "
            "regression check, not only as a pre-fix finding."
        ),
        expected_to_fail_today=True,
        failure_reason=(
            "verify_project_access() at src/api/websocket/auth.py:121-124 "
            "is documented (2026-08-06 plan preflight) as authorizing any "
            "authenticated user for any project; resolve() and execution "
            "status/results endpoints are similarly unscoped."
        ),
    ),
    Scenario(
        scenario_id="crosstenant-04-ws-subscription-cross-tenant-run",
        attack_class=_CLASS,
        title="WebSocket client subscribes to another tenant's run stream",
        entry_channel=EntryChannel.WS_SSE_STREAM,
        entry_path="WS/SSE subscription -> run authority resolver",
        payload=_cross_tenant_ws_subscription,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.TENANT_ISOLATION_ENFORCED,
            statement=(
                "The stream subscription path resolves the target run's "
                "owning organization before attaching the client and "
                "rejects the subscription when it does not match the "
                "authenticated organization_id, independent of whether "
                "run_id is a well-formed, existing identifier."
            ),
            on_missing_enforcement=(
                "Absent this resolution, the subscription must still be "
                "refused rather than silently streaming another tenant's "
                "run events."
            ),
        ),
        invariant=(
            "Streaming/subscription endpoints enforce the same tenant "
            "boundary as request/response endpoints; a long-lived "
            "connection is not exempt from the check merely because it "
            "was established once and then left open."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason=(
            "Related to the same authority-resolution gap named in "
            "crosstenant-03; the WS/SSE resume-cursor streams introduced "
            "in the durable-lifecycle wave were not scoped in this audit."
        ),
    ),
    Scenario(
        scenario_id="crosstenant-05-idempotency-key-collision",
        attack_class=_CLASS,
        title="Two tenants' requests hash to the same idempotency_key",
        entry_channel=EntryChannel.TOOL_INPUT,
        entry_path="ToolInvocation request -> idempotency dedup lookup",
        payload=_idempotency_key_collision_across_tenants,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.IDEMPOTENT_NO_DUPLICATE_EFFECT,
            statement=(
                "Idempotency dedup is scoped by (organization_id, "
                "idempotency_key), never by idempotency_key alone. Tenant "
                "B's request with the same key as tenant A's is evaluated "
                "and executed independently, never short-circuited to "
                "'already done' using tenant A's cached result."
            ),
            on_missing_enforcement=(
                "Absent organization-scoped dedup, the request must still "
                "be executed independently rather than silently served "
                "another tenant's cached outcome."
            ),
        ),
        invariant=(
            "An idempotency key is only meaningful within one tenant's "
            "namespace; two tenants generating the same key from a shared "
            "public template is expected and must never cause cross-tenant "
            "result reuse."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason="No idempotency dedup mechanism exists yet.",
    ),
)

GROUP = ScenarioGroup(attack_class=_CLASS, scenarios=SCENARIOS)
