"""Base provider interface for cricket fixtures and toss data."""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime


class BaseCricketProvider(ABC):
    """Abstract interface for cricket data providers."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique identifier for this provider (e.g., 'cricketdata_org', 'rapidapi_cricbuzz')."""
        pass

    @abstractmethod
    def fetch_scheduled_fixtures(self) -> List[Dict[str, Any]]:
        """
        Fetch upcoming and scheduled cricket fixtures.
        Returns a list of raw or semi-normalized fixture dictionaries.
        """
        pass

    @abstractmethod
    def fetch_live_and_recent_tosses(self) -> List[Dict[str, Any]]:
        """
        Fetch recent match results and confirmed coin tosses.
        """
        pass

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """Test provider connectivity, credentials and latency."""
        pass
