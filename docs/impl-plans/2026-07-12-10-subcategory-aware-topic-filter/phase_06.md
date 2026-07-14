# Phase 6: Tests across all ACs (with the historical-politician worked example)

**Goal:** Direct test coverage for every AC — crawl cycle-safety, classification
prompt/parsing/fail-closed, expansion-file format + runtime loading, and BLP-relaxation
scoping — using a deliberately non-BLP-heavy root as the worked example.
**AC Coverage:** All of AC1, AC2, AC3, AC4, AC5.

---

## Context

Test conventions (verified in the repo):
- `tests/test_<module>.py`, plain `pytest` functions, `unittest.mock.Mock`/`patch`.
- Anthropic is mocked by `patch("wiki_cite.<module>.Anthropic")` then assigning a
  `SimpleNamespace(messages=SimpleNamespace(create=<mock>))` client (see `tests/test_agent.py`).
- Config is global: `get_config()`/`set_config()`; use a `restore_config` fixture (already in
  `tests/test_article_picker.py`) or `set_config(Config(...))` and restore.
- mwclient pages/categories are mocked as objects with `.name` (and `.members` for
  categories); `site.pages` is mocked as a dict or a `Mock` with `__getitem__`.
- Coverage + branch coverage are on by default (`addopts` in `pyproject.toml`); keep new code
  exercised.

Worked-example root (per design Phase 6): use `"20th-century American politicians"` (or
`19th-century`) as the crawl/expansion root in fixtures — historical, so the test data itself
sidesteps live-BLP concerns while the BLP-relaxation *logic* is still exercised directly with
synthetic BLP articles.

## Implementation

### `tests/test_category_discovery.py` (new)

**Files:**
- Create: `tests/test_category_discovery.py`

Cover:

**Crawl (AC1) — `crawl_subcategories`** (imported from `wiki_cite.article_picker`):
- Build a mock `site` where `site.pages["Category:X"]` returns a mock Category whose
  `.members(namespace=14)` yields mock members (each with `.name = "Category:Child"`). A
  helper like `_make_site(tree: dict[str, list[str]])` mapping a category name to its
  subcategory names keeps this readable.
- `test_crawl_returns_root_and_all_reachable` (AC1.1): a 2–3 level tree returns the root plus
  every reachable subcategory name (prefix stripped).
- `test_crawl_skips_failed_branch` (AC1.2): one category whose `.members` raises is logged
  (assert via `caplog`) and skipped; siblings/other branches still returned; no exception
  escapes.
- `test_crawl_cycle_terminates_and_dedupes` (AC1.3): a graph where a child points back to an
  ancestor (and/or is reachable via two parents) terminates and each name appears once.
- `test_crawl_respects_max_depth`: with `max_depth=1`, only root + its direct subcats appear.

**Classification (AC2) — `classify_categories`:**
- `test_classify_keeps_content_excludes_maintenance` (AC2.1): mock the client's
  `messages.create` to return a text block containing a JSON keep-map that marks
  `"American politics task force"`/`"...articles by quality"`/`"...participants"` false and a
  topical name + a `"...stubs"` name true; assert the returned list keeps exactly the true
  ones (asserts stubs are kept).
- `test_classify_batch_error_fails_closed` (AC2.2): a `messages.create` that raises →
  those names excluded, warning logged, other batch(es) still classified. Use `batch_size`
  small enough to force ≥2 batches and make only one raise (e.g. `side_effect` list).
- `test_classify_malformed_response_excludes` (AC2.2): `messages.create` returns non-JSON /
  partial text → names absent from the parsed map default to excluded.
- Use `patch("wiki_cite.category_discovery.Anthropic")` and pass an explicit `client=` mock
  so no real key/network is needed.

**Expansion file + loader (AC3, AC4 loader half):**
- `test_write_expansion_file_format` (AC3.1): `write_expansion_file(root, names, max_depth=2)`
  into a `tmp_path` (monkeypatch `category_discovery.EXPANSIONS_DIR` to `tmp_path`) writes a
  JSON with `root`, `generated_at`, `max_depth`, and a sorted+deduped `categories` list that
  includes the root.
