"""Tests for article picker."""

import logging
import random
import sqlite3

import pytest
from unittest.mock import Mock, patch

from wiki_cite.article_picker import ArticlePicker, CandidateScorer, _build_session, build_focused_excerpt, crawl_subcategories
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
    fresh_page = _make_page("Fresh Article", cats=["News"], text="This is a fresh and notable claim about the subject.{{Citation needed}}", revision="42")
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": [seen_page, fresh_page]}

    titles = [c.title for c in picker.fetch_candidates(limit=5)]
    assert "Old News" not in titles
    assert "Fresh Article" in titles
    seen_page.text.assert_not_called()  # seen skip happens before any page fetch
    fresh_page.categories.assert_not_called()  # batch data supplies categories (#18)


def test_fetch_candidates_passes_category_overrides(mock_site):
    """A mock page in an excluded category is filtered out when fetch_candidates
    is called with an exclude_categories override."""
    picker = ArticlePicker(site=mock_site)

    excluded_page = _make_page("Excluded Article", cats=["Sports"], text="A claim about the subject.{{Citation needed}}")
    included_page = _make_page("Included Article", cats=["History"], text="A fresh and notable claim about the subject.{{Citation needed}}", revision="2")

    mock_site.pages = {"Category:All_articles_with_unsourced_statements": [excluded_page, included_page]}

    titles = [c.title for c in picker.fetch_candidates(limit=5, exclude_categories=["Sports"])]
    assert "Excluded Article" not in titles
    assert "Included Article" in titles
    included_page.categories.assert_not_called()  # batch data supplies categories (#18)


def test_expand_categories_replaces_name_with_discovery_file(monkeypatch):
    """A configured name with a discovery file is replaced with that file's discovered set."""
    monkeypatch.setattr(
        "wiki_cite.article_picker.load_expansion",
        lambda name: ["American Politicians", "American Politician Stubs"] if name == "American Politicians" else None,
    )

    result = ArticlePicker._expand_categories(["American Politicians"])

    assert result == ["American Politicians", "American Politician Stubs"]


def test_expand_categories_keeps_name_when_no_discovery_file(monkeypatch):
    """AC4.2: a name with no discovery file stays a single-name direct-match entry."""
    monkeypatch.setattr("wiki_cite.article_picker.load_expansion", lambda name: None)

    result = ArticlePicker._expand_categories(["Sports", "History"])

    assert result == ["Sports", "History"]


def test_expand_categories_dedupes_preserving_order(monkeypatch):
    monkeypatch.setattr(
        "wiki_cite.article_picker.load_expansion",
        lambda name: ["Shared", "A Only"] if name == "A" else ["Shared", "B Only"] if name == "B" else None,
    )

    result = ArticlePicker._expand_categories(["A", "B"])

    assert result == ["Shared", "A Only", "B Only"]


def test_fetch_candidates_expands_include_category_via_discovery_file(mock_site):
    """AC4.1: with an expansion file present for the configured include category, an
    article whose category is a *discovered subcategory* (not the root) passes the filter."""
    picker = ArticlePicker(site=mock_site)

    subcat = Mock()
    subcat.name = "Category:American Politician Stubs"
    matching_page = Mock()
    matching_page.name = "Subcat Article"
    matching_page.redirect = False
    matching_page.namespace = 0
    matching_page.protection = {}
    matching_page.revision = "1"
    matching_page.text = Mock(return_value="A fresh and notable claim about the subject.{{Citation needed}}")
    matching_page.categories = Mock(return_value=[subcat])

    mock_site.pages = {"Category:All_articles_with_unsourced_statements": [matching_page]}

    with patch(
        "wiki_cite.article_picker.load_expansion",
        side_effect=lambda name: ["American Politicians", "American Politician Stubs"] if name == "American Politicians" else None,
    ):
        titles = [c.title for c in picker.fetch_candidates(limit=5, include_categories=["American Politicians"])]

    assert "Subcat Article" in titles


