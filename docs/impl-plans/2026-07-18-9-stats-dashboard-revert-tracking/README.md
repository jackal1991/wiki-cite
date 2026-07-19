# Implementation Plan: Summary Stats Dashboard + Revert Tracking (#9)

Design: `docs/design-plans/2026-07-10-9-stats-dashboard-revert-tracking.md`

## Goal

Two additive changes on top of the #5 outcomes machinery:

1. **Revert tracking** — capture the post-push revision id (currently discarded
   by `push_edits()`), then a new `wiki-cite check-reverts` command walks each
   pushed article's newer revisions and, on finding a rollback/undo/revert
   marker, writes a `"reverted"` outcome row. This is the first code in the repo
   that inspects Wikipedia's post-push revision history.
2. **Summary dashboard view** — a top-of-page block on the existing `/stats`
   route showing distinct pushed-article count, approve/reject rate, and revert
   rate, computed from the `outcomes` table.

No `outcomes` schema migration is needed: the `revision_id` column and a
`record_outcome(..., revision_id=...)` signature already exist
(`seen_store.py`), and `"reverted"` is just a new outcome string.

## Phase sequence

| Phase | File | Scope | ACs |
|---|---|---|---|
| 1 | `phase-1-capture-revision-id.md` | Thread `page.save()`'s new revid through `push_edits()` → `record_outcome(outcome="pushed", revision_id=...)` | AC1 |
| 2 | `phase-2-revert-detection-core.md` | New `revert_checker.py`: pending-candidate query, `page.revisions()` walk, tag/summary marker match, `"reverted"` write | AC2 |
| 3 | `phase-3-check-reverts-cli.md` | `wiki-cite check-reverts` command with per-article failure isolation + summary printout | AC3 |
| 4 | `phase-4-revert-tracking-config.md` | `RevertTrackingConfig` (`check_horizon_days: 7`) wired into config load | AC5 |
| 5 | `phase-5-summary-dashboard.md` | Summary block on `/stats` (`stats_page` + `stats.html`) | AC4 |
| 6 | `phase-6-tests.md` | Round out test coverage for all ACs | (all) |

**Ordering note.** Phase 4 (config) is written after phases 2–3 for narrative
flow, but phases 2 and 3 both *read* `check_horizon_days`. Implement the config
section (phase 4) alongside phase 2, or have phase 2/3 fall back to a hardcoded
`7` behind a `get_config().revert_tracking.check_horizon_days` accessor that
phase 4 then backs with real config. Either way, land phase 4's config before
merging so the horizon is not a magic number. The phase-2 and phase-3 files
assume `config.revert_tracking.check_horizon_days` exists.

## Conventions to honor

- `uv run pytest` is the test command; coverage + branch coverage are on by default.
- `uv run ruff check .` is the only style gate (line-length 300, E/F/W). No black, no mypy.
- Tests live in `tests/` as `test_<module>.py`; `tmp_path` fixtures, no mocking of
  sqlite (per `test_seen_store.py` style). mwclient pages/sites are faked with
  `unittest.mock.Mock` (per `test_article_picker.py` / `test_wikipedia_push.py` style).
- Config sections are per-domain `BaseSettings` subclasses composed onto `Config`
  and gated in `Config.load` by an `if "<section>" in yaml_config` block — follow
  `FeedbackConfig` (config.py:69) exactly.
- Commits are LOCAL ONLY — never push.

## Grounding notes (verified against the branch)

- `outcomes` table has `revision_id TEXT` (seen_store.py:31); `record_outcome`'s
  second positional arg is `revision_id: str | None` (seen_store.py:134). No migration.
- `push_edits()` returns `(bool, str)` and today throws away `page.save()`'s
  return value (wikipedia_push.py:120-130). Phase 1 changes both.
- mwclient `Page.save()` returns the `result['edit']` dict (page.py); on a
  successful content change it contains `newrevid` (int) and `oldrevid`. On a
  **null edit** (no change) there is no `newrevid` — a `nochange` key is present
  instead. AC1.2's "no fabricated revision id" hinges on this: read
  `edit_result.get("newrevid")`, coerce to `str` only when present, else `None`.
- mwclient `Page.revisions()` default `prop` is
  `'ids|timestamp|flags|comment|user'` — it does **not** include `tags`. The
  revert walk MUST pass `prop='ids|timestamp|flags|comment|user|tags'` to see the
  `mw-rollback` / `mw-undo` / `mw-manual-revert` tags, and use `dir='newer'` to
  walk forward from the captured revid.
- `dimension_rates(dimension, success_outcomes=(...))` (seen_store.py:188) and
  the `_DIMENSION_COLUMNS` allowlist (seen_store.py:51) are the aggregation
  primitive and its SQL-injection guard. Any new aggregation reuses this shape.
- `stats_page` already has the `store_ok` / `sqlite3.Error` degrade pattern
  (web_app.py:440-449); the summary block reuses it.
- `STATS_DIMENSIONS` lives in `wiki_cite/stats.py` and is imported by both
  `cli.py` and `web_app.py` — the summary aggregation helper belongs there too so
  the two surfaces can't drift.
- No `page.revisions()` usage, scheduler, or background-thread pattern exists in
  the repo today (confirmed) — `check-reverts` is an external-cron CLI command,
  not an in-process job.

## Open note for the executor

The issue doc `docs/issues/9-stats-dashboard-revert-tracking.md` referenced by
the design is **not present on this branch** (only #5, #6, #8 issue docs exist).
The design doc is self-contained (Definition of Done + AC1–AC5), so this plan is
grounded in the design. If the issue doc is expected, flag it before execution.
