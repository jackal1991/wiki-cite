# Phase 4: Tests across all ACs

**Goal:** Direct, offline test coverage for every AC — multi-URL extraction, bounded/sequential
backlink scan (with self-reference exclusion and per-page-failure skip), cross-page dedup,
reliability-pipeline parity, the turn-budget behavior, and an explicit assertion that the system
prompt carries the anti-circularity guardrail.
**AC Coverage:** All of AC1, AC2, AC3, AC4, AC5.

---

## Context

Test conventions (verified in the repo):
- `tests/test_<module>.py`, plain `pytest` functions, `unittest.mock.Mock`/`patch`,
  `types.SimpleNamespace` for fake API objects.
- `tests/test_source_finder.py` already fixtures a `source_finder` (`SourceFinder()`), and tests
  `extract_citation_url` directly (`tests/test_source_finder.py:266-281`) — the pattern
  `extract_all_citation_urls` tests follow.
- `tests/test_agent.py` builds an `agent` fixture under `patch("wiki_cite.agent.Anthropic")`
  with `instance.client = SimpleNamespace(messages=SimpleNamespace(create=...))`
  (`tests/test_agent.py:32-34`), fakes response/content blocks via `SimpleNamespace`
  (`tests/test_agent.py:13-26`), and patches `agent.source_finder` methods with
  `patch.object(agent.source_finder, "search_web", ...)` (`tests/test_agent.py:161-214`). The
  turn-cap test `test_turn_cap_forces_terminal_tool` (`tests/test_agent.py:361`) and the
  system-prompt assertion `test_search_system_prompt_carries_sourcing_policy`
  (`tests/test_agent.py:61`) are the direct templates for AC1.2 and AC5.2.
- mwclient objects are mocked as plain objects with the attributes used: a backlink page needs
  `.name` (str) and `.text()` (returns wikitext or raises); the edited page's `.backlinks(...)`
  returns an iterable of those. `site.pages` is a `Mock`/dict supporting `site.pages[title]`.
- Config is global (`get_config()`); `test_config.py` already asserts `AgentConfig` defaults —
  extend it for `max_backlink_pages_to_check`.
- Coverage + branch coverage are on by default (`pyproject.toml` `addopts`); keep new code
  exercised.

Phase 1 → `extract_all_citation_urls` (`source_finder.py`). Phase 2 →
`fetch_backlink_pages` (`article_picker.py`), `SourceFinder.find_backlink_sources`, config key.
Phase 3 → `SEARCH_BACKLINKS_TOOL`, dispatch routing, prompt guardrail.

## Implementation

### `tests/test_source_finder.py` (extend)

**Multi-URL extraction (AC3.1, AC3.2)** — `extract_all_citation_urls`, imported like the
existing `extract_citation_url`:
- `test_extract_all_citation_urls_multiple_cites_and_bare` (AC3.1): wikitext with two
  `{{cite web|url=...}}` templates (distinct URLs) plus a bare `<ref>https://...</ref>` →
  returns all three distinct URLs in first-seen order.
- `test_extract_all_citation_urls_dedups_preserving_order` (AC3.1): the same URL appearing in a
  cite template and again bare → appears once, at its first-seen position.
- `test_extract_all_citation_urls_empty_when_no_citations` (AC3.2): plain prose with no
  URLs/cites → `[]` (no error).

**Candidate assembly (AC2.1, AC2.2, AC2.3, AC3.3, AC4.1, AC4.2)** —
`SourceFinder.find_backlink_sources(article_title, *, site=<mock>)` with an injected mock site so
no network is touched. Build a small `_make_site(...)` helper mapping the edited title's
`.backlinks(...)` to a list of fake backlink pages (each `SimpleNamespace`/`Mock` with `.name`
and a `.text` callable). Prefer injecting `site=` directly; alternatively
`patch("wiki_cite.source_finder.fetch_backlink_pages", ...)` for the pure-assembly tests.
- `test_find_backlink_sources_caps_pages` (AC2.1): a site whose edited page has more backlinks
  than `max_backlink_pages_to_check` → only that many pages are `.text()`-fetched (assert call
  count) regardless of total backlinks. Set the cap small via config for the test.
- `test_find_backlink_sources_skips_failed_page` (AC2.2): one backlink page's `.text()` raises →
  it is logged (`caplog`) and skipped; the other pages' URLs still returned; no exception
  escapes; the failed page does not consume a cap slot.
- `test_find_backlink_sources_excludes_self_reference` (AC2.3): a backlink whose `.name`
  normalizes to the edited article's title is not fetched and not represented in the output.
- `test_find_backlink_sources_dedups_across_pages` (AC3.3): the same external URL cited on two
  different backlink pages appears exactly once in the result.
