# Issue #19 — Pushed proposals never leave the queue list on the dashboard

**Status:** In Review
**Complexity:** Simple
**GitHub:** https://github.com/jackal1991/wiki-cite/issues/19
**PR:** https://github.com/jackal1991/wiki-cite/pull/21

## Worktree

- branch: fix/19-queue-clear-pushed
- path: .worktrees/19-queue-clear-pushed
- created: 2026-07-19

## Summary
Reported by the user: after approving and pushing a proposal's edits to Wikipedia, it does
not clear from the queue on the main dashboard — it stays listed alongside pending proposals.

## Root cause (verified against current code)
- `GET /api/proposals` (`wiki_cite/web_app.py:218-234`) returns **all** proposals regardless
  of `status`, with no filtering.
- `index.html`'s `loadProposals()` (`wiki_cite/templates/index.html:600-615`) renders every
  proposal returned, and `proposalCard()` (`:578-598`) only changes the status pill to
  "pushed" rather than removing/hiding the item.
- `pending_proposal_count()` (`web_app.py:40-43`) *does* correctly exclude non-pending
  proposals from the cap count, so the 10-proposal cap itself isn't affected — this is a
  display-only bug on the dashboard queue list.

## Expected behavior
Once a proposal's status becomes `pushed` (or `rejected`), it should no longer appear in the
active queue list on the dashboard.

## What's needed
- Filter `GET /api/proposals` (or add a query param) so the dashboard's queue list only
  shows unresolved (`pending`) proposals by default.
- Update `loadProposals()` / `proposalCard()` in `index.html` accordingly — the "pushed"
  status-pill styling becomes dead code if filtering happens server-side, so remove or repurpose
  it based on the approach taken.
- Add/adjust test coverage confirming pushed and rejected proposals are excluded from the
  dashboard queue response.

## Scope / touch points
- `wiki_cite/web_app.py` — `get_proposals()`
- `wiki_cite/templates/index.html` — `loadProposals()` / `proposalCard()`
- `tests/test_web_app.py` (or equivalent) — coverage for the filtered queue endpoint

## Complexity
Simple — a filtering gap in one endpoint and one template function, not a design change.
