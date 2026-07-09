# Implementation plan: #6 Topic/Category filter

Design: `docs/design-plans/2026-07-08-6-topic-category-filter.md`

Lets operators scope which Wikipedia categories the agent draws candidates from —
persistent defaults in `config.yaml`, plus an in-memory, session-scoped dashboard
override with search-and-select (never written back to `config.yaml`).

## Phases

| Phase | File | Scope | ACs |
|-------|------|-------|-----|
| 1 | `phase-1-config-and-filtering.md` | `include/exclude_categories` config + pure `is_candidate` filter | AC1, AC2 |
| 2 | `phase-2-category-search-endpoint.md` | `GET /api/categories/search` (Category namespace, prefix) | AC3 |
| 3 | `phase-3-runtime-override.md` | In-memory override, `GET`/`POST /api/settings/categories`, wired into fetch | AC4 |
| 4 | `phase-4-dashboard-ui.md` | Chips + search-and-select UI on the dashboard | AC5 |

Phases are ordered by dependency: 4 needs 2+3; 3 needs the `is_candidate` override args
added in 1.

## Cross-cutting notes (verified against the codebase)
- **Pydantic v2 gives AC1.2 for free**: `Config.load` builds
  `ArticleSelectionConfig(**yaml["article_selection"])` (`wiki_cite/config.py:108-109`),
  and a non-list value for a `list[str]` field raises `ValidationError` at load.
- **Category data is already fetched**: `is_candidate` computes
  `categories = self.get_categories(page)` at `wiki_cite/article_picker.py:258`, so
  filtering is pure logic with zero extra network cost. `CandidateArticle.categories`
  already exists.
- **Override is passed in, not re-read**: per the design, the picker takes optional
  `include_categories`/`exclude_categories` args (Phase 1 adds them to `is_candidate`,
  Phase 3 threads them through `fetch_candidates` and feeds them from the web app's
  in-memory override) — the picker never re-reads `get_config()` per fetch.
- **Endpoint tests must patch network services**: `create_app()` instantiates
  `ArticlePicker` (`web_app.py:35`) and `WikipediaPushService` (`web_app.py:37`), each
  opening a real `mwclient.Site` (`article_picker.py:87`, `wikipedia_push.py:57`).
  `tests/test_web_app.py` does not exist yet; Phase 2 creates it with a fixture that
  patches these out before `create_app()`.
- **No `config.yaml` write path**: overrides live in memory only and reset on restart
  (AC4.4). Do not add persistence — explicitly out of scope.

## Out of scope (from design)
Persisting overrides to `config.yaml`; multi-operator concurrent-edit handling;
subcategory/category-tree traversal (direct membership only).

## Verification per phase
- `uv run pytest` (coverage + branch coverage on by default)
- `uv run ruff check .`
- Phase 4 additionally requires manual browser verification (UI change).
