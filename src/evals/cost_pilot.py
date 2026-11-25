"""Arithmetic for the agent cost pilot. Pure: no I/O, no providers, no clock."""

from __future__ import annotations

import random
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.engine import make_url

NIGHTLY_AGENT_RUNS = 1248
NIGHTLY_JUDGED_TRIALS = 289
JUDGED_CLAIM_CAP = 24
JUDGE_CALLS_PER_CLAIM = Decimal("1.5")
JUDGE_COST_PER_CALL_USD = Decimal("0.00544")
NIGHTLY_CAP_MULTIPLIER = Decimal("1.5")
NIGHTLY_CEILING_USD = Decimal("12")
LEDGER_TOLERANCE = Decimal("0.10")
RUNNER_CAP_SECONDS = 18_000
NIGHTLY_CONCURRENCY = 8
_CENT = Decimal("0.01")


class PilotError(ValueError):
    """The pilot's numbers cannot support an estimate."""


class PreflightRefusedError(PilotError):
    """The pilot must not start; nothing has been spent."""


@dataclass(frozen=True)
class RunSample:
    """One admitted pilot run, as its terminal event and claim rows report it."""

    domain: str
    outcome: str  # succeeded | failed | budget_exceeded | timed_out
    ledger_cost_usd: Decimal | None  # None: the terminal event carried no usage
    wall_seconds: float | None
    claims: int


@dataclass(frozen=True)
class UnitCost:
    u_a: Decimal
    billed_usd: Decimal
    ledger_usd: Decimal
    ledger_billed_ratio: Decimal | None
    ledger_untrusted: bool
    unpriced_runs: int


def agent_unit_cost(samples: Sequence[RunSample], billed_usd: Decimal) -> UnitCost:
    """Billed USD per admitted run; the ledger is only compared against it."""
    if not any(sample.outcome == "succeeded" for sample in samples):
        raise PilotError("no run succeeded, so there is no agent unit cost")
    priced = [s.ledger_cost_usd for s in samples if s.ledger_cost_usd is not None]
    ledger_usd = sum(priced, Decimal(0))
    unpriced = len(samples) - len(priced)
    ratio = ledger_usd / billed_usd if billed_usd > 0 else None
    untrusted = unpriced > 0 or ratio is None or abs(ratio - 1) > LEDGER_TOLERANCE
    return UnitCost(
        u_a=billed_usd / len(samples),
        billed_usd=billed_usd,
        ledger_usd=ledger_usd,
        ledger_billed_ratio=ratio,
        ledger_untrusted=untrusted,
        unpriced_runs=unpriced,
    )


def bootstrap_mean_ci(
    values: Sequence[Decimal], *, seed: int, resamples: int = 2000
) -> tuple[Decimal, Decimal]:
    """Seeded percentile bootstrap, 90% interval of the mean."""
    if not values:
        raise PilotError("no values to bootstrap")
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(float(v) for v in rng.choices(values, k=len(values)))
        for _ in range(resamples)
    )
    low = means[int(0.05 * (resamples - 1))]
    high = means[int(0.95 * (resamples - 1))]
    return Decimal(str(low)), Decimal(str(high))


def billed_per_run(samples: Sequence[RunSample], unit: UnitCost) -> list[Decimal]:
    """Each run's ledger cost scaled so the runs sum to the billed total."""
    if unit.unpriced_runs or unit.ledger_usd <= 0:
        raise PilotError("per-run billed costs need every run priced by the ledger")
    scale = unit.billed_usd / unit.ledger_usd
    return [s.ledger_cost_usd * scale for s in samples if s.ledger_cost_usd is not None]


def judge_unit_cost(mean_claims: Decimal) -> Decimal:
    """Per judged trial: capped claims x calls per claim x price per call."""
    claims = min(mean_claims, Decimal(JUDGED_CLAIM_CAP))
    return claims * JUDGE_CALLS_PER_CLAIM * JUDGE_COST_PER_CALL_USD


def nightly_estimate(u_a: Decimal, u_jt: Decimal) -> Decimal:
    nightly = NIGHTLY_AGENT_RUNS * u_a + NIGHTLY_JUDGED_TRIALS * u_jt
    return nightly.quantize(_CENT, rounding=ROUND_HALF_UP)


def recommend_cadence(u_a: Decimal, u_jt: Decimal, *, ceiling: Decimal) -> str:
    """Full nightly, Tier-1 nightly, or weekly only: 1.5 x the estimate vs the ceiling."""
    if ceiling <= 0:
        raise PilotError("the nightly ceiling must be positive")
    if NIGHTLY_CAP_MULTIPLIER * nightly_estimate(u_a, u_jt) <= ceiling:
        return "full_nightly"
    if NIGHTLY_CAP_MULTIPLIER * NIGHTLY_AGENT_RUNS * u_a <= ceiling:
        return "tier1_nightly_weekly_full"
    return "weekly_full_report_only"


