"""Main FastAPI application entrypoint with lifespan startup and background scheduler."""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger

from app.config import settings
from app.db import init_db, AsyncSessionLocal
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.web.routes import router as web_router
from app.auth.dependencies import RequiresLoginException
from app.collectors.cricsheet import CricsheetCollector
from app.collectors.gaming_helper import GamingHelperCollector
from app.models import DataSource, ExternalSource, Team, Tournament, League, Match, PredictionSnapshot
from app.services.match_service import upsert_match
from app.services.prediction_service import lock_prediction
from app.schemas import PredictionCreate
from sqlalchemy import select, func


from app.logger import setup_logging
from app.services.seeder import seed_leagues, seed_admin_user


async def seed_initial_data() -> None:
    """Seed leagues, default sources, and initial baseline data."""
    async with AsyncSessionLocal() as session:
        # 1. Seed standard leagues
        await seed_leagues(session)

        # 2. Seed admin user
        await seed_admin_user(session)

        # 3. Seed Default External Sources
        res = await session.execute(select(func.count(ExternalSource.id)))
        if res.scalar_one() == 0:
            sources = [
                ExternalSource(name="Cricbuzz Live", url="https://m.cricbuzz.com", type="public_html"),
                ExternalSource(name="GamingHelper Online", url=settings.gaming_helper_base_url, type="json_api"),
                ExternalSource(name="Telegram VIP Toss Feed", url=settings.telegram_channel_url, type="telegram_channel"),
                ExternalSource(name="Cricsheet Archive", url=None, type="manual"),
            ]
            session.add_all(sources)
            await session.commit()
            logger.info("Initialized default external data sources.")

        # 4. Ingest Historical Toss Stats from Cricsheet (only if empty)
        res_t = await session.execute(select(func.count(TossResult.id)))
        if res_t.scalar_one() == 0:
            cricsheet = CricsheetCollector()
            await cricsheet.sync_historical_records_to_db(session, limit=100)

        # 5. Ingest sample / cached upcoming matches from GamingHelper (only if empty)
        res_m = await session.execute(select(func.count(Match.id)))
        if res_m.scalar_one() == 0:
            gh_collector = GamingHelperCollector()
            matches_data = gh_collector.load_local_fallback()
            if matches_data:
                from app.db import utc_now
                count = 0
                for m in matches_data[:20]:
                    mid = str(m.get("id"))
                    if not mid:
                        continue
                    team_a_name = m.get("team_a_name", "Team A")
                    team_b_name = m.get("team_b_name", "Team B")
                    tourn_name = m.get("tournament_name", "Cricket League")

                    # Ensure teams
                    team_objs = {}
                    for t_name in [team_a_name, team_b_name]:
                        stmt_t = select(Team).where(Team.name == t_name)
                        res_t = await session.execute(stmt_t)
                        t_obj = res_t.scalar_one_or_none()
                        if not t_obj:
                            t_obj = Team(name=t_name, short_code=t_name[:3].upper())
                            session.add(t_obj)
                            await session.commit()
                            await session.refresh(t_obj)
                        team_objs[t_name] = t_obj

                    # Ensure tournament / league
                    stmt_l = select(League).where(League.name == tourn_name)
                    res_l = await session.execute(stmt_l)
                    tourn = res_l.scalar_one_or_none()
                    if not tourn:
                        tourn = League(name=tourn_name, format="T20")
                        session.add(tourn)
                        await session.commit()
                        await session.refresh(tourn)

                    match_dict = {
                        "source_match_id": mid,
                        "gaming_helper_ref": mid,
                        "league_id": tourn.id,
                        "team_a_id": team_objs[team_a_name].id,
                        "team_b_id": team_objs[team_b_name].id,
                        "start_time_utc": utc_now(),
                        "status": "upcoming",
                        "odds_a": float(m.get("odds_a", 100)),
                        "odds_b": float(m.get("odds_b", 100)),
                    }
                    match_obj = await upsert_match(session, match_dict)
                    count += 1

                    # If prediction exists in payload, lock it
                    rec_team = m.get("recommended_team")
                    action = m.get("action_type") or ("PLAY" if rec_team else "SKIP")
                    rec_team_id = team_objs.get(rec_team).id if rec_team and rec_team in team_objs else None
                    try:
                        pred_create = PredictionCreate(
                            match_id=match_obj.id,
                            model_version_id="toss_pattern_v1",
                            source_channel=m.get("source") or "GamingHelper Online",
                            action_type=action,
                            predicted_team_id=rec_team_id if action == "PLAY" else None,
                            punter_rationale=m.get("punter_rationale")
                        )
                        await lock_prediction(session, pred_create)
                    except Exception:
                        pass

                logger.info(f"Initialized {count} matches with locked pre-toss tracking.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle event handling: database init, data seeding, and scheduler startup."""
    setup_logging()
    logger.info("Initializing Toss Analytics Application...")
    await init_db()
    try:
        await seed_initial_data()
    except Exception as e:
        logger.error(f"Error during initial data seeding: {e}")

    # Only start background scheduler outside test environments
    if os.getenv("TESTING") != "1" and settings.environment != "testing":
        start_scheduler()
    yield
    if os.getenv("TESTING") != "1" and settings.environment != "testing":
        stop_scheduler()
    logger.info("Toss Analytics Application shut down safely.")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforces strict production security headers on all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        return response


def create_app() -> FastAPI:
    """FastAPI application factory."""
    app = FastAPI(
        title="Toss Analytics & Tracking Admin Panel",
        description="Production cricket toss analytics with append-only cryptographic tracking and Wilson score statistical validation.",
        version="1.0.0",
        lifespan=lifespan
    )

    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(RequiresLoginException)
    async def requires_login_exception_handler(request: Request, exc: RequiresLoginException):
        return RedirectResponse(
            url=f"/auth/login?next={exc.next_url}",
            status_code=status.HTTP_303_SEE_OTHER
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

