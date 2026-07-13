# Test Requirements — Subcategory-Aware Topic Filter (issue #10)

Every acceptance criterion, the phase that implements it, and the test that verifies it.
Test command: `uv run pytest`. All coverage is automated; no manual verification is required
(the discovery command is exercised offline with `crawl_subcategories`/`classify_categories`
patched — see the "Operational note" below for the one thing only a real run confirms).

| AC | Description | Implemented in | Verified by |
|----|-------------|----------------|-------------|
| AC1.1 | Cycle-safe BFS crawl walks `members(namespace=14)`, sequential, reuses `_build_session()`, returns root + reachable subcats | Phase 1 (`crawl_subcategories`) | `tests/test_category_discovery.py::test_crawl_returns_root_and_all_reachable` (+ `..._respects_max_depth`); session reuse asserted in `tests/test_cli.py::test_cmd_discover_categories_writes_file` (uses `ArticlePicker().site`) |
| AC1.2 | Failed subcategory fetch is logged (`logger.warning`) and skipped; crawl still returns a partial result | Phase 1 | `tests/test_category_discovery.py::test_crawl_skips_failed_branch` |
| AC1.3 | A category reachable via two paths (or a cycle) is fetched/counted once | Phase 1 | `tests/test_category_discovery.py::test_crawl_cycle_terminates_and_dedupes` |
| AC2.1 | Names classified concurrently into content-relevant vs maintenance; `...stubs`/topical kept, task-force/quality/participant excluded | Phase 2 (`classify_categories`) | `tests/test_category_discovery.py::test_classify_keeps_content_excludes_maintenance` |
| AC2.2 | A failing/malformed classification call fails closed (excluded), is logged, does not abort the batch set | Phase 2 | `tests/test_category_discovery.py::test_classify_batch_error_fails_closed`, `::test_classify_malformed_response_excludes` |
| AC3.1 | Deterministic, sorted, deduped accepted list written to `data/category_expansions/<root-slug>.json` with root, timestamp, crawl params | Phase 3 (`write_expansion_file`) | `tests/test_category_discovery.py::test_write_expansion_file_format` |
| AC3.2 | Re-running for the same root overwrites deterministically (same inputs → same set, modulo timestamp); no append/merge | Phase 3 | `tests/test_category_discovery.py::test_write_expansion_deterministic_modulo_timestamp` |
| AC4.1 | `fetch_candidates` loads the expanded set from the static file and feeds it to the unchanged `category_filter()` | Phase 4 (`load_expansion`, `_expand_categories`) | `tests/test_article_picker.py::test_fetch_candidates_expands_include_from_file` |
| AC4.2 | A configured include category with no discovery file falls back to direct-match-only, no error | Phase 4 | `tests/test_article_picker.py::test_fetch_candidates_no_expansion_file_direct_match`; loader half: `tests/test_category_discovery.py::test_load_expansion_absent_returns_none` |
| AC5.1 | `relax_blp_when_topic_filtered=True` + active include filter → BLP check skipped in `_evaluate_candidate` | Phase 5 | `tests/test_article_picker.py::test_blp_relaxed_when_topic_filter_active` |
| AC5.2 | No include filter active → flag has no effect even when `True` | Phase 5 | `tests/test_article_picker.py::test_blp_not_relaxed_without_include_filter` |
| AC5.3 | Default (flag unset) is bit-for-bit identical to today — BLP always excluded | Phase 5 | `tests/test_article_picker.py::test_blp_default_flag_excludes`; config default: `tests/test_config.py::test_guardrails_relax_blp_default_false` |

## Supporting / non-AC tests
- `tests/test_config.py::test_config_load_relax_blp_flag` — the flag loads from a YAML
  `guardrails:` block.
- `tests/test_category_discovery.py::test_slugify_root_deterministic`,
  `::test_load_expansion_present` — slug + loader read path.
- `tests/test_cli.py::test_cmd_discover_categories_writes_file` — end-to-end command wiring,
  fully offline (crawl + classify patched).

## Operational note (not gating; no automated test)
The only thing tests cannot confirm is the *real* Wikipedia crawl rate/politeness and real
Anthropic classification quality on a live root — by design these are exercised by a human
running `uv run wiki-cite discover-categories "20th-century American politicians"` once when
picking an actual topic filter value (explicitly out of scope for this issue). The crawl
already inherits `_build_session()`'s 429/backoff handling, which has its own test
(`test_build_session_retries_on_429`).
