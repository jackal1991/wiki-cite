"""Tests for Wikipedia push service."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from wiki_cite.wikipedia_push import WikipediaPushService, RateLimiter
from wiki_cite.models import Article, EditProposal, ProposedEdit, EditType


@pytest.fixture
def rate_limiter():
    """Create rate limiter instance."""
    return RateLimiter(max_edits_per_hour=10)


@pytest.fixture
def mock_site():
    """Create mock mwclient site."""
    return Mock()


@pytest.fixture
def push_service(mock_site):
    """Create push service with mock site."""
    return WikipediaPushService(site=mock_site)


def test_rate_limiter_allows_edits_under_limit(rate_limiter):
    """Test that rate limiter allows edits under the limit."""
    for _ in range(10):
        assert rate_limiter.can_edit() is True
        rate_limiter.record_edit()

    # 11th edit should be blocked
    assert rate_limiter.can_edit() is False


def test_rate_limiter_resets_after_hour(rate_limiter):
    """Test that rate limiter resets after an hour."""
    # Fill up the limit
    for _ in range(10):
        rate_limiter.record_edit()

    assert rate_limiter.can_edit() is False

    # Manually set edit times to be old
    old_time = datetime.now() - timedelta(hours=2)
    rate_limiter.edit_times = [old_time] * 10

    # Should allow edits again
    assert rate_limiter.can_edit() is True


def test_rate_limiter_records_edit(rate_limiter):
    """Test that rate limiter records edits."""
    initial_count = len(rate_limiter.edit_times)
    rate_limiter.record_edit()

    assert len(rate_limiter.edit_times) == initial_count + 1


def test_check_for_conflicts_no_conflict(push_service, mock_site):
    """Test conflict detection when no conflict exists."""
    mock_page = Mock()
    mock_page.revision = "12345"
    mock_site.pages = {"Test Article": mock_page}

    has_conflict = push_service.check_for_conflicts("Test Article", "12345")
    assert has_conflict is False


def test_check_for_conflicts_with_conflict(push_service, mock_site):
    """Test conflict detection when conflict exists."""
    mock_page = Mock()
    mock_page.revision = "67890"  # Different revision
    mock_site.pages = {"Test Article": mock_page}

    has_conflict = push_service.check_for_conflicts("Test Article", "12345")
    assert has_conflict is True


def test_check_for_conflicts_on_error(push_service, mock_site):
    """Test conflict detection returns True on error."""
    def raise_error(key):
        raise Exception("API Error")

    mock_site.pages = Mock()
    mock_site.pages.__getitem__ = Mock(side_effect=raise_error)

    has_conflict = push_service.check_for_conflicts("Test Article", "12345")
    assert has_conflict is True  # Assume conflict on error to be safe


def test_push_edits_rate_limited(push_service):
    """Test that push is blocked when rate limited."""
    # Fill up rate limiter
    for _ in range(10):
        push_service.rate_limiter.record_edit()

    article = Article(title="Test", url="https://example.com", wikitext="test", revision_id="123")

    edit = ProposedEdit(
        edit_type=EditType.GRAMMAR_FIX,
        original_text="test",
        proposed_text="test2",
        rationale="fix",
        confidence="high",
        approved=True,
    )

    proposal = EditProposal(id="test-id", article=article, edits=[edit])

    success, message = push_service.push_edits(proposal, "modified text")

    assert success is False
    assert "Rate limit" in message


def test_push_edits_with_conflict(push_service, mock_site):
    """Test that push is blocked when there's a conflict."""
    # Mock conflict detection
    mock_page = Mock()
    mock_page.revision = "999"  # Different from article revision
    mock_site.pages = {"Test": mock_page}

    article = Article(title="Test", url="https://example.com", wikitext="test", revision_id="123")

    edit = ProposedEdit(
        edit_type=EditType.GRAMMAR_FIX,
        original_text="test",
        proposed_text="test2",
        rationale="fix",
        confidence="high",
        approved=True,
    )

    proposal = EditProposal(id="test-id", article=article, edits=[edit])

    success, message = push_service.push_edits(proposal, "modified text")

    assert success is False
    assert "conflict" in message.lower()


def test_push_edits_no_approved_edits(push_service, mock_site):
    """Test that push fails when no edits are approved."""
    # Mock no conflict
    mock_page = Mock()
    mock_page.revision = "123"
    mock_site.pages = {"Test": mock_page}

    article = Article(title="Test", url="https://example.com", wikitext="test", revision_id="123")

    edit = ProposedEdit(
        edit_type=EditType.GRAMMAR_FIX,
        original_text="test",
        proposed_text="test2",
        rationale="fix",
        confidence="high",
        approved=False,  # Not approved
    )

    proposal = EditProposal(id="test-id", article=article, edits=[edit])

    success, message = push_service.push_edits(proposal, "modified text")

    assert success is False
    assert "No approved edits" in message


def test_preview_diff(push_service):
    """Test diff preview generation."""
    article = Article(
        title="Test",
        url="https://example.com",
        wikitext="Line 1\nLine 2\nLine 3",
        revision_id="123",
    )

    proposal = EditProposal(id="test-id", article=article, edits=[])

    modified = "Line 1\nLine 2 modified\nLine 3"
    diff = push_service.preview_diff(proposal, modified)

    assert "original" in diff.lower() or "modified" in diff.lower()
    assert len(diff) > 0
