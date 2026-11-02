"""Database seeder for standard leagues and foundational entities."""

import json
from typing import List, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import League, AdminUser
from app.auth.security import hash_password
from app.config import settings
from loguru import logger

INITIAL_LEAGUES: List[Dict[str, str]] = [
    {
        "name": "IPL",
        "aliases": "Indian Premier League, TATA IPL, IPL T20, IPL 2026, Indian Premier League 2026",
        "format": "T20"
    },
    {
        "name": "BBL",
        "aliases": "Big Bash League, KFC BBL, BBL T20, BBL 2026, Big Bash League 2026",
        "format": "T20"
    },
    {
        "name": "WBBL",
        "aliases": "Women's Big Bash League, WBBL T20, WBBL 2026, Women Big Bash",
        "format": "T20"
    },
    {
        "name": "NPL",
        "aliases": "Nepal Premier League, NPL T20, Nepal Premier League 2026",
        "format": "T20"
    },
    {
        "name": "ICC ODI",
        "aliases": "ICC One Day International, ICC Men's ODI, ODI, World Cup ODI, One Day International",
        "format": "ODI"
    },
    {
        "name": "ICC T20I",
        "aliases": "ICC Men's T20I, T20I, Twenty20 International, ICC T20 World Cup, T20 International",
        "format": "T20"
    },
    {
        "name": "Emirates D10",
        "aliases": "Emirates D10 League, Emirates D10 Tournament, D10 Emirates, Emirates D20",
        "format": "T10"
    },
    {
        "name": "Local/Unknown",
        "aliases": "Other, Club, Domestic, General League, Unknown, Exhibition",
        "format": "T20"
    }
]


async def seed_leagues(session: AsyncSession) -> int:
    """Seed foundational cricket leagues if they do not exist."""
    seeded = 0
    for data in INITIAL_LEAGUES:
        stmt = select(League).where(League.name == data["name"])
        res = await session.execute(stmt)
        existing = res.scalar_one_or_none()
        if not existing:
            league = League(
                name=data["name"],
                aliases=data["aliases"],
                format=data["format"]
            )
            session.add(league)
            seeded += 1
    if seeded > 0:
        await session.commit()
        logger.info(f"Seeded {seeded} standard leagues.")
    return seeded


async def seed_admin_user(session: AsyncSession) -> None:
    """Seed initial default admin user from configuration if table empty."""
    stmt = select(AdminUser).where(AdminUser.username == settings.admin_username)
    res = await session.execute(stmt)
    existing = res.scalar_one_or_none()
    if not existing:
        admin = AdminUser(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            email="admin@tosstracker.local",
            is_active=True
        )
        session.add(admin)
        await session.commit()
        logger.info(f"Initialized admin user: {settings.admin_username}")


async def seed_external_sources(session: AsyncSession) -> int:
    """Seed foundational external sources and tipster channels if empty."""
    from app.models import ExternalSource

    initial_sources = [
        {
            "name": "Exchange Flow & Floor Insights",
            "type": "manual",
            "url": None,
            "parse_rules": json.dumps({"description": "Manual intelligence entered by system administrators."}),
            "poll_interval": 300,
            "enabled": True,
            "health": "HEALTHY"
        },
        {
            "name": "VIP Cricket Toss Alerts",
            "type": "telegram_channel",
            "url": None,
            "parse_rules": json.dumps({"channel_name": "@VIPCricketToss", "auto_queue_ambiguous": True}),
            "poll_interval": 60,
            "enabled": True,
            "health": "HEALTHY"
        },
        {
            "name": "Public T20 Market Odds",
            "type": "json_api",
            "url": "https://api.example.com/v1/cricket/odds",
            "parse_rules": json.dumps({"items_path": "matches", "team_a_key": "team1", "team_b_key": "team2", "lean_key": "toss_lean"}),
            "poll_interval": 600,
            "enabled": False,
            "health": "HEALTHY"
        }
    ]

    seeded = 0
    for data in initial_sources:
        stmt = select(ExternalSource).where(ExternalSource.name == data["name"])
        res = await session.execute(stmt)
        if not res.scalar_one_or_none():
            src = ExternalSource(
                name=data["name"],
                type=data["type"],
                url=data["url"],
                parse_rules=data["parse_rules"],
                poll_interval=data["poll_interval"],
                enabled=data["enabled"],
                health=data["health"]
            )
            session.add(src)
            seeded += 1

    if seeded > 0:
        await session.commit()
        logger.info(f"Seeded {seeded} initial external sources.")
    return seeded

