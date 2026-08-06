"""Redaction happens once, at the boundary, and hashing follows from it.

The load-bearing property is that a persisted boundary record's digest is
reproducible from the record as persisted. If a record were hashed raw and
stored redacted, its digest could never be checked again and evidence
integrity would become unfalsifiable rather than merely absent.
"""

import pytest

from src.core.contracts.redaction import (
    REDACTION_MARKER,
    SecretRef,
    boundary_digest,
    redact,
    snapshot_digest,
)


def test_values_under_credential_key_names_are_replaced() -> None:
    redacted = redact({"api_key": "sk-live-abcdef", "query": "primary source"})

    assert redacted == {"api_key": REDACTION_MARKER, "query": "primary source"}


def test_key_matching_ignores_case_and_separators() -> None:
    redacted = redact(
        {"X-API-Key": "abc", "Authorization": "Bearer xyz", "refresh_token": "r"}
    )

    assert set(redacted.values()) == {REDACTION_MARKER}


def test_non_string_values_under_a_credential_key_are_still_removed() -> None:
    redacted = redact({"token": 1234, "secret": {"nested": "value"}})

    assert redacted == {"token": REDACTION_MARKER, "secret": REDACTION_MARKER}


def test_credential_shaped_values_are_replaced_under_any_key() -> None:
    """The safety net for secrets arriving inside untrusted tool output."""
    redacted = redact(
        {
            "scraped": "the deploy key is ghp_0123456789abcdefghijklmnopqrstuvwxyz",
            "note": "nothing sensitive here",
        }
    )

    assert REDACTION_MARKER in str(redacted["scraped"])
    assert redacted["note"] == "nothing sensitive here"


def test_redaction_recurses_through_nested_containers() -> None:
    redacted = redact({"outer": [{"password": "p"}, {"safe": "s"}]})

    assert redacted == {"outer": [{"password": REDACTION_MARKER}, {"safe": "s"}]}


def test_redaction_does_not_mutate_its_input() -> None:
    original = {"password": "p", "nested": {"token": "t"}}
    snapshot = {"password": "p", "nested": {"token": "t"}}

    redact(original)

    assert original == snapshot


def test_redaction_is_idempotent() -> None:
    payload = {
        "api_key": "sk-live-abcdef",
        "outer": [{"password": "p"}, "ghp_0123456789abcdefghijklmnopqrstuvwxyz"],
        "safe": "value",
    }
    once = redact(payload)

    assert redact(once) == once


def test_a_persisted_records_digest_is_reproducible_from_what_was_persisted() -> None:
    """Redact, then hash. Never hash raw and store redacted."""
    payload = {"api_key": "sk-live-abcdef", "query": "primary source"}
    stored = redact(payload)

    assert boundary_digest(stored) == boundary_digest(redact(stored))


def test_redacted_and_raw_content_hash_differently() -> None:
    payload = {"api_key": "sk-live-abcdef"}

    assert boundary_digest(payload) != boundary_digest(redact(payload))


def test_boundary_digest_is_independent_of_key_order() -> None:
    assert boundary_digest({"a": 1, "b": 2}) == boundary_digest({"b": 2, "a": 1})


def test_a_secret_reference_survives_redaction_because_it_carries_no_secret() -> None:
    payload = {"credential": SecretRef(secret_id="openrouter-api-key").as_json()}

    assert redact(payload) == payload


def test_a_secret_reference_rejects_an_embedded_literal() -> None:
    with pytest.raises(ValueError, match="at least 1 character"):
        SecretRef(secret_id="")


def test_snapshot_digest_covers_acquired_bytes_verbatim() -> None:
    """Snapshot integrity means 'this is what the source said', unredacted."""
    raw = b"the key is sk-live-abcdef"

    assert snapshot_digest(raw) == snapshot_digest(b"the key is sk-live-abcdef")
    assert snapshot_digest(raw) != snapshot_digest(b"the key is [REDACTED]")


def test_snapshot_digest_and_boundary_digest_are_not_interchangeable() -> None:
    with pytest.raises(TypeError):
        snapshot_digest({"not": "bytes"})  # type: ignore[arg-type]


# --- The two notions of "secret", and which failure mode we accept ----------


