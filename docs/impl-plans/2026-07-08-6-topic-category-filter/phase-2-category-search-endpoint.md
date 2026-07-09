# Phase 2: Category search endpoint

**Goal:** Expose a read-only Wikipedia category-name search so the dashboard (Phase 4)
can offer search-and-select instead of unvalidated free-text category entry.

**Satisfies:** AC3 (`GET /api/categories/search`).

## Context (verified)
- Routes are flat `@app.route(...)` closures inside `create_app()` in
  `wiki_cite/web_app.py` (see the existing `/api/...` routes, e.g.
  `wiki_cite/web_app.py:161-177`), returning JSON via `jsonify` and reading input via
  `request` (already imported, `wiki_cite/web_app.py:10`).
- `create_app()` already builds `article_picker = ArticlePicker(seen_store=seen_store)`
  at `wiki_cite/web_app.py:35`. The picker holds a live `mwclient.Site` at
  `self.site` (`wiki_cite/article_picker.py:87`). Reuse `article_picker.site` for the
  search — no new connection, no local index.
- MediaWiki Category namespace is `14`. `mwclient`'s `site.allpages(prefix=..., namespace=14)`
  yields `Page` objects whose `.name` includes the `Category:` prefix; strip it for the
  response.
- **There is no `tests/test_web_app.py` yet** — this phase creates it. Constructing
  `create_app()` currently instantiates `ArticlePicker` (`web_app.py:35`) and
  `WikipediaPushService` (`web_app.py:37`), each of which opens a **real**
  `mwclient.Site("en.wikipedia.org")` (`article_picker.py:87`, `wikipedia_push.py:57`)
  — a network call at import time. Tests MUST patch these before calling `create_app()`.

## Changes

### `wiki_cite/web_app.py`
Add inside `create_app()` (near the other `/api/...` routes):

```python
@app.route("/api/categories/search")
def search_categories():
    """Search Wikipedia Category-namespace page names by prefix, for the
    dashboard's search-and-select. Read-only; no local index."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "query parameter 'q' is required"}), 400
    try:
        pages = article_picker.site.allpages(prefix=q, namespace=14, limit=20)
        names = [p.name.split(":", 1)[-1] for p in pages]
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"categories": names})
```

Notes:
- Empty/missing `q` → `400` before any Wikipedia call (AC3.2 — no full-namespace dump).
- `limit=20` caps the result set (AC3.1 "~20").
- `namespace=14` = Category namespace.
- The `mwclient` failure path returns `502` (upstream error) rather than a 500 stack
  trace, matching the defensive style already in the picker/source layers.

## Tests

### `tests/test_web_app.py` (new)
Add a shared fixture that stubs out the network-touching services so `create_app()` is
safe, and returns a Flask test client:

```python
from unittest.mock import Mock, patch
import pytest
from wiki_cite import web_app

@pytest.fixture
def client_and_site():
    fake_site = Mock()
    with patch.object(web_app, "ArticlePicker") as picker_cls, \
         patch.object(web_app, "WikipediaPushService"), \
         patch.object(web_app, "ClaudeAgent"), \
         patch.object(web_app, "SourceFinder"), \
         patch.object(web_app, "SeenStore"):
        picker_cls.return_value.site = fake_site
        app = web_app.create_app()
        app.config["TESTING"] = True
        yield app.test_client(), fake_site
```

Cases:
- **AC3.1 success**: `fake_site.allpages` returns objects with `.name` like
  `"Category:History of France"`; `GET /api/categories/search?q=Hist` → `200`, JSON
  `categories` list with the `Category:` prefix stripped; assert `allpages` was called
  with `prefix="Hist"`, `namespace=14`, `limit=20`.
- **AC3.2 missing `q`**: `GET /api/categories/search` → `400`, and assert
  `fake_site.allpages` was **not** called (no full-namespace dump).
- **AC3.2 blank `q`**: `GET /api/categories/search?q=%20%20` (whitespace) → `400`,
  `allpages` not called.
- Upstream failure: `fake_site.allpages` raises → `502` with an `error` key.

## Done when
- `uv run pytest tests/test_web_app.py` passes.
- `uv run ruff check .` clean.
- AC3.1, AC3.2 demonstrated. The test fixture patches out `mwclient.Site` construction
  so the suite makes no network calls.
