# Test Requirements — Issue #14 "Next in queue →" link

Issue #14 has no separate design doc and no labeled "Acceptance criteria" block; the
ACs below are derived from the issue's **What's needed** and **Explicitly out of
scope** sections, which are the spec. The queue/skip/ordering logic lives in a new
server route (Phase 1) and is covered by `uv run pytest`; the review-page control
(Phase 2) is browser JavaScript and — as with #12 and #15 — is verified manually /
via Playwright, since this repo has **no automated frontend test harness**.

| AC | Requirement (from issue #14) | Phase | Verification | Notes |
|----|------------------------------|-------|--------------|-------|
| AC1 | A "Next in queue →" control appears on `review.html`. | 2 | Manual / Playwright: open a proposal from the queue with 2+ pending proposals; assert `#next-in-queue` is visible and reads "Next in queue →". | Manual — no JS test harness. |
| AC2 | Activating it jumps directly to the next *pending* proposal, excluding the one being reviewed, without a round-trip back to `/`. | 1 (logic) + 2 (nav) | `test_next_pending_returns_following_pending`, `test_next_pending_excludes_current`, `test_next_pending_wraps_around` in `tests/test_web_app.py`; Manual / Playwright: click the control, assert the browser loads `/review/<next_id>` for a different pending proposal. | Server logic pytest-covered; the navigation itself is manual. |
| AC3 | The target is a *pending* proposal — pushed/rejected/resolved proposals are skipped. | 1 | `test_next_pending_skips_non_pending` (seed 3 pending, mark the middle one `pushed`, assert it is skipped). | Fully pytest-covered server-side. |
| AC4 | When there is no other pending proposal, the control is **not** a dead link — it shows "Back to queue" (→ `/`) instead. | 1 (signal) + 2 (UI) | `test_next_pending_none_when_only_current_pending`, `test_next_pending_none_with_single_proposal` (endpoint returns `{"next_id": null}`); Manual / Playwright: with one pending proposal, assert `#next-in-queue` reads "Back to queue →" and points at `/`. | Endpoint's null answer is pytest-covered; the UI swap is manual. |
| AC5 | No bulk/batch-review UI is added (out of scope) — this is single-item navigation only. | 2 | Static check: grep the diff — no "approve all" / multi-select / batch controls added to `review.html`. | Scope guard, not a behavior. |
| — | Unknown `proposal_id` returns 404 (matches existing `/api/proposals/<id>/...` route convention). | 1 | `test_next_pending_unknown_id_404`. | Convention check. |

## Manual verification (why)

AC1, the navigation half of AC2, and the UI half of AC4 exercise browser JavaScript
in `review.html`, and this project has no frontend test harness (only pytest for the
Python backend). They are verified operationally by running `uv run wiki-cite web`
against a queue pre-seeded with a couple of pending proposals plus one
pushed/rejected proposal. The server contract they rely on
(`GET /api/proposals/<id>/next` returning the next pending id or `null`, and 404 on
an unknown id) is fully covered by the Phase 1 pytest additions.

## Commands

- `uv run pytest tests/test_web_app.py` — the Phase 1 `test_next_pending_*` tests
  (expected: all pass).
- `uv run pytest` — full-suite regression guard (expected: all pass; Phase 2 adds no
  Python behavior).
- `uv run ruff check .` — lint (expected: clean).
- `uv run wiki-cite web` — launch the dashboard for manual / Playwright verification
  of the review-page control.
