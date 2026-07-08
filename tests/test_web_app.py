"""Tests for the Flask web application."""

from unittest.mock import Mock, patch

import pytest

from wiki_cite import web_app
from wiki_cite.config import Config


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
