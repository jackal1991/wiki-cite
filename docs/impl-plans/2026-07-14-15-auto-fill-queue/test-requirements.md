# Test Requirements — Issue #15 Auto-fill queue

The issue doc has no labeled "Acceptance criteria" block; the ACs below are derived
verbatim from its **What's needed** and **Design decisions** sections. There is **no
automated frontend test harness** in this repo (confirmed during #12). All ACs are
verified manually / via Playwright driving `wiki_cite/templates/index.html`. The
Python `uv run pytest` suite is only a regression guard — no server-side code changes
in this issue.

| AC | Requirement (from issue #15) | Phase | Verification | Notes |
|----|------------------------------|-------|--------------|-------|
| AC1 | An "Auto-fill queue" toggle/button appears on `index.html`, alongside "Fetch new article", off by default. | 1 | Manual / Playwright: load the dashboard; assert `#auto-fill-btn` renders next to `#fetch-btn`, reads "Auto-fill: off", `aria-pressed="false"`. | Manual — no JS test harness. |
| AC2 | When on, after a successful fetch returns to the queue and the pending count is under the 10 cap, the next fetch triggers automatically without a click. | 1 | Manual / Playwright: enable toggle below cap; assert a fetch starts unclicked and, after each `selected`, another starts automatically. | Manual — full loop drives paid agent fetches; use a stubbed/short-lived backend where possible. |
| AC3 | When the cap is reached, auto-fill stops and the toggle returns to off. | 1 | Manual / Playwright: pre-load the queue near 10; run the loop to the cap; assert toggle shows "off" and `#fetch-btn` is disabled with the cap note. | Manual — exercise against a queue pre-seeded near `MAX_PENDING_PROPOSALS`. |
| AC4 | When the toggle is turned off (manually), auto-filling stops; the in-flight fetch completes but no new fetch starts. | 1 | Manual / Playwright: toggle off mid-loop; assert no further `/api/fetch-article/stream` opens after the current one closes. | Manual. |
| AC5 | On a `failed`/`error`/`queue_full` outcome (or lost connection), auto-fill stops, the toggle shows off, and the error is surfaced. | 1 | Manual / Playwright: force each terminal event; assert `setAutoFill(false)` state (toggle "off") and the error banner appears. | Manual — may need a stubbed backend to force `failed`/`error`. |
| AC6 | Toggle state is not persisted — resets to off on page reload. | 1 | Manual / Playwright: enable toggle, reload; assert it reads "off". Also grep the diff: no `localStorage`/`sessionStorage`. | Manual + static check. |
| AC7 | Only one fetch runs at a time (sequential-only Wikipedia constraint). | 1 | Manual / Playwright: during the loop assert at most one open `EventSource` / one in-flight `/api/fetch-article/stream` at any moment. | Manual — the loop only fires from the idle `selected` handler, so single-stream is structural. |

## Commands

- `uv run pytest` — regression guard (expected: all pass; no behavior added here is Python-tested).
- `uv run wiki-cite web` — launch the dashboard for manual / Playwright verification.
