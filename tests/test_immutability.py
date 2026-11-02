"""Unit tests verifying append-only immutability of Prediction and SourceSnapshot models."""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
from app.db import Base
from app.models import (
    League, Team, Match, Prediction, SourceSnapshot, ExternalSource, ReadOnlyError
)


@pytest_asyncio.fixture
async def session():
    """Create an isolated in-memory SQLite database session."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as sess:
        # Seed foundational data
        league = League(name="IPL", format="T20")
        team_a = Team(name="CSK")
        team_b = Team(name="MI")
        source = ExternalSource(name="TelegramVIP", type="telegram_channel")
        sess.add_all([league, team_a, team_b, source])
        await sess.commit()

        match = Match(
            league_id=league.id,
            team_a_id=team_a.id,
            team_b_id=team_b.id,
            start_time_utc=datetime.now(timezone.utc),
            status="upcoming"
        )
        sess.add(match)
        await sess.commit()
        yield sess

    await engine.dispose()


@pytest.mark.asyncio
async def test_prediction_immutability_prevent_update(session: AsyncSession):
    """Attempting to update a Prediction must be rejected by ORM guard."""
    pred = Prediction(
        match_id=1,
        lean="TeamA",
        probability=0.58,
        hash="a" * 64,
        source="own"
    )
    session.add(pred)
    await session.commit()

    # Attempt mutation
    pred.lean = "TeamB"
    with pytest.raises(ReadOnlyError) as exc_info:
        await session.commit()
    assert "append-only. Updates are forbidden" in str(exc_info.value)
    await session.rollback()


@pytest.mark.asyncio
async def test_prediction_immutability_prevent_delete(session: AsyncSession):
    """Attempting to delete a Prediction must be rejected by ORM guard."""
    pred = Prediction(
        match_id=1,
        lean="TeamA",
        probability=0.55,
        hash="b" * 64,
        source="own"
    )
    session.add(pred)
    await session.commit()

    # Attempt deletion
    await session.delete(pred)
    with pytest.raises(ReadOnlyError) as exc_info:
        await session.commit()
    assert "append-only. Deletions are forbidden" in str(exc_info.value)
    await session.rollback()


@pytest.mark.asyncio
async def test_source_snapshot_immutability(session: AsyncSession):
    """Attempting to update or delete a SourceSnapshot must be rejected by ORM guard."""
    snap = SourceSnapshot(
        source_id=1,
        match_id=1,
        lean="CSK",
        market="Load: 75%"
    )
    session.add(snap)
    await session.commit()

    # Attempt mutation
    snap.lean = "MI"
    with pytest.raises(ReadOnlyError):
        await session.commit()
    await session.rollback()

    # Attempt deletion
    await session.delete(snap)
    with pytest.raises(ReadOnlyError):
        await session.commit()
    await session.rollback()

