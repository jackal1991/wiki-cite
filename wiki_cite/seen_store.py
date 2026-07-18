"""Persistent record of articles the agent has already processed.

Keeps "Fetch new article" idempotent: an article that was already analyzed
(selected or skipped) is not re-scanned from the top of the category on the next
fetch, so we don't re-pay for the same Claude calls or re-offer the same page.
Backed by stdlib sqlite3 — no extra dependency.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_articles (
    title        TEXT PRIMARY KEY,
    revision_id  TEXT,
    status       TEXT,
    seen_at      TEXT
)
"""

_OUTCOMES_SCHEMA = """
CREATE TABLE IF NOT EXISTS outcomes (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    article_title         TEXT NOT NULL,
    revision_id           TEXT,
    outcome               TEXT NOT NULL,
    recorded_at           TEXT NOT NULL,

    categories            TEXT,
    body_line_count       INTEGER,
    has_infobox           INTEGER,
    citation_needed_count INTEGER,

    edit_type             TEXT,
    confidence            TEXT,
    source_type           TEXT,
    source_api            TEXT,
    reliability           TEXT,
    policy_reference      TEXT
)
"""

# Columns aggregatable via dimension_rates. Validated against this allowlist
# before being interpolated into a query — never trust a caller-supplied string.
_DIMENSION_COLUMNS = frozenset(
    {
        "source_type",
        "source_api",
        "edit_type",
        "confidence",
        "has_infobox",
        "reliability",
        "policy_reference",
        "body_line_count",
        "categories",
    }
)


