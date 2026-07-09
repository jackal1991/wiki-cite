# Phase 1: Widen the outcomes schema + record skip/propose/push

**Goal:** Add the `outcomes` table and `record_outcome`/`dimension_rates` methods to
`SeenStore`, and wire the calls into the capture points that already exist in
`scan_events` (skip, propose) and `push_proposal` (push). No UI-route change yet
(that is Phase 2).

**ACs covered:** AC1 (record at every capture point; storage failure degrades),
AC6.1 (partial — a missing DB file does not crash the scan).

## Files

- `wiki_cite/seen_store.py` — new table, `record_outcome`, `dimension_rates`.
- `wiki_cite/web_app.py` — `scan_events` (skip + propose), `push_proposal`.
- `tests/test_seen_store.py` — new outcomes tests.

## Changes

### 1. `wiki_cite/seen_store.py`

The class already owns a shared `sqlite3` connection (`check_same_thread=False`)
guarded by `self._lock`, created in `__init__` (lines 32–38), and runs `_SCHEMA`
via `CREATE TABLE IF NOT EXISTS`. Reuse both for the new table.

- Add module-level logging: `import logging` and `logger = logging.getLogger(__name__)`.
- Add an `_OUTCOMES_SCHEMA` constant matching the design's schema sketch (design §"Schema
  sketch"): columns `id` (PK autoincrement), `article_title` (NOT NULL), `revision_id`,
  `outcome` (NOT NULL), `recorded_at` (NOT NULL), then article characteristics
  (`categories` TEXT JSON, `body_line_count` INTEGER, `has_infobox` INTEGER,
  `citation_needed_count` INTEGER) and citation/edit characteristics (`edit_type`,
  `confidence`, `source_type`, `source_api`, `reliability`, `policy_reference`), all nullable.
- In `__init__`, after the existing `self._conn.execute(_SCHEMA)`, also
  `self._conn.execute(_OUTCOMES_SCHEMA)` before the single `commit()`.
