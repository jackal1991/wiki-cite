# Phase 1: Capture the post-push revision ID

**Goal:** Stop discarding the revision id mwclient returns from `page.save()`.
Thread it out of `push_edits()` and into the `"pushed"` outcome rows so revert
detection (phase 2) has a revid anchor to walk forward from.

**ACs covered:** AC1.1 (captured revid persisted on `"pushed"` rows),
AC1.2 (no fabricated revid on failure / null edit; existing return contract preserved).

## Files

- `wiki_cite/wikipedia_push.py` — `push_edits()` return + `page.save()` capture.
- `wiki_cite/web_app.py` — `push_proposal` route threads the revid into `record_outcome`.
- `tests/test_wikipedia_push.py` — save-return capture tests.
- `tests/test_web_app.py` — push route persists revid.

## Background (verified)

`push_edits()` today returns `tuple[bool, str]` and calls `page.save(...)`
without using its return value (wikipedia_push.py:120-130). mwclient's
`Page.save()` returns the `result['edit']` dict; on a real content change it
carries `newrevid` (int). On a **null edit** it omits `newrevid` and includes
`nochange`. So the new id is `edit_result.get("newrevid")` — possibly absent.

## Changes

### 1. `wiki_cite/wikipedia_push.py` — `push_edits`

- Change the return type to `tuple[bool, str, str | None]` — success flag,
  message, and the new revision id (or `None`). Update the docstring `Returns:`
  block to match.
- Every existing early-return path (rate limit, edit conflict, empty summary,
  page-access failure, save failure) must now return a 3-tuple with `None` as
  the third element. Do **not** invent a revid on any failure path (AC1.2).
- In the success path, capture the save result:
  ```python
  edit_result = page.save(modified_text, summary=edit_summary, minor=True, bot=True)
  self.rate_limiter.record_edit()
  new_revid = edit_result.get("newrevid") if isinstance(edit_result, dict) else None
  new_revid = str(new_revid) if new_revid is not None else None
  return True, f"Successfully pushed edits. Edit summary: {edit_summary}", new_revid
  ```
  Storing as `str` matches the `revision_id TEXT` column and the existing
  `mark_seen`/`record_outcome` revid string usage. A null edit (no `newrevid`)
  yields `None` — a real, if rare, path that must not fabricate an id (AC1.2).

### 2. `wiki_cite/web_app.py` — `push_proposal` (currently line 399)

- Unpack the third value:
  ```python
  success, message, new_revid = push_service.push_edits(proposal, modified_text)
  ```
- In the `if success:` block, the `record_outcome(..., "pushed", ...)` loop
  (lines 405-415) currently passes `proposal.article.revision_id` as the revid
  (the *base* revision the edit was analyzed against). Change the second
  positional arg to `new_revid` so the row anchors on the **post-push** revision
  the revert checker must walk forward from:
  ```python
  seen_store.record_outcome(
      proposal.article.title,
      new_revid,
      "pushed",
      ...
  )
  ```
  Keep `mark_seen(proposal.article.title, proposal.article.revision_id, "pushed")`
  unchanged — `seen_articles` is the base-revision idempotency store, a different
  concern.
- If `new_revid` is `None` (null edit or missing id), the `"pushed"` rows are
  still written with `revision_id=None`; phase 2's candidate query filters those
  out (a pushed row with no revid can't be walked). That is the correct AC1.2
  degrade — no guess.

## Tests

### `tests/test_wikipedia_push.py`

Follow the existing Mock-site style. Build a `WikipediaPushService(site=mock_site)`
so no network/login happens. Stub `check_for_conflicts` to `False` (or arrange
the mock revision to match) and `proposal.get_edit_summary()` to a non-empty string.

- `test_push_edits_returns_new_revid_from_save` (AC1.1): mock
  `page.save` to return `{"result": "Success", "newrevid": 12345, "oldrevid": 12344}`.
  Assert `push_edits(...)` returns `(True, <msg>, "12345")`.
- `test_push_edits_null_edit_returns_none_revid` (AC1.2): mock `page.save` to
  return `{"result": "Success", "nochange": ""}` (no `newrevid`). Assert the
  third element is `None`, first is `True`.
- `test_push_edits_save_failure_returns_none_revid` (AC1.2): mock `page.save`
  to raise; assert `(False, <error msg>, None)` and that no exception escapes.
- Update any existing push tests that unpack a 2-tuple to the new 3-tuple.

### `tests/test_web_app.py`

- `test_push_persists_new_revid` (AC1.1): seed a proposal with one approved edit
  into `app.proposals`, monkeypatch the app's `push_service.push_edits` to return
  `(True, "ok", "999")`, POST the push route, then read the `outcomes` table
  (fresh `SeenStore` on the same `seen_db_path`) and assert the `"pushed"` row's
  `revision_id == "999"`.

## Done when

- `uv run pytest tests/test_wikipedia_push.py tests/test_web_app.py` passes.
- Every `push_edits` return path yields a 3-tuple; no failure path fabricates a revid.
- The `"pushed"` outcome row carries the post-push `newrevid`.
- `uv run ruff check .` clean.
