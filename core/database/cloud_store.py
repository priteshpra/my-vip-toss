"""Cloud PostgreSQL Persistence Store for TossIQ VIP.

Enables zero-hassle, reboot-proof & push-proof persistence on ephemeral platforms
like Streamlit Cloud by storing Toss Results (`toss_results_cloud`) and Locked
Pre-Toss Verdicts (`toss_locks_cloud`) in PostgreSQL (`DATABASE_URL`).

Falls back gracefully to local JSON files when `DATABASE_URL` is not configured.
"""

import importlib.util
import json
import os
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Column, Float, MetaData, String, Table, Text, create_engine, delete, func, select

_METADATA = MetaData()

TOSS_RESULTS_TABLE = Table(
    "toss_results_cloud",
    _METADATA,
    Column("match_id", String(128), primary_key=True),
    Column("match_date", String(32), index=True),
    Column("match_name", String(255)),
    Column("tournament", String(255)),
    Column("toss_time_ist", String(64)),
    Column("team_a", String(128)),
    Column("team_b", String(128)),
    Column("recommended_team", String(128), nullable=True),
    Column("stake_rating", String(200)),
    Column("actual_winner", String(128)),
    Column("toss_decision", String(32)),
    Column("outcome", String(32)),
    Column("outcome_label", String(128)),
    Column("is_manual", Boolean, default=False),
    Column("recorded_at", String(64)),
    Column("payload_json", Text),
    Column("updated_at", Float),
)

TOSS_LOCKS_TABLE = Table(
    "toss_locks_cloud",
    _METADATA,
    Column("lock_key", String(255), primary_key=True),
    Column("payload_json", Text),
    Column("updated_at", Float),
)

_ENGINE_CACHE: Dict[str, Any] = {}
_SCHEMA_READY: set = set()
_LAST_CLOUD_ERROR: str = ""


def _detect_pg_dialect() -> str:
    """Choose the installed synchronous PostgreSQL SQLAlchemy driver."""
    if importlib.util.find_spec("psycopg2") is not None:
        return "postgresql+psycopg2://"
    if importlib.util.find_spec("psycopg") is not None:
        return "postgresql+psycopg://"
    if importlib.util.find_spec("pg8000") is not None:
        return "postgresql+pg8000://"
    return "postgresql+psycopg2://"


def normalize_database_url(raw_url: str) -> str:
    """Normalize Postgres / Neon / Supabase / SQLite URLs for synchronous SQLAlchemy."""
    url = (raw_url or "").strip()
    if not url:
        return ""
    if url.startswith("sqlite"):
        return url
    dialect = _detect_pg_dialect()
    if url.startswith("postgres://"):
        return dialect + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return dialect + url[len("postgresql://"):]
    if url.startswith("postgresql+asyncpg://"):
        return dialect + url[len("postgresql+asyncpg://"):]
    return url


def get_cloud_database_url() -> str:
    """Resolve DATABASE_URL from environment or Streamlit secrets (never raises)."""
    for env_key in ("DATABASE_URL", "CLOUD_DATABASE_URL"):
        val = (os.environ.get(env_key) or "").strip()
        if val:
            return normalize_database_url(val)

    try:
        import streamlit as st
        for sec_key in ("DATABASE_URL", "CLOUD_DATABASE_URL"):
            val = str(st.secrets.get(sec_key, "") or "").strip()
            if val:
                return normalize_database_url(val)
        pg_sec = st.secrets.get("postgres", None)
        if isinstance(pg_sec, dict) or hasattr(pg_sec, "get"):
            val = str(pg_sec.get("url", "") or "").strip()
            if val:
                return normalize_database_url(val)
    except Exception:
        pass
    return ""


def is_cloud_db_enabled() -> bool:
    """Return True when a Cloud DB URL is configured."""
    return bool(get_cloud_database_url())


