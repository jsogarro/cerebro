"""Tenant-scoped, fenced, settle-once journal of external model calls.

The repository owns the durable half of model-call accounting. Each primitive is
its own committed transaction unless the caller passes a ``session``, in which
case the caller owns the commit (used to reconcile an interrupted owner in the
same transaction as its state change).

``BudgetLedger`` keeps the arithmetic policy; this module only makes its facts
durable and serializes concurrent callers on the owner's row, so two calls cannot
spend the same remainder.

Rows are keyed by organization on every read and write; the owner adapter is
what proves the owner belongs to that organization.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, cast

from sqlalchemy import ColumnElement, CursorResult, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.budget import BudgetDimension, BudgetExceededError
from src.models.db.model_call_reservation import OWNER_KINDS, ModelCallReservation
from src.repositories.tenant_scope import normalize_organization_id

_OPEN = "open"
_RESERVED = "reserved"
_MAY_HAVE_SENT = "may_have_sent"


def _rowcount(result: object) -> int:
    return cast(CursorResult[Any], result).rowcount


class OwnerLostError(RuntimeError):
    """The owner is gone, not live, or its fence moved: nothing may be sent."""


class DuplicateCallError(RuntimeError):
    """A call id was reserved twice for one organization."""


@dataclass(frozen=True)
class OwnerBudget:
    """The owner's pinned ceilings and live ownership, read under its row lock."""

    max_tokens: int
    max_cost_usd: Decimal
    fencing_token: int | None
    live: bool


class JournalOwner(Protocol):
    """The concrete research run, evaluation job or document job a journal serves.

    ``lock`` must ``SELECT ... FOR UPDATE`` the owner's row scoped to
    ``organization_id`` and raise :class:`OwnerLostError` when no such row exists
    in that organization, so a caller-supplied owner id is never trusted on its own.
    """

    kind: str
    owner_id: str

    async def lock(
        self, session: AsyncSession, organization_id: uuid.UUID
    ) -> OwnerBudget: ...


@dataclass(frozen=True)
class JournalTotals:
    known_tokens: int
    known_cost_usd: Decimal
    uncertain_tokens: int
    uncertain_cost_usd: Decimal
    open_tokens: int
    open_cost_usd: Decimal


@dataclass(frozen=True)
class JournalEntry:
    call_id: str
    dispatch_state: str
    settlement_state: str
    reserved_tokens: int
    reserved_cost_usd: Decimal
    actual_tokens: int | None
    actual_cost_usd: Decimal | None


@dataclass(frozen=True)
class Reconciliation:
    released_unsent: int
    settled_unknown: int


