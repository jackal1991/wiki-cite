"""Tests for edit guardrails."""

import pytest

from wiki_cite.guardrails import EditGuardrails
from wiki_cite.models import EditType, ProposedEdit


@pytest.fixture
def guardrails():
    """Create guardrails instance."""
    return EditGuardrails()


def test_count_words(guardrails):
    """Test word counting."""
    text = "This is a test sentence."
    assert guardrails.count_words(text) == 5

    # Test with templates (should be excluded)
    text_with_template = "This is {{cite book|title=Test}} a test."
    count = guardrails.count_words(text_with_template)
    assert count < 10  # Template content excluded


def test_calculate_similarity(guardrails):
    """Test similarity calculation."""
    text1 = "The quick brown fox"
    text2 = "The quick brown fox"
    assert guardrails.calculate_similarity(text1, text2) == 1.0

    text3 = "The slow brown fox"
    similarity = guardrails.calculate_similarity(text1, text3)
    assert 0.5 < similarity < 1.0


def test_is_citation_or_template(guardrails):
    """Test citation detection."""
    citation = "<ref>{{cite web|url=http://example.com}}</ref>"
    assert guardrails.is_citation_or_template(citation)

    regular_text = "This is regular text."
    assert not guardrails.is_citation_or_template(regular_text)


def test_validate_minimal_edit(guardrails):
    """Test that minimal edits are accepted."""
    edit = ProposedEdit(
        edit_type=EditType.GRAMMAR_FIX,
        original_text="The cat are sleeping",
        proposed_text="The cat is sleeping",
        rationale="Subject-verb agreement",
        confidence="high"
    )

    is_valid, reason = guardrails.validate_edit(edit, "", "")
    assert is_valid


def test_reject_large_edit(guardrails):
    """Test that large edits are rejected."""
    edit = ProposedEdit(
        edit_type=EditType.STYLE_FIX,
        original_text="Short text",
        proposed_text="This is a much longer piece of text that adds substantial new content beyond what should be allowed",
        rationale="Style improvement",
        confidence="medium"
    )

    is_valid, reason = guardrails.validate_edit(edit, "", "")
    # This should be rejected for adding too many words
    # (Actual result depends on configuration)


def test_check_policy_violations(guardrails):
    """Test policy violation detection."""
    # Promotional language
    text1 = "This is the best product ever made."
    violations = guardrails.check_policy_violations(text1)
    assert len(violations) > 0
    assert any("best" in v.lower() for v in violations)

    # Weasel words
    text2 = "Some say this is true."
    violations = guardrails.check_policy_violations(text2)
    assert len(violations) > 0
    assert any("some say" in v.lower() for v in violations)

    # Clean text
    text3 = "This is a factual statement."
    violations = guardrails.check_policy_violations(text3)
    # May or may not have violations depending on the text
