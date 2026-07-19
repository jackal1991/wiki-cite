# Phase 4: Tests across all ACs

**Goal:** Direct, AC-mapped test coverage for the whole change — batch-args mutation, the
batch-read helper + fallback, zero `page.text()` on off-topic reject, zero `page.categories()`
on accept, category parity between batch and fallback paths, and no-filter parity — plus any
updates existing `fetch_candidates` tests need under the new flow.
**AC Coverage:** all of AC1–AC5 (verification layer).

---

## Context

`tests/test_article_picker.py` conventions (verified):
- `mock_site` fixture = `Mock()`; `picker` fixture = `ArticlePicker(site=mock_site)`.
- `fetch_candidates` tests set `mock_site.pages = {"Category:All_articles_with_unsourced_statements":
  [page1, page2, ...]}` where the value is a **plain list** acting as `cat_page` (so it has no
  `.args` — this exercises Phase 1's `hasattr` guard) and each page is a `Mock()` with
  `.name/.redirect/.namespace/.protection/.revision/.text/.categories` set.
- `test_fetch_candidates_sets_start_sortkey_prefix` (line ~529) shows the pattern for asserting
  on `cat_page.args`: build a dedicated `cat_page` Mock with `cat_page.args = {}`, put it in
  `mock_site.pages`, run `fetch_candidates`, assert on `cat_page.args[...]`.

**Page double shape under the new flow.** Two valid ways to build a page for
`fetch_candidates`/`_evaluate_candidate` tests after this change:
- **Batch path** (exercises AC2/AC3): set `page._info = {"categories": [{"ns":14,"title":
  "Category:X"}, ...]}` so `_batch_categories()` returns the names and `page.categories` is
  never called. To *prove* it is never called, set `page.categories = Mock()` and assert
  `page.categories.assert_not_called()`.
- **Fallback path** (exercises AC4): set `page._info` to something the helper rejects (a bare
  Mock, or `{}`, or a dict with `clcontinue`) and set `page.categories = Mock(return_value=
  [cat_mock])` where `cat_mock.name = "Category:X"`, so the code falls back to
  `get_categories()`.

A tiny local page-builder helper in the test module keeps these readable, e.g.:

```python
def make_page(name, *, cats, text="A notable claim.{{Citation needed}}", ns=0,
              redirect=False, protection=None, batch=True):
    p = Mock()
    p.name = name
    p.redirect = redirect
    p.namespace = ns
    p.protection = protection or {}
    p.revision = "1"
    p.text = Mock(return_value=text)
    if batch:
        p._info = {"categories": [{"ns": 14, "title": f"Category:{c}"} for c in cats]}
        p.categories = Mock()  # must NOT be called on the batch path
    else:
        p._info = {}  # force fallback
        cat_mocks = [Mock(name=f"cm{c}") for c in cats]
        for m, c in zip(cat_mocks, cats):
            m.name = f"Category:{c}"
        p.categories = Mock(return_value=cat_mocks)
    return p
```

## Implementation

**Files:**
- Modify: `tests/test_article_picker.py` — add the tests below; update existing
  `fetch_candidates` accept/reject tests to set `page._info` (batch path) so they assert the
  intended zero-extra-request behavior rather than silently relying on the fallback.

### AC1 — batch query carries category data
1. `test_fetch_candidates_batch_query_requests_categories` — dedicated `cat_page` Mock with
   `.args = {}`, empty members; after `fetch_candidates()`, `cat_page.args["prop"] ==
   "info|imageinfo|categories"` and `cat_page.args["cllimit"] == "max"`.
2. `test_fetch_candidates_batch_query_args_coexist_with_start_prefix` — with
   `article_selection.category_start_prefix` set (use the `restore_config` fixture like the
   existing sortkey test), assert all three keys present in `cat_page.args`.
3. `test_fetch_candidates_batch_query_no_args_attr_is_safe` — `cat_page` is a bare list; no
   raise (mirrors `test_fetch_candidates_no_start_prefix_leaves_args_untouched`).

### AC2 — topic filter before any per-page fetch
4. `test_offtopic_candidate_rejected_without_text_fetch` (AC2.1) — `_evaluate_candidate` (or via
   `fetch_candidates`) on a batch-path page whose categories miss an active `include` filter:
   assert result is reject **and** `page.text.assert_not_called()`. Configure `include` via the
   `include_categories=[...]` override arg to `fetch_candidates`/`is_candidate`.
5. `test_ontopic_candidate_proceeds_to_text_fetch` (AC2.2) — a batch-path page whose categories
   match the `include` filter: `page.text` **is** called and the candidate is accepted. Also
   assert the no-filter case (`include=[]`) still reaches `page.text()`.

### AC3 — on-topic candidates reuse batch category data
6. `test_ontopic_accept_never_calls_page_categories` (AC3.1) — accepted batch-path page:
   `page.categories.assert_not_called()`; the built `CandidateArticle.categories` come from the
   batch data.
7. `test_batch_and_fallback_categories_are_content_identical` (AC3.2) — build one page via the
   batch path and an equivalent via the fallback path with the same titles; assert
   `set(_batch_categories(batch_page)) == set(get_categories(fallback_page))` (same names,
   prefix stripped, order-independent).

### AC4 — graceful fallback on incomplete batch data
8. `test_fallback_used_when_info_missing_categories` (AC4.1) — `_info={}` page: `_evaluate_candidate`
   falls back and calls `page.categories()` (assert called once); decision matches the batch-path
   equivalent.
9. `test_fallback_used_on_clcontinue_truncation` (AC4.1) — `_info={"categories":[...],
   "clcontinue":"x"}`: falls back to `get_categories()`; assert `page.categories` called.
10. `test_fallback_result_matches_full_list_decision` (AC4.2) — a page whose *full* category set
    would pass the include filter but whose batch data is truncated/missing: via the fallback it
    still **passes** (not silently rejected); and a symmetric page that should be rejected is
    rejected. Compare against the pre-#18 `get_categories()`-only decision.
11. (Phase 2 helper unit tests 1–6 from `phase_02.md`, if not already added there, land here.)

### AC5 — no-topic-filter behavior unchanged
12. `test_no_topic_filter_output_and_requests_unchanged` (AC5.1) — with `include=[]` and
    `exclude=[]` (default), a batch-path page is accepted exactly as before; `page.text` is
    called (needed for citation-needed) and `page.categories` is **not** (batch supplies them).
    Assert the yielded `CandidateArticle` matches the pre-change expectation (title, categories).

### Existing-test migration
13. Update `test_fetch_candidates_skips_seen`, `test_fetch_candidates_passes_category_overrides`,
    and the ranking tests (`test_fetch_candidates_ranks_by_learned_rate`, etc.) to give their
    fresh/kept pages a `page._info={"categories":[...]}` so they run the batch path and assert
    `page.categories` is not called — otherwise they pass only via the fallback and no longer
    prove the intended request savings. Keep at least one test on the explicit fallback path
    (AC4) so both paths stay covered.

## Notes

- Do not add any live-network or `integration`-marked test — the whole change is exercised with
  in-memory doubles. (An operational note about the real API's `prop=categories&cllimit=max`
  response shape lives in `test-requirements.md`, not as a gating test.)
- Sequential-only is a structural property (no new request sites), asserted indirectly by the
  `assert_not_called()` checks — no timing/concurrency test needed.

---

## Verification

Run: `uv run pytest tests/test_article_picker.py -q` then the full `uv run pytest`.
Also: `uv run ruff check .`
Expected: all new + migrated tests pass; branch coverage on `_batch_categories` and the
reordered `_evaluate_candidate` is complete (both the batch and fallback branches hit).

## Commit

`test: cover batch-category prefilter, fallback, and no-filter parity (#18)`
