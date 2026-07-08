"""Tests for the Flask web application."""

from unittest.mock import Mock, patch

import pytest

from wiki_cite import web_app


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
        yield app.test_client(), fake_site


def test_search_categories_success(client_and_site):
    """A non-empty query returns categories with the Category: prefix stripped."""
    client, fake_site = client_and_site
    page = Mock()
    page.name = "Category:History of France"
    fake_site.allpages = Mock(return_value=[page])

    response = client.get("/api/categories/search?q=Hist")

    assert response.status_code == 200
    assert response.get_json() == {"categories": ["History of France"]}
    fake_site.allpages.assert_called_once_with(prefix="Hist", namespace=14, limit=20)


def test_search_categories_missing_query(client_and_site):
    """A missing 'q' parameter is rejected before any Wikipedia call."""
    client, fake_site = client_and_site
    fake_site.allpages = Mock()

    response = client.get("/api/categories/search")

    assert response.status_code == 400
    assert "error" in response.get_json()
    fake_site.allpages.assert_not_called()


def test_search_categories_blank_query(client_and_site):
    """A whitespace-only 'q' parameter is rejected before any Wikipedia call."""
    client, fake_site = client_and_site
    fake_site.allpages = Mock()

    response = client.get("/api/categories/search?q=%20%20")

    assert response.status_code == 400
    fake_site.allpages.assert_not_called()


def test_search_categories_upstream_failure(client_and_site):
    """An mwclient failure surfaces as a 502 with an error message, not a 500."""
    client, fake_site = client_and_site
    fake_site.allpages = Mock(side_effect=RuntimeError("upstream unavailable"))

    response = client.get("/api/categories/search?q=Hist")

    assert response.status_code == 502
    assert "error" in response.get_json()
