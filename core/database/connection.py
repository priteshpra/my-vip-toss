"""SQLite Connection Factory and Database Initialization."""

import sqlite3
import os
import shutil
from datetime import datetime
from typing import Optional
from config import settings
from core.timezone import now_ist, format_ist


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Return a configured SQLite connection with WAL mode and foreign keys enabled."""
    target_path = db_path or settings.abs_database_path
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)

    conn = sqlite3.connect(target_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize the database schema and default system records."""
    schema_file = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_file, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn = get_connection(db_path)
    with conn:
        conn.executescript(schema_sql)

        # Seed default data sources
        sources = [
            ("cricket_data_org", "CricketData.org REST API", "CRICKET_API", "https://api.cricapi.com/v1"),
            ("the_odds_api", "The Odds API Public Odds", "ODDS_API", "https://api.the-odds-api.com/v4"),
            ("telegram_local", "Local Telegram Audit Adapter", "TELEGRAM", None),
            ("synthetic_provider", "Deterministic Synthetic Provider", "LOCAL", None),
            ("cricbuzz_live", "Cricbuzz Live Fixtures Feed", "CRICKET_API", "https://www.cricbuzz.com"),
        ]
        for src_id, name, p_type, base_url in sources:
            conn.execute("""
                INSERT OR IGNORE INTO data_sources (id, name, provider_type, base_url)
                VALUES (?, ?, ?, ?)
            """, (src_id, name, p_type, base_url))

        # Seed initial model versions
        models = [
            (
                "v1.0_bernoulli_baseline",
                "Independent Bernoulli Baseline",
                "Evaluates two-team coin tosses against strict 50% baseline with Wilson score intervals."
            ),
            (
                "v1.1_beta_binomial",
                "Empirical Bayes Beta-Binomial",
                "Incorporates sample size dampening with uninformative Beta(1,1) prior to avoid small-sample bias."
            )
        ]
        for m_id, m_name, m_desc in models:
            conn.execute("""
                INSERT OR IGNORE INTO model_versions (id, name, description)
                VALUES (?, ?, ?)
            """, (m_id, m_name, m_desc))

    conn.close()


def backup_db(db_path: Optional[str] = None, backup_dir: Optional[str] = None) -> str:
    """Perform a live, consistent SQLite backup to the backup directory."""
    src_path = db_path or settings.abs_database_path
    target_dir = backup_dir or settings.abs_backup_dir
    os.makedirs(target_dir, exist_ok=True)

    timestamp_str = now_ist().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(target_dir, f"toss_iq_backup_{timestamp_str}.db")

    src_conn = get_connection(src_path)
    bck_conn = sqlite3.connect(backup_file)
    with bck_conn:
        src_conn.backup(bck_conn)
    bck_conn.close()
    src_conn.close()

    return backup_file


# Type alias
get_db_connection = get_connection
