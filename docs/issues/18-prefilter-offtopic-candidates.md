# Issue #18 — Pre-filter off-topic candidates before per-page fetches in fetch_candidates()

**Status:** In Progress
**Complexity:** Complex
**GitHub:** https://github.com/jackal1991/wiki-cite/issues/18

## Worktree

- branch: feat/18-prefilter-offtopic-candidates
- path: .worktrees/18-prefilter-offtopic-candidates
- created: 2026-07-14

## Summary
When a topic filter (`article_selection.include_categories`, from #10's subcategory
discovery) is active, `ArticlePicker.fetch_candidates()` scans the huge base tracking
category (`Category:All_articles_with_unsourced_statements`, ~580K members) and rejects the
large majority of pages for being off-topic — but only *after* paying for expensive per-page
Wikipedia round-trips on each one. Two ordering/query inefficiencies make every off-topic
reject cost 2 sequential API requests that carry no information we couldn't have gotten from
the batch we already made. Redesign the candidate-selection path so the topic filter is
applied from data already present in the batch generator query, before any per-page fetch.

## Evidence (verified against current code)
`_evaluate_candidate()` in `wiki_cite/article_picker.py` runs in this order:

1. `page.redirect`, namespace, `is_protected(page)` — all **free** (populated from the batch
   generator query; see below).
2. `page.text()` (line ~444) — **1 network round-trip** to fetch full wikitext.
3. `get_categories(page)` → `page.categories()` (line ~344) — **a second, separate
   `list=categories` round-trip per page**, because mwclient's `page.categories()` is a lazy
   property.
4. `category_filter(categories, include, exclude)` (line ~460) — the topic-filter decision,
   where most base-category pages get rejected when a narrow topic filter is active.

So an off-topic page — the majority case under a narrow topic filter — pays for **both**
`page.text()` and `page.categories()` before being discarded at step 4. The `fetch_candidates`
loop only stops once it collects `candidate_pool_size` (default 30) *accepted* candidates, so
under a narrow filter it can iterate through thousands of base-category pages, each costing 2
wasted sequential requests.

**Why the batch already carries what we need (confirmed in the installed mwclient):**
`for page in cat_page` drives mwclient's `Category`, a `GeneratorList`. `GeneratorList.__init__`
hardcodes onto the `generator=categorymembers` batch query (up to 500 pages at once):

```
self.args['prop']   = 'info|imageinfo'
self.args['inprop'] = 'protection'
```

That combined query is exactly why the protection check in step 1 costs no extra request —
protection rides the batch. MediaWiki's API supports adding `prop=categories&cllimit=max` to
that **same** combined generator query, returning each page's category membership as part of
the initial 500-page batch, for **zero** additional per-page requests. That lets
`category_filter()` run before *any* per-page network call.

## Two inefficiencies, ranked
1. **Ordering (cheap interim win):** `category_filter()` currently runs *after* `page.text()`.
   Fetching categories and filtering *before* `page.text()` already saves the wikitext fetch on
   every off-topic reject. Low-risk, but still costs one `page.categories()` request per reject.
2. **Batch piggyback (the real structural win):** add `prop=categories&cllimit=max` to the
   `generator=categorymembers` batch so categories arrive with the batch. An off-topic reject
   then costs **zero** extra per-page requests, and even on-topic pages skip the separate
   `categories()` call (they still need `page.text()` for the {{Citation needed}} / BLP / body
   checks, which is unavoidable and only paid on pages that survive the topic filter).

## Category-intersection API question (answered)
MediaWiki's core API has **no** query for the true intersection of two categories —
`generator=categorymembers` takes a single `gcmtitle`, so we cannot ask Wikipedia for
"citation-needed AND topic X" in one query. True intersection is only available via
third-party tooling (PetScan `petscan.wmflabs.org`, or the Quarry/SQL replicas). Those are
**not** recommended here: external dependency, uptime/rate-limit risk, and they are built for
offline/batch list generation, not a live per-fetch candidate loop. The batch `prop=categories`
piggyback is the in-core-API way to get most of the benefit.

## Constraints to design around (verified in-repo)
- **Sequential-only Wikipedia requests.** Commit `bae9507` reverted concurrent candidate
  fetches after they tripped Wikimedia's bot rate limiter; `_build_session()` adds a
  retry/backoff adapter as the reactive complement. The redesign must stay sequential — it
  *reduces* request count, it must not reintroduce parallelism.
- **Must not break `SeenStore` idempotent fetch or `CandidateScorer` ranking.**
  `fetch_candidates()` still yields the same pool of `CandidateArticle` objects into the same
  seen-skip (`seen_store.is_seen`) and feedback-ranking (`_build_scorer` / sort) path.
  Categories still need to populate `CandidateArticle.categories` for `CandidateScorer`.

## Open design questions (flag, don't answer here)
- How to inject `prop=categories&cllimit=max` into mwclient's generator batch, which hardcodes
  `prop=info|imageinfo`. Options: subclass/customize the `GeneratorList`/`Category` query, or
  issue a custom `generator=categorymembers` query via `site.get()`/`site.api()` and adapt the
  results, while preserving the `info|imageinfo|protection` data the current code relies on.
- **`clcontinue` pagination:** a page with more categories than `cllimit` returns them across
  continuation pages. Decide how to handle pages whose category list is truncated in the batch
  (rare for articles, but the topic-filter decision must not be made on a partial category set
  — e.g. fall back to a per-page `categories()` only for those).
- Interaction with `category_start_prefix` (`gcmstartsortkeyprefix`) already set on the batch.
- Whether to keep inefficiency #1's simple reorder as an interim step or go straight to #2.

## Scope / touch points
- `wiki_cite/article_picker.py` — `_evaluate_candidate()` (ordering), `get_categories()` /
  the `page.categories()` call, and the `fetch_candidates()` batch-iteration loop.
- `tests/test_article_picker.py` — coverage for the new ordering / batch-category path, the
  clcontinue/truncation fallback, and a guard that off-topic candidates incur no `page.text()`.
- Possibly `config.yaml` if any new guard (e.g. category-truncation fallback toggle) is added.

## Notes
- Complex issue — needs a design doc under `docs/design-plans/` before implementation
  (per CLAUDE.md). Do not begin implementation without the design doc.
- Builds on #10 (subcategory-aware topic filter) and #6 (topic filter); only matters when a
  topic filter is active. Feeds the same `CandidateScorer` from #5 (outcomes feedback).
