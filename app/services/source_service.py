"""External data source management and multi-source tracking audit service."""

from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy import select, func, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ExternalSource, CollectorHealth, Prediction
from app.services.accuracy_service import get_accuracy_metrics_for_source
from app.schemas import AccuracyMetrics
from app.db import utc_now


async def get_all_sources(session: AsyncSession) -> List[ExternalSource]:
    """Retrieve all configured data sources."""
    query = select(ExternalSource).order_by(ExternalSource.name)
    result = await session.execute(query)
    return list(result.scalars().all())


async def log_source_update(
    session: AsyncSession,
    source: str,
    status: str = "OK",
    response_time_ms: Optional[int] = None,
    message: Optional[str] = None
) -> CollectorHealth:
    """Log an external data source poll / update event."""
    log_entry = CollectorHealth(
        source=source,
        status=status,
        response_time_ms=response_time_ms,
        message=message,
        last_run_utc=utc_now()
    )
    session.add(log_entry)
    await session.commit()
    return log_entry


async def get_source_leaderboard(session: AsyncSession) -> List[AccuracyMetrics]:
    """
    Generate an objective, audited accuracy leaderboard across all tracked prediction sources & channels.
    """
    # 1. Get distinct sources from predictions
    query = select(Prediction.source).distinct().where(Prediction.source.isnot(None))
    result = await session.execute(query)
    channels = [c for c in result.scalars().all() if c]

    leaderboard: List[AccuracyMetrics] = []

    # Also compute aggregate metric for all sources
    all_metric = await get_accuracy_metrics_for_source(session, source_channel=None)
    all_metric.source_name = "Overall Benchmark (All Sources)"
    leaderboard.append(all_metric)

    for ch in channels:
        metrics = await get_accuracy_metrics_for_source(session, source_channel=ch)
        leaderboard.append(metrics)

    # Sort by measured accuracy (descending), secondary by sample size
    leaderboard.sort(key=lambda m: (m.measured_accuracy_pct, m.evaluated_calls), reverse=True)
    return leaderboard