- `test_write_expansion_deterministic_modulo_timestamp` (AC3.2): writing twice with the same
  inputs yields identical `categories` (and `root`/`max_depth`); only `generated_at` may
  differ. Overwrites rather than merges (write once with names A,B; again with only C;
  second file's `categories` = {root, C}, not A,B,C).
- `test_load_expansion_present` / `test_load_expansion_absent_returns_none` (AC4.2 loader):
  present file → its `categories`; missing file → `None`; malformed file → `None` + warning.
- `test_slugify_root_deterministic`: prefix/case/space/underscore handling.

### `tests/test_article_picker.py` (extend)

**Files:**
- Modify: `tests/test_article_picker.py`

**Runtime expansion (AC4):** monkeypatch/point `load_expansion` (or the expansion dir) so a
known root maps to a known discovered set.
- `test_fetch_candidates_expands_include_from_file` (AC4.1): configure
  `include_categories = ["20th-century American politicians"]`; write (or patch loader to
  return) an expansion set containing a subcategory `"United States senators"`. A mock page
  whose category is `"United States senators"` (a discovered subcat, NOT the root) is
  yielded; a page in an unrelated category is filtered out. Assert `category_filter` /
  `_evaluate_candidate` used the widened list.
- `test_fetch_candidates_no_expansion_file_direct_match` (AC4.2): configured include category
  with no file → only articles directly in that category pass; a subcategory-only article is
  filtered out; nothing raises. (Patch `load_expansion` to return `None`.)

**BLP relaxation (AC5):** synthetic BLP article via `is_blp` categories (`["Living people"]`).
- `test_blp_relaxed_when_topic_filter_active` (AC5.1): `guardrails.relax_blp_when_topic_filtered
  = True`, non-empty include list that the BLP page matches → `is_candidate`/
  `_evaluate_candidate` accepts it (BLP check skipped). Ensure the page also has a
  `{{Citation needed}}` claim so it isn't rejected for another reason.
- `test_blp_not_relaxed_without_include_filter` (AC5.2): same flag `True`, empty include list
  → BLP page rejected with "BLP article".
- `test_blp_default_flag_excludes` (AC5.3): flag `False` (default) → BLP page rejected, both
  with and without an include filter; confirms parity with current behavior.
- Use the `restore_config` fixture (or set/reset `config.guardrails.relax_blp_when_topic_filtered`
  and `config.article_selection.include_categories` in a `try/finally`) so global config is
  restored.

### `tests/test_config.py` (extend)

**Files:**
- Modify: `tests/test_config.py`

- `test_guardrails_relax_blp_default_false`: `GuardrailsConfig().relax_blp_when_topic_filtered
  is False` (AC5.3 config side).
- `test_config_load_relax_blp_flag`: a YAML `guardrails:` block with
  `relax_blp_when_topic_filtered: true` loads through `Config.load` as `True`.

### `tests/test_cli.py` (extend)

**Files:**
- Modify: `tests/test_cli.py`

- `test_cmd_discover_categories_writes_file` (AC3 wiring): patch
  `wiki_cite.cli.crawl_subcategories` → a fixed name list, patch
  `wiki_cite.cli.classify_categories` → a fixed accepted subset, patch `ArticlePicker` so no
  real site is built, and point the expansion dir at `tmp_path`. Call
  `cmd_discover_categories(argparse.Namespace(root=..., max_depth=None, batch_size=20))` and
  assert the expected `<slug>.json` exists with the accepted categories (+ root). This keeps
  the test fully offline (no Wikipedia, no Anthropic).

## Verification

Run: `uv run pytest -q`
Also: `uv run ruff check .`
Expected: all tests pass (coverage + branch coverage on by default); every AC listed in
`test-requirements.md` has at least one passing test.

## Commit

`test: cover subcategory discovery, expansion loading, and BLP relaxation`
