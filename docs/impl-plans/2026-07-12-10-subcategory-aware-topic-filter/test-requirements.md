# Test Requirements — Subcategory-Aware Topic Filter (issue #10)

Every acceptance criterion, the phase that implements it, and the test that verifies it.
Test command: `uv run pytest`. All coverage is automated; no manual verification is required
(the discovery command is exercised offline with `crawl_subcategories`/`classify_categories`
patched — see the "Operational note" below for the one thing only a real run confirms).

| AC | Description | Implemented in | Verified by |
|----|-------------|----------------|-------------|
| AC1.1 | Cycle-safe BFS crawl walks `members(namespace=14)`, sequential, reuses `_build_session()`, returns root + reachable subcats | Phase 1 (`crawl_subcategories`) | `tests/test_article_picker.py::test_crawl_subcategories_shallow_tree` (+ `::test_crawl_subcategories_respects_max_depth`, `::test_crawl_subcategories_strips_category_prefix`); session reuse asserted in `tests/test_cli.py::test_cmd_discover_categories_writes_file` (uses `ArticlePicker().site`) |
| AC1.2 | Failed subcategory fetch is logged (`logger.warning`) and skipped; crawl still returns a partial result | Phase 1 | `tests/test_article_picker.py::test_crawl_subcategories_degrades_on_branch_failure` |
| AC1.3 | A category reachable via two paths (or a cycle) is fetched/counted once | Phase 1 | `tests/test_article_picker.py::test_crawl_subcategories_handles_cycles_and_diamonds` |
| AC2.1 | Names classified concurrently into content-relevant vs maintenance; `...stubs`/topical kept, task-force/quality/participant excluded | Phase 2 (`classify_categories`) | `tests/test_category_discovery.py::test_classify_batch_keeps_content_and_drops_maintenance` |
| AC2.2 | A failing/malformed classification call fails closed (excluded), is logged, does not abort the batch set | Phase 2 | `tests/test_category_discovery.py::test_classify_batch_raises_excludes_whole_batch`, `::test_classify_batch_malformed_response_excludes_whole_batch`, `::test_classify_categories_unions_batches_and_fails_closed_per_batch` |
| AC3.1 | Deterministic, sorted, deduped accepted list written to `data/category_expansions/<root-slug>.json` with root, timestamp, crawl params | Phase 3 (`write_expansion_file`) | `tests/test_category_discovery.py::test_write_expansion_file_includes_root_sorted_deduplicated` |
| AC3.2 | Re-running for the same root overwrites deterministically (same inputs → same set, modulo timestamp); no append/merge | Phase 3 | `tests/test_category_discovery.py::test_write_expansion_file_deterministic_except_timestamp`, `::test_write_expansion_file_overwrites_wholesale` |
| AC4.1 | `fetch_candidates` loads the expanded set from the static file and feeds it to the unchanged `category_filter()` | Phase 4 (`load_expansion`, `_expand_categories`) | `tests/test_article_picker.py::test_fetch_candidates_expands_include_category_via_discovery_file` |
| AC4.2 | A configured include category with no discovery file falls back to direct-match-only, no error | Phase 4 | `tests/test_article_picker.py::test_fetch_candidates_no_discovery_file_is_direct_match_only`; loader half: `tests/test_category_discovery.py::test_load_expansion_returns_none_when_file_absent` |
| AC5.1 | `relax_blp_when_topic_filtered=True` + active include filter → BLP check skipped in `_evaluate_candidate` | Phase 5 | `tests/test_article_picker.py::test_is_candidate_blp_relaxed_with_active_include_filter_is_accepted` |
| AC5.2 | No include filter active → flag has no effect even when `True` | Phase 5 | `tests/test_article_picker.py::test_is_candidate_blp_relaxed_without_include_filter_still_rejects` (unit level) and `::test_fetch_candidates_blp_relaxation_flag_has_zero_effect_with_no_include_filter` (through the real `fetch_candidates` request path, with no include filter configured anywhere) |
| AC5.3 | Default (flag unset) is bit-for-bit identical to today — BLP always excluded | Phase 5 | `tests/test_article_picker.py::test_is_candidate_blp_default_flag_rejects_with_include_filter`, `::test_is_candidate_blp_default_flag_rejects_without_include_filter`; config default: `tests/test_config.py::test_guardrails_config_defaults` |

## Supporting / non-AC tests
- `tests/test_config.py::test_config_load_relax_blp_when_topic_filtered_from_yaml` — the flag
  loads from a YAML `guardrails:` block.
- `tests/test_category_discovery.py::test_slugify_root_strips_prefix_and_normalizes`,
  `::test_slugify_root_drops_non_alnum_hyphen_chars`,
  `::test_load_expansion_returns_categories_when_file_exists` — slug + loader read path.
- `tests/test_cli.py::test_cmd_discover_categories_writes_file` — end-to-end command wiring,
  fully offline (crawl + classify + `ArticlePicker` patched; the real `write_expansion_file`
  runs against a `tmp_path` expansion dir and the resulting JSON file is asserted on disk).
  `::test_cmd_discover_categories_wires_crawl_classify_write` — argument-passing wiring with
  `write_expansion_file` also mocked.

## Operational note (not gating; no automated test)
The only thing tests cannot confirm is the *real* Wikipedia crawl rate/politeness and real
Anthropic classification quality on a live root — by design these are exercised by a human
running `uv run wiki-cite discover-categories "20th-century American politicians"` once when
picking an actual topic filter value (explicitly out of scope for this issue). The crawl
already inherits `_build_session()`'s 429/backoff handling, which has its own test
(`test_build_session_retries_on_429`).
