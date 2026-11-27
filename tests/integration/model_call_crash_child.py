"""Child process for the crash-accounting test: one governed call, killed at a barrier.

Usage: model_call_crash_child.py URL ORG OWNER_ID BARRIER PROVIDER_LOG

It drives the real ``invoke_model`` against the real journal. At the chosen
barrier it prints ``READY`` and blocks, so the parent can SIGKILL it with the
journal in exactly that state. The fake provider appends a line to
PROVIDER_LOG when (and only when) the request would have left the process.
"""

import asyncio
import sys
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.core.budget import BudgetLedger
from src.core.contracts import ExecutionBudget
from src.repositories.model_call_reservation_repository import (
    ModelCallReservationRepository,
    OwnerBudget,
    OwnerLostError,
)
from src.services.model_invocation import (
    CallPrice,
    ModelInvocationContext,
    invoke_model,
)

FENCE = 7


class ChildOwner:
    kind = "research_run"

    def __init__(self, owner_id: str) -> None:
        self.owner_id = owner_id

    async def lock(
        self, session: AsyncSession, organization_id: uuid.UUID
    ) -> OwnerBudget:
        row = (
            await session.execute(
                text(
                    "SELECT max_tokens, max_cost, fence, live FROM journal_test_owners "
                    "WHERE id = :id AND organization_id = :org FOR UPDATE"
                ),
                {"id": self.owner_id, "org": organization_id},
            )
        ).first()
        if row is None:
            raise OwnerLostError("no such owner in this organization")
        return OwnerBudget(row[0], Decimal(row[1]), row[2], row[3])


async def _hang() -> None:
    print("READY", flush=True)
    await asyncio.sleep(3600)


class _BarrierJournal:
    """The real repository, stopped at one point in the call's life."""

    def __init__(self, repo: ModelCallReservationRepository, barrier: str) -> None:
        self._repo, self._barrier = repo, barrier

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repo, name)

    async def mark_may_have_sent(self, *args: Any, **kwargs: Any) -> None:
        if self._barrier == "before_marker":
            await _hang()
        await self._repo.mark_may_have_sent(*args, **kwargs)

    async def settle_known(self, *args: Any, **kwargs: Any) -> bool:
        if self._barrier == "after_response":
            await _hang()
        return await self._repo.settle_known(*args, **kwargs)


async def main(url: str, org: str, owner_id: str, barrier: str, log: str) -> None:
    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
    repo = ModelCallReservationRepository(
        async_sessionmaker(engine, expire_on_commit=False),
        organization_id=org,
        owner=ChildOwner(owner_id),
    )
    ctx = ModelInvocationContext(
        ledger=BudgetLedger(
            ExecutionBudget(
                max_cost_usd=Decimal(1),
                max_total_tokens=100_000,
                max_tool_invocations=1,
                max_parallel_tasks=1,
                max_attempts_per_task=1,
                task_timeout_seconds=30,
            )
        ),
        journal=_BarrierJournal(repo, barrier),  # type: ignore[arg-type]
        fencing_token=FENCE,
    )

    async def provider(_remaining: float | None) -> Any:
        with open(log, "a") as handle:
            handle.write("sent\n")
        if barrier == "after_marker":
            await _hang()
        from types import SimpleNamespace

        return SimpleNamespace(
            success=True,
            content="x",
            finish_reason="stop",
            total_tokens=10,
            prompt_tokens=5,
            completion_tokens=5,
            cost_estimate=0.001,
        )

    await invoke_model(
        ctx, provider, estimated_tokens=400, price=CallPrice(Decimal("0.1"), "d" * 64)
    )


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:6]))
