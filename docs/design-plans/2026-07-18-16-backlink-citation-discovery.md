# Backlink Citation Discovery Design

## Summary
Give the agent's search loop a new tool that checks *other* Wikipedia articles linking to
the article being edited ("what links here" — MediaWiki `list=backlinks`, exposed by
mwclient as `page.backlinks()`) for citations that might also support the flagged claim.
Related articles that already discuss the same subject often already cite an external
source usable here too. The tool only *discovers* candidate external URLs already present
on the backlinking pages — it never lets the agent treat another Wikipedia article itself
as a citation (WP:CIRCULAR / WP:WPNOTRS). Every discovered URL goes through the exact same
`check_reliability()` + model-judgment pipeline as any other found source.

## Definition of Done
1. A new agent tool (`search_backlinks` or similar) is dispatched through the existing
   `_dispatch_search_tool` mechanism, consuming one of the loop's `max_search_turns` turns
   exactly like `search_web`/`search_scholar`/`search_crossref` do today — no separate
   model-turn budget.
2. Within that one tool call, up to a fixed, configured number of backlinking pages are
   scanned (`agent.max_backlink_pages_to_check`, default 10) — a hard cap, not an adaptive
   early-stop, mirroring the existing `max_candidates_per_fetch` cost-guard style.
3. For each scanned backlinking page, **all** distinct external citation URLs on that page
   are extracted (not just the first) — a new `extract_all_citation_urls()` alongside the
   existing single-URL `extract_citation_url()` — deduplicated across all scanned pages.
4. Every extracted URL is passed through the existing `check_reliability()` before being
   returned as a candidate `Source` — no separate/looser reliability path for
   backlink-discovered candidates.
