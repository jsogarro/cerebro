"""Class 6 — secret leakage.

Credentials reaching prompts, events, artifacts, or logs. Where possible,
scenarios assert on an exact known sentinel secret value rather than a
shape heuristic (an "AKIA..."-looking string is not proof anything real
leaked; the literal configured secret value appearing in a persisted record
is). Pattern-based redaction (src/core/pii_redactor.py's PIIRedactor) is
itself a deterministic mechanism — not model-mediated — so these scenarios
are DETERMINISTIC even though they test a redaction *pattern*, unlike the
model-mediated content_sanitizer used for injection wording.

Scenario 6.5 is a deliberate negative/benign case: it documents a real
design tension (over-broad redaction can corrupt legitimate content that
happens to look like a credential) rather than pretending the tension does
not exist.
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

_CLASS = AttackClass.SECRET_LEAKAGE
_SENTINEL_SECRET = "sk-live-CEREBRO-TEST-SENTINEL-9f8e7d6c5b4a3210"


def _pasted_bearer_token_in_query() -> dict[str, object]:
    return {
        "user_query": (
            "Here's the failing request, can you debug it? curl -H "
            f"'Authorization: Bearer {_SENTINEL_SECRET}' "
            "https://api.example.com/v1/search"
        ),
    }


def _secret_in_error_message() -> dict[str, object]:
    return {
        "tool_name": "academic_search",
        "raw_exception": (
            "ConnectionError: request to "
            f"https://provider.example/search?api_key={_SENTINEL_SECRET} "
            "failed: connection reset"
        ),
    }


def _lookalike_but_not_real_secret() -> str:
    # A plausible AWS-access-key-*shaped* string that is not a live
    # credential — the subject of the document is a security advisory
    # about a leaked key format, not an actual leak of this system's own
    # secrets. Included as a benign counter-case, not an attack.
    return (
        "The advisory describes keys matching the pattern "
        "AKIAIOSFODNN7EXAMPLE, which AWS explicitly reserves as a "
        "documentation placeholder and never issues as a live credential."
    )


def _secret_propagated_through_handoff() -> dict[str, object]:
    return {
        "upstream_tool_invocation_input": {
            "curl_command": f"Authorization: Bearer {_SENTINEL_SECRET}"
        },
        "downstream_agent_prompt_template": (
            "Continue the investigation using this prior context: "
            "{upstream_tool_invocation_input}"
        ),
    }


def _telemetry_trace_readable_as_evidence() -> dict[str, object]:
    return {
        "artifact_kind": "provider_call_trace",
        "trust": "application",
        "raw_outbound_headers": {"Authorization": f"Bearer {_SENTINEL_SECRET}"},
        "exposed_via_evidence_retrieval_endpoint": True,
    }


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        scenario_id="secret-01-pasted-bearer-token-in-query",
        attack_class=_CLASS,
        title="User-pasted bearer token in a research query reaches storage",
        entry_channel=EntryChannel.USER_QUERY,
        entry_path="user query -> ToolInvocation.input -> audit/event persistence",
        payload=_pasted_bearer_token_in_query,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.SECRET_ABSENT_FROM_PERSISTED_RECORD,
            statement=(
                f"The literal sentinel value {_SENTINEL_SECRET!r} does not "
                "appear anywhere in the persisted ToolInvocation.input or "
                "any RunEvent payload written for this request; it is "
                "replaced by a redaction placeholder before the write."
            ),
            on_missing_enforcement=(
                "Absent redaction, the write must still fail closed rather "
                "than persist the literal secret value — either redact or "
                "refuse to persist, never pass it through unmodified."
            ),
        ),
        invariant=(
            "Credential-shaped substrings never reach durable, "
            "tenant-visible storage in cleartext, regardless of which "
            "field or code path they arrived through."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason=(
            "src/core/pii_redactor.py exists but has no bearer-token/API-key "
            "pattern and no caller on the tool-invocation or event write "
            "path (that path does not exist yet)."
        ),
    ),
    Scenario(
        scenario_id="secret-02-secret-in-error-message",
        attack_class=_CLASS,
        title="Raw exception text with an embedded query-string API key",
        entry_channel=EntryChannel.TOOL_OUTPUT,
        entry_path="tool exception -> ToolInvocation.status_reason persistence",
        payload=_secret_in_error_message,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.SECRET_ABSENT_FROM_PERSISTED_RECORD,
            statement=(
                f"The literal sentinel value {_SENTINEL_SECRET!r} does not "
                "appear in the persisted status_reason or error_code "
                "fields; the redaction stage runs on error/status text the "
                "same way it runs on input, not as a separate, "
                "easier-to-miss path."
            ),
            on_missing_enforcement=(
                "Absent redaction on this path specifically, the field "
                "must still fail to persist the raw exception text "
                "verbatim rather than storing it unmodified."
            ),
        ),
        invariant=(
            "Error/status text is not exempt from the redaction boundary "
            "just because it originates from an exception rather than "
            "structured input — one redaction boundary applies to every "
            "field that reaches durable storage."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason=(
            "No redaction stage exists on any persistence path yet; "
            "status_reason has no write path at all before packet 4B."
        ),
    ),
    Scenario(
        scenario_id="secret-03-lookalike-not-a-real-secret-benign",
        attack_class=_CLASS,
        title="Documentation-placeholder key format is not this system's secret",
        entry_channel=EntryChannel.RETRIEVED_DOCUMENT,
        entry_path="retrieved advisory text -> agent summary -> persistence",
        payload=_lookalike_but_not_real_secret,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.SECRET_ABSENT_FROM_PERSISTED_RECORD,
            statement=(
                "Redaction targets values that match the system's own "
                "configured credential material (or values that were "
                "actually presented as live secrets in a request), not "
                "every string that is shaped like a key. AWS's own "
                "reserved documentation placeholder "
                "('AKIAIOSFODNN7EXAMPLE') is preserved verbatim in the "
                "persisted summary, since redacting it would corrupt "
                "legitimate content without preventing any real leak."
            ),
            on_missing_enforcement=(
                "This scenario is a deliberate negative case, not an "
                "attack: there is no 'missing enforcement' failure mode "
                "here, only an over-redaction failure mode, which is a "
                "correctness bug rather than a security gap. Included so "
                "the corpus does not implicitly endorse redacting every "
                "credential-shaped substring regardless of provenance."
            ),
        ),
        invariant=(
            "Redaction scope is a design decision with a real trade-off "
            "(false negatives on genuinely leaked third-party secrets vs. "
            "false positives corrupting legitimate content) and should be "
            "made explicitly by whoever implements packet 4B/4E's "
            "redaction contract, not discovered by accident."
        ),
        strength=EnforcementStrength.ADVISORY,
        notes=(
            "Marked ADVISORY because there is no deterministic way to "
            "distinguish 'shape matches a credential pattern' from 'is "
            "this system's own live secret' without a scope decision that "
            "has not been made yet (packet 4A's redaction contract). This "
            "scenario's purpose is to force that decision to be explicit, "
            "not to assert a specific mechanism must exist."
        ),
    ),
    Scenario(
        scenario_id="secret-04-propagated-through-inter-agent-handoff",
        attack_class=_CLASS,
        title="Unredacted upstream input interpolated into a downstream prompt",
        entry_channel=EntryChannel.INTER_AGENT_HANDOFF,
        entry_path=(
            "upstream ToolInvocation.input -> downstream agent prompt "
            "template interpolation -> persisted Artifact"
        ),
        payload=_secret_propagated_through_handoff,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.SECRET_ABSENT_FROM_PERSISTED_RECORD,
            statement=(
                f"The literal sentinel value {_SENTINEL_SECRET!r} does not "
                "appear in any persisted Artifact whose content derives "
                "from prompt interpolation, because prompt construction "
                "reads through the same redaction boundary as direct "
                "event/audit writes rather than a second, divergent path."
            ),
            on_missing_enforcement=(
                "Absent a shared redaction boundary, the interpolated "
                "prompt content must still fail to persist the secret "
                "verbatim, even if the upstream write path happened to "
                "redact it and this one does not."
            ),
        ),
        invariant=(
            "There is exactly one redaction boundary in the system, "
            "applied uniformly to every path that can persist content — "
            "not one path that redacts and a second, informally-trusted "
            "path (prompt construction) that does not."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason=(
            "No prompt-pinning/persistence path exists yet (D4b, in scope "
            "for 4A/4B); there is nothing to check for a shared redaction "
            "boundary against."
        ),
    ),
    Scenario(
        scenario_id="secret-05-telemetry-trace-exposed-via-evidence-path",
        attack_class=_CLASS,
        title="Debug trace with raw headers is readable through the evidence endpoint",
        entry_channel=EntryChannel.TOOL_OUTPUT,
        entry_path="provider-call trace Artifact -> evidence retrieval endpoint",
        payload=_telemetry_trace_readable_as_evidence,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.SECRET_ABSENT_FROM_PERSISTED_RECORD,
            statement=(
                "A telemetry/trace Artifact classified for internal "
                "debugging is not reachable through the same "
                "evidence-retrieval path exposed for task output, "
                "independent of whether the header value it contains was "
                "separately redacted; access scoping is a second, "
                "independent control, not a substitute for redaction."
            ),
            on_missing_enforcement=(
                "Absent access scoping by artifact kind, the trace content "
                "must still be redacted at write time so that even a "
                "reachable trace does not carry the literal secret."
            ),
        ),
        invariant=(
            "Secret protection is defense in depth across two independent "
            "controls — redaction at write time and access scoping at "
            "read time — because either one failing alone should not be "
            "sufficient for a leak."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason=(
            "No Artifact table, artifact-kind access scoping, or evidence "
            "retrieval endpoint exists yet (packets 4B/4E)."
        ),
    ),
)

GROUP = ScenarioGroup(attack_class=_CLASS, scenarios=SCENARIOS)
