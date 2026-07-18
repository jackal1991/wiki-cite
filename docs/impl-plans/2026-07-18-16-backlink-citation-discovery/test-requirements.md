# Test Requirements — Backlink Citation Discovery (issue #16)

Every acceptance criterion, the phase that implements it, and the test that verifies it.
Test command: `uv run pytest`. All coverage is automated and fully offline: mwclient
`backlinks()`/`text()` and the Anthropic client are mocked, so no test performs real
Wikipedia or Anthropic I/O. The one thing tests cannot confirm — real backlink
volume/politeness and real model citation judgment on a live article — is exercised only by a
human running `uv run wiki-cite analyze "<title>"` against a real article (see Operational note).

| AC | Description | Implemented in | Verified by |
|----|-------------|----------------|-------------|
| AC1.1 | `search_backlinks` defined as a tool schema, added to `SEARCH_TOOLS`/`ALL_TOOLS`, routed through `_dispatch_search_tool`; consumes one `max_search_turns` turn like any search tool; results are `_sources_to_dicts`-shaped | Phase 3 | `tests/test_agent.py::test_dispatch_search_backlinks_success`, `::test_search_backlinks_in_tool_lists` |
| AC1.2 | At the turn cap, `search_backlinks` is unavailable exactly like the other search tools — only `propose_edits` is offered | Phase 3 (free from `SEARCH_TOOLS` membership + existing force-terminate at `agent.py:444`) | `tests/test_agent.py::test_search_backlinks_unavailable_at_turn_cap` |
| AC2.1 | Bounded scan: fetch backlinking pages one at a time, stop after `agent.max_backlink_pages_to_check` (hard cap, counts only successfully fetched pages) | Phase 2 (`fetch_backlink_pages`, config key) | `tests/test_article_picker.py::test_fetch_backlink_pages_caps_and_is_sequential`; `tests/test_source_finder.py::test_find_backlink_sources_caps_pages`; config default `tests/test_config.py::test_agent_config_max_backlink_pages_default` |
| AC2.2 | A failed per-page fetch (or a failed `.backlinks()` call) is logged and skipped; the scan continues / returns a partial result rather than aborting | Phase 2 | `tests/test_article_picker.py::test_fetch_backlink_pages_skips_failed_page`; `tests/test_source_finder.py::test_find_backlink_sources_skips_failed_page` |
| AC2.3 | The edited article is never counted among its own backlinks (normalized-title self-reference exclusion) | Phase 2 | `tests/test_article_picker.py::test_fetch_backlink_pages_excludes_self`; `tests/test_source_finder.py::test_find_backlink_sources_excludes_self_reference` |
| AC3.1 | `extract_all_citation_urls` returns every distinct cite-template `url`/`URL` and bare `https?://` URL, deduped, first-seen order | Phase 1 | `tests/test_source_finder.py::test_extract_all_citation_urls_multiple_cites_and_bare`, `::test_extract_all_citation_urls_dedups_preserving_order` |
| AC3.2 | A page with no citations returns `[]`, not an error | Phase 1 | `tests/test_source_finder.py::test_extract_all_citation_urls_empty_when_no_citations` |
| AC3.3 | The same external URL cited on two different backlinking pages is surfaced once (cross-page dedup) | Phase 2 (`find_backlink_sources`) | `tests/test_source_finder.py::test_find_backlink_sources_dedups_across_pages` |
| AC4.1 | Every surfaced URL passes through the existing `check_reliability()` and returns as a `Source` with `reliability` set — identical shape/contract to `search_web`/`search_scholar`/`search_crossref` | Phase 2 | `tests/test_source_finder.py::test_find_backlink_sources_reliability_parity`; shape-at-dispatch: `tests/test_agent.py::test_dispatch_search_backlinks_success` |
| AC4.2 | A wikipedia.org URL (if ever extracted) is not exempted from `check_reliability()` — checked with no special-case acceptance, not dropped from the pipeline | Phase 2 | `tests/test_source_finder.py::test_find_backlink_sources_wikipedia_url_not_exempted` |
| AC5.1 | System prompt contains explicit WP:CIRCULAR / WP:WPNOTRS language (same structural style as the WP:RS/WP:PSTS/WP:SPS block) stating backlink results are candidate sources found via another article and Wikipedia itself is never citable | Phase 3 | Text present asserted by `tests/test_agent.py::test_search_system_prompt_forbids_citing_wikipedia` |
| AC5.2 | A test asserts the guardrail language is in the prompt, so a future edit can't silently drop it | Phase 4 | `tests/test_agent.py::test_search_system_prompt_forbids_citing_wikipedia` |

## Supporting / non-AC tests
- `tests/test_config.py` — `max_backlink_pages_to_check` loads from a YAML `agent:` block (if the
  file already has an `agent:` YAML-load test, extend it).
- Import-cycle smoke: `uv run python -c "import wiki_cite.source_finder, wiki_cite.article_picker"`
  (source_finder importing `fetch_backlink_pages` from article_picker must not cycle).

## Operational note (not gating; no automated test)
Tests cannot confirm the *real* backlink volume / crawl politeness on a live article, nor the
model's real judgment that a backlink-discovered source genuinely supports a specific claim
(and that it never cites the backlinking Wikipedia article) — by design these are exercised by a
human running `uv run wiki-cite analyze "<title>"` on a well-linked article once during review.
The sequential per-page fetch inherits `_build_session()`'s 429/backoff handling, which already
has its own test (`tests/test_source_finder.py::test_source_finder_session_retries_on_429` and
`tests/test_article_picker.py::test_build_session_retries_on_429`).
