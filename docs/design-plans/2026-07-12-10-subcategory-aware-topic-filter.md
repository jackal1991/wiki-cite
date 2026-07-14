# Subcategory-Aware Topic Filter Design

## Summary
`ArticlePicker`'s `include_categories`/`exclude_categories` topic filter (from #6) only
matches an article's *direct* MediaWiki categories. Wikipedia organizes broad topics into
deep subcategory trees with very few direct article members at the top, so the filter is
unusable for anything but a narrow, already-leaf-level category.

Revised from the first draft of this design: rather than expanding a topic into its
subcategory closure *at runtime* (bounded BFS + TTL cache inside `fetch_candidates`), this
version treats discovery as an **offline CLI command**, since Wikipedia's category
structure is stable and re-discovering it live on every fetch (or even hourly) buys
nothing. `wiki-cite discover-categories <root>` crawls the tree once (sequentially — still
Wikipedia's own etiquette rules, offline or not), fans out *relevance judgment* of the
discovered category names across parallel Claude calls (no MediaWiki request concerns
there — that traffic goes to Anthropic, not Wikipedia), and writes a static, versioned
category list. The runtime path (`fetch_candidates`) just reads that static list — no
live subcategory-tree walking, no request budget, no cache-TTL tradeoffs in the hot path
at all.

## Definition of Done
1. A new `wiki-cite discover-categories <root>` CLI command crawls the subcategory tree
   under a root category, sequentially and cycle-safely, and classifies each discovered
   category as content-relevant vs. Wikipedia-maintenance/organizational using parallel
   Claude calls.
2. The command writes a static, human-readable output (a generated file, e.g.
   `data/category_expansions/<root-slug>.json`) listing the accepted category names —
   checked into the repo like any other generated-but-versioned artifact, re-run manually
   when a topic's category structure is believed to have changed.
3. `ArticlePicker`/`fetch_candidates` reads that static list as the effective
   include/exclude set — `category_filter()` itself is unchanged (still a plain
   set-intersection check); there is no live Wikipedia subcategory call anywhere in the
   request-serving path.
4. A new, explicit, opt-in guardrail flag lets a scoped topic filter relax the always-on
   BLP exclusion — unchanged from the first draft, independent of the discovery mechanism.
5. Tests for: cycle-safe crawling, the relevance-classification prompt/parsing, the
   generated-file format and how the runtime side loads it, and the BLP-relaxation flag's
   scoping.

**Out of scope:** picking today's actual "US politics" filter value (a follow-up, run via
this new CLI once it ships), automatic/scheduled re-discovery (manual re-run only, given
how rarely category trees change), and turning BLP relaxation on by default.

## Acceptance Criteria

### 10-subcategory-aware-topic-filter.AC1: Cycle-safe sequential subcategory crawl
- **AC1.1 Success:** `discover-categories <root>` walks `cat_page.members(namespace=14)`
  breadth-first from the root, one sequential request per category (reusing
  `_build_session()`), and terminates even when the category graph contains a cycle
  (tracked via a `visited: set[str]`, since MediaWiki categories are a DAG/graph, not
  strictly a tree — a subcategory can, rarely, loop back to an ancestor).
- **AC1.2 Failure:** A failed fetch for one subcategory (network error, or 429 after
  retries exhaust) is logged (`logger.warning`) and that branch is skipped; the crawl
  continues and still produces a (partial) result rather than aborting outright.
- **AC1.3 Failure (already visited):** A category reachable via two different parent
  paths is only fetched/classified once, not double-counted or re-crawled.

### 10-subcategory-aware-topic-filter.AC2: Parallel relevance classification
- **AC2.1 Success:** Discovered category names are classified concurrently via the
  Anthropic API (batched, multiple in-flight calls — no MediaWiki traffic involved, so no
  etiquette constraint applies here) into content-relevant vs. maintenance/organizational
  (e.g. "American politics task force", "...articles by quality", "...participants" are
  excluded; "...stubs" and biographical/topical subcats are kept, since this tool
  specifically targets stub articles).
- **AC2.2 Failure:** A classification call failing (API error, malformed response) defaults
  that single category to *excluded* (fail closed — an uncategorized name doesn't silently
  widen the filter) and is logged, without aborting the rest of the batch.

### 10-subcategory-aware-topic-filter.AC3: Static, versioned output
- **AC3.1 Success:** The command writes a deterministic, sorted, deduplicated list of
  accepted category names to `data/category_expansions/<root-slug>.json`, including the
  root category itself, a discovery timestamp, and the root/crawl parameters used —
  enough to tell a stale file from a fresh one at a glance.