5. The system prompt explicitly states the tool may never be used to cite a Wikipedia
   article as a source, mirroring the existing WP:RS/WP:PSTS/WP:SPS guardrail language
   (from #7) in structure and strength.
6. Sequential Wikipedia-etiquette is preserved: backlink pages are fetched one request at a
   time via the existing `_build_session()` retry/backoff pattern.
7. Tests cover: multi-URL extraction, the reliability pipeline receiving backlink-sourced
   candidates identically to any other tool's candidates, the tool's turn-budget behavior,
   and an explicit assertion that the system prompt forbids citing Wikipedia itself.

**Out of scope:** any change to `check_reliability()`'s domain whitelist itself; any new
cross-tool caching between `search_backlinks` and the other search tools; ranking/ordering
candidate URLs by anything beyond existing reliability rating.

## Acceptance Criteria

### 16-backlink-citation-discovery.AC1: Tool fits the existing bounded loop unchanged
- **AC1.1 Success:** `search_backlinks` is defined as a tool schema alongside
  `SEARCH_SCHOLAR_TOOL`/`SEARCH_WEB_TOOL`/etc., added to `SEARCH_TOOLS`/`ALL_TOOLS`, and
  routed through `_dispatch_search_tool` — the model calling it consumes one
  `max_search_turns` turn, same as any other search tool.
- **AC1.2 Failure:** When the loop's turn cap is already reached, `search_backlinks` is
  unavailable exactly like the other search tools are — only `propose_edits` remains
  offered, per the existing force-terminate logic.

### 16-backlink-citation-discovery.AC2: Bounded, sequential backlink page scan
- **AC2.1 Success:** The tool scans backlinking pages one at a time (reusing
  `_build_session()`), stopping after `agent.max_backlink_pages_to_check` pages regardless
  of how many total backlinks exist for the article.
- **AC2.2 Failure:** A failed fetch for one backlinking page (network error, or exhausted
  retries) is logged and skipped — the scan continues with the remaining pages rather than
  aborting the whole tool call.
- **AC2.3 Failure (self-reference):** The article currently being edited is never counted
  as one of its own backlinks even if `backlinks()` were to somehow include it.

### 16-backlink-citation-discovery.AC3: Multi-URL extraction per page
- **AC3.1 Success:** `extract_all_citation_urls(text: str) -> list[str]` returns every
  distinct URL found across all `{{cite ...}}` templates' `url`/`URL` parameters plus any
  bare `https?://` URLs in the text, deduplicated, preserving first-seen order.
- **AC3.2 Failure:** A page with no citations at all returns an empty list, not an error.
- **AC3.3 Success:** URLs are deduplicated *across* scanned pages too — the same external
  URL cited on two different backlinking pages is only surfaced once.

### 16-backlink-citation-discovery.AC4: Reliability pipeline parity
- **AC4.1 Success:** Every URL surfaced by `search_backlinks` is passed through the
  existing `check_reliability()` and returned as a `Source` with its `ReliabilityRating`
  set — identical shape/contract to `search_web`/`search_scholar`/`search_crossref`
  results, so downstream guardrails (`guardrails.py`) and the model's own judgment treat it
  no differently.
- **AC4.2 Failure:** A Wikipedia URL, if one were ever accidentally extracted (e.g. an
  interwiki link inside a cite template), is not exempted from `check_reliability()` — it
  is checked and rejected like any other source, not special-cased into acceptance.

### 16-backlink-citation-discovery.AC5: Explicit anti-circularity guardrail
- **AC5.1 Success:** The system prompt contains explicit language — in the same
  structural style as the existing WP:RS/WP:PSTS/WP:SPS block — stating that
  `search_backlinks` results are *candidate sources found via another article*, and that
  citing Wikipedia itself (including the backlinking article) as a source is never
  permitted (WP:CIRCULAR).
- **AC5.2 Failure:** A test asserts the system prompt text contains this guardrail
  language, so a future prompt edit can't silently drop it.

## Architecture

**Evidence from codebase investigation (2026-07-18):**
- The agentic loop (`wiki_cite/agent.py`) dispatches every search tool through one function,
  `_dispatch_search_tool(name, tool_input)`, which routes by name to a `source_finder`
  method and returns `(ok, payload)` without ever raising — `search_backlinks` is a fifth
  entry in this same table, nothing structurally new.
- `max_search_turns` bounds *model turns*, not per-tool internal work — `crawl_subcategories`
  (#10) already established the precedent that a single tool/command can internally fetch
  many pages sequentially without that counting against a model-turn budget. The backlink
  page-scan cap (`max_backlink_pages_to_check`) is exactly this kind of internal cost guard,
  independent of `max_search_turns`.
- `extract_citation_url()` (`source_finder.py`) already parses wikitext via
  `mwparserfromhell`, checking `{{cite ...}}` templates' `url`/`URL` params first and
  falling back to a bare-URL regex. `extract_all_citation_urls()` follows the identical
  parse/fallback shape but iterates every template and every bare-URL match instead of
  returning on the first hit.
- `check_reliability()` already has no Wikipedia-domain carve-out in its whitelist — this
  is load-bearing for AC4.2 and requires no change, only reuse.
- `article_picker.py`'s `_build_session()` (sequential retry/backoff,
  `mediawiki.org/wiki/API:Etiquette`) and `crawl_subcategories`'s visited-set pattern are
  the direct precedent for how `search_backlinks` must fetch backlinking pages — one at a
  time, cycle/dedup-safe (here, dedup is on *extracted URLs*, not on page titles, since
  backlinks don't form a traversal graph the way subcategories do — one level of
  `page.backlinks()` is sufficient, no recursion).
- `config.yaml`'s existing cost-guard block (`max_edits_per_article`,
  `max_candidates_per_fetch`, `max_search_turns`, `search_results_per_query`) is the
  template for the new `max_backlink_pages_to_check` key.

**Decisions made (do not re-litigate):**
- Backlink-page-scan cap is a **fixed hard cap** (`max_backlink_pages_to_check: 10`),
  not adaptive early-stopping — consistent with this project's existing preference for
  simple, predictable cost guards over adaptive logic (see `max_candidates_per_fetch`).
- Extraction is **all distinct URLs per page**, not first-URL-only — the citation most
  relevant to this article's specific claim is not guaranteed to be the first one on the
  backlinking page.

