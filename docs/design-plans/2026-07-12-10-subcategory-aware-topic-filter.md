# Subcategory-Aware Topic Filter Design

## Summary
`ArticlePicker`'s `include_categories`/`exclude_categories` topic filter (from #6) only
matches an article's *direct* MediaWiki categories. Wikipedia organizes broad topics into
deep subcategory trees with very few direct article members at the top, so the filter is
unusable for anything but a narrow, already-leaf-level category. This design adds a bounded,
cached subcategory-tree expansion so a broad root category (e.g. "Politics of the United
States") resolves to the full set of descendant categories the filter actually checks
against — while staying within this project's sequential-only Wikipedia request constraint.

## Definition of Done
1. `ArticlePicker` matches candidates against the transitive subcategory closure of
   configured include/exclude categories, not just direct membership — bounded by a
   configurable depth/total-call budget.
2. The subcategory expansion is cached in-memory with a TTL, so it isn't re-walked on
   every fetch.
3. Stays strictly sequential — no concurrency reintroduced into Wikipedia calls.
4. `include_categories`/`exclude_categories` config, `/api/settings/categories`, and
   `/api/categories/search` keep working unchanged from the caller's side — this only
   makes the matcher underneath smarter.
5. A new, explicit, opt-in guardrail flag lets a scoped topic filter relax the
   always-on BLP exclusion — separate from the subcategory mechanism, off by default,
   and inert unless a topic filter is actually active.
6. Tests for depth/budget bounds, cache hit/miss, matching correctness (worked example:
   a stub biography several levels under "20th-century American politicians"), graceful
   degradation on a broken subcategory fetch, and the BLP-relaxation flag's scoping.

**Out of scope:** picking today's actual "US politics" filter value, or turning BLP
relaxation on by default — this design builds the mechanisms; using them is a follow-up.

## Acceptance Criteria

### 10-subcategory-aware-topic-filter.AC1: Bounded subcategory tree expansion
- **AC1.1 Success:** Given a root category with arbitrarily deep subcategories, expanding
  it returns the set of category names reachable via breadth-first search within the
  configured depth and total-call budget, making exactly one sequential
  `categorymembers(cmtype=subcat)` request per category visited.
- **AC1.2 Failure:** If a subcategory fetch errors (network failure, or 429 after
  retries are exhausted), that branch is skipped (logged via `logger.warning`) and the
  walk continues with remaining siblings/budget rather than aborting the whole expansion.
- **AC1.3 Failure (budget exhaustion):** If the total-call budget is exhausted before
  `max_depth` is reached, expansion stops, a warning is logged noting partial coverage,
  and the partial set is still used (better than nothing) rather than treated as an error.

### 10-subcategory-aware-topic-filter.AC2: Cached expansion
- **AC2.1 Success:** Repeated `fetch_candidates()` calls within the cache TTL reuse the
  previously-expanded set for the same `(root, max_depth, max_total)` key, making zero
  new subcategory-listing calls.
- **AC2.2 Failure:** Changing the root category or the depth/total bounds is a different
  cache key — it does not reuse a stale expansion from different bounds.

### 10-subcategory-aware-topic-filter.AC3: Integrates with existing matching, unchanged interface
- **AC3.1 Success:** `category_filter()`'s set-intersection/exclude-precedence logic is
  unchanged; it's simply handed the expanded include/exclude sets instead of the literal
  configured names. Verified with a worked example: a stub biography nested several
  categories under "20th-century American politicians" is correctly matched.
- **AC3.2 Failure:** An article whose direct categories fall outside the expanded set
  (and outside the exclude set) is still correctly rejected — expansion must not
  introduce false positives.

### 10-subcategory-aware-topic-filter.AC4: Sequential-only
- **AC4.1 Success:** Subcategory-listing calls reuse the existing `_build_session()`-backed
  connection (retry/backoff, `Retry-After` respect) and run one at a time.
- **AC4.2 Failure:** N/A by construction — enforced by not introducing any
  `concurrent.futures`/threading import into this code path (tests assert this statically
  isn't reintroduced, consistent with the revert in `bae9507`).

### 10-subcategory-aware-topic-filter.AC5: Opt-in BLP relaxation, scoped to active topic filters
- **AC5.1 Success:** A new `guardrails.relax_blp_when_topic_filtered` flag (default
  `False`) — when `True` *and* an `include_categories` filter is currently active —
  skips the `is_blp` exclusion check in `_evaluate_candidate`.
- **AC5.2 Failure:** With no `include_categories` filter active, the flag has no effect
  even if set `True` — BLP exclusion cannot be silently disabled repo-wide by mistake;
  relaxation is only ever in effect alongside an explicit topic scope.
- **AC5.3 Failure:** Default behavior (flag unset) is bit-for-bit identical to today —
  BLP articles always excluded.

## Architecture

**Evidence from empirical investigation (2026-07-12):** walked one level into
`Category:Politics of the United States` (76 direct articles, 23 direct subcats) and
sampled its children: `Politics of the United States by state or territory` had only 1
direct article but **69 further subcategories**; `American political people` had 1 direct
article but 22 subcategories. Actual biography stub articles clearly live 3+ levels deep,
past these organizational hub categories — but fanout compounds fast enough (23 → up to 69
at the next level) that an unbounded walk could require hundreds of sequential calls. The
same investigation script got HTTP 429'd from Wikimedia after roughly 8 sequential calls
with only a 0.3s gap between them — confirming that even a "polite," fully-sequential
pattern has a low practical ceiling right now. This pushes the design toward a **small,
hard total-call budget** (not just a depth limit) and **long cache TTLs**, so this cost is
paid rarely rather than per-fetch.

**Expansion algorithm — bounded BFS, budget-first:** From each configured root category,
walk `cat_page.members(namespace=14)` (mwclient's existing subcategory listing, already
sequential-safe via `_build_session`) breadth-first: process all categories at depth *N*
before any at depth *N+1*, so that if the total-call budget runs out, coverage is complete
at shallow levels rather than a partial, effectively-random slice of a deep level. Defaults,
chosen from the evidence above:
- `subcategory_max_depth: 3` (covers the observed "hub → hub → leaf-with-articles" pattern)
- `subcategory_max_total: 100` (hard cap on total subcategory-listing calls per root,
  independent of depth — the binding constraint given how fast fanout multiplies)
- Cache TTL: `86400` seconds (24h) — category trees change rarely; this is deliberately
  much longer than `source_finder.py`'s 1-hour search cache, since re-paying dozens of
  sequential calls on every fetch (or even hourly) isn't viable given the rate-limit
  ceiling observed today.

**Cache:** in-memory `dict[(root, max_depth, max_total), (expires_at, set[str])]` on
`ArticlePicker`, following the exact shape of `source_finder.py`'s `_cached_search` (same
`time.monotonic()`-based TTL pattern) for consistency — no persistence needed, since this
is a session-scoped speed-up, not idempotency-critical data (unlike `SeenStore`).

**Matching:** `category_filter()` itself is untouched. `fetch_candidates()` (or a new
helper called from it) expands the configured include/exclude lists into their full
subcategory closures once (cache permitting), then passes the *expanded* sets into
`category_filter()` exactly as it passes the literal ones today.

**BLP relaxation:** a second, independent config flag on `GuardrailsConfig`
(`relax_blp_when_topic_filtered: bool = False`). Checked in `_evaluate_candidate` alongside
the existing `self.config.article_selection.exclude_blp` check: relaxation only takes
effect when an include-category filter is non-empty, so it can never silently weaken BLP
protection on an unfiltered (i.e. "scan everything") run. This is a deliberate, narrow
carve-out, not a general BLP toggle — documented as such in `config.yaml` with a comment
explaining the safety trade-off (WP:BLP is Wikipedia's strictest sourcing-policy area;
inaccurate/unsourced claims about living people carry real defamation/privacy risk).

## Existing Patterns
- `_build_session()` (`article_picker.py`, added in `bae9507`) — the retry/backoff
  `requests.Session` passed to `mwclient.Site(pool=...)`; subcategory-listing calls reuse
  this unchanged, no new session/adapter needed.
- `source_finder.py`'s `_cached_search` — the TTL-cache shape (`dict` keyed by a tuple,
  `time.monotonic()`-based expiry) this design's category-tree cache mirrors.