- **AC3.2 Failure:** Re-running the command for the same root overwrites the file
  deterministically (same inputs → same output set, modulo the timestamp) rather than
  appending or merging with a possibly-stale previous run.

### 10-subcategory-aware-topic-filter.AC4: Runtime reads the static list, no live crawling
- **AC4.1 Success:** `fetch_candidates()` (or `category_filter()`'s caller) loads the
  expanded set from `data/category_expansions/<root-slug>.json` when the configured
  include/exclude category matches a discovered root, and uses it exactly as
  `category_filter()` already consumes `include_categories`/`exclude_categories` today —
  no code change to `category_filter()` itself.
- **AC4.2 Failure:** A configured include category with no corresponding discovery file
  falls back to direct-match-only (today's behavior) rather than erroring — discovery is
  opt-in per topic, not a hard requirement.

### 10-subcategory-aware-topic-filter.AC5: Opt-in BLP relaxation, scoped to active topic filters
- **AC5.1 Success:** A new `guardrails.relax_blp_when_topic_filtered` flag (default
  `False`) — when `True` *and* an `include_categories` filter is currently active — skips
  the `is_blp` exclusion check in `_evaluate_candidate`.
- **AC5.2 Failure:** With no `include_categories` filter active, the flag has no effect
  even if set `True` — BLP exclusion cannot be silently disabled repo-wide by mistake.
- **AC5.3 Failure:** Default behavior (flag unset) is bit-for-bit identical to today — BLP
  articles always excluded.

## Architecture

**Evidence from empirical investigation (2026-07-12):** walking one level into
`Category:Politics of the United States` (76 direct articles, 23 direct subcats) showed
`Politics of the United States by state or territory` with only 1 direct article but 69
further subcategories, and `American political people` with 1 direct article but 22
subcategories — actual biography stub articles live several levels past these
organizational hub categories, and fanout compounds quickly. The same investigation script
got HTTP 429'd after ~8 sequential calls with only a 0.3s gap, confirming Wikipedia's
practical rate ceiling is low even for "polite" sequential traffic right now. This is
exactly why doing this at request-serving time was the wrong call in the first draft: a
live "Fetch new article" click has no business paying a cost this variable and this
sensitive to Wikipedia's mood. Moving it to a deliberately-run, patient, offline CLI command
sidesteps the tension between "user is waiting" and "must go slow to be polite" entirely —
the human running `discover-categories` can wait as long as it takes.

**Two separate traffic types, two separate constraints:**
- **Wikipedia (category-listing) calls** — must stay sequential, reuse `_build_session()`'s
  retry/backoff, same as every other Wikipedia call in this codebase. Cost here is now a
  correctness/hygiene concern (cycle-safety, not re-fetching an already-visited category)
  rather than a live-latency concern, since nothing is waiting on it in real time.
- **Anthropic (relevance-classification) calls** — a completely different service with its
  own, much more permissive rate limits; parallelizing these has no bearing on Wikipedia
  etiquette at all. Batch discovered category names (e.g. 20 per call) and fire multiple
  batches concurrently (`concurrent.futures.ThreadPoolExecutor` or async, matching whatever
  pattern `agent.py`'s existing `Anthropic` client usage favors) to keep the CLI command's
  wall-clock time reasonable despite the crawl needing to go slow.

**Crawl → classify → write, as three clearly separated stages** (not interleaved), so each
is independently testable:
1. **Crawl:** BFS from root via `cat_page.members(namespace=14)`, sequential, visited-set
   for cycle safety. Produces a raw list of discovered category names (no judgment yet).
2. **Classify:** batch the raw names, dispatch concurrent Anthropic calls asking "is this
   category likely to contain actual [topic] biography/topic articles, or is it Wikipedia
   internal bookkeeping (maintenance, quality, task-force, importance, participant
   categories)?" — fail-closed (exclude) on any classification error.
3. **Write:** merge accepted names (plus the root itself) into
   `data/category_expansions/<root-slug>.json`.

**Runtime side stays trivial:** `fetch_candidates` (or a small loader) reads the JSON file
for a configured root, if present, and hands the flat name list to the existing,
unchanged `category_filter()`. No crawling, no budget, no TTL cache — the "cache" is just
the file on disk, invalidated only by manually re-running the CLI command.

**BLP relaxation:** unchanged from the first draft — a second, independent config flag on
`GuardrailsConfig` (`relax_blp_when_topic_filtered: bool = False`), checked in
`_evaluate_candidate` alongside `exclude_blp`, scoped to only take effect when an
include-category filter is active. Documented in `config.yaml` as a deliberate,
narrow carve-out given WP:BLP is Wikipedia's strictest sourcing-policy area.

## Existing Patterns
- `_build_session()` (`article_picker.py`) — reused unchanged for the crawl stage's
  Wikipedia calls.
- `ClaudeAgent`'s `Anthropic` client usage (`agent.py`) — the precedent for how this
  codebase already talks to the Anthropic API; the classification stage's client setup
  should mirror it rather than introduce a second, different pattern.
- `cli.py`'s `subparsers.add_parser(...)` + `cmd_<name>(args)` + `set_defaults(func=...)`
  — `discover-categories` follows this exactly, alongside existing commands like `fetch`
  and `stats`.
- `logger.warning(...)` via `logging.getLogger(__name__)` — the established visibility
  convention this design's skipped-branch and classification-failure warnings use.
- `is_protected()`'s "assume protected on error" / `get_categories()`'s "return `[]` on
  error" — the existing convention of degrading a single failed sub-fetch to a safe
  default; AC1.2 and AC2.2 both follow this fail-closed shape.
- `config.yaml`'s existing cost-guard-with-inline-comment style (`max_wikitext_chars`,
  `category_start_prefix`) — the BLP-relaxation flag's comment follows this.

## Implementation Phases

### Phase 1: Sequential, cycle-safe subcategory crawler
**Goal:** A pure-ish, testable BFS walker over `cat_page.members(namespace=14)`,
visited-set for cycle safety, degrading gracefully on a failed branch, producing a raw
discovered-category-name list.
**Components:** `wiki_cite/article_picker.py` (new function, e.g. `crawl_subcategories`).
**Done when:** AC1.1, AC1.2, AC1.3.

### Phase 2: Relevance classification via parallel Anthropic calls
**Goal:** Batch raw category names, classify concurrently (content-relevant vs.
maintenance/organizational) via the Anthropic API, fail-closed on error.
**Components:** new module, e.g. `wiki_cite/category_discovery.py` (keeps this
Anthropic-facing logic out of `article_picker.py`, which stays Wikipedia-facing).
**Done when:** AC2.1, AC2.2.

### Phase 3: `discover-categories` CLI command + static output
**Goal:** Wire crawl → classify → write into a runnable command; deterministic,
versioned JSON output.
**Components:** `wiki_cite/cli.py`, `data/category_expansions/` (new directory).
**Done when:** AC3.1, AC3.2.

### Phase 4: Runtime loader
**Goal:** `fetch_candidates()` loads a discovered-category JSON file when present for the
configured root, falling back to direct-match-only when absent.
**Components:** `wiki_cite/article_picker.py` (`fetch_candidates`).
**Done when:** AC4.1, AC4.2.

### Phase 5: BLP relaxation wiring
**Goal:** `_evaluate_candidate` checks `relax_blp_when_topic_filtered` alongside
`exclude_blp`, scoped to only take effect when an include-category filter is active.
**Components:** `wiki_cite/config.py`, `config.yaml`, `wiki_cite/article_picker.py`.
**Done when:** AC5.1, AC5.2, AC5.3.

### Phase 6: Tests, including the historical-politician worked example
**Goal:** Cover all ACs above; use `Category:20th-century American politicians` (or
`19th-century`) as the worked-example root — deliberately non-BLP-heavy, sidestepping the
BLP question for the test data itself while still exercising the (separately-flagged)
relaxation logic directly.
**Components:** `tests/test_article_picker.py`, `tests/test_category_discovery.py` (new).
**Done when:** All ACs above have direct test coverage.

## Glossary
- **Discovery:** the offline `discover-categories` CLI run that crawls a topic's
  subcategory tree and produces a static, accepted-category-names file.
- **Crawl:** the sequential, cycle-safe Wikipedia-facing stage of discovery (raw names,
  no judgment yet).
- **Classification:** the parallel, Anthropic-facing stage of discovery that judges which
  raw category names are content-relevant vs. Wikipedia-internal bookkeeping.
- **Category expansion file:** the static JSON artifact discovery produces, which the
  runtime path reads instead of crawling live.
- **BLP relaxation:** the opt-in, topic-filter-scoped carve-out from the always-on
  Biography-of-Living-Persons exclusion guardrail (unchanged from the first draft).
