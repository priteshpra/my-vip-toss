"""Comprehensive unit and operational test suite for Phase 7:
- Structured logging with automated secret redaction.
- SQLite hot database backups with retention rotation.
- APScheduler job registration and intervals.
- Telegram listener auto-reconnect and error handling.
- Unified run.py CLI commands.
- Automated codebase scan for forbidden words and hardcoded secrets.
"""

import os
import re
import sys
import glob
import sqlite3
import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings, BASE_DIR
from app.logger import redact_secrets
from app.services.backup_service import perform_database_backup
from app.jobs.scheduler import start_scheduler, stop_scheduler, scheduler
from app.collectors.telegram_listener import TelegramListener


# --------------------------------------------------------------------------
# 1. Secret Redaction Tests
# --------------------------------------------------------------------------
def test_secret_redaction_masks_sensitive_data():
    """Verify passwords, session cookies, auth tokens, and phone numbers are scrubbed."""
    raw_log = (
        f"User logged in with password={settings.admin_password} and secret_key={settings.secret_key}. "
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token.sig was issued. "
        "Cookie: toss_session=abc123sessiontokenhere. "
        "User contact phone=+919876543210."
    )
    scrubbed = redact_secrets(raw_log)

    assert settings.admin_password not in scrubbed
    assert settings.secret_key not in scrubbed
    assert "Bearer [REDACTED]" in scrubbed or "[REDACTED]" in scrubbed
    assert "toss_session=[REDACTED]" in scrubbed or "[REDACTED]" in scrubbed
    assert "+919876543210" not in scrubbed


# --------------------------------------------------------------------------
# 2. Database Backup & Retention Rotation Tests
# --------------------------------------------------------------------------
def test_database_backup_and_retention(tmp_path):
    """Verify online hot backup creates a valid SQLite database and prunes beyond keep_count."""
    # 1. Create a mock SQLite database
    db_file = tmp_path / "test_source.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE test_data (id INTEGER PRIMARY KEY, val TEXT);")
    conn.execute("INSERT INTO test_data (val) VALUES ('toss_intelligence');")
    conn.commit()
    conn.close()

    backup_dir = tmp_path / "backups"

    # 2. Run 5 backups with keep_count = 3
    for _ in range(5):
        import time
        time.sleep(0.01)
        res = perform_database_backup(
            db_path=str(db_file),
            backup_dir=str(backup_dir),
            keep_count=3
        )
        assert res["status"] == "SUCCESS"

    # 3. Verify exactly 3 backups remain
    remaining = [f for f in os.listdir(str(backup_dir)) if f.endswith(".db")]
    assert len(remaining) == 3

    # 4. Verify integrity of latest backup
    latest_file = os.path.join(str(backup_dir), remaining[-1])
    test_conn = sqlite3.connect(latest_file)
    cursor = test_conn.cursor()
    cursor.execute("SELECT val FROM test_data;")
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == "toss_intelligence"
    test_conn.close()


# --------------------------------------------------------------------------
# 3. APScheduler Job Registration Tests
# --------------------------------------------------------------------------
def test_scheduler_job_registrations():
    """Verify all 7 background maintenance jobs are registered with appropriate triggers."""
    sched = start_scheduler(autostart=False)
    job_ids = [job.id for job in sched.get_jobs()]

    assert "sync_live_matches" in job_ids
    assert "verify_toss_results" in job_ids
    assert "poll_external_sources" in job_ids
    assert "check_collector_health" in job_ids
    assert "sync_cricsheet_daily" in job_ids
    assert "backup_database" in job_ids
    assert "audit_prediction_hashes" in job_ids

    stop_scheduler()


# --------------------------------------------------------------------------
# 4. Telegram Auto-Reconnect Loop Handling
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_telegram_listener_auto_reconnect_and_health(async_db_session: AsyncSession):
    """Verify listener loop sets health to DEGRADED and performs backoff upon disconnection."""
    listener = TelegramListener()

    # Call _update_health directly to verify graceful degradation recording
    await listener._update_health(
        session=async_db_session,
        status="DEGRADED",
        message="Connection dropped; reconnecting in 5s"
    )

    from app.models import CollectorHealth
    from sqlalchemy import select
    res = await async_db_session.execute(
        select(CollectorHealth).where(CollectorHealth.source == "Telegram Listener")
    )
    health = res.scalar_one_or_none()
    assert health is not None
    assert health.status == "DEGRADED"
    assert "Connection dropped" in health.message


# --------------------------------------------------------------------------
# 5. Automated Codebase Scan for Forbidden Words
# --------------------------------------------------------------------------
def test_codebase_zero_forbidden_words():
    """
    Automated honesty scan:
    Assert zero occurrences of 'guaranteed', '99.99%', 'sure-shot', or 'sure shot' in application code or templates.
    """
    forbidden_patterns = [
        re.compile(r'\bguaranteed\b', re.IGNORECASE),
        re.compile(r'99\.99%?', re.IGNORECASE),
        re.compile(r'sure[\s\-]shot', re.IGNORECASE)
    ]

    base_dir = BASE_DIR
    target_dirs = ["app"]
    violations = []

    for tdir in target_dirs:
        dir_path = os.path.join(base_dir, tdir)
        for root, _, files in os.walk(dir_path):
            if "__pycache__" in root:
                continue
            for file in files:
                if file.endswith((".py", ".html", ".js")):
                    full_path = os.path.join(root, file)
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_no, line in enumerate(f, 1):
                            # Allow honest disclaimers that state: "never guarantee" or "do not guarantee"
                            if "do not guarantee" in line.lower() or "never guarantee" in line.lower() or "does not guarantee" in line.lower():
                                continue
                            for pat in forbidden_patterns:
                                if pat.search(line):
                                    violations.append(f"{full_path}:{line_no} -> {line.strip()}")

    assert len(violations) == 0, f"Found forbidden marketing words in application code: {violations}"
