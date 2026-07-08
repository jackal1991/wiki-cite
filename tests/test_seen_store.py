"""Tests for the processed-article store."""

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
