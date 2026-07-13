# Phase 1: Sequential, cycle-safe subcategory crawler

**Goal:** Add a sequential, cycle-safe BFS walker over a category's subcategory tree that
produces a raw discovered-category-name list, degrading gracefully on a failed branch.
**AC Coverage:** 10-subcategory-aware-topic-filter.AC1 (AC1.1, AC1.2, AC1.3)

---

## Context

`wiki_cite/article_picker.py` is the Wikipedia-facing module. It already has
`_build_session()` (retry/backoff session for mwclient) and an `ArticlePicker` class whose
`self.site` is an `mwclient.Site`. Nothing currently walks subcategories — `fetch_candidates`
iterates the *articles* of one flat category only.

Verified facts about the mwclient API (checked against the installed version):
- `site.pages["Category:Foo"]` returns an `mwclient.listing.Category` object (a `Page`
  subclass) that has a `.members(...)` method.
- `Category.members(prop='ids|title', namespace=None, ...)` returns an iterable/generator of
  page objects. Passing `namespace=14` restricts it to **subcategory** members only.
- Each yielded member object has a `.name` attribute of the form `"Category:Foo bar"`
  (i.e. it still carries the `Category:` prefix).

This phase adds a standalone module-level function; it does not touch `ArticlePicker` or
`fetch_candidates`.

## Implementation

### `crawl_subcategories` (new module-level function in `article_picker.py`)

**Files:**
- Modify: `wiki_cite/article_picker.py` — add one new module-level function near the top
  (after `_build_session`, before or after the excerpt helpers; keep it a plain function,
  not a method, so it is unit-testable without constructing an `ArticlePicker`).

**Signature:**
```python
def crawl_subcategories(
    site,
    root: str,
    max_depth: int | None = None,
) -> list[str]:
    """Breadth-first walk of the subcategory tree under ``root``.

    Sequential — one Wikipedia request in flight at a time, per API:Etiquette — and
    cycle-safe: MediaWiki categories form a graph (a subcategory can loop back to an
    ancestor), so a ``visited`` set guarantees termination and that each category is
    fetched at most once.

    Args:
        site: an mwclient Site (``ArticlePicker.site``); ``site.pages[...]`` yields a
            Category with ``.members(namespace=14)``.
        root: root category name, with or without the ``Category:`` prefix.
        max_depth: optional BFS depth cap (root is depth 0). ``None`` = unbounded
            (still terminates via the visited set).

    Returns:
        A sorted, de-duplicated list of discovered category names, WITHOUT the
        ``Category:`` prefix, INCLUDING the root itself. No relevance judgment is
        applied here — that is the classification stage's job.
    """
```

**What to implement:**
- Strip a leading `Category:` prefix from `root` for the seed, and use a helper to strip the
  prefix from each discovered member `.name` (reuse the existing normalization idea, but
  keep the *original* human-readable name — do NOT casefold or replace underscores in the
  returned names; only strip the `Category:` prefix. The returned names must be usable
  as-is later by `category_filter`, which does its own normalization).
- BFS with a `collections.deque` of `(name, depth)` and a `visited: set[str]`. Seed with the
  root. Track visited by a normalized key (casefolded, prefix-stripped) so that the same
  category reached via two parent paths (AC1.3) is only enqueued/fetched once — but store
  the readable name in the result set.
- For each dequeued category:
  - Skip if its normalized key is already visited; otherwise mark visited and add the
    readable name to the results.
  - If `max_depth` is not None and this node is at `max_depth`, do not fetch its children.
  - Otherwise fetch children: `cat_page = site.pages[f"Category:{name}"]`, then iterate
    `cat_page.members(namespace=14)`. Wrap the fetch+iteration in `try/except Exception`
    (AC1.2): on failure, `logger.warning("Skipping subcategory branch %r: %s", name, e)`
    and `continue` — the crawl must still return a partial result, never abort. This mirrors
    the existing `get_categories()`/`is_protected()` degrade-on-error convention.
  - For each child member, strip its `Category:` prefix and enqueue `(child_name, depth+1)`
    if its normalized key is not yet visited.
- The root itself is always included in the returned list (it is enqueued first and added to
  results when dequeued).
- Return `sorted(results)` (deterministic order; classification/write stages stay stable).

**Notes:**
- Keep this function Wikipedia-facing only — no Anthropic, no file I/O. Those belong to
  Phase 2 and Phase 3.
- One request at a time; do NOT parallelize the Wikipedia calls (etiquette + the retry
  session already handles 429/backoff).

**Tests:** (full coverage lands in Phase 6; you may add these now or there — Phase 6 owns
the AC-mapping.)
- AC1.1: a small mocked `site` whose categories form a shallow tree returns all reachable
  subcategory names plus the root.
- AC1.2: a category whose `.members()` (or `site.pages[...]`) raises is logged and skipped;
  the crawl still returns the other branches.
- AC1.3: a diamond/cycle (child reachable via two parents, or a child pointing back at an
  ancestor) terminates and yields each name exactly once.

---

## Verification

Run: `uv run pytest tests/test_article_picker.py -q`
Also: `uv run ruff check wiki_cite/article_picker.py`
Expected: existing tests still pass; new `crawl_subcategories` importable
(`from wiki_cite.article_picker import crawl_subcategories`).

## Commit

`feat: add sequential cycle-safe subcategory crawler`
