"""Tests for the dashboard summary aggregation."""

from wiki_cite.stats import compute_summary


def test_compute_summary_rates():
    counts = {"pushed_articles": 10, "reverted_articles": 2, "approved_edits": 8, "rejected_edits": 2}

    summary = compute_summary(counts)

    assert summary["revert_rate"] == 0.2
    assert summary["approval_rate"] == 0.8
    assert summary["has_data"] is True


def test_compute_summary_zero_denominators():
    counts = {"pushed_articles": 0, "reverted_articles": 0, "approved_edits": 0, "rejected_edits": 0}

    summary = compute_summary(counts)

    assert summary["revert_rate"] is None
    assert summary["approval_rate"] is None
    assert summary["has_data"] is False


def test_compute_summary_missing_keys_default_to_zero():
    summary = compute_summary({})

    assert summary["pushed_articles"] == 0
    assert summary["revert_rate"] is None
    assert summary["approval_rate"] is None
    assert summary["has_data"] is False


def test_compute_summary_pushed_with_no_reviews_has_data():
    counts = {"pushed_articles": 3, "reverted_articles": 0, "approved_edits": 0, "rejected_edits": 0}

    summary = compute_summary(counts)

    assert summary["has_data"] is True
    assert summary["revert_rate"] == 0.0
    assert summary["approval_rate"] is None
