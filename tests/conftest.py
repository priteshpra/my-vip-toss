"""Pytest fixtures for TossIQ test suite."""

import pytest
import sqlite3
import os
from core.database.connection import get_connection, init_db
from core.database.seeder import seed_synthetic_data
from core.database.repository import Repository


@pytest.fixture
def test_db_path(tmp_path):
    """Provide isolated SQLite database path per test."""
    db_file = str(tmp_path / "test_toss_iq.db")
    seed_synthetic_data(db_file)
    return db_file


@pytest.fixture
def test_conn(test_db_path):
    """Provide open SQLite connection with WAL & foreign keys enabled."""
    conn = get_connection(test_db_path)
    yield conn
    conn.close()


@pytest.fixture
def test_repo(test_conn):
    """Provide Repository instance bound to isolated test connection."""
    return Repository(test_conn)
