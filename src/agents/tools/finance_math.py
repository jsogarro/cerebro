"""Deterministic finance/math tool.

Pure functions with validated, structured (Pydantic) results — used so the
finance and analytics agents can report *exact* numbers instead of relying on
the language model's arithmetic. No external data, no LLM calls.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field


class DCFResult(BaseModel):
    method: str = "gordon_growth"
    present_value: float
    inputs: dict[str, float]


class NPVResult(BaseModel):
    npv: float
    discount_rate: float
    cashflows: list[float]


class RatioResult(BaseModel):
    ratios: dict[str, float]
    notes: list[str] = Field(default_factory=list)


class AmortizationResult(BaseModel):
    monthly_payment: float
    total_paid: float
    total_interest: float
    months: int


class StatsResult(BaseModel):
    count: int
    mean: float
    median: float
    stdev: float
    minimum: float
    maximum: float


def dcf_gordon_growth(
    fcf_next: float, growth_rate: float, discount_rate: float
) -> DCFResult:
    """Present value of a perpetually growing cash flow (Gordon growth model).

    PV = FCF_next / (discount_rate - growth_rate). Requires discount > growth.
    """
    if discount_rate <= growth_rate:
        raise ValueError("discount_rate must be greater than growth_rate")
    pv = fcf_next / (discount_rate - growth_rate)
    return DCFResult(
        present_value=round(pv, 4),
        inputs={
            "fcf_next": fcf_next,
            "growth_rate": growth_rate,
            "discount_rate": discount_rate,
        },
    )


def npv(discount_rate: float, cashflows: list[float]) -> NPVResult:
    """Net present value. cashflows[0] is t=0 (typically the initial outlay)."""
    if discount_rate <= -1:
        raise ValueError("discount_rate must be greater than -1")
    total = sum(cf / (1 + discount_rate) ** t for t, cf in enumerate(cashflows))
    return NPVResult(
        npv=round(total, 4), discount_rate=discount_rate, cashflows=list(cashflows)
    )


def financial_ratios(values: dict[str, float]) -> RatioResult:
    """Compute the standard ratios for whichever inputs are provided.

    Recognized inputs: current_assets, current_liabilities, inventory, cash,
    total_debt, total_equity, total_assets, revenue, gross_profit, net_income,
    ebit, interest_expense.
    """
    v = values
    ratios: dict[str, float] = {}
    notes: list[str] = []

    def has(*keys: str) -> bool:
        return all(k in v and v[k] is not None for k in keys)

    def safe(numer: float, denom: float, name: str) -> None:
        if denom == 0:
            notes.append(f"{name} skipped: division by zero")
        else:
            ratios[name] = round(numer / denom, 4)

    if has("current_assets", "current_liabilities"):
        safe(v["current_assets"], v["current_liabilities"], "current_ratio")
    if has("current_assets", "inventory", "current_liabilities"):
        safe(
            v["current_assets"] - v["inventory"],
            v["current_liabilities"],
            "quick_ratio",
        )
    if has("total_debt", "total_equity"):
        safe(v["total_debt"], v["total_equity"], "debt_to_equity")
    if has("gross_profit", "revenue"):
        safe(v["gross_profit"], v["revenue"], "gross_margin")
    if has("net_income", "revenue"):
        safe(v["net_income"], v["revenue"], "net_margin")
    if has("net_income", "total_equity"):
        safe(v["net_income"], v["total_equity"], "return_on_equity")
    if has("net_income", "total_assets"):
        safe(v["net_income"], v["total_assets"], "return_on_assets")
    if has("ebit", "interest_expense"):
        safe(v["ebit"], v["interest_expense"], "interest_coverage")

    if not ratios and not notes:
        notes.append("no recognized ratio inputs were provided")
    return RatioResult(ratios=ratios, notes=notes)


def compound_interest(
    principal: float,
    annual_rate: float,
    years: float,
    compounds_per_year: int = 1,
) -> float:
    """Future value with periodic compounding."""
    if compounds_per_year <= 0:
        raise ValueError("compounds_per_year must be positive")
    n = compounds_per_year
    fv: float = principal * (1 + annual_rate / n) ** (n * years)
    return round(fv, 4)


def loan_amortization(
    principal: float, annual_rate: float, months: int
) -> AmortizationResult:
    """Fixed-rate loan monthly payment and total interest."""
    if months <= 0:
        raise ValueError("months must be positive")
    r = annual_rate / 12
    if r == 0:
        payment = principal / months
    else:
        payment = principal * r / (1 - (1 + r) ** -months)
    total_paid = payment * months
    return AmortizationResult(
        monthly_payment=round(payment, 4),
        total_paid=round(total_paid, 4),
        total_interest=round(total_paid - principal, 4),
        months=months,
    )


def descriptive_stats(values: list[float]) -> StatsResult:
    """Basic descriptive statistics for a list of numbers."""
    if not values:
        raise ValueError("values must be non-empty")
    return StatsResult(
        count=len(values),
        mean=round(statistics.fmean(values), 4),
        median=round(statistics.median(values), 4),
        stdev=round(statistics.pstdev(values), 4),
        minimum=min(values),
        maximum=max(values),
    )


# Operation dispatch for the calculator agent -----------------------------------

_OPERATIONS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "dcf": lambda p: dcf_gordon_growth(
        p["fcf_next"], p["growth_rate"], p["discount_rate"]
    ),
    "npv": lambda p: npv(p["discount_rate"], p["cashflows"]),
    "ratios": lambda p: financial_ratios(p["values"]),
    "compound_interest": lambda p: {
        "future_value": compound_interest(
            p["principal"],
            p["annual_rate"],
            p["years"],
            int(p.get("compounds_per_year", 1)),
        )
    },
    "amortization": lambda p: loan_amortization(
        p["principal"], p["annual_rate"], int(p["months"])
    ),
    "stats": lambda p: descriptive_stats(p["values"]),
}


def calculate(operation: str, params: dict[str, Any]) -> Any:
    """Run a named operation. Raises ValueError for unknown ops / bad params."""
    op = _OPERATIONS.get(operation)
    if op is None:
        raise ValueError(
            f"Unknown operation '{operation}'. Available: {sorted(_OPERATIONS.keys())}"
        )
    try:
        return op(params)
    except KeyError as exc:
        raise ValueError(
            f"Missing required parameter for '{operation}': {exc}"
        ) from exc


AVAILABLE_OPERATIONS = sorted(_OPERATIONS.keys())

__all__ = [
    "AVAILABLE_OPERATIONS",
    "AmortizationResult",
    "DCFResult",
    "NPVResult",
    "RatioResult",
    "StatsResult",
    "calculate",
    "compound_interest",
    "dcf_gordon_growth",
    "descriptive_stats",
    "financial_ratios",
    "loan_amortization",
    "npv",
]