def test_fetch_candidates_no_discovery_file_is_direct_match_only(mock_site):
    """AC4.2: with no file for the configured include category, filtering is
    direct-match-only — the root itself passes, an unrelated subcategory does not — and
    nothing raises."""
    picker = ArticlePicker(site=mock_site)

    root_cat = Mock()
    root_cat.name = "Category:American Politicians"
    root_page = Mock()
    root_page.name = "Root Article"
    root_page.redirect = False
    root_page.namespace = 0
    root_page.protection = {}
    root_page.revision = "1"
    root_page.text = Mock(return_value="A fresh and notable claim about the subject.{{Citation needed}}")
    root_page.categories = Mock(return_value=[root_cat])

    subcat = Mock()
    subcat.name = "Category:Some Undiscovered Subcat"
    subcat_page = Mock()
    subcat_page.name = "Subcat Article"
    subcat_page.redirect = False
    subcat_page.namespace = 0
    subcat_page.protection = {}
    subcat_page.revision = "2"
    subcat_page.text = Mock(return_value="A fresh and notable claim about the subject.{{Citation needed}}")
    subcat_page.categories = Mock(return_value=[subcat])

    mock_site.pages = {"Category:All_articles_with_unsourced_statements": [root_page, subcat_page]}

    with patch("wiki_cite.article_picker.load_expansion", return_value=None):
        titles = [c.title for c in picker.fetch_candidates(limit=5, include_categories=["American Politicians"])]

    assert "Root Article" in titles
    assert "Subcat Article" not in titles


def test_fetch_candidates_blp_relaxation_flag_has_zero_effect_with_no_include_filter(mock_site):
    """AC5.2, exercised through the real fetch_candidates request path (not just
    is_candidate): with relax_blp_when_topic_filtered=True but no include_categories
    configured (the default no-topic-filter case), a BLP article is still excluded —
    the flag must have zero effect when there's no active include filter to scope it to."""
    picker = ArticlePicker(site=mock_site)
    picker.config.guardrails.relax_blp_when_topic_filtered = True
    try:
        blp_category = Mock()
        blp_category.name = "Category:Living people"
        blp_page = Mock()
        blp_page.name = "Living Person Article"
        blp_page.redirect = False
        blp_page.namespace = 0
        blp_page.protection = {}
        blp_page.revision = "1"
        blp_page.text = Mock(return_value="The politician was elected in 1990.{{Citation needed}}")
        blp_page.categories = Mock(return_value=[blp_category])

        mock_site.pages = {"Category:All_articles_with_unsourced_statements": [blp_page]}

        # No include_categories override, and config.article_selection.include_categories
        # defaults to [] — i.e. no topic filter active at all.
        titles = [c.title for c in picker.fetch_candidates(limit=5)]

        assert "Living Person Article" not in titles
    finally:
        picker.config.guardrails.relax_blp_when_topic_filtered = False


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


def _make_blp_page():
    page = Mock()
    page.redirect = False
    page.namespace = 0
    page.protection = {}
    page.text = Mock(return_value="The politician was elected in 1990.{{Citation needed}}")
    category = Mock()
    category.name = "Category:Living people"
    page.categories = Mock(return_value=[category])
    return page


def test_is_candidate_blp_relaxed_with_active_include_filter_is_accepted(picker):
    """AC5.1: flag True + non-empty include list + BLP article -> accepted (BLP check skipped)."""
    picker.config.guardrails.relax_blp_when_topic_filtered = True
    try:
        is_candidate, reason = picker.is_candidate(_make_blp_page(), include_categories=["Living people"])
        assert is_candidate is True
        assert reason == ""
    finally:
        picker.config.guardrails.relax_blp_when_topic_filtered = False


def test_is_candidate_blp_relaxed_without_include_filter_still_rejects(picker):
    """AC5.2: flag True + empty include list + BLP article -> rejected as "BLP article".
    The flag must never silently disable BLP exclusion repo-wide."""
    picker.config.guardrails.relax_blp_when_topic_filtered = True
    try:
        is_candidate, reason = picker.is_candidate(_make_blp_page(), include_categories=[])
        assert is_candidate is False
        assert reason == "BLP article"
    finally:
        picker.config.guardrails.relax_blp_when_topic_filtered = False


def test_is_candidate_blp_default_flag_rejects_with_include_filter(picker):
    """AC5.3: flag False (default) + BLP article -> rejected, identical to current
    behavior, even when an include filter is active."""
    assert picker.config.guardrails.relax_blp_when_topic_filtered is False
    is_candidate, reason = picker.is_candidate(_make_blp_page(), include_categories=["Living people"])
    assert is_candidate is False
    assert reason == "BLP article"


