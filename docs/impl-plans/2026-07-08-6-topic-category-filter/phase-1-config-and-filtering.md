# Phase 1: Config schema + include/exclude filtering

**Goal:** Add `include_categories` / `exclude_categories` to article-selection config
and gate `ArticlePicker.is_candidate()` on them, using each article's already-fetched
categories. Pure logic only — no new network calls, no model changes.

**Satisfies:** AC1 (config schema), AC2 (selection filtering logic).

## Context (verified)
- `ArticleSelectionConfig` lives at `wiki_cite/config.py:53-62`. It already uses the
  boolean-flag idiom (`exclude_blp`, `exclude_protected`) the design points to.
- `Config.load` (`wiki_cite/config.py:108-109`) constructs
  `ArticleSelectionConfig(**yaml_config["article_selection"])`, so a bad YAML value
  surfaces as a Pydantic `ValidationError` at load time. Pydantic v2 does **not**
  coerce a bare string / int into `list[str]`, so AC1.2 comes for free once the fields
  are typed `list[str]`.
- `ArticlePicker.is_candidate(page)` at `wiki_cite/article_picker.py:223-268` already
  computes `categories = self.get_categories(page)` at line 258 (names have the
  `Category:` prefix stripped, see `get_categories` at line 209-221). The new filter
  slots in right after that line, before/independent of the BLP and citation-needed
  checks.
- Test idioms: `tests/test_config.py` uses tempfile YAML fixtures;
  `tests/test_article_picker.py` builds `Mock()` pages with `.categories`,
  `.text`, `.protection`, `.namespace`, `.redirect`.

## Changes

### `wiki_cite/config.py`
Add two list fields to `ArticleSelectionConfig` (after `max_wikitext_chars`,
`wiki_cite/config.py:62`):

```python
include_categories: list[str] = Field(default_factory=list)
exclude_categories: list[str] = Field(default_factory=list)
```

Default `[]` preserves today's behavior. `Field` is already imported.

### `wiki_cite/article_picker.py`
1. Add a **pure** static helper (no I/O) that decides on membership:

```python
@staticmethod
def _normalize_category(name: str) -> str:
    """Normalize a category name for comparison: drop the ``Category:`` prefix,
    convert underscores to spaces, and casefold."""
    return name.split(":", 1)[-1].replace("_", " ").strip().casefold()

@staticmethod
def category_filter(
    categories: list[str],
    include: list[str],
    exclude: list[str],
) -> tuple[bool, str]:
    """Decide whether an article's categories pass the include/exclude filter.

    Exclude takes precedence: any overlap with ``exclude`` rejects regardless of
    ``include``. A non-empty ``include`` then requires overlap to pass. Empty
    lists are no-ops (matches today's behavior).
    """
    article = {ArticlePicker._normalize_category(c) for c in categories}
    excluded = {ArticlePicker._normalize_category(c) for c in exclude}
    hit = article & excluded
    if hit:
        return False, f"excluded category: {sorted(hit)[0]}"
    if include:
        included = {ArticlePicker._normalize_category(c) for c in include}
        if not (article & included):
            return False, "not in included categories"
    return True, ""
```

Normalization mirrors the case-insensitive category handling already used by
`is_blp` (`wiki_cite/article_picker.py:107-110`) and tolerates operators writing
either `"Foo"` or `"Category:Foo"` in `config.yaml`. Matching is exact on the
normalized name (direct membership only — subcategory traversal is out of scope
per the design's Definition of Done).

2. Wire it into `is_candidate` with optional per-call overrides so Phase 3 can feed
   in the runtime lists without the picker re-reading `get_config()`:

```python
def is_candidate(
    self,
    page,
    include_categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
) -> tuple[bool, str]:
    ...
    categories = self.get_categories(page)  # existing line 258

    include = include_categories if include_categories is not None else self.config.article_selection.include_categories
    exclude = exclude_categories if exclude_categories is not None else self.config.article_selection.exclude_categories
    ok, reason = self.category_filter(categories, include, exclude)
    if not ok:
        return False, reason

    # ... existing BLP / citation-needed checks unchanged ...
```

Place the filter immediately after `categories = self.get_categories(page)` (line 258)
and before the BLP check at line 261. `None` (not empty list) means "no override —
use config", so an operator clearing the list to `[]` at runtime is still honored in
Phase 3.

> Note: `fetch_candidates` threading of the override args is done in **Phase 3** (it
> currently calls `is_candidate(page)` with no args at `wiki_cite/article_picker.py:300`).
> Leaving it untouched here keeps Phase 1 to config + pure filter; the default-arg
> signature is backward-compatible.

## Tests

### `tests/test_config.py`
- `test_article_selection_category_lists_default_empty`: `ArticleSelectionConfig()`
  has `include_categories == []` and `exclude_categories == []` (AC1.1).
- Extend YAML-load coverage: a tempfile with
  `article_selection: {include_categories: [History], exclude_categories: [Sports]}`
  loads into the right lists (AC1.1).
- `test_article_selection_non_list_categories_rejected`: loading YAML where
  `include_categories` is a scalar (e.g. `"History"`) raises `pydantic.ValidationError`
  (AC1.2). Use `pytest.raises(ValidationError)` around `Config.load(path)`.

### `tests/test_article_picker.py`
Drive `ArticlePicker.category_filter` directly (pure, no mock page needed) for the
matrix, plus one `is_candidate` integration check:
- include-only, overlap → `(True, "")` (AC2.2 success).
- include-only, no overlap → `(False, "not in included categories")` (AC2.2 reject).
- exclude-only, overlap → `(False, "excluded category: ...")` (AC2.1).
- both set, article hits exclude **and** include → rejected as excluded (AC2.1
  precedence).
- both empty → `(True, "")` (AC2.3 no-op).
- normalization: `category_filter(["Living people"], ["living_people"], [])` passes
  (case/underscore/`Category:` prefix insensitivity).
- `is_candidate` integration: a mock page in an excluded category is rejected with the
  excluded reason even though it has a `{{Citation needed}}` tag (reuses the mock-page
  idiom from `test_is_candidate_accepts_article_with_citation_needed`,
  `tests/test_article_picker.py:127-138`).

## Done when
- `uv run pytest tests/test_config.py tests/test_article_picker.py` passes.
- `uv run ruff check .` clean.
- AC1.1, AC1.2, AC2.1, AC2.2, AC2.3 demonstrated by the tests above.
