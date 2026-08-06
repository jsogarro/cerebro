"""Trust-label propagation must be a deterministic function, not a convention.

Wave 4 treats retrieved content as untrusted data. That only means anything if
the label a transformation produces is computed rather than chosen, and if no
transformation can ever return output that is more trusted than its inputs.
"""

import itertools

import pytest

from src.core.contracts import TrustClassification
from src.core.contracts.trust import (
    at_least_as_trusted,
    propagate_trust,
    trust_rank,
)

ALL_LABELS = tuple(TrustClassification)


def test_trust_labels_are_totally_ordered_with_no_ties() -> None:
    ranks = [trust_rank(label) for label in ALL_LABELS]

    assert len(set(ranks)) == len(ALL_LABELS)
    assert trust_rank(TrustClassification.TRUSTED_CONTROL) == 0
    assert trust_rank(TrustClassification.DERIVED_UNTRUSTED) == max(ranks)


def test_derived_untrusted_is_the_least_trusted_label() -> None:
    assert trust_rank(TrustClassification.DERIVED_UNTRUSTED) > trust_rank(
        TrustClassification.EXTERNAL_UNTRUSTED
    )


def test_propagation_requires_at_least_one_input() -> None:
    with pytest.raises(ValueError, match="at least one input"):
        propagate_trust(())


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        (TrustClassification.TRUSTED_CONTROL, TrustClassification.TRUSTED_CONTROL),
        (TrustClassification.APPLICATION, TrustClassification.APPLICATION),
        (TrustClassification.USER_SUPPLIED, TrustClassification.DERIVED_UNTRUSTED),
        (
            TrustClassification.EXTERNAL_UNTRUSTED,
            TrustClassification.DERIVED_UNTRUSTED,
        ),
        (TrustClassification.DERIVED_UNTRUSTED, TrustClassification.DERIVED_UNTRUSTED),
    ],
)
def test_single_input_propagation_is_frozen(
    label: TrustClassification, expected: TrustClassification
) -> None:
    assert propagate_trust((label,)) is expected


def test_propagation_never_launders_taint() -> None:
    """The output can never be more trusted than the worst input."""
    for size in (1, 2, 3):
        for combination in itertools.product(ALL_LABELS, repeat=size):
            output = propagate_trust(combination)
            worst = max(trust_rank(label) for label in combination)

            assert trust_rank(output) >= worst, combination


def test_propagation_is_order_independent() -> None:
    for combination in itertools.permutations(ALL_LABELS, 3):
        assert propagate_trust(combination) is propagate_trust(
            tuple(reversed(combination))
        )


def test_mixing_control_with_untrusted_content_taints_the_output() -> None:
    result = propagate_trust(
        (
            TrustClassification.TRUSTED_CONTROL,
            TrustClassification.EXTERNAL_UNTRUSTED,
        )
    )

    assert result is TrustClassification.DERIVED_UNTRUSTED


def test_propagation_is_idempotent_under_reapplication() -> None:
    """Re-labelling an already-labelled output must not move the label again."""
    for combination in itertools.product(ALL_LABELS, repeat=2):
        once = propagate_trust(combination)

        assert propagate_trust((once,)) is once, combination


def test_at_least_as_trusted_matches_the_rank_order() -> None:
    assert at_least_as_trusted(
        TrustClassification.APPLICATION, TrustClassification.USER_SUPPLIED
    )
    assert at_least_as_trusted(
        TrustClassification.APPLICATION, TrustClassification.APPLICATION
    )
    assert not at_least_as_trusted(
        TrustClassification.EXTERNAL_UNTRUSTED, TrustClassification.APPLICATION
    )
