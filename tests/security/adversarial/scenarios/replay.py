"""Class 7 — replay.

Re-submitting a captured request or event to cause a duplicate effect.
Covers the tool-invocation idempotency boundary (4C), the durable
event/outbox log inherited from Wave 3, and the WS/SSE resume-cursor
streams introduced in the durable-lifecycle wave.
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

_CLASS = AttackClass.REPLAY


def _bit_identical_resubmission() -> dict[str, object]:
    return {
        "run_id": "run-1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "tool_name": "send_notification",
        "idempotency_key": "req-abc123",
        "input": {"recipient": "user@example.com", "body": "Report ready"},
        "resubmitted_after_status": "succeeded",
    }


def _event_log_replay() -> dict[str, object]:
    return {
        "event_id": "evt-approve-action-9f8e",
        "event_type": "sensitive_action_approved",
        "already_processed": True,
        "consumer": "outbox_relay",
        "downstream_side_effect": "real_money_booking",
    }


def _idempotency_key_with_mutated_input() -> dict[str, object]:
    return {
        "idempotency_key": "req-abc123",
        "original_input": {"query": "transformer robustness survey"},
        "replayed_input": {"query": "DELETE all citations for project X"},
    }


def _cross_run_idempotency_key_reuse() -> dict[str, object]:
    return {
        "idempotency_key": "template-derived-key-static-content",
        "original_run_id": "run-1",
        "replayed_run_id": "run-2",
        "same_tool_name": "academic_search",
        "same_input": {"query": "standard literature search"},
    }


def _stale_ws_cursor_resume() -> dict[str, object]:
    return {
        "run_id": "run-1",
        "run_terminal_state": "completed",
        "captured_resume_cursor": "cursor-before-terminal-event",
        "resume_attempts": 5,
        "downstream_side_effect_on_terminal_event": "send_completion_email",
    }


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        scenario_id="replay-01-bit-identical-resubmission",
        attack_class=_CLASS,
        title="Identical successful invocation resubmitted verbatim",
        entry_channel=EntryChannel.TOOL_INPUT,
        entry_path="ToolInvocation request -> idempotency dedup lookup",
        payload=_bit_identical_resubmission,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.IDEMPOTENT_NO_DUPLICATE_EFFECT,
            statement=(
                "A request with an idempotency_key matching a prior "
                "SUCCEEDED invocation of the same tool_name/capability_"
                "scope/canonical input returns the original recorded "
                "result rather than re-executing the tool a second time; "
                "send_notification does not fire twice."
            ),
            on_missing_enforcement=(
                "Absent dedup, the request must still be evaluated "
                "independently rather than defaulting to re-execution; a "
                "duplicate side effect is the failure mode to prevent."
            ),
        ),
        invariant=(
            "Idempotency keys make side-effecting tool calls safe to "
            "retry; the boundary is the single place this guarantee is "
            "enforced, not a convention callers are expected to honor."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason="No idempotency dedup mechanism exists on any tool path yet.",
    ),
    Scenario(
        scenario_id="replay-02-event-log-replay-duplicate-side-effect",
        attack_class=_CLASS,
        title="Captured event resubmitted against the outbox/event consumer",
        entry_channel=EntryChannel.EVENT_LOG,
        entry_path=(
            "captured RunEvent -> outbox relay / event consumer "
            "(src/repositories/run_event_repository.py)"
        ),
        payload=_event_log_replay,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.IDEMPOTENT_NO_DUPLICATE_EFFECT,
            statement=(
                "Event consumers are idempotent on event_id: resubmitting "
                "an already-processed sensitive_action_approved event does "
                "not trigger a second real_money_booking or other "
                "downstream side effect."
            ),
            on_missing_enforcement=(
                "Absent event_id dedup at the consumer, replay must still "
                "be rejected or no-op rather than re-triggering the "
                "downstream effect."
            ),
        ),
        invariant=(
            "The append-only event log's at-least-once delivery guarantee "
            "requires every consumer to be independently idempotent; "
            "'the event was already appended once' is not itself a "
            "duplicate-prevention mechanism at the consumer boundary."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason=(
            "No sensitive_action_approved event type or downstream "
            "consumer exists yet. Wave 3's outbox (src/models/db/"
            "run_event.py AgentRunEventOutbox, src/api/services/"
            "outbox_relay.py) already carries a per-row idempotency_key "
            "and documents 'consumers must dedupe on idempotency_key' as "
            "the contract new consumers are expected to follow — but that "
            "is a documented convention for a consumer to implement, not a "
            "structural guarantee the relay enforces on their behalf. "
            "Whoever builds the Wave 4 approval-event consumer must "
            "deliberately reuse this pattern; nothing forces it."
        ),
    ),
    Scenario(
        scenario_id="replay-03-idempotency-key-with-mutated-input",
        attack_class=_CLASS,
        title="Same idempotency_key replayed with different input content",
        entry_channel=EntryChannel.TOOL_INPUT,
        entry_path="ToolInvocation request -> idempotency dedup lookup",
        payload=_idempotency_key_with_mutated_input,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.IDEMPOTENT_NO_DUPLICATE_EFFECT,
            statement=(
                "Idempotency dedup keys on the tuple (tool_name, "
                "capability_scope, canonical input hash, idempotency_key). "
                "A key presented with input that hashes differently from "
                "what was originally recorded under that key is a "
                "conflict — rejected outright — never silently served the "
                "cached result or silently executed as new, unaudited work "
                "under an old key."
            ),
            on_missing_enforcement=(
                "Absent conflict detection, the mismatched replay must "
                "still be denied rather than resolved in either direction "
                "by default."
            ),
        ),
        invariant=(
            "An idempotency key identifies one specific request's content, "
            "not a slot that can be silently repointed at different work."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason="No idempotency dedup mechanism exists on any tool path yet.",
    ),
    Scenario(
        scenario_id="replay-04-cross-run-idempotency-key-reuse",
        attack_class=_CLASS,
        title="Idempotency key from run 1 replayed unmodified against run 2",
        entry_channel=EntryChannel.TOOL_INPUT,
        entry_path="ToolInvocation request -> idempotency dedup scope",
        payload=_cross_run_idempotency_key_reuse,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.IDEMPOTENT_NO_DUPLICATE_EFFECT,
            statement=(
                "Idempotency scope includes run_id (at minimum (run_id, "
                "task_id, idempotency_key)); the same key under a "
                "different run_id is evaluated as an independent request "
                "and executed, never treated as a duplicate of run 1's "
                "effect."
            ),
            on_missing_enforcement=(
                "Absent run-scoped dedup, the second run's request must "
                "still execute independently rather than short-circuit to "
                "run 1's cached result."
            ),
        ),
        invariant=(
            "Two runs that happen to derive the same idempotency_key from "
            "identical static template content are still two independent "
            "logical requests; run identity is part of the dedup key, not "
            "an afterthought."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason="No idempotency dedup mechanism exists on any tool path yet.",
    ),
    Scenario(
        scenario_id="replay-05-stale-ws-cursor-resume-duplicate-effect",
        attack_class=_CLASS,
        title="Resuming a stale WS/SSE cursor re-triggers a terminal side effect",
        entry_channel=EntryChannel.WS_SSE_STREAM,
        entry_path="opaque resume-cursor -> stream consumer -> downstream side effect",
        payload=_stale_ws_cursor_resume,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.IDEMPOTENT_NO_DUPLICATE_EFFECT,
            statement=(
                "The terminal event carries a stable event_id; any "
                "consumer wiring a side effect (send_completion_email) off "
                "'stream delivered the terminal event' dedupes on that "
                "event_id, so resuming from a stale cursor five times "
                "delivers the terminal event five times but fires the "
                "side effect at most once."
            ),
            on_missing_enforcement=(
                "Absent consumer-side dedup, repeated resume must still "
                "not be allowed to be the trigger for a repeated side "
                "effect — the safe default is to not attach unguarded "
                "side effects to at-least-once delivery."
            ),
        ),
        invariant=(
            "Resume-safety is a property of the consumer, not the stream: "
            "at-least-once delivery is an accepted trade-off only when "
            "every consumer that attaches a side effect is itself "
            "idempotent on event_id."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason=(
            "No completion-notification-on-evidence-readiness consumer "
            "exists yet. The Wave 3 durable-lifecycle work (PR #97) built "
            "opaque-cursor resume and proved it safe for the run-lifecycle "
            "stream itself, but that does not automatically cover a new "
            "Wave 4 consumer's own side effect — the new consumer must "
            "independently dedupe on event_id, and nothing structural "
            "forces it to."
        ),
    ),
)

GROUP = ScenarioGroup(attack_class=_CLASS, scenarios=SCENARIOS)
