"""Type-safe repository layer for SQLite operations."""

import sqlite3
import json
from typing import List, Dict, Any, Optional
from datetime import datetime

from core.models.schemas import (
    Tournament, Venue, Team, Match, TossResult,
    MarketSnapshot, PredictionSnapshot, PredictionEvaluation
)
from core.timezone import parse_to_ist, to_iso_ist, now_ist


class Repository:
    """Data Access Object providing transactional queries."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # --- Tournaments ---
    def upsert_tournament(self, t: Tournament) -> None:
        with self.conn:
            self.conn.execute("""
                INSERT INTO tournaments (id, name, category, format)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    category = excluded.category,
                    format = excluded.format
            """, (t.id, t.name, t.category.value, t.format.value))

    def get_all_tournaments(self) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM tournaments ORDER BY name ASC")
        return [dict(r) for r in cur.fetchall()]

    # --- Venues ---
    def upsert_venue(self, v: Venue) -> None:
        with self.conn:
            self.conn.execute("""
                INSERT INTO venues (id, name, city, country)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    city = excluded.city,
                    country = excluded.country
            """, (v.id, v.name, v.city, v.country))

    def get_all_venues(self) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM venues ORDER BY name ASC")
        return [dict(r) for r in cur.fetchall()]

    # --- Teams ---
    def upsert_team(self, t: Team) -> None:
        aliases_json = json.dumps(t.aliases)
        with self.conn:
            self.conn.execute("""
                INSERT INTO teams (id, name, short_code, category, aliases)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    short_code = excluded.short_code,
                    category = excluded.category,
                    aliases = excluded.aliases
            """, (t.id, t.name, t.short_code, t.category, aliases_json))

    def get_all_teams(self) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM teams ORDER BY name ASC")
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["aliases"] = json.loads(r["aliases"]) if r["aliases"] else []
        return rows

    def find_team_by_name_or_alias(self, query: str) -> Optional[Dict[str, Any]]:
        if not query:
            return None
        cleaned = query.strip().lower()
        teams = self.get_all_teams()

        # 1. Priority 1: Exact match on team name
        for t in teams:
            if t["name"].lower() == cleaned:
                return t

        # 2. Priority 2: Exact match on short code
        for t in teams:
            if t["short_code"].lower() == cleaned:
                return t

        # 3. Priority 3: Exact match on aliases
        for t in teams:
            for a in t["aliases"]:
                if a.lower() == cleaned:
                    return t

        return None

    # --- Matches ---
    def upsert_match(self, m: Match) -> None:
        with self.conn:
            self.conn.execute("""
                INSERT INTO matches (
                    id, external_provider_id, tournament_id, team_a_id, team_b_id,
                    venue_id, match_format, scheduled_start_ist, expected_toss_ist,
                    status, data_source_id, last_updated_ist, data_freshness_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    external_provider_id = excluded.external_provider_id,
                    tournament_id = excluded.tournament_id,
                    team_a_id = excluded.team_a_id,
                    team_b_id = excluded.team_b_id,
                    venue_id = excluded.venue_id,
                    match_format = excluded.match_format,
                    scheduled_start_ist = excluded.scheduled_start_ist,
                    expected_toss_ist = excluded.expected_toss_ist,
                    status = excluded.status,
                    data_source_id = excluded.data_source_id,
                    last_updated_ist = excluded.last_updated_ist,
                    data_freshness_status = excluded.data_freshness_status
            """, (
                m.id, m.external_provider_id, m.tournament_id, m.team_a_id, m.team_b_id,
                m.venue_id, m.match_format.value,
                to_iso_ist(m.scheduled_start_ist),
                to_iso_ist(m.expected_toss_ist),
                m.status.value, m.data_source_id,
                to_iso_ist(m.last_updated_ist),
                m.data_freshness_status
            ))

    def get_match_by_id(self, match_id: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT m.*,
                   t1.name AS team_a_name, t1.short_code AS team_a_short,
                   t2.name AS team_b_name, t2.short_code AS team_b_short,
                   tour.name AS tournament_name, tour.category AS tournament_category,
                   v.name AS venue_name, v.city AS venue_city,
                   tr.toss_winner_id, tw.name AS toss_winner_name, tr.toss_decision,
                   tr.verification_status, tr.official_source_url
            FROM matches m
            JOIN teams t1 ON m.team_a_id = t1.id
            JOIN teams t2 ON m.team_b_id = t2.id
            JOIN tournaments tour ON m.tournament_id = tour.id
            LEFT JOIN venues v ON m.venue_id = v.id
            LEFT JOIN toss_results tr ON m.id = tr.match_id
            LEFT JOIN teams tw ON tr.toss_winner_id = tw.id
            WHERE m.id = ?
        """, (match_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_matches_by_date_ist(self, date_str: Optional[str] = None, tournament_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Query matches starting on date_str (YYYY-MM-DD) in Asia/Kolkata, or all dates if 'All Dates'."""
        query = """
            SELECT m.*,
                   t1.name AS team_a_name, t1.short_code AS team_a_short,
                   t2.name AS team_b_name, t2.short_code AS team_b_short,
                   tour.name AS tournament_name, tour.category AS tournament_category,
                   v.name AS venue_name,
                   tr.toss_winner_id, tw.name AS toss_winner_name, tr.toss_decision,
                   tr.verification_status
            FROM matches m
            JOIN teams t1 ON m.team_a_id = t1.id
            JOIN teams t2 ON m.team_b_id = t2.id
            JOIN tournaments tour ON m.tournament_id = tour.id
            LEFT JOIN venues v ON m.venue_id = v.id
            LEFT JOIN toss_results tr ON m.id = tr.match_id
            LEFT JOIN teams tw ON tr.toss_winner_id = tw.id
            WHERE 1=1
        """
        params = []
        if date_str and date_str not in ("All Dates", "All"):
            query += " AND DATE(m.scheduled_start_ist) = ?"
            params.append(date_str)

        if tournament_id and tournament_id != "All":
            query += " AND m.tournament_id = ?"
            params.append(tournament_id)

        query += " ORDER BY m.scheduled_start_ist ASC"
        cur = self.conn.cursor()
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]

    def get_all_distinct_match_dates(self) -> List[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT DISTINCT DATE(scheduled_start_ist) FROM matches ORDER BY DATE(scheduled_start_ist) DESC")
        return [r[0] for r in cur.fetchall()]

    # --- Toss Results ---
    def upsert_toss_result(self, res: TossResult) -> None:
        with self.conn:
            self.conn.execute("""
                INSERT INTO toss_results (
                    match_id, toss_winner_id, toss_decision, match_winner_id,
                    official_source_url, source_provider_id, verified_at_ist,
                    verification_status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET
                    toss_winner_id = excluded.toss_winner_id,
                    toss_decision = excluded.toss_decision,
                    match_winner_id = excluded.match_winner_id,
                    official_source_url = excluded.official_source_url,
                    source_provider_id = excluded.source_provider_id,
                    verified_at_ist = excluded.verified_at_ist,
                    verification_status = excluded.verification_status,
                    notes = excluded.notes
            """, (
                res.match_id, res.toss_winner_id, res.toss_decision.value,
                res.match_winner_id, res.official_source_url, res.source_provider_id,
                to_iso_ist(res.verified_at_ist), res.verification_status.value, res.notes
            ))
            # Also update match status to TOSS_COMPLETED if upcoming
            self.conn.execute("""
                UPDATE matches
                SET status = 'TOSS_COMPLETED', last_updated_ist = ?
                WHERE id = ? AND status IN ('UPCOMING', 'TOSS_PENDING')
            """, (to_iso_ist(now_ist()), res.match_id))

    # --- Historical Match Statistics ---
    def insert_historical_match(
        self,
        hist_id: str,
        match_date: str,
        tournament_id: Optional[str],
        match_format: str,
        team_a_id: str,
        team_b_id: str,
        venue_id: Optional[str],
        toss_winner_id: str,
        toss_decision: Optional[str],
        source: str
    ) -> None:
        with self.conn:
            self.conn.execute("""
                INSERT OR REPLACE INTO historical_match_statistics (
                    id, match_date, tournament_id, match_format, team_a_id, team_b_id,
                    venue_id, toss_winner_id, toss_decision, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                hist_id, match_date, tournament_id, match_format, team_a_id, team_b_id,
                venue_id, toss_winner_id, toss_decision, source
            ))

    def get_historical_tosses_for_team(self, team_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT * FROM historical_match_statistics
            WHERE team_a_id = ? OR team_b_id = ?
            ORDER BY match_date DESC
            LIMIT ?
        """, (team_id, team_id, limit))
        return [dict(r) for r in cur.fetchall()]

    def get_h2h_historical_tosses(self, team_a_id: str, team_b_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT * FROM historical_match_statistics
            WHERE (team_a_id = ? AND team_b_id = ?)
               OR (team_a_id = ? AND team_b_id = ?)
            ORDER BY match_date DESC
            LIMIT ?
        """, (team_a_id, team_b_id, team_b_id, team_a_id, limit))
        return [dict(r) for r in cur.fetchall()]

    # --- Public Market Intelligence ---
    def insert_market_snapshot(self, snap: MarketSnapshot) -> int:
        with self.conn:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO market_snapshots (
                    match_id, provider_id, bookmaker, market_type,
                    team_a_odds, team_b_odds, team_a_implied_prob, team_b_implied_prob,
                    margin_percentage, snapshot_timestamp_ist, is_pre_toss, is_stale
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                snap.match_id, snap.provider_id, snap.bookmaker, snap.market_type.value,
                snap.team_a_odds, snap.team_b_odds, snap.team_a_implied_prob, snap.team_b_implied_prob,
                snap.margin_percentage, to_iso_ist(snap.snapshot_timestamp_ist),
                1 if snap.is_pre_toss else 0,
                1 if snap.is_stale else 0
            ))
            return cur.lastrowid

    def get_latest_market_snapshot(self, match_id: str, market_type: str = "TOSS_WINNER") -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT * FROM market_snapshots
            WHERE match_id = ? AND market_type = ?
            ORDER BY snapshot_timestamp_ist DESC, id DESC
            LIMIT 1
        """, (match_id, market_type))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_market_movement_history(self, match_id: str) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT * FROM market_snapshots
            WHERE match_id = ?
            ORDER BY snapshot_timestamp_ist ASC
        """, (match_id,))
        return [dict(r) for r in cur.fetchall()]

    # --- Immutable Pre-Toss Predictions ---
    def insert_prediction_snapshot(self, pred: PredictionSnapshot) -> int:
        evidence_supp = json.dumps(pred.supporting_evidence)
        evidence_contra = json.dumps(pred.contradicting_evidence)
        with self.conn:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO prediction_snapshots (
                    match_id, model_version_id, assessment_status,
                    team_a_prob, team_b_prob, confidence_interval_lower, confidence_interval_upper,
                    supporting_evidence_json, contradicting_evidence_json,
                    uncertainty_warnings, sample_size_evaluated,
                    snapshot_timestamp_ist, is_locked
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pred.match_id, pred.model_version_id, pred.assessment_status.value,
                pred.team_a_prob, pred.team_b_prob, pred.confidence_interval_lower, pred.confidence_interval_upper,
                evidence_supp, evidence_contra,
                pred.uncertainty_warnings, pred.sample_size_evaluated,
                to_iso_ist(pred.snapshot_timestamp_ist),
                1 if pred.is_locked else 0
            ))
            return cur.lastrowid

    def get_latest_prediction_snapshot(self, match_id: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT ps.*, mv.name AS model_name, mv.description AS model_desc
            FROM prediction_snapshots ps
            JOIN model_versions mv ON ps.model_version_id = mv.id
            WHERE ps.match_id = ?
            ORDER BY ps.snapshot_timestamp_ist DESC, ps.id DESC
            LIMIT 1
        """, (match_id,))
        row = cur.fetchone()
        if not row:
            return None
        res = dict(row)
        res["supporting_evidence"] = json.loads(res["supporting_evidence_json"]) if res["supporting_evidence_json"] else []
        res["contradicting_evidence"] = json.loads(res["contradicting_evidence_json"]) if res["contradicting_evidence_json"] else []
        return res

    def get_all_prediction_snapshots_with_outcomes(self) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT ps.*, m.scheduled_start_ist,
                   t1.name AS team_a_name, t2.name AS team_b_name,
                   tr.toss_winner_id, tw.name AS actual_toss_winner_name,
                   pe.brier_score, pe.log_loss, pe.is_correct
            FROM prediction_snapshots ps
            JOIN matches m ON ps.match_id = m.id
            JOIN teams t1 ON m.team_a_id = t1.id
            JOIN teams t2 ON m.team_b_id = t2.id
            LEFT JOIN toss_results tr ON m.id = tr.match_id
            LEFT JOIN teams tw ON tr.toss_winner_id = tw.id
            LEFT JOIN prediction_evaluations pe ON ps.id = pe.prediction_snapshot_id
            ORDER BY ps.snapshot_timestamp_ist DESC
        """)
        rows = [dict(r) for r in cur.fetchall()]
        for r in rows:
            r["supporting_evidence"] = json.loads(r["supporting_evidence_json"]) if r["supporting_evidence_json"] else []
            r["contradicting_evidence"] = json.loads(r["contradicting_evidence_json"]) if r["contradicting_evidence_json"] else []
        return rows

    # --- Prediction Evaluations ---
    def insert_prediction_evaluation(self, ev: PredictionEvaluation) -> int:
        with self.conn:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT OR REPLACE INTO prediction_evaluations (
                    prediction_snapshot_id, match_id, actual_toss_winner_id,
                    predicted_favored_id, is_correct, brier_score, log_loss, evaluated_at_ist
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ev.prediction_snapshot_id, ev.match_id, ev.actual_toss_winner_id,
                ev.predicted_favored_id, ev.is_correct, ev.brier_score, ev.log_loss,
                to_iso_ist(ev.evaluated_at_ist)
            ))
            return cur.lastrowid

    # --- Source Update Logs ---
    def log_source_update(
        self,
        source_id: str,
        endpoint: str,
        status_code: Optional[int],
        response_time_ms: int,
        records_fetched: int,
        is_success: bool,
        error_message: Optional[str]
    ) -> None:
        with self.conn:
            self.conn.execute("""
                INSERT INTO source_update_logs (
                    source_id, endpoint, status_code, response_time_ms,
                    records_fetched, is_success, error_message, logged_at_ist
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                source_id, endpoint, status_code, response_time_ms,
                records_fetched, 1 if is_success else 0, error_message,
                to_iso_ist(now_ist())
            ))


# Explicit class alias
class TossRepository(Repository):
    """Alias for Repository."""
    pass


__all__ = ["Repository", "TossRepository"]