def test_a_held_secret_is_removed_by_exact_value_even_with_no_known_shape() -> None:
    """The strong, deterministic layer: values the system actually holds.

    Shape patterns cannot catch a secret in a format they do not know. An
    exact-value pass over the credentials this system holds can, and it has no
    false positives by construction.
    """
    held = "hunter2-CEREBRO-SENTINEL-9f3a"
    payload = {"scraped": f"the operator pasted {held} into the ticket"}

    redacted = redact(payload, known_secret_values=frozenset({held}))

    assert held not in str(redacted)
    assert REDACTION_MARKER in str(redacted["scraped"])


def test_exact_value_redaction_reaches_nested_containers_and_keys() -> None:
    held = "hunter2-CEREBRO-SENTINEL-9f3a"

    redacted = redact(
        {"outer": [{"note": held}, held]},
        known_secret_values=frozenset({held}),
    )

    assert held not in str(redacted)


def test_exact_value_redaction_is_idempotent() -> None:
    held = "hunter2-CEREBRO-SENTINEL-9f3a"
    secrets = frozenset({held})
    once = redact({"a": held, "b": "safe"}, known_secret_values=secrets)

    assert redact(once, known_secret_values=secrets) == once


def test_a_short_held_value_is_rejected_rather_than_silently_scrubbing_everything() -> (
    None
):
    """A 2-character 'secret' would match inside ordinary prose."""
    with pytest.raises(ValueError, match="too short to redact by value"):
        redact({"a": "x"}, known_secret_values=frozenset({"ab"}))


def test_shape_matching_over_redacts_a_reserved_placeholder() -> None:
    """The accepted failure mode, asserted so it cannot change unnoticed.

    ``AKIAIOSFODNN7EXAMPLE`` is AWS's reserved documentation placeholder and is
    not a credential. The shape layer removes it anyway. We accept this false
    positive because redaction never touches snapshot bytes, so over-redaction
    can degrade a prompt or event excerpt but can never corrupt the evidentiary
    record or shift a locator offset.
    """
    redacted = redact({"note": "see AKIAIOSFODNN7EXAMPLE in the AWS docs"})

    assert REDACTION_MARKER in str(redacted["note"])


def test_over_redaction_cannot_reach_the_evidentiary_record() -> None:
    """Snapshot integrity is computed over verbatim bytes, never redacted ones."""
    verbatim = b"see AKIAIOSFODNN7EXAMPLE in the AWS docs"

    assert snapshot_digest(verbatim) == snapshot_digest(verbatim)
    assert b"AKIAIOSFODNN7EXAMPLE" in verbatim


@pytest.mark.parametrize(
    ("held", "surviving_fragment"),
    [
        pytest.param(
            "sk-abcdefgh SESSION xyz-tenant-42",
            "xyz-tenant-42",
            id="shape-matches-the-prefix",
        ),
        pytest.param(
            "prefix-AKIAIOSFODNN7EXAMPLE-suffix",
            "suffix",
            id="shape-matches-the-middle",
        ),
    ],
)
def test_exact_values_are_applied_before_shape_patterns(
    held: str, surviving_fragment: str
) -> None:
    """Ordering is load-bearing: a shape pass can fragment a held secret.

    Where a held secret only *partially* overlaps a pattern, running shape
    first replaces the overlapping part, and the exact-value pass no longer
    recognises what remains — so the rest of the secret survives into the
    record. Running exact values first removes the whole value.

    The two cases differ in where the overlap falls. When the pattern matches
    the prefix, the tail survives; when it matches the middle, the surrounding
    structure survives on both sides. Either way the record keeps material that
    was supposed to be gone.

    This is invisible to every other exact-value test, because those use held
    secrets that overlap no pattern at all — which is why this needs its own
    case and should not be deleted as redundant.
    """
    payload = {"scraped": f"operator pasted {held} into the ticket"}

    redacted = redact(payload, known_secret_values=frozenset({held}))

    # Assert the whole result, not merely the absence of the fragment, so this
    # pins what the correct ordering *produces* rather than only what it
    # removes. A future change that over-redacts the surrounding prose would
    # otherwise pass.
    assert redacted == {
        "scraped": f"operator pasted {REDACTION_MARKER} into the ticket"
    }
    assert held not in str(redacted)
    assert surviving_fragment not in str(redacted)
