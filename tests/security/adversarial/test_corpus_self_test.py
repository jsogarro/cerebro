"""Self-test for the adversarial corpus itself.

Most well-formedness rules (non-empty title/invariant/entry_path, an
ADVISORY scenario must explain itself, expected_to_fail_today requires a
reason) are enforced by ``Scenario.__post_init__`` at *construction* time —
which means a malformed entry already breaks collection loudly, at import,
rather than silently never running. This file adds the checks that can only
be made across the whole corpus (uniqueness, per-class coverage, payload
determinism) and proves the construction-time validation actually rejects a
malformed entry, rather than merely assuming it does.
"""

from __future__ import annotations

import pytest

from .registry import (
    ALL_SCENARIOS,
    by_attack_class,
    by_strength,
    expected_to_fail_today_scenarios,
)
from .types import (
    AttackClass,
    EnforcementStrength,
    ExpectedOutcome,
    GuaranteeKind,
    Scenario,
    ScenarioGroup,
)


class TestCorpusIsNonEmptyAndCovers9Classes:
    def test_corpus_is_non_empty(self) -> None:
        assert len(ALL_SCENARIOS) > 0

    @pytest.mark.parametrize("attack_class", list(AttackClass))
    def test_every_attack_class_has_at_least_four_scenarios(
        self, attack_class: AttackClass
    ) -> None:
        scenarios = by_attack_class(attack_class)
        assert len(scenarios) >= 4, (
            f"{attack_class} has only {len(scenarios)} scenarios; the "
            "corpus should cover each class with more than a token example"
        )

    def test_every_scenario_belongs_to_exactly_one_of_the_9_classes(self) -> None:
        seen = {s.attack_class for s in ALL_SCENARIOS}
        assert seen == set(AttackClass)


class TestScenarioIdsAreUnique:
    def test_scenario_ids_are_globally_unique(self) -> None:
        ids = [s.scenario_id for s in ALL_SCENARIOS]
        duplicates = {sid for sid in ids if ids.count(sid) > 1}
        assert not duplicates, f"duplicate scenario_id(s): {duplicates}"

    def test_scenario_ids_are_prefixed_by_a_class_slug(self) -> None:
        # A weak but useful sanity check: every id should start with a
        # short alphabetic slug and a hyphen, e.g. "injection-01-...".
        for scenario in ALL_SCENARIOS:
            prefix = scenario.scenario_id.split("-", 1)[0]
            assert prefix.isalpha() and prefix.islower(), (
                f"{scenario.scenario_id!r} does not start with a lowercase "
                "alphabetic class slug"
            )


class TestEveryScenarioIsWellFormed:
    """Aggregate re-assertion of what __post_init__ already enforces.

    These are redundant with construction-time validation by design: if
    construction had failed, collecting this very test module would have
    failed first. Kept explicit so a reader (or a future refactor that
    loosens __post_init__) has a direct, obviously-named check to point at.
    """

    @pytest.mark.parametrize(
        "scenario", ALL_SCENARIOS, ids=[s.scenario_id for s in ALL_SCENARIOS]
    )
    def test_scenario_has_required_prose_fields(self, scenario: Scenario) -> None:
        assert scenario.title.strip()
        assert scenario.entry_path.strip()
        assert scenario.invariant.strip()
        assert scenario.expected_outcome.statement.strip()
        assert scenario.expected_outcome.on_missing_enforcement.strip()

    @pytest.mark.parametrize(
        "scenario", ALL_SCENARIOS, ids=[s.scenario_id for s in ALL_SCENARIOS]
    )
    def test_scenario_payload_is_non_empty(self, scenario: Scenario) -> None:
        payload = scenario.payload()
        assert payload is not None
        # str/bytes/dict/list all support len(); every payload kind must
        # produce *something*, not an empty container or empty string.
        assert len(payload) > 0  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "scenario", ALL_SCENARIOS, ids=[s.scenario_id for s in ALL_SCENARIOS]
    )
    def test_scenario_payload_is_deterministic(self, scenario: Scenario) -> None:
        first = scenario.payload()
        second = scenario.payload()
        assert first == second, (
            f"{scenario.scenario_id}: payload() must return equal content "
            "on repeated calls so a harness can reproduce a run"
        )

    @pytest.mark.parametrize(
        "scenario", ALL_SCENARIOS, ids=[s.scenario_id for s in ALL_SCENARIOS]
    )
    def test_advisory_scenarios_explain_themselves(self, scenario: Scenario) -> None:
        if scenario.strength is EnforcementStrength.ADVISORY:
            assert scenario.notes.strip()

    @pytest.mark.parametrize(
        "scenario", ALL_SCENARIOS, ids=[s.scenario_id for s in ALL_SCENARIOS]
    )
    def test_expected_to_fail_today_has_a_reason(self, scenario: Scenario) -> None:
        if scenario.expected_to_fail_today:
            assert scenario.failure_reason.strip()
        else:
            assert scenario.failure_reason == ""


