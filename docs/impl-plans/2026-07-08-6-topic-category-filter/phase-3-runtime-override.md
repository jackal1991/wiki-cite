# Phase 3: Runtime override (read/write) + wiring into fetch

**Goal:** Hold an in-memory include/exclude override in the web app, seeded from
`config.yaml` at startup, exposed via GET/POST, and read by the fetch path so a new
"Fetch new article" click uses it immediately — without writing back to `config.yaml`.

**Satisfies:** AC4 (runtime override).

## Context (verified)
- `create_app()` reads `config = get_config()` once at `wiki_cite/web_app.py:25`.
- The fetch path is `scan_events()` (`wiki_cite/web_app.py:45-124`), which calls
  `article_picker.fetch_candidates(limit=max_scan)` at `wiki_cite/web_app.py:61`.
  `fetch_candidates` (`wiki_cite/article_picker.py:270-333`) calls
  `is_candidate(page)` with no override args at line 300.
- Phase 1 already added the override params to `is_candidate`. This phase threads them
  through `fetch_candidates` and feeds them from the web app's in-memory override.
- In-memory mutable state is fine here (single dashboard operator assumed — see design
  Out-of-scope). The existing app already keeps mutable `proposals: dict` in a closure
  (`wiki_cite/web_app.py:31`); follow the same pattern.

## Changes

### `wiki_cite/article_picker.py`
Thread overrides through `fetch_candidates`:

```python
def fetch_candidates(
    self,
    limit: int = 100,
    include_categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
) -> Iterator[CandidateArticle]:
    ...
    is_candidate, _ = self.is_candidate(
        page,
        include_categories=include_categories,
        exclude_categories=exclude_categories,
    )  # replaces the bare is_candidate(page) at line 300
```

`None` still means "fall back to config" (from Phase 1), so existing callers and tests
are unaffected.

### `wiki_cite/web_app.py`
1. Seed an in-memory override in `create_app()` (near `proposals`, `web_app.py:31`),
   copying the config lists so later mutation never touches the config object:

```python
category_overrides = {
    "include": list(config.article_selection.include_categories),
    "exclude": list(config.article_selection.exclude_categories),
}
```

2. Feed it into the fetch. In `scan_events()`, change the call at `web_app.py:61`:

```python
for candidate in article_picker.fetch_candidates(
    limit=max_scan,
    include_categories=category_overrides["include"],
    exclude_categories=category_overrides["exclude"],
):
```

Because `category_overrides` is read at fetch time (each `scan_events()` run), a POST
between fetches takes effect on the next "Fetch new article" (AC4.2).

3. Add the two routes:

```python
def _valid_category_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)

@app.route("/api/settings/categories")
def get_category_settings():
    """Return the active include/exclude lists (override if set, else the
    config.yaml defaults it was seeded from)."""
    return jsonify({"include": category_overrides["include"], "exclude": category_overrides["exclude"]})

@app.route("/api/settings/categories", methods=["POST"])
def set_category_settings():
    """Update the in-memory override. Rejects malformed payloads without
    mutating the previous override."""
    data = request.get_json(silent=True) or {}
    include = data.get("include", category_overrides["include"])
    exclude = data.get("exclude", category_overrides["exclude"])
    if not _valid_category_list(include) or not _valid_category_list(exclude):
        return jsonify({"error": "include and exclude must be lists of strings"}), 400
    category_overrides["include"] = list(include)
    category_overrides["exclude"] = list(exclude)
    return jsonify({"include": category_overrides["include"], "exclude": category_overrides["exclude"]})
```

Validation guarantees AC4.3: a non-list `include`/`exclude` returns 400 and the stored
override is untouched (nothing is assigned before the guard). Overrides are never
written back to `config.yaml`, so they reset on restart (AC4.4 — documented, not a bug).

## Tests

### `tests/test_web_app.py` (extend Phase 2 file)
Reuse the `create_app()` fixture that patches out network services. To assert seeding
from config, set a config with known lists via `set_config(...)` in a fixture, or patch
`web_app.get_config` to return a config whose `article_selection.include_categories` /
`exclude_categories` are known.

- **AC4.1**: `GET /api/settings/categories` returns the seeded config defaults.
- **AC4.2**: `POST /api/settings/categories` with
  `{"include": ["History"], "exclude": ["Sports"]}` → `200` echoing the new lists; a
  following `GET` reflects them.
- **AC4.2 wiring**: after a POST, trigger a fetch (e.g. `article_picker.fetch_candidates`
  is a `Mock` on the patched picker) and assert it was called with
  `include_categories=["History"], exclude_categories=["Sports"]`. (The Phase 2 fixture
  patches `ArticlePicker`, so `article_picker.fetch_candidates` is a Mock — set its
  return to an empty iterator and hit `/api/fetch-article`.)
- **AC4.3**: `POST` with `{"include": "History"}` (string, not list) → `400`, and a
  following `GET` shows the override **unchanged**.
- **AC4.3**: `POST` with `{"exclude": [1, 2]}` (non-string elements) → `400`, unchanged.

### `tests/test_article_picker.py` (extend Phase 1 file)
- `test_fetch_candidates_passes_category_overrides`: a mock page in an excluded
  category is filtered out when `fetch_candidates(..., exclude_categories=[...])` is
  called (reuses the `mock_site.pages` idiom from
  `test_fetch_candidates_skips_seen`, `tests/test_article_picker.py:189-210`).

## Done when
- `uv run pytest tests/test_web_app.py tests/test_article_picker.py` passes.
- `uv run ruff check .` clean.
- AC4.1–AC4.4 demonstrated. No write path to `config.yaml` is introduced.
