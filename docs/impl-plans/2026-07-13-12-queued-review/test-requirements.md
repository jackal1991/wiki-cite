# Test Requirements — Issue #12 Queued review

Acceptance criteria derived from the issue doc's "What's needed" section (there is no separate
AC list; that section is the spec). Each maps to the implementing phase and its verification.

| AC | Statement (from issue) | Phase | Verified by |
|----|------------------------|-------|-------------|
| AC1 | Stop auto-navigating to `/review/<id>` on the `selected` SSE event. Return to the queue view instead, refresh the proposals list, and re-enable the "Fetch new article" button (unless at cap). | 2 | Manual: fetch an article; view returns to queue, new proposal listed, button usable. No automated JS test (UI is untested by design). |
| AC2 | Server-side: the fetch route refuses to start a new scan when 10 pending proposals exist, returning a clear error/event. | 1 | `test_fetch_refused_at_cap_returns_queue_full` (JSON 409), `test_fetch_stream_refused_at_cap_emits_queue_full` (SSE `queue_full` event) in `tests/test_web_app.py`. |
| AC3 | Client-side: the fetch button reflects the cap (disabled with explanatory message when full), not just reacting to a server error after the fact. | 2 | Manual: with 10 pending proposals, button disabled on page load with the "Queue full" note. Backed by `test_pending_count_*` server tests (Phase 1) that the UI consumes. |
| AC4 | Cap counts proposals (articles), not raw edit count — a proposal with 3 edits still counts as 1. | 1 | `test_pending_count_counts_proposals_not_edits` (seeds a 2-edit proposal, asserts `pending == 1`). |
| AC5 | Resolving a proposal (push or full-reject) frees a queue slot, re-enabling further fetches if previously at cap. | 1 (server) + 2 (client) | `test_reject_proposal_sets_status_and_frees_slot`, `test_reject_proposal_unknown_id_404`, `test_push_frees_a_slot` in `tests/test_web_app.py`; manual verification of "Reject all" and push returning to queue with the button re-enabled. |

## Manual verification (why)

AC1 and AC3, and the client half of AC5, exercise browser JavaScript and SSE handling in
`index.html` / `review.html`. This project has no frontend test harness (only pytest for the
Python backend), so these are verified operationally by running `uv run wiki-cite web`. The
server contract they rely on (`/api/proposals/pending-count`, the `queue_full` event,
`/api/proposals/<id>/reject`) is fully covered by the Phase 1 pytest additions.
