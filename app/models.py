"""SQLAlchemy 2.0 Declarative Database Models for Toss Analytics."""

from datetime import datetime, date
from typing import Optional, List
from sqlalchemy import (
    String, Integer, Float, Boolean, Text, DateTime, Date, ForeignKey, Index, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base, utc_now


# 1. Data Sources
class DataSource(Base):
    """External data provider or ingestion source."""
    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)  # API, SCRAPER, TELEGRAM, FILE
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    base_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    logs: Mapped[List["SourceUpdateLog"]] = relationship("SourceUpdateLog", back_populates="source", cascade="all, delete-orphan")
    matches: Mapped[List["Match"]] = relationship("Match", back_populates="data_source")


class SourceUpdateLog(Base):
    """Health and ingestion logs for external sources."""
    __tablename__ = "source_update_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    records_fetched: Mapped[int] = mapped_column(Integer, default=0)
    is_success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    logged_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    source: Mapped["DataSource"] = relationship("DataSource", back_populates="logs")


# 2. Tournaments, Venues, Teams
class Tournament(Base):
    """Cricket tournament or series."""
    __tablename__ = "tournaments"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="LEAGUE")  # INTERNATIONAL, LEAGUE, DOMESTIC, OTHER
    format: Mapped[str] = mapped_column(String(20), default="T20")  # T20, ODI, TEST, T10, SIXES

    matches: Mapped[List["Match"]] = relationship("Match", back_populates="tournament")


class Venue(Base):
    """Cricket ground / venue."""
    __tablename__ = "venues"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    matches: Mapped[List["Match"]] = relationship("Match", back_populates="venue")


class Team(Base):
    """Cricket team."""
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    short_code: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="OTHER")
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    aliases: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# 3. Matches and Official Toss Results
class Match(Base):
    """Scheduled or past cricket match."""
    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    external_provider_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tournament_id: Mapped[str] = mapped_column(ForeignKey("tournaments.id", ondelete="RESTRICT"), nullable=False)
    team_a_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False)
    team_b_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False)
    venue_id: Mapped[Optional[str]] = mapped_column(ForeignKey("venues.id", ondelete="SET NULL"), nullable=True)
    match_format: Mapped[str] = mapped_column(String(20), default="T20")
    match_title: Mapped[str] = mapped_column(String(255), nullable=False)
    scheduled_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expected_toss_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30),
        default="UPCOMING"
    )  # UPCOMING, TOSS_PENDING, TOSS_COMPLETED, DELAYED, ABANDONED, RESULT_UNAVAILABLE
    data_source_id: Mapped[str] = mapped_column(ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False)
    odds_a: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    odds_b: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_updated_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    # Relationships
    tournament: Mapped["Tournament"] = relationship("Tournament", back_populates="matches")
    venue: Mapped[Optional["Venue"]] = relationship("Venue", back_populates="matches")
    team_a: Mapped["Team"] = relationship("Team", foreign_keys=[team_a_id])
    team_b: Mapped["Team"] = relationship("Team", foreign_keys=[team_b_id])
    data_source: Mapped["DataSource"] = relationship("DataSource", back_populates="matches")
    toss_result: Mapped[Optional["TossResult"]] = relationship("TossResult", back_populates="match", uselist=False, cascade="all, delete-orphan")
    predictions: Mapped[List["PredictionSnapshot"]] = relationship("PredictionSnapshot", back_populates="match", cascade="all, delete-orphan")


class TossResult(Base):
    """Ground-truth verified official toss result."""
    __tablename__ = "toss_results"

    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), primary_key=True)
    toss_winner_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False)
    toss_decision: Mapped[str] = mapped_column(String(10), nullable=False)  # BAT, BOWL, UNKNOWN
    match_winner_id: Mapped[Optional[str]] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    official_source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    source_provider_id: Mapped[str] = mapped_column(String(100), default="OFFICIAL")
    verified_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    verification_status: Mapped[str] = mapped_column(String(20), default="VERIFIED")  # VERIFIED, UNVERIFIED, DISPUTED
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    match: Mapped["Match"] = relationship("Match", back_populates="toss_result")
    toss_winner: Mapped["Team"] = relationship("Team", foreign_keys=[toss_winner_id])
    match_winner: Mapped[Optional["Team"]] = relationship("Team", foreign_keys=[match_winner_id])


