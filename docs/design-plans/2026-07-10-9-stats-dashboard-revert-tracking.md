# Summary Stats Dashboard + Revert Tracking Design

## Summary
Add a dashboard summary view (articles pushed, approve/reject rate, revert rate)
built on the existing `outcomes` table and `dimension_rates()` machinery from
#5, and a new revert-tracking subsystem — the first thing in this codebase
that inspects Wikipedia's post-push revision history — that detects when a
pushed edit gets reverted and feeds that back into `outcomes` as a new
`"reverted"` outcome type.

## Definition of Done
1. `push_edits()` captures the post-push revision ID from mwclient's
   `page.save()` and threads it into `record_outcome(..., revision_id=...)`,
   closing the gap where it's currently discarded.
2. A new `"reverted"` outcome type, detected by a periodic check against each
   previously-pushed article's revision history, writes a row to the existing
   `outcomes` table (no schema change needed beyond the new outcome string —
   the `revision_id` column already exists).
3. A new dashboard summary view (distinct articles pushed, approve/reject
   rate, revert rate) built on `dimension_rates()`-style aggregation over
   existing data.
4. A `wiki-cite check-reverts` CLI command (matching the existing argparse
   subcommand pattern) runs the revert check, since there's no scheduler in
   this repo — the user wires it to their own cron/launchd.
5. Tests covering the new store logic, revert-detection logic, and the
   summary view.

