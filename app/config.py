"""Application configuration and settings."""

import os
from typing import Optional
from zoneinfo import ZoneInfo
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Settings(BaseSettings):
    """Production application settings with environment variable support."""

    environment: str = Field(default="development", description="Environment mode (development/production/testing)")
    log_level: str = Field(default="INFO", description="Logging verbosity")

    # Database
    database_url: str = Field(
        default="sqlite+aiosqlite:///./toss_analytics.db",
        description="Async SQLAlchemy database URL"
    )
    sync_database_url: str = Field(
        default="sqlite:///./toss_analytics.db",
        description="Sync database URL for migrations / inspect"
    )

    # Timezones
    app_timezone: str = Field(default="Asia/Kolkata", description="Target display timezone")

    # Security & Admin Auth
    admin_username: str = Field(default="admin", description="Admin login username")
    admin_password: str = Field(default="admin_dev_pass_2026", description="Admin login password")
    secret_key: str = Field(default="dev_secret_key_random_hash_a9f823bc87e102d991bce987", description="Session signing key")
    session_cookie_name: str = "toss_session"

    # External Provider Configs
    gaming_helper_phone: Optional[str] = None
    gaming_helper_password: Optional[str] = None
    gaming_helper_enabled: bool = False

    telegram_api_id: Optional[str] = None
    telegram_api_hash: Optional[str] = None
    telegram_phone: Optional[str] = None
    telegram_channels: Optional[str] = None
    telegram_enabled: bool = False

    # Scheduler & Polling
    match_poll_interval: int = 300
    toss_poll_interval: int = 60

    # Statistical Thresholds & Honesty Gates
    min_historical_sample_size: int = 30
    significance_alpha: float = 0.05

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def resolved_database_url(self) -> str:
        """Resolve relative SQLite path to absolute path."""
        if self.database_url.startswith("sqlite+aiosqlite:///./"):
            rel = self.database_url.replace("sqlite+aiosqlite:///./", "")
            abs_p = os.path.join(BASE_DIR, rel).replace("\\", "/")
            return f"sqlite+aiosqlite:///{abs_p}"
        return self.database_url

    @property
    def resolved_sync_database_url(self) -> str:
        """Resolve relative sync SQLite path to absolute path."""
        if self.sync_database_url.startswith("sqlite:///./"):
            rel = self.sync_database_url.replace("sqlite:///./", "")
            abs_p = os.path.join(BASE_DIR, rel).replace("\\", "/")
            return f"sqlite:///{abs_p}"
        return self.sync_database_url

    @property
    def tz(self) -> ZoneInfo:
        """Returns ZoneInfo object for application display timezone."""
        return ZoneInfo(self.app_timezone)


settings = Settings()
