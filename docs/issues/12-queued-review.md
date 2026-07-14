# Issue #12 — Queued review: stack up to 10 pending proposals instead of forcing single-proposal review

**Status:** In Progress
**Complexity:** Standard
**GitHub:** https://github.com/jackal1991/wiki-cite/issues/12

## Worktree

- branch: feat/12-queued-review
- path: .worktrees/12-queued-review
- created: 2026-07-13

## Summary
Currently, clicking "Fetch new article" and finding a candidate immediately force-navigates
the browser to `/review/<proposal_id>` (see `index.html`'s `selected` event handler). This
makes it feel like you must resolve (approve+push, or reject) one proposal before you can
go back and fetch another, even though nothing on the backend actually requires that.

## What already works (do not re-litigate)
- `wiki_cite/web_app.py`'s `proposals: dict[str, EditProposal]` already accumulates
  multiple proposals across fetches with no server-side restriction blocking a new fetch
  while others are pending.
- `EditProposal.edits: list[ProposedEdit]` already supports multiple edits within one
  proposal (one article, several proposed edits) — this is unrelated to the queue-depth
  question and needs no change.
- The queue list ("In your queue" on `index.html`, backed by `GET /api/proposals`) already
  renders every proposal with a "Review →" link to `/review/<id>` — clicking into any
  specific queued proposal already works.
- `SeenStore` marking an article "seen" once it has a proposal (from #3, idempotent fetch)
  is unaffected by this — confirmed with the requester that a second, separate proposal
  for an already-proposed article is explicitly out of scope here.

## What's needed
1. Stop auto-navigating to `/review/<id>` on the `selected` SSE event. Return to the queue
   view instead, refresh the proposals list, and re-enable the "Fetch new article" button
   (unless the queue is at cap — see below).
2. Enforce a hard cap of **10 pending (unresolved) proposals** at once:
   - Server-side: `/api/fetch-article/stream` (or `fetch_candidates`'s caller) should
     refuse to start a new scan when 10 proposals with `status == "pending"` already
     exist, returning a clear error/event rather than silently scanning anyway.
   - Client-side: the "Fetch new article" button should reflect the cap (disabled with an
     explanatory message when full), not just react to a server error after the fact.
   - Cap counts **proposals** (articles), not raw edit count — a proposal with 3 edits
     still counts as 1 toward the cap.
3. Resolving a proposal (push or full-reject) should free a queue slot, re-enabling
   further fetches if previously at cap.

## Scope / touch points
- `wiki_cite/web_app.py` — the fetch-stream route (pending-count check), possibly a new
  `/api/proposals/pending-count`-style helper or reuse of the existing `proposals` dict.
- `wiki_cite/templates/index.html` — `handleEvent`'s `selected` case (stop navigating away),
  fetch-button enable/disable logic reflecting the cap.
- `tests/test_web_app.py` — cap enforcement (10 pending blocks a new fetch; resolving one
  frees a slot).

## Complexity
Standard — contained to two files, no architectural ambiguity, existing patterns
(the `proposals` dict, the SSE event stream, the fetch-button disable/enable pattern)
cover this directly.
