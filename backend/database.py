from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://user:password@localhost:5432/interview_db",
)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
)

AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    session = AsyncSessionFactory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_database(metadata: object) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        if engine.dialect.name == "postgresql":
            await connection.execute(
                text(
                    """
                    ALTER TABLE interview_sessions
                    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'WAITING'
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    UPDATE interview_sessions
                    SET status = COALESCE(status, state, 'WAITING')
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    ALTER TABLE interview_sessions
                    ADD COLUMN IF NOT EXISTS is_running BOOLEAN NOT NULL DEFAULT FALSE
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    ALTER TABLE interview_sessions
                    ADD COLUMN IF NOT EXISTS error_reason TEXT NULL
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    ALTER TABLE interview_sessions
                    ADD COLUMN IF NOT EXISTS duration_seconds INTEGER NOT NULL DEFAULT 0
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    ALTER TABLE interview_sessions
                    ADD COLUMN IF NOT EXISTS force_followup_test BOOLEAN NOT NULL DEFAULT FALSE
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    ALTER TABLE interview_sessions
                    ADD COLUMN IF NOT EXISTS current_question_text TEXT NULL
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    ALTER TABLE interview_sessions
                    ADD COLUMN IF NOT EXISTS greeting_text TEXT NULL
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    ALTER TABLE interview_sessions
                    ADD COLUMN IF NOT EXISTS config JSON NOT NULL DEFAULT '{}'::json
                    """
                )
            )


async def close_database() -> None:
    await engine.dispose()
