# Phase 2: "Next in queue →" control on the review page

**Goal:** Add a "Next in queue →" control to `review.html` that jumps straight to the next pending proposal (via the Phase 1 endpoint), and degrades to a "Back to queue" affordance when there is no other pending proposal — never a dead link.
**AC Coverage:** AC1 (control appears on the review page), AC2 (jumps directly to the next pending proposal without a round-trip to `/`), AC4 (no-next state is not a dead link) — see `test-requirements.md`.

---

## Context

`wiki_cite/templates/review.html` is a single-proposal review page. Verified in the
codebase:

- The current proposal id is exposed to the script as
  `const proposalId = '{{ proposal_id }}';` (line 64).
- The page already has a top-of-page back control:
  `<button class="btn-back" onclick="window.location.href='/'">← Back to queue</button>`
  (line 7).
- `loadProposal()` (line 79) fetches `/api/proposals/${proposalId}`, renders the
  page, and reveals `#proposal-content` and `#sticky-bar`. It is invoked once at the
  bottom of the script (line 331).
- Navigation elsewhere in this file is done with `window.location.href = '/'`
  (`rejectAll` line 273, `pushEdits` line 309) — i.e. resolving a proposal returns
  to the queue. This phase adds *lateral* navigation between pending proposals
  **without** resolving the current one.
- `escapeHtml` (line 73) exists for safe interpolation; the new control uses a fixed
  proposal id from the server, but continue to build URLs from that id directly.

Phase 1 added `GET /api/proposals/<proposal_id>/next`, returning
`{"next_id": <id>}` (a pending proposal id) or `{"next_id": null}` (nothing else to
review), and 404 for an unknown id. This phase consumes it.

There is **no automated frontend test harness** in this repo (confirmed during #12
and #15). This phase is verified manually / via Playwright per `test-requirements.md`;
the Phase 1 pytest additions cover the server contract it depends on.

## Implementation

### The control markup

**Files:**
- Modify: `wiki_cite/templates/review.html`

Add a "Next in queue →" control near the existing back button (line 7), so queue
navigation is grouped at the top of the page. Replace the lone back button with a
small nav row holding both controls, e.g.:

```html
<div class="review-nav">
    <button class="btn-back" onclick="window.location.href='/'">← Back to queue</button>
    <a id="next-in-queue" class="btn-ghost" href="#" style="display:none;">Next in queue →</a>
</div>
```

- Reuse the existing `btn-ghost` secondary-button style (already used for
  `#diff-toggle` on this page, line 47) so the control matches the page palette; no
  new CSS is required. Add a minimal flex rule for `.review-nav` (e.g.
  `display:flex; gap:10px; align-items:center;`) if the two controls need spacing —
  keep it consistent with the page.
- Render it hidden by default (`display:none`); the script reveals and targets it
  once it knows whether a next proposal exists. Rendering hidden-first avoids a
  flash of a control that might immediately become "Back to queue".

### Wiring the control

**Files:**
- Modify: `wiki_cite/templates/review.html`

**What to implement:**

Add a function that queries the Phase 1 endpoint and configures the control, and
call it after `loadProposal()` succeeds. It has exactly two visible states:

```js
async function setupNextInQueue() {
    const link = document.getElementById('next-in-queue');
    try {
        const res = await fetch(`/api/proposals/${proposalId}/next`);
        const { next_id } = await res.json();
        if (next_id) {
            // A pending proposal remains: jump straight to it (AC2).
            link.href = `/review/${next_id}`;
            link.textContent = 'Next in queue →';
        } else {
            // Nothing else pending: not a dead link — send back to the queue (AC4).
            link.href = '/';
            link.textContent = 'Back to queue →';
        }
        link.style.display = '';
    } catch (err) {
        // If the lookup fails, leave the control hidden; the top-left
        // "← Back to queue" button is always available as a fallback.
    }
}
```

Design notes:
- **Two states, never a dead link.** When `next_id` is present the control links to
  `/review/<next_id>` (a direct jump, no stop at `/` — AC2). When it is `null`, the
  control becomes a "Back to queue →" link to `/` (AC4). The page already has the
  top-left back button, so the failure branch can simply stay hidden.
- **Anchor, not `window.location` handler.** An `<a href>` gives normal
  link semantics (middle-click, open-in-new-tab) for free and needs no click
  handler; the href is set from the server-provided id.
- **Recompute on load only.** The next-pending target is resolved once when the
  review page loads. Navigating to `/review/<next_id>` is a full page load, so the
  next page recomputes its own "next" — no client-side cache to invalidate. (Since
  each hop re-queries live server state, a proposal resolved in another tab is
  naturally skipped on the next hop.)

**Hook it into load.** In the bottom init (line 331), call `setupNextInQueue()`
after `loadProposal()`:

```js
loadProposal();
setupNextInQueue();
```

`setupNextInQueue` is independent of `loadProposal`'s render (it only touches the
nav control), so it can run concurrently; it does not need to await `loadProposal`.

### Out of scope (guard)

Do **not** add any bulk/batch controls (e.g. "approve all", "skip all", multi-select
across proposals). Per the issue this is single-item navigation only; each edit still
gets individual review before push (AC5 is a scope guard, not a behavior to build).

## Verification

1. Regression guard (no server changes in this phase, but run the suite):
   - Run: `uv run pytest`
   - Expected: all tests pass.
2. Manual / Playwright check — launch `uv run wiki-cite web` and, following
   `test-requirements.md`:
   - With 2+ pending proposals in the queue, open one via "Review →". A
     "Next in queue →" control appears at the top of the review page (AC1).
   - Clicking it loads `/review/<next_id>` directly — the next *pending* proposal —
     without stopping at `/` (AC2). Confirm the loaded proposal is a different,
     pending one and not the one just left.
   - With only one pending proposal (resolve/push the others, or seed a single
     pending proposal), the control reads "Back to queue →" and points at `/` — no
     dead link (AC4).
   - Pushed/rejected proposals in the queue are never the jump target (AC3, backed by
     the Phase 1 `test_next_pending_skips_non_pending` test).

   Full end-to-end fetching drives real (paid) agent runs; prefer verifying the
   navigation against a queue pre-seeded with a couple of pending proposals (and one
   pushed/rejected) rather than fetching live.

## Commit

`feat: add "Next in queue" control to the review page`
