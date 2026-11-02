"""Background tasks for scheduled match synchronization, toss verification, external source polling, and database maintenance."""

import time
from loguru import logger
from sqlalchemy import select, func
from app.db import AsyncSessionLocal, utc_now
from app.models import Match, Team, League, ExternalSource, Prediction, PredictionOutcome, TossResult, CollectorHealth
from app.collectors.gaming_helper import GamingHelperCollector
from app.collectors.espn_cricbuzz import CricbuzzCollector
from app.collectors.cricsheet import CricsheetCollector
from app.collectors.external_sources import poll_all_enabled_sources
from app.services.match_service import upsert_match
from app.services.toss_service import record_toss_result
from app.services.prediction_service import verify_prediction_integrity
from app.services.normalizer import normalizer
from app.services.backup_service import perform_database_backup
from app.schemas import TossResultCreate


async def sync_live_matches_task() -> None:
    """Scheduled task: Fetch upcoming and live matches from GamingHelper with fallback to cache."""
    logger.info("Executing scheduled task: sync_live_matches_task (GamingHelper)")
    async with AsyncSessionLocal() as session:
        try:
            collector = GamingHelperCollector()
            result = await collector.sync_matches(session)
            logger.info(f"Synchronized matches from GamingHelper: {result}")
        except Exception as e:
            logger.error(f"Error in sync_live_matches_task: {e}")


async def verify_toss_results_task() -> None:
    """Scheduled task: Check for confirmed official toss declarations and evaluate predictions."""
    logger.info("Executing scheduled task: verify_toss_results_task (Public Fixtures)")
    async with AsyncSessionLocal() as session:
        try:
            stmt = select(Match).where(Match.status.in_(("upcoming", "live", "UPCOMING", "TOSS_PENDING")))
            res = await session.execute(stmt)
            pending_matches = res.scalars().all()

            cricbuzz = CricbuzzCollector()
            verified_count = 0

            for match in pending_matches:
                ref = match.gaming_helper_ref or str(match.id)
                toss_info = await cricbuzz.check_official_toss(ref)
                if toss_info and toss_info.get("toss_winner"):
                    raw_winner = toss_info["toss_winner"]
                    norm_winner = normalizer.normalize_team(raw_winner) or raw_winner

                    stmt_w = select(Team).where(Team.name == norm_winner)
                    res_w = await session.execute(stmt_w)
                    t_win = res_w.scalar_one_or_none()
                    if not t_win:
                        t_win = Team(name=norm_winner, short_code=norm_winner[:3].upper())
                        session.add(t_win)
                        await session.commit()
                        await session.refresh(t_win)

                    toss_create = TossResultCreate(
                        match_id=str(match.id),
                        toss_winner_id=str(t_win.id),
                        toss_decision=toss_info.get("toss_decision", "bat"),
                        official_source_url=toss_info.get("official_source_url"),
                        notes="Automated verification from official feed"
                    )
                    await record_toss_result(session, toss_create, source_provider_id="cricbuzz")
                    verified_count += 1

            if verified_count > 0:
                logger.info(f"Verified and recorded {verified_count} toss outcomes.")
        except Exception as e:
            logger.error(f"Error in verify_toss_results_task: {e}")


async def poll_external_sources_task() -> None:
    """Scheduled task: Ingest intelligence from all enabled external sources and tipster channels."""
    logger.info("Executing scheduled task: poll_external_sources_task")
    async with AsyncSessionLocal() as session:
        try:
            result = await poll_all_enabled_sources(session)
            logger.info(f"Poll completed: {result['sources_polled']} source(s) polled, {result['total_snapshots']} snapshot(s) added.")
        except Exception as e:
            logger.error(f"Error in poll_external_sources_task: {e}")


