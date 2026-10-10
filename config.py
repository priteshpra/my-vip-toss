"""TossIQ Application Configuration."""

import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    environment: str = "development"
    log_level: str = "INFO"

    # SQLite Storage
    database_path: str = "data/toss_iq.db"
    backup_dir: str = "data/backups"

    # Strict Timezone
    app_timezone: str = "Asia/Kolkata"

    # External Provider Keys (Optional for offline/synthetic testing)
    cricket_api_key: Optional[str] = None
    cricket_api_base_url: str = "https://api.cricapi.com/v1"

    odds_api_key: Optional[str] = None
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"

    # Statistical Thresholds & Gates
    min_historical_sample_size: int = 30
    statistical_significance_alpha: float = 0.05
    max_data_staleness_minutes: int = 60

    # Polling & Automation Intervals
    fixture_poll_interval_seconds: int = 300
    odds_poll_interval_seconds: int = 600

    # Optional Local Telegram Integration
    telegram_api_id: Optional[str] = None
    telegram_api_hash: Optional[str] = None
    telegram_phone: Optional[str] = None
    telegram_authorized_channels: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def abs_database_path(self) -> str:
        """Return absolute path to database file."""
        if os.path.isabs(self.database_path):
            return self.database_path
        base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, self.database_path)

    @property
    def abs_backup_dir(self) -> str:
        """Return absolute path to backup directory."""
        if os.path.isabs(self.backup_dir):
            return self.backup_dir
        base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, self.backup_dir)


settings = Settings()
