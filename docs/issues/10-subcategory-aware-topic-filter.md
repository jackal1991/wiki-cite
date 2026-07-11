# Issue #10 — Recursive/subcategory-aware category matching for article-selection topic filter

**Status:** In Progress
**Complexity:** Complex
**GitHub:** https://github.com/jackal1991/wiki-cite/issues/10

## Worktree

- branch: feat/10-subcategory-aware-topic-filter
- path: .worktrees/10-subcategory-aware-topic-filter
- created: 2026-07-11

## Summary
The topic filter added in #6 (`article_selection.include_categories`/`exclude_categories`)
only matches an article's **direct** MediaWiki categories — no subcategory traversal.
This makes it unusable for any broad topic: Wikipedia organizes broad subjects into deep
subcategory trees, so the top-level category itself has very few direct article members.

## Evidence
Tried setting `include_categories: ["Politics of the United States"]` to scope the agent to
US politics stubs. Checked via the MediaWiki API:
- `Category:Politics of the United States` — 76 direct member pages (mostly a hub with 23
  subcategories, e.g. "American politicians", "United States federal legislation", ...)
- `Category:All articles with unsourced statements` (the base tracking category) — 580,379 pages

Intersecting a 76-member set against a 580K-member set, scanned sequentially (per
mediawiki.org/wiki/API:Etiquette — no parallel requests), never surfaced a candidate in a
reasonable time. Checked several other plausible "US politics" categories (American
politicians: 37, 21st-century American politicians: 423, American politician stubs: 62,
United States senators: 21, Mayors of places in the United States: 17) — all too sparse
for a direct-membership-only match against the base category.

## What already exists (do not re-litigate)
- `ArticlePicker.category_filter()` / `_normalize_category()` (`wiki_cite/article_picker.py`)
  — the include/exclude overlap check, currently direct-category-only.
- `article_selection.include_categories`/`exclude_categories` in `config.py`/`config.yaml`,
  plus the runtime override in `web_app.py` (`category_overrides`, `/api/settings/categories`
  GET/POST) and the dashboard's category search-and-select
  (`/api/categories/search`, backed by `site.allpages` on the Category namespace).
- The sequential-only Wikipedia-fetch constraint from `bae9507` — any subcategory-tree walk
  must respect this (no parallel requests to Wikipedia).

## What's needed
`ArticlePicker`'s category filter needs to walk the subcategory tree under a configured
root category (bounded depth/fanout) and match an article against the full expanded set,
not just the literal category name(s) configured.

## Constraints to design around
- **Request budget**: every extra subcategory is at least one more MediaWiki API call
  (`list=categorymembers` on the subcategory), made **sequentially**. A broad topic could
  easily expand into hundreds of subcategories; needs a cost guard (depth limit, fanout cap,
  or category-size cutoff) so a single "topic filter" doesn't turn into a request storm.
- **Caching**: the expanded category-tree membership shouldn't be re-walked on every fetch;
  needs some kind of cache/TTL given category trees change rarely.
- Should reuse the existing `include_categories`/`exclude_categories` override + the live
  `/api/settings/categories` endpoint rather than introduce a parallel mechanism.

## Open design questions (flag, don't answer here)
- Whether `guardrails.skip_blp_articles` (currently always-on) should be relaxable for a
  scoped topic filter — most living US politicians are BLP, which combined with a topic
  filter could shrink the eligible pool further. Raised but not decided; BLP is a
  safety-relevant Wikipedia policy area, not just a convenience filter, so this needs
  explicit design discussion, not a quiet toggle.
- Whether including 19th/20th-century politician categories (mostly non-BLP; checked sizes:
  19th-century American politicians = 711, 20th-century American politicians = 726) is a
  better initial scope than a living-persons-heavy topic, sidestepping the BLP question
  rather than answering it.
- How deep/wide the subcategory walk should go by default, and whether that should be
  user-configurable per topic or a fixed global bound.

## Scope / touch points
- `wiki_cite/article_picker.py` — `category_filter`, `_normalize_category`, and the
  `fetch_candidates` construction of the effective include/exclude sets.
- `wiki_cite/config.py`/`config.yaml` — any new cost-guard config (depth/fanout limits, cache TTL).
- `wiki_cite/web_app.py` — `/api/settings/categories`, `/api/categories/search` if the
  subcategory expansion needs to be surfaced/previewed in the dashboard.
- `tests/` — coverage for tree-walk bounds, caching, and BLP/century-scope interaction
  (whichever direction the design lands on).

## Notes
- Complex issue — needs a design doc under `docs/design-plans/` before implementation
  (per CLAUDE.md).
- Builds directly on #6 (topic/category filter, still open) and interacts with #5's
  outcomes-feedback ranking (candidates now matched via a subcategory-expanded set feed
  into the same `CandidateScorer`).
