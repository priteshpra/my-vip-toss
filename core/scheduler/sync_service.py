"""Background synchronization and scheduled tasks for TossIQ."""

import logging
from typing import Dict, Any, List, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from core.database.repository import Repository, TossRepository
from core.database.connection import get_connection, get_db_connection
from core.providers.cricket_api import FixtureSyncEngine
from core.engine.statistics import evaluate_toss_advantage
from core.models.schemas import PredictionSnapshot, AssessmentStatus
from core.timezone import now_ist
from config import Settings

logger = logging.getLogger("tossiq.scheduler")


def run_full_sync_and_toss_prediction(
    repo: Repository,
    settings: Settings,
    custom_fixtures: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    1. Ingest/sync fixtures from external API or custom payload.
    2. Normalize teams, formats, and Asia/Kolkata dates.
    3. Generate/update pre-toss statistical assessments for all matches.
    """
    engine = FixtureSyncEngine(repo, settings)
    synced_count = 0
    errors = []

    # 1. Live Schedule Sync from Cricbuzz (Real-Time Matches)
    try:
        from core.providers.cricbuzz_provider import CricbuzzScheduleProvider
        cb_provider = CricbuzzScheduleProvider(repo)
        cb_res = cb_provider.sync_live_cricbuzz_matches()
        synced_count += cb_res.get("ingested_count", 0)
    except Exception as e:
        errors.append(f"Cricbuzz live sync error: {str(e)}")

    # 2. External API Sync (if key provided)
    if settings.cricket_api_key:
        api_res = engine.sync_from_api()
        synced_count += api_res.get("synced_count", 0)
        if not api_res.get("success"):
            errors.append(api_res.get("message", "API sync issue"))

    # 3. Custom/Uploaded Fixtures Sync (if provided)
    if custom_fixtures:
        for f in custom_fixtures:
            try:
                mid = engine.ingest_fixture_record(f, source_id="user_sync")
                if mid:
                    synced_count += 1
            except Exception as e:
                errors.append(f"Fixture parsing error: {str(e)}")

    # 3. Automatic Pre-Toss Evaluation Generation for all upcoming matches
    upcoming = repo.conn.execute("""
        SELECT m.id, m.team_a_id, m.team_b_id, m.scheduled_start_ist,
               t1.name as team_a_name, t2.name as team_b_name
        FROM matches m
        JOIN teams t1 ON m.team_a_id = t1.id
        JOIN teams t2 ON m.team_b_id = t2.id
        WHERE m.status != 'TOSS_COMPLETED'
    """).fetchall()

    predictions_generated = 0
    cur_time = now_ist()

    for m in upcoming:
        mid = m["id"]
        t_a_id = m["team_a_id"]
        t_b_id = m["team_b_id"]

        hist_a = repo.get_historical_tosses_for_team(t_a_id)
        hist_b = repo.get_historical_tosses_for_team(t_b_id)
        wa = sum(1 for h in hist_a if h["toss_winner_id"] == t_a_id)
        wb = sum(1 for h in hist_b if h["toss_winner_id"] == t_b_id)

        gate_res = evaluate_toss_advantage(
            wins_a=wa, total_a=len(hist_a),
            wins_b=wb, total_b=len(hist_b),
            min_sample_size=settings.min_historical_sample_size
        )

        # Check if snapshot already exists
        existing_snap = repo.get_latest_prediction_snapshot(mid)
        if not existing_snap:
            status_val = gate_res["assessment_status"]
            snapshot_item = PredictionSnapshot(
                match_id=mid,
                model_version_id="v1.0_bernoulli_baseline",
                assessment_status=AssessmentStatus(status_val),
                team_a_prob=gate_res.get("estimated_prob_a"),
                team_b_prob=gate_res.get("estimated_prob_b"),
                confidence_interval_lower=gate_res["confidence_interval"][0] if gate_res["confidence_interval"] else None,
                confidence_interval_upper=gate_res["confidence_interval"][1] if gate_res["confidence_interval"] else None,
                supporting_evidence=[f"Longitudinal evaluation across {len(hist_a) + len(hist_b)} verified tosses"],
                contradicting_evidence=["50% independent Bernoulli trial baseline"],
                uncertainty_warnings=gate_res["reason"],
                sample_size_evaluated=len(hist_a) + len(hist_b),
                snapshot_timestamp_ist=cur_time
            )
            try:
                repo.insert_prediction_snapshot(snapshot_item)
                predictions_generated += 1
            except Exception:
                pass

    return {
        "success": True,
        "synced_fixtures": synced_count,
        "predictions_generated": predictions_generated,
        "total_active_matches": len(upcoming),
        "errors": errors
    }


class TossIQScheduler:
    """Manages periodic background synchronization jobs."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.scheduler = BackgroundScheduler(daemon=True)

    def _scheduled_sync_job(self):
        try:
            conn = get_connection(self.settings.abs_database_path)
            repo = Repository(conn)
            run_full_sync_and_toss_prediction(repo, self.settings)
        except Exception as e:
            logger.error(f"Error in scheduled fixture sync: {e}")

    def start(self):
        """Register periodic sync jobs and start background scheduler."""
        if not self.scheduler.running:
            # Run every 6 hours, plus daily at midnight IST
            self.scheduler.add_job(
                self._scheduled_sync_job,
                trigger=IntervalTrigger(seconds=self.settings.fixture_poll_interval_seconds),
                id="daily_fixture_sync",
                name="Daily Cricket Fixtures & Toss Sync",
                replace_existing=True
            )
            self.scheduler.start()

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