def _same_database(first: str, second: str) -> bool:
    a, b = make_url(first), make_url(second)

    def host(url: Any) -> str:
        return "127.0.0.1" if url.host in (None, "localhost") else str(url.host)

    return (
        a.get_backend_name() == b.get_backend_name()
        and host(a) == host(b)
        and (a.port or 5432) == (b.port or 5432)
        and a.database == b.database
    )


def preflight(
    *,
    runs: int,
    concurrency: int,
    per_run_cap: Decimal,
    budget: Decimal,
    eval_database_url: str | None,
    app_database_url: str,
    research_runs: int,
    mcp_tool_path_enabled: bool,
    openrouter_enabled: bool,
) -> None:
    """Refuse a pilot that could overspend or write where an API reads."""
    if not openrouter_enabled:
        raise PreflightRefusedError("OPENROUTER_ENABLED is false")
    if not eval_database_url:
        raise PreflightRefusedError("EVAL_RUN_DATABASE_URL is not set")
    if _same_database(eval_database_url, app_database_url):
        raise PreflightRefusedError("EVAL_RUN_DATABASE_URL is the application database")
    if research_runs and not mcp_tool_path_enabled:
        raise PreflightRefusedError("research runs need MCP_TOOL_PATH_ENABLED")
    worst_case = (runs + concurrency) * per_run_cap
    if worst_case > budget:
        raise PreflightRefusedError(
            f"worst case ${worst_case} exceeds the ${budget} pilot budget"
        )


def build_report(
    samples: Sequence[RunSample],
    *,
    billed_usd: Decimal,
    gemini_calls: int,
    seed: int,
    ceiling: Decimal = NIGHTLY_CEILING_USD,
) -> dict[str, Any]:
    """The JSON-safe pilot report; raises rather than guessing."""
    unit = agent_unit_cost(samples, billed_usd)
    claims = [Decimal(min(s.claims, JUDGED_CLAIM_CAP)) for s in samples]
    u_jt = judge_unit_cost(Decimal(str(statistics.fmean(claims))))
    walls = [s.wall_seconds for s in samples if s.wall_seconds is not None]
    t_run = statistics.fmean(walls) if walls else None
    ci: tuple[Decimal, Decimal] | None = None
    if not unit.unpriced_runs and unit.ledger_usd > 0:
        ci = bootstrap_mean_ci(billed_per_run(samples, unit), seed=seed)
    nightly = {"mid": str(nightly_estimate(unit.u_a, u_jt))}
    if ci is not None:
        nightly |= {
            "low": str(nightly_estimate(ci[0], u_jt)),
            "high": str(nightly_estimate(ci[1], u_jt)),
        }
    return {
        "runs": len(samples),
        "u_a": {
            "point": str(unit.u_a),
            "ci90": None if ci is None else [str(ci[0]), str(ci[1])],
            "basis": "billed",
        },
        "billed_usd": str(unit.billed_usd),
        "ledger_usd": str(unit.ledger_usd),
        "ledger_billed_ratio": (
            None if unit.ledger_billed_ratio is None else str(unit.ledger_billed_ratio)
        ),
        "ledger_untrusted": unit.ledger_untrusted,
        "unpriced_runs": unit.unpriced_runs,
        "t_run_seconds": {
            "mean": t_run,
            "p90": (statistics.quantiles(walls, n=10)[-1] if len(walls) >= 2 else None),
        },
        "nightly_agent_seconds": (
            None if t_run is None else NIGHTLY_AGENT_RUNS * t_run / NIGHTLY_CONCURRENCY
        ),
        "runner_cap_seconds": RUNNER_CAP_SECONDS,
        "claims": {
            "mean": statistics.fmean(s.claims for s in samples),
            "max": max(s.claims for s in samples),
            "share_over_cap": sum(s.claims > JUDGED_CLAIM_CAP for s in samples)
            / len(samples),
        },
        "u_jt": str(u_jt),
        "outcomes": dict(Counter(s.outcome for s in samples)),
        "per_domain_runs": dict(Counter(s.domain for s in samples)),
        "gemini_calls": gemini_calls,
        "nightly_estimate_usd": nightly,
        "nightly_ceiling_usd": str(ceiling),
        "cadence": recommend_cadence(unit.u_a, u_jt, ceiling=ceiling),
    }
