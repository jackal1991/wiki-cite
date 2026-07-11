"""Tests for article picker."""

import random
import sqlite3

import pytest
from unittest.mock import Mock

from wiki_cite.article_picker import ArticlePicker, CandidateScorer, _build_session, build_focused_excerpt
from wiki_cite.config import Config, get_config, set_config
from wiki_cite.models import CandidateArticle
from wiki_cite.seen_store import SeenStore


@pytest.fixture
def mock_site():
    """Create a mock mwclient site."""
    site = Mock()
    return site


@pytest.fixture
def picker(mock_site):
    """Create article picker with mock site."""
    return ArticlePicker(site=mock_site)


def test_build_session_retries_on_429():
    """The session mwclient uses must back off and retry on 429/5xx, honoring
    Retry-After, since mwclient itself raises immediately on a 429 (client.py's
    raw_call only retries 5xx/connection errors, never 4xx)."""
    session = _build_session("TestBot/1.0 (test@example.com)")
    adapter = session.get_adapter("https://en.wikipedia.org")
    assert 429 in adapter.max_retries.status_forcelist
    assert adapter.max_retries.respect_retry_after_header is True
    assert session.headers["User-Agent"] == "TestBot/1.0 (test@example.com)"


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


def test_fetch_candidates_skips_seen(mock_site):
    """Already-seen article titles are skipped before any page fetch."""
    seen = Mock()
    seen.is_seen = Mock(side_effect=lambda title: title == "Old News")
    seen.dimension_rates = Mock(return_value={})
    picker = ArticlePicker(site=mock_site, seen_store=seen)

    seen_page = Mock()
    seen_page.name = "Old News"
    fresh_page = Mock()
    fresh_page.name = "Fresh Article"
    fresh_page.redirect = False
    fresh_page.namespace = 0
    fresh_page.protection = {}
    fresh_page.revision = "42"
    fresh_page.text = Mock(return_value="This is a fresh and notable claim about the subject.{{Citation needed}}")
    fresh_page.categories = Mock(return_value=["News"])
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": [seen_page, fresh_page]}

    titles = [c.title for c in picker.fetch_candidates(limit=5)]
    assert "Old News" not in titles
    assert "Fresh Article" in titles
    seen_page.text.assert_not_called()  # seen skip happens before any page fetch


def test_fetch_candidates_passes_category_overrides(mock_site):
    """A mock page in an excluded category is filtered out when fetch_candidates
    is called with an exclude_categories override."""
    picker = ArticlePicker(site=mock_site)

    sports_category = Mock()
    sports_category.name = "Category:Sports"
    excluded_page = Mock()
    excluded_page.name = "Excluded Article"
    excluded_page.redirect = False
    excluded_page.namespace = 0
    excluded_page.protection = {}
    excluded_page.revision = "1"
    excluded_page.text = Mock(return_value="A claim about the subject.{{Citation needed}}")
    excluded_page.categories = Mock(return_value=[sports_category])

    history_category = Mock()
    history_category.name = "Category:History"
    included_page = Mock()
    included_page.name = "Included Article"
    included_page.redirect = False
    included_page.namespace = 0
    included_page.protection = {}
    included_page.revision = "2"
    included_page.text = Mock(return_value="A fresh and notable claim about the subject.{{Citation needed}}")
    included_page.categories = Mock(return_value=[history_category])

    mock_site.pages = {"Category:All_articles_with_unsourced_statements": [excluded_page, included_page]}

    titles = [c.title for c in picker.fetch_candidates(limit=5, exclude_categories=["Sports"])]
    assert "Excluded Article" not in titles
    assert "Included Article" in titles


def test_category_filter_include_only_overlap_passes():
    """Include-only, article overlaps -> passes."""
    ok, reason = ArticlePicker.category_filter(["History"], ["History"], [])
    assert ok is True
    assert reason == ""


def test_category_filter_include_only_no_overlap_rejects():
    """Include-only, article does not overlap -> rejected."""
    ok, reason = ArticlePicker.category_filter(["Sports"], ["History"], [])
    assert ok is False
    assert reason == "not in included categories"


def test_category_filter_exclude_only_overlap_rejects():
    """Exclude-only, article overlaps -> rejected."""
    ok, reason = ArticlePicker.category_filter(["Sports"], [], ["Sports"])
    assert ok is False
    assert "excluded category" in reason


