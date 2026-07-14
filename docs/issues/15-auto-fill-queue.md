# Issue #15 — Auto-fill queue: keep fetching automatically until the 10-proposal cap

**Status:** In Progress
**Complexity:** Standard
**GitHub:** https://github.com/jackal1991/wiki-cite/issues/15

## Worktree

- branch: feat/15-auto-fill-queue
- path: .worktrees/15-auto-fill-queue
- created: 2026-07-14

## Summary
#12 made it possible to stack up to 10 pending proposals in the queue without
being forced into single-proposal review, but "Fetch new article" still only
fires once per click — filling the queue to 10 requires clicking it up to 10
times by hand. This adds an "Auto-fill queue" toggle that keeps fetching
automatically (one fetch at a time, respecting the existing 10-proposal cap
and the sequential-only Wikipedia constraint) until the queue is full or the
user turns it off.

## Design decisions (already made, do not re-litigate)
- **Explicit start/stop toggle, off by default.** Not fully-automatic-always-on.
  Given this session's rate-limit incident history and the real Anthropic API
  cost of each fetch (up to ~40 model calls per successful fetch, per
  `agent.max_search_turns` × `agent.max_candidates_per_fetch`), auto-fill must
  never run unattended unless explicitly turned on.
- **Stop and surface the error on a failed scan.** When a fetch cycle returns
  the existing `"failed"` outcome (no confidently-sourceable candidate found),
  auto-fill stops and shows the error — same as today's manual flow — rather
  than silently retrying. Avoids burning API budget on repeated failures while
  unattended.

## What's needed
- An "Auto-fill queue" toggle/button on `index.html`, alongside "Fetch new
  article".
- When on: after a successful fetch returns to the queue (per #12's `selected`
  handling) and the pending count is under the 10-proposal cap, automatically
  trigger the next fetch — without needing another click.
- When the cap is reached, or the toggle is turned off, or a `"failed"`/
  `"error"`/`queue_full` event occurs: stop auto-filling and leave the toggle
  in the off state, so it doesn't silently seem "on" after it has actually
  stopped.
- Persisting the toggle state is not required — resets to off on page reload
  (matches "off by default" being the safe default on every fresh session).

## Scope / touch points
- `wiki_cite/templates/index.html` — the toggle control, and the auto-continue
  logic hooked into `handleEvent`'s existing `selected`/`failed`/`error`/
  `queue_full` cases and `refreshFetchButton()` from #12.
- No server-side changes expected — the existing per-fetch cap enforcement
  (`MAX_PENDING_PROPOSALS`, `queue_full` event) from #12 already provides the
  stopping condition; this is purely a client-side auto-continue loop.

## Complexity
Standard — contained to one file, both open design questions already resolved,
reuses #12's existing cap/event machinery directly.