class TestOnMissingEnforcementNeverDescribesFallThroughToAllow:
    """Deny-by-default is the property under test.

    This is a heuristic lexical check, not a proof — but an
    ``on_missing_enforcement`` statement that contains language endorsing
    silent allow would be a real authoring mistake worth catching
    mechanically rather than relying on review to always catch it.
    """

    _FORBIDDEN_PHRASES = (
        "falls through to allow",
        "silently allow",
        "is permitted by default",
        "proceeds without",
    )

    @pytest.mark.parametrize(
        "scenario", ALL_SCENARIOS, ids=[s.scenario_id for s in ALL_SCENARIOS]
    )
    def test_no_forbidden_fall_through_language(self, scenario: Scenario) -> None:
        text = scenario.expected_outcome.on_missing_enforcement.lower()
        for phrase in self._FORBIDDEN_PHRASES:
            assert phrase not in text, (
                f"{scenario.scenario_id}: on_missing_enforcement reads as "
                f"endorsing fall-through-to-allow ({phrase!r})"
            )


class TestFilterFunctionsAgreeWithTheCorpus:
    def test_by_attack_class_partitions_the_corpus(self) -> None:
        recombined = tuple(
            scenario
            for attack_class in AttackClass
            for scenario in by_attack_class(attack_class)
        )
        assert sorted(s.scenario_id for s in recombined) == sorted(
            s.scenario_id for s in ALL_SCENARIOS
        )

    def test_by_strength_partitions_the_corpus(self) -> None:
        deterministic = by_strength(EnforcementStrength.DETERMINISTIC)
        advisory = by_strength(EnforcementStrength.ADVISORY)
        assert len(deterministic) + len(advisory) == len(ALL_SCENARIOS)
        assert {s.scenario_id for s in deterministic}.isdisjoint(
            {s.scenario_id for s in advisory}
        )

    def test_expected_to_fail_today_is_a_subset_of_the_corpus(self) -> None:
        failing = expected_to_fail_today_scenarios()
        assert {s.scenario_id for s in failing} <= {
            s.scenario_id for s in ALL_SCENARIOS
        }
        assert all(s.expected_to_fail_today for s in failing)


class TestMalformedEntriesAreRejectedAtConstructionTime:
    """Prove the validation actually rejects bad data, rather than assuming it.

    A corpus with a malformed entry that silently never runs is worse than
    no corpus — so this class deliberately constructs bad entries (never
    added to ALL_SCENARIOS) and asserts they are impossible.
    """

    def _valid_outcome(self) -> ExpectedOutcome:
        return ExpectedOutcome(
            guarantee=GuaranteeKind.CAPABILITY_DENIED,
            statement="a valid statement",
            on_missing_enforcement="denied by default",
        )

    def _valid_kwargs(self) -> dict[str, object]:
        return {
            "scenario_id": "test-00-placeholder",
            "attack_class": AttackClass.PRIVILEGE_ESCALATION,
            "title": "placeholder",
            "entry_channel": None,
            "entry_path": "placeholder",
            "payload": lambda: "x",
            "expected_outcome": self._valid_outcome(),
            "invariant": "placeholder invariant",
            "strength": EnforcementStrength.DETERMINISTIC,
        }

    def test_empty_statement_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="statement must not be empty"):
            ExpectedOutcome(
                guarantee=GuaranteeKind.CAPABILITY_DENIED,
                statement="   ",
                on_missing_enforcement="denied by default",
            )

    def test_empty_on_missing_enforcement_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="on_missing_enforcement"):
            ExpectedOutcome(
                guarantee=GuaranteeKind.CAPABILITY_DENIED,
                statement="a valid statement",
                on_missing_enforcement="",
            )

    def test_empty_scenario_id_is_rejected(self) -> None:
        from .types import EntryChannel

        kwargs = self._valid_kwargs()
        kwargs["scenario_id"] = ""
        kwargs["entry_channel"] = EntryChannel.TOOL_INPUT
        with pytest.raises(ValueError, match="scenario_id must not be empty"):
            Scenario(**kwargs)  # type: ignore[arg-type]

    def test_advisory_scenario_without_notes_is_rejected(self) -> None:
        from .types import EntryChannel

        kwargs = self._valid_kwargs()
        kwargs["strength"] = EnforcementStrength.ADVISORY
        kwargs["entry_channel"] = EntryChannel.TOOL_INPUT
        with pytest.raises(ValueError, match="ADVISORY scenarios must explain"):
            Scenario(**kwargs)  # type: ignore[arg-type]

    def test_expected_to_fail_today_without_reason_is_rejected(self) -> None:
        from .types import EntryChannel

        kwargs = self._valid_kwargs()
        kwargs["entry_channel"] = EntryChannel.TOOL_INPUT
        kwargs["expected_to_fail_today"] = True
        with pytest.raises(ValueError, match="requires a non-empty failure_reason"):
            Scenario(**kwargs)  # type: ignore[arg-type]

    def test_scenario_group_rejects_mismatched_attack_class(self) -> None:
        from .types import EntryChannel

        kwargs = self._valid_kwargs()
        kwargs["entry_channel"] = EntryChannel.TOOL_INPUT
        kwargs["attack_class"] = AttackClass.REPLAY
        mismatched = Scenario(**kwargs)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="does not match group"):
            ScenarioGroup(
                attack_class=AttackClass.PRIVILEGE_ESCALATION,
                scenarios=(mismatched,),
            )
