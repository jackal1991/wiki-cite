# Phase 1 — WCAG AA contrast remediation + focus indicators

**Files:** `wiki_cite/templates/base.html` (primary), `wiki_cite/templates/index.html`,
`wiki_cite/templates/review.html` (inline color literals only).
**No behavior change** — colors and focus outlines only.

## 1a. Fix failing text colors (base.html `<style>`)
Replace each flagged low-contrast color. Recommended unified token `#656b5c`
(clears 4.5:1 on every light surface; see overview table). Apply to:

- `.muted { color: #8a917f; }` → `#656b5c` (line ~69). Used on both white and
  cream — the recommended token covers both.
- `.queue-meta .when { color: #9aa08d; }` → `#656b5c` (line ~146).
- `.article-meta .rev { color: #8a917f; }` → `#656b5c` (line ~151).
- `.source-card-site { color: #8a917f; }` → `#656b5c` (line ~209).
- `.working-note { color: #7a8271; }` → `#656b5c` (line ~240).
- `.pipe-detail { color: #8a917f; }` → `#656b5c` (line ~288).
- `.pipe-label { color: #aab09e; }` (pending default) → `#656b5c` (line ~285).
  Note the `done`/`active` overrides (`#3a4433`, `#14432f`) already pass — keep them.
- `.btn-primary:disabled` / `.is-disabled` text `#9aa08d` → `#656b5c`
  (line ~81); background `#eceadf` stays. `#656b5c` on `#eceadf` = 4.57.

## 1b. Fix the pending pipeline marker (UI component, needs 3:1)
`.pipe-marker.pending { background:#f4f2e9; border:1.5px solid #ddd9cc; color:#c7c3b4; }`
(line ~284). The `#ddd9cc` border on `#f4f2e9` and the `#c7c3b4` glyph both fall
under 3:1. Pending markers render empty (no glyph text) so the *border* is the
visible UI element: darken the border to at least 3:1 vs the card white/cream
(e.g. `#b7b3a4` → verify) and darken the `color` to match the label token so any
future glyph is legible. Verify with the script in 1d.

## 1c. Add focus indicators (currently none exist anywhere)
There are no `:focus` / `:focus-visible` rules in base.html. Add a single shared
rule near the top of the `<style>` block (after the `a:hover` rule, ~line 22).
Requirement: focus ring ≥ 3:1 against adjacent colors (WCAG 2.4.11 / non-text).

Add:
```css
:focus-visible {
    outline: 2px solid #14432f;
    outline-offset: 2px;
    border-radius: 4px;
}
```
- `#14432f` (the topbar green) gives 11.2:1 on white and 9.9:1 on cream — well
  clear of 3:1, and reads as on-brand.
- `outline-offset` keeps the ring off tight-radius buttons.
- Because controls on the **dark** topbar/terminal also need a visible ring,
  add a light-on-dark override for those contexts:
```css
.topbar :focus-visible,
.agent-view :focus-visible { outline-color: #eaf3ec; }
```
  (The topbar has the `Human review required` pill and the terminal is
  non-interactive today, but this future-proofs any control placed on dark.)
- Do **not** blanket-remove default outlines; only style `:focus-visible` so
  mouse clicks don't show a ring but keyboard nav does.

## 1d. Inline color literals in index.html / review.html
Several muted grays are hard-coded in inline `style=` attributes and JS strings.
Fix the ones on light backgrounds:
- `index.html` line ~13: `color:#7a8271` (fetch caption) → `#656b5c`.
- `index.html` line ~318: `color:#7a8271` (empty-queue panel) → `#656b5c`.
- `review.html` line ~210: `color:#7a8271` (loading source preview) → `#656b5c`.
- `review.html` `.loading` uses class `#7a8271` via base.html `.loading`
  (line ~294) → `#656b5c`.
- Leave dark-terminal literals (`#6f9a80`, `#8fc4a3`, etc. on `#0f3524`) for
  Phase 2 — they sit on the dark background and are handled with the pane work;
  spot-check them there.

Do **not** hunt every literal blindly — target the ones enumerated in the issue
and the overview table. `#5c6653`, `#6b7263`, `#55604f`, `#6b7263`-family text
already passes; don't churn passing values.

## 1e. Verification script (run and paste output into the PR)
Write a throwaway script under the scratchpad (not committed) that recomputes the
ratio for every color you changed against its real background and asserts the
threshold. Reference implementation:
```python
def lum(h):
    h=h.lstrip('#'); r,g,b=[int(h[i:i+2],16)/255 for i in (0,2,4)]
    f=lambda c: c/12.92 if c<=0.03928 else ((c+0.055)/1.055)**2.4
    R,G,B=f(r),f(g),f(b); return 0.2126*R+0.7152*G+0.0722*B
def ratio(fg,bg):
    a,b=lum(fg),lum(bg); a,b=max(a,b),min(a,b); return (a+0.05)/(b+0.05)
```
Every normal-text pair must be ≥ 4.5; every UI/large pair ≥ 3.0. If any chosen
color misses, darken it and re-run before moving on.

## Done when
- All colors in the overview table (and the pending marker border) meet their
  threshold, confirmed by the script.
- A visible focus ring appears when tabbing through buttons/links on both light
  and dark surfaces.
- `uv run ruff check .` clean (templates aren't linted, but confirm nothing else
  regressed).
