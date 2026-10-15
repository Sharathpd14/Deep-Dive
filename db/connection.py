import sqlite3
from contextlib import contextmanager
from pathlib import Path

from config import settings

DB_PATH = Path(settings.db_path)
SCHEMA = Path(__file__).with_name("schema.sql")


@contextmanager
def get_conn():
    """New connection per call: safe for FastAPI background threads."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA.read_text())