# Issue #8 — Dashboard: WCAG contrast compliance + separate Wikipedia/activity-feed panes

**Status:** Ready
**Complexity:** Standard
**GitHub:** https://github.com/jackal1991/wiki-cite/issues/8

## Summary
The Flask review dashboard has two UI problems to fix:

1. **Accessibility / contrast** — audit fonts, colors, and interactive elements
   against WCAG 2.1 AA contrast ratios (4.5:1 normal text, 3:1 large
   text/UI components/focus indicators) and fix failures.
2. **Layout** — separate the Wikipedia article content and the agentic
   "over-the-shoulder" activity feed into clearly delineated panes so a
   reviewer can track each independently.

## Current layout structure
All styling is inline in a single `<style>` block in
`wiki_cite/templates/base.html` (no external CSS file). Three templates share
that shell (dark-green topbar, single column, `max-width: 900px`):

- **`base.html`** — shared shell + all CSS for every view.
- **`index.html`** — renders two views toggled via `display:none`:
  - `#queue-view`: the proposal queue.
  - `#working-view`: the over-the-shoulder agent activity view, shown while
    fetching. Consumes the SSE stream (`EventSource('/api/fetch-article/stream')`,
    served by `web_app.py` `fetch_article_stream` / `scan_events`).
- **`review.html`** — per-proposal review page (`/review/<proposal_id>`):
  article header, trust strip, per-edit diffs, combined diff, sticky push bar.
  Has **no** live activity feed.

**Key pane-separation finding:** in `index.html`'s working view, the article's
wikitext preview excerpt (`.agent-view-body`, from `scan.preview`) and the
agent's live activity log (`.agent-log`) are stacked inside a single dark
terminal container (`.agent-view`) — interleaved, not split into distinct
panes. The live feed also exists only on the fetch/queue page, never on the
review page — whoever implements this should decide whether "separate panes"
means splitting article-preview vs. activity-log within the working view,
surfacing article content alongside activity on the review page, or both.

## Contrast issues spotted (starting points, not exhaustive)
- Low-contrast muted grays on the cream/white backgrounds: `.muted` `#8a917f`,
  `.queue-meta .when` `#9aa08d`, `.working-note` `#7a8271`, pending
  `.pipe-label` `#c7c3b4`, `.pipe-detail` `#8a917f` — verify against 4.5:1 and
  fix failures.
- **No `:focus` / `:focus-visible` styles anywhere in `base.html`** — keyboard
  focus indicators for buttons/links appear entirely missing (WCAG 2.4.7 /
  non-text contrast requirement).

## Scope / touch points
- `wiki_cite/templates/base.html` — CSS: contrast fixes, focus styles.
- `wiki_cite/templates/index.html` — split `.agent-view` into distinct
  article-pane / activity-log-pane.
- `wiki_cite/templates/review.html` — decide/implement whether it gets a
  live-activity pane too (see key finding above).
- `wiki_cite/web_app.py` — only if SSE event handling needs to change to feed
  two independent regions.

## Notes
- Filed by supervisor agent; GitHub label application (`status/ready`) failed
  — same permissions error as #6/#7 (`jgreaney-HCG` lacks
  `AddLabelsToLabelable` on this repo). No labels currently on this issue.
