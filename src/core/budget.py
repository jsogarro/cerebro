"""Enforcement of an ``ExecutionBudget``'s cost, token and tool ceilings.

One :class:`BudgetLedger` is created per run and travels with every task of
that run (``BUDGET_LEDGER_CONTEXT_KEY``), so retries and workflow re-entries
draw on the same budget rather than starting a fresh one.

Policy, stated once:

* Before a model call the ledger *reserves* ``prompt estimate + max_tokens``.
  A call that cannot fit is refused; it is never shrunk or moved to a cheaper
  model to make it fit.
* After the call the reservation is replaced by the provider's reported
  usage.
* Unknown usage is never counted as zero. A successful response reporting no
  tokens is charged its reservation, and because its cost cannot be bounded
  the ledger then fails closed on cost: no further call is admitted.
* Cost is only as good as the provider's own ``cost_estimate``. Before a call
  the ledger holds the larger of the provider-priced estimate for that call
  (reservation tokens at the model's rate) and the largest single-call cost
  seen so far, for every call in flight. When the provider cannot price a
  call the estimate is zero, so the first such calls can overshoot
  ``max_cost_usd``; the overshoot is recorded and ends the run.
* A failed provider response is taken to be unbilled; a call that errors or is
  cancelled with no response is unknown and charged its reservation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from src.core.contracts import ExecutionBudget

BUDGET_LEDGER_CONTEXT_KEY = "budget_ledger"

# The smallest generation cap any worker call requests; a plan whose token
# ceiling cannot hold one call per worker cannot complete.
MIN_WORKER_CALL_TOKENS = 2000

_CHARS_PER_TOKEN = 3  # deliberately below the usual ~4 so the estimate runs high


class BudgetDimension(StrEnum):
    COST = "cost"
    TOKENS = "tokens"
    TOOL_INVOCATIONS = "tool_invocations"


class BudgetExceededError(RuntimeError):
    """A ceiling would be, or was, exceeded. Terminal: never retried."""

    code = "BUDGET_EXCEEDED"

    def __init__(
        self,
        dimension: BudgetDimension,
        *,
        limit: Decimal | int,
        used: Decimal | int,
        requested: Decimal | int = 0,
        detail: str = "",
    ) -> None:
        self.dimension = dimension
        self.limit = limit
        self.used = used
        self.requested = requested
        message = (
            f"budget exceeded: {dimension.value} limit={limit} used={used} "
            f"requested={requested}"
        )
        super().__init__(f"{message} ({detail})" if detail else message)

    def as_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "dimension": self.dimension.value,
            "limit": str(self.limit),
            "used": str(self.used),
            "requested": str(self.requested),
        }


@dataclass(frozen=True)
class CallReservation:
    tokens: int
    cost: Decimal = Decimal(0)
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True)
class ReportedUsage:
    """Usage as the provider reported it. Unknown usage is ``None``, not zero."""

    total_tokens: int
    cost_usd: Decimal


def _is_well_formed(usage: ReportedUsage) -> bool:
    return (
        usage.total_tokens >= 0 and usage.cost_usd.is_finite() and usage.cost_usd >= 0
    )


def estimate_prompt_tokens(prompt: str) -> int:
    # Not telemetry's count_tokens: that returns 0 when tiktoken is missing,
    # which would reserve nothing. This always errs high.
    return len(prompt) // _CHARS_PER_TOKEN + 1


class BudgetLedger:
    """Counts spend against one budget. Methods never await, so each is atomic."""

    def __init__(self, budget: ExecutionBudget) -> None:
        self._max_cost = budget.max_cost_usd
        self._max_tokens = budget.max_total_tokens
        self._max_tools = budget.max_tool_invocations
        self.tokens_used = 0
        self.cost_used = Decimal(0)
        self.tool_invocations = 0
        self.unknown_usage_calls = 0
        self.exceeded: BudgetExceededError | None = None
        self._reserved_tokens = 0
        self._reserved_cost = Decimal(0)
        self._open: set[str] = set()
        self._max_call_cost = Decimal(0)
        self._cost_unverified = False

    def usage_payload(self) -> dict[str, Any]:
        return {
            "tokens_used": self.tokens_used,
            "cost_usd": str(self.cost_used),
            "tool_invocations": self.tool_invocations,
            "unknown_usage_calls": self.unknown_usage_calls,
        }

    @property
    def remaining_tokens(self) -> int:
        """Unspent, unreserved token headroom left in this ledger."""
        return max(0, self._max_tokens - self.tokens_used - self._reserved_tokens)

    def ensure_not_exceeded(self) -> None:
        if self.exceeded is not None:
            raise self.exceeded

    def reserve_call(
        self, estimated_tokens: int, estimated_cost: Decimal = Decimal(0)
    ) -> CallReservation:
        """Admit one model call or raise; the reservation must be settled."""
        self.ensure_not_exceeded()
        if self._cost_unverified:
            raise self._exceeded(
                BudgetDimension.COST,
                limit=self._max_cost,
                used=self.cost_used,
                detail="an earlier call reported no usage, so spend is unbounded",
            )
        held = self._reserved_tokens + estimated_tokens
        if self.tokens_used + held > self._max_tokens:
            raise self._exceeded(
                BudgetDimension.TOKENS,
                limit=self._max_tokens,
                used=self.tokens_used,
                requested=held,
            )
        call_cost = max(estimated_cost, self._max_call_cost)
        held_cost = self._reserved_cost + call_cost
        if self.cost_used + held_cost > self._max_cost:
            raise self._exceeded(
                BudgetDimension.COST,
                limit=self._max_cost,
                used=self.cost_used,
                requested=held_cost,
            )
        self._reserved_tokens += estimated_tokens
        self._reserved_cost += call_cost
        reservation = CallReservation(tokens=estimated_tokens, cost=call_cost)
        self._open.add(reservation.call_id)
        return reservation

    def release(self, reservation: CallReservation) -> None:
        """Give back a reservation whose call provably never left the process."""
        if reservation.call_id not in self._open:
            return
        self._open.discard(reservation.call_id)
        self._reserved_tokens -= reservation.tokens
        self._reserved_cost -= reservation.cost

    def settle(self, reservation: CallReservation, usage: ReportedUsage | None) -> None:
        """Replace a reservation with what the call actually consumed, once.

        A reservation already settled or released is ignored, so a replayed
        settlement can neither subtract the held totals twice nor charge twice.
        """
        if reservation.call_id not in self._open:
            return
        self._open.discard(reservation.call_id)
        self._reserved_tokens -= reservation.tokens
        self._reserved_cost -= reservation.cost
        if usage is not None and not _is_well_formed(usage):
            usage = None
        if usage is None:
            self.tokens_used += reservation.tokens
            self.unknown_usage_calls += 1
            # ponytail: one unknown-usage call (including a timeout or
            # cancellation) stops all later calls; a per-call price bound
            # would let the run continue.
            self._cost_unverified = True
        else:
            self.tokens_used += usage.total_tokens
            self.cost_used += usage.cost_usd
            self._max_call_cost = max(self._max_call_cost, usage.cost_usd)
        if self.tokens_used > self._max_tokens:
            self._exceeded(
                BudgetDimension.TOKENS,
                limit=self._max_tokens,
                used=self.tokens_used,
            )
        elif self.cost_used > self._max_cost:
            self._exceeded(
                BudgetDimension.COST,
                limit=self._max_cost,
                used=self.cost_used,
            )

    def charge_tool_invocation(self) -> None:
        """Count one tool invocation, or raise if it would exceed the ceiling."""
        self.ensure_not_exceeded()
        if self.tool_invocations + 1 > self._max_tools:
            raise self._exceeded(
                BudgetDimension.TOOL_INVOCATIONS,
                limit=self._max_tools,
                used=self.tool_invocations,
                requested=1,
            )
        self.tool_invocations += 1

    def _exceeded(
        self,
        dimension: BudgetDimension,
        *,
        limit: Decimal | int,
        used: Decimal | int,
        requested: Decimal | int = 0,
        detail: str = "",
    ) -> BudgetExceededError:
        """Record the first ceiling crossed and return it for the caller to raise."""
        error = BudgetExceededError(
            dimension, limit=limit, used=used, requested=requested, detail=detail
        )
        if self.exceeded is None:
            self.exceeded = error
        return error


def check_plan_fits(budget: ExecutionBudget, worker_count: int) -> None:
    """Refuse, before dispatch, a plan whose token ceiling cannot hold its workers."""
    required = worker_count * MIN_WORKER_CALL_TOKENS
    if budget.max_total_tokens < required:
        raise BudgetExceededError(
            BudgetDimension.TOKENS,
            limit=budget.max_total_tokens,
            used=0,
            requested=required,
            detail=f"{worker_count} workers need one call each",
        )
