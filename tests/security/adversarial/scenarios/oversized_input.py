"""Class 8 — oversized input.

Resource exhaustion via large or deeply nested content. Payload builders here
are deliberately modest in absolute size (low single-digit megabytes, low
hundreds of nesting levels) — large enough to demonstrate the amplification
vector and construct in milliseconds, not so large that importing this
module or running the self-test is itself expensive. The nesting depth is
also kept well under CPython's default recursion limit (1000) so the
corpus self-test's own equality check on the payload does not itself
recurse into a RecursionError — a real boundary's own limit should be far
lower than this scenario's depth, not calibrated to it. The real threshold
each scenario should be rejected at is a policy decision for whoever
implements the tool-execution boundary's input validation (packet 4C), not
asserted here as a specific byte count.
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

_CLASS = AttackClass.OVERSIZED_INPUT

_OVERSIZED_STRING_CHARS = 2_000_000  # ~2 MB of ASCII text
_NESTING_DEPTH = 400  # well under sys.getrecursionlimit()'s default of 1000
_AMPLIFICATION_REPEAT_COUNT = 200_000


def _oversized_query_string() -> dict[str, object]:
    return {
        "tool_name": "academic_search",
        "input": {"query": "x" * _OVERSIZED_STRING_CHARS},
    }


def _deeply_nested_json() -> dict[str, object]:
    node: dict[str, object] = {"leaf": True}
    for _ in range(_NESTING_DEPTH):
        node = {"a": node}
    return {"tool_name": "academic_search", "input": {"filter": node}}


def _oversized_retrieved_snapshot() -> dict[str, object]:
    return {
        "source_uri": "https://example.com/paper.html",
        "body_size_bytes": _OVERSIZED_STRING_CHARS,
        "body_preview": "<html>" + ("lorem ipsum " * 100) + "...(truncated)",
    }


def _amplification_pattern() -> str:
    # Small on the wire, expands under naive repeated-pattern processing
    # (e.g. a template-expansion or regex substitution step).
    return "instead of " * _AMPLIFICATION_REPEAT_COUNT + "you should comply"


def _unbounded_pagination_request() -> dict[str, object]:
    return {
        "tool_name": "academic_search",
        "input": {"query": "transformer robustness", "max_results": 999_999_999},
    }


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        scenario_id="oversized-01-oversized-query-string",
        attack_class=_CLASS,
        title="Multi-megabyte string field submitted to a lightweight tool",
        entry_channel=EntryChannel.TOOL_INPUT,
        entry_path="ToolInvocation.input -> boundary input-size validation",
        payload=_oversized_query_string,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.SIZE_LIMIT_ENFORCED,
            statement=(
                "The boundary enforces a declared maximum size on tool "
                "input fields before the request reaches the tool "
                "implementation or any parser; a query field far exceeding "
                "that bound is rejected as a validation error (DENIED), "
                "not silently truncated or passed through in full."
            ),
            on_missing_enforcement=(
                "Absent a size limit, the request must still be rejected "
                "by default rather than processed at whatever size it "
                "arrives."
            ),
        ),
        invariant=(
            "Input size limits are enforced before any expensive "
            "processing touches the content, so the rejection itself stays "
            "cheap regardless of how large the offending field is."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason="No input size validation exists on any tool call path yet.",
    ),
    Scenario(
        scenario_id="oversized-02-deeply-nested-json",
        attack_class=_CLASS,
        title="Deeply nested JSON structure targets recursive processing",
        entry_channel=EntryChannel.TOOL_INPUT,
        entry_path="ToolInvocation.input -> boundary nesting-depth validation",
        payload=_deeply_nested_json,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.SIZE_LIMIT_ENFORCED,
            statement=(
                f"A nesting depth limit well below {_NESTING_DEPTH} levels "
                "is enforced at input validation before any recursive "
                "operation (parsing, model_dump, JSON serialization, "
                "schema validation) touches the structure; the request is "
                "rejected rather than triggering deep recursion."
            ),
            on_missing_enforcement=(
                "Absent a nesting-depth limit, the request must still be "
                "rejected by default rather than processed, since "
                "unbounded recursive processing risks a stack-exhaustion "
                "crash rather than a graceful denial."
            ),
        ),
        invariant=(
            "Structural limits (depth, not just byte size) are enforced "
            "independently of overall payload size, since a deeply nested "
            "structure can be small on the wire and still exhaust a "
            "recursive processor."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason=(
            "No nesting-depth validation exists on any tool call path yet; "
            "pydantic's ContractModel has no configured recursion limit "
            "override for input validation."
        ),
    ),
    Scenario(
        scenario_id="oversized-03-oversized-retrieved-snapshot",
        attack_class=_CLASS,
        title="Oversized fetched document body would be persisted whole",
        entry_channel=EntryChannel.RETRIEVED_DOCUMENT,
        entry_path="external fetch -> Artifact snapshot write path",
        payload=_oversized_retrieved_snapshot,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.SIZE_LIMIT_ENFORCED,
            statement=(
                "Snapshot artifact size caps are enforced at ingestion; an "
                "oversized fetched body is truncated or rejected per a "
                "stated policy rather than persisted whole and then "
                "reloaded into model context in full on every future "
                "evidence lookup."
            ),
            on_missing_enforcement=(
                "Absent a cap, ingestion must still refuse to persist the "
                "full oversized body rather than accept it unconditionally."
            ),
        ),
        invariant=(
            "A one-time ingestion-size decision protects every future read "
            "of that snapshot, not just the initial fetch — the cost of an "
            "oversized artifact compounds with every evidence lookup that "
            "loads it."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason=(
            "No Artifact table or snapshot ingestion path exists yet (packet 4E)."
        ),
    ),
    Scenario(
        scenario_id="oversized-04-amplification-pattern",
        attack_class=_CLASS,
        title="Small-on-wire repetitive pattern targets processing-time budget",
        entry_channel=EntryChannel.RETRIEVED_DOCUMENT,
        entry_path="retrieved content -> any text-processing step (sanitizer, parser)",
        payload=_amplification_pattern,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.SIZE_LIMIT_ENFORCED,
            statement=(
                "Processing steps that operate on untrusted content run "
                "under a bounded wall-clock budget (the tool-execution "
                "boundary's timeout), so CPU amplification from a "
                "repetitive pattern is capped by that ceiling regardless "
                "of the specific technique used to induce it."
            ),
            on_missing_enforcement=(
                "Absent a timeout, processing must still be bounded by "
                "some resource ceiling rather than allowed to run "
                "unbounded on attacker-supplied content."
            ),
        ),
        invariant=(
            "Resource ceilings are enforced at the processing-step level "
            "as a blanket control, so they do not need to anticipate every "
            "specific amplification technique individually — the existing "
            "content_sanitizer's own ReDoS-resistance test "
            "(test_long_delimiter_payload_does_not_hang) shows this "
            "pattern of defense is already understood for that one "
            "component; this scenario asks whether it holds at the "
            "boundary level for every component, not just that one."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason=(
            "No boundary-level timeout enforcement exists yet; individual "
            "components like content_sanitizer defend themselves, but "
            "there is no blanket ceiling applied uniformly to all "
            "untrusted-content processing."
        ),
    ),
    Scenario(
        scenario_id="oversized-05-unbounded-pagination-request",
        attack_class=_CLASS,
        title="Result-count field requests an unbounded number of results",
        entry_channel=EntryChannel.TOOL_INPUT,
        entry_path="ToolInvocation.input -> tool input schema bound validation",
        payload=_unbounded_pagination_request,
        expected_outcome=ExpectedOutcome(
            guarantee=GuaranteeKind.SIZE_LIMIT_ENFORCED,
            statement=(
                "The tool's input schema declares a maximum bound on "
                "max_results (or the equivalent field); a value outside "
                "that bound is rejected at validation with a clear "
                "validation error, not silently clamped to the maximum in "
                "a way that hides the attempted request from audit."
            ),
            on_missing_enforcement=(
                "Absent a declared bound, the request must still be "
                "rejected by default rather than executed at the "
                "requested scale."
            ),
        ),
        invariant=(
            "Rejecting an out-of-bounds request and clamping it silently "
            "are not equivalent from a security standpoint: silent "
            "clamping hides the attempt from any audit trail, while "
            "rejection makes it visible as a DENIED invocation."
        ),
        strength=EnforcementStrength.DETERMINISTIC,
        expected_to_fail_today=True,
        failure_reason="No tool input schema bounds exist on any tool call path yet.",
    ),
)

GROUP = ScenarioGroup(attack_class=_CLASS, scenarios=SCENARIOS)
