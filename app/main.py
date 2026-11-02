"""Main FastAPI application entrypoint with lifespan startup and background scheduler."""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.config import settings
from app.db import init_db, AsyncSessionLocal
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.web.routes import router as web_router
from app.collectors.cricsheet import CricsheetCollector
from app.collectors.gaming_helper import GamingHelperCollector
from app.models import DataSource, Team, Tournament, Match, PredictionSnapshot
from app.services.match_service import upsert_match
from app.services.prediction_service import lock_prediction
from app.schemas import PredictionCreate
from sqlalchemy import select, func


async def seed_initial_data() -> None:
    """Seed data sources, historical toss records, and initial matches if empty."""
    async with AsyncSessionLocal() as session:
        # 1. Seed Data Sources
        res = await session.execute(select(func.count(DataSource.id)))
        if res.scalar_one() == 0:
            sources = [
                DataSource(id="cricbuzz", name="Cricbuzz Live", provider_type="SCRAPER"),
                DataSource(id="gaming_helper", name="GamingHelper Online", provider_type="API"),
                DataSource(id="telegram_vip", name="Telegram VIP Channels", provider_type="TELEGRAM"),
                DataSource(id="cricsheet", name="Cricsheet Historical Archive", provider_type="FILE"),
            ]
            session.add_all(sources)
            await session.commit()
            logger.info("Initialized default data sources.")

        # 2. Ingest Historical Toss Stats from Cricsheet
        cricsheet = CricsheetCollector()
        await cricsheet.sync_historical_records_to_db(session, limit=1000)

        # 3. Ingest sample / cached upcoming matches from GamingHelper
        gh_collector = GamingHelperCollector()
        matches_data = gh_collector.load_local_fallback()
        if matches_data:
            from app.db import utc_now
            count = 0
            for m in matches_data[:20]:
                mid = str(m.get("id"))
                if not mid:
                    continue
                team_a = m.get("team_a_name", "Team A")
                team_b = m.get("team_b_name", "Team B")
                tourn_name = m.get("tournament_name", "Cricket League")

                # Ensure teams
                for t_name in [team_a, team_b]:
                    t_obj = await session.get(Team, t_name)
                    if not t_obj:
                        session.add(Team(id=t_name, name=t_name, short_code=t_name[:3].upper()))

                # Ensure tournament
                tourn = await session.get(Tournament, tourn_name)
                if not tourn:
                    session.add(Tournament(id=tourn_name, name=tourn_name, category="LEAGUE"))

                await session.commit()

                match_dict = {
                    "id": mid,
                    "tournament_id": tourn_name,
                    "team_a_id": team_a,
                    "team_b_id": team_b,
                    "match_format": "T20",
                    "match_title": m.get("match_title") or f"{team_a} vs {team_b}",
                    "scheduled_start_utc": utc_now(),
                    "status": "UPCOMING" if m.get("status") == "UPCOMING" else "TOSS_PENDING",
                    "data_source_id": "gaming_helper",
                    "odds_a": float(m.get("odds_a", 100)),
                    "odds_b": float(m.get("odds_b", 100)),
                }
                match_obj = await upsert_match(session, match_dict)
                count += 1

                # If prediction exists in payload, lock it
                rec_team = m.get("recommended_team")
                action = m.get("action_type") or ("PLAY" if rec_team else "SKIP")
                try:
                    pred_create = PredictionCreate(
                        match_id=mid,
                        model_version_id="toss_pattern_v1",
                        source_channel=m.get("source") or "GamingHelper Online",
                        action_type=action,
                        predicted_team_id=rec_team if action == "PLAY" else None,
                        punter_rationale=m.get("punter_rationale")
                    )
                    await lock_prediction(session, pred_create)
                except Exception:
                    pass

            logger.info(f"Initialized {count} matches with locked pre-toss tracking.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle event handling: database init, data seeding, and scheduler startup."""
    logger.info("Initializing Toss Analytics Application...")
    await init_db()
    try:
        await seed_initial_data()
    except Exception as e:
        logger.error(f"Error during initial data seeding: {e}")

    start_scheduler()
    yield
    stop_scheduler()
    logger.info("Toss Analytics Application shut down safely.")


def create_app() -> FastAPI:
    """FastAPI application factory."""
    app = FastAPI(
        title="Toss Analytics & Tracking Admin Panel",
        description="Production cricket toss analytics with append-only cryptographic tracking and Wilson score statistical validation.",
        version="1.0.0",
        lifespan=lifespan
    )

    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(web_router)

    @app.get("/health")
    async def healthcheck():
        return {
            "status": "healthy",
            "service": "toss-analytics",
            "environment": settings.environment,
            "timezone": settings.app_timezone
        }

    return app


app = create_app()

