# Topic/Category Filtering Design

## Summary
Let operators scope which Wikipedia categories the agent draws candidate articles
from. `config.yaml` sets persistent defaults (`include_categories` /
`exclude_categories`); the dashboard adds a runtime, session-scoped override with
search-and-select so operators only pick valid, existing Wikipedia categories
rather than free-typing names that could silently fail to match anything.

## Definition of Done
- `ArticleSelectionConfig` gains `include_categories: list[str]` and
  `exclude_categories: list[str]` (default `[]` — unchanged behavior).
- `ArticlePicker.is_candidate()` filters using each article's already-fetched
  categories: an exclude match rejects regardless of include; a non-empty
  include list requires overlap to pass.
- A dashboard runtime override lets the operator view/edit the active
  include/exclude lists without restarting the server. It seeds from
  `config.yaml` at startup and lives in memory only — it is never written back
  to `config.yaml` (confirmed with user).
- A category search endpoint (backed by `site.allpages(prefix=q, namespace=14,
  ...)`, the MediaWiki Category namespace) lets the operator find and select
  valid categories by typing a prefix, instead of free-typing exact names.
- Tests: config parsing/defaults; `is_candidate()` filtering (include-only,
  exclude-only, both, neither, exclude-takes-precedence); category search
  endpoint; runtime override endpoint.
- Out of scope: persisting overrides to `config.yaml`; multi-operator
  concurrent-edit conflict handling (single dashboard operator assumed,
  matching current architecture); category tree traversal (subcategories) —
  matches on an article's direct category membership only, same as today's BLP
  category check.

## Acceptance Criteria

### 6-topic-category-filter.AC1: Config schema
- **6-topic-category-filter.AC1.1 Success:** `config.yaml` can set
  `article_selection.include_categories` / `exclude_categories` as string
  lists; unset defaults to `[]`, behavior unchanged from today.
- **6-topic-category-filter.AC1.2 Failure:** a non-list value for either field
  fails Pydantic validation at load time with a clear error.

### 6-topic-category-filter.AC2: Selection filtering logic
- **6-topic-category-filter.AC2.1 Success:** `exclude_categories` is
  non-empty and the article's categories intersect it → rejected ("excluded
  category: <name>"), regardless of the include list.
- **6-topic-category-filter.AC2.2 Success:** `include_categories` is
  non-empty, no exclude match, and the article's categories intersect it →
  passes; no intersection → rejected ("not in included categories").
- **6-topic-category-filter.AC2.3 Failure/neutral:** both lists empty → no
  change to existing behavior (BLP/protection/citation-needed checks alone
  decide).

### 6-topic-category-filter.AC3: Category search
- **6-topic-category-filter.AC3.1 Success:** `GET
  /api/categories/search?q=<prefix>` returns up to ~20 matching category
  names from the Category namespace via `site.allpages`.
- **6-topic-category-filter.AC3.2 Failure:** empty/missing `q` returns 400
  (no full-namespace dump).

### 6-topic-category-filter.AC4: Runtime override
- **6-topic-category-filter.AC4.1 Success:** `GET
  /api/settings/categories` returns the active include/exclude lists
  (override if set, else `config.yaml` defaults).
- **6-topic-category-filter.AC4.2 Success:** `POST
  /api/settings/categories` with `{"include": [...], "exclude": [...]}`
  updates the in-memory override; the next "Fetch new article" call uses it
  immediately.
- **6-topic-category-filter.AC4.3 Failure:** malformed payload (non-list
  values) → 400, previous override unchanged.
- **6-topic-category-filter.AC4.4 Note:** overrides reset to `config.yaml`
  defaults on server restart — documented behavior, not a bug.

### 6-topic-category-filter.AC5: Dashboard UI
- **6-topic-category-filter.AC5.1 Success:** the dashboard shows the current
  include/exclude categories as removable chips/tags, with a search-as-you-type
  box (backed by AC3) to add new ones — no free-text entry of unvalidated
  category names.

## Architecture
Config-only persistent defaults, plus an in-memory runtime override held by
the web app (not re-read from `config.yaml`), plus a category-name search
endpoint for the UI:

- `ArticlePicker` currently reads `get_config()` once at construction. The
  runtime override needs the *current* include/exclude lists fed into
  `is_candidate()` / `fetch_candidates()` per fetch, not just at picker
  construction — the web app should pass the active lists in (e.g. as
  arguments, or by re-reading a small mutable settings object) rather than
  `ArticlePicker` re-reading `get_config()` on every call.
- The search endpoint is read-only against Wikipedia (`site.allpages(prefix=q,
  namespace=14)`) — no local index to maintain.
- The runtime override endpoint is a simple in-memory read/write (e.g. a
  module-level object or `app.config` entry) guarded the same way other
  mutable web_app state is handled today.

## Existing Patterns
- Boolean exclude flags (`exclude_blp`, `exclude_protected`) → extend the same
  `ArticleSelectionConfig` idiom with list fields.
- `get_categories()` already fetches per-article categories at zero extra
  network cost — filtering is pure logic, no model changes needed
  (`CandidateArticle.categories` already exists).
- Test idiom: mock `page.categories()` in `tests/test_article_picker.py`;
  tempfile YAML fixtures in `tests/test_config.py`.
- Route idiom: flat `@app.route(...)` functions in `wiki_cite/web_app.py`
  (see the existing `/api/...` routes), JSON in/out via `jsonify`/`request`.

## Implementation Phases

### Phase 1: Config schema + include/exclude filtering
**Goal:** add include/exclude category config and gate `is_candidate()` on it.
**Components:** `wiki_cite/config.py`, `wiki_cite/article_picker.py`,
`tests/test_config.py`, `tests/test_article_picker.py`
**Done when:** AC1, AC2

### Phase 2: Category search endpoint
**Goal:** expose Wikipedia category-name search for the dashboard to consume.
**Components:** `wiki_cite/web_app.py` (new route), tests
**Done when:** AC3

### Phase 3: Runtime override (read/write) + wiring into fetch
**Goal:** in-memory override of include/exclude lists, read by the fetch path
instead of the static config alone.
**Components:** `wiki_cite/web_app.py`, `wiki_cite/article_picker.py` (accept
overrides), tests
**Done when:** AC4

### Phase 4: Dashboard UI
**Goal:** search-and-select UI for include/exclude categories on the
dashboard.
**Components:** `wiki_cite/templates/index.html` (or `base.html`), any new JS
**Done when:** AC5

## Glossary
- **Include/exclude category filter**: config- and runtime-level lists that
  scope which Wikipedia categories an article must (include) or must not
  (exclude) belong to, to be selected as a candidate.
- **Runtime override**: an in-memory, session-scoped set of include/exclude
  lists set via the dashboard, taking precedence over `config.yaml` until
  server restart.
