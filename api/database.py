"""SQLite database helpers for the demo API.

A single connection-per-request pattern is used. The DB file path is
configurable via the ``DB_PATH`` env var so the same code runs locally,
in Docker (mounted volume), and in Kubernetes (persistent volume).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

DB_PATH = os.environ.get("DB_PATH", "data.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    # Return rows as dict-like objects so we can serialize them easily.
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = get_connection()
    try:
        yield conn.cursor()
        conn.commit()
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    price       REAL    NOT NULL,
    stock       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sales (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL,
    sold_at     TEXT    NOT NULL,
    revenue     REAL    NOT NULL
);
"""


def init_db() -> None:
    """Create tables if they do not exist yet."""
    with cursor() as cur:
        cur.executescript(SCHEMA)
