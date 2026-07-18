"""Tests for post-push revert detection."""

from unittest.mock import Mock

from wiki_cite.revert_checker import (
    check_article_for_revert,
    check_pending_reverts,
    is_revert_revision,
)
from wiki_cite.seen_store import SeenStore


def test_is_revert_revision_matches_tag():
    assert is_revert_revision(["mw-rollback"], None) is True


def test_is_revert_revision_matches_summary():
    assert is_revert_revision([], "Undid revision 123 by X") is True


def test_is_revert_revision_ignores_normal_edit():
    assert is_revert_revision(["mw-visualeditor"], "typo fix") is False


def test_is_revert_revision_no_tags_no_comment():
    assert is_revert_revision(None, None) is False


def test_check_article_for_revert_skips_own_revision():
    site = Mock()
    page = Mock()
    page.revisions = Mock(return_value=[{"revid": 12345, "tags": [], "comment": ""}])
    site.pages = {"Test Article": page}

    assert check_article_for_revert(site, "Test Article", "12345") is False
    page.revisions.assert_called_once_with(startid=12345, dir="newer", prop="ids|timestamp|flags|comment|user|tags")


def test_check_article_for_revert_detects_newer_revert():
    site = Mock()
    page = Mock()
    page.revisions = Mock(
        return_value=[
            {"revid": 12345, "tags": [], "comment": ""},
            {"revid": 12346, "tags": ["mw-undo"], "comment": "Undid revision 12345"},
        ]
    )
    site.pages = {"Test Article": page}

    assert check_article_for_revert(site, "Test Article", "12345") is True


def test_check_article_for_revert_non_numeric_revid_returns_false():
    site = Mock()
    assert check_article_for_revert(site, "Test Article", "not-a-number") is False


def test_check_pending_reverts_records_reverted_row(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    store.record_outcome("Test Article", "12345", "pushed")

    site = Mock()
    page = Mock()
    page.revisions = Mock(
        return_value=[
            {"revid": 12345, "tags": [], "comment": ""},
            {"revid": 12346, "tags": ["mw-rollback"], "comment": ""},
        ]
    )
    site.pages = {"Test Article": page}

    summary = check_pending_reverts(site, store, horizon_days=7)

    assert summary.checked == 1
    assert summary.reverts_found == 1
    assert summary.failures == []
    assert store.pending_revert_candidates(horizon_days=7) == []


def test_check_pending_reverts_no_match_leaves_candidate_pending(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    store.record_outcome("Test Article", "12345", "pushed")

    site = Mock()
    page = Mock()
    page.revisions = Mock(return_value=[{"revid": 12345, "tags": [], "comment": ""}])
    site.pages = {"Test Article": page}

    summary = check_pending_reverts(site, store, horizon_days=7)

    assert summary.checked == 1
    assert summary.reverts_found == 0
    assert summary.failures == []
    assert store.pending_revert_candidates(horizon_days=7) == [("Test Article", "12345")]


def test_check_pending_reverts_isolates_per_article_failures(tmp_path):
    store = SeenStore(tmp_path / "seen.db")
    store.record_outcome("Bad Article", "1", "pushed")
    store.record_outcome("Good Article", "2", "pushed")

    def fake_getitem(title):
        page = Mock()
        if title == "Bad Article":
            page.revisions = Mock(side_effect=RuntimeError("upstream error"))
        else:
            page.revisions = Mock(return_value=[{"revid": 2, "tags": [], "comment": ""}])
        return page

    site = Mock()
    site.pages = Mock()
    site.pages.__getitem__ = Mock(side_effect=fake_getitem)

    summary = check_pending_reverts(site, store, horizon_days=7)

    assert summary.checked == 2
    assert summary.reverts_found == 0
    assert summary.failures == [("Bad Article", "upstream error")]
