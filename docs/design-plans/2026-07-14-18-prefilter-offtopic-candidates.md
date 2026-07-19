# Pre-filter Off-topic Candidates Design

## Summary
When a topic filter (`article_selection.include_categories`, from #10) is active,
`ArticlePicker.fetch_candidates()` walks the huge base tracking category
(`Category:All_articles_with_unsourced_statements`, ~580K members) and rejects most
candidates as off-topic — but only after paying for a full wikitext fetch (`page.text()`)
and a separate `page.categories()` request per candidate. Both are avoidable: the batch
`generator=categorymembers` query mwclient already issues can be extended with
`prop=categories&cllimit=max` so category data for all ~500 candidates in a batch arrives
for free, letting the topic filter run before any per-page network call at all.

## Definition of Done
1. The topic-filter check runs from category data already in the batch
   `generator=categorymembers` query (`prop=categories&cllimit=max` merged into
   `cat_page.args`, the same mutation pattern already used for `gcmstartsortkeyprefix`) —
   zero extra per-page requests for the common case.
2. Off-topic candidates are rejected without ever calling `page.text()` — this subsumes
   the simpler "just reorder the checks" fix, since category data is available before any
   per-page network call.
3. On-topic candidates still need `page.text()` (unavoidable — citation-needed/BLP/body
   checks require wikitext) but skip the separate `page.categories()` call, reusing the
   piggybacked batch data instead.
4. Graceful fallback: if a page's `categories` field is absent, malformed, or truncated in
   the batch response, fall back to the existing per-page `page.categories()` call for just
   that page rather than deciding accept/reject on incomplete data.
5. No behavior change when no topic filter is configured (`include`/`exclude` both empty).
6. Sequential-only Wikipedia requests preserved throughout — no new parallelism.
7. `SeenStore` idempotent-fetch and `CandidateScorer` feedback-ranking integration
   unchanged; `CandidateArticle.categories` is still populated correctly either way.
8. Tests demonstrate: an off-topic reject makes zero requests beyond the batch listing, an
   on-topic pass gets correct categories with no separate `categories()` call, and the
   fallback path activates correctly on malformed/truncated per-page category data.

**Out of scope:** true category-intersection via a third-party tool (PetScan/Quarry) —
confirmed not available in MediaWiki's core API and not recommended for a live per-fetch
path (external dependency, uptime/rate-limit risk, built for offline batch list
generation, not this).

## Acceptance Criteria

### 18-prefilter-offtopic-candidates.AC1: Batch query carries category data
- **AC1.1 Success:** `fetch_candidates()` merges `prop=categories` (added to the existing
  `info|imageinfo` value) and `cllimit=max` into `cat_page.args` before iterating, using
  the same `cat_page.args["..."] = ...` mutation pattern already used for
  `gcmstartsortkeyprefix` — verified via a test asserting these keys end up in
  `cat_page.args` after calling `fetch_candidates()`, mirroring
  `test_fetch_candidates_sets_start_sortkey_prefix`.
- **AC1.2 Failure:** A `cat_page` without a mutable `.args` (e.g. a bare list in test
  doubles, per the existing `hasattr(cat_page, "args")` guard) is left untouched rather
  than raising — matches the existing `category_start_prefix` guard's behavior exactly.

### 18-prefilter-offtopic-candidates.AC2: Topic filter runs before any per-page fetch
- **AC2.1 Success:** For a candidate whose batch-provided `categories` don't match an
  active include filter, `_evaluate_candidate()` rejects it *before* calling `page.text()`
  — verified by asserting `page.text` was never called (a `Mock()` page's `.text` call
  count is 0) on a rejected off-topic candidate.
- **AC2.2 Failure:** A candidate that *does* match the topic filter (or when no topic
  filter is configured at all) proceeds to `page.text()` exactly as today — verified the
  accept path is unaffected by this change.

### 18-prefilter-offtopic-candidates.AC3: On-topic candidates reuse batch category data
- **AC3.1 Success:** For an accepted candidate, `Page.categories()` (the separate
  per-page `list=categories` API call) is never invoked — the categories used for
  `CandidateArticle.categories` come from the batch-provided data, verified by asserting
  a mocked `page.categories` method's call count is 0 on an accepted candidate.
- **AC3.2 Failure:** The resulting `CandidateArticle.categories` list is
  content-identical (same category names, `Category:` prefix stripped, same order-independent
  set) to what the old `get_categories(page)` path would have produced from a
  `page.categories()` call — verified with a fixture where both paths are computed and
  compared.

### 18-prefilter-offtopic-candidates.AC4: Graceful fallback on incomplete batch data
- **AC4.1 Success:** If a page's batch `_info` dict has no `"categories"` key, or the
  response includes a `clcontinue` marker for that page (indicating a truncated category
  list — only realistic for a page with 500+ categories, given `cllimit=max`), the code
  falls back to the existing `get_categories(page)` (a per-page `page.categories()` call)
  for that one page only, rather than filtering on incomplete data.
- **AC4.2 Failure:** This fallback must not silently reject a page that would have passed
  with the full category list, nor silently accept a page that would have been rejected —
  verified with a fixture simulating a truncated/missing categories field where the
  fallback's result matches what the pre-#18 `get_categories()`-only path would have
  produced.

### 18-prefilter-offtopic-candidates.AC5: No-topic-filter behavior unchanged
- **AC5.1 Success:** With both `include_categories` and `exclude_categories` empty (the
  default), `fetch_candidates()`'s output and request pattern are unchanged from before
  this design — `category_filter([], [], [])`'s existing no-op behavior is preserved, and
  the batch `prop=categories` addition, while still requested, imposes no new behavior
  since there's nothing to filter against.

## Architecture

**One dominant approach** — investigation ruled out the alternative (hand-rolling a raw
`site.get()`/`site.api()` query loop bypassing mwclient's `Category`/`GeneratorList`
entirely) as unnecessary complexity: mwclient's continuation handling is generic
(`self.args.update(data['continue'])` in `GeneratorList`'s `load_chunk`), so it already
transparently merges whatever continuation keys the API returns — including a
hypothetical `clcontinue` — without needing custom pagination logic. The existing
`cat_page.args["gcmstartsortkeyprefix"] = start_prefix` mutation (added for
`category_start_prefix`) is direct proof this extension point works and is already relied
upon in this codebase.

**Query change:** in `fetch_candidates()`, alongside the existing
`gcmstartsortkeyprefix` mutation, add:
```python
cat_page.args["prop"] = "info|imageinfo|categories"
cat_page.args["cllimit"] = "max"
```
(`GeneratorList.__init__` sets `self.args['prop'] = 'info|imageinfo'` by default —
overwriting with the superset string is fine since `prop` is a single pipe-delimited
value, not a list mwclient merges automatically.)

**Reading the piggybacked data:** `mwclient.page.Page.categories()` always issues a fresh
`PagePropertyGenerator` query regardless of what's already in `page._info` (confirmed by
reading `page.py` directly) — it does not opportunistically use pre-fetched data. So a new
helper reads the raw batch data directly:

```python
def _batch_categories(page) -> list[str] | None:
    """Category names already present in the batch generator response for `page`
    (added via prop=categories on the cat_page query), or None if absent/unusable —
    signals the caller to fall back to a per-page page.categories() call."""
    info = getattr(page, "_info", None) or {}
    raw = info.get("categories")
    if raw is None:
        return None
    return [c["title"].replace("Category:", "") for c in raw if "title" in c]
```

Live-verified response shape: `{"categories": [{"ns": 14, "title": "Category:X"}, ...]}`
per page, confirmed against the real API with `prop=categories&cllimit=max`. No
`clcontinue` appeared even for a 12-category test page — with `cllimit=max` (the API max,
500), truncation is only realistic for pages with 500+ categories, an edge case real
articles essentially never hit, but AC4's fallback still covers it defensively (checking
for a `clcontinue` key alongside the missing-categories case) rather than assuming it
can't happen.

