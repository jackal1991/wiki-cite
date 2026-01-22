"""Tests for source finder."""

import pytest
from unittest.mock import Mock, patch

from wiki_cite.source_finder import SourceFinder, ReliabilityRating
from wiki_cite.models import Source, SourceType


@pytest.fixture
def source_finder():
    """Create source finder instance."""
    return SourceFinder()


def test_check_reliability_for_nytimes(source_finder):
    """Test reliability check for New York Times."""
    rating = source_finder.check_reliability("https://www.nytimes.com/article")
    assert rating == ReliabilityRating.GENERALLY_RELIABLE


def test_check_reliability_for_gov_domain(source_finder):
    """Test reliability check for government domains."""
    rating = source_finder.check_reliability("https://www.whitehouse.gov/page")
    assert rating == ReliabilityRating.GENERALLY_RELIABLE


def test_check_reliability_for_edu_domain(source_finder):
    """Test reliability check for educational domains."""
    rating = source_finder.check_reliability("https://www.stanford.edu/research")
    assert rating == ReliabilityRating.GENERALLY_RELIABLE


def test_check_reliability_for_potentially_unreliable(source_finder):
    """Test reliability check for potentially unreliable sources."""
    rating = source_finder.check_reliability("https://www.dailymail.co.uk/article")
    assert rating == ReliabilityRating.POTENTIALLY_UNRELIABLE


def test_check_reliability_for_unknown_domain(source_finder):
    """Test reliability check for unknown domains."""
    rating = source_finder.check_reliability("https://www.random-blog.com/post")
    assert rating == ReliabilityRating.SITUATIONALLY_RELIABLE


def test_verify_url_exists_success(source_finder):
    """Test URL verification for existing URL."""
    with patch.object(source_finder.session, "head") as mock_head:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_head.return_value = mock_response

        result = source_finder.verify_url_exists("https://example.com")
        assert result is True


def test_verify_url_exists_failure(source_finder):
    """Test URL verification for non-existing URL."""
    with patch.object(source_finder.session, "head") as mock_head:
        mock_head.side_effect = Exception("Connection failed")

        with patch.object(source_finder.session, "get") as mock_get:
            mock_get.side_effect = Exception("Connection failed")

            result = source_finder.verify_url_exists("https://invalid.com")
            assert result is False


def test_extract_claims_from_simple_text(source_finder):
    """Test extracting claims from simple text."""
    wikitext = """
This is the first sentence about something.
This is the second sentence with more information.
"""
    claims = source_finder.extract_claims(wikitext)

    assert len(claims) >= 2
    assert any("first sentence" in claim for claim in claims)


def test_extract_claims_removes_templates(source_finder):
    """Test that templates are removed when extracting claims."""
    wikitext = """
This is a sentence.
{{cite book|title=Test}}
This is another sentence.
"""
    claims = source_finder.extract_claims(wikitext)

    # Should extract sentences but not template
    for claim in claims:
        assert "cite book" not in claim.lower()


def test_extract_claims_removes_references(source_finder):
    """Test that reference tags are removed."""
    wikitext = """
This is a sentence<ref>Reference text here</ref>.
Another sentence without ref.
"""
    claims = source_finder.extract_claims(wikitext)

    # Should not include reference text
    for claim in claims:
        assert "<ref>" not in claim
        assert "Reference text here" not in claim


def test_search_semantic_scholar_without_api_key(source_finder):
    """Test that search returns empty without API key."""
    # Ensure no API key is set
    source_finder.config.semantic_scholar_api_key = ""

    sources = source_finder.search_semantic_scholar("test query")
    assert sources == []


def test_search_crossref_without_email(source_finder):
    """Test that CrossRef search returns empty without email."""
    # Ensure no email is set
    source_finder.config.crossref_email = ""

    sources = source_finder.search_crossref("test query")
    assert sources == []


def test_find_sources_for_claim_returns_sorted(source_finder):
    """Test that sources are sorted by reliability."""
    with patch.object(source_finder, "search_semantic_scholar") as mock_semantic:
        # Mock sources with different reliability
        source1 = Source(
            title="Test Article 1",
            url="https://example.com/1",
            source_type=SourceType.WEB,
            reliability=ReliabilityRating.POTENTIALLY_UNRELIABLE,
        )
        source2 = Source(
            title="Test Article 2",
            url="https://example.com/2",
            source_type=SourceType.JOURNAL,
            reliability=ReliabilityRating.GENERALLY_RELIABLE,
        )
        mock_semantic.return_value = [source1, source2]

        sources = source_finder.find_sources_for_claim("test claim")

        # Generally reliable should come first
        assert sources[0].reliability == ReliabilityRating.GENERALLY_RELIABLE
