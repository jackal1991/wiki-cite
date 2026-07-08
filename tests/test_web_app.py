"""Tests for the Flask web application."""

from unittest.mock import Mock, patch

import pytest

from wiki_cite import web_app
from wiki_cite.config import Config, set_config
from wiki_cite.models import Article, EditProposal, EditType, ProposedEdit
from wiki_cite.seen_store import SeenStore
from wiki_cite.web_app import create_app


@pytest.fixture
def client_and_site():
    """Flask test client with all network-touching services stubbed out."""
    fake_site = Mock()
    with (
        patch.object(web_app, "ArticlePicker") as picker_cls,
        patch.object(web_app, "WikipediaPushService"),
        patch.object(web_app, "ClaudeAgent"),
        patch.object(web_app, "SourceFinder"),
        patch.object(web_app, "SeenStore"),
    ):
        picker_cls.return_value.site = fake_site
        app = web_app.create_app()
        app.config["TESTING"] = True
        yield app.test_client(), fake_site, picker_cls.return_value


@pytest.fixture
def client_with_seeded_categories():
    """Flask test client whose config seeds known include/exclude category lists."""
    fake_site = Mock()
    config = Config.load("nonexistent.yaml")
    config.article_selection.include_categories = ["History"]
    config.article_selection.exclude_categories = ["Sports"]
    with (
        patch.object(web_app, "get_config", return_value=config),
        patch.object(web_app, "ArticlePicker") as picker_cls,
        patch.object(web_app, "WikipediaPushService"),
        patch.object(web_app, "ClaudeAgent"),
        patch.object(web_app, "SourceFinder"),
        patch.object(web_app, "SeenStore"),
    ):
        picker_cls.return_value.site = fake_site
        app = web_app.create_app()
        app.config["TESTING"] = True
        yield app.test_client(), fake_site, picker_cls.return_value


@pytest.fixture
def app(tmp_path, monkeypatch):
    # ArticlePicker/WikipediaPushService open a real mwclient.Site connection
    # when constructed without one; stub it out so app creation stays offline.
    monkeypatch.setattr("mwclient.Site", Mock())
    set_config(Config(SEEN_DB_PATH=str(tmp_path / "seen.db")))
    return create_app()


def make_proposal() -> EditProposal:
    article = Article(title="Groveland Four", url="https://en.wikipedia.org/wiki/Groveland_Four", wikitext="...", revision_id="123")
    edits = [
        ProposedEdit(edit_type=EditType.CITATION_ADDED, original_text="a", proposed_text="a[1]", rationale="sourced", confidence="high"),
        ProposedEdit(edit_type=EditType.GRAMMAR_FIX, original_text="b", proposed_text="b.", rationale="grammar", confidence="medium"),
    ]
    return EditProposal(id="p1", article=article, edits=edits)


def test_search_categories_success(client_and_site):
    """A non-empty query returns categories with the Category: prefix stripped."""
    client, fake_site, _ = client_and_site
    page = Mock()
    page.name = "Category:History of France"
    fake_site.allpages = Mock(return_value=[page])

    response = client.get("/api/categories/search?q=Hist")

    assert response.status_code == 200
    assert response.get_json() == {"categories": ["History of France"]}
    fake_site.allpages.assert_called_once_with(prefix="Hist", namespace=14, limit=20)


def test_search_categories_missing_query(client_and_site):
    """A missing 'q' parameter is rejected before any Wikipedia call."""
    client, fake_site, _ = client_and_site
    fake_site.allpages = Mock()

    response = client.get("/api/categories/search")

    assert response.status_code == 400
    assert "error" in response.get_json()
    fake_site.allpages.assert_not_called()


def test_search_categories_blank_query(client_and_site):
    """A whitespace-only 'q' parameter is rejected before any Wikipedia call."""
    client, fake_site, _ = client_and_site
    fake_site.allpages = Mock()

    response = client.get("/api/categories/search?q=%20%20")

    assert response.status_code == 400
    fake_site.allpages.assert_not_called()


