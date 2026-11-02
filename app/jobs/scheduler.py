"""APScheduler background job scheduler runner.

Configures:
- GamingHelper match polling (1-2 min).
- Public fixtures and verified toss outcome checks.
- External sources and tipster feed polling.
- Daily Cricsheet incremental sync.
- Collector health audits.
- Nightly SQLite database backup with automatic retention pruning.
- Periodic cryptographic prediction hash audits.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from app.config import settings
from app.jobs.tasks import (
    sync_live_matches_task,
    verify_toss_results_task,
    poll_external_sources_task,
    sync_cricsheet_daily_task,
    check_collector_health_task,
    backup_database_task,
    audit_prediction_hashes_task
)

scheduler = AsyncIOScheduler()


def start_scheduler() -> AsyncIOScheduler:
    """Start APScheduler with all configured production tasks."""
    global scheduler
    try:
        if scheduler.state != 1 and hasattr(scheduler, '_executors') and scheduler._executors:
            scheduler = AsyncIOScheduler()
    except Exception:
        scheduler = AsyncIOScheduler()

    if scheduler.running:
        return scheduler

    # 1. GamingHelper live matches poll (every 1-2 min, configurable)
    gh_interval = max(60, min(120, settings.gaming_helper_poll_interval))
    scheduler.add_job(
        sync_live_matches_task,
        "interval",
        seconds=gh_interval,
        id="sync_live_matches",
        replace_existing=True,
    )

    # 2. Public fixtures & toss verification (every 1-5 min)
    scheduler.add_job(
        verify_toss_results_task,
        "interval",
        seconds=max(60, settings.fixtures_poll_interval),
        id="verify_toss_results",
        replace_existing=True,
    )

    # 3. External sources & tipster feeds poll (every 5 min)
    scheduler.add_job(
        poll_external_sources_task,
        "interval",
        seconds=max(60, settings.sources_poll_interval),
        id="poll_external_sources",
        replace_existing=True,
    )

    # 4. Collector health checks (every 5 min)
    scheduler.add_job(
        check_collector_health_task,
        "interval",
        seconds=max(60, settings.health_check_interval),
        id="check_collector_health",
        replace_existing=True,
    )

    # 5. Incremental Cricsheet daily sync (every 24 hours)
    scheduler.add_job(
        sync_cricsheet_daily_task,
        "interval",
        hours=settings.cricsheet_sync_interval_hours,
        id="sync_cricsheet_daily",
        replace_existing=True,
    )

    # 6. Nightly database hot backup with retention pruning (every 24 hours)
    scheduler.add_job(
        backup_database_task,
        "interval",
        hours=settings.backup_interval_hours,
        id="backup_database",
        replace_existing=True,
    )

    # 7. Cryptographic hash integrity audit (every 30 min)
    scheduler.add_job(
        audit_prediction_hashes_task,
        "interval",
        seconds=1800,
        id="audit_prediction_hashes",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("APScheduler initialized: all production background jobs registered and running.")
    return scheduler


def stop_scheduler() -> None:
    """Safely shut down background scheduler."""
    global scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)
        scheduler = AsyncIOScheduler()
        logger.info("APScheduler background scheduler shut down.")
