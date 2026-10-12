from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from nba_agent.config import Settings


def make_pool(settings: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        settings.database_url.get_secret_value(),
        open=False,
        min_size=1,
        max_size=5,
        timeout=10,
        kwargs={
            "row_factory": dict_row,
            "connect_timeout": 5,
            "options": "-c statement_timeout=10000 -c idle_in_transaction_session_timeout=15000",
        },
    )


@asynccontextmanager
async def read_connection(pool: AsyncConnectionPool):
    async with pool.connection() as connection, connection.transaction():
        await connection.execute("SET TRANSACTION READ ONLY")
        yield connection
