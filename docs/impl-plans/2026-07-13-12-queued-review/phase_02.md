# Phase 2: Queue-first UI — stop force-navigating, make the fetch button cap-aware

**Goal:** After a successful fetch, return to the queue (not the review page) and reflect the pending cap in the "Fetch new article" button; make full-reject on the review page free a queue slot and return to the queue.
**AC Coverage:** AC1 (stop auto-navigating on `selected`; return to queue, refresh list, re-enable button unless at cap), AC3 (button reflects the cap with an explanatory message when full), AC5 (full-reject frees a slot — client half)

---

## Context

Phase 1 added the server pieces: `MAX_PENDING_PROPOSALS = 10`, a `queue_full` terminal event
from `scan_events()`, `GET /api/proposals/pending-count` (`{pending, cap, at_cap}`), and
`POST /api/proposals/<id>/reject` (sets `status = "rejected"`). This phase is frontend-only,
consuming those.

Verified current frontend state:
- `wiki_cite/templates/index.html` `handleEvent`'s `selected` case (lines 317-327) closes the
  SSE and does `setTimeout(() => { window.location.href = '/review/' + evt.proposal_id }, 1100)`
  — this is the force-navigation to remove.
- `returnToQueue(message)` (lines 235-247) hides the working view, shows the queue, and
  **unconditionally** re-enables the fetch button (`btn.disabled = false`). This must become
  cap-aware.
- `fetchNewArticle()` (line 340) disables the button and opens the `EventSource`.
- `loadProposals()` (line 529) fetches `/api/proposals` and renders the queue list; it is
  already called on page load (line 548).
- The fetch button is `#fetch-btn` (index.html line 60), with a sibling `<span>` describing
  it (line 61). Disabled styling already exists: `.btn-primary:disabled, .btn-primary.is-disabled`
  (base.html line 88).
- `wiki_cite/templates/review.html` `rejectAll()` (lines 262-271) loops `reject-edit` over every
  edit but never resolves the proposal or leaves the page. `pushEdits()` (line 296) already
  redirects to `/` after a successful push (line 306).

## Implementation

### index.html — cap-aware fetch button

**Files:**
- Modify: `wiki_cite/templates/index.html`

**What to implement:**

1. Add a small status element for the cap message next to the fetch button. In the markup
   after the `#fetch-btn` button (around line 60-61), add:

   ```html
   <span id="fetch-cap-note" class="muted" style="font-size:13px; color:#8a3b28; display:none;"></span>
   ```

2. Add a `refreshFetchButton()` function (place it near `returnToQueue`, inside the
   `{% raw %}` script block). It queries the pending-count endpoint and enables/disables the
   button accordingly:

   ```javascript
   async function refreshFetchButton() {
       const btn = document.getElementById('fetch-btn');
       const note = document.getElementById('fetch-cap-note');
       try {
           const res = await fetch('/api/proposals/pending-count');
           const { pending, cap, at_cap } = await res.json();
           if (at_cap) {
               btn.disabled = true;
               btn.classList.add('is-disabled');
               note.textContent = `Queue full — ${pending}/${cap} pending. Resolve or push a proposal to fetch more.`;
               note.style.display = '';
           } else {
               btn.disabled = false;
               btn.classList.remove('is-disabled');
               note.style.display = 'none';
           }
       } catch (err) {
           // On failure, fail open: leave the button enabled so the server-side cap still guards.
           btn.disabled = false;
           btn.classList.remove('is-disabled');
           note.style.display = 'none';
       }
   }
   ```

3. Make `returnToQueue(message)` cap-aware. Replace its unconditional re-enable
   (`btn.disabled = false; btn.classList.remove('is-disabled');`, lines ~239-241) with a call
   to `refreshFetchButton()` so the button reflects the cap after returning:

   ```javascript
   function returnToQueue(message) {
       if (evtSource) { evtSource.close(); evtSource = null; }
       document.getElementById('working-view').style.display = 'none';
       document.getElementById('queue-view').style.display = 'block';
       refreshFetchButton();
       if (message) {
           const errorDiv = document.getElementById('error');
           errorDiv.textContent = message;
           errorDiv.style.display = 'block';
       }
   }
   ```

4. Call `refreshFetchButton()` on page load, alongside the existing `loadProposals()` /
   `loadCategoryFilter()` calls (lines 548-549):

   ```javascript
   loadProposals();
   loadCategoryFilter();
   refreshFetchButton();
   ```

### index.html — stop force-navigating on `selected`, return to queue instead

**Files:**
- Modify: `wiki_cite/templates/index.html` — `handleEvent`'s `selected` case (lines 317-327)

