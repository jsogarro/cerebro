"""One provider span per real attempt, metadata only, and never a runtime dependency."""

from __future__ import annotations

import asyncio
import contextlib
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from src.core.budget import BudgetLedger
from src.core.contracts import ExecutionBudget
from src.services.model_invocation import (
    CallPrice,
    ModelInvocationContext,
    UnbilledTransportError,
    invoke_model,
)

PRICE = CallPrice(cost_usd=Decimal("0.01"), digest="d" * 64)
SECRET = "sk-SENTINEL-do-not-export"


class _Span:
    def __init__(self, kwargs: dict[str, Any], boom: bool) -> None:
        self.kwargs = kwargs
        self.updates: list[dict[str, Any]] = []
        self.ended = 0
        self.boom = boom

    def update(self, **kwargs: Any) -> None:
        if self.boom:
            raise RuntimeError("backend down")
        self.updates.append(kwargs)

    def end(self) -> None:
        if self.boom:
            raise RuntimeError("backend down")
        self.ended += 1


class _Client:
    def __init__(self, *, boom: bool = False, start_boom: bool = False) -> None:
        self.spans: list[_Span] = []
        self.boom, self.start_boom = boom, start_boom

    def start_observation(self, **kwargs: Any) -> _Span:
        if self.start_boom:
            raise RuntimeError("backend down")
        span = _Span(kwargs, self.boom)
        self.spans.append(span)
        return span


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> _Client:
    fake = _Client()
    monkeypatch.setattr("src.core.tracing.get_langfuse_client", lambda: fake)
    return fake


def _ledger() -> BudgetLedger:
    return BudgetLedger(
        ExecutionBudget(
            max_cost_usd=Decimal("1"),
            max_total_tokens=100_000,
            max_tool_invocations=5,
            max_parallel_tasks=2,
            max_attempts_per_task=1,
            task_timeout_seconds=30,
        )
    )


def _response(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "success": True,
        "content": f"answer {SECRET}",
        "finish_reason": "completed",
        "total_tokens": 100,
        "prompt_tokens": 60,
        "completion_tokens": 40,
        "cost_estimate": 0.002,
        "provider": "gemini",
        "model_name": "m",
        "request_id": "r",
        "error_message": f"failed with {SECRET}",
    }
    return SimpleNamespace(**{**fields, **overrides})


def _call(*outcomes: Any):
    queue = list(outcomes)
    calls = []

    async def call(timeout: float | None) -> Any:
        calls.append(timeout)
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    call.calls = calls  # type: ignore[attr-defined]
    return call


async def _run(ctx: ModelInvocationContext, call: Any) -> Any:
    return await invoke_model(ctx, call, estimated_tokens=500, price=PRICE)


async def test_one_span_per_attempt_under_the_trace_parent_with_usage(
    client: _Client,
) -> None:
    ctx = ModelInvocationContext(ledger=_ledger(), trace_parent="a" * 32)
    await _run(ctx, _call(_response()))
    (span,) = client.spans
    assert span.kwargs["trace_context"] == {"trace_id": "a" * 32}
    assert span.ended == 1
    merged = {k: v for update in span.updates for k, v in update.items()}
    assert merged["usage_details"]["total"] == 100
    assert merged["model"] == "m"


async def test_a_retried_call_emits_one_span_per_real_attempt(client: _Client) -> None:
    ctx = ModelInvocationContext(
        ledger=_ledger(), trace_parent="a" * 32, max_attempts=2
    )
    await _run(ctx, _call(UnbilledTransportError("down"), _response()))
    assert len(client.spans) == 2 and all(s.ended == 1 for s in client.spans)


async def test_an_attempt_that_never_reached_the_provider_has_no_span(
    client: _Client,
) -> None:
    ctx = ModelInvocationContext(
        ledger=_ledger(), trace_parent="a" * 32, remaining_seconds=lambda: 0.0
    )
    with pytest.raises(Exception, match="deadline"):
        await _run(ctx, _call(_response()))
    assert client.spans == []


async def test_no_trace_parent_means_no_span(client: _Client) -> None:
    await _run(ModelInvocationContext(ledger=_ledger()), _call(_response()))
    assert client.spans == []


@pytest.mark.parametrize(
    "outcome",
    [_response(), _response(success=False), RuntimeError(f"boom {SECRET}")],
)
async def test_no_response_text_or_secret_is_exported(
    client: _Client, outcome: Any
) -> None:
    ctx = ModelInvocationContext(ledger=_ledger(), trace_parent="a" * 32)
    with contextlib.suppress(RuntimeError):
        await _run(ctx, _call(outcome))
    assert client.spans and SECRET not in repr(
        [(s.kwargs, s.updates) for s in client.spans]
    )


async def test_a_cancelled_attempt_ends_its_span_and_still_settles(
    client: _Client,
) -> None:
    ledger = _ledger()
    ctx = ModelInvocationContext(ledger=ledger, trace_parent="a" * 32)
    with pytest.raises(asyncio.CancelledError):
        await _run(ctx, _call(asyncio.CancelledError()))
    assert client.spans[0].ended == 1 and ledger.unknown_usage_calls == 1


@pytest.mark.parametrize("mode", ["start", "update_end"])
async def test_a_failing_backend_changes_neither_result_nor_accounting(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    broken = _Client(boom=mode == "update_end", start_boom=mode == "start")
    monkeypatch.setattr("src.core.tracing.get_langfuse_client", lambda: broken)
    ledger = _ledger()
    ctx = ModelInvocationContext(ledger=ledger, trace_parent="a" * 32)
    result = await _run(ctx, _call(_response()))
    assert result.succeeded and ledger.tokens_used == 100
    with pytest.raises(RuntimeError, match="provider"):
        await _run(ctx, _call(RuntimeError("provider")))
    assert ledger.unknown_usage_calls == 1


async def test_a_missing_backend_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.core.tracing.get_langfuse_client", lambda: None)
    ctx = ModelInvocationContext(ledger=_ledger(), trace_parent="a" * 32)
    assert (await _run(ctx, _call(_response()))).succeeded


async def test_an_unusable_response_after_dispatch_settles_unknown_once_and_ends_the_span(
    client: _Client,
) -> None:
    from tests.test_provider_invocation_contract import _Journal

    journal, ledger = _Journal(), _ledger()
    ctx = ModelInvocationContext(ledger=ledger, journal=journal, trace_parent="a" * 32)
    with pytest.raises(ArithmeticError):
        await _run(ctx, _call(_response(cost_estimate="n/a")))
    assert journal.names == ["reserve", "mark", "settle_unknown"]
    assert ledger.unknown_usage_calls == 1
    assert client.spans[0].ended == 1
    with pytest.raises(Exception, match="budget|ceiling|cost"):
        await _run(ctx, _call(_response()))


async def test_a_missing_token_class_is_omitted_from_the_span_usage(
    client: _Client,
) -> None:
    ctx = ModelInvocationContext(ledger=_ledger(), trace_parent="a" * 32)
    await _run(ctx, _call(_response(prompt_tokens=None)))
    usage = {
        k: v
        for u in client.spans[0].updates
        for k, v in u.get("usage_details", {}).items()
    }
    assert usage == {"output": 40, "total": 100}
