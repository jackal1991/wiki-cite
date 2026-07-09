# Phase 4: Dashboard UI (search-and-select category filter)

**Goal:** Give the operator a panel on the dashboard showing the active include/exclude
categories as removable chips, with a search-as-you-type box (backed by Phase 2) to add
new ones — no free-text entry of unvalidated category names.

**Satisfies:** AC5 (dashboard UI). Depends on Phase 2 (`/api/categories/search`) and
Phase 3 (`GET`/`POST /api/settings/categories`).

## Context (verified)
- The dashboard is `wiki_cite/templates/index.html`, extending
  `wiki_cite/templates/base.html`. Content goes in `{% block content %}`
  (`index.html:5`), scripts in `{% block scripts %}` wrapped in `{% raw %}`
  (`index.html:58-59`) so `{{ }}` in JS isn't parsed by Jinja.
- `base.html` exposes `{% block extra_styles %}` (`base.html:296`) for page-scoped CSS,
  and already defines `.panel`, `.btn-primary`, `.eyebrow`, `.section-head`, `.banner`,
  `.muted` used by `index.html`. Reuse these classes for visual consistency.
- Existing JS idioms in `index.html`: `escapeHtml()` (`index.html:78-82`),
  `fetch('/api/...')` with `async/await` (`loadProposals`, `index.html:310-325`), and
  DOM building via template strings. Follow these — no framework, no build step, no
  external libraries (CSP / self-contained page).
- The "Fetch new article" button and controls sit in the top `.panel`
  (`index.html:6-15`). Place the category filter as a sibling panel there so it's
  visible before the operator clicks fetch.

## Changes (all in `wiki_cite/templates/index.html`)

### Markup — add a filter panel inside `#queue-view` (after the intro `.panel`, ~`index.html:15`)

```html
<div class="panel" id="category-filter" style="margin-top:18px;">
    <div class="eyebrow">Category filter</div>
    <p class="muted" style="font-size:13px; margin:4px 0 12px;">
        Scope which categories the agent draws candidates from. Applies to the next fetch;
        resets to config defaults on server restart.
    </p>

    <label class="filter-label">Include (article must be in one of these)</label>
    <div class="chip-row" id="include-chips"></div>

    <label class="filter-label">Exclude (article must not be in any of these)</label>
    <div class="chip-row" id="exclude-chips"></div>

    <div class="cat-search">
        <input type="text" id="cat-search-input" autocomplete="off" spellcheck="false"
               placeholder="Search Wikipedia categories…">
        <div class="cat-search-results" id="cat-search-results" style="display:none;"></div>
    </div>
    <div id="filter-status" class="muted" style="font-size:12px; margin-top:8px;"></div>
</div>
```

A single search box with two "add to include / add to exclude" affordances per result
row (a result renders two small buttons, or the row adds to whichever target is
currently selected via a small toggle). Keep it simple: each result row shows the
category name plus `+ include` / `+ exclude` buttons.

### Styles — add to `{% block extra_styles %}` (create the block in index.html)
Minimal CSS for `.chip-row`, `.chip` (with an `×` remove button), `.filter-label`,
`.cat-search`, `.cat-search-results`, `.cat-search-result`. Reuse the existing color
palette (greens `#187a49` etc. seen in `base.html:79`). Chips must have an accessible
remove control (a `<button>` with `aria-label`, not a bare span) — the project has an
open UI a11y/contrast concern, so use real buttons and sufficient contrast.

### Script — add to the `{% raw %}<script>` block
State + functions (mirror existing style):

```javascript
let categoryFilter = { include: [], exclude: [] };

async function loadCategoryFilter() {
    const res = await fetch('/api/settings/categories');
    categoryFilter = await res.json();
    renderChips();
}

function renderChips() {
    renderChipRow('include-chips', categoryFilter.include, 'include');
    renderChipRow('exclude-chips', categoryFilter.exclude, 'exclude');
}
// renderChipRow builds <button class="chip"> name <span aria-hidden>×</span></button>
// wired to removeCategory(kind, name)

async function saveCategoryFilter() {
    const res = await fetch('/api/settings/categories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(categoryFilter),
    });
    const data = await res.json();
    if (!res.ok) { setFilterStatus(data.error || 'Save failed', true); return; }
    categoryFilter = data;
    renderChips();
    setFilterStatus('Saved — applies to your next fetch');
}

function addCategory(kind, name) {
    if (!categoryFilter[kind].includes(name)) categoryFilter[kind].push(name);
    saveCategoryFilter();
}
function removeCategory(kind, name) {
    categoryFilter[kind] = categoryFilter[kind].filter(c => c !== name);
    saveCategoryFilter();
}

// Debounced search against Phase 2 endpoint; only add via a returned result
// (no free-text add — the input never directly adds a category).
let searchTimer = null;
function onCatSearchInput(e) {
    const q = e.target.value.trim();
    clearTimeout(searchTimer);
    if (!q) { hideResults(); return; }
    searchTimer = setTimeout(() => runCatSearch(q), 200);
}
async function runCatSearch(q) {
    const res = await fetch('/api/categories/search?q=' + encodeURIComponent(q));
    if (!res.ok) { hideResults(); return; }
    const { categories } = await res.json();
    renderResults(categories); // each row: name + "+ include" / "+ exclude" buttons
}
```

- Call `loadCategoryFilter()` alongside `loadProposals()` at the bottom of the script
  (`index.html:327`).
- Wire `oninput` of `#cat-search-input` to `onCatSearchInput`.
- All rendered category names / result text go through `escapeHtml()`.
- The input is search-only: a category is added **only** by clicking a search result
  (AC5.1 — no unvalidated free-text names).

## Manual verification (required — this is a UI change)
Per project convention, exercise the feature in a browser, not just via tests:
1. `uv run wiki-cite web`, open `http://localhost:5000`.
2. Confirm the Category filter panel loads with the config-default chips.
3. Type a prefix (e.g. "History") → results appear from the live endpoint; click
   `+ include` → a chip appears and persists across a page reload (until server restart).
4. Remove a chip via its `×` button → it disappears and the POST succeeds.
5. Confirm no way to add a raw typed string that isn't a returned search result.
6. If the app can't be launched in this environment, say so explicitly rather than
   claiming success.

> Note: `create_app()` opens real `mwclient.Site` connections
> (`web_app.py:35,37`), so the live category search hits Wikipedia. That's expected for
> manual verification.

## Done when
- The panel renders, loads current settings, adds/removes chips via the search-backed
  flow, and persists via `POST /api/settings/categories`.
- No free-text category entry path exists (AC5.1).
- Manual browser verification done (or its absence stated explicitly).
- `uv run ruff check .` clean (no Python changes expected in this phase, but run it).