- `category_start_prefix` / `candidate_pool_size` / `max_wikitext_chars` in
  `ArticleSelectionConfig` — the precedent for adding new cost-guard config fields with an
  inline comment explaining what they bound.
- `is_protected()`'s "assume protected on error" and `get_categories()`'s "return `[]` on
  error" — the existing convention of degrading a single failed sub-fetch to a safe
  default rather than raising; AC1.2's per-branch skip-and-continue follows this.
- `logger.warning(...)` via `logging.getLogger(__name__)` (`article_picker.py`,
  `source_finder.py`) — the established visibility convention this design's partial-coverage
  and skipped-branch warnings use.

## Implementation Phases

### Phase 1: Bounded subcategory tree expansion
**Goal:** A pure-ish, testable BFS walker over `cat_page.members(namespace=14)`, bounded
by depth and total-call budget, sequential, degrading gracefully on a failed branch.
**Components:** `wiki_cite/article_picker.py` (new function, e.g. `_expand_category_tree`).
**Done when:** AC1.1, AC1.2, AC1.3.

### Phase 2: TTL cache for expansions
**Goal:** Cache expansion results per `(root, max_depth, max_total)` key, mirroring
`source_finder.py`'s `_cached_search` shape.
**Components:** `wiki_cite/article_picker.py` (`ArticlePicker.__init__`/new cache dict).
**Done when:** AC2.1, AC2.2.

