"""Fuzzy team and league name normalization utility using rapidfuzz with alias tables."""

import re
from typing import Dict, List, Optional, Tuple
from rapidfuzz import fuzz, process

# Canonical Team Dictionary with common aliases
STANDARD_TEAMS: Dict[str, List[str]] = {
    # IPL Teams
    "Chennai Super Kings": ["CSK", "Chennai", "Super Kings", "Chennai SK"],
    "Mumbai Indians": ["MI", "Mumbai", "Indians"],
    "Royal Challengers Bengaluru": ["RCB", "Royal Challengers Bangalore", "Bangalore", "Bengaluru"],
    "Kolkata Knight Riders": ["KKR", "Kolkata", "Knight Riders"],
    "Rajasthan Royals": ["RR", "Rajasthan", "Royals"],
    "Delhi Capitals": ["DC", "Delhi", "Capitals", "Delhi Daredevils"],
    "Punjab Kings": ["PBKS", "Punjab", "Kings XI Punjab", "KXIP"],
    "Sunrisers Hyderabad": ["SRH", "Hyderabad", "Sunrisers"],
    "Lucknow Super Giants": ["LSG", "Lucknow", "Super Giants"],
    "Gujarat Titans": ["GT", "Gujarat", "Titans"],

    # BBL Teams
    "Perth Scorchers": ["Perth", "Scorchers", "PS"],
    "Sydney Sixers": ["Sixers", "Sydney 6ers"],
    "Sydney Thunder": ["Thunder"],
    "Melbourne Stars": ["Stars"],
    "Melbourne Renegades": ["Renegades"],
    "Brisbane Heat": ["Heat", "Brisbane"],
    "Adelaide Strikers": ["Strikers", "Adelaide"],
    "Hobart Hurricanes": ["Hurricanes", "Hobart"],

    # WBBL Teams (Women guarded)
    "Sydney Sixers Women": ["Sixers Women", "Sydney 6ers Women", "SS-W"],
    "Sydney Thunder Women": ["Thunder Women", "ST-W"],
    "Perth Scorchers Women": ["Scorchers Women", "PS-W"],
    "Melbourne Stars Women": ["Stars Women", "MS-W"],
    "Melbourne Renegades Women": ["Renegades Women", "MR-W"],
    "Brisbane Heat Women": ["Heat Women", "BH-W"],
    "Adelaide Strikers Women": ["Strikers Women", "AS-W"],
    "Hobart Hurricanes Women": ["Hurricanes Women", "HH-W"],

    # International
    "India": ["IND", "Team India", "Men in Blue"],
    "Australia": ["AUS", "Aussies", "Australia Men"],
    "England": ["ENG", "England Men"],
    "South Africa": ["SA", "Proteas"],
    "New Zealand": ["NZ", "Blackcaps"],
    "Pakistan": ["PAK", "Men in Green"],
    "Sri Lanka": ["SL", "Lions"],
    "West Indies": ["WI", "Windies"],
    "Bangladesh": ["BAN", "Tigers"],
    "Afghanistan": ["AFG"],
}

# Canonical League Dictionary
STANDARD_LEAGUES: Dict[str, List[str]] = {
    "IPL": ["Indian Premier League", "TATA IPL", "IPL T20", "IPL 2026", "IPL-2026"],
    "BBL": ["Big Bash League", "KFC BBL", "BBL T20", "BBL 2026", "Big Bash"],
    "WBBL": ["Women's Big Bash League", "WBBL T20", "WBBL 2026", "Womens Big Bash"],
    "NPL": ["Nepal Premier League", "NPL T20", "Nepal Premier League 2026"],
    "ICC ODI": ["ICC One Day International", "ICC Men's ODI", "ODI", "One Day International", "World Cup ODI"],
    "ICC T20I": ["ICC Men's T20I", "T20I", "Twenty20 International", "ICC T20 World Cup", "T20 World Cup"],
    "Emirates D10": ["Emirates D10 League", "Emirates D10 Tournament", "D10 Emirates", "Emirates D20"],
    "Local/Unknown": ["Other", "Club", "Domestic", "General League", "Unknown", "Exhibition"],
}


def clean_text(text: str) -> str:
    """Strip emojis, flags, punctuation, and normalize whitespace."""
    if not text:
        return ""
    # Remove emojis and flag characters
    cleaned = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    # Remove common special characters
    cleaned = re.sub(r'[^\w\s\-\&]', ' ', cleaned)
    # Collapse whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def is_women_entity(text: str) -> bool:
    """Check if entity explicitly denotes a women's team or league."""
    return bool(re.search(r'\b(women|wmn|womens|women\'s|w)\b', text.lower()))


def is_u19_entity(text: str) -> bool:
    """Check if entity denotes an Under-19 team."""
    return bool(re.search(r'\b(u19|u-19|under 19|under-19)\b', text.lower()))


