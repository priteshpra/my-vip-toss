"""SQLAlchemy 2.0 Declarative Database Models for Toss Analytics & Tracking Admin Panel.

Strictly enforces:
- Append-only immutability for Prediction and SourceSnapshot (no updates/deletions).
- Comprehensive tracking for Leagues, Teams, Venues, Matches, Toss Results, Markets, Sources, and Audits.
"""

from datetime import datetime, date
from typing import Optional, List
from sqlalchemy import (
    String, Integer, Float, Boolean, Text, DateTime, Date, ForeignKey, Index, UniqueConstraint, event
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base, utc_now


# --------------------------------------------------------------------------
# 1. League, Team, Venue Entities
# --------------------------------------------------------------------------
class League(Base):
    """Cricket League / Tournament / Series."""
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)
    aliases: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON or comma-delimited
    format: Mapped[str] = mapped_column(String(20), default="T20")  # T20, ODI, TEST, T10, etc.

    matches: Mapped[List["Match"]] = relationship("Match", back_populates="league")


class Team(Base):
    """Cricket Team."""
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)
    aliases: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON or comma-delimited
    short_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class Venue(Base):
    """Cricket Venue / Ground."""
    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    matches: Mapped[List["Match"]] = relationship("Match", back_populates="venue")


# --------------------------------------------------------------------------
# 2. Match & Confirmed Toss Result
# --------------------------------------------------------------------------
class Match(Base):
    """Scheduled or past cricket match fixture."""
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id", ondelete="RESTRICT"), nullable=False, index=True)
    team_a_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False, index=True)
    team_b_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False, index=True)
    venue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("venues.id", ondelete="SET NULL"), nullable=True, index=True)
    start_time_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="upcoming", index=True)  # upcoming, live, toss_done, delayed, abandoned
    source_match_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    gaming_helper_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    odds_a: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    odds_b: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    @property
    def scheduled_start_utc(self) -> datetime:
        return self.start_time_utc

    @scheduled_start_utc.setter
    def scheduled_start_utc(self, value: datetime) -> None:
        self.start_time_utc = value

    @property
    def match_title(self) -> str:
        t_a = self.team_a.name if self.team_a else str(self.team_a_id)
        t_b = self.team_b.name if self.team_b else str(self.team_b_id)
        return f"{t_a} vs {t_b}"

    @property
    def countdown_seconds(self) -> int:
        now = utc_now()
        start = self.start_time_utc
        if start.tzinfo is None:
            from datetime import timezone
            start = start.replace(tzinfo=timezone.utc)
        return int((start - now).total_seconds())

    @property
    def countdown_display(self) -> str:
        diff = self.countdown_seconds
        if diff <= 0:
            abs_diff = abs(diff)
            mins = abs_diff // 60
            if mins < 1:
                return "Starting now (Toss Pending)"
            return f"Started {mins}m ago (Toss Pending)"
        mins = diff // 60
        hrs = mins // 60
        rem_mins = mins % 60
        if hrs > 0:
            return f"Starts in {hrs}h {rem_mins}m"
        return f"Starts in {rem_mins}m"

    # Relationships
    league: Mapped["League"] = relationship("League", back_populates="matches")
    team_a: Mapped["Team"] = relationship("Team", foreign_keys=[team_a_id])
    team_b: Mapped["Team"] = relationship("Team", foreign_keys=[team_b_id])
    venue: Mapped[Optional["Venue"]] = relationship("Venue", back_populates="matches")
    toss_result: Mapped[Optional["TossResult"]] = relationship("TossResult", back_populates="match", uselist=False, cascade="all, delete-orphan")
    predictions: Mapped[List["Prediction"]] = relationship("Prediction", back_populates="match", cascade="all, delete-orphan")
    market_entries: Mapped[List["MarketEntry"]] = relationship("MarketEntry", back_populates="match", cascade="all, delete-orphan")


