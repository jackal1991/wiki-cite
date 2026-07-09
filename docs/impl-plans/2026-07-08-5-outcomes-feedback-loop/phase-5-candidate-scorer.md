# Phase 5: `CandidateScorer` + wire into `fetch_candidates`

**Goal:** Implement the pure scoring function (per-dimension rate blend + neutral prior for
under-sampled dimensions + epsilon jitter) and use it to sort the pool before truncating to
`limit` — so ranking happens **before** any Claude call is spent (the pool is built from cheap
title/`is_candidate` checks only).

**ACs covered:** AC4 (learned rates re-rank before a Claude call), AC5 (epsilon prevents a
death-spiral).

**Depends on:** Phase 1 (`dimension_rates`), Phase 4 (config + pooling).

## Files

- `wiki_cite/article_picker.py` — `CandidateScorer`, `_build_scorer`, wire into
  `fetch_candidates`.
- `tests/test_article_picker.py` — scorer + ranking tests.

## Changes

### `CandidateScorer` (pure, unit-testable — no I/O)

Per design §"Chosen approach: feeding rates back". A class taking pre-fetched rates so it never
touches sqlite or Wikipedia:

```python
class CandidateScorer:
    def __init__(self, rates: dict[str, dict[str, tuple[int, int]]], epsilon: float, min_samples: int):
        self._rates = rates          # {dimension: {value: (successes, total)}}
        self._epsilon = epsilon
        self._min_samples = min_samples

    def score(self, candidate: CandidateArticle) -> float:
        scores = []
        for category in candidate.categories:
            successes, total = self._rates.get("categories", {}).get(category, (0, 0))
            scores.append(successes / total if total >= self._min_samples else 0.5)
        has_infobox_key = str(candidate.has_infobox)   # "True"/"False" — matches dimension_rates mapping
        successes, total = self._rates.get("has_infobox", {}).get(has_infobox_key, (0, 0))
        scores.append(successes / total if total >= self._min_samples else 0.5)
        base = sum(scores) / len(scores) if scores else 0.5
        return base + random.random() * self._epsilon
```

Key invariants (AC5.2): a dimension value below `min_samples` gets the **neutral 0.5 prior**,
never a raw 0 — so a never-tried dimension is treated as "unknown", not "bad". The `+
random()*epsilon` jitter (AC5.1) means even a well-observed low-rate candidate occasionally
sorts ahead. Add `import random` to the module. `has_infobox_key` must match Phase 1's
`dimension_rates` `1->"True"/0->"False"` mapping — call this out so the two stay in sync.

### `_build_scorer(self) -> CandidateScorer | None`

Reads the learned rates for the scored dimensions from `self.seen_store` and constructs the
scorer. Guards (design §"Chosen approach", AC6.1/AC6.3):

- Return `None` immediately if `self.seen_store is None`, or if
  `self.config.feedback.enabled is False` (the manual escape hatch).
- Wrap the `dimension_rates` calls in `try/except sqlite3.Error` (import `sqlite3`); on any
  error return `None`. A `None` scorer means the pool yields in category order (Phase 4
  behavior).
- Build `rates = {"categories": store.dimension_rates("categories"), "has_infobox":
  store.dimension_rates("has_infobox")}` and return
  `CandidateScorer(rates, self.config.feedback.epsilon, self.config.feedback.min_samples)`.
- Only article-level dimensions known at fetch time (`categories`, `has_infobox`) are scored —
  edit-level dims (`source_type`, etc.) are not available on a `CandidateArticle` before
  analysis, which is exactly why AC4.2 is satisfiable (no Claude call needed to rank).

### Wire into `fetch_candidates`

Replace Phase 4's `yield from pool[:limit]` with (design §"Chosen approach"):

```python
scorer = self._build_scorer()
ranked = sorted(pool, key=scorer.score, reverse=True) if scorer else pool
yield from ranked[:limit]
```

`sorted` is called on the already-collected pool — no `ClaudeAgent` call happens in
`fetch_candidates` (verify: `agent` is only referenced in `web_app.scan_events`, never in the
picker), so ranking spends zero Claude calls (AC4.2).

## Tests (`tests/test_article_picker.py`)

Scorer tests are pure — no mock site needed:

- `test_scorer_prefers_higher_rate_dimension` (AC4.1): construct `CandidateScorer` with
  `rates={"categories": {"news-ish": (9, 10), "journal-ish": (1, 10)}}`, `epsilon=0`,
  `min_samples=5`; build two `CandidateArticle`s (categories `["news-ish"]` vs
  `["journal-ish"]`); assert `score(news) > score(journal)`.
- `test_scorer_neutral_prior_for_undersampled` (AC5.2): a category with `(0, 0)` or `total <
  min_samples` scores at 0.5, not 0 — assert `score` of an unknown-category candidate equals
  0.5 with `epsilon=0`.
- `test_scorer_epsilon_can_reorder` (AC5.1): with `epsilon>0` and a fixed `random.seed(...)`,
  show a low-rate candidate can sort ahead of a high-rate one across seeded runs (or assert the
  jitter term is in `[0, epsilon)` and that with epsilon large enough the order can flip) —
  proves no strict, sticky ordering.
- `test_fetch_candidates_ranks_by_learned_rate` (AC4.1 end-to-end): seed a real `SeenStore`
  (`tmp_path`) with a clear rate gap between two categories, build mock pages whose
  `categories` correlate with each, set `feedback.enabled=True`, `epsilon=0` (deterministic),
  and assert `fetch_candidates(limit=1)` yields the high-rate candidate first — and assert no
  agent/analysis was invoked (the picker has no agent reference; document this).
- `test_fetch_candidates_disabled_feedback_is_category_order` (ties to Phase 4/AC6):
  `feedback.enabled=False` → order unchanged from category order even with a seeded DB.

Use `set_config` (config.py lines 119–122) or a config fixture to inject `feedback`/`epsilon`/
`min_samples` per test; reset it in teardown so tests don't leak the global config.

## Done when

- `uv run pytest tests/test_article_picker.py` passes.
- A seeded outcomes history with a rate gap produces the predicted candidate order with no
  Claude call spent (AC4); a 0-sample dimension scores 0.5, and epsilon can surface a low-rate
  candidate across seeded runs (AC5).
- `uv run ruff check .` clean.
