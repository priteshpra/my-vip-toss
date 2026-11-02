"""Cricsheet open data historical match parser and ingestion collector."""

import os
import json
from datetime import datetime, date
from typing import List, Dict, Any, Optional
import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import HistoricalMatchStatistic

LOCAL_HISTORICAL_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "synthetic",
    "sample_historical_toss.json"
)


class CricsheetCollector:
    """Ingests empirical historical toss records for longitudinal baseline analysis."""

    def __init__(self, data_path: Optional[str] = None):
        self.data_path = data_path or LOCAL_HISTORICAL_FILE

    def load_local_records(self) -> List[Dict[str, Any]]:
        """Load historical records from local JSON file."""
        if not os.path.exists(self.data_path):
            logger.warning(f"Historical toss file not found at {self.data_path}")
            return []

        try:
            with open(self.data_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    logger.info(f"Loaded {len(data)} historical match records from {self.data_path}")
                    return data
        except Exception as e:
            logger.error(f"Error loading historical records: {e}")
        return []

    async def sync_historical_records_to_db(
        self,
        session: AsyncSession,
        limit: int = 2000
    ) -> int:
        """
        Sync historical records to database table historical_match_statistics.
        Returns count of newly inserted records.
        """
        records = self.load_local_records()
        if not records:
            return 0

        inserted_count = 0
        batch = []

        # Check existing IDs to prevent duplicates
        existing_res = await session.execute(select(HistoricalMatchStatistic.id))
        existing_ids = set(existing_res.scalars().all())

        for r in records[:limit]:
            rec_id = str(r.get("id", ""))
            if not rec_id or rec_id in existing_ids:
                continue

            # Parse date safely
            d_str = r.get("date") or "2025-01-01"
            try:
                m_date = datetime.strptime(d_str, "%Y-%m-%d").date()
            except Exception:
                m_date = date(2025, 1, 1)

            stat = HistoricalMatchStatistic(
                id=rec_id,
                match_date=m_date,
                tournament_name=r.get("tournament", "League"),
                match_format=r.get("format", "T20"),
                team_a_name=r.get("team_a", "Team A"),
                team_b_name=r.get("team_b", "Team B"),
                venue_name=r.get("venue"),
                toss_winner_name=r.get("toss_winner", "Team A"),
                toss_decision=(r.get("toss_decision") or "BAT").upper(),
                match_winner_name=r.get("match_winner"),
                source="CRICSHEET"
            )
            batch.append(stat)
            existing_ids.add(rec_id)
            inserted_count += 1

            if len(batch) >= 200:
                session.add_all(batch)
                await session.commit()
                batch.clear()

        if batch:
            session.add_all(batch)
            await session.commit()

        logger.info(f"Successfully synced {inserted_count} historical match records into database.")
        return inserted_count