class TossResult(Base):
    """Ground truth official toss outcome."""
    __tablename__ = "toss_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    winner_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)  # bat, field
    source: Mapped[str] = mapped_column(String(50), default="manual")  # telegram, manual, cricbuzz, api
    telegram_message_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True, index=True)
    received_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    official_source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    @property
    def toss_winner_id(self) -> int:
        return self.winner_team_id

    @toss_winner_id.setter
    def toss_winner_id(self, val: int) -> None:
        self.winner_team_id = val

    @property
    def toss_decision(self) -> str:
        return self.decision

    @toss_decision.setter
    def toss_decision(self, val: str) -> None:
        self.decision = val

    @property
    def toss_winner(self) -> Optional["Team"]:
        return self.winner_team

    @property
    def toss_winner_id(self) -> int:
        return self.winner_team_id

    @property
    def toss_decision(self) -> str:
        return self.decision

    @property
    def verified_at_utc(self) -> datetime:
        return self.received_at_utc

    match: Mapped["Match"] = relationship("Match", back_populates="toss_result")
    winner_team: Mapped["Team"] = relationship("Team", foreign_keys=[winner_team_id])


# --------------------------------------------------------------------------
# 3. Prediction & Outcome (IMMUTABLE & APPEND-ONLY)
# --------------------------------------------------------------------------
class Prediction(Base):
    """
    Append-only pre-toss prediction record.
    Strictly immutable: updates and deletions are blocked by ORM guards and triggers.
    """
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    lean: Mapped[str] = mapped_column(String(20), default="NO_EDGE")  # TeamA, TeamB, NO_EDGE
    predicted_team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ci_low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ci_high: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    locked_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # SHA-256 seal
    source: Mapped[str] = mapped_column(String(50), default="own")  # own, manual, telegram, etc.
    is_pre_toss: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    punter_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    @property
    def snapshot_hash(self) -> str:
        return self.hash

    @snapshot_hash.setter
    def snapshot_hash(self, value: str) -> None:
        self.hash = value

    @property
    def action_type(self) -> str:
        return "SKIP" if self.lean == "NO_EDGE" else "PLAY"

    @property
    def evaluation(self) -> Optional["PredictionOutcome"]:
        return self.outcome

    @property
    def source_channel(self) -> str:
        return self.source

    @property
    def is_locked(self) -> bool:
        return bool(self.hash)

    @property
    def assessment_status(self) -> str:
        return "PRE_TOSS_LOCKED" if self.is_pre_toss else "POST_START_DISQUALIFIED"

    match: Mapped["Match"] = relationship("Match", back_populates="predictions")
    predicted_team: Mapped[Optional["Team"]] = relationship("Team")
    outcome: Mapped[Optional["PredictionOutcome"]] = relationship("PredictionOutcome", back_populates="prediction", uselist=False, cascade="all, delete-orphan")


class PredictionOutcome(Base):
    """Evaluated outcome of a pre-toss prediction against ground truth toss."""
    __tablename__ = "prediction_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    result: Mapped[str] = mapped_column(String(20), nullable=False)  # PASS, FAIL, SKIPPED
    brier_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    evaluated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    @property
    def is_correct(self) -> Optional[bool]:
        if self.result == "PASS":
            return True
        elif self.result == "FAIL":
            return False
        return None

    prediction: Mapped["Prediction"] = relationship("Prediction", back_populates="outcome")


# --------------------------------------------------------------------------
# 4. Market Entry (APPEND-ONLY)
# --------------------------------------------------------------------------
class MarketEntry(Base):
    """Append-only market intelligence, load percentage, and odds snapshot."""
    __tablename__ = "market_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    team_lean: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    load_market: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    odds_a: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    odds_b: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fetched_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)

    match: Mapped["Match"] = relationship("Match", back_populates="market_entries")


# --------------------------------------------------------------------------
# 5. External Source & Source Snapshot (APPEND-ONLY) & Source Outcome
# --------------------------------------------------------------------------
class ExternalSource(Base):
    """External provider, API, web scraper, or Telegram tipster channel."""
    __tablename__ = "external_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)
    url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # public_html, json_api, manual, telegram_channel
    parse_rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON parse rules
    poll_interval: Mapped[int] = mapped_column(Integer, default=300)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    health: Mapped[str] = mapped_column(String(30), default="HEALTHY")  # HEALTHY, DEGRADED, OFFLINE

    snapshots: Mapped[List["SourceSnapshot"]] = relationship("SourceSnapshot", back_populates="source_ref")


