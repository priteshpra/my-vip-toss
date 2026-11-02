"""Background tasks for scheduled match synchronization, toss verification, and audit integrity."""

from loguru import logger
from sqlalchemy import select
from app.db import AsyncSessionLocal, utc_now
from app.models import Match, Team, League, ExternalSource, Prediction, PredictionOutcome, TossResult
from app.collectors.gaming_helper import GamingHelperCollector
from app.collectors.espn_cricbuzz import CricbuzzCollector
from app.services.match_service import upsert_match
from app.services.toss_service import record_toss_result
from app.services.prediction_service import verify_prediction_integrity
from app.services.normalizer import normalizer
from app.schemas import TossResultCreate


async def sync_live_matches_task() -> None:
    """Scheduled task: Fetch upcoming and live matches from active collectors."""
    logger.info("Executing scheduled task: sync_live_matches_task")
    async with AsyncSessionLocal() as session:
        try:
            collector = GamingHelperCollector()
            result = await collector.sync_matches(session)
            logger.info(f"Synchronized matches from GamingHelper: {result}")
        except Exception as e:
            logger.error(f"Error in sync_live_matches_task: {e}")


async def verify_toss_results_task() -> None:
    """Scheduled task: Check for confirmed official toss declarations and evaluate predictions."""
    logger.info("Executing scheduled task: verify_toss_results_task")
    async with AsyncSessionLocal() as session:
        try:
            # Query matches where toss is not done
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