**Reordered `_evaluate_candidate()`:**
1. Redirect / namespace / protection checks — unchanged, still free (protection already
   rides the batch via `inprop=protection`).
2. **New**: resolve categories via `_batch_categories(page)`, falling back to
   `get_categories(page)` (today's per-page call) only if `_batch_categories` returns
   `None`.
3. **New position**: run `category_filter(categories, include, exclude)` here — before
   `page.text()`.
4. Only on topic-filter pass: `page.text()` (unavoidable — needed for the BLP check, body
   line count, and `{{Citation needed}}` extraction), then the existing BLP/citation-needed
   checks unchanged.

This inverts the current order (text → categories → filter) to (categories → filter →
text), and replaces the categories step's per-page network call with a free batch read in
the common case.

**`get_categories(page)` stays** as the fallback path (AC4) and continues to be used
as-is by any other caller (e.g. `is_candidate()`'s direct/non-batch usage) — this design
only changes `fetch_candidates()`'s internal flow, not `get_categories()`'s public
behavior or signature.

## Existing Patterns
- `cat_page.args["gcmstartsortkeyprefix"] = start_prefix` (`fetch_candidates()`,
  `category_start_prefix` feature) — the established, working precedent for mutating the
  `Category`/`GeneratorList` query args before iteration; this design's `prop`/`cllimit`
  additions follow it exactly, including the same `hasattr(cat_page, "args")` guard for
  test-double safety.
- `_expand_categories()`'s fail-open pattern (`load_expansion(name)` returning `None` →
  keep the name as-is) — the same "returns `None` to signal fallback" shape this design's
  `_batch_categories()` helper uses.
- `is_protected()`'s "assume protected on error" / `get_categories()`'s "return `[]` on
  error" — the established convention of degrading a single failed sub-fetch to a safe
  default rather than raising; AC4's fallback follows this same shape.
- `logger.warning(...)` via `logging.getLogger(__name__)` — used when the fallback path
  triggers, so silent-but-slower degradation is still visible in logs.

## Implementation Phases

### Phase 1: Batch query extension
**Goal:** Add `prop=categories&cllimit=max` to `cat_page.args` in `fetch_candidates()`,
following the existing `gcmstartsortkeyprefix` mutation pattern.
**Components:** `wiki_cite/article_picker.py` (`fetch_candidates`).
**Done when:** AC1.1, AC1.2.

### Phase 2: Batch-category read helper with fallback signal
**Goal:** Add `_batch_categories(page)` that reads `page._info['categories']` when
present and well-formed, returning `None` (fallback signal) when absent/truncated.
**Components:** `wiki_cite/article_picker.py` (new helper, near `get_categories`).
**Done when:** AC4.1, AC4.2 (helper-level correctness; wiring into the evaluation flow is Phase 3).

### Phase 3: Reorder `_evaluate_candidate()`
**Goal:** Resolve categories (batch-first, fallback second) and run `category_filter()`
before `page.text()`; only fetch wikitext for candidates that pass the topic filter.
**Components:** `wiki_cite/article_picker.py` (`_evaluate_candidate`).
**Done when:** AC2.1, AC2.2, AC3.1, AC3.2, AC5.1.

### Phase 4: Tests across all ACs
**Goal:** Direct test coverage for every AC — batch-args mutation, zero-`page.text()`-calls
on off-topic reject, zero-`page.categories()`-calls on accept, the fallback path, and
no-filter-configured parity.
**Components:** `tests/test_article_picker.py`.
**Done when:** All ACs above have direct test coverage.

## Glossary
- **Batch query:** the single `generator=categorymembers` API request (up to 500 pages)
  that `for page in cat_page:` issues per page of results — already the source of
  `info|imageinfo|protection` data for each candidate; this design adds `categories` to it.
- **Piggybacked category data:** category names read directly from a page's raw batch
  response (`page._info['categories']`), as opposed to a separate per-page
  `page.categories()` API call.
- **Fallback path:** the per-page `get_categories(page)` call, retained for the rare case
  where a page's category list wasn't fully present in the batch response (missing key or
  `clcontinue`-truncated).