def _get_engine():
    """Return cached SQLAlchemy Engine and ensure tables exist."""
    global _LAST_CLOUD_ERROR
    url = get_cloud_database_url()
    if not url:
        return None

    engine = _ENGINE_CACHE.get(url)
    if engine is None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=240,
            connect_args=connect_args,
        )
        _ENGINE_CACHE[url] = engine

    if url not in _SCHEMA_READY:
        _METADATA.create_all(engine)
        _SCHEMA_READY.add(url)

    _LAST_CLOUD_ERROR = ""
    return engine


def _record_to_row(record: Dict[str, Any], now_ts: float) -> Dict[str, Any]:
    return {
        "match_id": str(record.get("match_id") or "").strip()[:128],
        "match_date": str(record.get("match_date") or "").split("T")[0].strip()[:32],
        "match_name": str(record.get("match_name") or "")[:255],
        "tournament": str(record.get("tournament") or "")[:255],
        "toss_time_ist": str(record.get("toss_time_ist") or "")[:64],
        "team_a": str(record.get("team_a") or "")[:128],
        "team_b": str(record.get("team_b") or "")[:128],
        "recommended_team": str(record.get("recommended_team"))[:128] if record.get("recommended_team") else None,
        "stake_rating": str(record.get("stake_rating") or "")[:200],
        "actual_winner": str(record.get("actual_winner") or "")[:128],
        "toss_decision": str(record.get("toss_decision") or "")[:32],
        "outcome": str(record.get("outcome") or "")[:32],
        "outcome_label": str(record.get("outcome_label") or "")[:128],
        "is_manual": bool(record.get("is_manual", False)),
        "recorded_at": str(record.get("recorded_at") or "")[:64],
        "payload_json": json.dumps(record, ensure_ascii=False),
        "updated_at": now_ts,
    }


def cloud_load_all_results() -> Optional[List[Dict[str, Any]]]:
    """Load all toss results from Cloud DB. Returns None if disabled or unreachable."""
    global _LAST_CLOUD_ERROR
    try:
        engine = _get_engine()
        if engine is None:
            return None
        with engine.connect() as conn:
            rows = conn.execute(select(TOSS_RESULTS_TABLE)).mappings().all()
        out: List[Dict[str, Any]] = []
        for r in rows:
            raw_json = r.get("payload_json")
            if raw_json:
                try:
                    parsed = json.loads(raw_json)
                    if isinstance(parsed, dict):
                        out.append(parsed)
                        continue
                except Exception:
                    pass
            out.append({
                "match_id": r["match_id"],
                "match_date": r["match_date"],
                "match_name": r["match_name"],
                "tournament": r["tournament"],
                "toss_time_ist": r["toss_time_ist"],
                "team_a": r["team_a"],
                "team_b": r["team_b"],
                "recommended_team": r["recommended_team"],
                "stake_rating": r["stake_rating"],
                "actual_winner": r["actual_winner"],
                "toss_decision": r["toss_decision"],
                "outcome": r["outcome"],
                "outcome_label": r["outcome_label"],
                "is_manual": bool(r["is_manual"]),
                "recorded_at": r["recorded_at"],
            })
        return out
    except Exception as exc:
        _LAST_CLOUD_ERROR = str(exc)
        return None


def cloud_upsert_many_results(records: List[Dict[str, Any]]) -> int:
    """Upsert multiple toss result records in a single transaction."""
    global _LAST_CLOUD_ERROR
    if not records:
        return 0
    try:
        engine = _get_engine()
        if engine is None:
            return 0
        now_ts = time.time()
        with engine.begin() as conn:
            existing_ids = set(
                conn.execute(select(TOSS_RESULTS_TABLE.c.match_id)).scalars().all()
            )
            count = 0
            for rec in records:
                row = _record_to_row(rec, now_ts)
                mid = row["match_id"]
                if not mid:
                    continue
                if mid in existing_ids:
                    upd_vals = {k: v for k, v in row.items() if k != "match_id"}
                    conn.execute(
                        TOSS_RESULTS_TABLE.update()
                        .where(TOSS_RESULTS_TABLE.c.match_id == mid)
                        .values(**upd_vals)
                    )
                else:
                    conn.execute(TOSS_RESULTS_TABLE.insert().values(**row))
                    existing_ids.add(mid)
                count += 1
        return count
    except Exception as exc:
        _LAST_CLOUD_ERROR = str(exc)
        return 0


