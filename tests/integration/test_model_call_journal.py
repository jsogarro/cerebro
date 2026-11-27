"""The model-call journal is durable, tenant-scoped, fenced and settles once.

Runs against real Postgres: row locks, CHECKs and the migration are the point.
A "crash" is a fresh engine and repository over the same database after the
first holder simply stopped, so only committed facts can survive.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import CheckConstraint, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from src.core.budget import BudgetDimension, BudgetExceededError
from src.models.db.base import Base
from src.models.db.model_call_reservation import ModelCallReservation
from src.repositories.model_call_reservation_repository import (
    DuplicateCallError,
    ModelCallReservationRepository,
    OwnerBudget,
    OwnerLostError,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"
ORG_A, ORG_B = uuid.uuid4(), uuid.uuid4()
DIGEST = "d" * 64


def _alembic(url: str, *args: str) -> str:
    result = subprocess.run(
        [str(ALEMBIC), *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return result.stdout


@pytest.fixture(name="server", scope="module")
def server_fixture() -> Iterator[PostgresContainer]:
    with PostgresContainer(image="postgres:16-alpine") as container:
        yield container


@pytest.fixture(name="url", scope="module")
def url_fixture(server: PostgresContainer) -> str:
    host, port = server.get_container_host_ip(), server.get_exposed_port(5432)
    return f"postgresql://{server.username}:{server.password}@{host}:{port}/{server.dbname}"


@pytest.fixture(name="migrated", scope="module")
def migrated_fixture(url: str) -> str:
    _alembic(url, "upgrade", "head")
    return url


@pytest_asyncio.fixture(name="engine")
async def engine_fixture(migrated: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS journal_test_owners ("
                "id text NOT NULL, organization_id uuid NOT NULL, "
                "max_tokens int NOT NULL, max_cost numeric NOT NULL, "
                "fence bigint, live boolean NOT NULL, PRIMARY KEY (id, organization_id))"
            )
        )
        await conn.execute(text("DELETE FROM model_call_reservations"))
        await conn.execute(text("DELETE FROM journal_test_owners"))
    yield engine
    await engine.dispose()


@pytest.fixture(name="factory")
def factory_fixture(engine: AsyncEngine) -> Callable[[], AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


class _Owner:
    """Adapter over a real row, locked FOR UPDATE like a production owner row."""

    kind = "research_run"

    def __init__(self, owner_id: str) -> None:
        self.owner_id = owner_id

    async def lock(self, session: AsyncSession, organization_id: uuid.UUID):  # type: ignore[no-untyped-def]
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
        return OwnerBudget(
            max_tokens=row[0],
            max_cost_usd=Decimal(row[1]),
            fencing_token=row[2],
            live=row[3],
        )


async def _owner(
    engine: AsyncEngine,
    *,
    org: uuid.UUID = ORG_A,
    tokens: int = 1000,
    cost: str = "1",
    fence: int | None = 7,
    live: bool = True,
) -> _Owner:
    owner_id = f"run-{uuid.uuid4()}"
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO journal_test_owners VALUES (:id, :org, :t, :c, :f, :l)"),
            {
                "id": owner_id,
                "org": org,
                "t": tokens,
                "c": Decimal(cost),
                "f": fence,
                "l": live,
            },
        )
    return _Owner(owner_id)


def _repo(
    factory: Callable[[], AsyncSession], owner: _Owner, org: uuid.UUID = ORG_A
) -> ModelCallReservationRepository:
    return ModelCallReservationRepository(factory, organization_id=org, owner=owner)


async def _reserve(
    repo: ModelCallReservationRepository, call: str, tokens: int = 400, fence: int = 7
) -> None:
    await repo.reserve(
        call,
        tokens=tokens,
        cost_usd=Decimal("0.1"),
        price_digest=DIGEST,
        fencing_token=fence,
    )


def test_one_head_chained_on_the_similarity_revision() -> None:
    heads = [
        line for line in _alembic("postgresql://x/y", "heads").splitlines() if line
    ]
    assert len(heads) == 1
    migration = next(
        (REPO_ROOT / "alembic" / "versions").glob("*add_model_call_reservations.py")
    )
    assert (
        'down_revision: str | Sequence[str] | None = "b8e3f1a5c7d9"'
        in migration.read_text()
    )


async def test_every_orm_check_index_and_policy_exists_after_migration(
    engine: AsyncEngine,
) -> None:
    table = Base.metadata.tables["model_call_reservations"]
    declared = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
    async with engine.connect() as conn:
        checks = {
            r[0]
            for r in await conn.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE contype='c' AND "
                    "conrelid = 'model_call_reservations'::regclass"
                )
            )
        }
        indexes = {
            r[0]
            for r in await conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename='model_call_reservations'"
                )
            )
        }
        policies = {
            r[0]
            for r in await conn.execute(
                text(
                    "SELECT policyname FROM pg_policies "
                    "WHERE tablename='model_call_reservations'"
                )
            )
        }
    assert declared and declared <= checks
    assert {i.name for i in table.indexes} <= indexes
    assert "tenant_isolation_model_call_reservations" in policies


@pytest.mark.parametrize(
    "values",
    [
        # known with no recorded usage would be NULL under a lazy CHECK, not FALSE
        "settlement_state='settled_known', settled_at=now()",
        "settlement_state='settled_unknown', settled_at=now(), actual_tokens=1, "
        "actual_cost_usd=1",
        "settlement_state='released_unsent', dispatch_state='may_have_sent', "
        "settled_at=now()",
        "settlement_state='open', settled_at=now()",
        "settlement_state='settled_unknown'",
        "reserved_tokens=-1",
    ],
)
async def test_database_rejects_inconsistent_rows(
    engine: AsyncEngine, factory: Callable[[], AsyncSession], values: str
) -> None:
    owner = await _owner(engine)
    repo = _repo(factory, owner)
    call = f"c-{uuid.uuid4()}"
    await _reserve(repo, call)
    async with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(f"UPDATE model_call_reservations SET {values} WHERE call_id=:c"),
                {"c": call},
            )


async def test_reserve_records_an_open_unsent_row_and_rejects_a_duplicate(
    engine: AsyncEngine, factory: Callable[[], AsyncSession]
) -> None:
    repo = _repo(factory, await _owner(engine))
    await _reserve(repo, "c1")
    entry = await repo.entry("c1")
    assert entry is not None
    assert (entry.dispatch_state, entry.settlement_state) == ("reserved", "open")
    with pytest.raises(DuplicateCallError):
        await _reserve(repo, "c1")


async def test_concurrent_reservations_cannot_spend_the_same_remainder(
    engine: AsyncEngine, factory: Callable[[], AsyncSession]
) -> None:
    owner = await _owner(engine, tokens=1000)
    results = await asyncio.gather(
        *(_reserve(_repo(factory, owner), f"c{i}", tokens=400) for i in range(5)),
        return_exceptions=True,
    )
    admitted = [r for r in results if r is None]
    refused = [r for r in results if isinstance(r, BudgetExceededError)]
    assert len(admitted) == 2 and len(refused) == 3
    totals = await _repo(factory, owner).totals()
    assert totals.open_tokens == 800


async def test_a_call_settles_exactly_once(
    engine: AsyncEngine, factory: Callable[[], AsyncSession]
) -> None:
    repo = _repo(factory, await _owner(engine))
    await _reserve(repo, "c1")
    await repo.mark_may_have_sent("c1", fencing_token=7)
    assert await repo.settle_known(
        "c1", tokens=120, cost_usd=Decimal("0.02"), fencing_token=7
    )
    assert not await repo.settle_known(
        "c1", tokens=999, cost_usd=Decimal("9"), fencing_token=7
    )
    assert not await repo.settle_unknown("c1", fencing_token=7)
    totals = await repo.totals()
    assert (totals.known_tokens, totals.known_cost_usd) == (120, Decimal("0.02"))
    assert totals.open_tokens == 0 and totals.uncertain_tokens == 0


async def test_unknown_usage_keeps_its_reservation_and_stops_later_calls(
    engine: AsyncEngine, factory: Callable[[], AsyncSession]
) -> None:
    repo = _repo(factory, await _owner(engine))
    await _reserve(repo, "c1")
    await repo.mark_may_have_sent("c1", fencing_token=7)
    assert await repo.settle_unknown("c1", fencing_token=7)
    totals = await repo.totals()
    assert totals.uncertain_tokens == 400 and totals.known_tokens == 0
    with pytest.raises(BudgetExceededError) as caught:
        await _reserve(repo, "c2", tokens=1)
    assert caught.value.dimension is BudgetDimension.COST


async def test_an_unmarked_call_releases_but_a_marked_one_cannot(
    engine: AsyncEngine, factory: Callable[[], AsyncSession]
) -> None:
    repo = _repo(factory, await _owner(engine))
    await _reserve(repo, "unmarked")
    assert await repo.release_unsent("unmarked", fencing_token=7)
    assert (await repo.totals()).open_tokens == 0
    await _reserve(repo, "marked")
    await repo.mark_may_have_sent("marked", fencing_token=7)
    assert not await repo.release_unsent("marked", fencing_token=7)
    assert (await repo.totals()).open_tokens == 400


@pytest.mark.parametrize("barrier", ["before_marker", "after_marker"])
async def test_a_replacement_reconciles_a_crashed_owner_once(
    engine: AsyncEngine,
    factory: Callable[[], AsyncSession],
    migrated: str,
    barrier: str,
) -> None:
    owner = await _owner(engine)
    first = _repo(factory, owner)
    await _reserve(first, "c1")
    if barrier == "after_marker":
        await first.mark_may_have_sent("c1", fencing_token=7)
    # The holder stops here. The replacement has its own engine and connections.
    replacement_engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    try:
        second = _repo(
            async_sessionmaker(replacement_engine, expire_on_commit=False), owner
        )
        first_pass = await second.reconcile_owner()
        again = await second.reconcile_owner()
        totals = await second.totals()
    finally:
        await replacement_engine.dispose()
    if barrier == "before_marker":
        assert (first_pass.released_unsent, first_pass.settled_unknown) == (1, 0)
        assert totals.uncertain_tokens == 0 and totals.open_tokens == 0
    else:
        assert (first_pass.released_unsent, first_pass.settled_unknown) == (0, 1)
        assert totals.uncertain_tokens == 400
    assert (again.released_unsent, again.settled_unknown) == (0, 0)


async def test_a_stale_owner_cannot_overwrite_a_reconciled_call(
    engine: AsyncEngine, factory: Callable[[], AsyncSession]
) -> None:
    repo = _repo(factory, await _owner(engine))
    await _reserve(repo, "c1")
    await repo.mark_may_have_sent("c1", fencing_token=7)
    await repo.reconcile_owner()
    assert not await repo.settle_known(
        "c1", tokens=1, cost_usd=Decimal(0), fencing_token=7
    )
    entry = await repo.entry("c1")
    assert entry is not None and entry.settlement_state == "settled_unknown"
    assert entry.actual_tokens is None


async def test_a_moved_fence_or_dead_owner_sends_nothing(
    engine: AsyncEngine, factory: Callable[[], AsyncSession]
) -> None:
    repo = _repo(factory, await _owner(engine))
    await _reserve(repo, "c1")
    with pytest.raises(OwnerLostError):
        await repo.mark_may_have_sent("c1", fencing_token=6)
    entry = await repo.entry("c1")
    assert entry is not None and entry.dispatch_state == "reserved"
    dead = _repo(factory, await _owner(engine, live=False))
    with pytest.raises(OwnerLostError):
        await _reserve(dead, "c2")
    assert await dead.entry("c2") is None


async def test_fenced_settlement_rejects_another_holders_fence(
    engine: AsyncEngine, factory: Callable[[], AsyncSession]
) -> None:
    repo = _repo(factory, await _owner(engine))
    await _reserve(repo, "c1")
    await repo.mark_may_have_sent("c1", fencing_token=7)
    assert not await repo.settle_known(
        "c1", tokens=1, cost_usd=Decimal(0), fencing_token=8
    )
    entry = await repo.entry("c1")
    assert entry is not None and entry.settlement_state == "open"


async def test_another_tenant_cannot_see_settle_or_reconcile_a_call(
    engine: AsyncEngine, factory: Callable[[], AsyncSession]
) -> None:
    owner_a = await _owner(engine, org=ORG_A)
    repo_a = _repo(factory, owner_a)
    await _reserve(repo_a, "c1")
    await repo_a.mark_may_have_sent("c1", fencing_token=7)
    # Owner ids are only unique within an organization, so B can hold the same id.
    with pytest.raises(OwnerLostError):
        await _reserve(_repo(factory, owner_a, ORG_B), "c9")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO journal_test_owners VALUES (:id, :org, 1000, 1, 7, true)"
            ),
            {"id": owner_a.owner_id, "org": ORG_B},
        )
    repo_b = _repo(factory, owner_a, ORG_B)
    assert await repo_b.entry("c1") is None
    assert (await repo_b.totals()).open_tokens == 0
    assert (await repo_b.reconcile_owner()).settled_unknown == 0
    assert not await repo_b.settle_known(
        "c1", tokens=1, cost_usd=Decimal(0), fencing_token=7
    )
    entry = await repo_a.entry("c1")
    assert entry is not None and entry.settlement_state == "open"
    assert (await repo_a.totals()).open_tokens == 400


async def test_reconciling_an_owner_that_is_not_the_organizations_is_refused(
    engine: AsyncEngine, factory: Callable[[], AsyncSession]
) -> None:
    """Reconcile takes the owner lock, so the owner must exist in this tenant."""
    owner = await _owner(engine, org=ORG_A)
    repo = _repo(factory, owner)
    await _reserve(repo, "c1")
    await repo.mark_may_have_sent("c1", fencing_token=7)

    with pytest.raises(OwnerLostError):
        await _repo(factory, owner, ORG_B).reconcile_owner()

    entry = await repo.entry("c1")
    assert entry is not None and entry.settlement_state == "open"


async def test_every_row_carries_its_organization(
    engine: AsyncEngine, factory: Callable[[], AsyncSession]
) -> None:
    repo = _repo(factory, await _owner(engine))
    await _reserve(repo, "c1")
    async with engine.connect() as conn:
        orgs = {
            r[0]
            for r in await conn.execute(
                text("SELECT organization_id FROM model_call_reservations")
            )
        }
    assert orgs == {ORG_A}
    assert ModelCallReservation.organization_id.nullable is False
