"""Pure pilot arithmetic: unit costs, projection, cadence and preflight."""

from decimal import Decimal

import pytest

from src.evals.cost_pilot import (
    PilotError,
    PreflightRefusedError,
    RunSample,
    agent_unit_cost,
    bootstrap_mean_ci,
    build_report,
    judge_unit_cost,
    nightly_estimate,
    preflight,
    recommend_cadence,
)

JUDGE_U_JT_AT_24_CLAIMS = Decimal("0.19584")


def _run(cost: str | None = "0.10", outcome: str = "succeeded", claims: int = 10):
    return RunSample(
        domain="research",
        outcome=outcome,
        ledger_cost_usd=None if cost is None else Decimal(cost),
        wall_seconds=60.0,
        claims=claims,
    )


def test_u_a_is_billed_per_run_and_flags_a_disagreeing_ledger() -> None:
    samples = [_run("0.10") for _ in range(10)]  # ledger total $1.00

    unit = agent_unit_cost(samples, billed_usd=Decimal("3.00"))

    assert unit.u_a == Decimal("0.30")
    assert unit.ledger_billed_ratio == Decimal("1.00") / Decimal("3.00")
    assert unit.ledger_untrusted


def test_ledger_within_ten_percent_is_trusted() -> None:
    unit = agent_unit_cost([_run("0.10") for _ in range(10)], Decimal("1.05"))

    assert unit.u_a == Decimal("0.105")
    assert not unit.ledger_untrusted


def test_a_run_without_usage_is_unpriced_not_free() -> None:
    samples = [_run("0.10"), _run(None, outcome="timed_out")]

    unit = agent_unit_cost(samples, billed_usd=Decimal("0.10"))

    assert unit.unpriced_runs == 1
    assert unit.ledger_untrusted
    assert unit.u_a == Decimal("0.05")


def test_no_succeeded_run_raises() -> None:
    with pytest.raises(PilotError, match="no run succeeded"):
        agent_unit_cost([_run(outcome="failed")], billed_usd=Decimal("0.10"))
    with pytest.raises(PilotError):
        agent_unit_cost([], billed_usd=Decimal("0"))


@pytest.mark.parametrize(
    ("u_a", "expected"),
    [("0.01", "69.08"), ("0.05", "119.00"), ("0.20", "306.20")],
)
def test_projection_matches_the_planning_rows(u_a: str, expected: str) -> None:
    assert nightly_estimate(Decimal(u_a), JUDGE_U_JT_AT_24_CLAIMS) == Decimal(expected)


def test_judge_cost_clamps_claims_at_24() -> None:
    assert judge_unit_cost(Decimal(24)) == JUDGE_U_JT_AT_24_CLAIMS
    assert judge_unit_cost(Decimal(10)) == Decimal(10) * Decimal("1.5") * Decimal(
        "0.00544"
    )
    assert judge_unit_cost(Decimal(30)) == JUDGE_U_JT_AT_24_CLAIMS


def test_cadence_rule() -> None:
    assert recommend_cadence(
        Decimal("0.01"), JUDGE_U_JT_AT_24_CLAIMS, ceiling=Decimal("200")
    ) == ("full_nightly")
    assert recommend_cadence(
        Decimal("0.006"), JUDGE_U_JT_AT_24_CLAIMS, ceiling=Decimal("12")
    ) == ("tier1_nightly_weekly_full")
    assert recommend_cadence(
        Decimal("0.007"), JUDGE_U_JT_AT_24_CLAIMS, ceiling=Decimal("12")
    ) == ("weekly_full_report_only")
    with pytest.raises(PilotError, match="ceiling"):
        recommend_cadence(
            Decimal("0.01"), JUDGE_U_JT_AT_24_CLAIMS, ceiling=Decimal("0")
        )


def test_bootstrap_ci_is_seeded_and_brackets_the_mean() -> None:
    values = [Decimal(v) for v in ("0.05", "0.10", "0.20", "0.08", "0.12", "0.30")]

    first = bootstrap_mean_ci(values, seed=7)
    assert first == bootstrap_mean_ci(values, seed=7)
    assert first[0] < sum(values) / len(values) < first[1]


def _preflight(**overrides: object) -> None:
    kwargs: dict[str, object] = {
        "runs": 30,
        "concurrency": 4,
        "per_run_cap": Decimal("0.25"),
        "budget": Decimal("10"),
        "eval_database_url": "postgresql+asyncpg://u@127.0.0.1:5432/cerebro_pilot",
        "app_database_url": "postgresql://u@localhost/cerebro",
        "research_runs": 12,
        "mcp_tool_path_enabled": True,
        "openrouter_enabled": True,
    }
    kwargs.update(overrides)
    preflight(**kwargs)  # type: ignore[arg-type]


def test_preflight_passes_the_stage_b_configuration() -> None:
    _preflight()


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"per_run_cap": Decimal("0.30")}, "worst case"),
        (
            {"eval_database_url": "postgresql+asyncpg://u@127.0.0.1/cerebro"},
            "application database",
        ),
        ({"eval_database_url": None}, "EVAL_RUN_DATABASE_URL"),
        ({"mcp_tool_path_enabled": False}, "MCP_TOOL_PATH_ENABLED"),
        ({"openrouter_enabled": False}, "OPENROUTER_ENABLED"),
    ],
)
def test_preflight_refuses(overrides: dict[str, object], reason: str) -> None:
    with pytest.raises(PreflightRefusedError, match=reason):
        _preflight(**overrides)


def test_report_has_every_decision_input() -> None:
    samples = [_run("0.10", claims=c) for c in (5, 10, 30)] + [
        _run("0.05", outcome="budget_exceeded", claims=0)
    ]

    report = build_report(samples, billed_usd=Decimal("0.70"), gemini_calls=0, seed=1)

    assert report["u_a"]["point"] == "0.175"
    assert report["u_a"]["basis"] == "billed"
    assert report["u_a"]["ci90"] is not None
    assert report["outcomes"] == {"succeeded": 3, "budget_exceeded": 1}
    assert report["claims"]["share_over_cap"] == 0.25
    assert report["gemini_calls"] == 0
    assert set(report["nightly_estimate_usd"]) == {"low", "mid", "high"}
    assert report["nightly_ceiling_usd"] == "12"
    assert report["cadence"] == "weekly_full_report_only"
