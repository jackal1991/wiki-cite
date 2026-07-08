"""Tests for article picker."""

import pytest
from unittest.mock import Mock

from wiki_cite.article_picker import ArticlePicker, build_focused_excerpt


@pytest.fixture
def mock_site():
    """Create a mock mwclient site."""
    site = Mock()
    return site


@pytest.fixture
def picker(mock_site):
    """Create article picker with mock site."""
    return ArticlePicker(site=mock_site)


def test_count_body_lines_simple(picker):
    """Test counting body lines in simple text."""
    text = """Line one.
Line two.
Line three."""
    count = picker.count_body_lines(text)
    assert count == 3


def test_count_body_lines_excludes_templates(picker):
    """Test that templates are excluded from line count."""
    text = """Line one.
{{Infobox
|name = Test
|value = Something
}}
Line two."""
    count = picker.count_body_lines(text)
    # Should only count the actual content lines, not template
    assert count <= 2


def test_count_body_lines_excludes_references(picker):
    """Test that references section is excluded."""
    text = """Line one.
Line two.

== References ==
* Reference 1
* Reference 2"""
    count = picker.count_body_lines(text)
    assert count == 2


def test_is_blp_detects_living_people_category(picker):
    """Test BLP detection via categories."""
    categories = ["Living people", "American actors"]
    is_blp = picker.is_blp("", categories)
    assert is_blp is True


def test_is_blp_detects_blp_template(picker):
    """Test BLP detection via template."""
    text = "{{BLP}}\nThis is an article."
    is_blp = picker.is_blp(text, [])
    assert is_blp is True


def test_is_blp_returns_false_for_regular_article(picker):
    """Test that regular articles are not flagged as BLP."""
    text = "This is a regular article about a historical event."
    categories = ["History", "Events"]
    is_blp = picker.is_blp(text, categories)
    assert is_blp is False


def test_is_candidate_rejects_redirect(picker, mock_site):
    """Test that redirects are rejected."""
    page = Mock()
    page.redirect = True

    is_candidate, reason = picker.is_candidate(page)
    assert is_candidate is False
    assert "redirect" in reason


def test_is_candidate_rejects_empty_page(picker):
    """Test that empty pages are rejected."""
    page = Mock()
    page.redirect = False
    page.namespace = 0
    page.text = Mock(return_value="")
    page.categories = Mock(return_value=[])
    page.protection = {}

    is_candidate, reason = picker.is_candidate(page)
    assert is_candidate is False
    assert "empty" in reason.lower()


def test_is_candidate_rejects_non_article_namespace(picker):
    """Category/Template/etc. pages (namespace != 0) are rejected."""
    page = Mock()
    page.redirect = False
    page.namespace = 14  # Category namespace

    is_candidate, reason = picker.is_candidate(page)
    assert is_candidate is False
    assert "namespace" in reason.lower()


def test_is_candidate_rejects_article_without_citation_needed(picker):
    """An article with no {{Citation needed}} tag is not a candidate."""
    page = Mock()
    page.redirect = False
    page.namespace = 0
    page.protection = {}
    page.text = Mock(return_value="The sky is blue and well documented in many sources.")
    page.categories = Mock(return_value=["Colors"])

    is_candidate, reason = picker.is_candidate(page)
    assert is_candidate is False
    assert "citation-needed" in reason.lower()


def test_is_candidate_accepts_article_with_citation_needed(picker):
    """An article with a {{Citation needed}} tag is a candidate."""
    page = Mock()
    page.redirect = False
    page.namespace = 0
    page.protection = {}
    page.text = Mock(return_value="The tower is the tallest structure in the region.{{Citation needed}}")
    page.categories = Mock(return_value=["Buildings"])

    is_candidate, reason = picker.is_candidate(page)
    assert is_candidate is True
    assert reason == ""


def test_extract_citation_needed_claims_variants(picker):
    """Extracts the preceding sentence for {{Citation needed}}, {{cn}}, {{fact}}."""
    wikitext = "The dam was completed in 1931. It generated power for the whole valley.{{Citation needed}} Later it was expanded twice.{{cn}} A museum opened nearby in 1990.{{fact}}"
    claims = picker.extract_citation_needed_claims(wikitext)
    assert "It generated power for the whole valley." in claims
    assert "Later it was expanded twice." in claims
    assert "A museum opened nearby in 1990." in claims


def test_extract_citation_needed_claims_none(picker):
    """No tags -> no claims."""
    assert picker.extract_citation_needed_claims("A fully sourced sentence.<ref>x</ref>") == []


def test_is_candidate_rejects_overlong_article(picker):
    """Cost guard: articles longer than max_wikitext_chars are skipped before analysis."""
    page = Mock()
    page.redirect = False
    page.namespace = 0
    page.protection = {}
    long_text = "word " * 5000 + "{{Citation needed}}"  # ~25k chars, over the 12k default
    page.text = Mock(return_value=long_text)
    page.categories = Mock(return_value=["History"])

    is_candidate, reason = picker.is_candidate(page)
    assert is_candidate is False
    assert "too long" in reason.lower()


def test_build_focused_excerpt_keeps_lead_and_problem_para():
    """Excerpt keeps the lead and the flagged paragraph, drops the rest."""
    wikitext = "'''Widget''' is a small mechanical part used in machines.\n\n== History ==\n\nWidgets were mass-produced from the 1920s onward. Sales peaked in 1955.{{Citation needed}}\n\n== Uses ==\n\nThey appear in clocks and radios. This paragraph is unrelated filler about uses.\n"
    excerpt = build_focused_excerpt(wikitext)
    assert "Widget is a small mechanical part" in excerpt.replace("'''", "")
    assert "Sales peaked in 1955.{{Citation needed}}" in excerpt
    assert "== History ==" in excerpt  # section heading of the flagged paragraph
    assert "unrelated filler" not in excerpt
    assert "[…]" in excerpt


def test_build_focused_excerpt_skips_leading_infobox():
    """The lead is the first prose block, not a leading template/infobox."""
    wikitext = "{{Infobox thing\n|name=Test\n}}\n\n'''Test''' is the subject.{{cn}}\n"
    excerpt = build_focused_excerpt(wikitext)
    assert "Infobox" not in excerpt
    assert "Test is the subject" in excerpt.replace("'''", "")


def test_is_protected_with_edit_protection(picker):
    """Test detection of edit-protected pages."""
    page = Mock()
    page.protection = {"edit": ["sysop"]}

    is_protected = picker.is_protected(page)
    assert is_protected is True


def test_is_protected_without_protection(picker):
    """Test detection of unprotected pages."""
    page = Mock()
    page.protection = {}

    is_protected = picker.is_protected(page)
    assert is_protected is False
