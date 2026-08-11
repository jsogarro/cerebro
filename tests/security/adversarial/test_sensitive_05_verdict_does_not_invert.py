"""`sensitive-05` must not upgrade its verdict for having been refused.

The exerciser used to read
``Verdict.HELD_VIA_FLOOR if all_allowed else Verdict.HELD``, and ``HELD``
outranks ``HELD_VIA_FLOOR`` (see :class:`Verdict`). So a boundary that
*refused* the sequence would have scored this scenario **higher** than one that
allowed it — rewarding it for the single outcome it cannot take credit for.

The scenario's own guarantee is ``NO_DETERMINISTIC_GUARANTEE_TODAY``: nothing
in ``src/core/tools/`` carries state across invocations, so no cumulative-effect
rule exists that could refuse the sequence. Any refusal therefore comes from
something *unrelated* failing — and the inverted branch would have read that as
the guarantee holding.

**Why this test needs a stub, stated plainly.** The else-branch is unreachable
against the real boundary today: all four calls are individually authorized and
all four succeed, so a corpus run only ever takes the ``all_allowed`` path. That
is exactly why the inversion survived — it lived in a path nothing executed.
A test that merely ran the corpus would prove nothing about it, so this forces
the denial by giving the exerciser a grant for a different tool, which produces
a real ``NO_MATCHING_GRANT`` denial through the real capability machinery rather
than a fabricated outcome.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.contracts.capabilities import SensitivityClass

from . import exercisers
from .harness import Verdict


@pytest.fixture(name="denied_grants")
def denied_grants_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every call in the scenario land on a real capability denial.

    The grant is issued for a tool the scenario never invokes, so
    ``decide_capability`` finds no candidate and denies with
    ``NO_MATCHING_GRANT``. The denial is produced by the same code path a
    genuine refusal would take.
    """

    real_grant_for = exercisers.grant_for

    def grant_for_another_tool(**kwargs: Any) -> Any:
        return real_grant_for(**{**kwargs, "tool_name": "a-tool-nobody-calls"})

    monkeypatch.setattr(exercisers, "grant_for", grant_for_another_tool)


async def test_a_refused_sequence_does_not_outrank_an_allowed_one(
    denied_grants: None,
) -> None:
    observation = await exercisers.EXERCISERS[
        "sensitive-05-compositional-privilege-laundering"
    ]()

    assert observation.verdict is not Verdict.HELD, (
        "the boundary refused the sequence and the scenario reported the "
        "strongest verdict it has. A refusal here cannot have come from a "
        "cumulative-effect rule, because none exists — so this would be the "
        "scenario taking credit for an unrelated failure."
    )
    assert observation.verdict is Verdict.HELD_VIA_FLOOR


async def test_the_evidence_reports_what_actually_happened(
    denied_grants: None,
) -> None:
    """The old evidence string asserted 'all executed' unconditionally."""

    observation = await exercisers.EXERCISERS[
        "sensitive-05-compositional-privilege-laundering"
    ]()

    assert "denied" in observation.evidence
    assert "not all executed" in observation.evidence


async def test_the_allowed_path_is_unchanged() -> None:
    """The positive control: without the stub, the scenario is as it was.

    Without this, a fix that returned ``HELD_VIA_FLOOR`` unconditionally *and*
    broke the normal path would still satisfy both tests above.
    """

    observation = await exercisers.EXERCISERS[
        "sensitive-05-compositional-privilege-laundering"
    ]()

    assert observation.verdict is Verdict.HELD_VIA_FLOOR
    assert "all executed" in observation.evidence
    assert observation.control.kind is not None


def test_the_scenario_still_declares_the_sensitivity_it_exercises() -> None:
    """Guards the fixture: a renamed tool would make the denial vacuous."""

    assert SensitivityClass.INTERNAL_WRITE.value == "internal_write"
