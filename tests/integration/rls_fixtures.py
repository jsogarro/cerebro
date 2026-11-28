"""A Postgres built the way a deployment builds it, with one login per role.

Roles are applied first, then migrations, so every table is under forced
row-level security and ``app`` and ``maint`` are the real, bound roles; ``admin``
is the superuser that seeds and inspects. Import the fixtures into a test module.
"""

from collections.abc import AsyncIterator, Callable, Iterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from tests.integration.test_rls_roles import ALEMBIC, _apply_roles, _role_url, _run

Factory = Callable[[], AsyncSession]


@pytest.fixture(name="server", scope="module")
def server_fixture() -> Iterator[PostgresContainer]:
    with PostgresContainer(image="postgres:16-alpine") as container:
        yield container


@pytest_asyncio.fixture(name="engines", scope="module", loop_scope="module")
async def engines_fixture(
    server: PostgresContainer,
) -> AsyncIterator[dict[str, AsyncEngine]]:
    host, port = server.get_container_host_ip(), server.get_exposed_port(5432)
    admin_url = f"postgresql://{server.username}:{server.password}@{host}:{port}/{server.dbname}"
    _apply_roles(admin_url)
    _run([str(ALEMBIC), "upgrade", "head"], admin_url)
    _apply_roles(admin_url)
    engines = {
        "admin": create_async_engine(
            admin_url.replace("postgresql://", "postgresql+asyncpg://")
        ),
        "app": create_async_engine(_role_url(admin_url, "cerebro_app")),
        "maint": create_async_engine(_role_url(admin_url, "cerebro_maint")),
    }
    yield engines
    for engine in engines.values():
        await engine.dispose()


def _factory(engine: AsyncEngine) -> Factory:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(name="app")
def app_fixture(engines: dict[str, AsyncEngine]) -> Factory:
    return _factory(engines["app"])


@pytest.fixture(name="maint")
def maint_fixture(engines: dict[str, AsyncEngine]) -> Factory:
    return _factory(engines["maint"])


@pytest.fixture(name="admin")
def admin_fixture(engines: dict[str, AsyncEngine]) -> Factory:
    return _factory(engines["admin"])
