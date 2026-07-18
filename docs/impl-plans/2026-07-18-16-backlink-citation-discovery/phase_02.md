# Phase 2: Backlink page fetch + candidate assembly

**Goal:** A Wikipedia-facing helper that fetches up to `agent.max_backlink_pages_to_check`
backlinking pages one at a time (skip-and-log on failure, never counting the edited article
itself), plus a `SourceFinder` method that runs each fetched page through Phase 1's extractor,
cross-page-deduplicates the candidate URLs, and turns each through the existing
`check_reliability()` into a `Source`.
**AC Coverage:** 16-backlink-citation-discovery.AC2 (AC2.1, AC2.2, AC2.3), AC3.3, AC4 (AC4.1, AC4.2)

---

## Context

**Module split (planner decision, per design "final module decision left to the planner"):**
Follow the #10 Wikipedia-facing-vs-service split exactly.
- The raw mwclient `.backlinks()` + `.text()` fetch is **Wikipedia-facing** → a module-level
  function in `article_picker.py`, mirroring `crawl_subcategories` (`article_picker.py:46`):
  takes an injected `site`, is sequential, degrades on per-page failure, is unit-testable with a
  mock site.
- The extract → dedup → `check_reliability` → `Source` assembly is **sourcing service** work →
  a `SourceFinder` method in `source_finder.py`, colocated with `check_reliability`
  (`source_finder.py:116`) and `Source` construction, so backlink candidates flow through the
  exact same reliability path as `search_web`/`search_scholar`/`search_crossref`.

This ordering is safe for imports: `article_picker.py` imports `category_discovery`, `config`,
`models` — **not** `source_finder`. So `source_finder.py` importing the new
`fetch_backlink_pages` from `article_picker` introduces **no cycle** (verified: no
`source_finder` import exists anywhere in `article_picker.py`).

**Verified mwclient facts (checked against the installed version in this worktree):**
- `mwclient.page.Page.backlinks(namespace=None, filterredir='all', redirect=False, limit=None,
  generator=True, max_items=None, api_chunk_size=None)` — "List pages that link to the current
  page, similar to Special:Whatlinkshere" (API:Backlinks). Returns an iterable of `Page`
  objects.
- `site.pages[title]` yields a `Page`; a `Page` has `.name` (full title, e.g. `"Some Article"`
  for mainspace, `"Category:Foo"` for a category) and `.text()` (fetches wikitext, may raise on
  a network/API error — the same failure surface `_evaluate_candidate` already guards at
  `article_picker.py:444`).
- `namespace=0` restricts backlinks to the **article** namespace; `filterredir="nonredirects"`
  excludes redirect pages (a redirect carries no citations of its own). Use both — the mirror of
  `crawl_subcategories`'s `namespace=14`.

**Existing cost-guard config precedent:** `AgentConfig` (`config.py:12`) holds
`max_candidates_per_fetch`, `max_search_turns`, `search_results_per_query`; `config.yaml:1-6`
carries the inline-commented block. `max_backlink_pages_to_check` is a new sibling of exactly
this kind — a fixed hard cap, not adaptive early-stopping (design decision, matching
`max_candidates_per_fetch`).

## Implementation

### `fetch_backlink_pages` (new module-level function in `article_picker.py`)

**Files:**
- Modify: `wiki_cite/article_picker.py` — add a module-level function near `crawl_subcategories`
  (plain function, not an `ArticlePicker` method, so it is unit-testable with a mock `site`).

**Signature:**
```python
def fetch_backlink_pages(site, title: str, max_pages: int) -> list[tuple[str, str]]:
    """Sequentially fetch the wikitext of pages that link TO ``title``.

    Uses MediaWiki "what links here" (``page.backlinks``) restricted to the article
    namespace and non-redirects. Fetches at most ``max_pages`` pages one request at a
    time (per API:Etiquette — the injected ``site`` already carries _build_session()'s
    429/backoff pool). A page whose fetch fails is logged and skipped; the scan
    continues with the rest rather than aborting (mirrors crawl_subcategories's
    degrade-on-branch-failure). The edited article itself is never included even if it
    somehow appears among its own backlinks.

    Args:
        site: an mwclient Site (e.g. ArticlePicker.site).
        title: the article currently being edited.
        max_pages: hard cap on backlinking pages fetched (agent.max_backlink_pages_to_check).

    Returns:
        A list of (page_title, wikitext) for successfully fetched backlinking pages,
        in backlink-iteration order, length <= max_pages. Empty list if the article
        has no backlinks or none could be fetched.
    """
```

**What to implement:**
- `self_key = title.replace("_", " ").strip().casefold()` — the normalized identity of the
  edited article, for AC2.3 self-reference exclusion (same normalization idiom
  `crawl_subcategories` uses for its visited keys).
- `page = site.pages[title]`. Wrap the `.backlinks(...)` call itself in `try/except Exception`:
  if obtaining the backlink iterator raises, `logger.warning(...)` and return `[]` (no
  backlinks obtainable is not an error to the caller — AC2.2 at the whole-scan level).
