# Issue #9 — Summary stats dashboard + revert tracking

**Status:** In Review
**Complexity:** Complex
**GitHub:** https://github.com/jackal1991/wiki-cite/issues/9
**PR:** https://github.com/jackal1991/wiki-cite/pull/23

## Worktree

- branch: feat/9-stats-dashboard-revert-tracking
- path: .worktrees/9-stats-dashboard-revert-tracking
- created: 2026-07-08

## Summary
Add a dashboard view surfacing high-level operational stats — the number of
articles edited (pushed to Wikipedia), and the percentage of those edits later
reverted — building on the outcomes-feedback infrastructure from #5.

Two parts: (1) a mostly-cheap summary view over data we already record, and
(2) a genuinely new revert-tracking subsystem, which is the hard part.

## What already exists (do not re-litigate)
- `wiki_cite/seen_store.py`'s `outcomes` SQLite table already records one row
  per edit-level event (`proposed`, `skipped`, `approved`, `rejected`,
  `pushed`) with `article_title`, `revision_id`, categories, `edit_type`,
  `confidence`, `source_type`/`api`, etc.
- `dimension_rates()` already computes per-dimension success rates.
- `/stats` (`wiki_cite/web_app.py` `stats_page`,
  `wiki_cite/templates/stats.html`) already renders those per-dimension tables.
- Therefore "articles edited" (distinct `pushed` `article_title` count) and
  approve/reject rates are cheaply derivable from existing data — this part is
  largely a new **summary view**, not new data plumbing.

## What's missing — revert tracking (the hard part)
No revert-detection mechanism exists anywhere in the codebase:
- `wiki_cite/wikipedia_push.py`'s `push_edits()` calls `page.save(...)` via
  mwclient but **discards the returned new revision ID** — nothing is persisted
  to check against later.
- No `"reverted"` outcome type exists.
- No scheduled/background job of any kind exists in this repo.
- No code calls `page.revisions()` to inspect an article's history after a
  push. (`check_for_conflicts` reads only the single current `page.revision`
  for pre-push conflict detection — a different use.)

This needs new architecture:
1. Capture + store the post-push revision ID at push time.
2. A periodic mechanism to re-check each previously-pushed article's revision
   history for a revert. Wikipedia reverts don't happen instantly, so this
   **cannot** be a page-load-time check — it must run on a delay/schedule.
3. Feed a detected revert back into the `outcomes` table (new `"reverted"`
   outcome row) so the existing stats machinery picks it up automatically.

## Open design questions (flag, don't answer here)
- **Revert detection method:** revert-tag / edit-summary pattern match vs.
  content-hash comparison back to the pre-edit wikitext.
- **What triggers the periodic check:** a CLI command run via cron, a
  background thread in the Flask app, or a manual "refresh" button.
- **Retention / horizon:** how far back and for how long to keep re-checking a
  given pushed edit before giving up.

## Scope / touch points
- `wiki_cite/wikipedia_push.py` — capture + return the new revision id from the
  push.
- `wiki_cite/seen_store.py` — new column/table + `"reverted"` outcome type.
- `wiki_cite/web_app.py` + a new template — dashboard summary view (total
  articles edited, approve/reject/revert rates).
- `wiki_cite/cli.py` — possibly a new command to run the revert check.
- `tests/` — coverage for the new store logic, revert detection, and view.

## Notes
- Complex issue — needs a design doc under `docs/design-plans/` before
  implementation (per CLAUDE.md).
- Filed by supervisor agent. GitHub label application (`status/ready`) failed —
  same permissions error as #6/#7/#8 (`jgreaney-HCG` lacks
  `AddLabelsToLabelable` on this repo). No labels currently on this issue.