def cloud_upsert_result(record: Dict[str, Any]) -> bool:
    """Upsert a single toss result record into Cloud DB."""
    return cloud_upsert_many_results([record]) > 0


def cloud_delete_result(match_id: str) -> bool:
    """Delete a match result from Cloud DB by match_id."""
    global _LAST_CLOUD_ERROR
    mid = str(match_id or "").strip()
    if not mid:
        return False
    try:
        engine = _get_engine()
        if engine is None:
            return False
        with engine.begin() as conn:
            res = conn.execute(
                delete(TOSS_RESULTS_TABLE).where(TOSS_RESULTS_TABLE.c.match_id == mid)
            )
            return bool(res.rowcount and res.rowcount > 0)
    except Exception as exc:
        _LAST_CLOUD_ERROR = str(exc)
        return False


def cloud_load_all_locks() -> Optional[Dict[str, Any]]:
    """Load all locked predictions from Cloud DB."""
    global _LAST_CLOUD_ERROR
    try:
        engine = _get_engine()
        if engine is None:
            return None
        with engine.connect() as conn:
            rows = conn.execute(select(TOSS_LOCKS_TABLE)).mappings().all()
        out: Dict[str, Any] = {}
        for r in rows:
            try:
                val = json.loads(r["payload_json"])
                if isinstance(val, dict):
                    out[r["lock_key"]] = val
            except Exception:
                continue
        return out
    except Exception as exc:
        _LAST_CLOUD_ERROR = str(exc)
        return None


def cloud_upsert_locks(entries: Dict[str, Any]) -> int:
    """Upsert one or more locked predictions into Cloud DB."""
    global _LAST_CLOUD_ERROR
    if not entries:
        return 0
    try:
        engine = _get_engine()
        if engine is None:
            return 0
        now_ts = time.time()
        with engine.begin() as conn:
            existing_keys = set(
                conn.execute(select(TOSS_LOCKS_TABLE.c.lock_key)).scalars().all()
            )
            count = 0
            for k, v in entries.items():
                lkey = str(k or "").strip()[:255]
                if not lkey or not isinstance(v, dict):
                    continue
                payload = json.dumps(v, ensure_ascii=False)
                if lkey in existing_keys:
                    conn.execute(
                        TOSS_LOCKS_TABLE.update()
                        .where(TOSS_LOCKS_TABLE.c.lock_key == lkey)
                        .values(payload_json=payload, updated_at=now_ts)
                    )
                else:
                    conn.execute(
                        TOSS_LOCKS_TABLE.insert().values(
                            lock_key=lkey, payload_json=payload, updated_at=now_ts
                        )
                    )
                    existing_keys.add(lkey)
                count += 1
        return count
    except Exception as exc:
        _LAST_CLOUD_ERROR = str(exc)
        return 0


def get_cloud_db_status() -> Dict[str, Any]:
    """Return Cloud DB connection status and row counts for UI diagnostics."""
    url = get_cloud_database_url()
    if not url:
        return {
            "enabled": False,
            "connected": False,
            "backend": "Local JSON (Ephemeral)",
            "results_count": 0,
            "locks_count": 0,
            "error": "",
        }
    backend = "SQLite" if url.startswith("sqlite") else "PostgreSQL"
    try:
        engine = _get_engine()
        with engine.connect() as conn:
            res_cnt = conn.execute(select(func.count()).select_from(TOSS_RESULTS_TABLE)).scalar() or 0
            lck_cnt = conn.execute(select(func.count()).select_from(TOSS_LOCKS_TABLE)).scalar() or 0
        return {
            "enabled": True,
            "connected": True,
            "backend": backend,
            "results_count": int(res_cnt),
            "locks_count": int(lck_cnt),
            "error": "",
        }
    except Exception as exc:
        return {
            "enabled": True,
            "connected": False,
            "backend": backend,
            "results_count": 0,
            "locks_count": 0,
            "error": _LAST_CLOUD_ERROR or str(exc),
        }
