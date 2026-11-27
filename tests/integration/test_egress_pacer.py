"""Two workers' pacers on separate sessions never overlap on one host."""

import asyncio
import time
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.services.similarity_service import PgHostPacer

pytestmark = [pytest.mark.integration, pytest.mark.slow]

SPACING_S = 3.0


@pytest.fixture(name="server", scope="module")
def server_fixture() -> Iterator[PostgresContainer]:
    with PostgresContainer(image="postgres:16-alpine") as container:
        yield container


@pytest_asyncio.fixture(name="engine")
async def engine_fixture(server: PostgresContainer) -> AsyncIterator[object]:
    url = (
        f"postgresql+asyncpg://{server.username}:{server.password}@"
        f"{server.get_container_host_ip()}:{server.get_exposed_port(5432)}/{server.dbname}"
    )
    engine = create_async_engine(url, pool_size=5, max_overflow=0)
    yield engine
    await engine.dispose()


def _pacers(engine: object) -> tuple[PgHostPacer, PgHostPacer]:
    factory = async_sessionmaker(engine, expire_on_commit=False)  # type: ignore[call-overload]
    return PgHostPacer(factory, poll_s=0.05), PgHostPacer(factory, poll_s=0.05)


async def _request(pacer: PgHostPacer, host: str, starts: list[float]) -> None:
    async with pacer.slot(host):
        starts.append(time.monotonic())
        await asyncio.sleep(SPACING_S)


async def test_the_second_worker_starts_one_spacing_after_the_first(
    engine: object,
) -> None:
    first, second = _pacers(engine)
    starts: list[float] = []
    await asyncio.gather(
        _request(first, "arxiv.org", starts), _request(second, "arxiv.org", starts)
    )
    assert max(starts) - min(starts) >= SPACING_S


async def test_different_hosts_do_not_wait_for_each_other(engine: object) -> None:
    first, second = _pacers(engine)
    starts: list[float] = []
    await asyncio.gather(
        _request(first, "arxiv.org", starts),
        _request(second, "api.openalex.org", starts),
    )
    assert max(starts) - min(starts) < 1.0


async def test_a_waiter_gives_up_at_its_deadline_and_holds_no_connection(
    engine: object,
) -> None:
    holder, waiter = _pacers(engine)
    release = asyncio.Event()

    async def hold() -> None:
        async with holder.slot("arxiv.org"):
            await release.wait()

    held = asyncio.create_task(hold())
    await asyncio.sleep(0.3)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            async with waiter.slot("arxiv.org"):
                pytest.fail("the slot must not open while it is held")
    assert time.monotonic() - started < 1.0
    assert engine.pool.checkedout() == 1  # type: ignore[attr-defined]
    release.set()
    await held
    assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]


async def test_a_waiter_gets_the_slot_once_the_holder_leaves(engine: object) -> None:
    holder, waiter = _pacers(engine)
    order: list[str] = []

    async def hold() -> None:
        async with holder.slot("arxiv.org"):
            order.append("holder")
            await asyncio.sleep(0.5)

    async def wait() -> None:
        await asyncio.sleep(0.2)
        async with waiter.slot("arxiv.org"):
            order.append("waiter")

    await asyncio.gather(hold(), wait())
    assert order == ["holder", "waiter"]