def test_search_categories_upstream_failure(client_and_site):
    """An mwclient failure surfaces as a 502 with an error message, not a 500."""
    client, fake_site, _ = client_and_site
    fake_site.allpages = Mock(side_effect=RuntimeError("upstream unavailable"))

    response = client.get("/api/categories/search?q=Hist")

    assert response.status_code == 502
    assert "error" in response.get_json()


def test_get_category_settings_returns_seeded_config_defaults(client_with_seeded_categories):
    """GET returns the include/exclude lists the app was seeded with from config.yaml."""
    client, _, _ = client_with_seeded_categories

    response = client.get("/api/settings/categories")

    assert response.status_code == 200
    assert response.get_json() == {"include": ["History"], "exclude": ["Sports"]}


def test_post_category_settings_updates_and_persists_in_memory(client_and_site):
    """POST updates the override, and a following GET reflects the new lists."""
    client, _, _ = client_and_site

    post_response = client.post("/api/settings/categories", json={"include": ["History"], "exclude": ["Sports"]})
    assert post_response.status_code == 200
    assert post_response.get_json() == {"include": ["History"], "exclude": ["Sports"]}

    get_response = client.get("/api/settings/categories")
    assert get_response.get_json() == {"include": ["History"], "exclude": ["Sports"]}


def test_post_category_settings_feeds_fetch_candidates(client_and_site):
    """A POSTed override is passed through to fetch_candidates on the next fetch."""
    client, _, picker = client_and_site
    picker.fetch_candidates = Mock(return_value=iter([]))

    client.post("/api/settings/categories", json={"include": ["History"], "exclude": ["Sports"]})
    client.get("/api/fetch-article")

    _, kwargs = picker.fetch_candidates.call_args
    assert kwargs["include_categories"] == ["History"]
    assert kwargs["exclude_categories"] == ["Sports"]


def test_post_category_settings_rejects_non_list_include(client_and_site):
    """A non-list 'include' is rejected with 400 and the override is left unchanged."""
    client, _, _ = client_and_site

    response = client.post("/api/settings/categories", json={"include": "History"})

    assert response.status_code == 400
    assert client.get("/api/settings/categories").get_json() == {"include": [], "exclude": []}


def test_post_category_settings_rejects_non_string_elements(client_and_site):
    """A list with non-string elements is rejected with 400 and left unchanged."""
    client, _, _ = client_and_site

    response = client.post("/api/settings/categories", json={"exclude": [1, 2]})

    assert response.status_code == 400
    assert client.get("/api/settings/categories").get_json() == {"include": [], "exclude": []}


def test_approve_edit_persists_outcome(app, tmp_path):
    proposal = make_proposal()
    app.proposals[proposal.id] = proposal
    client = app.test_client()

    response = client.post(f"/api/proposals/{proposal.id}/approve-edit/0")
    assert response.status_code == 200

    fresh_store = SeenStore(tmp_path / "seen.db")
    successes, total = fresh_store.dimension_rates("edit_type")["citation"]
    assert (successes, total) == (1, 1)


def test_reject_edit_persists_outcome(app, tmp_path):
    proposal = make_proposal()
    app.proposals[proposal.id] = proposal
    client = app.test_client()

    response = client.post(f"/api/proposals/{proposal.id}/reject-edit/1")
    assert response.status_code == 200

    fresh_store = SeenStore(tmp_path / "seen.db")
    successes, total = fresh_store.dimension_rates("edit_type", success_outcomes=("rejected",))["grammar"]
    assert (successes, total) == (1, 1)


def test_approve_then_reject_two_edits_survive_restart(app, tmp_path):
    proposal = make_proposal()
    app.proposals[proposal.id] = proposal
    client = app.test_client()

    client.post(f"/api/proposals/{proposal.id}/approve-edit/0")
    client.post(f"/api/proposals/{proposal.id}/reject-edit/1")

    # Simulate a restart: discard the app/store, re-open the same DB file.
    del app
    fresh_store = SeenStore(tmp_path / "seen.db")
    approved, total_citation = fresh_store.dimension_rates("edit_type")["citation"]
    assert (approved, total_citation) == (1, 1)

    rejected, total_grammar = fresh_store.dimension_rates("edit_type", success_outcomes=("rejected",))["grammar"]
    assert (rejected, total_grammar) == (1, 1)
