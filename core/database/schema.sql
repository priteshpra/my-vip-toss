-- ==============================================================================
-- TossIQ Database Schema (DDL)
-- ==============================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- 1. Data Sources and Ingestion Logs
CREATE TABLE IF NOT EXISTS data_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider_type TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    base_url TEXT,
    rate_limit_per_minute INTEGER DEFAULT 60,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_update_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    status_code INTEGER,
    response_time_ms INTEGER,
    records_fetched INTEGER DEFAULT 0,
    is_success INTEGER NOT NULL,
    error_message TEXT,
    logged_at_ist TIMESTAMP NOT NULL,
    FOREIGN KEY (source_id) REFERENCES data_sources(id) ON DELETE CASCADE
);

-- 2. Core Entities: Tournaments, Venues, Teams
CREATE TABLE IF NOT EXISTS tournaments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    format TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS venues (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT,
    country TEXT
);

CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    short_code TEXT NOT NULL,
    category TEXT DEFAULT 'OTHER',
    aliases TEXT
);

-- 3. Daily Match Centre (IST Asia/Kolkata)
CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY,
    external_provider_id TEXT,
    tournament_id TEXT NOT NULL,
    team_a_id TEXT NOT NULL,
    team_b_id TEXT NOT NULL,
    venue_id TEXT,
    match_format TEXT NOT NULL,
    scheduled_start_ist TIMESTAMP NOT NULL,
    expected_toss_ist TIMESTAMP,
    status TEXT CHECK(status IN (
        'UPCOMING', 'TOSS_PENDING', 'TOSS_COMPLETED',
        'DELAYED', 'ABANDONED', 'RESULT_UNAVAILABLE'
    )) NOT NULL DEFAULT 'UPCOMING',
    data_source_id TEXT NOT NULL,
    last_updated_ist TIMESTAMP NOT NULL,
    data_freshness_status TEXT DEFAULT 'FRESH',
    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE RESTRICT,
    FOREIGN KEY (team_a_id) REFERENCES teams(id) ON DELETE RESTRICT,
    FOREIGN KEY (team_b_id) REFERENCES teams(id) ON DELETE RESTRICT,
    FOREIGN KEY (venue_id) REFERENCES venues(id) ON DELETE SET NULL,
    FOREIGN KEY (data_source_id) REFERENCES data_sources(id) ON DELETE RESTRICT
);

-- 4. Confirmed Official Toss Results (Ground Truth)
CREATE TABLE IF NOT EXISTS toss_results (
    match_id TEXT PRIMARY KEY,
    toss_winner_id TEXT NOT NULL,
    toss_decision TEXT CHECK(toss_decision IN ('bat', 'bowl', 'unknown')) NOT NULL,
    match_winner_id TEXT,
    official_source_url TEXT,
    source_provider_id TEXT NOT NULL,
    verified_at_ist TIMESTAMP NOT NULL,
    verification_status TEXT CHECK(verification_status IN ('VERIFIED', 'UNVERIFIED', 'DISPUTED')) DEFAULT 'VERIFIED',
    notes TEXT,
    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
    FOREIGN KEY (toss_winner_id) REFERENCES teams(id) ON DELETE RESTRICT,
    FOREIGN KEY (match_winner_id) REFERENCES teams(id) ON DELETE SET NULL
);

-- 5. Historical Toss Outcomes (Longitudinal Sample)
CREATE TABLE IF NOT EXISTS historical_match_statistics (
    id TEXT PRIMARY KEY,
    match_date DATE NOT NULL,
    tournament_id TEXT,
    match_format TEXT NOT NULL,
    team_a_id TEXT NOT NULL,
    team_b_id TEXT NOT NULL,
    venue_id TEXT,
    toss_winner_id TEXT NOT NULL,
    toss_decision TEXT,
    source TEXT NOT NULL,
    FOREIGN KEY (team_a_id) REFERENCES teams(id) ON DELETE RESTRICT,
    FOREIGN KEY (team_b_id) REFERENCES teams(id) ON DELETE RESTRICT
);