# 4. Historical Match Statistics (Longitudinal verified sample)
class HistoricalMatchStatistic(Base):
    """Empirical match history for calculating objective baseline frequencies."""
    __tablename__ = "historical_match_statistics"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    match_date: Mapped[date] = mapped_column(Date, nullable=False)
    tournament_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    match_format: Mapped[str] = mapped_column(String(20), default="T20")
    team_a_name: Mapped[str] = mapped_column(String(200), nullable=False)
    team_b_name: Mapped[str] = mapped_column(String(200), nullable=False)
    venue_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    toss_winner_name: Mapped[str] = mapped_column(String(200), nullable=False)
    toss_decision: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # BAT, BOWL
    match_winner_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="CRICSHEET")


# 5. Append-Only Cryptographically Locked Prediction Snapshots
class PredictionSnapshot(Base):
    """
    Append-only pre-toss prediction record.
    Never edited or deleted. Locked with SHA-256 hash and UTC timestamp before match start.
    """
    __tablename__ = "prediction_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    model_version_id: Mapped[str] = mapped_column(String(100), default="baseline_model")
    source_channel: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    action_type: Mapped[str] = mapped_column(String(20), default="PLAY")  # PLAY, SKIP
    predicted_team_id: Mapped[Optional[str]] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    assessment_status: Mapped[str] = mapped_column(
        String(50),
        default="PRE_TOSS_ESTIMATE_AVAILABLE"
    )  # INSUFFICIENT_DATA, NO_VALIDATED_ADVANTAGE, PRE_TOSS_ESTIMATE_AVAILABLE, TOSS_COMPLETED
    estimated_prob_a: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimated_prob_b: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence_interval_lower: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence_interval_upper: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sample_size_evaluated: Mapped[int] = mapped_column(Integer, default=0)
    p_value_vs_baseline: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Cryptographic immutability & audit lock
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256
    locked_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_pre_toss: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    punter_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    match: Mapped["Match"] = relationship("Match", back_populates="predictions")
    predicted_team: Mapped[Optional["Team"]] = relationship("Team")
    evaluation: Mapped[Optional["PredictionEvaluation"]] = relationship(
        "PredictionEvaluation",
        back_populates="prediction_snapshot",
        uselist=False,
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_pred_match_id", "match_id"),
        Index("idx_pred_hash", "snapshot_hash"),
        Index("idx_pred_source", "source_channel"),
    )


# 6. Post-Toss Evaluations
class PredictionEvaluation(Base):
    """
    Evaluation record created strictly after official toss ground truth is confirmed.
    Evaluates prediction accuracy, Brier score, and deviation from fair 50/50 baseline.
    """
    __tablename__ = "prediction_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("prediction_snapshots.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    actual_toss_winner_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False)
    predicted_team_id: Mapped[Optional[str]] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    
    # Outcomes: True (Hit/Win), False (Miss/Loss), None (Skipped / No Action)
    is_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    brier_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    evaluated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    prediction_snapshot: Mapped["PredictionSnapshot"] = relationship("PredictionSnapshot", back_populates="evaluation")
    actual_toss_winner: Mapped["Team"] = relationship("Team", foreign_keys=[actual_toss_winner_id])


# 7. Telegram Tipster & Channel Records (Audited)
class TelegramChannelRecord(Base):
    """Audited messages from Telegram toss tipster channels."""
    __tablename__ = "telegram_channel_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_name: Mapped[str] = mapped_column(String(100), nullable=False)
    channel_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    message_id: Mapped[str] = mapped_column(String(100), nullable=False)
    post_timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edit_timestamp_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_message_text: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_team_id: Mapped[Optional[str]] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    extracted_match_id: Mapped[Optional[str]] = mapped_column(ForeignKey("matches.id", ondelete="SET NULL"), nullable=True)
    is_pre_toss: Mapped[bool] = mapped_column(Boolean, default=True)
    message_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    audit_status: Mapped[str] = mapped_column(
        String(50),
        default="VERIFIED_PRE_TOSS"
    )  # VERIFIED_PRE_TOSS, POST_TOSS_EDITED, AMBIGUOUS, DUPLICATE, UNVERIFIED
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ingested_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("channel_name", "message_id", name="uq_channel_message"),
        Index("idx_tg_channel_audit", "channel_name", "audit_status"),
    )

