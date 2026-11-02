"""External tipster and third-party feed collector with immediate cryptographic lock."""

from typing import Dict, Any, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import PredictionSnapshot
from app.schemas import PredictionCreate
from app.services.prediction_service import lock_prediction
from loguru import logger


class ExternalSourceCollector:
    """Ingests predictions from third-party tipster feeds, webhooks, or external trackers."""

    @staticmethod
    async def ingest_external_prediction(
        session: AsyncSession,
        payload: Dict[str, Any]
    ) -> PredictionSnapshot:
        """
        Normalize and cryptographically lock incoming tipster prediction.
        Rejects retro-active calls automatically.
        """
        match_id = str(payload.get("match_id", "")).strip()
        if not match_id:
            raise ValueError("Payload missing match_id.")

        source_channel = payload.get("source_channel") or payload.get("channel_name") or "External Feed"
        predicted_team_id = payload.get("predicted_team_id")
        action = (payload.get("action") or payload.get("action_type") or "PLAY").upper()
        if action not in ("PLAY", "SKIP"):
            action = "PLAY"

        pred_data = PredictionCreate(
            match_id=match_id,
            model_version_id=payload.get("model_version_id", "external_feed_v1"),
            source_channel=source_channel,
            action_type=action,
            predicted_team_id=predicted_team_id,
            estimated_prob_a=payload.get("estimated_prob_a"),
            estimated_prob_b=payload.get("estimated_prob_b"),
            punter_rationale=payload.get("rationale") or payload.get("punter_rationale"),
            raw_payload_json=str(payload)
        )

        snapshot = await lock_prediction(session, pred_data)
        logger.info(f"Ingested and locked external prediction for match {match_id} from {source_channel}.")
        return snapshot