def test_is_candidate_blp_default_flag_rejects_without_include_filter(picker):
    """AC5.3: flag False (default) + BLP article -> rejected, identical to current
    behavior, with no include filter active."""
    assert picker.config.guardrails.relax_blp_when_topic_filtered is False
    is_candidate, reason = picker.is_candidate(_make_blp_page(), include_categories=[])
    assert is_candidate is False
    assert reason == "BLP article"


def _make_candidate_page(name, revision="1", categories=None):
    """Categories are supplied via the batch path (page._info), matching the
    real generator=categorymembers&prop=categories response (#18) — page.categories()
    is stubbed to a Mock() so any accidental call is easy to notice/assert against."""
    cats = categories if categories is not None else []
    page = Mock()
    page.name = name
    page.redirect = False
    page.namespace = 0
    page.protection = {}
    page.revision = revision
    page.text = Mock(return_value=f"{name} is notable for something.{{{{Citation needed}}}}")
    page._info = {"categories": [{"ns": 14, "title": f"Category:{c}"} for c in cats]}
    page.categories = Mock()
    return page


def _make_page(name, *, cats, text="A fresh and notable claim about the subject.{{Citation needed}}", ns=0, redirect=False, protection=None, batch=True, revision="1"):
    """Build a page double for either category-resolution route (#18): the
    batch path (page._info carries categories, page.categories() must not be
    called) or the fallback path (page._info is unusable, page.categories()
    is stubbed as get_categories() expects — objects with a .name attribute)."""
    page = Mock()
    page.name = name
    page.redirect = redirect
    page.namespace = ns
    page.protection = protection or {}
    page.revision = revision
    page.text = Mock(return_value=text)
    if batch:
        page._info = {"categories": [{"ns": 14, "title": f"Category:{c}"} for c in cats]}
        page.categories = Mock()  # must NOT be called on the batch path
    else:
        page._info = {}  # force fallback
        page.categories = Mock(return_value=[_mock_category(f"Category:{c}") for c in cats])
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


def test_fetch_candidates_batch_query_requests_categories(mock_site, restore_config):
    """fetch_candidates piggybacks prop=categories&cllimit=max onto the batch
    generator=categorymembers query so category membership arrives with the
    initial batch (issue #18) instead of a later per-page request."""
    picker = ArticlePicker(site=mock_site)
    cat_page = Mock()
    cat_page.args = {}
    cat_page.__iter__ = Mock(return_value=iter([]))
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": cat_page}

    list(picker.fetch_candidates(limit=3))

    assert cat_page.args["prop"] == "info|imageinfo|categories"
    assert cat_page.args["cllimit"] == "max"


def test_fetch_candidates_batch_query_args_coexist_with_start_prefix(mock_site, restore_config):
    """The prop/cllimit batch args and gcmstartsortkeyprefix must coexist —
    the categories piggyback is independent of whether a start prefix is
    configured."""
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
    assert cat_page.args["prop"] == "info|imageinfo|categories"
    assert cat_page.args["cllimit"] == "max"


def test_fetch_candidates_batch_query_no_args_attr_is_safe(mock_site):
    """A cat_page without a mutable .args (a bare list, as used by other
    fetch_candidates tests) must not raise when the categories piggyback runs."""
    picker = ArticlePicker(site=mock_site)
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": []}

    result = list(picker.fetch_candidates(limit=3))

    assert result == []


def test_batch_categories_reads_info_and_strips_prefix(picker):
    """The batch-read path strips 'Category:' the same way get_categories() does,
    so both paths produce content-identical category names (AC3.2/AC4.2)."""
    page = Mock()
    page._info = {"categories": [{"ns": 14, "title": "Category:History"}, {"ns": 14, "title": "Category:Physics"}]}

    assert picker._batch_categories(page) == ["History", "Physics"]


def test_batch_categories_empty_list_is_complete_not_fallback(picker):
    """A page genuinely in zero categories is complete data, not missing data —
    must return [], not None (which would trigger a needless fallback)."""
    page = Mock()
    page._info = {"categories": []}

    assert picker._batch_categories(page) == []


