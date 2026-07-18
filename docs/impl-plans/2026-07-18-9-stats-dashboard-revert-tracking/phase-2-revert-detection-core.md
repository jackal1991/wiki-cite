# Phase 2: Revert detection core

**Goal:** A new `wiki_cite/revert_checker.py` module plus a `SeenStore` helper
that: (a) finds `"pushed"` outcome rows still inside the revert horizon with no
`"reverted"` row yet, (b) walks each article's newer revisions via mwclient, and
(c) writes a `"reverted"` outcome row when a rollback/undo/revert marker is found.

**ACs covered:** AC2.1 (marker match → `"reverted"` row), AC2.2 (no match → no
row, stays pending), AC2.3 (horizon expiry → dropped from the candidate set).

**Depends on:** Phase 1 (`"pushed"` rows carry the post-push revid). Reads
`config.revert_tracking.check_horizon_days` (phase 4 — see README ordering note).

## Files

- `wiki_cite/seen_store.py` — `pending_revert_candidates(horizon_days)` query.
- `wiki_cite/revert_checker.py` — **new** module: revisions walk + marker match.
- `tests/test_seen_store.py` — candidate-query tests.
- `tests/test_revert_checker.py` — **new**: marker-matching tests (phase 6 rounds these out).

## Part A — `SeenStore.pending_revert_candidates`

Add a method returning the pushed articles still worth checking:

```python
def pending_revert_candidates(self, horizon_days: int) -> list[tuple[str, str]]:
    """Return [(article_title, revision_id)] for `"pushed"` outcomes that are
    within `horizon_days` of their recorded_at, have a non-null revision_id, and
    have no later `"reverted"` row for the same (article_title, revision_id).
    """
```

Implementation notes:

- Guard `self._conn is None` → return `[]`, and wrap the read in
  `try/except sqlite3.Error` → `logger.warning(...)`, return `[]` (mirrors the
  existing degrade style in this file).
- Compute the cutoff in Python and bind it as a parameter (do **not** rely on
  SQLite date functions over ISO strings mixing formats): `recorded_at` is
  `datetime.now().isoformat()`, so a lexicographic `>=` on isoformat strings is a
  valid time comparison. Use:
  ```python
  cutoff = (datetime.now() - timedelta(days=horizon_days)).isoformat()
  ```
- Query the pushed rows, then exclude ones already reverted. A single SQL is fine:
  ```sql
  SELECT p.article_title, p.revision_id
  FROM outcomes p
  WHERE p.outcome = 'pushed'
    AND p.revision_id IS NOT NULL
    AND p.recorded_at >= ?
    AND NOT EXISTS (
      SELECT 1 FROM outcomes r
      WHERE r.outcome = 'reverted'
        AND r.article_title = p.article_title
        AND r.revision_id = p.revision_id
    )
  ```
  Bind `cutoff` as the single `?` parameter. De-duplicate in Python if the same
  `(title, revid)` appears on multiple `"pushed"` rows (phase 1 writes one pushed
  row per approved edit, so a multi-edit push produces duplicate `(title, revid)`
  pairs) — return a de-duplicated list, e.g. via `dict.fromkeys(...)`, to avoid
  re-walking the same article N times per run.
- `recorded_at >= cutoff` is exactly AC2.3: once `now - recorded_at > horizon`,
  the row drops out of the candidate set — no unbounded growth.

## Part B — `wiki_cite/revert_checker.py` (new)

Single-purpose module: given an mwclient `Site`, a `SeenStore`, and the horizon,
check each pending candidate and record reverts. Keep the pure marker-matching
logic separate from the I/O so it is unit-testable without a live site.

### Revert markers

