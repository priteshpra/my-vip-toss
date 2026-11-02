"""APScheduler background job scheduler runner."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from app.config import settings
from app.jobs.tasks import (
    sync_live_matches_task,
    verify_toss_results_task,
    audit_prediction_hashes_task
)

scheduler = AsyncIOScheduler()


def start_scheduler() -> AsyncIOScheduler:
    """Start APScheduler with configured tasks."""
    if scheduler.running:
        return scheduler

    scheduler.add_job(
        sync_live_matches_task,
        "interval",
        seconds=max(60, settings.match_poll_interval),
        id="sync_live_matches",
        replace_existing=True,
    )

    scheduler.add_job(
        verify_toss_results_task,
        "interval",
        seconds=max(30, settings.toss_poll_interval),
        id="verify_toss_results",
        replace_existing=True,
    )

    scheduler.add_job(
        audit_prediction_hashes_task,
        "interval",
        seconds=1800,
        id="audit_prediction_hashes",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("APScheduler background tasks initialized and running.")
    return scheduler


def stop_scheduler() -> None:
    """Safely shut down background scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler background scheduler shut down.")