**Out of scope:** instant/real-time revert detection (impossible given
Wikipedia's revert latency), a general-purpose job scheduler, and
alerting/notifications on revert.

## Acceptance Criteria

### 9-stats-dashboard-revert-tracking.AC1: Revision capture at push time
- **AC1.1 Success:** After `push_edits()` succeeds, the new revision ID
  returned by mwclient's `page.save()` is persisted via
  `record_outcome(outcome="pushed", revision_id=<new_id>)`.
- **AC1.2 Failure:** If `page.save()` raises, or its response has no
  identifiable revision id, `push_edits()` still returns its existing
  `(bool, str)` contract, and no outcome row is written with a fabricated
  revision id — `revision_id=None` in that case, not a guess.

### 9-stats-dashboard-revert-tracking.AC2: Revert detection
- **AC2.1 Success:** For a `"pushed"` outcome row within the 7-day horizon,
  `wiki-cite check-reverts` walks `page.revisions()` newer than the captured
  revision id and, on finding a revision whose tags or edit summary match
  known rollback/undo/revert markers, writes a new `outcomes` row with
  `outcome="reverted"` referencing the same `article_title`/`revision_id`.
- **AC2.2 Failure:** If no later revision matches revert markers, no
  `"reverted"` row is written and the article stays in the pending-check set.
- **AC2.3 Failure (horizon expiry):** Once 7 days have elapsed since the
  `"pushed"` timestamp with no revert found, the article is excluded from
  future check runs (no unbounded growth of the pending-check set).

### 9-stats-dashboard-revert-tracking.AC3: `check-reverts` CLI command
- **AC3.1 Success:** `uv run wiki-cite check-reverts` collects all `"pushed"`
  outcomes within the horizon that lack a subsequent `"reverted"` row, checks
  each via the detector, and prints a summary (checked count, reverts found).
- **AC3.2 Failure:** A network/API error on one article does not abort the
  batch — the command continues to remaining articles and reports which ones
  failed to check.

### 9-stats-dashboard-revert-tracking.AC4: Summary dashboard view
- **AC4.1 Success:** The stats view shows distinct pushed-article count,
  revert rate (`reverted` / `pushed`), and approve/reject rate, computed from
  existing `outcomes` data via `dimension_rates()`-style aggregation.
- **AC4.2 Failure:** If the `outcomes` table is empty or unreadable (mirroring
  the existing `store_ok` pattern in `stats_page`), the view renders a
  "no data yet" state rather than erroring.

### 9-stats-dashboard-revert-tracking.AC5: Config
- **AC5.1 Success:** `config.yaml` gains a `revert_tracking` section with
  `check_horizon_days: 7` (overridable), consumed by both the CLI command and
  the detector.
- **AC5.2 Failure:** A missing/omitted config section falls back to the
  documented default (7 days) rather than crashing.

## Architecture

**Detection method — tag/edit-summary match.** After a push, walk
`page.revisions()` for revisions newer than our captured `revision_id`. For
each, inspect MediaWiki's revision `tags` (e.g. `mw-rollback`, `mw-undo`) and
`comment` fields for known revert markers. This needs only revision metadata
(no full wikitext fetch), matching how Wikipedia's own tooling already flags
most reverts. Content-hash comparison was considered but rejected for v1: it
requires a full-content fetch per check and is ambiguous on partial reverts;
tag/summary match is cheaper and covers the common case. If false-negatives
from manual (non-tool) reverts turn out to matter in practice, a hash-based
fallback can be layered on later without changing the `outcomes` schema.

**Trigger — external cron via new CLI command.** `wiki-cite check-reverts` is
a new argparse subcommand (same shape as existing `cmd_<name>` commands in
`cli.py`). It is not scheduled by this codebase; the user wires it to their
own cron/launchd. This avoids introducing a new concurrency pattern into
`web_app.py` and avoids a new runtime dependency (APScheduler/Celery), staying
consistent with this project's local-tool nature.

**Retention — 7-day horizon.** A `"pushed"` outcome is a check candidate until
either a `"reverted"` row is recorded for it, or 7 days elapse since its
`recorded_at` timestamp, whichever comes first. The horizon is a config value
(`revert_tracking.check_horizon_days`), not hardcoded, so it can be tuned
without a code change.

**New module: `wiki_cite/revert_checker.py`.** Houses the pending-candidate
query (pushed, not yet reverted, within horizon), the mwclient
`page.revisions()` walk, and the tag/summary matching logic. Kept separate
from `wikipedia_push.py` (which only handles the push side) and `seen_store.py`
(which only handles persistence) to keep each module single-purpose, per this
repo's existing separation of I/O modules.

**Revision capture threading.** `push_edits()` in `wikipedia_push.py` changes
its return to also carry the new revision id (extending its existing
`(bool, str)` tuple return, e.g. to `(bool, str, str | None)`), and the caller
in `web_app.py`'s push route passes that id into `record_outcome(...,
revision_id=...)` alongside the existing `outcome="pushed"` call.

**Summary view.** Reuses the existing `/stats` route and
`dimension_rates()`-backed query pattern; adds a top-of-page summary block
computed from a new small aggregation (distinct pushed article count, revert
rate) rather than a new page, since this is additive to data already shown
there.

## Existing Patterns
- `outcomes` table (`seen_store.py`) already has the `revision_id` column and
  a `record_outcome()` signature that accepts it — no migration needed to
  start writing it.
- `dimension_rates(dimension, success_outcomes=(...))` is the existing
  aggregation primitive; the revert rate and pushed-article count reuse this
  shape rather than introducing a parallel query mechanism.
- `_DIMENSION_COLUMNS` allowlist in `seen_store.py` guards against SQL
  injection on dynamic dimension queries — any new aggregation must go through
  the same allowlisted-column discipline, not raw string interpolation.
- CLI commands follow the `subparsers.add_parser(...)` +
  `cmd_<name>(args)` + `set_defaults(func=cmd_<name>)` pattern in `cli.py`;
  `check-reverts` follows this exactly, alongside the existing `stats`
  command's dimension-iteration style for its summary printout.
- `stats_page`'s `store_ok` / `sqlite3.Error` guard in `web_app.py` is the
  existing "graceful empty/broken store" pattern; the new summary block
  reuses it rather than adding a second error-handling style.
- Config sections are per-domain `BaseSettings` subclasses composed onto the
  main `Config` (see `FeedbackConfig` for the most recent precedent from #5);
  `RevertTrackingConfig` follows the same shape.
- No scheduler, no `page.revisions()` usage, and no background-thread pattern
  exist anywhere in this codebase today — confirmed by investigation, not
  assumed.

## Implementation Phases

### Phase 1: Capture post-push revision ID
**Goal:** Stop discarding the revision id mwclient returns from `page.save()`.
**Components:** `wiki_cite/wikipedia_push.py`, `wiki_cite/web_app.py` (push
route), `wiki_cite/seen_store.py` (already supports `revision_id`, verify only).
**Done when:** AC1.1, AC1.2.

### Phase 2: Revert detection core
**Goal:** New `wiki_cite/revert_checker.py` module: pending-candidate query,
`page.revisions()` walk, tag/summary revert matching.
**Components:** `wiki_cite/revert_checker.py` (new), `wiki_cite/seen_store.py`
(pending-candidate query helper, new `"reverted"` outcome writes).
**Done when:** AC2.1, AC2.2, AC2.3.

### Phase 3: `check-reverts` CLI command
**Goal:** Wire the detector into a runnable batch command with per-article
failure isolation and a summary printout.
**Components:** `wiki_cite/cli.py`.
**Done when:** AC3.1, AC3.2.

### Phase 4: Config
**Goal:** Make the retention horizon (and any other detector tunables)
configurable, following the existing per-section `BaseSettings` pattern.
**Components:** `config.yaml`, `wiki_cite/config.py`.
**Done when:** AC5.1, AC5.2.

### Phase 5: Dashboard summary view
**Goal:** Add the pushed-article count / approve-reject rate / revert-rate
summary block to the existing stats page.
**Components:** `wiki_cite/web_app.py` (`stats_page`),
`wiki_cite/templates/stats.html`.
**Done when:** AC4.1, AC4.2.

### Phase 6: Tests
**Goal:** Cover new store logic (pending-candidate query, `"reverted"`
writes), revert-detection matching logic, the CLI command's failure
isolation, and the summary view's empty/error states.
**Components:** `tests/test_seen_store.py`, `tests/test_revert_checker.py`
(new), `tests/test_cli.py`, `tests/test_web_app.py`.
**Done when:** All ACs above have direct test coverage.

## Glossary
- **Outcome row:** one record in the `outcomes` SQLite table representing a
  single edit-lifecycle event (`proposed`, `skipped`, `approved`, `rejected`,
  `pushed`, and now `reverted`).
- **Revert horizon:** the configurable window (default 7 days) after a push
  during which the revert checker keeps re-checking that article; after it
  elapses without a detected revert, the article is no longer checked.
- **Pending-check candidate:** a `"pushed"` outcome row with no corresponding
  `"reverted"` row yet, still within its revert horizon.