**What to implement:**

The proposal is already saved server-side by the time `selected` fires. Instead of navigating
to `/review/<id>`, briefly show the "found" confirmation, then return to the queue, refresh the
proposals list, and let `refreshFetchButton()` (via `returnToQueue`) re-enable or keep the
button disabled per the cap.

Replace the `setTimeout(... window.location.href ...)` (line 326) so the case reads:

```javascript
case 'selected':
    scan.sweeping = false;
    scan.phase = DONE_PHASE;
    document.getElementById('working-title').textContent = `Found ${evt.edit_count} edit${evt.edit_count === 1 ? '' : 's'} on “${evt.title}” — added to your queue`;
    document.getElementById('working-sub').textContent = 'Back to your queue. Review it when you like — nothing is published until you approve it.';
    pushLog('select', '★', `Selected “${evt.title}” · ${evt.edit_count} edits — added to queue`);
    renderViewport();
    renderPipeline();
    if (evtSource) { evtSource.close(); evtSource = null; }
    setTimeout(() => { returnToQueue(); loadProposals(); }, 1100);
    break;
```

Keep the ~1100ms delay so the reviewer sees the "found" confirmation before the view flips
back. `returnToQueue()` (no message) hides the working view and refreshes the button;
`loadProposals()` re-renders the queue list including the new proposal.

### index.html — guard `fetchNewArticle` against a disabled button

**Files:**
- Modify: `wiki_cite/templates/index.html` — `fetchNewArticle()` (line 340)

**What to implement:**

Add an early guard at the top of `fetchNewArticle()` so a click on a disabled/at-cap button is
a no-op (defense in depth alongside the CSS `disabled` state):

```javascript
function fetchNewArticle() {
    const btn = document.getElementById('fetch-btn');
    if (btn.disabled) return;
    // ... existing body (disable button, reset scan state, open EventSource) ...
```

### index.html — handle the `queue_full` event

**Files:**
- Modify: `wiki_cite/templates/index.html` — `handleEvent` switch (lines 250-337)

**What to implement:**

Add a case for the Phase 1 `queue_full` terminal event. It returns to the queue with the
message; `returnToQueue` → `refreshFetchButton` will keep the button disabled since the queue
is still full:

```javascript
case 'queue_full':
    returnToQueue(evt.error || 'Queue is full. Resolve some proposals before fetching more.');
    break;
```

### review.html — full-reject frees a slot and returns to the queue

**Files:**
- Modify: `wiki_cite/templates/review.html` — `rejectAll()` (lines 262-271)

**What to implement:**

After rejecting every edit, call the new proposal-level reject endpoint to mark the whole
proposal `rejected` (freeing its queue slot), then return to the queue:

```javascript
async function rejectAll() {
    if (!confirm('Reject all edits?')) return;
    for (let i = 0; i < proposal.edits.length; i++) {
        try {
            await fetch(`/api/proposals/${proposalId}/reject-edit/${i}`, { method: 'POST' });
            proposal.edits[i].approved = false;
        } catch (error) { /* keep going */ }
    }
    try {
        await fetch(`/api/proposals/${proposalId}/reject`, { method: 'POST' });
    } catch (error) { /* status update best-effort */ }
    window.location.href = '/';
}
```

`pushEdits()` already redirects to `/` after a successful push and the server already sets
`status = "pushed"`, so the push path needs no change — returning to the queue there will show
the freed slot via the now-cap-aware button.

## Tests

No automated frontend tests exist in this project (the JS is untested by design). The Phase 1
pytest additions cover the server contract these UI changes depend on
(`pending-count`, `queue_full`, `reject`). Verify this phase operationally.

Confirm the existing suite still passes (no server route names or shapes changed here):
`uv run pytest`

---

## Verification

Operational (manual) verification — run the dashboard:

```
uv run wiki-cite web
```

Then confirm:
1. Fetch an article; when the agent finds one, the view returns to the **queue** (not
   `/review/<id>`), the new proposal appears in "In your queue", and the fetch button is usable
   again. (AC1)
2. With 10 pending proposals in the queue, the "Fetch new article" button is **disabled** on
   page load with the "Queue full — 10/10 pending…" note. (AC3)
3. On the review page, "Reject all" returns to the queue and the rejected proposal no longer
   counts toward the cap — if it was previously at 10, the fetch button becomes enabled. (AC5)
4. Pushing a proposal likewise frees a slot (already worked server-side; confirm the button
   re-enables on return to the queue). (AC5)

Also run: `uv run pytest` and `uv run ruff check .` — both clean.

## Commit

`feat: return to queue after fetch and reflect the 10-proposal cap in the UI`