MediaWiki flags most reverts with change tags; the checker matches on tags first,
edit-summary substrings second (the design's tag/summary method):

```python
REVERT_TAGS = frozenset({"mw-rollback", "mw-undo", "mw-manual-revert", "mw-reverted"})
REVERT_SUMMARY_MARKERS = ("revert", "reverted", "rv ", "undo", "undid", "rollback", "restore")
```

Pure predicate (unit-tested directly):

```python
def is_revert_revision(tags: list[str] | None, comment: str | None) -> bool:
    if tags and any(t in REVERT_TAGS for t in tags):
        return True
    if comment:
        low = comment.lower()
        if any(marker in low for marker in REVERT_SUMMARY_MARKERS):
            return True
    return False
```

Keep the summary-marker list conservative and documented — a false positive
writes a spurious `"reverted"` row that dents the revert rate, so tags are the
primary signal and summary matching is the cheap fallback the design accepts for
v1 (manual reverts without tags). Note inline that content-hash confirmation was
deliberately deferred (design §Architecture).

### The walk

```python
def check_article_for_revert(site, article_title: str, pushed_revid: str) -> bool:
    """Return True if a revision newer than pushed_revid reverts our edit."""
    page = site.pages[article_title]
    revisions = page.revisions(
        startid=int(pushed_revid),
        dir="newer",
        prop="ids|timestamp|flags|comment|user|tags",
    )
    for rev in revisions:
        if str(rev.get("revid")) == str(pushed_revid):
            continue  # startid is inclusive — skip our own revision
        if is_revert_revision(rev.get("tags"), rev.get("comment")):
            return True
    return False
```

- `prop` MUST include `tags` (the default prop omits it — verified against the
  installed mwclient). `dir="newer"` walks forward in time from our push.
- `startid` is inclusive, so the first yielded revision is our own push — skip it
  by revid.
- `pushed_revid` is a `str` (TEXT column); `startid` needs an `int`. Guard the
  `int(...)` conversion — a non-numeric stored id (shouldn't happen post-phase-1,
  but be defensive at this I/O boundary) is treated as un-walkable: log and return
  `False` rather than raising.

### Batch entry point

```python
def check_pending_reverts(site, store, horizon_days: int) -> RevertCheckSummary:
    """Walk every pending candidate; record `"reverted"` on match. Per-article
    errors are caught so one bad article can't abort the batch (AC3.2)."""
```

- Return a small dataclass/`NamedTuple` `RevertCheckSummary(checked, reverts_found, failures)`
  where `failures` is a `list[tuple[str, str]]` of `(article_title, error)`.
- For each `(title, revid)` from `store.pending_revert_candidates(horizon_days)`:
  - `try:` call `check_article_for_revert`; on `True`, call
    `store.record_outcome(title, revid, "reverted")` (no dimension kwargs — a
    revert is an article/revision-level event) and increment `reverts_found`.
  - `except Exception as e:` append `(title, str(e))` to `failures` and continue —
    a network/API error on one article must not stop the batch (AC3.2). Increment
    `checked` for every attempted article.

Note: `record_outcome` already swallows its own sqlite errors, so a storage
hiccup on the write does not raise here.

## Tests (this phase; phase 6 extends)

### `tests/test_seen_store.py`

- `test_pending_revert_candidates_returns_recent_pushed`: record a `"pushed"`
  row with a revid and recent `recorded_at`; assert it appears as `(title, revid)`.
- `test_pending_revert_candidates_excludes_reverted`: record `"pushed"` then a
  `"reverted"` row for the same `(title, revid)`; assert it is **not** returned (AC2.2/AC2.1 followthrough).
- `test_pending_revert_candidates_excludes_expired`: record a `"pushed"` row and
  then age it past the horizon. Since `recorded_at` is set by `record_outcome`
  itself, insert the aged row via the store's connection with an explicit old
  `recorded_at` (or expose the write path), and assert a small `horizon_days`
  excludes it (AC2.3).
- `test_pending_revert_candidates_excludes_null_revid`: a `"pushed"` row with
  `revision_id=None` is not returned (can't be walked).
- `test_pending_revert_candidates_dedupes`: two `"pushed"` rows, same `(title, revid)`
  → one entry returned.

### `tests/test_revert_checker.py` (new)

- `test_is_revert_revision_matches_tag`: `is_revert_revision(["mw-rollback"], None)` is `True`.
- `test_is_revert_revision_matches_summary`: `is_revert_revision([], "Undid revision 123 by X")` is `True`.
- `test_is_revert_revision_ignores_normal_edit`: `is_revert_revision(["mw-visualeditor"], "typo fix")` is `False`.
- `test_check_article_for_revert_skips_own_revision`: fake `site.pages[title].revisions(...)`
  to yield only our own revid → `False` (AC2.2).
- `test_check_article_for_revert_detects_newer_revert`: fake revisions to yield our
  revid then a newer one tagged `mw-undo` → `True` (AC2.1).

Fake the site with `Mock`: `site.pages.__getitem__` returns a page whose
`revisions(...)` returns a list of dicts like
`{"revid": 12346, "tags": ["mw-undo"], "comment": "..."}`.

## Done when

- `uv run pytest tests/test_seen_store.py tests/test_revert_checker.py` passes.
- `pending_revert_candidates` returns only in-horizon, un-reverted, revid-bearing pushes.
- The revisions walk requests `tags` and skips the anchor revision.
- `uv run ruff check .` clean.
