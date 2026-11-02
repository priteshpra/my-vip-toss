"""Pytest configuration and shared fixtures for Toss Analytics & Tracking Admin Panel."""

import os
os.environ["TESTING"] = "1"

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.db import Base
from app.main import app
from app.services.seeder import seed_leagues, seed_admin_user


@pytest_asyncio.fixture
async def async_db_session():
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
    async with session_factory() as session:
        await seed_leagues(session)
        await seed_admin_user(session)
        yield session

    await engine.dispose()


@pytest.fixture
def client():
    """Provide a FastAPI TestClient instance."""
    with TestClient(app) as test_client:
        yield test_client