class SeenStore:
    """Thread-safe store of processed article titles.

    Keyed by title (available from category iteration without a page fetch), so
    the skip decision is cheap. The revision id is recorded for context and for a
    future "revisit after the article was edited" enhancement.
    """

    def __init__(self, path: str | Path = "wiki_cite_seen.db"):
        # check_same_thread=False + a lock: the Flask dev server serves the SSE
        # scan on a worker thread, so the connection is shared across threads.
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        try:
            self._conn = sqlite3.connect(str(path), check_same_thread=False)
            self._conn.execute(_SCHEMA)
            self._conn.execute(_OUTCOMES_SCHEMA)
            self._conn.commit()
        except sqlite3.Error:
            # connect() itself can fail (e.g. a missing parent directory), and a
            # corrupt/unreadable file fails at the first query instead. Either way,
            # self._conn is left None and every query method below checks for that
            # (or guards its own sqlite3.Error), so a store left in this state
            # degrades to empty reads / no-op writes instead of raising out of a
            # scan or a review-UI click.
            logger.warning("Failed to open/initialize store at %s; store will degrade to no-op reads/writes", path, exc_info=True)
            self._conn = None

    def is_seen(self, title: str) -> bool:
        """Return True if this article title has already been processed."""
        if self._conn is None:
            return False
        try:
            with self._lock:
                row = self._conn.execute("SELECT 1 FROM seen_articles WHERE title = ?", (title,)).fetchone()
            return row is not None
        except sqlite3.Error:
            logger.warning("Failed to check is_seen for %r", title, exc_info=True)
            return False

    def mark_seen(self, title: str, revision_id: str, status: str) -> None:
        """Record an article as processed (status: "selected", "skipped", "pushed")."""
        if self._conn is None:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO seen_articles (title, revision_id, status, seen_at) VALUES (?, ?, ?, ?) ON CONFLICT(title) DO UPDATE SET revision_id=excluded.revision_id, status=excluded.status, seen_at=excluded.seen_at",
                    (title, revision_id, status, datetime.now().isoformat()),
                )
                self._conn.commit()
        except sqlite3.Error:
            logger.warning("Failed to mark %r seen", title, exc_info=True)

    def count(self) -> int:
        """Number of processed articles on record."""
        if self._conn is None:
            return 0
        try:
            with self._lock:
                return self._conn.execute("SELECT COUNT(*) FROM seen_articles").fetchone()[0]
        except sqlite3.Error:
            logger.warning("Failed to count seen articles", exc_info=True)
            return 0

    def record_outcome(
        self,
        article_title: str,
        revision_id: str | None,
        outcome: str,
        *,
        categories: list[str] | None = None,
        body_line_count: int | None = None,
        has_infobox: bool | None = None,
        citation_needed_count: int | None = None,
        edit_type: str | None = None,
        confidence: str | None = None,
        source_type: str | None = None,
        source_api: str | None = None,
        reliability: str | None = None,
        policy_reference: str | None = None,
    ) -> None:
        """Append one outcomes row. Never raises past the caller — logs and
        swallows sqlite errors so a storage hiccup can't break a scan or a
        review-UI click."""
        if self._conn is None:
            return

        categories_json = json.dumps(categories) if categories is not None else None
        has_infobox_int = int(has_infobox) if has_infobox is not None else None

        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO outcomes (
                        article_title, revision_id, outcome, recorded_at,
                        categories, body_line_count, has_infobox, citation_needed_count,
                        edit_type, confidence, source_type, source_api, reliability, policy_reference
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article_title,
                        revision_id,
                        outcome,
                        datetime.now().isoformat(),
                        categories_json,
                        body_line_count,
                        has_infobox_int,
                        citation_needed_count,
                        edit_type,
                        confidence,
                        source_type,
                        source_api,
                        reliability,
                        policy_reference,
                    ),
                )
                self._conn.commit()
        except sqlite3.Error:
            logger.warning("Failed to record outcome %r for %r", outcome, article_title, exc_info=True)

    def dimension_rates(self, dimension: str, success_outcomes: tuple[str, ...] = ("approved", "pushed")) -> dict[str, tuple[int, int]]:
        """Return {value: (successes, total)} for a given outcomes column.

        Counts rows whose outcome is in success_outcomes as successes and all
        recorded rows for that value as the denominator. Never divides — callers
        compute the rate and must handle total == 0.
        """
        if dimension not in _DIMENSION_COLUMNS:
            raise ValueError(f"Unknown dimension: {dimension!r}")

        if self._conn is None:
            return {}

        try:
            with self._lock:
                rows = self._conn.execute(f"SELECT {dimension}, outcome FROM outcomes WHERE {dimension} IS NOT NULL").fetchall()  # noqa: S608 (dimension validated against _DIMENSION_COLUMNS above)
        except sqlite3.Error:
            logger.warning("Failed to read dimension_rates(%r)", dimension, exc_info=True)
            return {}

        tallies: dict[str, list[int]] = {}

        def tally(value: str, is_success: bool) -> None:
            counts = tallies.setdefault(value, [0, 0])
            counts[1] += 1
            if is_success:
                counts[0] += 1

        for raw_value, row_outcome in rows:
            is_success = row_outcome in success_outcomes
            if dimension == "categories":
                try:
                    values = json.loads(raw_value)
                except (json.JSONDecodeError, TypeError):
                    continue
                for value in values:
                    tally(value, is_success)
            elif dimension == "has_infobox":
                # Stored as 0/1; the scorer keys on str(bool) ("True"/"False"), so
                # map here to keep the scorer and stats in agreement.
                tally("True" if raw_value else "False", is_success)
            else:
                tally(str(raw_value), is_success)

        return {value: (successes, total) for value, (successes, total) in tallies.items()}

    def pending_revert_candidates(self, horizon_days: int) -> list[tuple[str, str]]:
        """Return [(article_title, revision_id)] for `"pushed"` outcomes that are
        within `horizon_days` of their recorded_at, have a non-null revision_id, and
        have no later `"reverted"` row for the same (article_title, revision_id).
        """
        if self._conn is None:
            return []

        cutoff = (datetime.now() - timedelta(days=horizon_days)).isoformat()

        try:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT p.article_title, p.revision_id
                    FROM outcomes p
                    WHERE p.outcome = 'pushed'
                      AND p.revision_id IS NOT NULL
                      AND p.recorded_at >= ?
                      AND NOT EXISTS (
                        SELECT 1 FROM outcomes r
                        WHERE r.outcome = 'reverted'
                          AND r.article_title = p.article_title
                          AND r.revision_id = p.revision_id
                      )
                    """,
                    (cutoff,),
                ).fetchall()
        except sqlite3.Error:
            logger.warning("Failed to read pending_revert_candidates", exc_info=True)
            return []

        return list(dict.fromkeys((title, revision_id) for title, revision_id in rows))
