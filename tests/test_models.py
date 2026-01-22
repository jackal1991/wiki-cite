"""Tests for data models."""

import pytest

from wiki_cite.models import (
    EditProposal,
    EditType,
    ProposedEdit,
    Source,
    SourceType,
    Article,
)


def test_source_citation_template():
    """Test citation template generation."""
    # Test book citation
    book = Source(
        title="Test Book",
        authors=["John Doe"],
        publication_date="2020",
        publisher="Test Publisher",
        isbn="123-456-789",
        source_type=SourceType.BOOK,
    )

    citation = book.to_citation_template()
    assert "{{cite book" in citation
    assert "Test Book" in citation
    assert "Doe" in citation
    assert "2020" in citation

    # Test news citation
    news = Source(
        title="Test Article",
        authors=["Jane Smith"],
        publication_date="2021-01-01",
        publisher="Test News",
        url="http://example.com",
        source_type=SourceType.NEWS,
    )

    citation = news.to_citation_template()
    assert "{{cite news" in citation
    assert "Test Article" in citation
    assert "Smith" in citation


def test_edit_proposal_summary():
    """Test edit summary generation."""
    article = Article(
        title="Test Article",
        url="http://example.com",
        wikitext="Test content",
        revision_id="12345",
    )

    edit1 = ProposedEdit(
        edit_type=EditType.CITATION_ADDED,
        original_text="test",
        proposed_text="test<ref>source</ref>",
        rationale="Adding citation",
        confidence="high",
        approved=True,
    )

    edit2 = ProposedEdit(
        edit_type=EditType.GRAMMAR_FIX,
        original_text="is",
        proposed_text="are",
        rationale="Grammar fix",
        confidence="high",
        approved=True,
    )

    proposal = EditProposal(
        id="test-123",
        article=article,
        edits=[edit1, edit2],
    )

    summary = proposal.get_edit_summary()
    assert "citation" in summary.lower()
    assert "grammar" in summary.lower()
    assert "AI-assisted" in summary


def test_get_approved_edits():
    """Test filtering approved edits."""
    article = Article(
        title="Test Article",
        url="http://example.com",
        wikitext="Test content",
        revision_id="12345",
    )

    edit1 = ProposedEdit(
        edit_type=EditType.CITATION_ADDED,
        original_text="test1",
        proposed_text="test1<ref>source</ref>",
        rationale="Adding citation",
        confidence="high",
        approved=True,
    )

    edit2 = ProposedEdit(
        edit_type=EditType.GRAMMAR_FIX,
        original_text="test2",
        proposed_text="test2 fixed",
        rationale="Grammar",
        confidence="high",
        approved=False,
    )

    edit3 = ProposedEdit(
        edit_type=EditType.WIKILINK_ADDED,
        original_text="test3",
        proposed_text="[[test3]]",
        rationale="Add wikilink",
        confidence="high",
        approved=True,
    )

    proposal = EditProposal(
        id="test-123",
        article=article,
        edits=[edit1, edit2, edit3],
    )

    approved = proposal.get_approved_edits()
    assert len(approved) == 2
    assert edit1 in approved
    assert edit3 in approved
    assert edit2 not in approved
