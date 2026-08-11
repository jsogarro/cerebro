"""Executable invariants for run-scoped version pinning."""

import pytest
from pydantic import ValidationError

from src.core.contracts import (
    PinnedComponentKind,
    PinnedComponentVersion,
    PinnedVersions,
)

PROMPT_PIN = PinnedComponentVersion(
    kind=PinnedComponentKind.PROMPT,
    component_id="research.synthesis",
    version="3",
)
EVALUATOR_PIN = PinnedComponentVersion(
    kind=PinnedComponentKind.EVALUATOR,
    component_id="citation-resolution",
    version="2026-07-01",
)
SCHEMA_PIN = PinnedComponentVersion(
    kind=PinnedComponentKind.ARTIFACT_SCHEMA,
    component_id="research-report",
    version="1.4",
)
POLICY_PIN = PinnedComponentVersion(
    kind=PinnedComponentKind.POLICY,
    component_id="tool-permission",
    version="7",
)


def _pinned_versions(**overrides: object) -> PinnedVersions:
    payload: dict[str, object] = {
        "workflow_definition_id": "research",
        "workflow_definition_version": "1",
        "routing_policy_id": "default",
        "routing_policy_version": "1",
        "event_envelope_version": "1.0",
        "components": (PROMPT_PIN, EVALUATOR_PIN, SCHEMA_PIN, POLICY_PIN),
    }
    payload.update(overrides)
    return PinnedVersions.model_validate(payload)


def test_pinned_versions_round_trip_through_canonical_json() -> None:
    pinned = _pinned_versions()

    restored = PinnedVersions.model_validate_json(pinned.canonical_json())

    assert restored == pinned


def test_pinned_versions_is_immutable() -> None:
    pinned = _pinned_versions()

    with pytest.raises(ValidationError):
        pinned.workflow_definition_version = "2"  # type: ignore[misc]


def test_pinned_versions_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _pinned_versions(prompt_version="9")


@pytest.mark.parametrize(
    "missing_field",
    [
        "workflow_definition_id",
        "workflow_definition_version",
        "routing_policy_id",
        "routing_policy_version",
        "event_envelope_version",
    ],
)
def test_pinned_versions_requires_every_core_pin(missing_field: str) -> None:
    payload = _pinned_versions().model_dump()
    del payload[missing_field]

    with pytest.raises(ValidationError):
        PinnedVersions.model_validate(payload)


def test_pinned_versions_rejects_duplicate_component_pins() -> None:
    duplicate = PinnedComponentVersion(
        kind=PinnedComponentKind.PROMPT,
        component_id=PROMPT_PIN.component_id,
        version="4",
    )

    with pytest.raises(ValidationError):
        _pinned_versions(components=(PROMPT_PIN, duplicate))


def test_same_component_id_may_be_pinned_under_different_kinds() -> None:
    prompt = PinnedComponentVersion(
        kind=PinnedComponentKind.PROMPT,
        component_id="shared",
        version="1",
    )
    evaluator = PinnedComponentVersion(
        kind=PinnedComponentKind.EVALUATOR,
        component_id="shared",
        version="1",
    )

    pinned = _pinned_versions(components=(prompt, evaluator))

    assert len(pinned.components) == 2


def test_version_of_resolves_a_pinned_component() -> None:
    pinned = _pinned_versions()

    assert (
        pinned.version_of(PinnedComponentKind.EVALUATOR, "citation-resolution")
        == "2026-07-01"
    )
    assert pinned.version_of(PinnedComponentKind.EVALUATOR, "unpinned") is None


def test_versions_for_filters_by_kind() -> None:
    pinned = _pinned_versions()

    assert pinned.versions_for(PinnedComponentKind.ARTIFACT_SCHEMA) == {
        "research-report": "1.4"
    }


def test_empty_component_set_is_allowed_and_still_pins_the_core_versions() -> None:
    pinned = _pinned_versions(components=())

    assert pinned.components == ()
    assert pinned.routing_policy_version == "1"
    assert pinned.versions_for(PinnedComponentKind.PROMPT) == {}


def test_a_pin_may_carry_the_content_digest_of_what_it_pinned() -> None:
    """A declared version cannot detect an edit that skipped the version bump.

    Prompts resolve at runtime by agent type, and their declared versions are a
    hand-maintained table. Recording the digest of the pinned content alongside
    the version is what makes drift between admission and execution detectable
    rather than merely unlikely.
    """
    pinned = PinnedComponentVersion(
        kind=PinnedComponentKind.PROMPT,
        component_id="agent.literature_review",
        version="1.0.0",
        content_sha256="b" * 64,
    )

    assert pinned.content_sha256 == "b" * 64


def test_a_pin_without_a_digest_is_still_valid_but_unverifiable() -> None:
    assert PROMPT_PIN.content_sha256 is None


def test_a_malformed_digest_is_rejected() -> None:
    with pytest.raises(ValidationError, match="String should match pattern"):
        PinnedComponentVersion(
            kind=PinnedComponentKind.PROMPT,
            component_id="agent.literature_review",
            version="1.0.0",
            content_sha256="not-a-digest",
        )


def test_the_pinned_digest_is_retrievable_for_drift_comparison() -> None:
    pinned = PinnedVersions(
        workflow_definition_id="workflow.research",
        workflow_definition_version="1.0.0",
        routing_policy_id="routing.default",
        routing_policy_version="1.0.0",
        event_envelope_version="1.0",
        components=(
            PinnedComponentVersion(
                kind=PinnedComponentKind.PROMPT,
                component_id="agent.literature_review",
                version="1.0.0",
                content_sha256="c" * 64,
            ),
        ),
    )

    assert (
        pinned.content_hash_of(PinnedComponentKind.PROMPT, "agent.literature_review")
        == "c" * 64
    )
    assert pinned.content_hash_of(PinnedComponentKind.PROMPT, "absent") is None
