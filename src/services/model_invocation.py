"""The one wrapper every governed external model call passes through.

Reserve, check guard and deadline, commit the dispatch marker, invoke, normalize,
settle: in that order, once per attempt. The reservation lives here and nowhere
else (not in the service, not in the provider), so a call cannot be counted
twice or not at all.

An attempt that may have reached the provider is never assumed free: an error,
a cancellation or a response without usage settles as unknown and keeps its
reservation as an uncertain bound. Only a call that provably never left, or one
the provider documents as unbilled, costs nothing.

There is exactly one attempt unless the caller raises ``max_attempts``. A retry
is taken only after :class:`UnbilledTransportError`, and each attempt gets its
own reservation and journal row.
"""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy.exc import SQLAlchemyError
from structlog import get_logger

from src.core.budget import (
    BudgetDimension,
    BudgetExceededError,
    BudgetLedger,
    ReportedUsage,
)
from src.core.tracing import end_model_attempt, start_model_attempt

logger = get_logger()

MODEL_INVOCATION_CONTEXT_KEY = "model_invocation_context"
"""Task-context key under which a run's owner supplies its ``ModelInvocationContext``."""


class CallStatus(StrEnum):
    COMPLETE = "complete"
    TRUNCATED = "truncated"
    FILTERED = "filtered"
    EMPTY = "empty"
    FAILED = "failed"


class UsageCertainty(StrEnum):
    REPORTED = "reported"
    UNKNOWN = "unknown"


class UnbilledTransportError(RuntimeError):
    """The request failed before reaching the provider, which documents no charge."""


class DeadlineExceededError(RuntimeError):
    """No time is left to start another external call."""


_COMPLETE = {"completed", "complete", "stop", "end_turn", "STOP"}
_TRUNCATED = {"length", "max_tokens", "MAX_TOKENS"}
_FILTERED = {
    "content_filter",
    "filtered",
    "SAFETY",
    "RECITATION",
    "BLOCKLIST",
    "PROHIBITED_CONTENT",
    "SPII",
}


@dataclass(frozen=True)
class CallPrice:
    """The reservation's price at the pinned catalog rate, with its provenance.

    ``fixture`` marks an explicitly configured zero-price route (a local or test
    model); an unpriced route is ``None`` and is refused, never read as free.
    """

    cost_usd: Decimal
    digest: str
    fixture: bool = False


@dataclass(frozen=True)
class ModelCallResult:
    status: CallStatus
    content: str | None
    finish_reason: str | None
    provider: str | None
    model: str | None
    request_id: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost_usd: Decimal | None
    usage_certainty: UsageCertainty
    attempts: int
    error: str | None = None
    # The provider's own response, for callers not yet moved to this type.
    response: Any = field(default=None, compare=False, repr=False)

    @property
    def succeeded(self) -> bool:
        return self.status is CallStatus.COMPLETE


class CallJournal(Protocol):
    """The slice of ``ModelCallReservationRepository`` the wrapper drives."""

    async def reserve(
        self,
        call_id: str,
        *,
        tokens: int,
        cost_usd: Decimal,
        price_digest: str,
        fencing_token: int | None = None,
        attempt_ref: str | None = None,
    ) -> None: ...

    async def mark_may_have_sent(
        self, call_id: str, *, fencing_token: int | None = None
    ) -> None: ...

    async def settle_known(
        self,
        call_id: str,
        *,
        tokens: int,
        cost_usd: Decimal,
        fencing_token: int | None = None,
    ) -> bool: ...

    async def settle_unknown(
        self, call_id: str, *, fencing_token: int | None = None
    ) -> bool: ...

    async def release_unsent(
        self, call_id: str, *, fencing_token: int | None = None
    ) -> bool: ...


def _no_guard() -> None:
    return None


def _no_deadline() -> float | None:
    return None


@dataclass(frozen=True)
class ModelInvocationContext:
    """What a governed call needs from its owner: budget, lifetime and identity."""

    ledger: BudgetLedger | None = None
    journal: CallJournal | None = None
    # Seconds left before the owner's deadline, or None when it has none.
    remaining_seconds: Callable[[], float | None] = _no_deadline
    # Raises when the owner was cancelled or lost its lease.
    guard: Callable[[], None] = _no_guard
    fencing_token: int | None = None
    attempt_ref: str | None = None
    max_attempts: int = 1
    trace_parent: str | None = None

    @classmethod
    def for_ledger(cls, ledger: BudgetLedger) -> "ModelInvocationContext":
        """A run's ledger alone, with the configured attempt cap."""
        from src.core.config import settings

        return cls(ledger=ledger, max_attempts=settings.MODEL_ROUTER_MAX_RETRIES)


