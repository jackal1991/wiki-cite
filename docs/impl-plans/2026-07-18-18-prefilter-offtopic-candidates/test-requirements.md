# Test Requirements — Pre-filter Off-topic Candidates (issue #18)

Every acceptance criterion, the phase that implements it, and the test that verifies it.
Test command: `uv run pytest`. All coverage is automated with in-memory doubles — no live
Wikipedia call and no `integration`-marked test is needed (see the operational note below for
the one thing only a real API call confirms). All tests live in `tests/test_article_picker.py`.

| AC | Description | Implemented in | Verified by |
|----|-------------|----------------|-------------|
| AC1.1 | Batch query merges `prop=info\|imageinfo\|categories` and `cllimit=max` into `cat_page.args`, coexisting with `gcmstartsortkeyprefix` | Phase 1 (`fetch_candidates`) | `test_fetch_candidates_batch_query_requests_categories`, `test_fetch_candidates_batch_query_args_coexist_with_start_prefix` |
| AC1.2 | A `cat_page` without a mutable `.args` (bare list) is left untouched, not raised on | Phase 1 | `test_fetch_candidates_batch_query_no_args_attr_is_safe` (mirrors `test_fetch_candidates_no_start_prefix_leaves_args_untouched`) |
| AC2.1 | Off-topic candidate (batch categories miss active include filter) rejected **before** `page.text()` | Phase 3 (`_evaluate_candidate`) | `test_offtopic_candidate_rejected_without_text_fetch` (`page.text.assert_not_called()`) |
| AC2.2 | On-topic candidate (or no filter configured) proceeds to `page.text()` as today | Phase 3 | `test_ontopic_candidate_proceeds_to_text_fetch` |
| AC3.1 | Accepted candidate never calls the per-page `page.categories()`; categories come from batch data | Phase 2 + 3 | `test_ontopic_accept_never_calls_page_categories` (`page.categories.assert_not_called()`) |
| AC3.2 | Batch-derived `CandidateArticle.categories` are content-identical (prefix-stripped, order-independent) to the `get_categories()` path | Phase 2 | `test_batch_and_fallback_categories_are_content_identical`, `test_batch_categories_reads_info_and_strips_prefix` |
| AC4.1 | Missing `categories` key or a `clcontinue` marker → fall back to per-page `get_categories()` for that page only | Phase 2 (`_batch_categories`) + 3 (wiring) | `test_batch_categories_missing_key_returns_none`, `test_batch_categories_clcontinue_returns_none`, `test_batch_categories_non_dict_info_returns_none`, `test_fallback_used_when_info_missing_categories`, `test_fallback_used_on_clcontinue_truncation` |
| AC4.2 | Fallback never silently flips a decision vs. the full-list result; malformed entry falls back rather than filtering on a partial list | Phase 2 + 3 | `test_batch_categories_malformed_entry_returns_none`, `test_batch_categories_empty_list_is_complete_not_fallback`, `test_fallback_result_matches_full_list_decision` |
| AC5.1 | Both include/exclude empty → output and request pattern unchanged (`category_filter([],[],[])` no-op preserved) | Phase 3 | `test_no_topic_filter_output_and_requests_unchanged` |

## Supporting / migration tests
- `test_fetch_candidates_skips_seen`, `test_fetch_candidates_passes_category_overrides`, and the
  ranking tests (`test_fetch_candidates_ranks_by_learned_rate`,
  `test_fetch_candidates_disabled_feedback_is_category_order`,
  `test_fetch_candidates_missing_db_matches_category_order`) — migrated in Phase 4 to give
  kept/fresh pages a `page._info={"categories":[...]}` so they exercise the batch path and assert
  `page.categories` is not called. At least one test stays on the explicit fallback path (AC4) so
  both branches remain covered.
- `_batch_categories` unit tests (Phase 2): the six `test_batch_categories_*` cases give the
  helper full branch coverage independent of the `fetch_candidates` request path.

## Coverage note
Branch coverage is on by default (`uv run pytest`). Both branches of `_batch_categories`'s
guards (dict/non-dict `_info`, present/absent `categories`, `clcontinue`, malformed entry) and
both arms of `_evaluate_candidate`'s new `categories is None` fallback must be hit — the AC4
fallback tests plus the AC2/AC3 batch-path tests cover both arms.

## Operational note (not gating; no automated test)
Tests cannot confirm the *real* MediaWiki response shape for
`generator=categorymembers&prop=categories&cllimit=max` — the design doc live-verified it as
`{"categories": [{"ns": 14, "title": "Category:X"}, ...]}` per page with no `clcontinue` even
for a 12-category page. That shape is the assumption `_batch_categories` parses; if a future
mwclient/API change alters it, the helper's guards degrade to the per-page fallback (slower but
correct) rather than misbehaving. A human confirms real request-count savings by running
`uv run wiki-cite fetch` under an active topic filter and watching the request volume drop — out
of scope for automated tests.