class EntityNormalizer:
    """Fuzzy entity normalizer with exact alias mapping and category guarding."""

    def __init__(
        self,
        teams_dict: Optional[Dict[str, List[str]]] = None,
        leagues_dict: Optional[Dict[str, List[str]]] = None
    ):
        self.teams = teams_dict or STANDARD_TEAMS
        self.leagues = leagues_dict or STANDARD_LEAGUES
        self._build_indexes()

    def _build_indexes(self):
        """Build flat lowercase lookup maps and candidate lists."""
        self.team_lookup: Dict[str, str] = {}
        self.all_team_candidates: List[Tuple[str, str]] = []  # (alias_or_name, canonical)

        for canonical, aliases in self.teams.items():
            self.team_lookup[canonical.lower()] = canonical
            self.all_team_candidates.append((canonical, canonical))
            for alias in aliases:
                self.team_lookup[alias.lower()] = canonical
                self.all_team_candidates.append((alias, canonical))

        self.league_lookup: Dict[str, str] = {}
        self.all_league_candidates: List[Tuple[str, str]] = []

        for canonical, aliases in self.leagues.items():
            self.league_lookup[canonical.lower()] = canonical
            self.all_league_candidates.append((canonical, canonical))
            for alias in aliases:
                self.league_lookup[alias.lower()] = canonical
                self.all_league_candidates.append((alias, canonical))

    def register_team(self, canonical_name: str, aliases: Optional[List[str]] = None):
        """Dynamically register or update a team in the normalizer."""
        self.team_lookup[canonical_name.lower()] = canonical_name
        self.all_team_candidates.append((canonical_name, canonical_name))
        for a in (aliases or []):
            if a.strip():
                self.team_lookup[a.strip().lower()] = canonical_name
                self.all_team_candidates.append((a.strip(), canonical_name))

    def register_league(self, canonical_name: str, aliases: Optional[List[str]] = None):
        """Dynamically register or update a league in the normalizer."""
        self.league_lookup[canonical_name.lower()] = canonical_name
        self.all_league_candidates.append((canonical_name, canonical_name))
        for a in (aliases or []):
            if a.strip():
                self.league_lookup[a.strip().lower()] = canonical_name
                self.all_league_candidates.append((a.strip(), canonical_name))

    def normalize_team(self, query: str, threshold: float = 78.0) -> Optional[str]:
        """
        Normalize raw team name to canonical team.
        Applies exact alias matching first, followed by gender-guarded RapidFuzz matching.
        """
        if not query:
            return None

        cleaned = clean_text(query).strip()
        cleaned_lower = cleaned.lower()

        # 1. Exact match check
        if cleaned_lower in self.team_lookup:
            return self.team_lookup[cleaned_lower]

        # 2. Gender & Age Category Guarding
        query_is_women = is_women_entity(cleaned)
        query_is_u19 = is_u19_entity(cleaned)

        # Filter candidates matching gender & age constraints
        valid_candidates = []
        for cand_str, canonical in self.all_team_candidates:
            if is_women_entity(canonical) != query_is_women:
                continue
            if is_u19_entity(canonical) != query_is_u19:
                continue
            valid_candidates.append((cand_str, canonical))

        if not valid_candidates:
            return None

        # 3. RapidFuzz Token Sort Ratio
        cand_strings = [c[0] for c in valid_candidates]
        match = process.extractOne(
            cleaned,
            cand_strings,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=threshold
        )

        if match:
            matched_str, score, idx = match
            return valid_candidates[idx][1]

        # 4. Fallback: WRatio for partial phrase matches
        match_w = process.extractOne(
            cleaned,
            cand_strings,
            scorer=fuzz.WRatio,
            score_cutoff=max(82.0, threshold)
        )
        if match_w:
            matched_str, score, idx = match_w
            return valid_candidates[idx][1]

        return None

    def normalize_team_name(self, query: str, threshold: float = 78.0) -> Tuple[Optional[str], float]:
        """
        Normalize team name returning tuple of (canonical_team_name, confidence_score).
        Compatibility bridge for external source adapters and services.
        """
        res = self.normalize_team(query, threshold=threshold)
        return (res, 100.0 if res else 0.0)

    def normalize_league(self, query: Optional[str], threshold: float = 75.0) -> str:
        """
        Normalize raw tournament/league name to canonical standard league.
        If league cannot be identified or is empty, returns 'Local/Unknown'.
        """
        if not query or not str(query).strip():
            return "Local/Unknown"

        cleaned = clean_text(str(query)).strip()
        cleaned_lower = cleaned.lower()

        # 1. Exact match
        if cleaned_lower in self.league_lookup:
            return self.league_lookup[cleaned_lower]

        # 2. RapidFuzz matching with token sort ratio
        cand_strings = [c[0] for c in self.all_league_candidates]
        match = process.extractOne(
            cleaned,
            cand_strings,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=threshold
        )
        if match:
            matched_str, score, idx = match
            return self.all_league_candidates[idx][1]

        # 3. Partial check with high threshold (>= 88) to avoid generic word collision (e.g. "Cup", "League")
        match_w = process.extractOne(
            cleaned,
            cand_strings,
            scorer=fuzz.token_set_ratio,
            score_cutoff=88.0
        )
        if match_w:
            matched_str, score, idx = match_w
            return self.all_league_candidates[idx][1]

        return "Local/Unknown"

    def match_teams(self, name_a: str, name_b: str, threshold: float = 78.0) -> bool:
        """Check whether two raw team strings refer to the same team."""
        norm_a = self.normalize_team(name_a, threshold=threshold)
        norm_b = self.normalize_team(name_b, threshold=threshold)
        if norm_a and norm_b:
            return norm_a == norm_b
        # Direct string fallback if neither in standard dictionary
        clean_a = clean_text(name_a).lower()
        clean_b = clean_text(name_b).lower()
        if clean_a == clean_b:
            return True
        if is_women_entity(name_a) != is_women_entity(name_b):
            return False
        score = fuzz.token_sort_ratio(clean_a, clean_b)
        return score >= threshold


def normalize_venue(venue_str: Optional[str]) -> Optional[str]:
    """Clean and normalize venue name."""
    if not venue_str:
        return None
    cleaned = clean_text(str(venue_str)).strip()
    return cleaned if len(cleaned) > 1 else None


# Singleton default normalizer instance
normalizer = EntityNormalizer()