- `test_find_backlink_sources_reliability_parity` (AC4.1): a known-reliable URL
  (`https://www.bbc.com/...`) comes back as a `Source` with
  `reliability == ReliabilityRating.GENERALLY_RELIABLE`, `url` set, `source_type ==
  SourceType.WEB` — same `Source` shape the other tools produce; assert
  `check_reliability` was applied (rating matches `source_finder.check_reliability(url)`).
- `test_find_backlink_sources_wikipedia_url_not_exempted` (AC4.2): a
  `https://en.wikipedia.org/...` URL among the extracted candidates is returned as a `Source`
  with `reliability == source_finder.check_reliability(that_url)` — i.e. passed through
  `check_reliability` with no exemption and no forced acceptance (it is NOT special-cased to
  GENERALLY_RELIABLE and NOT dropped from the pipeline). This proves parity, not deletion —
  actual rejection is the model/prompt's job.

### `tests/test_article_picker.py` (extend)

**Bounded sequential fetch (AC2.1, AC2.2, AC2.3)** — `fetch_backlink_pages(site, title,
max_pages)` with a mock site (reuse the file's existing mwclient-mock idioms):
- `test_fetch_backlink_pages_caps_and_is_sequential` (AC2.1): more backlinks than `max_pages` →
  returns exactly `max_pages` `(name, text)` tuples; only that many `.text()` calls made.
- `test_fetch_backlink_pages_skips_failed_page` (AC2.2): a page whose `.text()` raises is logged
  and skipped; siblings still returned; a `.backlinks()` call that itself raises → `[]` (no
  exception escapes).
- `test_fetch_backlink_pages_excludes_self` (AC2.3): a backlink whose `.name` equals the edited
  title (modulo underscore/case normalization) is skipped and never `.text()`-fetched.
  (This is the article_picker-level counterpart to the source_finder self-reference test; both
  are cheap and pin the behavior at each layer.)

### `tests/test_agent.py` (extend)

**Tool wiring + budget (AC1.1, AC1.2)** and **guardrail text (AC5.1, AC5.2)**:
- `test_dispatch_search_backlinks_success` (AC1.1): `patch.object(agent.source_finder,
  "find_backlink_sources", return_value=[<a Source>])`; `agent._dispatch_search_tool(
  "search_backlinks", {"article_title": "Foo"})` → `ok is True` and the payload is the
  `_sources_to_dicts` JSON of that source (same shape as the existing
  `test_dispatch_search_web_success`). Assert `find_backlink_sources` was called with
  `"Foo"`.
- `test_search_backlinks_in_tool_lists` (AC1.1): `SEARCH_BACKLINKS_TOOL in SEARCH_TOOLS` and
  `in ALL_TOOLS`; `_SEARCH_TOOL_API_NAMES["search_backlinks"] == "wikipedia_backlinks"`.
- `test_search_backlinks_unavailable_at_turn_cap` (AC1.2): extend/mirror
  `test_turn_cap_forces_terminal_tool` — at the cap the tools offered are `[PROPOSE_EDITS_TOOL]`
  only, so `SEARCH_BACKLINKS_TOOL` (like every search tool) is absent. Assert against the
  `tools=` kwarg captured from the final `messages.create` call.
- `test_search_system_prompt_forbids_citing_wikipedia` (AC5.2): assert `SEARCH_SYSTEM_PROMPT`
  contains the anti-circularity language — e.g. `"WP:CIRCULAR"` and a phrase forbidding citing
  Wikipedia itself / the backlinking article as a source. This mirrors the existing
  `test_search_system_prompt_carries_sourcing_policy` and is the regression guard that a future
  prompt edit can't silently drop the guardrail (AC5.2 explicitly requires this test).

### `tests/test_config.py` (extend)

- `test_agent_config_max_backlink_pages_default`: `AgentConfig().max_backlink_pages_to_check
  == 10` (AC2.1 config side).
- If `test_config.py` has a YAML-load test for the `agent:` block, extend it (or add one) to
  assert `max_backlink_pages_to_check` loads from a YAML `agent:` block.

## Verification

Run: `uv run pytest -q`
Also: `uv run ruff check .`
Also (optional, catches accidental network use): confirm the new source_finder/agent tests pass
with no real key/site by running them in isolation:
`uv run pytest tests/test_source_finder.py tests/test_agent.py tests/test_article_picker.py -q`.
Expected: all tests pass (coverage + branch coverage on by default); every AC in
`test-requirements.md` has at least one passing test; no test performs real Wikipedia/Anthropic
I/O.

## Commit

`test: cover backlink citation discovery across all ACs`
