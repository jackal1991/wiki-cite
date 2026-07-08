"""Persistent record of articles the agent has already processed.

Keeps "Fetch new article" idempotent: an article that was already analyzed
(selected or skipped) is not re-scanned from the top of the category on the next
fetch, so we don't re-pay for the same Claude calls or re-offer the same page.
Backed by stdlib sqlite3 — no extra dependency.
"""

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_articles (
    title        TEXT PRIMARY KEY,
    revision_id  TEXT,
    status       TEXT,
    seen_at      TEXT
)
"""


class SeenStore:
    """Thread-safe store of processed article titles.

    Keyed by title (available from category iteration without a page fetch), so
    the skip decision is cheap. The revision id is recorded for context and for a
    future "revisit after the article was edited" enhancement.
    """

    def __init__(self, path: str | Path = "wiki_cite_seen.db"):
        # check_same_thread=False + a lock: the Flask dev server serves the SSE
        # scan on a worker thread, so the connection is shared across threads.
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    def is_seen(self, title: str) -> bool:
        """Return True if this article title has already been processed."""
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM seen_articles WHERE title = ?", (title,)).fetchone()
        return row is not None

    def mark_seen(self, title: str, revision_id: str, status: str) -> None:
        """Record an article as processed (status: "selected", "skipped", "pushed")."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO seen_articles (title, revision_id, status, seen_at) VALUES (?, ?, ?, ?) ON CONFLICT(title) DO UPDATE SET revision_id=excluded.revision_id, status=excluded.status, seen_at=excluded.seen_at",
                (title, revision_id, status, datetime.now().isoformat()),
            )
            self._conn.commit()

    def count(self) -> int:
        """Number of processed articles on record."""
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM seen_articles").fetchone()[0]
