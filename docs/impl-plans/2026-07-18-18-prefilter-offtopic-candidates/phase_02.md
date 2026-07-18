# Phase 2: `_batch_categories()` read helper with fallback signal

**Goal:** Add a helper that reads each page's category names from the batch-provided
`page._info["categories"]` when present and well-formed, and returns `None` (the fallback
signal) when the data is absent, truncated, or unusable — so the caller can fall back to a
per-page `page.categories()` call for just that page.
**AC Coverage:** 18-prefilter-offtopic-candidates.AC4 (AC4.1, AC4.2) at the helper level.
(Wiring into `_evaluate_candidate()` is Phase 3.)

---

## Context

Verified against the installed mwclient:
- The raw per-page batch response is stored as `page._info` (page.py:34). With Phase 1's
  `prop=categories`, each page's `_info` carries a `"categories"` list whose items are dicts
  shaped `{"ns": 14, "title": "Category:X"}` (live-confirmed shape in the design doc).
- `mwclient.page.Page.categories()` always builds a **fresh** `PagePropertyGenerator` query
  (page.py:385) and never inspects `page._info` — so the pre-fetched data is *only* reachable
  by reading `_info` directly. This helper is the batch-read path; `get_categories()` (which
  calls `page.categories()`) remains the per-page fallback.
- `get_categories()` (article_picker.py:334-346) strips the prefix via
  `cat.name.replace("Category:", "")` over `page.categories()` page objects. This helper must
  produce the **same** names from the raw dicts, i.e. strip `"Category:"` from each
  `item["title"]`, so downstream `category_filter()` and `CandidateArticle.categories` are
  content-identical regardless of which path produced them (AC3.2 / AC4.2).

**Critical grounding gotcha (drove the helper's guards):** a bare `unittest.mock.Mock()` page
— which most existing `fetch_candidates` tests use — has a **truthy auto-attribute** `_info`
(a child `Mock`), and `mock._info.get("categories")` returns another `Mock`, not `None`.
The design doc's literal helper (`info = getattr(page, "_info", None) or {}`) would then try to
iterate a `Mock` and raise `TypeError`. To keep the "unusable → fall back" contract robust
(and to avoid rewriting every existing bare-`Mock` test), the helper must **type-check**:
non-dict `_info` or non-list `categories` → return `None` (fall back). This is a strict superset
of the design doc's helper: it returns `None` in every case the doc's version does, plus the
malformed-`_info` case, which is exactly AC4's "decide on complete data or fall back" intent.

`load_expansion(...) → None` (`_expand_categories`) and `get_categories()`/`is_protected()`'s
degrade-to-safe-default conventions are the established "return a sentinel to signal fallback"
patterns this helper follows.

## Implementation

### `_batch_categories()` (new method on `ArticlePicker`)

**Files:**
- Modify: `wiki_cite/article_picker.py` — add a method next to `get_categories()`
  (after it, line ~346), keeping the batch-read and fallback paths visually adjacent.

**What to implement:**

```python
def _batch_categories(self, page) -> list[str] | None:
    """Category names for ``page`` taken from the batch generator response
    (``page._info['categories']``, present when the batch query included
    ``prop=categories`` — see #18), with the ``Category:`` prefix stripped to
    match ``get_categories()``.

    Returns ``None`` to signal the caller to fall back to a per-page
    ``get_categories(page)`` call, when the batch data is absent, unusable, or
    truncated:

      * no ``_info`` dict, or ``_info`` is not a real dict (e.g. a bare test
        double), or no ``categories`` key;
      * a ``clcontinue`` marker on ``_info`` — the page has more categories than
        the batch returned (only realistic for 500+ category pages given
        ``cllimit=max``), so the list is partial and must not drive a filter
        decision;
      * ``categories`` is not a list of ``{"title": ...}`` dicts.
    """
    info = getattr(page, "_info", None)
    if not isinstance(info, dict):
        return None
    if info.get("clcontinue") is not None:
        return None
    raw = info.get("categories")
    if not isinstance(raw, list):
        return None
    names: list[str] = []
    for item in raw:
        if not isinstance(item, dict) or "title" not in item:
            return None  # malformed entry: don't filter on a partial/garbled list
        names.append(item["title"].replace("Category:", ""))
    return names
```

Design decisions grounded in the ACs:
- **AC4.1** — the two "fall back" triggers named in the AC (no `"categories"` key; a
  `clcontinue` marker) both return `None`. `clcontinue` is checked on `_info` because that is
  where mwclient's `load_chunk` merges continuation keys (listing.py:110-116); with
  `cllimit=max` it essentially never appears for real articles, but the check makes the
  truncation case safe rather than assumed-impossible.
- **AC4.2** — a malformed/partial `categories` entry returns `None` (fall back) rather than a
  half-built list, so the fallback can never silently reject a page that a full list would have
  passed, nor accept one it would have rejected. The names produced on the success path use the
  identical `.replace("Category:", "")` stripping as `get_categories()`, so the two paths yield
  order-independent-equal category sets.
- Empty `categories: []` is a **valid** answer (returns `[]`, not `None`) — a page genuinely in
  zero categories is complete data, not missing data. Only absent/malformed/truncated → `None`.

Keep `get_categories()` unchanged — it stays the fallback path and any other caller's public
entry point (design "get_categories(page) stays").

## Tests

**File:** `tests/test_article_picker.py`

Unit-test the helper directly via `picker._batch_categories(page)` with hand-built page doubles
whose `_info` is a real dict. (Phase 4 owns the AC-mapping table; these may live here or move
there.)

1. `test_batch_categories_reads_info_and_strips_prefix` (AC4.2 success) — `page._info =
   {"categories": [{"ns":14,"title":"Category:History"},{"ns":14,"title":"Category:Physics"}]}`
   → returns `["History", "Physics"]`, matching what `get_categories()` produces from the same
   titles.
2. `test_batch_categories_empty_list_is_complete_not_fallback` — `_info={"categories": []}` →
   returns `[]` (not `None`).
3. `test_batch_categories_missing_key_returns_none` (AC4.1) — `_info={}` (no `categories`) →
   `None`.
4. `test_batch_categories_non_dict_info_returns_none` (AC4.1 / bare-Mock safety) — a bare
   `Mock()` page (truthy Mock `_info`) → `None`, proving fallback fires instead of raising.
5. `test_batch_categories_clcontinue_returns_none` (AC4.1 truncation) —
   `_info={"categories":[{"title":"Category:X"}], "clcontinue":"..."}` → `None`.
6. `test_batch_categories_malformed_entry_returns_none` (AC4.2) — `_info={"categories":[{"ns":14}]}`
   (entry without `"title"`) → `None`, not a partial list.

---

## Verification

Run: `uv run pytest tests/test_article_picker.py -q`
Also: `uv run ruff check wiki_cite/article_picker.py`
Expected: helper importable/callable; all helper unit tests pass; existing tests unaffected
(helper is not yet wired into the evaluation flow).

## Commit

`feat: add _batch_categories batch-read helper with fallback signal (#18)`
