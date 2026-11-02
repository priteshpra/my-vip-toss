"""Database connection, engine, sessions, and timezone utilities."""

from datetime import datetime, timezone
from typing import AsyncGenerator, Optional
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import create_engine
from app.config import settings


# 1. Base Model Class
class Base(DeclarativeBase):
    """Base class for all SQLAlchemy 2.0 declarative models."""
    pass


# 2. Async Engine Configuration
connect_args = {}
if settings.resolved_database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

async_engine = create_async_engine(
    settings.resolved_database_url,
    echo=False,
    connect_args=connect_args,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# 3. FastAPI Dependency for Database Sessions
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for yielding an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# 4. Sync Engine for Alembic Migrations and Data Seeding
sync_connect_args = {}
if settings.resolved_sync_database_url.startswith("sqlite"):
    sync_connect_args["check_same_thread"] = False

sync_engine = create_engine(
    settings.resolved_sync_database_url,
    echo=False,
    connect_args=sync_connect_args,
    future=True,
)


# 5. Strict Timezone Converters (UTC internally, IST for presentation)
IST_TZ = ZoneInfo("Asia/Kolkata")
UTC_TZ = timezone.utc


def utc_now() -> datetime:
    """Current UTC datetime."""
    return datetime.now(timezone.utc)


def ist_now() -> datetime:
    """Current IST datetime."""
    return datetime.now(IST_TZ)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure datetime has UTC timezone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_to_ist(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert a UTC or naive datetime to IST."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Treat naive as UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST_TZ)


def format_ist(dt: Optional[datetime], fmt: str = "%Y-%m-%d %I:%M %p IST") -> str:
    """Format datetime as IST string for dashboard display."""
    if dt is None:
        return "N/A"
    ist_dt = utc_to_ist(dt)
    return ist_dt.strftime(fmt)


async def init_db() -> None:
    """Initialize database tables."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
