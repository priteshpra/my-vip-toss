"""Background tasks for scheduled match synchronization, toss verification, and audit integrity."""

from loguru import logger
from sqlalchemy import select
from app.db import AsyncSessionLocal, utc_now
from app.models import Match, PredictionSnapshot
from app.collectors.gaming_helper import GamingHelperCollector
from app.collectors.espn_cricbuzz import CricbuzzCollector
from app.services.match_service import upsert_match
from app.services.toss_service import record_toss_result
from app.services.prediction_service import verify_prediction_integrity
from app.schemas import TossResultCreate


async def sync_live_matches_task() -> None:
    """Scheduled task: Fetch upcoming and live matches from active collectors."""
    logger.info("Executing scheduled task: sync_live_matches_task")
    async with AsyncSessionLocal() as session:
        try:
            collector = GamingHelperCollector()
            matches_data = await collector.fetch_live_bets()
            for m in matches_data:
                mid = str(m.get("id"))
                if not mid:
                    continue
                # Map payload to match table fields
                team_a = m.get("team_a_name", "Team A")
                team_b = m.get("team_b_name", "Team B")
                title = m.get("match_title") or f"{team_a} vs {team_b}"
                
                # Check scheduled start or use now + 2 hours as default
                start_utc = utc_now()
                
                match_dict = {
                    "id": mid,
                    "tournament_id": m.get("tournament_name", "General League"),
                    "team_a_id": team_a,
                    "team_b_id": team_b,
                    "match_format": "T20",
                    "match_title": title,
                    "scheduled_start_utc": start_utc,
                    "status": "UPCOMING" if m.get("status") == "UPCOMING" else "TOSS_PENDING",
                    "data_source_id": "gaming_helper",
                    "odds_a": float(m.get("odds_a", 100)),
                    "odds_b": float(m.get("odds_b", 100)),
                }
                # Check if tournament and teams exist, if not create basic placeholders
                from app.models import Team, Tournament, DataSource
                # Ensure data source exists
                ds = await session.get(DataSource, "gaming_helper")
                if not ds:
                    ds = DataSource(id="gaming_helper", name="GamingHelper Online", provider_type="API")
                    session.add(ds)
                    await session.commit()

                # Ensure teams exist
                for t_name in [team_a, team_b]:
                    t_obj = await session.get(Team, t_name)
                    if not t_obj:
                        session.add(Team(id=t_name, name=t_name, short_code=t_name[:3].upper()))
                # Ensure tournament exists
                tourn_id = m.get("tournament_name", "General League")
                tourn = await session.get(Tournament, tourn_id)
                if not tourn:
                    session.add(Tournament(id=tourn_id, name=tourn_id, category="LEAGUE"))
                await session.commit()

                await upsert_match(session, match_dict)
            logger.info(f"Synchronized {len(matches_data)} matches from GamingHelper.")
        except Exception as e:
            logger.error(f"Error in sync_live_matches_task: {e}")


async def verify_toss_results_task() -> None:
    """Scheduled task: Check for confirmed official toss declarations and evaluate predictions."""
    logger.info("Executing scheduled task: verify_toss_results_task")
    async with AsyncSessionLocal() as session:
        try:
            # Query matches with status UPCOMING or TOSS_PENDING
            stmt = select(Match).where(Match.status.in_(("UPCOMING", "TOSS_PENDING")))
            res = await session.execute(stmt)
            pending_matches = res.scalars().all()

            cricbuzz = CricbuzzCollector()
            verified_count = 0

            for match in pending_matches:
                toss_info = await cricbuzz.check_official_toss(match.id)
                if toss_info and toss_info.get("toss_winner"):
                    winner_name = toss_info["toss_winner"]
                    # Ensure team exists
                    from app.models import Team
                    t_win = await session.get(Team, winner_name)
                    if not t_win:
                        t_win = Team(id=winner_name, name=winner_name, short_code=winner_name[:3].upper())
                        session.add(t_win)
                        await session.commit()

                    toss_create = TossResultCreate(
                        match_id=match.id,
                        toss_winner_id=winner_name,
                        toss_decision=toss_info.get("toss_decision", "BAT"),
                        official_source_url=toss_info.get("official_source_url"),
                        notes="Automated verification from official feed"
                    )
                    await record_toss_result(session, toss_create, source_provider_id="CRICBUZZ")
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
            stmt = select(PredictionSnapshot.id)
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