class SourceSnapshot(Base):
    """Append-only snapshot from an external source or tipster."""
    __tablename__ = "source_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("external_sources.id", ondelete="RESTRICT"), nullable=False, index=True)
    match_id: Mapped[Optional[int]] = mapped_column(ForeignKey("matches.id", ondelete="SET NULL"), nullable=True, index=True)
    lean: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    market: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    session_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fetched_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)

    source_ref: Mapped["ExternalSource"] = relationship("ExternalSource", back_populates="snapshots")
    outcome: Mapped[Optional["SourceOutcome"]] = relationship("SourceOutcome", back_populates="snapshot", uselist=False, cascade="all, delete-orphan")


class SourceOutcome(Base):
    """Outcome grading of an external source snapshot."""
    __tablename__ = "source_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("source_snapshots.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    result: Mapped[str] = mapped_column(String(20), nullable=False)  # PASS, FAIL
    evaluated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    snapshot: Mapped["SourceSnapshot"] = relationship("SourceSnapshot", back_populates="outcome")


# --------------------------------------------------------------------------
# 6. Telegram Review, Admin, Audit & Collector Health
# --------------------------------------------------------------------------
class TelegramReview(Base):
    """Audited raw messages from monitored Telegram tipster channels."""
    __tablename__ = "telegram_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDING", index=True)  # PENDING, APPROVED, REJECTED, PROCESSED
    channel_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    extracted_data_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AdminUser(Base):
    """Administrator user credentials and permissions."""
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditLog(Base):
    """System-wide immutable security and administrative audit log."""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)  # RECORD_TOSS, LOCK_PREDICTION, ADMIN_LOGIN, SYNC
    entity: Mapped[str] = mapped_column(String(100), nullable=False)  # Match, Prediction, Source
    entity_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    user: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class CollectorHealth(Base):
    """Real-time health status and response times of collectors and scrapers."""
    __tablename__ = "collector_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="OK", index=True)  # OK, ERROR, DEGRADED
    last_run_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_success_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class HistoricalMatchStatistic(Base):
    """Longitudinal historical cricket toss outcomes for baseline reference."""
    __tablename__ = "historical_match_statistics"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    match_date: Mapped[date] = mapped_column(Date, nullable=False)
    tournament_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    match_format: Mapped[str] = mapped_column(String(20), default="T20")
    team_a_name: Mapped[str] = mapped_column(String(200), nullable=False)
    team_b_name: Mapped[str] = mapped_column(String(200), nullable=False)
    venue_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    toss_winner_name: Mapped[str] = mapped_column(String(200), nullable=False)
    toss_decision: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    match_winner_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    captain_a: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    captain_b: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    toss_winner_captain: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="CRICSHEET")


# --------------------------------------------------------------------------
# 7. Immutability Event Listeners (ORM Guards)
# --------------------------------------------------------------------------
class ReadOnlyError(Exception):
    """Raised when an attempt is made to mutate or delete an immutable entity."""
    pass


@event.listens_for(Prediction, "before_update")
def _prevent_prediction_update(mapper, connection, target):
    raise ReadOnlyError("Prediction records are cryptographically sealed and append-only. Updates are forbidden.")


@event.listens_for(Prediction, "before_delete")
def _prevent_prediction_delete(mapper, connection, target):
    raise ReadOnlyError("Prediction records are cryptographically sealed and append-only. Deletions are forbidden.")


@event.listens_for(SourceSnapshot, "before_update")
def _prevent_source_snapshot_update(mapper, connection, target):
    raise ReadOnlyError("SourceSnapshot records are immutable and append-only. Updates are forbidden.")


@event.listens_for(SourceSnapshot, "before_delete")
def _prevent_source_snapshot_delete(mapper, connection, target):
    raise ReadOnlyError("SourceSnapshot records are immutable and append-only. Deletions are forbidden.")


# Backwards-compatible aliases for schemas and existing query helpers
PredictionSnapshot = Prediction
PredictionEvaluation = PredictionOutcome
Tournament = League
MarketSnapshot = MarketEntry
DataSource = ExternalSource
SourceUpdateLog = CollectorHealth
