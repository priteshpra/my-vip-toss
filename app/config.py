"""Application configuration and typed settings."""

import os
from typing import Optional
from zoneinfo import ZoneInfo
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Settings(BaseSettings):
    """Production application settings with environment variable support and strict typing."""

    BASE_DIR: str = Field(default=BASE_DIR, description="Root directory of workspace")
    environment: str = Field(default="development", description="Environment mode")
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
    session_expire_minutes: int = Field(default=120, description="Session idle timeout in minutes")
    session_cookie_secure: bool = Field(default=False, description="Enforce Secure flag on cookies")
    login_max_failed_attempts: int = Field(default=5, description="Max failed login attempts before lockout")
    login_lockout_duration_seconds: int = Field(default=900, description="Lockout duration in seconds")
    csrf_cookie_name: str = "toss_csrf"

    # GamingHelper Online Ingestion Config
    gaming_helper_base_url: str = Field(
        default="https://gaminglive.gaminghelperonline.com/api",
        description="Base API URL for GamingHelper"
    )
    gaming_helper_login_url: str = Field(
        default="https://gaminglive.gaminghelperonline.com/api/users/login",
        description="Login URL for GamingHelper"
    )
    gaming_helper_live_url: str = Field(
        default="https://gaminglive.gaminghelperonline.com/api/live-bet",
        description="Live bets endpoint for GamingHelper"
    )
    gaming_helper_phone: Optional[str] = None
    gaming_helper_password: Optional[str] = None
    gaming_helper_enabled: bool = False
    gaming_helper_timeout: float = Field(default=15.0, description="HTTP timeout for GamingHelper calls")
    gaming_helper_max_retries: int = Field(default=3, description="Max retries for transient errors")
    gaming_helper_backoff_factor: float = Field(default=1.5, description="Exponential backoff factor")

    # Telegram Telethon & Channel Config
    telegram_api_id: Optional[str] = None
    telegram_api_hash: Optional[str] = None
    telegram_phone: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_channel_url: str = "https://t.me/s/KINGTOSSLINE111"
    telegram_channels: str = "@KINGTOSSLINE111"
    telegram_enabled: bool = False

    # Polling & Intervals (in seconds)
    match_poll_interval: int = 300
    toss_poll_interval: int = 60
    gaming_helper_poll_interval: int = Field(default=90, description="GamingHelper poll interval in seconds (1-2 min)")
    fixtures_poll_interval: int = Field(default=300, description="Public fixtures refresh interval in seconds")
    sources_poll_interval: int = Field(default=300, description="External sources poll interval in seconds")
    cricsheet_sync_interval_hours: int = Field(default=24, description="Cricsheet incremental sync interval in hours")
    health_check_interval: int = Field(default=300, description="Collector health check interval in seconds")

    # Automated Backups
    backup_dir: str = Field(default="_backups", description="Directory to store database backups")
    backup_keep_count: int = Field(default=7, description="Number of nightly database backups to retain")
    backup_interval_hours: int = Field(default=24, description="Database backup interval in hours")

    # Statistical Thresholds & Honesty Gates
    min_sample: int = Field(default=30, description="Minimum sample size threshold for statistical validity")
    confidence: float = Field(default=0.95, description="Confidence interval level")
    significance_alpha: float = Field(default=0.05, description="Significance level alpha for binomial tests")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
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
