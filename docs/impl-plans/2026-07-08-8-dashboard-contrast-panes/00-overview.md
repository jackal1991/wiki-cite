# Impl Plan — Issue #8: Dashboard WCAG contrast + separate Wikipedia/activity panes

**Issue doc:** `docs/issues/8-dashboard-contrast-panes.md`
**Branch:** `feat/8-dashboard-contrast-panes`
**Worktree:** `.worktrees/8-dashboard-contrast-panes`
**Complexity:** Standard (issue doc is the design)
**Date:** 2026-07-08

## Goal
Two independent UI fixes to the Flask review dashboard:
1. Bring text/UI colors and focus indicators up to WCAG 2.1 AA (4.5:1 normal
   text, 3:1 large text / UI components / focus rings).
2. Split the working-view's single dark terminal container into two clearly
   delineated panes: the Wikipedia article-content preview vs. the agent's live
   "over-the-shoulder" activity feed.

## Scope of change (files)
- `wiki_cite/templates/base.html` — the entire `<style>` block lives here; both
  color fixes and focus styles land here, plus the two-pane CSS.
- `wiki_cite/templates/index.html` — working-view markup restructured into two
  panes; a handful of inline color literals fixed; no change to the SSE event
  contract or the `scan`/`agentLog` data flow.
- `wiki_cite/templates/review.html` — scope decision only (see Phase 3); a few
  inline color literals fixed.
- `wiki_cite/web_app.py` — **not touched.** Both panes are already fed by the
  existing event stream (`preview` → article pane, log events → activity pane),
  so no server change is needed. This deliberately avoids the merge conflict
  with #5 flagged in the issue's conflict gate.

## Key decision: what "separate panes" means
Applies to `index.html`'s `#working-view` only. The article wikitext preview
(`.agent-view-body`) and the live activity log (`.agent-log`) are today stacked
inside one `.agent-view` box; they will become two visually separated, labeled
panes. `review.html` does **not** get a live feed — the SSE stream only runs
during fetch, and by the time a proposal is under review the agent is no longer
running (rationale recorded in Phase 3).

## Contrast baseline (measured, sRGB, against actual backgrounds)
All flagged foreground/background pairs fail today:

| Selector | Color / bg | Ratio | Need |
|---|---|---|---|
| `.muted` | `#8a917f` / `#fff` | 3.26 | 4.5 |
| `.muted` | `#8a917f` / cream `#f3f1e9` | 2.88 | 4.5 |
| `.queue-meta .when` | `#9aa08d` / `#fff` | 2.70 | 4.5 |
| `.working-note` | `#7a8271` / `#f7f6ee` | 3.68 | 4.5 |
| `.pipe-label` (pending) | `#aab09e` / `#fff` | 2.23 | 4.5 |
| `.pipe-marker.pending` | `#c7c3b4` / `#f4f2e9` | 1.57 | 3.0 (UI) |
| `.pipe-detail` | `#8a917f` / `#fff` | 3.26 | 4.5 |
| `.btn-primary:disabled` text | `#9aa08d` / `#eceadf` | 2.23 | 4.5 |
| `.source-card-site` | `#8a917f` / `#fbfdfb` | 3.19 | 4.5 |
| `.article-meta .rev` | `#8a917f` / `#fff` | 3.26 | 4.5 |
| inline `#7a8271` (index/review) | / `#fff` | 3.99 | 4.5 |

`.btn-ghost` `#5c6653` on `#f1efe6` already passes (5.24) — leave it.

## Recommended replacement token
`#656b5c` — a single darker olive-gray that clears 4.5:1 on **every** light
surface in the app (white 5.51, cream 4.87, working-note 5.08, pending 4.91,
disabled-button 4.57). Use it as the unified "muted text" color. Where a pair
needs a hair more headroom or a different hue, `#5f6656` / `#5c6653` also pass.
These are recommendations — the implementor may pick equivalent values as long
as the Phase 1 verification script confirms ≥ the required ratio.

## Phases
1. `phase-1-contrast.md` — color remediation + focus indicators (base.html; inline literals).
2. `phase-2-panes.md` — split working view into article pane + activity pane (index.html + base.html CSS).
3. `phase-3-review-and-verify.md` — review.html scope decision + full verification & sign-off.

## Verification (no automated template tests exist)
There is no `test_web_app.py` and templates are untested, so verification is:
- a contrast-ratio calculation over the final chosen colors (script in Phase 1),
- launching `uv run wiki-cite web` and exercising fetch + review by eye
  (keyboard-tab through controls to confirm focus rings),
- `uv run pytest` and `uv run ruff check .` to confirm no regressions elsewhere.
