"""SQLite connection management. One short-lived connection per unit of work (thread-safe, WAL)."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from app.core.config import get_settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_init_lock = threading.Lock()
_initialised: set[str] = set()


def _connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(path: Optional[str] = None) -> None:
    """Create all tables if they do not exist (idempotent)."""
    path = path or get_settings().sqlite_path
    with _init_lock:
        conn = _connect(path)
        try:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.commit()
        finally:
            conn.close()
        _initialised.add(path)


@contextmanager
def get_conn(path: Optional[str] = None) -> Iterator[sqlite3.Connection]:
    """Yield a connection; commit on success, roll back on error, always close."""
    path = path or get_settings().sqlite_path
    if path not in _initialised:
        init_db(path)
    conn = _connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def db_reachable(path: Optional[str] = None) -> bool:
    try:
        with get_conn(path) as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except Exception:
        return False
