# Phase 5: Opt-in BLP relaxation, scoped to active topic filters

**Goal:** A new `guardrails.relax_blp_when_topic_filtered` flag (default `False`) that, only
when `True` AND an include-category filter is active, skips the BLP exclusion in
`_evaluate_candidate`. Default behavior is bit-for-bit unchanged.
**AC Coverage:** 10-subcategory-aware-topic-filter.AC5 (AC5.1, AC5.2, AC5.3)

---

## Context

`GuardrailsConfig` (`wiki_cite/config.py`) holds edit guardrail flags; `Config.load` already
maps a `guardrails:` YAML block onto it. The always-on BLP exclusion lives in
`ArticlePicker._evaluate_candidate` (`wiki_cite/article_picker.py`):
```python
if self.config.article_selection.exclude_blp and self.is_blp(page_text, categories):
    return False, "BLP article", page_text, categories
```
Note this is gated by `article_selection.exclude_blp`, and `_evaluate_candidate` already has
the resolved `include` list in scope (after Phase 4, `include` is the expanded list).

This is independent of the discovery mechanism (design: "unchanged from the first draft").

## Implementation

### Config flag (in `wiki_cite/config.py`)

**Files:**
- Modify: `wiki_cite/config.py`

Add to `GuardrailsConfig`:
```python
class GuardrailsConfig(BaseSettings):
    max_new_words: int = 50
    max_content_removal_pct: int = 20
    min_similarity_ratio: float = 0.85
    skip_blp_articles: bool = True
    # Opt-in, topic-scoped carve-out: when True AND an include_categories filter is active,
    # skip the always-on BLP exclusion. Off by default — WP:BLP is Wikipedia's strictest
    # sourcing-policy area, so this only ever relaxes within a deliberately narrowed topic.
    relax_blp_when_topic_filtered: bool = False
```
`Config.load` already forwards the whole `guardrails:` block into `GuardrailsConfig(**...)`,
so no loader change is needed.

### `config.yaml` documentation

**Files:**
- Modify: `config.yaml`

Under `guardrails:`, add the flag with an inline comment matching the existing cost-guard
comment style:
```yaml
guardrails:
  max_new_words: 50           # Excluding citations/templates
  max_content_removal_pct: 20
  min_similarity_ratio: 0.85
  skip_blp_articles: true
  relax_blp_when_topic_filtered: false  # opt-in: only when an include_categories filter is active, allow BLP articles (WP:BLP is strict — keep off unless deliberately scoping a topic)
```

### BLP check scoping (in `wiki_cite/article_picker.py`)

**Files:**
- Modify: `wiki_cite/article_picker.py` — `_evaluate_candidate`

Replace the BLP guard with a version that honors the scoped relaxation:
```python
# BLP is excluded by default. A deliberately-scoped topic filter may opt out via
# guardrails.relax_blp_when_topic_filtered — but ONLY when an include filter is
# actually active, so the flag can never silently disable BLP exclusion repo-wide.
include_filter_active = bool(include)
blp_relaxed = self.config.guardrails.relax_blp_when_topic_filtered and include_filter_active
if self.config.article_selection.exclude_blp and not blp_relaxed and self.is_blp(page_text, categories):
    return False, "BLP article", page_text, categories
```
- `include` here is the resolved include list already computed a few lines above (the
  `include_categories if include_categories is not None else config...` line). With Phase 4,
  `fetch_candidates` passes the expanded list; for direct `is_candidate()` callers it is
  whatever was passed/configured. Either way, "active" = non-empty include list.
- AC5.2: when `include` is empty, `include_filter_active` is `False`, so `blp_relaxed` is
  `False` regardless of the config flag — BLP still excluded.
- AC5.3: with the flag at its `False` default, `blp_relaxed` is always `False`, so the
  condition reduces exactly to today's `exclude_blp and is_blp(...)`.

**Tests:** (Phase 6 owns AC mapping)
- AC5.1: flag `True` + non-empty include list + BLP article → candidate accepted (BLP check
  skipped).
- AC5.2: flag `True` + empty include list + BLP article → rejected as "BLP article".
- AC5.3: flag `False` (default) + BLP article → rejected, identical to current behavior,
  with and without an include filter.

---

## Verification

Run: `uv run pytest tests/test_config.py tests/test_article_picker.py -q`
Also: `uv run ruff check wiki_cite/config.py wiki_cite/article_picker.py`
Expected: new flag defaults to `False`; existing BLP-exclusion tests unchanged.

## Commit

`feat: add opt-in topic-scoped BLP relaxation guardrail flag`
