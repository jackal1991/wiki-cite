"""Tests for source finder."""

import pytest
from unittest.mock import Mock, patch

from wiki_cite.source_finder import SourceFinder, ReliabilityRating, extract_citation_url, extract_all_citation_urls
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


def test_search_web_without_api_key(source_finder):
    """Test that web search returns empty without an API key."""
    source_finder.config.brave_api_key = ""

    sources = source_finder.search_web("test query")
    assert sources == []


def test_search_web_with_api_key(source_finder):
    """Test that web search parses Brave results into Source objects."""
    source_finder.config.brave_api_key = "test-key"

    with patch.object(source_finder.session, "get") as mock_get:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "web": {
                "results": [
                    {
                        "title": "Example Article",
                        "url": "https://www.nytimes.com/example",
                        "profile": {"name": "The New York Times"},
                    }
                ]
            }
        }
        mock_get.return_value = mock_response

        sources = source_finder.search_web("test claim")

        assert len(sources) == 1
        assert sources[0].title == "Example Article"
        assert sources[0].url == "https://www.nytimes.com/example"
        assert sources[0].reliability == ReliabilityRating.GENERALLY_RELIABLE


def test_search_web_caches_identical_query(source_finder):
    """Test that a repeated identical search is served from cache, not the network."""
    source_finder.config.brave_api_key = "test-key"

    with patch.object(source_finder.session, "get") as mock_get:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"web": {"results": [{"title": "Example", "url": "https://example.com"}]}}
        mock_get.return_value = mock_response

        first = source_finder.search_web("test claim")
        second = source_finder.search_web("test claim")

        assert mock_get.call_count == 1
        assert first == second


def test_search_web_different_query_bypasses_cache(source_finder):
    """Test that a different query is not served from another query's cache entry."""
    source_finder.config.brave_api_key = "test-key"

    with patch.object(source_finder.session, "get") as mock_get:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"web": {"results": []}}
        mock_get.return_value = mock_response

        source_finder.search_web("first claim")
        source_finder.search_web("second claim")

        assert mock_get.call_count == 2


def test_search_semantic_scholar_rate_limited_logs_and_returns_empty(source_finder, caplog):
    """Test that a 429 is logged (not silently swallowed) and yields no sources."""
    source_finder.config.semantic_scholar_api_key = "test-key"

    with patch.object(source_finder.session, "get") as mock_get:
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "30"}
        mock_get.return_value = mock_response

        with caplog.at_level("WARNING"):
            sources = source_finder.search_semantic_scholar("test query")

        assert sources == []
        assert any("429" in record.message for record in caplog.records)


def test_source_finder_session_retries_on_429():
    """Test that the shared session is configured to retry rate-limited responses."""
    finder = SourceFinder()
    adapter = finder.session.get_adapter("https://api.semanticscholar.org")
    assert 429 in adapter.max_retries.status_forcelist
    assert adapter.max_retries.respect_retry_after_header is True


def test_fetch_page_preview_no_url(source_finder):
    """Test that preview fetching handles a missing URL gracefully."""
    preview = source_finder.fetch_page_preview("")
    assert preview["ok"] is False
    assert preview["error"]


def test_fetch_page_preview_extracts_metadata(source_finder):
    """Test that preview fetching extracts title/description from HTML."""
    html = b"""
    <html><head>
        <title>Fallback Title</title>
        <meta property="og:title" content="Example Page Title">
        <meta property="og:description" content="An example description.">
        <meta property="og:site_name" content="Example Site">
    </head><body></body></html>
    """

    with patch.object(source_finder.session, "get") as mock_get:
        mock_response = Mock()
        mock_response.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_response.raw.read.return_value = html
        mock_get.return_value = mock_response

        preview = source_finder.fetch_page_preview("https://example.com/article")

        assert preview["ok"] is True
        assert preview["title"] == "Example Page Title"
        assert preview["description"] == "An example description."
        assert preview["site_name"] == "Example Site"


def test_fetch_page_preview_rejects_non_html(source_finder):
    """Test that preview fetching skips non-HTML content types."""
    with patch.object(source_finder.session, "get") as mock_get:
        mock_response = Mock()
        mock_response.headers = {"Content-Type": "application/pdf"}
        mock_get.return_value = mock_response

        preview = source_finder.fetch_page_preview("https://example.com/paper.pdf")

        assert preview["ok"] is False
        assert "PDF" in preview["error"] or "pdf" in preview["error"]


def test_extract_citation_url_from_cite_template():
    """Test extracting a URL from a {{cite web}} template."""
    text = "were accused<ref>{{cite web |title=Test |url=https://example.com/page |date=2020}}</ref>"
    assert extract_citation_url(text) == "https://example.com/page"


def test_extract_citation_url_from_bare_url():
    """Test extracting a bare URL when no citation template is present."""
    text = "See https://example.com/source for details."
    assert extract_citation_url(text) == "https://example.com/source"


def test_extract_citation_url_returns_none_when_absent():
    """Test that extraction returns None when there's no URL."""
    text = "Fixed grammar, no citation here."
    assert extract_citation_url(text) is None


def test_extract_all_citation_urls_multiple_cites_and_bare():
    """Test that every cite-template URL and bare URL is returned, in first-seen order."""
    text = (
        "First claim.<ref>{{cite web |title=A |url=https://example.com/a}}</ref> "
        "Second claim.<ref>{{cite news |title=B |URL=https://example.com/b}}</ref> "
        "See also https://example.com/c for background."
    )
    assert extract_all_citation_urls(text) == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]


def test_extract_all_citation_urls_dedups_preserving_order():
    """Test that a URL repeated as a cite param and again bare is surfaced once, in first-seen order."""
    text = (
        "First claim.<ref>{{cite web |title=A |url=https://example.com/a}}</ref> "
        "Repeated claim.<ref>{{cite web |title=A2 |url=https://example.com/a}}</ref> "
        "Also see https://example.com/a and https://example.com/b for details."
    )
    assert extract_all_citation_urls(text) == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_extract_all_citation_urls_empty_when_no_citations():
    """Test that a citation-free page returns an empty list, not None or an error."""
    text = "Fixed grammar, no citation here."
    assert extract_all_citation_urls(text) == []


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
