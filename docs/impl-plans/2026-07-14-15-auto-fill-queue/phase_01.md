# Phase 1: Auto-fill queue toggle

**Goal:** Add an "Auto-fill queue" toggle that automatically triggers successive fetches until the 10-proposal cap is reached or the user turns it off.
**AC Coverage:** AC1, AC2, AC3, AC4, AC5, AC6, AC7 (see `test-requirements.md`)

---

## Context

Issue #12 (merged) introduced a 10-proposal pending cap and the supporting
client machinery in `wiki_cite/templates/index.html`:

- `MAX_PENDING_PROPOSALS = 10` in `web_app.py`; endpoint
  `GET /api/proposals/pending-count` returns `{pending, cap, at_cap}`.
- The server emits a `queue_full` SSE event and refuses to fetch when already at cap.
- `refreshFetchButton()` (index.html ~L236) async-fetches the pending count and
  disables `#fetch-btn` + shows `#fetch-cap-note` when `at_cap` is true.
- `returnToQueue(message)` (~L260) closes the SSE source, swaps back to the queue
  view, calls `refreshFetchButton()`, and shows an error banner when a message is passed.
- `handleEvent` (~L273) terminal cases:
  - `selected` (~L340): marks the pipeline done, closes the SSE source, then
    `setTimeout(() => { returnToQueue(); loadProposals(); }, 1100)`.
  - `failed` (~L351), `error` (~L357), `queue_full` (~L360): each calls
    `returnToQueue(<message>)`.
- `fetchNewArticle()` (~L366) early-returns if `#fetch-btn` is disabled, otherwise
  disables the button, resets `scan`, and opens `new EventSource('/api/fetch-article/stream')`.
- Relevant globals (~L152-155): `const scan`, `let agentLog`, `let evtSource`.
- The `<script>` body is wrapped in `{% raw %}`…`{% endraw %}`; init at the bottom
  (~L575-577) runs `loadProposals()`, `loadCategoryFilter()`, `refreshFetchButton()`.

Today "Fetch new article" fires exactly once per click. This phase adds a client-side
auto-continue loop. **No server-side changes** — the existing cap enforcement and
`queue_full` event already provide the stopping condition. One fetch runs at a time
(a fetch is only ever kicked off from an idle queue view), which preserves the
sequential-only Wikipedia constraint.

## Implementation

### Toggle control

**Files:**
- Modify: `wiki_cite/templates/index.html`

Add an "Auto-fill queue" toggle button in the controls row that currently holds
`#fetch-btn` (the `<div style="margin-top:22px; …">` at ~L59-64), placed immediately
after `#fetch-btn`. Use a `<button>` with `type="button"`, `id="auto-fill-btn"`,
`aria-pressed="false"`, and an `onclick="toggleAutoFill()"` handler. Give it a
distinct look from the primary fetch button (it is a secondary control) — reuse an
existing ghost/secondary style if one fits, or add a small style block consistent
with the page's palette. Default label text: `Auto-fill: off`.

Rationale for a button + `aria-pressed` over a checkbox: matches the existing
button-driven controls in this template and gives an accessible pressed state.

### Auto-continue logic

**Files:**
- Modify: `wiki_cite/templates/index.html`

**What to implement:**

1. **State + UI helper.** Add a global `let autoFillEnabled = false;` alongside the
   other globals (~L155). Add a helper that is the single source of truth for the
   toggle's state and appearance:

   ```js
   function setAutoFill(on) {
       autoFillEnabled = on;
       const btn = document.getElementById('auto-fill-btn');
       btn.setAttribute('aria-pressed', on ? 'true' : 'false');
       btn.textContent = on ? 'Auto-fill: on' : 'Auto-fill: off';
       btn.classList.toggle('is-on', on);   // style hook; optional if unstyled
   }
   ```

   Every stop condition and the manual toggle go through `setAutoFill` so the
   button never shows "on" once the loop has actually stopped (AC3, AC4, AC5).

2. **Manual toggle handler.** `toggleAutoFill()` flips the state. When turning it
   **on**, immediately try to start the loop (so the user does not also have to
   click "Fetch new article"); when turning it **off**, just stop — do not cancel
   any in-flight fetch, only prevent the next one:

   ```js
   function toggleAutoFill() {
       if (autoFillEnabled) { setAutoFill(false); return; }
       setAutoFill(true);
       // Only kick off immediately if we're idle in the queue view and a fetch
       // isn't already running. Starting mid-fetch is unnecessary — the in-flight
       // fetch's 'selected' handler will pick up the loop.
       if (!evtSource) maybeAutoContinue();
   }
   ```

   Note: leave `#auto-fill-btn` clickable during a fetch so the user can turn the
   loop off mid-flight. The in-flight fetch completes; because `autoFillEnabled`
   is then false, no further fetch is triggered (AC4, AC7).