def test_category_filter_exclude_takes_precedence_over_include():
    """Article hits both exclude and include -> rejected as excluded."""
    ok, reason = ArticlePicker.category_filter(["Sports"], ["Sports"], ["Sports"])
    assert ok is False
    assert "excluded category" in reason


def test_category_filter_both_empty_is_noop():
    """Both lists empty -> passes (matches today's behavior)."""
    ok, reason = ArticlePicker.category_filter(["Anything"], [], [])
    assert ok is True
    assert reason == ""


def test_category_filter_normalizes_case_underscore_and_prefix():
    """Matching is case/underscore/``Category:`` prefix insensitive."""
    ok, reason = ArticlePicker.category_filter(["Living people"], ["living_people"], [])
    assert ok is True
    assert reason == ""


def test_is_candidate_rejects_excluded_category_even_with_citation_needed(picker):
    """An excluded-category article is rejected even with a {{Citation needed}} tag."""
    picker.config.article_selection.exclude_categories = ["Sports"]
    page = Mock()
    page.redirect = False
    page.namespace = 0
    page.protection = {}
    page.text = Mock(return_value="The tower is the tallest structure in the region.{{Citation needed}}")
    category = Mock()
    category.name = "Category:Sports"
    page.categories = Mock(return_value=[category])

    try:
        is_candidate, reason = picker.is_candidate(page)
        assert is_candidate is False
        assert "excluded category" in reason
    finally:
        picker.config.article_selection.exclude_categories = []


def _make_candidate_page(name, revision="1"):
    page = Mock()
    page.name = name
    page.redirect = False
    page.namespace = 0
    page.protection = {}
    page.revision = revision
    page.text = Mock(return_value=f"{name} is notable for something.{{{{Citation needed}}}}")
    page.categories = Mock(return_value=["Test"])
    return page


def test_fetch_candidates_pool_preserves_order(mock_site):
    """With no scorer active, pooling is a no-op reorder: category order in, same order out."""
    picker = ArticlePicker(site=mock_site)
    pages = [_make_candidate_page(f"Article {i}") for i in range(4)]
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": pages}

    titles = [c.title for c in picker.fetch_candidates(limit=3)]
    assert titles == ["Article 0", "Article 1", "Article 2"]


def test_fetch_candidates_sets_start_sortkey_prefix(mock_site, restore_config):
    """category_start_prefix threads into the mwclient category as
    gcmstartsortkeyprefix, so scanning skips ahead of the default sortkey order
    (which lists digit/punctuation-titled pages before "A")."""
    config = get_config()
    config.article_selection.category_start_prefix = "A"
    set_config(config)

    picker = ArticlePicker(site=mock_site)
    cat_page = Mock()
    cat_page.args = {}
    cat_page.__iter__ = Mock(return_value=iter([]))
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": cat_page}

    list(picker.fetch_candidates(limit=3))

    assert cat_page.args["gcmstartsortkeyprefix"] == "A"


def test_fetch_candidates_no_start_prefix_leaves_args_untouched(mock_site, restore_config):
    """An empty category_start_prefix (the default) must not touch cat_page.args."""
    config = get_config()
    config.article_selection.category_start_prefix = ""
    set_config(config)

    picker = ArticlePicker(site=mock_site)
    cat_page = Mock()
    cat_page.args = {}
    cat_page.__iter__ = Mock(return_value=iter([]))
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": cat_page}

    list(picker.fetch_candidates(limit=3))

    assert "gcmstartsortkeyprefix" not in cat_page.args


@pytest.fixture
def restore_config():
    """Config is global (get_config/set_config); restore it after tests that override it."""
    original = get_config()
    yield
    set_config(original)


def _mock_category(name):
    """get_categories() expects mwclient category objects (a `.name` attribute),
    not plain strings — mirror that shape for pages used in scorer tests."""
    cat = Mock()
    cat.name = name
    return cat


def _candidate(categories, has_infobox=False):
    return CandidateArticle(title="T", url="u", wikitext="w", body_line_count=1, revision_id="1", categories=categories, has_infobox=has_infobox)


def test_scorer_prefers_higher_rate_dimension():
    """AC4.1: a candidate correlated with a higher-rate dimension value scores higher."""
    rates = {"categories": {"news-ish": (9, 10), "journal-ish": (1, 10)}}
    scorer = CandidateScorer(rates, epsilon=0, min_samples=5)

    news = _candidate(["news-ish"])
    journal = _candidate(["journal-ish"])

    assert scorer.score(news) > scorer.score(journal)


