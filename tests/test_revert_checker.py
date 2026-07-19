"""Tests for post-push revert detection."""

import sqlite3
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


def test_is_revert_revision_ignores_mw_reverted_victim_tag():
    """`mw-reverted` marks the revision that GOT reverted (the victim), not the
    reverting edit — it must not be read as a revert signal, or a later,
    unrelated edit getting reverted would falsely flag our own pushed revision."""
    assert is_revert_revision(["mw-reverted"], "added a sentence") is False


def test_is_revert_revision_ignores_restore_summary():
    """"restore" is deliberately not a marker — it also matches legitimate
    summaries like "restored formatting" that are not reverts."""
    assert is_revert_revision([], "restored formatting") is False


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


def test_check_article_for_revert_ignores_reverted_victim_tag_on_later_edit():
    """Regression for the mw-reverted false positive: our edit is rev N. A later,
    unrelated edit N+1 (by someone else) gets reverted at N+2, so N+1 carries
    `mw-reverted` (the victim tag) — but N+1 is not itself an action tag and
    N+2's own tags/comment carry no revert marker either, so our edit N was
    never reverted and this must return False."""
    site = Mock()
    page = Mock()
    page.revisions = Mock(
        return_value=[
            {"revid": 100, "tags": [], "comment": "our push"},
            {"revid": 101, "tags": ["mw-reverted"], "comment": "editor X's unrelated edit"},
            {"revid": 102, "tags": [], "comment": "editor Y manually retypes 101's prior text"},
        ]
    )
    site.pages = {"Test Article": page}

    assert check_article_for_revert(site, "Test Article", "100") is False


def test_check_article_for_revert_multiple_non_revert_revisions():
    """Several ordinary edits after the anchor, none matching, walk to the end."""
    site = Mock()
    page = Mock()
    page.revisions = Mock(
        return_value=[
            {"revid": 12345, "tags": [], "comment": ""},
            {"revid": 12346, "tags": ["mw-visualeditor"], "comment": "typo"},
            {"revid": 12347, "tags": [], "comment": "added a sentence"},
        ]
    )
    site.pages = {"Test Article": page}

    assert check_article_for_revert(site, "Test Article", "12345") is False


def test_check_article_for_revert_non_numeric_revid_returns_false():
    site = Mock()
    assert check_article_for_revert(site, "Test Article", "not-a-number") is False


def test_check_pending_reverts_writes_reverted_row(tmp_path):
    """AC2.1 end-to-end: a matched revert writes a "reverted" row and closes
    the candidate loop (AC2.2 followthrough)."""
    path = tmp_path / "seen.db"
    store = SeenStore(path)
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

    conn = sqlite3.connect(str(path))
    row = conn.execute("SELECT article_title, revision_id FROM outcomes WHERE outcome = 'reverted'").fetchone()
    conn.close()
    assert row == ("Test Article", "12345")

    assert store.pending_revert_candidates(horizon_days=7) == []


def test_check_pending_reverts_no_match_leaves_pending(tmp_path):
    """AC2.2: no revert marker found → no "reverted" row, candidate stays pending."""
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


def test_check_pending_reverts_isolates_failures(tmp_path):
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
