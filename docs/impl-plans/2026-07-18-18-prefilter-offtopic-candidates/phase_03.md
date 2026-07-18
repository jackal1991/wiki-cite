# Phase 3: Reorder `_evaluate_candidate()` — topic filter before `page.text()`

**Goal:** Resolve categories (batch-read first, per-page fallback second) and run
`category_filter()` **before** `page.text()`, so an off-topic candidate is rejected with zero
per-page requests and an on-topic candidate skips the separate `page.categories()` call while
still fetching wikitext for the citation-needed/BLP checks.
**AC Coverage:** 18-prefilter-offtopic-candidates.AC2 (AC2.1, AC2.2), AC3 (AC3.1, AC3.2),
AC5 (AC5.1)

---

## Context

Current `_evaluate_candidate()` order (article_picker.py:421-476), verified:
1. `page.redirect` (line 431) — free (from batch `_info`).
2. `getattr(page, "namespace", 0) != 0` (line 435) — free.
3. `is_protected(page)` (line 439) — free (`page.protection` from batch `inprop=protection`).
4. **`page.text()` (line 444)** — 1 network round-trip.
5. `max_wikitext_chars` guard (line 453) — uses `page_text`.
6. **`get_categories(page)` → `page.categories()` (line 456 → 344)** — a 2nd round-trip.
7. `category_filter(categories, include, exclude)` (line 460) — the topic-filter decision.
8. BLP check (line 469, needs `page_text` + `categories`), then citation-needed (line 473).

So today an off-topic page pays for both `page.text()` **and** `page.categories()` before being
discarded at step 7. Under a narrow topic filter the loop iterates thousands of base-category
pages, each costing 2 wasted sequential requests.

`_evaluate_candidate()` returns `(ok, reason, page_text | None, categories | None)`;
`fetch_candidates()` (line 579) unpacks all four and passes `page_text`/`categories` straight
into `_build_candidate()` (line 584) so an accepted candidate is not re-fetched.
`_build_candidate()` uses `categories` for both `is_blp(...)` and `CandidateArticle.categories`
(lines 497-498) — so whatever categories this phase resolves must remain a correct, complete
list for accepted candidates (design DoD #7).

This phase changes only `_evaluate_candidate()`'s internal ordering and the categories source.
It must not touch the return-tuple shape, `fetch_candidates()`'s unpack/`_build_candidate` call,
the seen-skip, or the scorer/ranking.

## Implementation

### Reorder `_evaluate_candidate()`

**Files:**
- Modify: `wiki_cite/article_picker.py` — `_evaluate_candidate()` (lines 421-476).

**New order:** redirect → namespace → protection → **resolve categories → topic filter** →
`page.text()` → length guard → BLP → citation-needed. Concretely, replace the block from the
`page.text()` fetch (line ~442) through `category_filter` (line ~460) with:

```python
    # Resolve categories WITHOUT a per-page fetch when the batch carried them
    # (prop=categories, #18); fall back to a per-page page.categories() call only
    # when the batch data is absent/truncated/unusable.
    categories = self._batch_categories(page)
    if categories is None:
        logger.warning("Batch categories unavailable for %r; falling back to per-page fetch", getattr(page, "name", "?"))
        categories = self.get_categories(page)

    include = include_categories if include_categories is not None else self.config.article_selection.include_categories
    exclude = exclude_categories if exclude_categories is not None else self.config.article_selection.exclude_categories

    # Topic filter runs BEFORE any wikitext fetch: an off-topic reject costs zero
    # per-page requests (categories came from the batch), and even on-topic pages
    # skip the separate page.categories() call.
    ok, reason = self.category_filter(categories, include, exclude)
    if not ok:
        return False, reason, None, categories

    # On-topic: now fetch wikitext (unavoidable — needed for the BLP check, body
    # line count, and {{Citation needed}} extraction).
    try:
        page_text = page.text()
    except Exception as e:
        return False, f"error reading page: {e}", None, categories

    if not page_text:
        return False, "empty page", None, categories

    # Cost guard: don't spend a Claude call on a very long article.
    max_chars = self.config.article_selection.max_wikitext_chars
    if max_chars and len(page_text) > max_chars:
        return False, f"too long to analyze ({len(page_text)} chars)", page_text, categories
```

Then the existing BLP block (lines 464-470) and citation-needed block (472-474) follow
unchanged, and the final `return True, "", page_text, categories` is unchanged.

Important details:
- **Off-topic reject returns `page_text=None`** (it was never fetched) — matches the "None if
  rejected before fetching" contract in the method's own docstring (lines 427-429). Do not
  fetch text on the reject path.
- **`categories` is populated on the reject path** now (previously the pre-text rejects
  returned `None, None`). This is harmless — `fetch_candidates` discards the tuple on `is_ok
  == False` (line 580-581) — and it keeps the return shape honest. Do not change the
  redirect/namespace/protection early-returns above (those legitimately have no categories
  yet and stay `..., None, None`).
- **Fallback logging** uses the module `logger.warning(...)` (design "Existing Patterns"), so
  the rare slower path is visible without aborting. Include `page.name` for traceability.
- **No `include`/`exclude` when empty:** `category_filter([], [], [])` is already a no-op
  (line 388-399), so with no topic filter configured the new order still fetches text and
  proceeds exactly as before — the only observable change is categories now come from the
  batch instead of `page.categories()`. That satisfies AC5.1.
- **`max_wikitext_chars` moved after the filter:** previously the length guard ran before
  category resolution. Moving it after the topic filter is fine — an off-topic overlong page is
  rejected earlier now (a strict improvement), and an on-topic overlong page still returns
  `(False, "too long...", page_text, categories)` with the same `page_text` as before.

### Sequential-only invariant

No new requests, no parallelism (design DoD #6): the batch-read is a pure `_info` dict access,
and the only per-page call left on the accept path is `page.text()`. The `get_categories()`
fallback is the *same* single sequential call the code makes today, now reached only rarely.

## Tests

Covered in Phase 4 (AC2/AC3/AC5). At minimum, after this phase the existing
`test_fetch_candidates_*` tests that assert accept/reject behavior must still pass — but note
**they will need `page._info` set** (Phase 4 handles updating them), because bare-`Mock` pages
now flow through `_batch_categories()`. A bare `Mock` `_info` is caught by the helper's
`isinstance` guard (Phase 2) and falls back to `get_categories()`, which those tests already
stub via `page.categories = Mock(return_value=[...])` — so existing accept/reject tests keep
working through the fallback path even before Phase 4 adds explicit `_info`. Confirm this holds
when running the suite.

---

## Verification

Run: `uv run pytest tests/test_article_picker.py -q`
Also: `uv run ruff check wiki_cite/article_picker.py`
Expected: existing accept/reject tests pass (via the `_batch_categories` fallback into the
stubbed `page.categories`); no regressions in `fetch_candidates` ranking/seen tests.

## Commit

`refactor: run topic filter before page.text() in _evaluate_candidate (#18)`