def test_batch_categories_missing_key_returns_none(picker):
    """No 'categories' key in _info (batch prop=categories data absent) signals
    fallback to per-page get_categories() (AC4.1)."""
    page = Mock()
    page._info = {}

    assert picker._batch_categories(page) is None


def test_batch_categories_non_dict_info_returns_none(picker):
    """A bare Mock() page has a truthy auto-attribute _info that is itself a
    Mock, not a dict — must be treated as unusable and fall back rather than
    raising when iterated (AC4.1)."""
    page = Mock()

    assert picker._batch_categories(page) is None


def test_batch_categories_clcontinue_returns_none(picker):
    """A clcontinue marker on _info means the category list was truncated by
    the API — must fall back rather than filter on a partial list (AC4.1)."""
    page = Mock()
    page._info = {"categories": [{"title": "Category:X"}], "clcontinue": "some-continue-token"}

    assert picker._batch_categories(page) is None


def test_batch_categories_malformed_entry_returns_none(picker):
    """An entry missing 'title' means the categories list is malformed — must
    fall back rather than return a partial list that could silently flip a
    filter decision (AC4.2)."""
    page = Mock()
    page._info = {"categories": [{"ns": 14}]}

    assert picker._batch_categories(page) is None


def test_offtopic_candidate_rejected_without_text_fetch(picker):
    """AC2.1: an off-topic candidate (batch categories miss the active include
    filter) is rejected before page.text() is ever called — the whole point of
    running the topic filter first (#18)."""
    page = _make_page("Off Topic", cats=["Sports"])

    ok, reason = picker.is_candidate(page, include_categories=["History"])

    assert ok is False
    assert reason == "not in included categories"
    page.text.assert_not_called()


def test_ontopic_candidate_proceeds_to_text_fetch(picker):
    """AC2.2: an on-topic candidate (batch categories match the include filter)
    still reaches page.text() as before; the no-filter case also proceeds."""
    matching_page = _make_page("On Topic", cats=["History"])
    ok, _ = picker.is_candidate(matching_page, include_categories=["History"])
    assert ok is True
    matching_page.text.assert_called_once()

    no_filter_page = _make_page("No Filter", cats=["Anything"])
    ok, _ = picker.is_candidate(no_filter_page, include_categories=[])
    assert ok is True
    no_filter_page.text.assert_called_once()


def test_ontopic_accept_never_calls_page_categories(picker):
    """AC3.1: an accepted candidate reuses the batch-provided categories and
    never triggers the separate page.categories() round trip."""
    page = _make_page("Accepted Article", cats=["History"])

    ok, _ = picker.is_candidate(page)

    assert ok is True
    page.categories.assert_not_called()


def test_batch_and_fallback_categories_are_content_identical(picker):
    """AC3.2: the batch-read path and the per-page fallback path produce
    content-identical category names (prefix-stripped, order-independent) for
    the same underlying categories."""
    batch_page = _make_page("Batch", cats=["History", "Physics"], batch=True)
    fallback_page = _make_page("Fallback", cats=["History", "Physics"], batch=False)

    assert set(picker._batch_categories(batch_page)) == set(picker.get_categories(fallback_page))


def test_fallback_used_when_info_missing_categories(picker):
    """AC4.1: _info missing the 'categories' key falls back to get_categories(),
    calling page.categories() exactly once, and still reaches the same decision
    a complete batch list would have produced."""
    page = _make_page("Fallback Missing", cats=["History"], batch=False)

    ok, _ = picker.is_candidate(page, include_categories=["History"])

    assert ok is True
    page.categories.assert_called_once()


def test_fallback_used_on_clcontinue_truncation(picker):
    """AC4.1: a clcontinue marker on _info signals truncated batch data — must
    fall back to get_categories() rather than filter on a partial list."""
    page = _make_page("Truncated", cats=["History"], batch=True)
    page._info["clcontinue"] = "some-continue-token"
    page.categories = Mock(return_value=[_mock_category("Category:History")])

    ok, _ = picker.is_candidate(page, include_categories=["History"])

    assert ok is True
    page.categories.assert_called_once()


