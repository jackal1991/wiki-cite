# Phase 3 — review.html scope decision + full verification & sign-off

**Files:** `wiki_cite/templates/review.html` (decision; inline literal fixes done
in Phase 1). Possibly no functional diff here beyond Phase 1's color fixes.

## review.html decision (record this in the PR body)
The issue leaves open whether the per-proposal review page should also gain a
live-activity pane. **Decision: no live feed on review.html.**

Rationale:
- The SSE stream (`/api/fetch-article/stream` → `scan_events`) runs only during
  the fetch/scan phase. By the time a proposal exists and is opened at
  `/review/<id>`, the agent has finished — there is no live activity to show.
- Surfacing a *replayed* activity log would require persisting per-proposal event
  history and new `web_app.py` endpoints — out of scope for a Standard
  contrast/layout issue, and it would touch `web_app.py` (conflict risk with #5).
- The review page already separates concerns well (article header, trust strip,
  per-edit diffs, combined diff, sticky push bar); the "two panes" requirement is
  satisfied by the working view (Phase 2).

If a future issue wants article-content-beside-activity on the review page, file
it separately; note it as possible follow-up in the PR.

So review.html's only changes in this issue are the Phase 1 inline color fixes.

## Full verification
Run from the worktree root.

1. **Contrast** — re-run the Phase 1 script over every final color; confirm all
   normal text ≥ 4.5:1 and all UI/focus elements ≥ 3:1. Paste the output in the
   PR.
2. **Launch the dashboard** — `uv run wiki-cite web`, open http://localhost:5000:
   - Queue view: muted metadata (`.muted`, `.queue-meta .when`, empty-queue
     panel) is legibly darker; tab through "Fetch new article" and "Review →" and
     confirm a visible focus ring.
   - Trigger **Fetch new article**: the working view shows two separated,
     labeled panes — wikitext streams into the article pane, activity lines into
     the activity pane; pipeline pending labels/markers are legible.
   - Open a proposal at `/review/<id>`: confirm the loading text, source-preview
     caption, and article-meta rev are legible; tab through Approve/Reject/Push
     and confirm focus rings.
   - If no live fetch is available in the environment, say so explicitly rather
     than claiming the panes render — the two-pane markup can still be confirmed
     by inspecting the served HTML.
3. **Regression gates:**
   - `uv run pytest` — full suite green (no template tests exist, but confirm
     nothing else broke).
   - `uv run ruff check .` — clean.
4. Confirm `git diff --stat` shows **no** `wiki_cite/web_app.py` change.

## Commit
Local commit only (project is local-commits-only; do not push). Suggested
message subject: `feat(dashboard): WCAG AA contrast + split working-view panes (#8)`.
Note in the PR/commit body: the review.html no-live-feed decision, that
web_app.py was intentionally untouched, and the contrast-script output.

## Done when
- review.html decision recorded; no web_app.py diff.
- Contrast script passes for all changed colors.
- Dashboard exercised (or the inability to run it stated plainly).
- `uv run pytest` and `uv run ruff check .` both clean.