**Flow:**
1. Model calls `search_backlinks(article_title)` — one `max_search_turns` turn.
2. `_dispatch_search_tool` routes to a new `SourceFinder`/`ArticlePicker` method (final
   module decision left to the planner/implementor — likely `source_finder.py` to keep
   `check_reliability`/`Source` construction colocated, calling into a small
   `article_picker.py` or shared-session helper for the actual mwclient `.backlinks()`
   call, following the existing Wikipedia-facing-vs-Anthropic-facing module split from #10).
3. Fetch up to `max_backlink_pages_to_check` backlinking pages sequentially (skip failures,
   log and continue).
4. For each fetched page, run `extract_all_citation_urls()`, accumulate into a
   deduplicated set of candidate URLs across all scanned pages.
5. Run each candidate URL through `check_reliability()`, build `Source` objects exactly as
   the other search tools do.
6. Return the resulting `Source` list as the tool's JSON payload, same shape as
   `search_web`/`search_scholar`/`search_crossref`.

## Existing Patterns
- `_dispatch_search_tool()` (`agent.py`) — the dispatch table `search_backlinks` joins.
- `extract_citation_url()` (`source_finder.py`) — direct precedent for
  `extract_all_citation_urls()`'s parse/fallback shape.
- `check_reliability()` (`source_finder.py`) — reused unchanged; no Wikipedia-domain
  carve-out, which is exactly what AC4.2 depends on.
- `_build_session()` (`article_picker.py`) — reused unchanged for backlink-page fetches.
- `crawl_subcategories`'s cycle-safety/fail-skip-continue pattern (`article_picker.py`,
  #10) — precedent for "one bad page doesn't abort the whole scan."
- The WP:RS/WP:PSTS/WP:SPS system-prompt block (`agent.py`, #7) — the structural template
  the new anti-circularity guardrail language must match.
- `config.yaml`'s inline-commented cost-guard block — template for
  `max_backlink_pages_to_check`.
- `_SEARCH_TOOL_API_NAMES` (`agent.py`) — gets a new entry (e.g.
  `"search_backlinks": "wikipedia_backlinks"`) for activity-log labeling, same as every
  other search tool.

## Implementation Phases

### Phase 1: Multi-URL citation extraction
**Goal:** `extract_all_citation_urls()` in `source_finder.py`, parallel to the existing
single-URL function, dedup-preserving-order across cite templates and bare URLs.
**Components:** `wiki_cite/source_finder.py`.
**Done when:** AC3.1, AC3.2.

### Phase 2: Backlink page fetch + candidate assembly
**Goal:** New method that calls `page.backlinks()`, sequentially fetches up to
`max_backlink_pages_to_check` pages (skip-and-log on failure), extracts and cross-page
dedups candidate URLs via Phase 1, and runs each through `check_reliability()` to produce
`Source` objects.
**Components:** `wiki_cite/source_finder.py` (and/or `wiki_cite/article_picker.py` for the
raw mwclient call, per the #10 module split), `config.yaml` (`max_backlink_pages_to_check`),
`wiki_cite/config.py`.
**Done when:** AC2.1, AC2.2, AC2.3, AC3.3, AC4.1, AC4.2.

### Phase 3: Agent tool wiring + anti-circularity guardrail
**Goal:** New `search_backlinks` tool schema, `SEARCH_TOOLS`/`ALL_TOOLS` entry,
`_dispatch_search_tool` routing, `_SEARCH_TOOL_API_NAMES` entry, and explicit
WP:CIRCULAR guardrail language added to the system prompt in the same structural style as
the existing WP:RS/WP:PSTS/WP:SPS block.
**Components:** `wiki_cite/agent.py`.
**Done when:** AC1.1, AC1.2, AC5.1.

### Phase 4: Tests
**Goal:** Cover all ACs above — multi-URL extraction unit tests, mocked-`backlinks()`
dispatch tests (including a failing-page-skip case), reliability-pipeline-parity
assertion, turn-budget behavior, and the system-prompt guardrail-text assertion (AC5.2).
**Components:** `tests/test_source_finder.py`, `tests/test_agent.py`.
**Done when:** All ACs above have direct test coverage.

## Glossary
- **Backlink:** a Wikipedia article that links to the article currently being edited
  (MediaWiki "what links here" / `list=backlinks`).
- **Backlink scan:** the bounded, sequential fetch of up to
  `max_backlink_pages_to_check` backlinking pages within a single `search_backlinks` tool
  call.
- **Candidate citation URL:** an external URL extracted from a backlinking page's existing
  citations — never itself accepted as a source until it independently passes
  `check_reliability()` and the model's relevance judgment for the claim under edit.
- **Anti-circularity guardrail:** the explicit system-prompt language forbidding the agent
  from ever citing a Wikipedia article (WP:CIRCULAR / WP:WPNOTRS).