class ModelCallReservationRepository:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        *,
        organization_id: uuid.UUID | str | None,
        owner: JournalOwner,
    ) -> None:
        if owner.kind not in OWNER_KINDS or not owner.owner_id:
            raise ValueError(f"unsupported journal owner {owner.kind!r}")
        self._factory = session_factory
        self._org = normalize_organization_id(organization_id)
        self._owner = owner

    @asynccontextmanager
    async def _tx(self, session: AsyncSession | None) -> AsyncIterator[AsyncSession]:
        if session is not None:
            yield session
            return
        async with self._factory() as own, own.begin():
            yield own

    def _scope(self) -> tuple[ColumnElement[bool], ...]:
        return (
            ModelCallReservation.organization_id == self._org,
            ModelCallReservation.owner_kind == self._owner.kind,
            ModelCallReservation.owner_id == self._owner.owner_id,
        )

    async def _lock_live(
        self, session: AsyncSession, fencing_token: int | None
    ) -> OwnerBudget:
        budget = await self._owner.lock(session, self._org)
        if not budget.live or (
            fencing_token is not None and budget.fencing_token != fencing_token
        ):
            raise OwnerLostError("owner is no longer live under this fence")
        return budget

    async def totals(self, *, session: AsyncSession | None = None) -> JournalTotals:
        async with self._tx(session) as tx:
            return await self._totals(tx)

    async def _totals(self, session: AsyncSession) -> JournalTotals:
        rows = await session.execute(
            select(
                ModelCallReservation.settlement_state,
                func.coalesce(func.sum(ModelCallReservation.reserved_tokens), 0),
                func.coalesce(func.sum(ModelCallReservation.reserved_cost_usd), 0),
                func.coalesce(func.sum(ModelCallReservation.actual_tokens), 0),
                func.coalesce(func.sum(ModelCallReservation.actual_cost_usd), 0),
            )
            .where(*self._scope())
            .group_by(ModelCallReservation.settlement_state)
        )
        by_state = {r[0]: r[1:] for r in rows}
        zero = (0, Decimal(0), 0, Decimal(0))
        known = by_state.get("settled_known", zero)
        unknown = by_state.get("settled_unknown", zero)
        open_ = by_state.get(_OPEN, zero)
        return JournalTotals(
            known_tokens=int(known[2] or 0),
            known_cost_usd=Decimal(known[3] or 0),
            uncertain_tokens=int(unknown[0]),
            uncertain_cost_usd=Decimal(unknown[1]),
            open_tokens=int(open_[0]),
            open_cost_usd=Decimal(open_[1]),
        )

    async def reserve(
        self,
        call_id: str,
        *,
        tokens: int,
        cost_usd: Decimal,
        price_digest: str,
        fencing_token: int | None = None,
        attempt_ref: str | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        """Admit one call against the owner's remainder, or raise.

        Locks the owner, adds every durable reservation and settlement it already
        has, and commits the new row before returning, so a concurrent caller sees
        it. An unknown earlier charge stops all later calls: its true size is not
        known, so no remainder is.
        """
        async with self._tx(session) as tx:
            budget = await self._lock_live(tx, fencing_token)
            totals = await self._totals(tx)
            if totals.uncertain_tokens or totals.uncertain_cost_usd:
                raise BudgetExceededError(
                    BudgetDimension.COST,
                    limit=budget.max_cost_usd,
                    used=totals.known_cost_usd,
                    detail="an earlier call's spend is unknown",
                )
            used_tokens = totals.known_tokens + totals.open_tokens
            if used_tokens + tokens > budget.max_tokens:
                raise BudgetExceededError(
                    BudgetDimension.TOKENS,
                    limit=budget.max_tokens,
                    used=used_tokens,
                    requested=tokens,
                )
            used_cost = totals.known_cost_usd + totals.open_cost_usd
            if used_cost + cost_usd > budget.max_cost_usd:
                raise BudgetExceededError(
                    BudgetDimension.COST,
                    limit=budget.max_cost_usd,
                    used=used_cost,
                    requested=cost_usd,
                )
            tx.add(
                ModelCallReservation(
                    organization_id=self._org,
                    call_id=call_id,
                    owner_kind=self._owner.kind,
                    owner_id=self._owner.owner_id,
                    attempt_ref=attempt_ref,
                    fencing_token=fencing_token,
                    price_digest=price_digest,
                    reserved_tokens=tokens,
                    reserved_cost_usd=cost_usd,
                    dispatch_state=_RESERVED,
                    settlement_state=_OPEN,
                )
            )
            try:
                await tx.flush()
            except IntegrityError as error:
                raise DuplicateCallError(call_id) from error

    async def mark_may_have_sent(
        self,
        call_id: str,
        *,
        fencing_token: int | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        """Commit that the request may leave the process; raise if it must not."""
        async with self._tx(session) as tx:
            await self._lock_live(tx, fencing_token)
            moved = await tx.execute(
                update(ModelCallReservation)
                .where(
                    *self._scope(),
                    ModelCallReservation.call_id == call_id,
                    ModelCallReservation.settlement_state == _OPEN,
                    ModelCallReservation.dispatch_state == _RESERVED,
                )
                .values(dispatch_state=_MAY_HAVE_SENT)
            )
            if _rowcount(moved) != 1:
                raise OwnerLostError(f"call {call_id} is not open for dispatch")

    async def _settle(
        self,
        call_id: str,
        *,
        state: str,
        fencing_token: int | None,
        only_dispatch: str | None = None,
        actual: tuple[int, Decimal] | None = None,
        session: AsyncSession | None,
    ) -> bool:
        conditions: list[ColumnElement[bool]] = [
            *self._scope(),
            ModelCallReservation.call_id == call_id,
            ModelCallReservation.settlement_state == _OPEN,
            ModelCallReservation.fencing_token.is_not_distinct_from(fencing_token),
        ]
        if only_dispatch is not None:
            conditions.append(ModelCallReservation.dispatch_state == only_dispatch)
        values: dict[str, object] = {
            "settlement_state": state,
            "settled_at": func.now(),
        }
        if actual is not None:
            values["actual_tokens"], values["actual_cost_usd"] = actual
        async with self._tx(session) as tx:
            result = await tx.execute(
                update(ModelCallReservation).where(*conditions).values(**values)
            )
            return _rowcount(result) == 1

    async def settle_known(
        self,
        call_id: str,
        *,
        tokens: int,
        cost_usd: Decimal,
        fencing_token: int | None = None,
        session: AsyncSession | None = None,
    ) -> bool:
        """Settle once with usage the provider reported; ``False`` if not open."""
        return await self._settle(
            call_id,
            state="settled_known",
            fencing_token=fencing_token,
            actual=(tokens, cost_usd),
            session=session,
        )

    async def settle_unknown(
        self,
        call_id: str,
        *,
        fencing_token: int | None = None,
        session: AsyncSession | None = None,
    ) -> bool:
        """Settle once keeping the reserved amounts as an uncertain bound."""
        return await self._settle(
            call_id,
            state="settled_unknown",
            fencing_token=fencing_token,
            session=session,
        )

    async def release_unsent(
        self,
        call_id: str,
        *,
        fencing_token: int | None = None,
        session: AsyncSession | None = None,
    ) -> bool:
        """Release a reservation that provably never left the process."""
        return await self._settle(
            call_id,
            state="released_unsent",
            fencing_token=fencing_token,
            only_dispatch=_RESERVED,
            session=session,
        )

    async def reconcile_owner(
        self, *, session: AsyncSession | None = None
    ) -> Reconciliation:
        """Close every still-open call of an interrupted owner. Idempotent.

        Unmarked rows never left, so they release unbilled; marked rows with no
        confirmed response keep their reservation as an uncertain bound. A stale
        owner's later settlement finds the row no longer open and changes nothing.
        """
        async with self._tx(session) as tx:
            await self._owner.lock(tx, self._org)
            counts: list[int] = []
            for dispatch, state in (
                (_RESERVED, "released_unsent"),
                (_MAY_HAVE_SENT, "settled_unknown"),
            ):
                result = await tx.execute(
                    update(ModelCallReservation)
                    .where(
                        *self._scope(),
                        ModelCallReservation.settlement_state == _OPEN,
                        ModelCallReservation.dispatch_state == dispatch,
                    )
                    .values(settlement_state=state, settled_at=func.now())
                )
                counts.append(_rowcount(result))
            return Reconciliation(released_unsent=counts[0], settled_unknown=counts[1])

    async def entry(self, call_id: str) -> JournalEntry | None:
        async with self._tx(None) as tx:
            row = (
                await tx.execute(
                    select(ModelCallReservation).where(
                        *self._scope(), ModelCallReservation.call_id == call_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return JournalEntry(
                call_id=row.call_id,
                dispatch_state=row.dispatch_state,
                settlement_state=row.settlement_state,
                reserved_tokens=row.reserved_tokens,
                reserved_cost_usd=row.reserved_cost_usd,
                actual_tokens=row.actual_tokens,
                actual_cost_usd=row.actual_cost_usd,
            )
