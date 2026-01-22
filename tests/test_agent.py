"""Tests for Claude agent."""

import pytest
from unittest.mock import Mock, patch, MagicMock

from wiki_cite.agent import ClaudeAgent
from wiki_cite.models import Article, EditType, ProposedEdit


@pytest.fixture
def agent():
    """Create agent instance."""
    with patch("wiki_cite.agent.Anthropic"):
        return ClaudeAgent()


@pytest.fixture
def sample_article():
    """Create a sample article for testing."""
    return Article(
        title="Test Article",
        url="https://en.wikipedia.org/wiki/Test_Article",
        wikitext="This is a test article. It was created in 2020.",
        revision_id="12345",
    )


def test_extract_json_from_response_with_code_block(agent):
    """Test extracting JSON from markdown code block."""
    response = """Here are the edits:
```json
[
  {"edit_type": "grammar", "original_text": "test", "proposed_text": "test2"}
]
```
"""
    result = agent._extract_json_from_response(response)
    assert len(result) == 1
    assert result[0]["edit_type"] == "grammar"


def test_extract_json_from_response_with_raw_json(agent):
    """Test extracting raw JSON from response."""
    response = '[{"edit_type": "citation", "original_text": "test"}]'
    result = agent._extract_json_from_response(response)
    assert len(result) == 1
    assert result[0]["edit_type"] == "citation"


def test_extract_json_from_invalid_response(agent):
    """Test extracting JSON from invalid response."""
    response = "This is not JSON"
    result = agent._extract_json_from_response(response)
    assert result == []


def test_apply_edits_single_edit(agent, sample_article):
    """Test applying a single edit to article."""
    edit = ProposedEdit(
        edit_type=EditType.GRAMMAR_FIX,
        original_text="is a test",
        proposed_text="is a great test",
        rationale="Enhancement",
        confidence="high",
    )

    result = agent.apply_edits(sample_article, [edit])

    assert "is a great test" in result
    assert "is a test" not in result or result.count("is a test") < sample_article.wikitext.count(
        "is a test"
    )


def test_apply_edits_multiple_edits(agent, sample_article):
    """Test applying multiple edits to article."""
    edit1 = ProposedEdit(
        edit_type=EditType.GRAMMAR_FIX,
        original_text="This is",
        proposed_text="This was",
        rationale="Tense correction",
        confidence="high",
    )

    edit2 = ProposedEdit(
        edit_type=EditType.WIKILINK_ADDED,
        original_text="2020",
        proposed_text="[[2020]]",
        rationale="Add wikilink",
        confidence="high",
    )

    result = agent.apply_edits(sample_article, [edit1, edit2])

    assert "This was" in result
    assert "[[2020]]" in result


def test_apply_edits_no_edits(agent, sample_article):
    """Test applying empty edit list."""
    result = agent.apply_edits(sample_article, [])
    assert result == sample_article.wikitext


def test_build_sources_context_with_claims(agent, sample_article):
    """Test building source context."""
    with patch.object(agent.source_finder, "extract_claims") as mock_extract:
        mock_extract.return_value = ["Test claim 1", "Test claim 2"]

        with patch.object(agent.source_finder, "find_sources_for_claim") as mock_find:
            mock_find.return_value = []

            context = agent._build_sources_context(sample_article)

            assert "Available Sources" in context
            mock_extract.assert_called_once()


def test_build_sources_context_no_claims(agent, sample_article):
    """Test building source context when no claims found."""
    with patch.object(agent.source_finder, "extract_claims") as mock_extract:
        mock_extract.return_value = []

        context = agent._build_sources_context(sample_article)

        assert "No clear factual claims" in context
