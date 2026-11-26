from collections.abc import AsyncIterator, Awaitable, Callable

import asyncpg
import httpx
import pytest

from tests.e2e.journeys.harness import BASE_URL, DATABASE_DSN, Account, register


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as http:
        yield http


@pytest.fixture
async def db() -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(DATABASE_DSN)
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
def new_account(client: httpx.AsyncClient) -> Callable[[str], Awaitable[Account]]:
    async def make(prefix: str) -> Account:
        return await register(client, prefix)

    return make