-- 6. Public Market Intelligence (Immutable Odds Snapshots)
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    bookmaker TEXT NOT NULL,
    market_type TEXT NOT NULL,
    team_a_odds REAL NOT NULL,
    team_b_odds REAL NOT NULL,
    team_a_implied_prob REAL NOT NULL,
    team_b_implied_prob REAL NOT NULL,
    margin_percentage REAL NOT NULL,
    snapshot_timestamp_ist TIMESTAMP NOT NULL,
    is_pre_toss INTEGER NOT NULL DEFAULT 1,
    is_stale INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
    FOREIGN KEY (provider_id) REFERENCES data_sources(id) ON DELETE RESTRICT
);

-- 7. Model Versions
CREATE TABLE IF NOT EXISTS model_versions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    parameters_json TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8. Immutable Pre-Toss Prediction Snapshots (Zero In-Place Overwrites)
CREATE TABLE IF NOT EXISTS prediction_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT NOT NULL,
    model_version_id TEXT NOT NULL,
    assessment_status TEXT CHECK(assessment_status IN (
        'INSUFFICIENT_DATA',
        'NO_VALIDATED_ADVANTAGE',
        'PRE_TOSS_ESTIMATE_AVAILABLE',
        'TOSS_COMPLETED'
    )) NOT NULL,
    team_a_prob REAL,
    team_b_prob REAL,
    confidence_interval_lower REAL,
    confidence_interval_upper REAL,
    supporting_evidence_json TEXT,
    contradicting_evidence_json TEXT,
    uncertainty_warnings TEXT,
    sample_size_evaluated INTEGER DEFAULT 0,
    snapshot_timestamp_ist TIMESTAMP NOT NULL,
    is_locked INTEGER DEFAULT 1,
    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
    FOREIGN KEY (model_version_id) REFERENCES model_versions(id) ON DELETE RESTRICT,
    UNIQUE(match_id, model_version_id, snapshot_timestamp_ist)
);

-- 9. Post-Toss Prediction Evaluations (Immutable Evaluation Log)
CREATE TABLE IF NOT EXISTS prediction_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_snapshot_id INTEGER NOT NULL UNIQUE,
    match_id TEXT NOT NULL,
    actual_toss_winner_id TEXT NOT NULL,
    predicted_favored_id TEXT,
    is_correct INTEGER,
    brier_score REAL,
    log_loss REAL,
    evaluated_at_ist TIMESTAMP NOT NULL,
    FOREIGN KEY (prediction_snapshot_id) REFERENCES prediction_snapshots(id) ON DELETE CASCADE,
    FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
    FOREIGN KEY (actual_toss_winner_id) REFERENCES teams(id) ON DELETE RESTRICT
);

-- 10. Local Telegram Research Records (Strictly Deterministic & Local)
CREATE TABLE IF NOT EXISTS telegram_channel_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_name TEXT NOT NULL,
    channel_id TEXT,
    message_id TEXT NOT NULL,
    post_timestamp_ist TIMESTAMP NOT NULL,
    edit_timestamp_ist TIMESTAMP,
    raw_message_text TEXT NOT NULL,
    extracted_team_id TEXT,
    extracted_match_id TEXT,
    is_pre_toss INTEGER NOT NULL,
    audit_status TEXT CHECK(audit_status IN (
        'VERIFIED_PRE_TOSS', 'POST_TOSS_EDITED', 'AMBIGUOUS', 'DUPLICATE', 'UNVERIFIED'
    )) NOT NULL,
    notes TEXT,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (extracted_match_id) REFERENCES matches(id) ON DELETE SET NULL,
    FOREIGN KEY (extracted_team_id) REFERENCES teams(id) ON DELETE SET NULL,
    UNIQUE(channel_name, message_id)
);

-- Performance & Integrity Indexes
CREATE INDEX IF NOT EXISTS idx_matches_start ON matches(scheduled_start_ist);
CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);
CREATE INDEX IF NOT EXISTS idx_pred_match ON prediction_snapshots(match_id);
CREATE INDEX IF NOT EXISTS idx_market_match ON market_snapshots(match_id, snapshot_timestamp_ist);
CREATE INDEX IF NOT EXISTS idx_hist_teams ON historical_match_statistics(team_a_id, team_b_id);
CREATE INDEX IF NOT EXISTS idx_tg_channel_msg ON telegram_channel_records(channel_name, message_id);
