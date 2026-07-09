# Phase 4: `FeedbackConfig` + candidate pooling in `ArticlePicker`

**Goal:** Add `article_selection.candidate_pool_size` and a new `feedback` config block, and
rework `fetch_candidates` to pull a *pool* of candidates (larger than `limit`) before
yielding — but with **no scoring yet**. This phase only proves the pooling plumbing is inert:
with no active scorer, pool order equals today's category order.

**ACs covered:** none directly — this is the plumbing that AC4/AC5 (Phase 5) build on. The
gate is that existing `test_article_picker.py` fetch-order behavior is unchanged.

**Depends on:** nothing beyond current `main`.

## Files

- `wiki_cite/config.py` — `candidate_pool_size`, `FeedbackConfig`, wire into `Config.load`.
- `config.yaml` — new keys.
- `wiki_cite/article_picker.py` — factor candidate construction into a helper; add pooling.
- `tests/test_config.py`, `tests/test_article_picker.py` — extend.

## Changes

### `wiki_cite/config.py`

Follow the existing nested-`BaseSettings` pattern (design §"Config additions",
§"Existing Patterns"):

- Add `candidate_pool_size: int = 30` to `ArticleSelectionConfig` (lines 46–55).
- Add a new class:
  ```python
  class FeedbackConfig(BaseSettings):
      """Configuration for the outcomes-feedback loop that re-ranks candidates."""
      enabled: bool = True
      epsilon: float = 0.15
      min_samples: int = 5
  ```
- Add `feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)` to `Config`
  (lines 63–67).
- In `Config.load`, add the same guard the other blocks use (lines 93–102):
  `if "feedback" in yaml_config: config_data["feedback"] = FeedbackConfig(**yaml_config["feedback"])`.

### `config.yaml`

- Under `article_selection:` add `candidate_pool_size: 30  # look-ahead pool size (>= limit)`.
- Add a top-level block:
  ```yaml
  feedback:
    enabled: true
    epsilon: 0.15
    min_samples: 5
  ```

### `wiki_cite/article_picker.py`

Currently `fetch_candidates` (lines 270–333) is a single generator loop that, per page,
skips seen/non-candidate pages and inline-constructs a `CandidateArticle` (lines 314–325).

- **Factor the per-page construction into a helper**, e.g. `_build_candidate(self, page) ->
  CandidateArticle`, holding the current lines 306–325 body (page text, categories, body
  lines, infobox detection, `CandidateArticle(...)`). Both the (new) pooling loop and any
  other caller reuse it (design §"Existing Patterns").
- **Rework the loop into pool-then-yield** (design §"Chosen approach: feeding rates back"):
  ```python
  pool_size = max(self.config.article_selection.candidate_pool_size, limit)
  pool: list[CandidateArticle] = []
  for page in cat_page:
      if len(pool) >= pool_size:
          break
      if self.seen_store is not None and self.seen_store.is_seen(page.name):
          continue
      is_candidate, _ = self.is_candidate(page)
      if not is_candidate:
          continue
      try:
          pool.append(self._build_candidate(page))
      except Exception as e:
          print(f"Error processing page {page.name}: {e}")
          continue
  # Phase 4: no scorer yet -> identity order.
  yield from pool[:limit]
  ```
  Keep the existing per-page `try/except` around construction (current lines 330–332) inside
  `_build_candidate`'s call site so a bad page is skipped, not fatal.
- **Important inertness guarantee:** when `candidate_pool_size <= limit`, `pool_size == limit`
  and the loop yields exactly the first `limit` candidates in category order — identical to
  today. The existing `test_fetch_candidates_skips_seen` test (uses `limit=5`, default
  pool_size 30 → pool_size 30, but only 1 valid candidate exists) must still pass: pool
  collects `Fresh Article`, `Old News` is skipped by `is_seen`, order preserved.

Do **not** add the scorer here. A `_build_scorer` that returns `None` may be stubbed for
Phase 5 to fill, but Phase 4's yield is plain `pool[:limit]`.

## Tests

- `tests/test_config.py::test_feedback_config_defaults`: `FeedbackConfig()` → `enabled True`,
  `epsilon 0.15`, `min_samples 5`.
- `test_config.py::test_candidate_pool_size_default`: `ArticleSelectionConfig().candidate_pool_size == 30`.
- `test_config.py::test_config_load_feedback_block`: YAML with a `feedback:` block loads into
  `config.feedback` (mirror `test_config_load_from_yaml`).
- `tests/test_article_picker.py`: existing `test_fetch_candidates_skips_seen` must pass
  **unmodified**. Add `test_fetch_candidates_pool_preserves_order`: feed several valid mock
  pages, assert `fetch_candidates(limit=N)` yields them in category order (pooling is a no-op
  reorder) with `feedback.enabled` default.

## Done when

- `uv run pytest tests/test_config.py tests/test_article_picker.py` passes with the existing
  fetch-order tests unmodified.
- `_build_candidate` is the single construction path; the pooling loop yields `pool[:limit]`
  in category order (no scoring).
- `uv run ruff check .` clean.
