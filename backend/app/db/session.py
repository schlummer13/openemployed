from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=10,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=10,
        # Schutz vor hängenden Abfragen/Transaktionen (blockieren sonst Verbindungen und Sperren)
        connect_args={"server_settings": {"statement_timeout": "60000", "idle_in_transaction_session_timeout": "120000",
                                          "lock_timeout": "15000", "application_name": "openemployed"}},
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


async def ping_database() -> None:
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))