- Add `record_outcome(...)` with the exact signature from the design (design §"`SeenStore`
  additions"): positional `article_title`, `revision_id`, `outcome`, then keyword-only
  (`*`) optional dimensions. Serialize `categories` with `json.dumps(categories)` when not
  `None`; coerce `has_infobox` to `int(...)` (0/1) when not `None`. Timestamp with
  `datetime.now().isoformat()` (mirrors `mark_seen`). Wrap the `INSERT` + `commit()` in
  `try/except sqlite3.Error` under `self._lock`; on error, `logger.warning(...)` and return
  (never raise) — this is AC1.2.
- Add `dimension_rates(dimension, success_outcomes=("approved", "pushed"))` returning
  `dict[str, tuple[int, int]]` = `{value: (successes, total)}`:
  - **Validate `dimension` against an allowlist of real column names** (do not interpolate a
    caller string straight into SQL). Define a module-level frozenset/tuple of aggregatable
    columns: `source_type`, `source_api`, `edit_type`, `confidence`, `has_infobox`,
    `reliability`, `policy_reference`, `body_line_count`, `categories`. Raise `ValueError`
    for anything else. This keeps the query parameterizable without SQL injection.
  - For the scalar columns: `SELECT <col>, outcome FROM outcomes WHERE <col> IS NOT NULL`,
    then tally per value in Python — total = all rows for that value, successes = rows whose
    `outcome in success_outcomes`. (Grouping in Python keeps the success-set membership
    logic in one place and avoids a second parametrized IN-clause.)
  - `categories` is special: it is JSON-encoded. Decode each row's `categories` with
    `json.loads`, and count each list element as its own dimension value (design §"Schema
    sketch" — decode in Python). Guard `json.loads` failures per-row (skip malformed).
  - `has_infobox` is stored 0/1 — return the values as the strings the scorer/stats expect;
    the design's scorer keys on `str(candidate.has_infobox)` i.e. `"True"`/`"False"`, so map
    `1 -> "True"`, `0 -> "False"` here so the scorer and stats agree. Document this mapping
    inline.
  - `dimension_rates` does **no division** (design §"`SeenStore` additions") — callers
    compute the rate and handle `total == 0`.

### 2. `wiki_cite/web_app.py` — capture points

`scan_events` and `push_proposal` already call `seen_store.mark_seen(...)`; slot
`record_outcome` next to those existing mutations (design §"Capture points").

- **Skip** (`scan_events`, currently line 111–113, next to `mark_seen(..., "skipped")`):
  call `seen_store.record_outcome(candidate.title, candidate.revision_id, "skipped",
  categories=candidate.categories, body_line_count=candidate.body_line_count,
  has_infobox=candidate.has_infobox,
  citation_needed_count=len(candidate.citation_needed_claims))`.
- **Propose** (`scan_events`, at the `has_confident_citation()` branch, currently lines
  99–109, next to `mark_seen(..., "selected")`): record **one row per edit** in
  `proposal.edits` (design §"Capture points" — edit grain, so citation characteristics are
  captured for later AC4 scoring). For each `edit`, pass the article-level dims
  (`categories`, `body_line_count`, `has_infobox`, `citation_needed_count` from `candidate`)
  plus `edit_type=edit.edit_type.value`, `confidence=edit.confidence`,
  `source_type=edit.source.source_type.value if edit.source else None`,
  `reliability=edit.source.reliability.value if edit.source and edit.source.reliability
  else None`, `policy_reference=edit.policy_reference`, `outcome="proposed"`.
  `edit.source` is `None` today, so those columns stay NULL until #4 (AC6.2).
- **Push** (`push_proposal`, currently line 306, next to `mark_seen(..., "pushed")`): record
  one `"pushed"` row per approved edit (`proposal.get_approved_edits()`), same edit-level
  shape as propose, so `dimension_rates` counts pushes as successes at the same grain it
  counted proposals.

`record_outcome` swallows its own sqlite errors, so no extra `try/except` is needed at these
call sites — but confirm a raised error inside recording cannot abort `scan_events` or return
a 500 (AC1.2). Since `candidate` exposes `categories`/`body_line_count`/`has_infobox`
(`CandidateArticle`, models.py lines 148–161), the article dims are available at the skip/
propose sites without a re-fetch.

## Tests (`tests/test_seen_store.py`)

Follow the existing style: `tmp_path`, a fresh `SeenStore` per test, no mocking.

- `test_record_outcome_inserts_row`: record a `"skipped"` outcome with article dims; assert a
  direct `SELECT` (via a fresh `SeenStore` on the same path, or the store's connection) shows
  the row with the right `outcome`, `article_title`, and a non-null `recorded_at` (AC1.1).
- `test_record_outcome_swallows_errors`: point a `SeenStore` at a path, then force a
  sqlite failure (e.g. close/replace the underlying connection, or record with the DB file
  made unwritable) and assert `record_outcome` returns `None` without raising (AC1.2).
- `test_dimension_rates_counts_successes_and_total`: record several rows for
  `source_type=news` (some `approved`/`pushed`, some `rejected`) and assert
  `dimension_rates("source_type")["news"] == (K, N)` with N = all rows, K = success rows.
- `test_dimension_rates_categories_explodes_json`: record two rows each with a
  `categories` JSON list sharing one category; assert that category's total counts both rows.
- `test_dimension_rates_rejects_unknown_dimension`: assert `ValueError` for a bogus column
  name (SQL-injection guard).
- `test_dimension_rates_empty_db_returns_empty`: fresh store → `dimension_rates("source_type")
  == {}` (feeds AC6.1).

## Done when

- `uv run pytest tests/test_seen_store.py` passes.
- A manual/tested scan that skips one candidate, selects another, and pushes it produces the
  expected outcomes rows (skip: 1 article-level row; propose: one row per edit; push: one row
  per approved edit) with correct `outcome` values and dims.
- Deleting the DB file and re-running the scan does not crash (recording is best-effort).
- `uv run ruff check .` clean.
