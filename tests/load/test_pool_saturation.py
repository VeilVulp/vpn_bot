"""Stress connection pool with many parallel sessions."""

import asyncio

import pytest
from sqlalchemy import select, text

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import User

pytestmark = pytest.mark.load


@pytest.mark.asyncio
async def test_pool_handles_50_parallel_reads(db_initialized):
    async def _read():
        async with AsyncSessionLocal() as session:
            await session.execute(select(User).limit(1))
            await asyncio.sleep(0.05)

    await asyncio.gather(*[_read() for _ in range(50)])


@pytest.mark.asyncio
async def test_pool_parallel_raw_connections(db_initialized):
    async def _ping():
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))

    results = await asyncio.gather(*[_ping() for _ in range(40)], return_exceptions=True)
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(failures) <= 2, f"Too many pool failures: {failures}"
