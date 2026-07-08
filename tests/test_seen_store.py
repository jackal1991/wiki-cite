"""Tests for the processed-article store."""

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
