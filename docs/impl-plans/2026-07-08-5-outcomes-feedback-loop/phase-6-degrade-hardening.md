# Phase 6: Full degrade-path hardening + tests

**Goal:** Make every AC6 failure mode explicit and tested — missing DB file, corrupt file,
old-schema file (a pre-Phase-1 DB with only `seen_articles`), and `feedback.enabled=false`.
Nothing in `fetch_candidates`, `ArticlePicker.__init__`, or `create_app()` may raise on a
missing/unreadable/partial-schema outcomes DB; it must log and fall back to today's unweighted
category order, and the stats surfaces must render "no data" instead of a 500.

**ACs covered:** AC6 (all sub-ACs).

**Depends on:** Phases 1, 3, 4, 5.

## Files

- `wiki_cite/seen_store.py` — harden `dimension_rates`/init against partial schema & corrupt DB.
- `wiki_cite/article_picker.py` — confirm `_build_scorer` returns `None` on every failure.
- `tests/test_seen_store.py`, `tests/test_article_picker.py`, `tests/test_web_app.py` — degrade tests.

## Changes / verification

### `SeenStore`

- `__init__` runs `CREATE TABLE IF NOT EXISTS` for both tables, so opening an old DB that has
  only `seen_articles` **adds** the `outcomes` table rather than failing (AC6.2 old-schema).
  Verify this: opening a `SeenStore` on a DB file that predates this design must not raise and
  must leave `seen_articles` intact.
- `dimension_rates` must tolerate a `sqlite3.OperationalError`/`DatabaseError` (corrupt file,
  missing column) — wrap its query in `try/except sqlite3.Error` and return `{}` on error so
  callers see "no data" rather than a traceback. (Alternatively, the callers each wrap it; but
  centralizing the empty-dict fallback here keeps `/stats` and the scorer simple. Choose one
  and be consistent — the design has `/stats` wrapping in try/except, so if `dimension_rates`
  already returns `{}` on error, `/stats`'s guard is belt-and-suspenders, which is fine.)
- A corrupt DB file: `sqlite3.connect` itself may succeed lazily and fail on first query.
  Ensure the `__init__` `execute(_SCHEMA)`/`execute(_OUTCOMES_SCHEMA)` path does not crash
  `create_app()` on a corrupt file — if it can, `create_app` needs to tolerate a failed store
  init (log + continue with a store whose reads return empty / whose writes are no-ops). Decide
  the seam: simplest is that `SeenStore.__init__` catches a schema-setup `sqlite3.Error`, logs,
  and marks itself degraded so `record_outcome` no-ops and `dimension_rates` returns `{}`.

### `ArticlePicker._build_scorer`

Confirm (from Phase 5) it returns `None` when: `seen_store is None`, `feedback.enabled is
False`, or any `sqlite3.Error` from `dimension_rates`. `ranked = pool` in that case →
category order (AC6.1/AC6.3). No code path in `fetch_candidates` or `__init__` may raise on a
bad DB.

### `create_app`

`SeenStore(config.seen_db_path)` is constructed at app startup (web_app.py line 34). Ensure a
missing directory / corrupt file there does not throw out of `create_app()` (AC6.3). If
`SeenStore.__init__` is hardened per above, this is covered.

## Tests

- `tests/test_seen_store.py`:
  - `test_opens_old_schema_db_adds_outcomes` (AC6.2): create a DB with only the
    `seen_articles` table (run the old `_SCHEMA` by hand or via a `SeenStore` then drop the
    outcomes table), reopen with the new `SeenStore`, assert no raise and `record_outcome`
    works.
  - `test_dimension_rates_on_corrupt_db_returns_empty` (AC6.3): write garbage bytes to a
    `.db` file, open a `SeenStore` (or force a query error), assert `dimension_rates` returns
    `{}` and does not raise.
- `tests/test_article_picker.py`:
  - `test_fetch_candidates_missing_db_matches_category_order` (AC6.1): point the picker's
    `seen_store` at a fresh empty DB (or `seen_store=None`), assert `fetch_candidates` yields
    the same order as a plain category walk and does not raise.
  - `test_fetch_candidates_corrupt_db_falls_back` (AC6.3): a `seen_store` whose
    `dimension_rates` raises `sqlite3.Error` → `_build_scorer` returns `None` → category
    order, no raise. (Use a `Mock` seen_store whose `dimension_rates` raises.)
  - `test_fetch_candidates_feedback_disabled` (AC6): `feedback.enabled=False` → category order.
- `tests/test_web_app.py`:
  - `test_stats_route_corrupt_db_no_500` (AC6.3): corrupt/empty DB → GET `/stats` returns 200
    with "no data", not a 500.
  - `test_create_app_with_missing_db_dir_does_not_raise` (AC6.3): build `create_app()` with a
    `seen_db_path` in a nonexistent/odd location and assert it does not throw at construction.

## Done when

- `uv run pytest` (full suite) passes.
- A DB file that is corrupt, missing, or contains only `seen_articles` does not raise anywhere
  in `create_app()` or `fetch_candidates`; the picker silently uses unweighted category order,
  and `/stats` + `wiki-cite stats` render "no data" (AC6.1/6.2/6.3).
- `uv run ruff check .` clean.