def test_fallback_result_matches_full_list_decision(picker):
    """AC4.2: the fallback never silently flips a decision vs. the full-list
    result — a page whose full category set passes the include filter still
    passes via the fallback, and one that should be rejected still is."""
    passing_page = _make_page("Passes", cats=["History"], batch=False)
    ok, _ = picker.is_candidate(passing_page, include_categories=["History"])
    assert ok is True

    rejecting_page = _make_page("Rejected", cats=["Sports"], batch=False)
    ok, _ = picker.is_candidate(rejecting_page, include_categories=["History"])
    assert ok is False


def test_no_topic_filter_output_and_requests_unchanged(mock_site):
    """AC5.1: with no include/exclude configured, output and request pattern
    are unchanged from before #18 — page.text() is still called (needed for
    citation-needed), page.categories() is not (the batch data supplies
    categories), and the yielded candidate matches expectations."""
    picker = ArticlePicker(site=mock_site)
    page = _make_page("Plain Article", cats=["Anything"])
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": [page]}

    candidates = list(picker.fetch_candidates(limit=5))

    assert [c.title for c in candidates] == ["Plain Article"]
    assert candidates[0].categories == ["Anything"]
    page.text.assert_called_once()
    page.categories.assert_not_called()


def test_evaluate_candidate_distrusts_batch_categories_when_flagged(picker):
    """trust_batch_categories=False forces the per-page get_categories()
    fallback even when page._info has a well-formed, seemingly-complete
    categories list — this is how fetch_candidates protects against a
    silently truncated batch (see the truncated-batch test below)."""
    page = _make_page("Flagged", cats=["Sports"], batch=True)
    page.categories = Mock(return_value=[_mock_category("Category:History")])

    ok, _, _, categories = picker._evaluate_candidate(page, ["History"], [], trust_batch_categories=False)

    assert ok is True
    assert categories == ["History"]
    page.categories.assert_called_once()


def test_fetch_candidates_truncated_batch_forces_fallback(mock_site):
    """Realistic truncated-batch shape, confirmed against the live API: a
    500-page generator batch commonly exceeds cllimit=max well before every
    page's categories are listed (verified: ~20-30/500 pages get any
    categories data per round for a busy category), so MediaWiki returns a
    TOP-LEVEL clcontinue that mwclient echoes back into cat_page.args — never
    into any individual page's _info. A page caught by that cutoff has a
    well-formed but silently PARTIAL categories list with no per-page marker
    of its own. Once cat_page.args shows clcontinue, fetch_candidates must
    distrust every page's batch categories for the rest of this fetch and use
    the per-page fallback, or an off-topic-looking (but actually on-topic)
    page could be wrongly rejected on incomplete data."""
    picker = ArticlePicker(site=mock_site)
    cat_page = Mock()
    cat_page.args = {"clcontinue": "12345|SomeCategory"}  # truncation already flagged

    # _info looks complete (a well-formed list) but is missing "History" —
    # only the per-page fallback's full list has it.
    page = _make_page("Boundary Page", cats=["Sports"], batch=True)
    page.categories = Mock(return_value=[_mock_category("Category:Sports"), _mock_category("Category:History")])
    cat_page.__iter__ = Mock(return_value=iter([page]))
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": cat_page}

    titles = [c.title for c in picker.fetch_candidates(limit=5, include_categories=["History"])]

    # Trusting the truncated batch list (["Sports"]) would wrongly reject this
    # page; the fallback's full list (["Sports", "History"]) correctly accepts it.
    assert "Boundary Page" in titles
    page.categories.assert_called_once()


def test_fetch_candidates_dedupes_pages_reyielded_across_continuation_rounds(mock_site):
    """mwclient re-yields the same page across clcontinue continuation
    rounds when a batch's categories sub-query is truncated (live-confirmed:
    a repeated request with the same clcontinue token returns the identical
    ~500-page batch again, not new pages) — an in-memory title dedup guards
    against double-processing and duplicate candidates within one fetch."""
    picker = ArticlePicker(site=mock_site)
    page = _make_page("Repeated Page", cats=["History"])
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": [page, page]}

    candidates = list(picker.fetch_candidates(limit=5))

    assert [c.title for c in candidates] == ["Repeated Page"]
    page.text.assert_called_once()


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
    news_page = _make_candidate_page("News Article", categories=["news-ish"])
    journal_page = _make_candidate_page("Journal Article", categories=["journal-ish"])
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": [journal_page, news_page]}

    titles = [c.title for c in picker.fetch_candidates(limit=1)]
    assert titles == ["News Article"]
    news_page.categories.assert_not_called()
    journal_page.categories.assert_not_called()


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
    journal_page = _make_candidate_page("Journal Article", categories=["journal-ish"])
    news_page = _make_candidate_page("News Article", categories=["news-ish"])
    mock_site.pages = {"Category:All_articles_with_unsourced_statements": [journal_page, news_page]}

    titles = [c.title for c in picker.fetch_candidates(limit=2)]
    assert titles == ["Journal Article", "News Article"]
    journal_page.categories.assert_not_called()
    news_page.categories.assert_not_called()


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


