"""Pydantic v2 validation schemas for Toss Analytics API and Dashboards."""

from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, ConfigDict, Field


# 1. Base Configuration Schema
class OrmBaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# 2. Team & Tournament Schemas
class TeamBase(OrmBaseModel):
    id: str
    name: str
    short_code: str
    category: str = "OTHER"
    logo_url: Optional[str] = None


class TeamCreate(TeamBase):
    aliases: Optional[str] = None


class TeamRead(TeamBase):
    pass


class TournamentBase(OrmBaseModel):
    id: str
    name: str
    category: str = "LEAGUE"
    format: str = "T20"


class TournamentRead(TournamentBase):
    pass


class VenueRead(OrmBaseModel):
    id: str
    name: str
    city: Optional[str] = None
    country: Optional[str] = None


# 3. Match Schemas
class MatchBase(OrmBaseModel):
    id: str
    tournament_id: str
    team_a_id: str
    team_b_id: str
    venue_id: Optional[str] = None
    match_format: str = "T20"
    match_title: str
    scheduled_start_utc: datetime
    expected_toss_utc: Optional[datetime] = None
    status: str = "UPCOMING"
    data_source_id: str
    odds_a: Optional[float] = None
    odds_b: Optional[float] = None


class MatchCreate(MatchBase):
    external_provider_id: Optional[str] = None


class TossResultRead(OrmBaseModel):
    match_id: str
    toss_winner_id: str
    toss_decision: str
    match_winner_id: Optional[str] = None
    official_source_url: Optional[str] = None
    verified_at_utc: datetime
    verification_status: str
    notes: Optional[str] = None


class TossResultCreate(BaseModel):
    match_id: Union[str, int]
    toss_winner_id: Union[str, int]
    toss_decision: str = "BAT"  # BAT or BOWL
    match_winner_id: Optional[Union[str, int]] = None
    official_source_url: Optional[str] = None
    notes: Optional[str] = None


class MatchRead(MatchBase):
    tournament: Optional[TournamentRead] = None
    venue: Optional[VenueRead] = None
    team_a: Optional[TeamRead] = None
    team_b: Optional[TeamRead] = None
    toss_result: Optional[TossResultRead] = None
    scheduled_start_ist: Optional[str] = None
    expected_toss_ist: Optional[str] = None


# 4. Prediction & Evaluation Schemas
class PredictionCreate(BaseModel):
    """Schema for submitting or locking a pre-toss prediction."""
    match_id: Union[str, int]
    model_version_id: str = "baseline_model"
    source_channel: Optional[str] = "Statistical Engine"
    action_type: str = "PLAY"  # PLAY or SKIP
    predicted_team_id: Optional[Union[str, int]] = None
    estimated_prob_a: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    estimated_prob_b: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    punter_rationale: Optional[str] = None
    raw_payload_json: Optional[str] = None


class PredictionEvaluationRead(OrmBaseModel):
    id: int
    match_id: str
    actual_toss_winner_id: str
    predicted_team_id: Optional[str] = None
    is_correct: Optional[bool] = None
    brier_score: Optional[float] = None
    evaluated_at_utc: datetime


class PredictionRead(OrmBaseModel):
    id: int
    match_id: str
    model_version_id: str
    source_channel: Optional[str] = None
    action_type: str
    predicted_team_id: Optional[str] = None
    predicted_team: Optional[TeamRead] = None
    assessment_status: str
    estimated_prob_a: Optional[float] = None
    estimated_prob_b: Optional[float] = None
    confidence_interval_lower: Optional[float] = None
    confidence_interval_upper: Optional[float] = None
    sample_size_evaluated: int = 0
    p_value_vs_baseline: Optional[float] = None
    snapshot_hash: str
    locked_at_utc: datetime
    locked_at_ist: Optional[str] = None
    is_locked: bool
    is_pre_toss: bool
    punter_rationale: Optional[str] = None
    evaluation: Optional[PredictionEvaluationRead] = None


class PredictionAuditResponse(BaseModel):
    prediction_id: int
    match_id: str
    snapshot_hash: str
    computed_hash: str
    is_valid: bool
    locked_at_utc: datetime
    scheduled_start_utc: datetime
    is_pre_toss_verified: bool
    audit_verdict: str


# 5. Statistical & Accuracy Schemas (Honesty Gates)
class WilsonConfidenceInterval(BaseModel):
    confidence_level: float = 0.95
    lower_bound: float
    upper_bound: float
    display_str: str


class BinomialBaselineTest(BaseModel):
    baseline_probability: float = 0.50
    sample_proportion: float
    p_value: float
    is_significant: bool
    conclusion: str


class AccuracyMetrics(BaseModel):
    source_name: str
    total_calls: int
    play_calls: int
    skip_calls: int
    evaluated_calls: int
    wins: int
    losses: int
    measured_accuracy_pct: float
    wilson_interval_95: WilsonConfidenceInterval
    binomial_test: BinomialBaselineTest
    brier_score: Optional[float] = None
    sample_size_status: str  # SUFFICIENT (n>=30) or LOW_SAMPLE (n<30)
    honest_disclaimer: str = "Toss is a random event. Past stats do not guarantee results. Play responsibly."


# 6. Telegram Channel Ingest Schema
class TelegramMessageIngest(BaseModel):
    channel_name: str
    channel_id: Optional[str] = None
    message_id: str
    post_timestamp_utc: datetime
    raw_message_text: str
    extracted_team_id: Optional[str] = None
    extracted_match_id: Optional[str] = None
    notes: Optional[str] = None

