"""Thread-safe in-memory login rate limiter and account lockout manager."""

import time
import threading
from typing import Tuple, Dict, Any
from app.config import settings


class LoginRateLimiter:
    """
    Tracks failed login attempts by key (e.g. IP or username:IP)
    and enforces temporary lockouts to prevent brute-force attacks.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        lockout_duration_seconds: int = 900,
        attempt_window_seconds: int = 900,
    ):
        self.max_attempts = max_attempts
        self.lockout_duration_seconds = lockout_duration_seconds
        self.attempt_window_seconds = attempt_window_seconds
        self._lock = threading.Lock()
        # key -> {"attempts": [timestamps], "locked_until": timestamp}
        self._records: Dict[str, Dict[str, Any]] = {}

    def is_locked_out(self, key: str) -> Tuple[bool, int]:
        """
        Check if the key is currently locked out.
        Returns (is_locked, remaining_lockout_seconds).
        """
        with self._lock:
            now = time.time()
            record = self._records.get(key)
            if not record:
                return False, 0

            locked_until = record.get("locked_until", 0)
            if locked_until > now:
                remaining = int(locked_until - now)
                return True, max(1, remaining)

            # If lockout expired, clear locked_until
            if locked_until > 0 and locked_until <= now:
                record["locked_until"] = 0
                record["attempts"] = []

            return False, 0

    def record_failed_attempt(self, key: str) -> Tuple[int, bool, int]:
        """
        Record a failed login attempt for the key.
        Returns (current_attempts_in_window, is_now_locked_out, lockout_remaining_seconds).
        """
        with self._lock:
            now = time.time()
            if key not in self._records:
                self._records[key] = {"attempts": [], "locked_until": 0}

            record = self._records[key]

            # If already locked out
            if record["locked_until"] > now:
                remaining = int(record["locked_until"] - now)
                return len(record["attempts"]), True, max(1, remaining)

            # Filter out attempts older than window
            cutoff = now - self.attempt_window_seconds
            record["attempts"] = [t for t in record["attempts"] if t > cutoff]
            record["attempts"].append(now)

            attempt_count = len(record["attempts"])
            if attempt_count >= self.max_attempts:
                record["locked_until"] = now + self.lockout_duration_seconds
                return attempt_count, True, self.lockout_duration_seconds

            return attempt_count, False, 0

    def reset(self, key: str) -> None:
        """Reset failed attempts and clear lockout on successful login."""
        with self._lock:
            if key in self._records:
                del self._records[key]

    def clear_all(self) -> None:
        """Clear all rate limit records (useful for testing)."""
        with self._lock:
            self._records.clear()


# Global rate limiter instance initialized with application settings
login_rate_limiter = LoginRateLimiter(
    max_attempts=settings.login_max_failed_attempts,
    lockout_duration_seconds=settings.login_lockout_duration_seconds,
)

