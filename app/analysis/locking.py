"""Single local scorer at a time, without locking the application's database."""

import sqlite3
from contextlib import contextmanager

from app import database
from app.analysis.provider import ScoringError


@contextmanager
def scoring_lock():
    # A separate SQLite file supplies a cross-platform, OS-released process lock.
    # No article/watchlist transaction is held open during an API request.
    path = database.DATABASE_PATH.with_name("scoring.lock.db")
    connection = sqlite3.connect(path, timeout=0)
    try:
        try:
            connection.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError:
            raise ScoringError("Another scorer is running or the scoring lock is unavailable. Retry after it finishes.") from None
        yield
    finally:
        connection.close()
