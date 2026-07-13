# Phase 2: Relevance classification via parallel Anthropic calls

**Goal:** A new Anthropic-facing module that batches raw category names, classifies each
concurrently as content-relevant vs. Wikipedia-maintenance/organizational, and fails closed
(excludes) on any classification error.
**AC Coverage:** 10-subcategory-aware-topic-filter.AC2 (AC2.1, AC2.2)

---

## Context

`wiki_cite/agent.py` is the precedent for how this codebase talks to Anthropic:
- `from anthropic import Anthropic`
- `self.client = Anthropic(api_key=self.config.anthropic_api_key)` (config via
  `get_config()`; the model name comes from `self.config.agent.model`).
- Calls `self.client.messages.create(model=..., max_tokens=..., system=..., messages=[...])`
  and reads `response.content` blocks (`block.type == "text"` → `block.text`).

This phase creates a **new module** so the Anthropic-facing classification logic stays out
of `article_picker.py` (which must remain Wikipedia-facing). The new module must NOT import
from `article_picker.py` (Phase 4 has `article_picker` import a loader from here — importing
the other direction too would create a cycle).

Tests mock the Anthropic client the same way `tests/test_agent.py` does: patch
`wiki_cite.category_discovery.Anthropic` and assign a `SimpleNamespace` client whose
`messages.create` is a controllable mock. Follow that pattern so the classification is
testable without a real API key.

## Implementation

### `wiki_cite/category_discovery.py` (new module)

**Files:**
- Create: `wiki_cite/category_discovery.py`

**Module-level setup:**
```python
import concurrent.futures
import json
import logging

from anthropic import Anthropic

from wiki_cite.config import get_config

logger = logging.getLogger(__name__)
```

**Classification prompt (module constant):**
Write a `CLASSIFY_SYSTEM_PROMPT` explaining the judgment: given a list of Wikipedia category
names under a topic root, decide for each whether it is likely to contain actual
content/biography/topic articles (KEEP) vs. Wikipedia-internal bookkeeping (EXCLUDE).
- EXCLUDE examples to name in the prompt: task-force categories ("... task force"),
  quality/assessment ("... articles by quality", "... importance"), participant/WikiProject
  ("... participants", "WikiProject ..."), maintenance/tracking categories.
- KEEP: topical/biographical subcategories AND `...stubs` categories (this tool specifically
  targets stub articles, so stub categories are content-relevant — call this out explicitly
  per AC2.1).
- Instruct the model to respond with **only** a JSON object mapping each input category name
  verbatim to a boolean `true` (keep) / `false` (exclude), e.g.
  `{"20th-century American politicians": true, "American politics task force": false}`.

**Functions:**

```python
def classify_categories(
    names: list[str],
    *,
    client: Anthropic | None = None,
    batch_size: int = 20,
    max_workers: int = 4,
) -> list[str]:
    """Classify category names concurrently; return the accepted (content-relevant) names.

    Batches ``names`` (default 20/call), dispatches batches across a ThreadPoolExecutor
    (Anthropic traffic — no Wikipedia etiquette constraint applies), and unions the KEEP
    results. Fail-closed: any batch whose call errors or whose response can't be parsed
    contributes NO accepted names (every name in it defaults to excluded) and is logged —
    the rest of the batches still complete.
    """
```
- If `client is None`, build one: `client = Anthropic(api_key=get_config().anthropic_api_key)`
  (mirror `agent.py`).
- De-duplicate `names` while preserving determinism (work from `sorted(set(names))`).
- Split into batches of `batch_size`.
- Submit `_classify_batch(client, model, batch)` for each batch to a
  `ThreadPoolExecutor(max_workers=max_workers)`; collect results as they complete.
- On a future raising, `logger.warning("Classification batch failed: %s", e)` and treat the
  whole batch as excluded (AC2.2 — fail closed).
- Return `sorted(accepted)` where `accepted` is the union of names each batch marked KEEP.

```python
def _classify_batch(client, model: str, names: list[str]) -> list[str]:
    """One Anthropic call classifying a batch; returns the KEEP names. Fail-closed:
    on a missing/unparseable/partial response, only names explicitly marked true are kept."""
```
- Build a user message listing the batch names and call
  `client.messages.create(model=model, max_tokens=..., system=CLASSIFY_SYSTEM_PROMPT,
  messages=[{"role": "user", "content": <the listed names + JSON-only instruction>}])`.
  (A plain messages call is sufficient; the agentic tool-loop is not needed here. Do NOT
  pass `thinking`/`output_config` unless you confirm the mocked shape in tests tolerates it —
  keep the call minimal.)
- Concatenate `block.text for block in response.content if block.type == "text"`.
- Parse via a helper `_parse_keep_map(text, names) -> dict[str, bool]` that:
  - Extracts the first JSON object from the text (tolerate code fences / surrounding prose,
    similar in spirit to `agent._extract_json_from_response`).
  - Returns `{name: bool(...)}` only for names present in the response; a name absent from
    the parsed map, or any `json.JSONDecodeError`, yields that name defaulting to `False`
    (excluded) — fail closed.
- Return `[name for name in names if keep_map.get(name, False)]`.
- Wrap the call+parse in `try/except Exception`: on error, `logger.warning(...)` and return
  `[]` (whole batch excluded). `classify_categories` also guards at the future level; both
  layers fail closed.

**Notes:**
- Model comes from `get_config().agent.model` — reuse the same model the agent uses; do not
  introduce a second model config.
- No Wikipedia calls in this module. No file I/O yet (Phase 3 adds the write/loader helpers —
  they can live in this module since they are pure JSON I/O and do not import Anthropic-only
  state, but keep them as separate functions).

**Tests:** (Phase 6 owns AC mapping; these are the shapes to cover)
- AC2.1: with a mocked client returning a KEEP/EXCLUDE JSON map, maintenance-style names are
  dropped and topical/`...stubs` names are kept.
- AC2.2: a batch whose `messages.create` raises, or returns malformed/non-JSON text, results
  in every name in that batch excluded (not kept), and the other batches still classify.

---

## Verification

Run: `uv run pytest tests/test_category_discovery.py -q` (test file created in Phase 6; if
running this phase standalone, verify import: `uv run python -c "from wiki_cite.category_discovery import classify_categories"`).
Also: `uv run ruff check wiki_cite/category_discovery.py`
Expected: module imports cleanly; no real network call happens without a client.

## Commit

`feat: add parallel Anthropic relevance classification for categories`
