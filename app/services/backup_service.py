"""Automated database backup service.

Strictly manages:
- Online SQLite hot backup using sqlite3.Connection.backup() API (lock-safe).
- Timestamped backup retention and automatic rotation (retaining last N backups).
- Audit logging of backup operations.
"""

import os
import sqlite3
import shutil
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from loguru import logger
from app.config import settings, BASE_DIR


def resolve_sqlite_file_path() -> str:
    """Extract local filesystem path from SQLite database URL."""
    url = settings.sync_database_url
    if ":///" in url:
        raw_path = url.split(":///", 1)[1]
    elif "://" in url:
        raw_path = url.split("://", 1)[1]
    else:
        raw_path = "./toss_analytics.db"

    if not os.path.isabs(raw_path):
        raw_path = os.path.join(BASE_DIR, raw_path.lstrip("./"))
    return os.path.abspath(raw_path)


def perform_database_backup(
    db_path: Optional[str] = None,
    backup_dir: Optional[str] = None,
    keep_count: Optional[int] = None
) -> Dict[str, Any]:
    """
    Execute a safe online hot backup of the SQLite database and rotate old archives.
    """
    target_db = db_path or resolve_sqlite_file_path()
    dest_dir = backup_dir or os.path.join(BASE_DIR, settings.backup_dir)
    max_keep = keep_count or settings.backup_keep_count

    os.makedirs(dest_dir, exist_ok=True)

    if not os.path.exists(target_db):
        msg = f"Database file '{target_db}' does not exist; skipping backup."
        logger.warning(msg)
        return {"status": "SKIPPED", "message": msg, "backup_file": None}

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    backup_filename = f"toss_analytics_backup_{timestamp}.db"
    backup_path = os.path.join(dest_dir, backup_filename)

    try:
        # 1. Use SQLite's online backup API for concurrency safety
        src_conn = sqlite3.connect(target_db, timeout=30.0)
        dst_conn = sqlite3.connect(backup_path)

        with dst_conn:
            src_conn.backup(dst_conn, pages=100)

        dst_conn.close()
        src_conn.close()

        size_bytes = os.path.getsize(backup_path)
        logger.info(f"Database backup created: {backup_filename} ({size_bytes / 1024:.1f} KB)")

        # 2. Prune old backups
        all_backups = sorted(
            [
                os.path.join(dest_dir, f)
                for f in os.listdir(dest_dir)
                if f.startswith("toss_analytics_backup_") and f.endswith(".db")
            ],
            key=os.path.getmtime
        )

        pruned = 0
        while len(all_backups) > max_keep:
            oldest = all_backups.pop(0)
            try:
                os.remove(oldest)
                pruned += 1
            except Exception as pe:
                logger.warning(f"Could not remove old backup '{oldest}': {pe}")

        return {
            "status": "SUCCESS",
            "backup_file": backup_path,
            "filename": backup_filename,
            "size_bytes": size_bytes,
            "total_backups_retained": len(all_backups),
            "pruned_count": pruned
        }

    except Exception as exc:
        logger.error(f"Failed to execute database backup: {exc}")
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except Exception:
                pass
        return {"status": "ERROR", "error": str(exc), "backup_file": None}