def price_digest(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def normalize_response(response: Any, *, attempts: int = 1) -> ModelCallResult:
    """Turn a provider response into a result; only a complete, non-empty one succeeds."""
    content = getattr(response, "content", None)
    raw_finish = getattr(response, "finish_reason", None)
    # A provider that reports no reason is taken at its word that it finished.
    finish = raw_finish if isinstance(raw_finish, str) else None
    total = getattr(response, "total_tokens", None)
    reported = (
        isinstance(total, int)
        and not isinstance(total, bool)
        and total > 0
        and not getattr(response, "usage_estimated", False)
    )
    error = getattr(response, "error_message", None)
    if not getattr(response, "success", False):
        status = CallStatus.FAILED
    elif finish in _TRUNCATED:
        status, error = CallStatus.TRUNCATED, f"finish reason {finish!r}"
    elif finish in _FILTERED:
        status, error = CallStatus.FILTERED, f"finish reason {finish!r}"
    elif finish is not None and finish not in _COMPLETE:
        status, error = CallStatus.FAILED, f"unrecognized finish reason {finish!r}"
    elif not isinstance(content, str) or not content.strip():
        status, error = CallStatus.EMPTY, "response had no content"
    else:
        status = CallStatus.COMPLETE
    cost = getattr(response, "cost_estimate", None)
    return ModelCallResult(
        status=status,
        content=content if isinstance(content, str) else None,
        finish_reason=finish,
        provider=getattr(response, "provider", None) or None,
        model=getattr(response, "model_name", None) or None,
        request_id=getattr(response, "request_id", None),
        prompt_tokens=getattr(response, "prompt_tokens", None) if reported else None,
        completion_tokens=(
            getattr(response, "completion_tokens", None) if reported else None
        ),
        total_tokens=total if reported else None,
        cost_usd=Decimal(str(cost)) if reported and cost is not None else None,
        usage_certainty=UsageCertainty.REPORTED if reported else UsageCertainty.UNKNOWN,
        attempts=attempts,
        error=error,
        response=response,
    )


def _usage(result: ModelCallResult) -> ReportedUsage | None:
    if result.usage_certainty is UsageCertainty.UNKNOWN:
        return None
    assert result.total_tokens is not None
    if result.cost_usd is None:
        # Tokens without a cost are not a complete measurement.
        return None
    return ReportedUsage(total_tokens=result.total_tokens, cost_usd=result.cost_usd)


def _unpriced() -> BudgetExceededError:
    return BudgetExceededError(
        BudgetDimension.COST,
        limit=0,
        used=0,
        detail="route has no pinned price, so its spend cannot be bounded",
    )


async def invoke_model(
    ctx: ModelInvocationContext,
    call: Callable[[float | None], Awaitable[Any]],
    *,
    estimated_tokens: int,
    price: CallPrice | None,
) -> ModelCallResult:
    """Run one governed model call; ``call`` receives the seconds left, if bounded."""
    if (ctx.ledger is not None or ctx.journal is not None) and price is None:
        raise _unpriced()
    attempts = 0
    while True:
        attempts += 1
        try:
            return await _attempt(
                ctx, call, estimated_tokens=estimated_tokens, price=price, n=attempts
            )
        except UnbilledTransportError:
            if attempts >= ctx.max_attempts:
                raise


async def _attempt(
    ctx: ModelInvocationContext,
    call: Callable[[float | None], Awaitable[Any]],
    *,
    estimated_tokens: int,
    price: CallPrice | None,
    n: int,
) -> ModelCallResult:
    ledger, journal = ctx.ledger, ctx.journal
    remaining = _check_live(ctx)
    reservation = None
    call_id = uuid.uuid4().hex
    if ledger is not None:
        assert price is not None
        reservation = ledger.reserve_call(estimated_tokens, price.cost_usd)
    marked = False
    try:
        if journal is not None:
            assert price is not None
            await journal.reserve(
                call_id,
                tokens=estimated_tokens,
                cost_usd=price.cost_usd,
                price_digest=price.digest,
                fencing_token=ctx.fencing_token,
                attempt_ref=ctx.attempt_ref,
            )
            try:
                remaining = _check_live(ctx)
                await journal.mark_may_have_sent(
                    call_id, fencing_token=ctx.fencing_token
                )
                marked = True
            except BaseException:
                await _best_effort(
                    journal.release_unsent(call_id, fencing_token=ctx.fencing_token)
                )
                raise
    except BaseException:
        if ledger is not None and reservation is not None:
            ledger.release(reservation)
        raise

    span = start_model_attempt(
        ctx.trace_parent,
        {"call_id": call_id, "attempt": n, "estimated_tokens": estimated_tokens},
    )
    try:
        response = await call(remaining)
    except UnbilledTransportError as exc:
        end_model_attempt(span, metadata={"outcome": type(exc).__name__})
        await asyncio.shield(
            _settle(ctx, call_id, reservation, ReportedUsage(0, Decimal(0)), marked)
        )
        raise
    except BaseException as exc:
        end_model_attempt(span, metadata={"outcome": type(exc).__name__})
        await asyncio.shield(_settle(ctx, call_id, reservation, None, marked))
        raise
    try:
        result = normalize_response(response, attempts=n)
        usage = _usage(result)
    except Exception as exc:
        # The provider answered, so the call may have been billed: a response
        # we cannot read is unknown spend, never free.
        end_model_attempt(span, metadata={"outcome": type(exc).__name__})
        await asyncio.shield(_settle(ctx, call_id, reservation, None, marked))
        raise
    end_model_attempt(span, **_span_update(result))
    await asyncio.shield(_settle(ctx, call_id, reservation, usage, marked))
    return result


def _span_update(result: ModelCallResult) -> dict[str, Any]:
    """Metadata and usage only: never content, request text or provider error prose."""
    update: dict[str, Any] = {
        "model": result.model,
        "metadata": {
            "outcome": result.status.value,
            "provider": result.provider,
            "finish_reason": result.finish_reason,
            "usage_certainty": result.usage_certainty.value,
            "request_id": result.request_id,
        },
    }
    if result.usage_certainty is UsageCertainty.REPORTED:
        counts = {
            "input": result.prompt_tokens,
            "output": result.completion_tokens,
            "total": result.total_tokens,
        }
        update["usage_details"] = {k: v for k, v in counts.items() if v is not None}
        if result.cost_usd is not None:
            update["cost_details"] = {"total": float(result.cost_usd)}
    return update


def _check_live(ctx: ModelInvocationContext) -> float | None:
    """Raise if the owner is cancelled or out of time; else return the time left."""
    ctx.guard()
    remaining = ctx.remaining_seconds()
    if remaining is not None and remaining <= 0:
        raise DeadlineExceededError("the owner's deadline has passed")
    return remaining


async def _settle(
    ctx: ModelInvocationContext,
    call_id: str,
    reservation: Any,
    usage: ReportedUsage | None,
    marked: bool,
) -> None:
    # Ledger first: it is in-process and cannot fail, so the run's budget is
    # right even if the journal write that follows is lost.
    if ctx.ledger is not None and reservation is not None:
        ctx.ledger.settle(reservation, usage)
    if ctx.journal is None or not marked:
        return
    if usage is None:
        await _best_effort(
            ctx.journal.settle_unknown(call_id, fencing_token=ctx.fencing_token)
        )
    else:
        await _best_effort(
            ctx.journal.settle_known(
                call_id,
                tokens=usage.total_tokens,
                cost_usd=usage.cost_usd,
                fencing_token=ctx.fencing_token,
            )
        )


async def _best_effort(write: Awaitable[Any]) -> None:
    # The provider has already been paid by now, so losing a journal write must
    # not discard its response; the row stays open and reconciliation settles it
    # conservatively as unknown.
    try:
        await write
    except (SQLAlchemyError, OSError):
        logger.exception("model_call_journal_write_failed")


__all__ = [
    "MODEL_INVOCATION_CONTEXT_KEY",
    "CallJournal",
    "CallPrice",
    "CallStatus",
    "DeadlineExceededError",
    "ModelCallResult",
    "ModelInvocationContext",
    "UnbilledTransportError",
    "UsageCertainty",
    "invoke_model",
    "normalize_response",
    "price_digest",
]
