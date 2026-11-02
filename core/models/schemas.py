"""Pydantic v2 schemas and validation models for TossIQ."""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator


class MatchFormat(str, Enum):
    T20 = "T20"
    ODI = "ODI"
    TEST = "TEST"
    T10 = "T10"
    OTHER = "OTHER"


class TournamentCategory(str, Enum):
    INTERNATIONAL = "INTERNATIONAL"
    DOMESTIC = "DOMESTIC"
    T20_LEAGUE = "T20_LEAGUE"
    WOMEN = "WOMEN"
    OTHER = "OTHER"


class MatchStatus(str, Enum):
    UPCOMING = "UPCOMING"
    TOSS_PENDING = "TOSS_PENDING"
    TOSS_COMPLETED = "TOSS_COMPLETED"
    DELAYED = "DELAYED"
    ABANDONED = "ABANDONED"
    RESULT_UNAVAILABLE = "RESULT_UNAVAILABLE"


class TossDecision(str, Enum):
    BAT = "bat"
    BOWL = "bowl"
    UNKNOWN = "unknown"


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    DISPUTED = "DISPUTED"


class AssessmentStatus(str, Enum):
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NO_VALIDATED_ADVANTAGE = "NO_VALIDATED_ADVANTAGE"
    PRE_TOSS_ESTIMATE_AVAILABLE = "PRE_TOSS_ESTIMATE_AVAILABLE"
    TOSS_COMPLETED = "TOSS_COMPLETED"


class MarketType(str, Enum):
    TOSS_WINNER = "TOSS_WINNER"
    MATCH_WINNER = "MATCH_WINNER"


class TelegramAuditStatus(str, Enum):
    VERIFIED_PRE_TOSS = "VERIFIED_PRE_TOSS"
    POST_TOSS_EDITED = "POST_TOSS_EDITED"
    AMBIGUOUS = "AMBIGUOUS"
    DUPLICATE = "DUPLICATE"
    UNVERIFIED = "UNVERIFIED"


class Tournament(BaseModel):
    id: str
    name: str
    category: TournamentCategory = TournamentCategory.OTHER
    format: MatchFormat = MatchFormat.T20


class Venue(BaseModel):
    id: str
    name: str
    city: Optional[str] = None
    country: Optional[str] = None


class Team(BaseModel):
    id: str
    name: str
    short_code: str
    category: str = "OTHER"
    aliases: List[str] = Field(default_factory=list)


class Match(BaseModel):
    id: str
    external_provider_id: Optional[str] = None
    tournament_id: str
    team_a_id: str
    team_b_id: str
    venue_id: Optional[str] = None
    match_format: MatchFormat
    scheduled_start_ist: datetime
    expected_toss_ist: Optional[datetime] = None
    status: MatchStatus = MatchStatus.UPCOMING
    data_source_id: str
    last_updated_ist: datetime
    data_freshness_status: str = "FRESH"


class TossResult(BaseModel):
    match_id: str
    toss_winner_id: str
    toss_decision: TossDecision = TossDecision.UNKNOWN
    match_winner_id: Optional[str] = None
    official_source_url: Optional[str] = None
    source_provider_id: str
    verified_at_ist: datetime
    verification_status: VerificationStatus = VerificationStatus.VERIFIED
    notes: Optional[str] = None


class MarketSnapshot(BaseModel):
    id: Optional[int] = None
    match_id: str
    provider_id: str
    bookmaker: str
    market_type: MarketType
    team_a_odds: float = Field(gt=1.0)
    team_b_odds: float = Field(gt=1.0)
    team_a_implied_prob: float = Field(ge=0.0, le=1.0)
    team_b_implied_prob: float = Field(ge=0.0, le=1.0)
    margin_percentage: float = Field(ge=0.0)
    snapshot_timestamp_ist: datetime
    is_pre_toss: bool = True
    is_stale: bool = False


class PredictionSnapshot(BaseModel):
    id: Optional[int] = None
    match_id: str
    model_version_id: str
    assessment_status: AssessmentStatus
    team_a_prob: Optional[float] = None
    team_b_prob: Optional[float] = None
    confidence_interval_lower: Optional[float] = None
    confidence_interval_upper: Optional[float] = None
    supporting_evidence: List[str] = Field(default_factory=list)
    contradicting_evidence: List[str] = Field(default_factory=list)
    uncertainty_warnings: Optional[str] = None
    sample_size_evaluated: int = 0
    snapshot_timestamp_ist: datetime
    is_locked: bool = True


class PredictionEvaluation(BaseModel):
    id: Optional[int] = None
    prediction_snapshot_id: int
    match_id: str
    actual_toss_winner_id: str
    predicted_favored_id: Optional[str] = None
    is_correct: Optional[int] = None  # 1, 0, or None if neutral/inconclusive
    brier_score: Optional[float] = None
    log_loss: Optional[float] = None
    evaluated_at_ist: datetime
