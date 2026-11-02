"""Structured logging configuration with automated secret redaction.

Strictly enforces:
- Automatic masking of passwords, API tokens, hashes, session cookies, and phone numbers.
- Redaction applies across all console and file log outputs.
- Compatible with Loguru structured formatting.
"""

import os
import re
import sys
from typing import List, Pattern, Optional
from loguru import logger
from app.config import settings, BASE_DIR

# Sensitive parameter keys to scrub from logs
SENSITIVE_PATTERNS: List[Pattern] = [
    re.compile(r'(?i)(password|passwd|pwd|secret|api_hash|bot_token|token|session|auth|csrf_token)\s*[:=]\s*["\']?([^"\'\s,;]+)["\']?'),
    re.compile(r'(?i)(bearer\s+)([A-Za-z0-9\-_\.]+)'),
    re.compile(r'(?i)(cookie\s*:\s*.*?toss_session=)([A-Za-z0-9\-_\.]+)'),
    re.compile(r'(\+?[0-9]{2,3}[-\s]?[0-9]{5}[-\s]?[0-9]{5})'),  # 10-12 digit phone numbers
]


def redact_secrets(text: str) -> str:
    """Scrub sensitive credentials, passwords, tokens, and phone numbers from string."""
    if not text:
        return ""

    scrubbed = str(text)

    # 1. Exact match replacements for known config secrets
    known_secrets = [
        settings.secret_key,
        settings.admin_password,
        settings.gaming_helper_password,
        settings.telegram_api_hash,
        settings.telegram_bot_token,
    ]
    for sec in known_secrets:
        if sec and len(sec) >= 6 and sec in scrubbed:
            scrubbed = scrubbed.replace(sec, "[REDACTED]")

    # 2. Regex pattern masking
    scrubbed = re.sub(r'\+?[0-9]{10,14}', '[REDACTED]', scrubbed)
    scrubbed = re.sub(r'(?i)(password|passwd|pwd|secret|api_hash|bot_token|token|session|auth|csrf_token)\s*[:=]\s*["\']?([^"\'\s,;]+)["\']?', r'\1=[REDACTED]', scrubbed)
    scrubbed = re.sub(r'(?i)bearer\s+[A-Za-z0-9\-_\.]+', 'Bearer [REDACTED]', scrubbed)
    scrubbed = re.sub(r'(?i)toss_session=[A-Za-z0-9\-_\.]+', 'toss_session=[REDACTED]', scrubbed)

    return scrubbed


def secret_redaction_sink_filter(record) -> bool:
    """Filter that mutates log record in-place to redact secrets."""
    record["message"] = redact_secrets(record["message"])
    return True


def setup_logging(log_level: Optional[str] = None) -> None:
    """
    Configure Loguru logger with secret-redacting sinks for stdout and file.
    """
    level = log_level or settings.log_level.upper()
    logger.remove()

    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    # Console sink with redaction filter
    logger.add(
        sys.stderr,
        format=log_format,
        level=level,
        filter=secret_redaction_sink_filter,
        colorize=True
    )

    # Optional file sink in logs/app.log (gracefully fallback to console if file access is restricted)
    try:
        logs_dir = os.path.join(BASE_DIR, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        file_path = os.path.join(logs_dir, "app.log")

        logger.add(
            file_path,
            format=log_format,
            level=level,
            filter=secret_redaction_sink_filter,
            rotation="10 MB",
            retention="14 days",
            compression="zip",
            encoding="utf-8"
        )
    except Exception as exc:
        logger.warning(f"File log sink could not be initialized ({exc}); using stderr sink only.")

    logger.info("Structured logging initialized with active credential redaction.")
