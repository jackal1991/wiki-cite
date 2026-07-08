# Issue #6 — Allow filtering candidate articles by topic/category

**Status:** In Progress
**Complexity:** Complex
**GitHub:** https://github.com/jackal1991/wiki-cite/issues/6

## Worktree
- branch: feat/6-topic-category-filter
- path: .worktrees/6-topic-category-filter
- created: 2026-07-08

## Design
Design plan: `docs/design-plans/2026-07-08-6-topic-category-filter.md`
(currently on branch `feat/6-topic-category-filter`; not yet merged to `main`).
Chosen scope: config.yaml defaults + a runtime, session-scoped dashboard
override (not persisted back to config.yaml) with category search/select
(via `site.allpages`, Category namespace) rather than free-text entry.

## Summary
`ArticlePicker` (`wiki_cite/article_picker.py`) only pulls candidates from a single
hardcoded Wikipedia category (`article_selection.category` in `config.yaml`, currently
`Category:All_articles_with_unsourced_statements`). It already fetches each article's
full category list via `get_categories()`, but that list is only used to detect BLP
status (`is_blp`) — there is no way to scope article selection to a topic or set of
categories (e.g. "only science articles" or "exclude sports").

We want to be able to filter/select which topics or categories the agent pulls
candidate articles from.

## Motivation
Today every fetch draws from the same firehose category with no topical control.
Operators can't focus the agent on subject areas they're comfortable reviewing (or
steer it away from areas they aren't). Category information is already being fetched
per article, so the gating data is available at effectively no extra cost.

## Scope / touch points
- **`config.yaml` / config schema (`wiki_cite/config.py`)** — add an include/exclude
  topic-or-category filter to `article_selection` (e.g. `include_categories` /
  `exclude_categories`).
- **`wiki_cite/article_picker.py`** — apply the filter during `is_candidate` /
  `fetch_candidates` using the already-fetched category list. Decide whether the
  primary source category stays fixed and filters are applied as a post-filter, or
  whether the source category itself becomes selectable.
- **`wiki_cite/models.py`** — possibly, if the filter config needs a structured model.
- **Web dashboard + API (`wiki_cite/web_app.py`, `templates/`)** — optional, if we
  want this configurable at runtime rather than only via `config.yaml`.
- **Tests (`tests/test_article_picker.py`, `tests/test_config.py`)** — cover
  include/exclude filtering and config parsing.

## Open questions
- Config-only (`config.yaml`) for a first cut, or runtime-configurable via the
  dashboard from the start?
- Match on exact category names, substring/topic keywords, or both?
- Include-list, exclude-list, or both? Interaction with the existing BLP/protected
  exclusions.

## Notes
- Filed by supervisor agent; GitHub label application (`status/ready`) did not take
  (issue #6 had no labels when design phase began) — corrected to `status/in-progress`
  as part of starting design.
