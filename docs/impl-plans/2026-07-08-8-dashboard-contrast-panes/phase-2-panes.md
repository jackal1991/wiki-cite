# Phase 2 — Split the working view into two delineated panes

**Files:** `wiki_cite/templates/index.html` (markup + one JS selector note),
`wiki_cite/templates/base.html` (CSS).
**No `web_app.py` / SSE change.** Both panes are driven by the existing event
stream: `candidate.preview` feeds the article pane, log events feed the activity
pane. The `scan` object and `agentLog` array stay exactly as they are.

## Current structure (index.html ~lines 39–49)
```
.agent-view                      (one dark terminal box)
├─ .agent-view-bar               (title bar: dots, filename, "live")
├─ .agent-view-body #agent-body  (ARTICLE wikitext preview — renderViewport)
└─ .agent-log #agent-log         (ACTIVITY feed — pushLog)
```
The article preview and the activity log are stacked inside one container with
only a hairline border between them — the issue's core "not split into distinct
panes" finding.

## Target structure
Two sibling panes, each with its own header/label, visually separated (gap +
independent rounded borders), so a reviewer reads article content on one side
and agent activity on the other. Keep the same dark terminal aesthetic.

```
.agent-panes                     (flex/grid wrapper, gap between panes)
├─ .agent-pane.article-pane
│   ├─ .agent-pane-head          ("Article source" + filename + live dot)
│   └─ .agent-view-body #agent-body     (unchanged id — renderViewport writes here)
└─ .agent-pane.activity-pane
    ├─ .agent-pane-head          ("Agent activity")
    └─ .agent-log #agent-log            (unchanged id — pushLog writes here)
```

### Layout guidance
- On wide viewports (the working view is capped at `max-width:680px` via
  `.working-wrap`), stack the two panes vertically with a clear gap and distinct
  borders, OR place side-by-side if width allows — but given the 680px cap,
  **vertical stack with clear separation** is the pragmatic default. Each pane
  gets its own `border`, `border-radius`, and title row so they read as two
  documents, not one scroll.
- Preserve the existing `min-height`/`max-height` + `overflow-y:auto` on
  `#agent-body` (120/210px) and `#agent-log` (128px) so neither pane grows
  unbounded.
- Keep the "live" pulse indicator on the **article pane** head (that's the pane
  that streams the wikitext); the activity pane head can carry a static label.

## index.html changes
1. Replace the single `.agent-view` block (lines 39–49) with the two-pane
   markup above. **Preserve the ids `agent-body`, `agent-log`, and `agent-file`**
   — the JS (`renderViewport`, `pushLog`) selects them by id and must keep
   working untouched.
2. The `agent-view-file` filename span moves into the article pane head. The
   `.agent-view-live` badge moves with it.
3. No JS logic change is required. If `.agent-view` is referenced by any selector
   in JS, re-point it; grep confirms it is only used as a CSS container, not a JS
   query target (`renderViewport`/`pushLog` use `getElementById`). Verify with
   `grep -n "agent-view" wiki_cite/templates/index.html` before finishing.

## base.html CSS changes
- Add `.agent-panes`, `.agent-pane`, `.article-pane`, `.activity-pane`,
  `.agent-pane-head` rules. Reuse the existing dark palette
  (`#0f3524` body, `#123c2a` header bar, `#1c5138` borders, `#8fc4a3`/`#9fd0b0`
  labels) so it matches the current terminal look.
- The existing `.agent-view*` and `.agent-log*` rules (lines ~243–276) can be
  largely reused: keep `.agent-view-body`, `.agent-view-title`, `.agent-log`,
  `.agent-log-line` and their children unchanged (the JS emits that markup).
  Retire only the outer `.agent-view` / `.agent-view-bar` wrapper styling that no
  longer maps to the new structure, folding what's needed into `.agent-pane` /
  `.agent-pane-head`.
- Pane-head label text must meet 3:1 on the dark header (`#8fc4a3` on `#123c2a`
  ≈ passes; verify any new label color you introduce with the Phase 1 script).
- Give the two panes a visible gap (e.g. `gap:14px` on `.agent-panes`) and each
  pane its own `border:1px solid #123c2a; border-radius:11px` so the separation
  is unmistakable.

## Accessibility note
Label each pane for screen readers: give the wrapper `role="group"` /
`aria-label` or a visible heading per pane (the `.agent-pane-head` text serves
as the visible label). The live-updating regions can carry `aria-live="polite"`
on `#agent-log` so activity is announced without stealing focus — optional but
cheap and on-theme for an accessibility issue.

## Done when
- The working view shows two clearly separated, individually-labeled panes:
  article source vs. agent activity.
- A fetch run still streams wikitext into the article pane and log lines into the
  activity pane (ids preserved; SSE untouched).
- No `web_app.py` diff.
- `grep -n "agent-view" wiki_cite/templates/index.html` shows no dangling JS
  reference to the removed wrapper.