- Iterate `page.backlinks(namespace=0, filterredir="nonredirects")`. For each backlink page:
  - `bl_key = backlink.name.replace("_", " ").strip().casefold()`; if `bl_key == self_key`,
    skip (AC2.3) — do NOT let it consume one of the `max_pages` slots.
  - Fetch its text inside `try/except Exception`: on failure
    `logger.warning("Skipping backlink page %r: %s", backlink.name, e)` and `continue` (AC2.2) —
    a per-page failure never aborts the scan.
  - On success append `(backlink.name, text)` to results.
  - Stop once `len(results) >= max_pages` (AC2.1 — hard cap counts *successfully fetched*
    pages; skipped/failed pages don't consume the budget).
- Return the results list (order = backlink-iteration order; no sorting — unlike the crawler,
  ordering here is not load-bearing and dedup happens downstream on URLs).

**Notes:**
- Sequential only; do NOT parallelize the `.text()` fetches (etiquette + the pooled retry
  session already handle 429/backoff, exactly as `crawl_subcategories` relies on).
- Wikipedia-facing only: no URL extraction, no reliability, no `Source` — those are the
  `SourceFinder` method's job.

### `find_backlink_sources` (new method on `SourceFinder` in `source_finder.py`)

**Files:**
- Modify: `wiki_cite/source_finder.py` — add a method to `SourceFinder`; add
  `from wiki_cite.article_picker import fetch_backlink_pages` and (if not already imported)
  `import mwclient` and `from wiki_cite.config import get_config` is already present. Import
  `urlparse` is already present (`source_finder.py:9`).

**Signature:**
```python
def find_backlink_sources(self, article_title: str, *, site=None) -> list[Source]:
    """Discover candidate citation URLs from articles that link to ``article_title``.

    Fetches up to ``config.agent.max_backlink_pages_to_check`` backlinking pages
    (fetch_backlink_pages), extracts ALL citation URLs from each (Phase 1
    extract_all_citation_urls), deduplicates across every scanned page, and runs each
    distinct URL through the SAME check_reliability() the other search tools use —
    returning Source objects identical in shape to search_web results. A discovered
    URL is only ever a *candidate*: it is never accepted as a source here, and never
    exempted from reliability checking (including any wikipedia.org URL that slips
    through — WP:CIRCULAR is enforced by check_reliability parity + the system prompt,
    not by a carve-out).

    Args:
        article_title: the article currently being edited.
        site: injected mwclient Site (tests pass a mock); when None a real
            en.wikipedia.org Site is built lazily with the pooled retry session.

    Returns:
        Candidate Source objects, one per distinct discovered URL, in first-seen order.
    """
```

**What to implement:**
- `if site is None: site = mwclient.Site("en.wikipedia.org", pool=_build_session(...))` — build
  lazily so tests that inject a `site` (or patch `fetch_backlink_pages`) never touch the
  network. Reuse `article_picker._build_session` or replicate `ArticlePicker.__init__`'s
  construction (`article_picker.py:209`); prefer importing and reusing `_build_session` to keep
  one retry-policy definition. The `user_agent` comes from `self.config.wikipedia.user_agent`.
- `max_pages = self.config.agent.max_backlink_pages_to_check`.
- `pages = fetch_backlink_pages(site, article_title, max_pages)`.
- Cross-page dedup (AC3.3): keep a `seen: set[str]` and a `sources: list[Source]`. For each
  `(_page_title, wikitext)` in `pages`, for each `url in extract_all_citation_urls(wikitext)`:
  skip if `url in seen`; else `seen.add(url)` and append
  ```python
  Source(
      title=urlparse(url).netloc or url,
      url=url,
      source_type=SourceType.WEB,
      reliability=self.check_reliability(url),
  )
  ```
  `check_reliability` is called for **every** URL with no special-casing (AC4.1, AC4.2) — a
  `wikipedia.org` URL gets whatever rating `check_reliability` returns, never an exemption or a
  forced acceptance.
- Return `sources`.

**Notes:**
- Return shape must be a plain `list[Source]` so the agent's existing `_sources_to_dicts`
  (`agent.py:212`) serializes backlink candidates byte-identically to every other search tool's
  results (AC4.1) — no new payload shape, no new downstream handling in `guardrails.py`.
- No new cross-tool caching (explicitly out of scope in the design); `find_backlink_sources`
  does NOT go through `_cached_search`.

### Config: `max_backlink_pages_to_check`

**Files:**
- Modify: `wiki_cite/config.py` — add to `AgentConfig` (`config.py:12`, beside
  `max_candidates_per_fetch`):
  ```python
  # Cost guard: hard cap on backlinking pages fetched per search_backlinks tool
  # call (a single model turn can internally fetch many pages sequentially, like
  # crawl_subcategories — this bounds that inner work, independent of max_search_turns).
  max_backlink_pages_to_check: int = 10
  ```
- Modify: `config.yaml` — add to the `agent:` block (`config.yaml:1-6`), matching the existing
  inline-comment style:
  ```yaml
    max_backlink_pages_to_check: 10   # cost guard: max backlinking pages scanned per search_backlinks call
  ```

## Verification

Run: `uv run pytest tests/test_source_finder.py tests/test_article_picker.py tests/test_config.py -q`
Also: `uv run ruff check wiki_cite/source_finder.py wiki_cite/article_picker.py wiki_cite/config.py`
Also confirm no import cycle: `uv run python -c "import wiki_cite.source_finder, wiki_cite.article_picker"`.
Also confirm the config key loads: `uv run wiki-cite config` shows `max_backlink_pages_to_check`.
Expected: modules import cleanly with no circular-import error; new config key defaults to 10.
Full AC test coverage lands in Phase 4.

## Commit

`feat: add sequential backlink page fetch and candidate-source assembly`
