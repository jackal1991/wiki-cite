# Phase 4: Runtime loader — read the static expansion file, no live crawling

**Goal:** `fetch_candidates()` expands a configured include/exclude category to its
discovered subcategory set by reading the static JSON file when present, falling back to
direct-match-only when absent — with no live Wikipedia subcategory call in the request path.
**AC Coverage:** 10-subcategory-aware-topic-filter.AC4 (AC4.1, AC4.2)

---

## Context

Current `fetch_candidates` (in `wiki_cite/article_picker.py`) passes the caller's
`include_categories`/`exclude_categories` overrides (possibly `None`) straight through to
`_evaluate_candidate`, which resolves `None` against config and calls the unchanged
`category_filter()`.

`category_filter()` must NOT change (AC4.1). The expansion happens by widening the include
(and exclude) *name lists* before filtering — the filter still does its plain
set-intersection.

Phase 3 added `expansion_file_path(root)` and the file format to `category_discovery.py`.
This phase adds a pure loader and calls it once per fetch (not per page).

Import direction: `article_picker.py` imports the loader from `category_discovery.py`.
`category_discovery.py` must not import `article_picker.py` (verified no cycle: crawl lives in
`article_picker`, and the CLI — not `category_discovery` — imports it).

## Implementation

### `load_expansion` loader (add to `wiki_cite/category_discovery.py`)

**Files:**
- Modify: `wiki_cite/category_discovery.py`

```python
def load_expansion(name: str) -> list[str] | None:
    """Return the discovered category-name list for a root ``name`` if an expansion file
    exists (data/category_expansions/<slug>.json), else None. Pure read; no network.
    Malformed/unreadable file -> log a warning and return None (fall back to direct match)."""
```
- `path = expansion_file_path(name)`; if not `path.exists()`, return `None`.
- Read+`json.load`; return the `"categories"` list. On any `OSError`/`json.JSONDecodeError`,
  `logger.warning("Ignoring unreadable expansion file %s: %s", path, e)` and return `None`.

### `_expand_categories` + `fetch_candidates` wiring (in `wiki_cite/article_picker.py`)

**Files:**
- Modify: `wiki_cite/article_picker.py`

- Add import near the top: `from wiki_cite.category_discovery import load_expansion`.
- Add a small static/instance helper:
  ```python
  @staticmethod
  def _expand_categories(names: list[str]) -> list[str]:
      """For each configured category name, if a discovery file exists for it, replace it
      with that file's discovered set (root + accepted subcats); otherwise keep the name
      as-is (AC4.2 fallback). Returns a deduplicated, order-stable list."""
  ```
  For each `name` in `names`: `expanded = load_expansion(name)`; extend the result with
  `expanded` if not `None`, else append `name`. De-duplicate while preserving order.
- In `fetch_candidates`, resolve the effective lists ONCE, before the page loop, and expand
  them, then pass the explicit expanded lists into `_evaluate_candidate` for every page:
  ```python
  include = include_categories if include_categories is not None else self.config.article_selection.include_categories
  exclude = exclude_categories if exclude_categories is not None else self.config.article_selection.exclude_categories
  include = self._expand_categories(include)
  exclude = self._expand_categories(exclude)
  ...
  is_ok, _, page_text, categories = self._evaluate_candidate(page, include, exclude)
  ```
  Because `include`/`exclude` are now explicit non-`None` lists, `_evaluate_candidate`'s
  existing `x if x is not None else config` logic uses them directly. `category_filter()` is
  untouched. Expansion cost is paid once per fetch, not once per page.

**Notes:**
- No live Wikipedia subcategory walk anywhere in `fetch_candidates` — the only new work is a
  local file read (AC4 headline requirement).
- AC4.2: a configured include category with no discovery file simply stays a single-name
  direct-match entry — no error, today's behavior preserved.
- Empty include/exclude lists expand to empty lists (no-op), so behavior with no topic filter
  is unchanged.

**Tests:** (Phase 6 owns AC mapping)
- AC4.1: with an expansion file present for the configured include category, an article whose
  category is a *discovered subcategory* (not the root) passes the filter.
- AC4.2: with no file for the configured include category, filtering is direct-match-only
  (an article in the root category passes; one in an undiscovered subcategory does not) and
  nothing raises.

---

## Verification

Run: `uv run pytest tests/test_article_picker.py -q`
Also: `uv run ruff check wiki_cite/article_picker.py`
Expected: existing `fetch_candidates`/`category_filter` tests still pass; expansion applied
once per fetch.

## Commit

`feat: expand topic filter from static discovery file at fetch time`
