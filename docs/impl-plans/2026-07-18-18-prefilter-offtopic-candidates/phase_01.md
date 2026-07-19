# Phase 1: Batch query extension — piggyback `prop=categories` onto the generator

**Goal:** Add `prop=categories&cllimit=max` to the `generator=categorymembers` batch query
in `fetch_candidates()`, following the existing `gcmstartsortkeyprefix` mutation pattern, so
each candidate's category membership arrives with the initial batch for zero extra per-page
requests.
**AC Coverage:** 18-prefilter-offtopic-candidates.AC1 (AC1.1, AC1.2)

---

## Context

`wiki_cite/article_picker.py::fetch_candidates()` iterates `for page in cat_page:` where
`cat_page = self.site.pages["Category:All_articles_with_unsourced_statements"]`. In the
installed mwclient, that object is an `mwclient.listing.Category` (a `GeneratorList`).

Verified against the installed mwclient (`.venv/.../mwclient/listing.py`,
`.venv/.../mwclient/page.py`):

- `GeneratorList.__init__` hardcodes onto the batch query (listing.py:180-181):
  ```python
  self.args['prop']   = 'info|imageinfo'
  self.args['inprop'] = 'protection'
  ```
  This combined `generator=categorymembers` query (up to 500 pages/chunk) is exactly why the
  protection check in `_evaluate_candidate()` costs no extra request — protection rides the
  batch via `inprop=protection`.
- `Category.args` is a plain mutable dict; the existing `category_start_prefix` feature already
  mutates it at line 554-555 of `article_picker.py`:
  ```python
  if start_prefix and hasattr(cat_page, "args"):
      cat_page.args["gcmstartsortkeyprefix"] = start_prefix
  ```
  This is the established, working precedent for extending the batch query before iteration.
- `load_chunk` (listing.py:83-118) issues the query from `self.args` and merges any
  `data['continue']` back into `self.args` — so a `clcontinue` continuation key (if the API
  ever returns one) is handled transparently by mwclient's generic continuation logic; no
  custom pagination is needed here.
- Each yielded page is constructed with the raw per-page response dict stored as
  `page._info` (page.py:34). Adding `prop=categories` makes each page's `_info` carry a
  `"categories"` list — the data Phase 2 reads. (Reading it is Phase 2; this phase only
  requests it.)

`prop` is a single pipe-delimited string value, not a list mwclient merges — so overwriting
`self.args['prop']` with the superset `'info|imageinfo|categories'` is correct and keeps the
`info|imageinfo|protection` data the current code depends on.

This phase is a query-shape change only. `_evaluate_candidate()` still reads categories via
`get_categories()` after this phase — the reorder and batch-read are Phases 2-3.

## Implementation

### Extend `cat_page.args` in `fetch_candidates()`

**Files:**
- Modify: `wiki_cite/article_picker.py` — `fetch_candidates()`, right where
  `gcmstartsortkeyprefix` is set (line ~553-555).

**What to implement:**

Alongside the existing `start_prefix` mutation, add the `prop`/`cllimit` merge under the same
`hasattr(cat_page, "args")` guard so test doubles without a mutable `.args` (e.g. a bare list)
are left untouched exactly as the `category_start_prefix` guard already does (AC1.2):

```python
start_prefix = self.config.article_selection.category_start_prefix
if hasattr(cat_page, "args"):
    if start_prefix:
        cat_page.args["gcmstartsortkeyprefix"] = start_prefix
    # Piggyback each candidate's category membership onto the batch
    # generator=categorymembers query so the topic filter can run before any
    # per-page fetch (see issue #18). prop is a single pipe-delimited value, so
    # overwriting the default 'info|imageinfo' with the superset is correct and
    # preserves the info|imageinfo|protection data the rest of the flow relies on.
    cat_page.args["prop"] = "info|imageinfo|categories"
    cat_page.args["cllimit"] = "max"
```

Notes:
- Keep the existing `start_prefix and hasattr(...)` behavior intact — the `prop`/`cllimit`
  additions must apply whenever `.args` is mutable, **independent** of whether a start prefix
  is configured (categories piggyback must happen on every fetch, prefix or not). Restructure
  to a single `if hasattr(cat_page, "args"):` block with the prefix set conditionally inside,
  as shown, so both mutations share the one guard.
- Do **not** parallelize or add a second request here — this is purely additive query args on
  the one existing sequential generator. Sequential-only is preserved (design DoD #6).
- No config change: `cllimit=max` is a constant, not a tunable.

## Tests

**File:** `tests/test_article_picker.py`

Mirror the existing `test_fetch_candidates_sets_start_sortkey_prefix` (line ~529), which builds
a `cat_page` mock with `cat_page.args = {}` and asserts the key lands after
`fetch_candidates()` runs. (Full AC-mapped coverage is owned by Phase 4; add or leave these to
Phase 4 as convenient — they are listed here so the phase is self-verifiable.)

1. `test_fetch_candidates_batch_query_requests_categories` (AC1.1) — build a `cat_page` mock
   with `cat_page.args = {}` and an empty/tiny member list; after `fetch_candidates()`, assert
   `cat_page.args["prop"] == "info|imageinfo|categories"` and `cat_page.args["cllimit"] == "max"`.
2. `test_fetch_candidates_batch_query_args_coexist_with_start_prefix` (AC1.1) — with
   `category_start_prefix` configured, assert all three keys (`gcmstartsortkeyprefix`, `prop`,
   `cllimit`) end up in `cat_page.args` together.
3. `test_fetch_candidates_batch_query_no_args_attr_is_safe` (AC1.2) — a `cat_page` that is a
   bare list (no `.args`) does not raise; mirrors
   `test_fetch_candidates_no_start_prefix_leaves_args_untouched`. Reuse the existing
   `mock_site.pages = {"Category:...": [ ... ]}` list-as-cat_page shape the other
   `fetch_candidates` tests already use — a plain list has no `.args`, so the guard is
   exercised directly.

---

## Verification

Run: `uv run pytest tests/test_article_picker.py -q`
Also: `uv run ruff check wiki_cite/article_picker.py`
Expected: existing tests still pass (the extra `prop`/`cllimit` args are inert until Phase 3
reads them); new arg-mutation tests pass.

## Commit

`feat: piggyback prop=categories onto the candidate batch query (#18)`
