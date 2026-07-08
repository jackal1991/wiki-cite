# Outcomes Feedback Loop Design

GitHub issue: #5

## Summary

Widen `SeenStore` from a single-column "have we touched this title" record into an **outcomes table** that captures, per article and per edit, the characteristics of what was scanned (article topic/shape, citation type/provenance) alongside what happened to it (skipped / proposed / approved / rejected / pushed). The highest-value new capture is durable: **per-edit approve/reject decisions from the review UI, currently held only in the in-memory `proposals` dict, are lost on process restart.** A new `wiki-cite stats` command and `/stats` route aggregate the table into approval/success rates by dimension (source type, finding API, edit type, article length bucket, etc.). Those learned rates are then fed back into `ArticlePicker.fetch_candidates` as a **ranking signal with an exploration epsilon**, so future scans spend Claude calls on articles that resemble past successes without ever fully starving a dimension of new attempts.

This closes the loop: **outcomes are captured → aggregated into rates → rates re-rank future candidates → new outcomes refine the rates.**

## Definition of Done

- `SeenStore` (or a store built directly on its schema/connection) has a widened table recording article characteristics, citation characteristics, and an `outcome` enum with timestamp, for every skip/propose/approve/reject/push event.
- Approve/reject decisions made in the review UI (`/api/proposals/<id>/approve-edit/<i>`, `/reject-edit/<i>`) are persisted to that table synchronously with the existing in-memory mutation — a server restart does not lose them.
- A `wiki-cite stats` CLI command and a `/stats` web route both render the same aggregation: approval/success rate grouped by each recorded dimension, with sample counts (never a bare percentage with an unstated `n`).
- `ArticlePicker.fetch_candidates` scores and ranks/filters candidates using per-dimension rates learned from the outcomes table, before an article is sent to `ClaudeAgent` for analysis (i.e., before a Claude call is spent on it).
- Scoring includes an exploration epsilon so a dimension with zero or few samples is not permanently deprioritized — the picker still occasionally tries under-sampled or historically-low dimensions so they can accumulate data.
- The outcomes table works standalone (no dependency on #4): citation-characteristic columns that only #4's agentic loop can populate (provenance API, verified source type) are nullable and simply absent until #4 ships.
- A missing, empty, or corrupt outcomes DB degrades `fetch_candidates` to today's unweighted category-order behavior — it never raises out of the fetch path.

**Out of scope:** the agentic search loop itself (#4), any change to the guardrail/approval UI beyond adding persistence calls, and any admin UI for editing or purging outcome rows.

## Acceptance Criteria

### 5-outcomes-feedback-loop.AC1: Outcomes are recorded at every existing capture point
- **AC1.1 Success:** a full pass through `scan_events` (skip a candidate, then select one, then approve one edit and reject another via the review endpoints, then push) produces one outcomes row per skip/propose/approve/reject/push event, each with the correct `outcome` value, `article_title`, and a timestamp.
- **AC1.2 Failure:** an outcomes-recording call that raises (e.g. a locked/corrupt DB file) aborts `scan_events` or an approve/reject request — recording must be wrapped so a storage failure degrades to "outcome not recorded" (logged), not a 500 on the review UI or a broken scan.

### 5-outcomes-feedback-loop.AC2: Approve/reject decisions survive a restart
- **AC2.1 Success:** after approving edit 0 and rejecting edit 1 of a proposal, restarting the Flask process and re-querying the outcomes table (directly, or via `/stats`) shows both decisions with their edit-level dimensions (`edit_type`, `confidence`, `source type` if known) intact. (The in-memory `proposals` dict itself is still lost on restart — this AC is about the outcomes row, not proposal replay.)
- **AC2.2 Failure:** an implementation that only writes the decision to `ProposedEdit.approved` in memory, with no corresponding outcomes-table write in the approve/reject route handlers, fails this AC even if `/stats` looks correct for the current process's lifetime.

### 5-outcomes-feedback-loop.AC3: Stats surface aggregates correctly and never divides by zero
- **AC3.1 Success:** given N outcome rows for a dimension value (e.g. `source_type=news`) with K approvals, `wiki-cite stats` and `/stats` both report `K/N` for that value with `N` shown alongside the rate; dimension values with `N=0` are omitted or shown as "no data" rather than computed.
- **AC3.2 Failure:** a rate computed as `approved / (approved + rejected)` that raises `ZeroDivisionError` when both are 0, or that silently reports `0%` (indistinguishable from "always rejected") for a dimension with zero samples, is rejected.

### 5-outcomes-feedback-loop.AC4: Learned rates re-rank candidates before a Claude call is spent
- **AC4.1 Success:** given an outcomes history where `source_type=news` edits have a much higher approval rate than `source_type=journal` edits, and two candidate articles whose recorded categories/characteristics correlate with each source type respectively, `fetch_candidates` yields the higher-scored (news-correlated) candidate before the lower-scored one, within the same `limit`-bounded scan — without any additional Claude/API calls being made to determine the order.
- **AC4.2 Failure:** an implementation that reorders candidates only *after* calling `ClaudeAgent.analyze_article_events` on each of them (i.e. spends the Claude call the ranking was supposed to avoid) does not satisfy this AC, even if the final order is correct.

### 5-outcomes-feedback-loop.AC5: Exploration epsilon prevents a feedback death-spiral
- **AC5.1 Success:** a dimension value with zero or very few (`< min_samples`) recorded outcomes is still selected for scanning with probability ≥ epsilon per scan (or is given a neutral/prior score rather than a score of 0), so it can accumulate outcomes over repeated fetches instead of being permanently starved by dimensions with an established high rate.
- **AC5.2 Failure:** a scoring function that multiplies a raw empirical rate (defaulting to 0 for zero samples) straight into a hard filter or a rank with no floor/epsilon — so a never-tried dimension always sorts last and is never picked again — is rejected.

### 5-outcomes-feedback-loop.AC6: Degrades gracefully without an outcomes DB or with #4 not yet implemented
- **AC6.1 Success:** deleting the outcomes DB file (or pointing at a fresh empty one) and running a fetch produces the same candidate order as today's unweighted category-order scan — `fetch_candidates` does not raise, and no scoring step assumes non-empty history.
- **AC6.2 Success:** running the outcomes/stats/scoring code before #4 lands (i.e. `ProposedEdit.source` still routinely `None`, no provenance API recorded) still records article-level dimensions and approve/reject outcomes correctly; provenance-dependent aggregates (e.g. "approval rate by finding API") simply show no data rather than erroring.
- **AC6.3 Failure:** any code path in `fetch_candidates`, `ArticlePicker.__init__`, or `create_app()` that throws (rather than logging and falling back) when the outcomes DB is missing, unreadable, or has an old/partial schema is rejected.

## Architecture

### Chosen approach: widen `SeenStore`'s schema in place, add a scorer consumed by the picker

Keep one sqlite file and one connection-owning class — `SeenStore` already solves the concurrency problem (`check_same_thread=False` + a lock, shared across the Flask dev server's worker thread) that a second store would have to re-solve. Add a second table, `outcomes`, alongside the existing `seen_articles` table in the same file/connection, and add narrow methods to `SeenStore` for recording rows and reading aggregates. `seen_articles` keeps its current job (cheap "already processed" title lookup); `outcomes` is an append-only log of everything that happened, at both article and edit grain, joined loosely by `article_title` + `revision_id` (no foreign key — sqlite in this codebase favors simple, defensive schemas over relational integrity, and an outcomes log should never fail to insert because a parent row is missing).

#### Schema sketch

```sql
CREATE TABLE IF NOT EXISTS outcomes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    article_title       TEXT NOT NULL,
    revision_id         TEXT,
    outcome             TEXT NOT NULL,   -- skipped | proposed | approved | rejected | pushed
    recorded_at         TEXT NOT NULL,

    -- Article characteristics (known at candidate-fetch time; NULL for edit-only rows if not re-supplied)
    categories          TEXT,            -- JSON list, e.g. '["American film actors", ...]'
    body_line_count     INTEGER,
    has_infobox         INTEGER,         -- 0/1
    citation_needed_count INTEGER,

    -- Citation/edit characteristics (NULL for article-level skipped rows)
    edit_type           TEXT,            -- citation | grammar | style | wikilink | policy | formatting
    confidence          TEXT,            -- high | medium | low
    source_type         TEXT,            -- journal | news | book | web | government
    source_api          TEXT,            -- semantic_scholar | crossref | web (NULL until #4 lands)
    reliability          TEXT,           -- generally_reliable | situationally_reliable | ...
    policy_reference    TEXT
)
```

`categories` is stored as a JSON-encoded list (mirrors the existing "no extra dependency, stdlib sqlite3" constraint — no array column type, no join table). Aggregation queries that need per-category rates decode it in Python; this repo's outcome volumes (tens to low thousands of rows) don't justify a normalized categories table.

#### `SeenStore` additions

```python
class SeenStore:
    ...
    def record_outcome(
        self,
        article_title: str,
        revision_id: str | None,
        outcome: str,                       # "skipped" | "proposed" | "approved" | "rejected" | "pushed"
        *,
        categories: list[str] | None = None,
        body_line_count: int | None = None,
        has_infobox: bool | None = None,
        citation_needed_count: int | None = None,
        edit_type: str | None = None,
        confidence: str | None = None,
        source_type: str | None = None,
        source_api: str | None = None,
        reliability: str | None = None,
        policy_reference: str | None = None,
    ) -> None:
        """Append one outcomes row. Never raises past the caller — logs and
        swallows sqlite errors so a storage hiccup can't break a scan or a
        review-UI click (AC1.2)."""

    def dimension_rates(self, dimension: str, success_outcomes: tuple[str, ...] = ("approved", "pushed")) -> dict[str, tuple[int, int]]:
        """Return {value: (successes, total)} for a given column (e.g. "source_type"),
        counting rows whose outcome is in success_outcomes as successes and all
        recorded rows for that value as the denominator. Never divides — callers
        compute the rate and must handle total == 0."""
```

`record_outcome` wraps its `INSERT` in a `try/except sqlite3.Error`, logs, and returns — matching AC1.2's requirement that storage failures degrade rather than propagate. `dimension_rates` does no division; it returns raw counts so every caller (CLI, `/stats`, the picker's scorer) makes its own explicit zero-sample decision instead of a shared helper silently defaulting to 0%.

### Capture points wired to `record_outcome`

| Existing code | New call |
|---|---|
| `scan_events`, candidate skipped (no confident citation) | `record_outcome(title, revision_id, "skipped", categories=..., body_line_count=..., has_infobox=..., citation_needed_count=...)` |
| `scan_events`, candidate selected | `record_outcome(title, revision_id, "proposed", ...)` once per edit in `proposal.edits` (so citation characteristics are captured at the edit grain used later for AC4 scoring) |
| `web_app.approve_edit` | `record_outcome(proposal.article.title, proposal.article.revision_id, "approved", edit_type=edit.edit_type.value, confidence=edit.confidence, source_type=edit.source.source_type.value if edit.source else None, reliability=edit.source.reliability.value if edit.source and edit.source.reliability else None, policy_reference=edit.policy_reference)` |
| `web_app.reject_edit` | same shape, `outcome="rejected"` |
| `web_app.push_proposal`, on success | `record_outcome(..., "pushed")` — already sits next to the existing `seen_store.mark_seen(..., "pushed")` call |

`edit.source` is `None` today (per the issue) for most edits — `source_type`/`reliability`/`source_api` columns simply stay `NULL` until #4 populates `ProposedEdit.source` from the agentic loop's tool results. This is why AC6.2 exists: the design must not assume those columns are populated.

### Stats surface

`SeenStore.dimension_rates` is the single aggregation primitive; `wiki-cite stats` and `/stats` are two thin renderers over the same dimensions list (`source_type`, `source_api`, `edit_type`, `confidence`, `has_infobox`, a bucketed `body_line_count`, and `categories` exploded per-value):

```python
# cli.py
def cmd_stats(args):
    store = SeenStore(get_config().seen_db_path)
    for dimension in STATS_DIMENSIONS:
        print(f"\n{dimension}:")
        for value, (successes, total) in sorted(store.dimension_rates(dimension).items()):
            if total == 0:
                continue  # AC3.1: never show a rate with n=0
            print(f"  {value:<30} {successes}/{total} ({successes / total:.0%})")
```

```python
# web_app.py
@app.route("/stats")
def stats_page():
    store_ok, dimensions = True, {}
    try:
        for dimension in STATS_DIMENSIONS:
            dimensions[dimension] = {v: (s, t) for v, (s, t) in seen_store.dimension_rates(dimension).items() if t > 0}
    except sqlite3.Error:
        store_ok = False
    return render_template("stats.html", dimensions=dimensions, store_ok=store_ok)
```

### Chosen approach: feeding rates back into `ArticlePicker.fetch_candidates`

`fetch_candidates` today is a pure generator over `cat_page` in Wikipedia's category order, yielding the first `limit` candidates that pass `is_candidate`. To rank by learned success rate *before* spending a Claude call, the picker needs a **lookahead buffer**: pull a larger pool of candidates than `limit` from the category (cheap — title + `is_candidate` checks, no Claude call), score each against the learned rates, then yield in score order, capped at `limit`. This preserves "no extra Claude calls spent to rank" (AC4.2) at the cost of reading a bit further into the category per fetch — bounded by a new `article_selection.candidate_pool_size` config (default e.g. 30), itself a cheap-relative-to-Claude cost guard in the same spirit as `max_wikitext_chars`.

```python
class CandidateScorer:
    """Turns learned per-dimension outcome rates into a candidate score.
    Pure function of (candidate, rates) — no I/O, so it's unit-testable
    without sqlite or Wikipedia."""

    def __init__(self, rates: dict[str, dict[str, tuple[int, int]]], epsilon: float, min_samples: int):
        self._rates = rates          # {dimension: {value: (successes, total)}}
        self._epsilon = epsilon
        self._min_samples = min_samples

    def score(self, candidate: CandidateArticle) -> float:
        """Blend the candidate's known article-level dimensions' success rates.
        Falls back to a neutral 0.5 prior when a dimension/value has fewer than
        min_samples observations, so under-sampled candidates aren't starved
        (AC5). Adds independent epsilon-random jitter so even well-observed,
        low-rate dimensions occasionally surface (AC5.1)."""
        scores = []
        for category in candidate.categories:
            successes, total = self._rates.get("categories", {}).get(category, (0, 0))
            scores.append(successes / total if total >= self._min_samples else 0.5)
        has_infobox_key = str(candidate.has_infobox)
        successes, total = self._rates.get("has_infobox", {}).get(has_infobox_key, (0, 0))
        scores.append(successes / total if total >= self._min_samples else 0.5)

        base = sum(scores) / len(scores) if scores else 0.5
        return base + random.random() * self._epsilon    # jitter: never a strict, sticky ordering
```

`ArticlePicker.fetch_candidates` uses it like:

```python
def fetch_candidates(self, limit: int = 100) -> Iterator[CandidateArticle]:
    ...
    pool_size = self.config.article_selection.candidate_pool_size
    scorer = self._build_scorer()   # None if outcomes DB missing/empty/corrupt -> AC6.1
    pool: list[CandidateArticle] = []
    for page in cat_page:
        if len(pool) >= pool_size:
            break
        if self.seen_store is not None and self.seen_store.is_seen(page.name):
            continue
        is_candidate, _ = self.is_candidate(page)
        if not is_candidate:
            continue
        pool.append(self._build_candidate(page))

    ranked = sorted(pool, key=scorer.score, reverse=True) if scorer else pool
    yield from ranked[:limit]
```

`_build_scorer` wraps `SeenStore.dimension_rates` calls in `try/except sqlite3.Error` and returns `None` on any failure — `ranked = pool` (today's unweighted category order) in that case, satisfying AC6.1/AC6.3. When `pool_size <= limit` (e.g. a small category or an early config), behavior is unchanged from today aside from the harmless full-pool scoring pass.

### Config additions

```yaml
# config.yaml
article_selection:
  candidate_pool_size: 30    # how many candidates to look ahead & rank before yielding `limit`

feedback:
  enabled: true
  epsilon: 0.15              # exploration jitter added to every candidate score
  min_samples: 5             # dimension values below this sample count get the neutral 0.5 prior
```

```python
# config.py
class ArticleSelectionConfig(BaseSettings):
    ...
    candidate_pool_size: int = 30

class FeedbackConfig(BaseSettings):
    """Configuration for the outcomes-feedback loop that re-ranks candidates."""

    enabled: bool = True
    epsilon: float = 0.15
    min_samples: int = 5

class Config(BaseSettings):
    ...
    feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)
```

`feedback.enabled: false` is the manual escape hatch alongside the automatic degrade-to-unscored path (AC6) — useful for isolating a regression to the scorer versus the picker itself.

## Existing Patterns

- `SeenStore` already owns the sqlite connection lifecycle (`check_same_thread=False`, a `threading.Lock`, `CREATE TABLE IF NOT EXISTS`) — the `outcomes` table reuses the same connection and lock rather than opening a second file.
- `web_app.py`'s approve/reject/push routes already call `seen_store.mark_seen(...)` at the push step — `record_outcome` calls slot into the same routes next to the existing mutations, not a new subsystem.
- `Config` is Pydantic `BaseSettings` with nested config classes mirrored in `config.yaml` (`AgentConfig`, `ArticleSelectionConfig`, etc.) and `extra="ignore"` — `FeedbackConfig` follows that exact shape, and `Config.load` needs the same `if "feedback" in yaml_config: config_data["feedback"] = FeedbackConfig(**yaml_config["feedback"])` block added.
- `cli.py` already has a flat `cmd_*` + `argparse` subparser pattern (`cmd_fetch_articles`, `cmd_config`) — `cmd_stats` follows it exactly, added as a new subparser in `main()`.
- `ArticlePicker.fetch_candidates` is already a generator, and `is_candidate`/`_build_candidate`-shaped helpers already exist inline in the loop body — the pool/rank rework factors the per-page candidate construction into a small helper so both the current inline code and the new pooling loop share it, rather than duplicating the `CandidateArticle(...)` construction.
- `models.py`'s `EditType`, `SourceType`, `ReliabilityRating` are already `Enum`s with `.value` strings — outcome rows store `.value` (plain `TEXT`), matching how `get_edit_summary` already reads `edit_type.value`.
- Tests for `SeenStore` (`tests/test_seen_store.py`) use `tmp_path` fixtures and a fresh `SeenStore` per test with no mocking — `test_seen_store.py` gets outcomes-table tests in the same style; `test_article_picker.py` already builds fake `mwclient` pages for `fetch_candidates` tests, so scorer/ranking tests extend that fixture rather than hitting real Wikipedia.

## Implementation Phases

### Phase 1: Widen the outcomes schema + record skip/propose/push
**Goal:** add the `outcomes` table and `record_outcome`/`dimension_rates` to `SeenStore`; wire it into the capture points that already exist in `scan_events` and `push_proposal` (skip, propose, push — no UI change needed).
**Components:** `wiki_cite/seen_store.py`, `wiki_cite/web_app.py` (`scan_events`, `push_proposal`).
**Done when:** AC1, AC6.1 (partial) — a scan that skips one candidate, selects another, and pushes it produces three outcomes rows with the right dimensions; deleting the DB file and re-running doesn't crash the scan.

### Phase 2: Persist approve/reject decisions from the review UI
**Goal:** the single highest-value capture from the issue — make `approve_edit`/`reject_edit` durable.
**Components:** `wiki_cite/web_app.py` (`approve_edit`, `reject_edit` routes).
**Done when:** AC2 — approve/reject calls in a test client produce outcomes rows readable after the app object is torn down and rebuilt against the same DB file (simulating a restart), independent of Phase 1's picker work.

### Phase 3: Stats CLI + web route
**Goal:** `wiki-cite stats` and `/stats` rendering `dimension_rates` output, with a `stats.html` template following the existing `base.html`/`index.html` structure.
**Components:** `wiki_cite/cli.py` (`cmd_stats`), `wiki_cite/web_app.py` (`/stats` route), `wiki_cite/templates/stats.html`.
**Done when:** AC3 — unit tests seed known outcome rows and assert the printed/rendered rates match `successes/total`, with zero-sample dimension values excluded, not shown as 0%.

### Phase 4: `FeedbackConfig` + candidate pooling in `ArticlePicker`
**Goal:** add `candidate_pool_size`/`feedback.*` config; rework `fetch_candidates` to pull a pool ahead of `limit` (no scoring yet — this phase only proves the pooling/ordering plumbing is inert, i.e. pool order == today's order when no scorer is active).
**Components:** `wiki_cite/config.py`, `config.yaml`, `wiki_cite/article_picker.py`.
**Done when:** existing `test_article_picker.py` fetch-order tests still pass unmodified with `feedback.enabled: false` (default pool behavior is a no-op reorder).

### Phase 5: `CandidateScorer` + wire into `fetch_candidates`
**Goal:** implement the pure scoring function (rate blend + neutral prior for under-sampled dimensions + epsilon jitter) and use it to sort the pool.
**Components:** `wiki_cite/article_picker.py` (new `CandidateScorer` class or module-level function), `wiki_cite/seen_store.py` (`dimension_rates` already exists from Phase 1).
**Done when:** AC4, AC5 — a seeded outcomes history with a clear rate gap between two dimension values produces the predicted candidate order; a dimension with 0 samples still scores at the neutral prior (not 0), and repeated runs occasionally surface a low-rate/under-sampled candidate ahead of a high-rate one (epsilon > 0 verified via a fixed random seed in the test).

### Phase 6: Full degrade-path hardening + tests
**Goal:** make every failure mode in AC6 explicit and tested — missing DB file, corrupt file, old-schema file (pre-Phase-1 `seen_articles`-only DB), `feedback.enabled: false`.
**Components:** `wiki_cite/seen_store.py`, `wiki_cite/article_picker.py`, `tests/test_seen_store.py`, `tests/test_article_picker.py`.
**Done when:** AC6 — a corrupted DB file (or one from before this design, containing only `seen_articles`) does not raise anywhere in `create_app()` or `fetch_candidates`; the picker silently falls back to unweighted order, and `/stats`/`wiki-cite stats` render a "no data" state instead of a 500/traceback.

### Phase 7: Provenance columns wired for #4 (sequenced after #4 lands)
**Goal:** once #4's agentic loop populates `ProposedEdit.source` (source type, reliability) and exposes which tool/API found it, extend the `record_outcome` calls in `scan_events`/approve/reject to fill `source_api`/`source_type`/`reliability` from that data instead of leaving them `NULL`.
**Components:** `wiki_cite/web_app.py`, wherever #4 lands its tool-call provenance (`wiki_cite/agent.py` per the #4 design's `_dispatch_search_tool`).
**Done when:** with #4 implemented, a full scan → approve cycle produces outcomes rows with non-null `source_api`; `dimension_rates("source_api")` returns real data; **this phase is a no-op / stays dormant if #4 has not shipped yet** — Phases 1–6 do not depend on it.

## Glossary

- **Outcome:** one row in the `outcomes` table — a single skip/propose/approve/reject/push event with the article/citation characteristics known at that moment.
- **Dimension:** a column of the outcomes table used for aggregation (e.g. `source_type`, `edit_type`, `has_infobox`, `categories`) — `dimension_rates(dimension)` returns `{value: (successes, total)}` for it.
- **Candidate pool:** the lookahead buffer `fetch_candidates` reads from the category (size `candidate_pool_size`, ≥ `limit`) before scoring and truncating to `limit` — the mechanism that lets ranking happen without spending extra Claude calls.
- **Exploration epsilon:** the random jitter added to every candidate's score so a dimension is never permanently starved of future attempts just because its empirical rate (or sample count) is currently low.
- **Neutral prior (0.5):** the score assigned to a dimension value with fewer than `min_samples` recorded outcomes — treated as "unknown," not "bad," so it isn't buried by dimensions with an established high rate.
- **Provenance:** which search API found a given source and what type it is — populated from inside #4's agentic tool-use loop once it exists; `NULL` until then.
