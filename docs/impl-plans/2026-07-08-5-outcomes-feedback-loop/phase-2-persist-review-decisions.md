# Phase 2: Persist approve/reject decisions from the review UI

**Goal:** The highest-value capture from the issue — make the review UI's per-edit
approve/reject decisions durable. Today `approve_edit`/`reject_edit` mutate only
`ProposedEdit.approved` in the in-memory `proposals` dict, so a restart loses them.

**ACs covered:** AC2 (decisions survive a restart).

**Depends on:** Phase 1 (`SeenStore.record_outcome` must exist).

## Files

- `wiki_cite/web_app.py` — `approve_edit` (lines 236–249), `reject_edit` (lines 251–264).
- `tests/test_web_app.py` — new file (no web_app tests exist today).

## Changes

### `wiki_cite/web_app.py`

Both routes already resolve `proposal` and bounds-check `edit_index` before mutating
`proposal.edits[edit_index].approved`. Add the outcomes write **synchronously with** that
mutation (design §"Capture points"):

- In `approve_edit`, after `proposal.edits[edit_index].approved = True`, call
  `seen_store.record_outcome(proposal.article.title, proposal.article.revision_id,
  "approved", edit_type=edit.edit_type.value, confidence=edit.confidence,
  source_type=edit.source.source_type.value if edit.source else None,
  reliability=edit.source.reliability.value if edit.source and edit.source.reliability
  else None, policy_reference=edit.policy_reference)` where `edit = proposal.edits[edit_index]`.
- In `reject_edit`, the same call with `outcome="rejected"`.

`record_outcome` is best-effort (swallows sqlite errors from Phase 1), so a storage failure
cannot turn a review click into a 500 (AC1.2 / AC2.2's inverse). Do **not** add a separate
try/except that would re-raise.

Note: these routes do not record the article-level dims (`categories`/`body_line_count`/
`has_infobox`) — those aren't available from an in-memory `Article` (models.py lines 164–173
has no categories). That is expected: the propose-time rows from Phase 1 carry the article
dims; approve/reject rows carry the edit-level dims. `dimension_rates` for edit dimensions
(`edit_type`, `confidence`, `source_type`) is what AC3/AC4 consume from these rows.

## Tests (`tests/test_web_app.py`)

Use Flask's test client (`app.test_client()`), a `tmp_path` DB via
`SEEN_DB_PATH`/config override, and seed a proposal directly into the in-memory `proposals`
dict. Because `proposals` is a closure local inside `create_app`, expose it for tests via one
of:

- Seed through the real flow is heavy; instead, **inject a proposal** by importing the app
  factory and reaching the dict. Simplest robust approach: add a tiny test-only seam — the
  design does not call for changing route internals, so prefer constructing the proposal and
  POSTing. If direct dict access is needed, access via `app` config or a module-level hook.
  Recommended: register the proposal by calling the app's internal store through a fixture that
  builds `create_app()` and then, before requests, inserts into the captured `proposals`
  mapping (obtain it by having the fixture build the proposal and use the fetch flow is
  overkill). Keep the seam minimal and test-only.

Concretely test:

- `test_approve_edit_persists_outcome`: build app on a `tmp_path` DB, seed a proposal with one
  edit, POST `/api/proposals/<id>/approve-edit/0`, then open a **fresh** `SeenStore` on the
  same DB path and assert `dimension_rates("edit_type")` (or a direct row query) shows one
  `approved` row with the edit's `edit_type`/`confidence` (AC2.1 — survives simulated restart).
- `test_reject_edit_persists_outcome`: same, `reject-edit/1`, assert a `rejected` row.
- `test_approve_then_reject_two_edits_survive_restart`: approve edit 0, reject edit 1, tear
  down the app object, rebuild `create_app()` (or just a fresh `SeenStore`) on the same file,
  assert both decisions are present with their edit-level dims intact (AC2.1 end-to-end).

If seeding the in-memory dict cleanly proves awkward, document the chosen seam in the test file
and keep it test-only (no production behavior change).

## Done when

- `uv run pytest tests/test_web_app.py` passes.
- Approve/reject via the test client produces outcomes rows readable from a fresh `SeenStore`
  on the same DB file after the app object is discarded (simulated restart) — AC2.1.
- An implementation that only writes `ProposedEdit.approved` with no `record_outcome` call
  fails the new tests — AC2.2.
- `uv run ruff check .` clean.