def test_scorer_neutral_prior_for_undersampled():
    """AC5.2: an under-sampled (or never-seen) value scores at the neutral 0.5 prior, not 0."""
    scorer = CandidateScorer(rates={}, epsilon=0, min_samples=5)
    unknown = _candidate(["never-seen-category"])

    assert scorer.score(unknown) == 0.5


def test_scorer_epsilon_can_reorder():
    """AC5.1: epsilon jitter means no strict, sticky ordering — a low-rate candidate can
    sort ahead of a high-rate one across seeded runs."""
    rates = {"categories": {"high": (9, 10), "low": (1, 10)}}
    high = _candidate(["high"])
    low = _candidate(["low"])

    flipped = False
    for seed in range(50):
        random.seed(seed)
        scorer = CandidateScorer(rates, epsilon=1.0, min_samples=5)
        if scorer.score(low) > scorer.score(high):
            flipped = True
            break
    assert flipped


def test_fetch_candidates_ranks_by_learned_rate(mock_site, tmp_path, restore_config):
    """AC4.1 end-to-end: a clear rate gap between two categories re-ranks the pool,
    with zero Claude/agent calls (the picker has no agent reference at all)."""
    store = SeenStore(tmp_path / "seen.db")
    for _ in range(9):
        store.record_outcome("x", "1", "approved", categories=["news-ish"])
    store.record_outcome("y", "1", "rejected", categories=["news-ish"])
    for _ in range(9):
        store.record_outcome("z", "1", "rejected", categories=["journal-ish"])
    store.record_outcome("w", "1", "approved", categories=["journal-ish"])

    config = Config()
    config.feedback.enabled = True
    config.feedback.epsilon = 0.0
    set_config(config)

    picker = ArticlePicker(site=mock_site, seen_store=store)
    news_page = _make_candidate_page("News Article")
    news_page.categories = Mock(return_value=[_mock_category("news-ish")])
    journal_page = _make_candidate_page("Journal Article")
    journal_page.categories = Mock(return_value=[_mock_category("journal-ish")])
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": [journal_page, news_page]}

    titles = [c.title for c in picker.fetch_candidates(limit=1)]
    assert titles == ["News Article"]


def test_fetch_candidates_disabled_feedback_is_category_order(mock_site, tmp_path, restore_config):
    """feedback.enabled=False -> category order, even with a seeded DB showing a rate gap."""
    store = SeenStore(tmp_path / "seen.db")
    for _ in range(9):
        store.record_outcome("x", "1", "approved", categories=["news-ish"])
    for _ in range(9):
        store.record_outcome("z", "1", "rejected", categories=["journal-ish"])

    config = Config()
    config.feedback.enabled = False
    set_config(config)

    picker = ArticlePicker(site=mock_site, seen_store=store)
    journal_page = _make_candidate_page("Journal Article")
    journal_page.categories = Mock(return_value=[_mock_category("journal-ish")])
    news_page = _make_candidate_page("News Article")
    news_page.categories = Mock(return_value=[_mock_category("news-ish")])
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": [journal_page, news_page]}

    titles = [c.title for c in picker.fetch_candidates(limit=2)]
    assert titles == ["Journal Article", "News Article"]


def test_fetch_candidates_missing_db_matches_category_order(mock_site, tmp_path, restore_config):
    """AC6.1: a fresh/empty outcomes DB (no history yet) still yields plain category order."""
    config = Config()
    config.feedback.epsilon = 0.0
    set_config(config)

    store = SeenStore(tmp_path / "fresh.db")
    picker = ArticlePicker(site=mock_site, seen_store=store)
    pages = [_make_candidate_page(f"Article {i}") for i in range(3)]
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": pages}

    titles = [c.title for c in picker.fetch_candidates(limit=3)]
    assert titles == ["Article 0", "Article 1", "Article 2"]


def test_fetch_candidates_corrupt_db_falls_back(mock_site):
    """AC6.3: dimension_rates raising sqlite3.Error -> _build_scorer returns None
    -> category order, no raise out of fetch_candidates."""
    seen = Mock()
    seen.is_seen = Mock(return_value=False)
    seen.dimension_rates = Mock(side_effect=sqlite3.OperationalError("disk I/O error"))
    picker = ArticlePicker(site=mock_site, seen_store=seen)

    pages = [_make_candidate_page(f"Article {i}") for i in range(3)]
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": pages}

    titles = [c.title for c in picker.fetch_candidates(limit=3)]
    assert titles == ["Article 0", "Article 1", "Article 2"]


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