### Phase 3: Wire expansion into matching
**Goal:** `fetch_candidates()` expands configured include/exclude lists before calling
the (unchanged) `category_filter()`.
**Components:** `wiki_cite/article_picker.py` (`fetch_candidates`).
**Done when:** AC3.1, AC3.2, AC4.1, AC4.2.

### Phase 4: Config
**Goal:** Add `subcategory_max_depth`, `subcategory_max_total` to
`ArticleSelectionConfig`; add `relax_blp_when_topic_filtered` to `GuardrailsConfig`, with
a `config.yaml` comment documenting the safety trade-off.
**Components:** `wiki_cite/config.py`, `config.yaml`.
**Done when:** AC5.2, AC5.3 (config plumbing only; behavior wiring is Phase 5).

### Phase 5: BLP relaxation wiring
**Goal:** `_evaluate_candidate` checks `relax_blp_when_topic_filtered` alongside
`exclude_blp`, scoped to only take effect when an include-category filter is active.
**Components:** `wiki_cite/article_picker.py` (`_evaluate_candidate`).
**Done when:** AC5.1, AC5.2, AC5.3.

### Phase 6: Tests, including the historical-politician worked example
**Goal:** Cover all ACs above; use `Category:20th-century American politicians` (or
`19th-century`) as the worked-example root per the confirmed test scope — deliberately
non-BLP-heavy, sidestepping the BLP question for the test data itself while still
exercising the (separately-flagged) relaxation logic directly.
**Components:** `tests/test_article_picker.py`.
**Done when:** All ACs above have direct test coverage.

## Glossary
- **Subcategory tree expansion:** resolving a configured root category into the full set
  of descendant category names (via bounded BFS), used as the effective include/exclude
  set for matching.
- **Total-call budget:** the hard cap (`subcategory_max_total`) on how many sequential
  `categorymembers` subcategory-listing calls one expansion may make, independent of how
  deep that ends up reaching — the binding cost constraint given observed fanout and
  rate-limit sensitivity.
- **BLP relaxation:** the opt-in, topic-filter-scoped carve-out from the always-on
  Biography-of-Living-Persons exclusion guardrail.
