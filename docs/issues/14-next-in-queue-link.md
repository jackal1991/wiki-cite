# Issue #14 — Add "Next in queue →" link on the review page

**Status:** In Review
**Complexity:** Standard
**GitHub:** https://github.com/jackal1991/wiki-cite/issues/14
**PR:** https://github.com/jackal1991/wiki-cite/pull/20

## Worktree

- branch: feat/14-next-in-queue-link
- path: .worktrees/14-next-in-queue-link
- created: 2026-07-18

## Summary
#12 shipped queued review (up to 10 pending proposals can stack up), but working through
a full queue is still fully manual: the only navigation is `/` → click "Review →" on a
card → resolve it → back to `/` → click the next card. There's no shortcut from inside
the review page itself.

## What's needed
Add a "Next in queue →" link/button on `review.html` that jumps directly to the next
*pending* proposal (excluding the one currently being reviewed), skipping the round-trip
back to `/`. When there is no other pending proposal, show something else instead (e.g.
"Back to queue" or hide the control) rather than a dead link.

## Explicitly out of scope
A full bulk/batch-review UI (e.g. "approve all" across multiple proposals at once) — each
edit still needs individual scrutiny before push, so batch-approval isn't something we
actually want. This issue is just about faster single-item navigation through the queue,
not batch action.

## Scope / touch points
- `wiki_cite/templates/review.html` — the new "Next in queue" control; likely reuses the
  existing `GET /api/proposals` endpoint (already returns `status` per proposal) to find
  the next pending one client-side, or a small new server route if that's cleaner.
- `tests/test_web_app.py` — if a new server route is added.

## Complexity
Standard — small, contained UI addition on an existing page, reusing an existing endpoint.
