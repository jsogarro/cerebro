"""One wrapper reserves, guards, dispatches, normalizes and settles every model call."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from src.core.budget import BudgetDimension, BudgetExceededError, BudgetLedger
from src.core.contracts import ExecutionBudget
from src.services.model_invocation import (
    CallPrice,
    CallStatus,
    DeadlineExceededError,
    ModelInvocationContext,
    UnbilledTransportError,
    UsageCertainty,
    invoke_model,
    normalize_response,
)

PRICE = CallPrice(cost_usd=Decimal("0.01"), digest="d" * 64)


def _ledger(tokens: int = 100_000, cost: str = "1") -> BudgetLedger:
    return BudgetLedger(
        ExecutionBudget(
            max_cost_usd=Decimal(cost),
            max_total_tokens=tokens,
            max_tool_invocations=5,
            max_parallel_tasks=2,
            max_attempts_per_task=1,
            task_timeout_seconds=30,
        )
    )


def _response(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "success": True,
        "content": "answer",
        "finish_reason": "completed",
        "total_tokens": 100,
        "prompt_tokens": 60,
        "completion_tokens": 40,
        "cost_estimate": 0.002,
        "provider": "gemini",
        "model_name": "m",
        "request_id": "r",
        "error_message": None,
    }
    return SimpleNamespace(**{**fields, **overrides})


class _Journal:
    def __init__(
        self, *, fail_on: str | None = None, error: type[Exception] = OperationalError
    ) -> None:
        self.events: list[tuple[str, Any]] = []
        self.fail_on = fail_on
        self.error = error

    async def reserve(self, call_id: str, **kwargs: Any) -> None:
        self._record("reserve", call_id, kwargs)

    async def mark_may_have_sent(self, call_id: str, **kwargs: Any) -> None:
        self._record("mark", call_id, kwargs)

    async def settle_known(self, call_id: str, **kwargs: Any) -> bool:
        self._record("settle_known", call_id, kwargs)
        return True

    async def settle_unknown(self, call_id: str, **kwargs: Any) -> bool:
        self._record("settle_unknown", call_id, kwargs)
        return True

    async def release_unsent(self, call_id: str, **kwargs: Any) -> bool:
        self._record("release_unsent", call_id, kwargs)
        return True

    def _record(self, name: str, call_id: str, kwargs: dict[str, Any]) -> None:
        if self.fail_on == name:
            if self.error is OperationalError:
                raise OperationalError(name, {}, Exception(f"{name} unavailable"))
            raise self.error(f"{name} unavailable")
        self.events.append((name, call_id))

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.events]


class _Provider:
    def __init__(self, *outcomes: Any, journal: _Journal | None = None) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0
        self.timeouts: list[float | None] = []
        self.journal = journal
        self.journal_at_dispatch: list[str] = []

    async def __call__(self, timeout: float | None) -> Any:
        self.calls += 1
        self.timeouts.append(timeout)
        if self.journal is not None:
            self.journal_at_dispatch = list(self.journal.names)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


async def _invoke(
    ctx: ModelInvocationContext, provider: _Provider, *, price: CallPrice | None = PRICE
) -> Any:
    return await invoke_model(ctx, provider, estimated_tokens=500, price=price)


class TestOrderingAndSettlement:
    async def test_marker_is_committed_before_the_provider_is_contacted(self) -> None:
        journal = _Journal()
        provider = _Provider(_response(), journal=journal)
        ctx = ModelInvocationContext(ledger=_ledger(), journal=journal)
        result = await _invoke(ctx, provider)
        assert provider.journal_at_dispatch == ["reserve", "mark"]
        assert journal.names == ["reserve", "mark", "settle_known"]
        assert result.succeeded and result.total_tokens == 100
        assert result.cost_usd == Decimal("0.002")

    async def test_reported_usage_replaces_the_reservation_in_the_ledger(self) -> None:
        ledger = _ledger()
        await _invoke(ModelInvocationContext(ledger=ledger), _Provider(_response()))
        assert ledger.tokens_used == 100 and ledger.unknown_usage_calls == 0

    async def test_provider_error_is_unknown_spend_and_stops_later_calls(self) -> None:
        journal, ledger = _Journal(), _ledger()
        ctx = ModelInvocationContext(ledger=ledger, journal=journal)
        with pytest.raises(RuntimeError, match="boom"):
            await _invoke(ctx, _Provider(RuntimeError("boom")))
        assert journal.names == ["reserve", "mark", "settle_unknown"]
        assert ledger.tokens_used == 500 and ledger.unknown_usage_calls == 1
        with pytest.raises(BudgetExceededError):
            await _invoke(ctx, _Provider(_response()))

    async def test_cancellation_after_dispatch_settles_unknown_once(self) -> None:
        journal, ledger = _Journal(), _ledger()
        ctx = ModelInvocationContext(ledger=ledger, journal=journal)
        with pytest.raises(asyncio.CancelledError):
            await _invoke(ctx, _Provider(asyncio.CancelledError()))
        assert journal.names.count("settle_unknown") == 1
        assert ledger.unknown_usage_calls == 1

    async def test_failed_response_with_reported_usage_is_charged_not_free(
        self,
    ) -> None:
        ledger = _ledger()
        result = await _invoke(
            ModelInvocationContext(ledger=ledger),
            _Provider(_response(success=False, error_message="bad", total_tokens=80)),
        )
        assert result.status is CallStatus.FAILED
        assert ledger.tokens_used == 80 and ledger.unknown_usage_calls == 0

    async def test_failed_response_without_usage_is_unknown_not_free(self) -> None:
        ledger = _ledger()
        result = await _invoke(
            ModelInvocationContext(ledger=ledger),
            _Provider(_response(success=False, total_tokens=0)),
        )
        assert result.usage_certainty is UsageCertainty.UNKNOWN
        assert result.cost_usd is None and result.total_tokens is None
        assert ledger.tokens_used == 500 and ledger.unknown_usage_calls == 1

    async def test_a_journal_write_lost_after_the_call_keeps_the_response(self) -> None:
        journal = _Journal(fail_on="settle_known")
        result = await _invoke(
            ModelInvocationContext(ledger=_ledger(), journal=journal),
            _Provider(_response()),
        )
        assert result.succeeded

    async def test_a_bug_in_the_journal_after_the_call_is_not_swallowed(self) -> None:
        journal = _Journal(fail_on="settle_known", error=TypeError)
        with pytest.raises(TypeError, match="settle_known"):
            await _invoke(
                ModelInvocationContext(ledger=_ledger(), journal=journal),
                _Provider(_response()),
            )


class TestNothingSentWhenAdmissionFails:
    async def test_reservation_that_cannot_commit_sends_nothing_and_frees_the_hold(
        self,
    ) -> None:
        ledger = _ledger(tokens=600)
        provider = _Provider(_response())
        ctx = ModelInvocationContext(ledger=ledger, journal=_Journal(fail_on="reserve"))
        with pytest.raises(OperationalError, match="reserve"):
            await _invoke(ctx, provider)
        assert provider.calls == 0
        # 500 held would leave no room for this if the hold had leaked.
        await _invoke(ModelInvocationContext(ledger=ledger), _Provider(_response()))

    async def test_marker_that_cannot_commit_sends_nothing_and_releases_unsent(
        self,
    ) -> None:
        journal = _Journal(fail_on="mark")
        provider = _Provider(_response())
        ctx = ModelInvocationContext(ledger=_ledger(), journal=journal)
        with pytest.raises(OperationalError, match="mark"):
            await _invoke(ctx, provider)
        assert provider.calls == 0
        assert journal.names == ["reserve", "release_unsent"]

    async def test_a_cancelled_owner_reserves_and_sends_nothing(self) -> None:
        def guard() -> None:
            raise asyncio.CancelledError

        journal, provider = _Journal(), _Provider(_response())
        ctx = ModelInvocationContext(ledger=_ledger(), journal=journal, guard=guard)
        with pytest.raises(asyncio.CancelledError):
            await _invoke(ctx, provider)
        assert provider.calls == 0 and journal.events == []

    async def test_owner_lost_between_reserve_and_marker_sends_nothing(self) -> None:
        checks = iter([None, RuntimeError("lease lost")])

        def guard() -> None:
            outcome = next(checks)
            if outcome is not None:
                raise outcome

        journal, provider = _Journal(), _Provider(_response())
        ctx = ModelInvocationContext(ledger=_ledger(), journal=journal, guard=guard)
        with pytest.raises(RuntimeError, match="lease lost"):
            await _invoke(ctx, provider)
        assert provider.calls == 0
        assert journal.names == ["reserve", "release_unsent"]


class TestDeadline:
    async def test_the_remaining_deadline_reaches_the_provider_call(self) -> None:
        provider = _Provider(_response())
        ctx = ModelInvocationContext(remaining_seconds=lambda: 12.5)
        await _invoke(ctx, provider)
        assert provider.timeouts == [12.5]

    async def test_an_expired_deadline_reserves_and_sends_nothing(self) -> None:
        journal, provider = _Journal(), _Provider(_response())
        ctx = ModelInvocationContext(
            ledger=_ledger(), journal=journal, remaining_seconds=lambda: 0.0
        )
        with pytest.raises(DeadlineExceededError):
            await _invoke(ctx, provider)
        assert provider.calls == 0 and journal.events == []


class TestPricing:
    async def test_an_unpriced_route_is_refused_before_dispatch(self) -> None:
        provider = _Provider(_response())
        with pytest.raises(BudgetExceededError) as caught:
            await _invoke(
                ModelInvocationContext(ledger=_ledger()), provider, price=None
            )
        assert caught.value.dimension is BudgetDimension.COST
        assert provider.calls == 0

    async def test_an_explicit_zero_price_fixture_is_admitted(self) -> None:
        fixture = CallPrice(cost_usd=Decimal(0), digest="f" * 64, fixture=True)
        provider = _Provider(_response(cost_estimate=0.0))
        result = await _invoke(
            ModelInvocationContext(ledger=_ledger()), provider, price=fixture
        )
        assert result.succeeded and provider.calls == 1

    async def test_a_call_that_cannot_fit_never_reaches_the_provider(self) -> None:
        provider = _Provider(_response())
        with pytest.raises(BudgetExceededError):
            await _invoke(ModelInvocationContext(ledger=_ledger(tokens=100)), provider)
        assert provider.calls == 0


class TestOneAttempt:
    async def test_a_plain_failure_is_never_retried_by_default(self) -> None:
        journal = _Journal()
        provider = _Provider(RuntimeError("transient"), _response())
        ctx = ModelInvocationContext(ledger=_ledger(), journal=journal)
        with pytest.raises(RuntimeError):
            await _invoke(ctx, provider)
        assert provider.calls == 1 and journal.names.count("reserve") == 1

    async def test_a_failure_after_dispatch_is_not_retried_even_when_allowed(
        self,
    ) -> None:
        provider = _Provider(RuntimeError("transient"), _response())
        ctx = ModelInvocationContext(ledger=_ledger(), max_attempts=3)
        with pytest.raises(RuntimeError):
            await _invoke(ctx, provider)
        assert provider.calls == 1

    async def test_documented_unbilled_failures_retry_up_to_the_cap_each_with_a_row(
        self,
    ) -> None:
        journal, ledger = _Journal(), _ledger()
        provider = _Provider(*(UnbilledTransportError("dns") for _ in range(5)))
        ctx = ModelInvocationContext(ledger=ledger, journal=journal, max_attempts=3)
        with pytest.raises(UnbilledTransportError):
            await _invoke(ctx, provider)
        assert provider.calls == 3 and journal.names.count("reserve") == 3
        assert ledger.tokens_used == 0 and ledger.unknown_usage_calls == 0

    async def test_an_unbilled_failure_then_success_charges_only_the_success(
        self,
    ) -> None:
        ledger = _ledger()
        provider = _Provider(UnbilledTransportError("dns"), _response())
        ctx = ModelInvocationContext(ledger=ledger, max_attempts=2)
        result = await _invoke(ctx, provider)
        assert result.attempts == 2 and ledger.tokens_used == 100

    async def test_the_deadline_is_checked_before_every_attempt(self) -> None:
        left = iter([5.0, 0.0])
        provider = _Provider(UnbilledTransportError("dns"), _response())
        ctx = ModelInvocationContext(
            ledger=_ledger(), remaining_seconds=lambda: next(left), max_attempts=3
        )
        with pytest.raises(DeadlineExceededError):
            await _invoke(ctx, provider)
        assert provider.calls == 1


class TestNormalization:
    @pytest.mark.parametrize(
        ("overrides", "status"),
        [
            ({}, CallStatus.COMPLETE),
            ({"finish_reason": "stop"}, CallStatus.COMPLETE),
            ({"finish_reason": "length"}, CallStatus.TRUNCATED),
            ({"finish_reason": "MAX_TOKENS"}, CallStatus.TRUNCATED),
            ({"finish_reason": "content_filter"}, CallStatus.FILTERED),
            ({"finish_reason": "SAFETY"}, CallStatus.FILTERED),
            ({"finish_reason": "PROHIBITED_CONTENT"}, CallStatus.FILTERED),
            ({"finish_reason": "SPII"}, CallStatus.FILTERED),
            ({"finish_reason": "BLOCKLIST"}, CallStatus.FILTERED),
            ({"content": "   "}, CallStatus.EMPTY),
            ({"content": None}, CallStatus.EMPTY),
            ({"success": False}, CallStatus.FAILED),
            ({"finish_reason": "error"}, CallStatus.FAILED),
            ({"finish_reason": "something_new"}, CallStatus.FAILED),
        ],
    )
    def test_only_a_complete_non_empty_response_succeeds(
        self, overrides: dict[str, Any], status: CallStatus
    ) -> None:
        result = normalize_response(_response(**overrides))
        assert result.status is status
        assert result.succeeded is (status is CallStatus.COMPLETE)

    def test_an_adapter_estimate_is_never_reported_usage(self) -> None:
        result = normalize_response(_response(usage_estimated=True))
        assert result.usage_certainty is UsageCertainty.UNKNOWN
        assert result.total_tokens is None and result.cost_usd is None

    def test_a_response_with_no_usage_is_unknown_not_zero(self) -> None:
        result = normalize_response(_response(total_tokens=0, cost_estimate=0.0))
        assert result.total_tokens is None and result.cost_usd is None
        assert result.usage_certainty is UsageCertainty.UNKNOWN


class TestWorkerUsesTheWrapper:
    """``LLMWorkerAgentBase`` routes every governed call through ``invoke_model``."""

    @staticmethod
    def _task(ctx: ModelInvocationContext | None, ledger: BudgetLedger | None = None):
        from src.agents.models import AgentTask
        from src.core.budget import BUDGET_LEDGER_CONTEXT_KEY
        from src.services.model_invocation import MODEL_INVOCATION_CONTEXT_KEY

        context: dict[str, Any] = {}
        if ctx is not None:
            context[MODEL_INVOCATION_CONTEXT_KEY] = ctx
        if ledger is not None:
            context[BUDGET_LEDGER_CONTEXT_KEY] = ledger
        return AgentTask(
            id="t", agent_type="test_budget", input_data={"query": "q"}, context=context
        )

    @staticmethod
    def _route(response: Any, *, fail_closed: bool = True) -> Any:
        from src.agents.llm_worker_base import _GenerationRoute
        from tests.test_budget_enforcement import _FIXTURE_DECISION, _route

        route = _route([response])
        return _GenerationRoute(
            router=route.router,
            routing_decision=_FIXTURE_DECISION,
            fail_closed=fail_closed,
        )

    async def _generate(self, task: Any, route: Any) -> Any:
        from unittest.mock import patch

        from tests.test_budget_enforcement import _Worker

        worker = _Worker()
        with patch.object(_Worker, "_select_generation_route", return_value=route):
            return await worker._generate_with_routing("prompt", task)

    async def test_the_owners_remaining_deadline_bounds_the_provider_request(
        self,
    ) -> None:
        route = self._route(_response())
        ctx = ModelInvocationContext(ledger=_ledger(), remaining_seconds=lambda: 7.2)
        await self._generate(self._task(ctx), route)
        request = route.router.route_and_generate.await_args.args[0]
        assert request.timeout_seconds == 8

    @pytest.mark.parametrize("finish", ["length", "content_filter"])
    async def test_incomplete_output_is_a_failure_not_research(
        self, finish: str
    ) -> None:
        route = self._route(_response(finish_reason=finish))
        content, confidence = await self._generate(self._task(None, _ledger()), route)
        assert content is None and confidence == 0.0

    async def test_empty_output_is_a_failure_not_research(self) -> None:
        route = self._route(_response(content=""))
        content, _ = await self._generate(self._task(None, _ledger()), route)
        assert content is None

    async def test_failed_fail_closed_call_is_plan_provider_unavailable(self) -> None:
        from src.agents.llm_worker_base import PlanProviderUnavailableError

        route = self._route(_response(success=False, error_message="down"))
        with pytest.raises(PlanProviderUnavailableError, match="down"):
            await self._generate(self._task(None, _ledger()), route)

    async def test_deadline_expiry_is_not_reported_as_a_provider_failure(self) -> None:
        route = self._route(_response())
        ctx = ModelInvocationContext(ledger=_ledger(), remaining_seconds=lambda: 0.0)
        with pytest.raises(DeadlineExceededError):
            await self._generate(self._task(ctx), route)
        route.router.route_and_generate.assert_not_called()

    async def test_governed_direct_gemini_call_is_refused_as_unpriced(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from tests.test_budget_enforcement import _Worker

        worker = _Worker()
        gemini = MagicMock()
        gemini.generate_content_result = AsyncMock()
        worker.gemini_service = gemini
        with pytest.raises(BudgetExceededError) as caught:
            await worker._generate_with_gemini("p", self._task(None, _ledger()))
        assert caught.value.dimension is BudgetDimension.COST
        gemini.generate_content_result.assert_not_called()

    def test_the_configured_attempt_cap_reaches_the_context(self) -> None:
        from unittest.mock import patch

        from src.core.config import settings
        from tests.test_budget_enforcement import _Worker

        assert settings.MODEL_ROUTER_MAX_RETRIES == 1
        with patch.object(settings, "MODEL_ROUTER_MAX_RETRIES", 4):
            ctx = _Worker()._invocation_context(self._task(None, _ledger()))
        assert ctx is not None and ctx.max_attempts == 4

    def test_the_legacy_router_is_built_without_a_hidden_retry_count(self) -> None:
        from unittest.mock import patch

        from src.core.config import settings
        from tests.test_budget_enforcement import _Worker

        with (
            patch.object(settings, "MULTI_PROVIDER_ROUTING_ENABLED", True),
            patch.object(settings, "OPENROUTER_API_KEY", "k"),
            patch("src.ai_brain.providers.ModelRouter") as router_cls,
        ):
            _Worker()._build_legacy_openrouter_route()
        assert "max_retries" not in router_cls.call_args.args[0]


class TestCancelledRunSpendsOnceAndStaysStopped:
    """The research fixture: a run cancelled mid-call under one shared budget."""

    async def test_a_cancelled_call_settles_once_and_the_runs_next_call_is_refused(
        self,
    ) -> None:
        journal, ledger = _Journal(), _ledger()
        ctx = ModelInvocationContext(ledger=ledger, journal=journal)
        first = _Provider(asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await _invoke(ctx, first)
        second = _Provider(_response())
        with pytest.raises(BudgetExceededError):
            await _invoke(ctx, second)
        assert first.calls == 1 and second.calls == 0
        assert journal.names == ["reserve", "mark", "settle_unknown"]
        assert ledger.unknown_usage_calls == 1

    async def test_cancelling_during_settlement_still_settles_exactly_once(
        self,
    ) -> None:
        journal, ledger = _Journal(), _ledger()
        gate = asyncio.Event()
        original = journal.settle_unknown

        async def slow_settle(call_id: str, **kwargs: Any) -> bool:
            await gate.wait()
            return await original(call_id, **kwargs)

        journal.settle_unknown = slow_settle  # type: ignore[method-assign]
        ctx = ModelInvocationContext(ledger=ledger, journal=journal)
        task = asyncio.ensure_future(_invoke(ctx, _Provider(asyncio.CancelledError())))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert journal.names.count("settle_unknown") == 1
        assert ledger.unknown_usage_calls == 1

    async def test_cancelling_during_an_unbilled_settlement_still_settles_once(
        self,
    ) -> None:
        journal, ledger = _Journal(), _ledger()
        gate = asyncio.Event()
        original = journal.settle_known

        async def slow_settle(call_id: str, **kwargs: Any) -> bool:
            await gate.wait()
            return await original(call_id, **kwargs)

        journal.settle_known = slow_settle  # type: ignore[method-assign]
        ctx = ModelInvocationContext(ledger=ledger, journal=journal)
        task = asyncio.ensure_future(
            _invoke(ctx, _Provider(UnbilledTransportError("down")))
        )
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert journal.names.count("settle_known") == 1

    async def test_two_workers_of_one_run_share_the_stopped_budget(self) -> None:
        ledger, journal = _ledger(), _Journal()
        ctx = ModelInvocationContext(ledger=ledger, journal=journal)
        cancelled = TestWorkerUsesTheWrapper._route(_response())
        cancelled.router.route_and_generate.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await TestWorkerUsesTheWrapper()._generate(
                TestWorkerUsesTheWrapper._task(ctx), cancelled
            )
        later = TestWorkerUsesTheWrapper._route(_response())
        with pytest.raises(BudgetExceededError):
            await TestWorkerUsesTheWrapper()._generate(
                TestWorkerUsesTheWrapper._task(ctx), later
            )
        later.router.route_and_generate.assert_not_called()
        assert journal.names.count("settle_unknown") == 1


class _Shape(BaseModel):
    a: int


def _structured_route(*responses: Any, fail_closed: bool = True) -> Any:
    from src.agents.llm_worker_base import _GenerationRoute
    from tests.test_budget_enforcement import _FIXTURE_DECISION, _route

    route = _route(list(responses))
    return _GenerationRoute(
        router=route.router, routing_decision=_FIXTURE_DECISION, fail_closed=fail_closed
    )


class TestGovernedRoutesDoNotQuietlyReask:
    async def _structured(self, task: Any, route: Any) -> Any:
        from unittest.mock import patch

        from tests.test_budget_enforcement import _Worker

        with patch.object(_Worker, "_select_generation_route", return_value=route):
            return await _Worker()._generate_structured_with_routing("p", _Shape, task)

    async def test_a_parse_failure_is_one_provider_call_then_a_typed_failure(
        self,
    ) -> None:
        from src.agents.llm_worker_base import PlanProviderUnavailableError

        route = _structured_route(_response(content="not json"), _response())
        task = TestWorkerUsesTheWrapper._task(None, _ledger())
        with pytest.raises(PlanProviderUnavailableError):
            await self._structured(task, route)
        assert route.router.route_and_generate.await_count == 1

    async def test_a_truncated_result_is_a_failure_not_a_second_call(self) -> None:
        from src.agents.llm_worker_base import PlanProviderUnavailableError

        truncated = _response(content='{"a": 1}', finish_reason="length")
        route = _structured_route(truncated, _response(content='{"a": 2}'))
        task = TestWorkerUsesTheWrapper._task(None, _ledger())
        with pytest.raises(PlanProviderUnavailableError):
            await self._structured(task, route)
        assert route.router.route_and_generate.await_count == 1

    async def test_an_ungoverned_route_still_reasks_with_more_tokens(self) -> None:
        route = _structured_route(
            _response(content="not json"),
            _response(content='{"a": 2}'),
            fail_closed=False,
        )
        task = TestWorkerUsesTheWrapper._task(None)
        result = await self._structured(task, route)
        assert result.a == 2
        assert route.router.route_and_generate.await_count == 2

    async def test_a_missed_deadline_is_not_reported_as_provider_unavailable(
        self,
    ) -> None:
        route = _structured_route(_response(content='{"a": 1}'))
        ctx = ModelInvocationContext(ledger=_ledger(), remaining_seconds=lambda: 0.0)
        with pytest.raises(DeadlineExceededError):
            await self._structured(TestWorkerUsesTheWrapper._task(ctx), route)
        route.router.route_and_generate.assert_not_called()

    async def test_a_plan_provider_missing_from_the_registry_is_unavailable_not_unpriced(
        self,
    ) -> None:
        from src.agents.llm_worker_base import PlanProviderUnavailableError

        route = TestWorkerUsesTheWrapper._route(_response())
        route.router.registry.providers = {}
        task = TestWorkerUsesTheWrapper._task(None, _ledger())
        with pytest.raises(PlanProviderUnavailableError, match="not available"):
            await TestWorkerUsesTheWrapper()._generate(task, route)
        route.router.route_and_generate.assert_not_called()
