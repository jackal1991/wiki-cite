# Phase 3: `discover-categories` CLI command + static output

**Goal:** Wire crawl → classify → write into a runnable `wiki-cite discover-categories <root>`
command that produces a deterministic, versioned JSON category-expansion file.
**AC Coverage:** 10-subcategory-aware-topic-filter.AC3 (AC3.1, AC3.2)

---

## Context

`wiki_cite/cli.py` builds an `argparse` CLI: each command is a
`subparsers.add_parser(name, ...)` + a `cmd_<name>(args)` function + `set_defaults(func=...)`.
Existing commands (`fetch`, `analyze`, `web`, `config`, `stats`) follow this exactly.
`main()` calls `_configure_logging(...)` then `args.func(args)`.

Phase 1 gives `crawl_subcategories(site, root, max_depth)` in `article_picker.py`.
Phase 2 gives `classify_categories(names, ...)` in `category_discovery.py`.

This phase adds the write/path/slug helpers (pure JSON I/O, no Anthropic, no Wikipedia — safe
for Phase 4's runtime loader to import) to `category_discovery.py`, plus the CLI command.

## Implementation

### File-format helpers (add to `wiki_cite/category_discovery.py`)

**Files:**
- Modify: `wiki_cite/category_discovery.py`

Add, using `from pathlib import Path` and `from datetime import datetime, timezone`:

```python
EXPANSIONS_DIR = Path("data/category_expansions")

def slugify_root(root: str) -> str:
    """Filesystem slug for a root category name: strip a Category: prefix, casefold,
    spaces/underscores -> hyphens, drop anything but [a-z0-9-]. Deterministic."""

def expansion_file_path(root: str) -> Path:
    """EXPANSIONS_DIR / f"{slugify_root(root)}.json" (used by both writer and loader)."""

def write_expansion_file(root: str, categories: list[str], *, max_depth: int | None) -> Path:
    """Write the deterministic, sorted, deduplicated expansion file and return its path."""
```

`write_expansion_file` behavior (AC3.1, AC3.2):
- Ensure `EXPANSIONS_DIR.mkdir(parents=True, exist_ok=True)`.
- Build the category set = `sorted(set(categories) | {root_without_prefix})` — the root is
  always included even if classification would have dropped it.
- Write JSON (via `json.dump(..., indent=2, ensure_ascii=False, sort_keys=True)` +
  trailing newline) with keys:
  - `"root"`: the root name (prefix-stripped, human-readable).
  - `"generated_at"`: `datetime.now(timezone.utc).isoformat()` — the discovery timestamp,
    the one field allowed to differ between two runs of identical inputs.
  - `"max_depth"`: the crawl depth cap used (int or null).
  - `"categories"`: the sorted, deduplicated accepted list (includes the root).
- Overwrite the file wholesale (open in `"w"`), never append/merge (AC3.2). Given a fixed
  crawl+classification result, the file content is identical run-to-run except
  `generated_at`.

### `cmd_discover_categories` + subparser (in `wiki_cite/cli.py`)

**Files:**
- Modify: `wiki_cite/cli.py`

- Add imports: `from wiki_cite.article_picker import ArticlePicker, crawl_subcategories` and
  `from wiki_cite.category_discovery import classify_categories, write_expansion_file`.
- Add `cmd_discover_categories(args)`:
  - Build a site the same way `ArticlePicker` does. Simplest: `picker = ArticlePicker()` and
    use `picker.site` (this reuses `_build_session()`'s retry/backoff and the configured
    user-agent — AC1.1 requires the crawl reuse that session). Do NOT open a bare
    `mwclient.Site` without the pooled session.
  - `print(f"Crawling subcategories under {args.root!r}...")`, then
    `raw = crawl_subcategories(picker.site, args.root, max_depth=args.max_depth)`.
  - `print(f"Discovered {len(raw)} categories; classifying...")`, then
    `accepted = classify_categories(raw, batch_size=args.batch_size)`.
  - `path = write_expansion_file(args.root, accepted, max_depth=args.max_depth)`.
  - `print(f"Wrote {len(...)} accepted categories to {path}")` (count the written set, which
    includes the root).
- Register the subparser in `main()` alongside the others:
  ```python
  discover_parser = subparsers.add_parser(
      "discover-categories",
      help="Crawl a category's subcategory tree and write a static expansion file",
  )
  discover_parser.add_argument("root", help="Root category name (with or without Category: prefix)")
  discover_parser.add_argument("--max-depth", type=int, default=None, help="BFS depth cap (default: unbounded)")
  discover_parser.add_argument("--batch-size", type=int, default=20, help="Category names per Anthropic classification call")
  discover_parser.set_defaults(func=cmd_discover_categories)
  ```

### New data directory

**Files:**
- Create: `data/category_expansions/` — add a `.gitkeep` (empty file) so the directory exists
  in the repo before the first discovery run. Generated `<slug>.json` files are checked in
  like any other versioned-but-generated artifact (per the design's Definition of Done #2).

## Verification

Run: `uv run pytest tests/test_cli.py -q`
Also run the command wiring smoke test without hitting the network by mocking in a test
(Phase 6), or verify argparse registration:
`uv run wiki-cite discover-categories --help` (should show `root`, `--max-depth`,
`--batch-size`).
Expected: `discover-categories` is a registered subcommand; helper functions importable from
`wiki_cite.category_discovery`.

## Commit

`feat: add discover-categories CLI command and static expansion output`