3. **The auto-continue step.** `maybeAutoContinue()` decides whether to fire the
   next fetch. It must re-check the pending count against the cap itself rather
   than trusting `#fetch-btn`'s disabled state, and it stops (turns the toggle
   off) when the cap is reached:

   ```js
   async function maybeAutoContinue() {
       if (!autoFillEnabled) return;
       try {
           const res = await fetch('/api/proposals/pending-count');
           const { at_cap } = await res.json();
           if (at_cap) { setAutoFill(false); return; }   // cap reached → stop (AC3)
       } catch (err) {
           // Network hiccup checking the count: stop rather than loop blindly.
           setAutoFill(false);
           return;
       }
       // Re-check the flag: the user may have toggled off during the await.
       if (autoFillEnabled) fetchNewArticle();
   }
   ```

4. **Hook the success path.** In `handleEvent`'s `selected` case, the existing
   `setTimeout(() => { returnToQueue(); loadProposals(); }, 1100)` returns to the
   queue after a successful fetch. Extend that callback so that, once back in the
   queue, an enabled loop continues:

   ```js
   setTimeout(() => {
       returnToQueue();
       loadProposals();
       maybeAutoContinue();   // continue the loop if auto-fill is on (AC2)
   }, 1100);
   ```

   `maybeAutoContinue` no-ops when `autoFillEnabled` is false, so the manual
   single-click flow is unchanged.

5. **Hook the stop paths.** In the `failed`, `error`, and `queue_full` cases of
   `handleEvent`, turn the toggle off before (or right alongside) the existing
   `returnToQueue(...)` call so a failed scan, an error, or hitting the cap stops
   the loop and leaves the toggle visibly off, while the error banner still shows
   via `returnToQueue` (AC5, AC3). For example, for `failed`:

   ```js
   case 'failed': {
       setAutoFill(false);
       let message = evt.error || 'No article found';
       if (evt.skipped && evt.skipped.length) message += ` (skipped: ${evt.skipped.join(', ')})`;
       returnToQueue(message);
       break;
   }
   ```

   Apply the same `setAutoFill(false)` to the `error` and `queue_full` cases.
   Also add `setAutoFill(false)` to the `evtSource.onerror` handler's
   lost-connection branch in `fetchNewArticle()` (~L386-388), so a dropped stream
   stops the loop too.

6. **No persistence.** Do not read or write `localStorage`/`sessionStorage` for the
   toggle. `autoFillEnabled` defaults to `false` and the button renders "off" on
   every load, so a page reload always resets to off (AC6). Do **not** add a call
   to `setAutoFill` in the bottom init block — the default HTML (`aria-pressed="false"`,
   `Auto-fill: off`) already reflects the off state.

**Tests:**
No automated frontend test harness exists in this repo (confirmed during #12).
Verify manually / via Playwright per `test-requirements.md`. There are no
server-side changes, so the Python test suite is a regression guard only.

---

## Verification

1. Lint the template is not applicable (ruff covers Python only); run the existing
   suite as a regression guard:
   - Run: `uv run pytest`
   - Expected: all tests pass (no server changes were made).
2. Manual / Playwright check of the toggle behavior — launch `uv run wiki-cite web`
   and, following `test-requirements.md`:
   - The "Auto-fill: off" button renders next to "Fetch new article" and reads
     "off" on load (AC1, AC6).
   - Clicking it flips to "Auto-fill: on" (aria-pressed="true") and, when the queue
     is below the cap, starts a fetch without a further click (AC2).
   - After a successful fetch it returns to the queue and automatically starts the
     next fetch (AC2, AC7 — only one stream open at a time).
   - Clicking the toggle again turns it off; the current fetch (if any) finishes but
     no new fetch starts (AC4).
   - When the pending count reaches 10, the loop stops and the toggle shows "off"
     (AC3); the fetch button is disabled with the cap note (existing #12 behavior).
   - On a `failed`/`error`/`queue_full`/lost-connection outcome, the loop stops, the
     toggle shows "off", and the error banner appears (AC5).
   - Reload the page: the toggle is back to "off" (AC6).

   Full end-to-end auto-fill drives real (paid) agent fetches; prefer verifying the
   toggle state transitions and the single-open-stream invariant with a stubbed or
   short-lived backend, and confirm the cap-stop against a queue pre-loaded near 10.

## Commit

`feat: add auto-fill queue toggle that fetches until the pending cap`