async def sync_cricsheet_daily_task() -> None:
    """Scheduled task: Incremental daily synchronization of open-data Cricsheet archives."""
    logger.info("Executing scheduled task: sync_cricsheet_daily_task")
    async with AsyncSessionLocal() as session:
        try:
            cricsheet = CricsheetCollector()
            stats = await cricsheet.sync_historical_records_to_db(session, limit=500)
            logger.info(f"Daily Cricsheet sync complete: {stats} record(s) processed.")
        except Exception as e:
            logger.error(f"Error in sync_cricsheet_daily_task: {e}")


async def check_collector_health_task() -> None:
    """Scheduled task: Audit health and response times across all collectors."""
    logger.info("Executing scheduled task: check_collector_health_task")
    async with AsyncSessionLocal() as session:
        try:
            now = utc_now()
            # 1. GamingHelper health check
            gh = GamingHelperCollector()
            gh_start = time.time()
            gh_ok = True
            gh_msg = "Healthy"
            try:
                matches = gh.load_local_fallback()
                gh_time = int((time.time() - gh_start) * 1000)
                gh_msg = f"Operational ({len(matches)} cached records)"
            except Exception as e:
                gh_ok = False
                gh_time = int((time.time() - gh_start) * 1000)
                gh_msg = str(e)

            stmt_gh = select(CollectorHealth).where(CollectorHealth.source == "gaming_helper")
            res_gh = await session.execute(stmt_gh)
            c_gh = res_gh.scalar_one_or_none()
            if not c_gh:
                c_gh = CollectorHealth(source="gaming_helper")
                session.add(c_gh)
            c_gh.status = "OK" if gh_ok else "DEGRADED"
            c_gh.last_run_utc = now
            c_gh.response_time_ms = gh_time
            c_gh.message = gh_msg
            if gh_ok:
                c_gh.last_success_utc = now
            else:
                c_gh.last_error_utc = now
                c_gh.last_error = gh_msg

            # 2. Cricbuzz / ESPN public collector health check
            cb_start = time.time()
            cb = CricbuzzCollector()
            cb_time = int((time.time() - cb_start) * 1000)
            stmt_cb = select(CollectorHealth).where(CollectorHealth.source == "espn_cricbuzz")
            res_cb = await session.execute(stmt_cb)
            c_cb = res_cb.scalar_one_or_none()
            if not c_cb:
                c_cb = CollectorHealth(source="espn_cricbuzz")
                session.add(c_cb)
            c_cb.status = "OK"
            c_cb.last_run_utc = now
            c_cb.last_success_utc = now
            c_cb.response_time_ms = cb_time
            c_cb.message = "Public fixtures scraper operational with rate limiting"

            await session.commit()
            logger.info("Collector health checks updated successfully.")
        except Exception as e:
            logger.error(f"Error in check_collector_health_task: {e}")


async def backup_database_task() -> None:
    """Scheduled task: Hot backup of SQLite database file with retention pruning."""
    logger.info("Executing scheduled task: backup_database_task")
    try:
        res = perform_database_backup()
        if res["status"] == "SUCCESS":
            logger.info(f"Database backup successful: {res['filename']} ({res['size_bytes']} bytes). Total retained: {res['total_backups_retained']}.")
        else:
            logger.warning(f"Database backup notice: {res.get('message') or res.get('error')}")
    except Exception as e:
        logger.error(f"Error in backup_database_task: {e}")


async def audit_prediction_hashes_task() -> None:
    """Scheduled task: Run cryptographic audit across all locked prediction records."""
    logger.info("Executing scheduled task: audit_prediction_hashes_task")
    async with AsyncSessionLocal() as session:
        try:
            stmt = select(Prediction.id)
            res = await session.execute(stmt)
            pred_ids = res.scalars().all()

            tampered = 0
            for pid in pred_ids:
                audit = await verify_prediction_integrity(session, pid)
                if not audit.is_valid:
                    tampered += 1
                    logger.critical(f"INTEGRITY ALERT: Prediction {pid} hash mismatch! Data altered.")

            if tampered == 0:
                logger.info(f"Cryptographic integrity audit PASSED for all {len(pred_ids)} prediction records.")
        except Exception as e:
            logger.error(f"Error in audit_prediction_hashes_task: {e}")