class _FakeMember:
    """Stand-in for an mwclient member Page: only `.name` is used by crawl_subcategories."""

    def __init__(self, name: str):
        self.name = name


class _FakeCategoryPage:
    """Stand-in for an mwclient Category: `.members(namespace=14)` returns subcategories."""

    def __init__(self, children: list[str] | Exception):
        self._children = children

    def members(self, namespace=None, **kwargs):
        if isinstance(self._children, Exception):
            raise self._children
        return [_FakeMember(f"Category:{child}") for child in self._children]


def _make_crawl_site(tree: dict[str, list[str] | Exception]):
    """Build a fake mwclient Site whose `.pages["Category:X"]` looks up `tree[X]`."""
    site = Mock()
    site.pages = {f"Category:{name}": _FakeCategoryPage(children) for name, children in tree.items()}
    return site


def test_crawl_subcategories_shallow_tree():
    """AC1.1: a shallow tree returns all reachable subcategory names plus the root."""
    tree = {
        "Root": ["Child A", "Child B"],
        "Child A": ["Grandchild"],
        "Child B": [],
        "Grandchild": [],
    }
    site = _make_crawl_site(tree)

    result = crawl_subcategories(site, "Category:Root")

    assert result == sorted(["Root", "Child A", "Child B", "Grandchild"])


def test_crawl_subcategories_degrades_on_branch_failure(caplog):
    """AC1.2: a branch whose `.members()` raises is logged and skipped, but the
    crawl still returns the other branches instead of aborting."""
    tree = {
        "Root": ["Good Branch", "Bad Branch"],
        "Good Branch": ["Leaf"],
        "Bad Branch": RuntimeError("API error"),
        "Leaf": [],
    }
    site = _make_crawl_site(tree)

    with caplog.at_level(logging.WARNING):
        result = crawl_subcategories(site, "Root")

    assert result == sorted(["Root", "Good Branch", "Bad Branch", "Leaf"])
    assert any("Bad Branch" in record.message for record in caplog.records)


def test_crawl_subcategories_handles_cycles_and_diamonds():
    """AC1.3: a category reachable via two parents (diamond) or pointing back at an
    ancestor (cycle) terminates and yields each name exactly once."""
    tree = {
        "Root": ["Branch A", "Branch B"],
        "Branch A": ["Shared", "Root"],  # cycle back to Root
        "Branch B": ["Shared"],  # diamond: Shared reachable via A and B
        "Shared": ["Root"],  # cycle back to Root again
    }
    site = _make_crawl_site(tree)

    result = crawl_subcategories(site, "Root")

    assert result == sorted(["Root", "Branch A", "Branch B", "Shared"])
    assert len(result) == len(set(result))


def test_crawl_subcategories_respects_max_depth():
    """max_depth caps the BFS: nodes at the cap are included but not expanded."""
    tree = {
        "Root": ["Child"],
        "Child": ["Grandchild"],
        "Grandchild": [],
    }
    site = _make_crawl_site(tree)

    result = crawl_subcategories(site, "Root", max_depth=1)

    assert result == sorted(["Root", "Child"])


def test_crawl_subcategories_strips_category_prefix():
    """Returned names never carry the `Category:` prefix, root included, regardless
    of whether the caller passed the prefix in."""
    tree = {"Root": ["Child"], "Child": []}
    site = _make_crawl_site(tree)

    result = crawl_subcategories(site, "Category:Root")

    assert result == ["Child", "Root"]
    assert all(not name.startswith("Category:") for name in result)
