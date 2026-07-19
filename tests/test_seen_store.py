"""Tests for the processed-article store."""

import sqlite3

import pytest

from wiki_cite.seen_store import SeenStore


def test_unseen_then_seen(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    assert store.is_seen("Groveland Four") is False

    store.mark_seen("Groveland Four", "123", "skipped")
    assert store.is_seen("Groveland Four") is True
    assert store.count() == 1


def test_mark_seen_is_idempotent_per_title(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    store.mark_seen("Rosewood, Florida", "1", "skipped")
    store.mark_seen("Rosewood, Florida", "2", "pushed")  # same title, new status
    assert store.count() == 1
    assert store.is_seen("Rosewood, Florida") is True


def test_persists_across_instances(tmp_path):
    path = tmp_path / "seen.db"
    SeenStore(path).mark_seen("Ocoee massacre", "9", "selected")
    # A fresh store on the same file still sees it.
    assert SeenStore(path).is_seen("Ocoee massacre") is True


def test_record_outcome_inserts_row(tmp_path):
    path = tmp_path / "seen.db"
    store = SeenStore(path)
    store.record_outcome(
        "Groveland Four",
        "123",
        "skipped",
        categories=["American film actors"],
        body_line_count=42,
        has_infobox=True,
        citation_needed_count=2,
    )

    row = store._conn.execute("SELECT article_title, outcome, recorded_at, has_infobox FROM outcomes").fetchone()
    assert row[0] == "Groveland Four"
    assert row[1] == "skipped"
    assert row[2] is not None
    assert row[3] == 1


def test_record_outcome_swallows_errors(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    store._conn.close()  # force sqlite to raise on the next statement

    assert store.record_outcome("Rosewood, Florida", "1", "skipped") is None


def test_dimension_rates_counts_successes_and_total(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    store.record_outcome("A", "1", "approved", source_type="news")
    store.record_outcome("B", "2", "pushed", source_type="news")
    store.record_outcome("C", "3", "rejected", source_type="news")

    assert store.dimension_rates("source_type")["news"] == (2, 3)


def test_dimension_rates_categories_explodes_json(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    store.record_outcome("A", "1", "skipped", categories=["Film", "Actors"])
    store.record_outcome("B", "2", "approved", categories=["Film"])

    assert store.dimension_rates("categories")["Film"] == (1, 2)


def test_dimension_rates_rejects_unknown_dimension(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    with pytest.raises(ValueError):
        store.dimension_rates("article_title")


def test_dimension_rates_empty_db_returns_empty(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    assert store.dimension_rates("source_type") == {}


def test_opens_old_schema_db_adds_outcomes(tmp_path):
    """A DB that predates this design (only seen_articles) gets the outcomes
    table added on open, rather than failing (AC6.2)."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE seen_articles (title TEXT PRIMARY KEY, revision_id TEXT, status TEXT, seen_at TEXT)")
    conn.execute("INSERT INTO seen_articles (title, revision_id, status, seen_at) VALUES (?, ?, ?, ?)", ("Old Article", "1", "skipped", "2020-01-01"))
    conn.commit()
    conn.close()

    store = SeenStore(path)  # must not raise
    assert store.is_seen("Old Article") is True  # pre-existing row is intact

    store.record_outcome("Old Article", "1", "skipped")  # outcomes table now exists
    assert store.dimension_rates("source_type") == {}


def test_dimension_rates_on_corrupt_db_returns_empty(tmp_path):
    """A corrupt/non-sqlite file must not raise anywhere — reads degrade to
    empty results (AC6.3)."""
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a real sqlite database file")

    store = SeenStore(path)  # schema init fails internally; must not raise
    assert store.dimension_rates("source_type") == {}
    assert store.is_seen("Anything") is False
    assert store.record_outcome("Anything", "1", "skipped") is None


def test_pending_revert_candidates_returns_recent_pushed(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    store.record_outcome("Groveland Four", "123", "pushed")

    assert store.pending_revert_candidates(horizon_days=7) == [("Groveland Four", "123")]


def test_pending_revert_candidates_excludes_reverted(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    store.record_outcome("Groveland Four", "123", "pushed")
    store.record_outcome("Groveland Four", "123", "reverted")

    assert store.pending_revert_candidates(horizon_days=7) == []


def test_pending_revert_candidates_excludes_expired(tmp_path):
    path = tmp_path / "seen.db"
    store = SeenStore(path)
    store.record_outcome("Groveland Four", "123", "pushed")

    # Age the row past any horizon by rewriting recorded_at directly.
    conn = sqlite3.connect(str(path))
    conn.execute("UPDATE outcomes SET recorded_at = '2000-01-01T00:00:00' WHERE article_title = 'Groveland Four'")
    conn.commit()
    conn.close()

    assert store.pending_revert_candidates(horizon_days=7) == []


def test_pending_revert_candidates_excludes_null_revid(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    store.record_outcome("Groveland Four", None, "pushed")

    assert store.pending_revert_candidates(horizon_days=7) == []


def test_pending_revert_candidates_dedupes(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    store.record_outcome("Groveland Four", "123", "pushed", edit_type="citation_added")
    store.record_outcome("Groveland Four", "123", "pushed", edit_type="grammar_fix")

    assert store.pending_revert_candidates(horizon_days=7) == [("Groveland Four", "123")]


def test_pending_revert_candidates_on_missing_store_returns_empty(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a real sqlite database file")

    store = SeenStore(path)
    assert store.pending_revert_candidates(horizon_days=7) == []


def test_summary_counts_returns_tallies(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    store.record_outcome("Groveland Four", "1", "pushed")
    store.record_outcome("Groveland Four", "1", "reverted")
    store.record_outcome("Ocoee massacre", "2", "pushed")
    store.record_outcome("Ocoee massacre", "2", "approved")
    store.record_outcome("Rosewood, Florida", "3", "approved")
    store.record_outcome("Rosewood, Florida", "3", "rejected")

    assert store.summary_counts() == {
        "pushed_articles": 2,
        "reverted_articles": 1,
        "approved_edits": 2,
        "rejected_edits": 1,
    }


def test_summary_counts_distinct_per_article_not_per_row(tmp_path):
    """Two pushed rows for the same article (multi-edit push) count as one article."""
    store = SeenStore(tmp_path / "seen.db")
    store.record_outcome("Groveland Four", "1", "pushed", edit_type="citation_added")
    store.record_outcome("Groveland Four", "1", "pushed", edit_type="grammar_fix")

    assert store.summary_counts()["pushed_articles"] == 1


def test_summary_counts_empty_db_returns_zeros(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    assert store.summary_counts() == {
        "pushed_articles": 0,
        "reverted_articles": 0,
        "approved_edits": 0,
        "rejected_edits": 0,
    }


def test_summary_counts_on_missing_store_returns_empty(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a real sqlite database file")

    store = SeenStore(path)
    assert store.summary_counts() == {}
