"""Apply docker/roles.sql through an admin connection.

Skipped when ROLES_ADMIN_DATABASE_URL is unset (SQLite and single-role dev).
A missing password variable fails the start rather than creating a role with
no password.
"""

import asyncio
import os
from pathlib import Path

import asyncpg

PASSWORD_SETTINGS = {
    "cerebro.migrator_password": "CEREBRO_MIGRATOR_PASSWORD",
    "cerebro.app_password": "CEREBRO_APP_PASSWORD",
    "cerebro.maint_password": "CEREBRO_MAINT_PASSWORD",
}


async def apply_roles(admin_url: str) -> None:
    connection = await asyncpg.connect(admin_url.replace("+asyncpg", "", 1))
    try:
        async with connection.transaction():
            # Bound parameters are echoed by log_statement=all, so stop logging
            # before any password is sent.
            await connection.execute(
                "SET LOCAL log_statement = 'none'; "
                "SET LOCAL log_min_duration_statement = -1; "
                "SET LOCAL log_min_error_statement = 'panic'"
            )
            for setting, variable in PASSWORD_SETTINGS.items():
                await connection.execute(
                    "SELECT set_config($1, $2, true)", setting, os.environ[variable]
                )
            await connection.execute(Path(__file__).with_name("roles.sql").read_text())
    finally:
        await connection.close()


if __name__ == "__main__":
    admin_url = os.environ.get("ROLES_ADMIN_DATABASE_URL")
    if admin_url:
        asyncio.run(apply_roles(admin_url))
