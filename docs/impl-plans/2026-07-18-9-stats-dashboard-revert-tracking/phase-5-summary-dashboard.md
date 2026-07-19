# Phase 5: Summary dashboard view

**Goal:** Add a top-of-page summary block to the existing `/stats` route showing
distinct pushed-article count, revert rate, and approve/reject rate — computed
from the `outcomes` table. Additive to the per-dimension tables already there.

**ACs covered:** AC4.1 (distinct pushed count, revert rate, approve/reject rate
from existing outcomes data), AC4.2 (empty/unreadable store → "no data yet"
state, not an error — reuse the existing `store_ok` pattern).

**Depends on:** Phase 1 (`"pushed"` rows), Phase 2 (`"reverted"` rows). The
summary is meaningful even before any revert exists (revert rate = 0).

## Design of the aggregation

`dimension_rates` aggregates a *column's* values; the summary counts *outcome
types*, so it needs a small new query. Keep the SQL in `SeenStore` and the pure
rate arithmetic in `stats.py` (where `STATS_DIMENSIONS` already lives) so both
the route and any future CLI printout share one definition and can't drift.

### `wiki_cite/seen_store.py` — `summary_counts`

```python
def summary_counts(self) -> dict[str, int]:
    """Raw outcome tallies for the dashboard summary. Distinct-article counts
    for pushed/reverted (an article is one push regardless of edit-row count),
    raw row counts for approved/rejected (edit-grain review decisions)."""
```

- Guard `self._conn is None` → return `{}`; wrap in `try/except sqlite3.Error` →
  `logger.warning(...)`, return `{}` (existing degrade style).
- Run under `self._lock`. Return keys:
  - `pushed_articles`: `SELECT COUNT(DISTINCT article_title) FROM outcomes WHERE outcome = 'pushed'`
  - `reverted_articles`: `... WHERE outcome = 'reverted'`
  - `approved_edits`: `SELECT COUNT(*) FROM outcomes WHERE outcome = 'approved'`
  - `rejected_edits`: `... WHERE outcome = 'rejected'`
- Distinct on `article_title` for pushed/reverted matches the "pushed-article
  count" wording (phase 1 writes one pushed row per approved edit, so a raw count
  would over-count multi-edit pushes). Revert rate then reads as reverted-articles
  over pushed-articles — apples to apples.

### `wiki_cite/stats.py` — pure summary computation

Add a pure function (no I/O) that turns raw counts into display-ready rates,
returning `None` for a rate when its denominator is 0 (the template renders "—"
rather than dividing by zero):

```python
def compute_summary(counts: dict[str, int]) -> dict:
    pushed = counts.get("pushed_articles", 0)
    reverted = counts.get("reverted_articles", 0)
    approved = counts.get("approved_edits", 0)
    rejected = counts.get("rejected_edits", 0)
    review_total = approved + rejected
    return {
        "pushed_articles": pushed,
        "reverted_articles": reverted,
        "revert_rate": (reverted / pushed) if pushed else None,
        "approved_edits": approved,
        "rejected_edits": rejected,
        "approval_rate": (approved / review_total) if review_total else None,
        "has_data": bool(pushed or review_total),
    }
```

`has_data` drives AC4.2's "no data yet" state — an empty outcomes table yields
all-zero counts and `has_data=False`.

### `wiki_cite/web_app.py` — `stats_page` (currently line 440)

Fold the summary into the **existing** `try/except sqlite3.Error` block so a
broken DB flips the same `store_ok` flag (AC4.2 reuses the AC6 pattern from #5):

```python
@app.route("/stats")
def stats_page():
    store_ok, dimensions, summary = True, {}, None
    try:
        summary = compute_summary(seen_store.summary_counts())
        for dimension in STATS_DIMENSIONS:
            dimensions[dimension] = {v: (s, t) for v, (s, t) in seen_store.dimension_rates(dimension).items() if t > 0}
    except sqlite3.Error:
        store_ok = False
    return render_template("stats.html", dimensions=dimensions, summary=summary, store_ok=store_ok)
```

Import `compute_summary` from `wiki_cite.stats` (alongside the existing
`STATS_DIMENSIONS` import, web_app.py:20).

### `wiki_cite/templates/stats.html`

Add a summary block **above** the existing per-dimension loop, inside the first
`.panel` region (mirror the existing panel/section styling — no new external
assets). Render percentages only when the rate is not `None`:

```jinja
{% if summary and summary.has_data %}
<div class="section-head"><h2>Summary</h2></div>
<div class="panel">
    <table style="width:100%; border-collapse:collapse;">
        <tr><td>Articles pushed</td><td class="mono" style="text-align:right;">{{ summary.pushed_articles }}</td></tr>
        <tr><td>Reverted</td>
            <td class="mono" style="text-align:right;">
                {{ summary.reverted_articles }}{% if summary.revert_rate is not none %} ({{ "%.0f"|format(summary.revert_rate * 100) }}%){% endif %}
            </td></tr>
        <tr><td>Approve / reject</td>
            <td class="mono" style="text-align:right;">
                {{ summary.approved_edits }}/{{ summary.rejected_edits }}{% if summary.approval_rate is not none %} ({{ "%.0f"|format(summary.approval_rate * 100) }}% approved){% endif %}
            </td></tr>
    </table>
</div>
{% elif store_ok %}
<div class="panel"><p class="muted">No pushes recorded yet.</p></div>
{% endif %}
```

The existing `{% if not store_ok %}` banner (stats.html:13-15) already covers the
unreadable-DB case, so the summary just needs the empty-data branch. Keep the
per-dimension loop below unchanged.

## Tests (`tests/test_web_app.py`, `tests/test_stats.py`)

- `tests/test_stats.py` (new or extend): `test_compute_summary_rates` — feed
  `{"pushed_articles": 10, "reverted_articles": 2, "approved_edits": 8, "rejected_edits": 2}`
  and assert `revert_rate == 0.2`, `approval_rate == 0.8`, `has_data is True`.
- `test_compute_summary_zero_denominators` (AC4.2): all-zero counts →
  `revert_rate is None`, `approval_rate is None`, `has_data is False`. Assert no
  `ZeroDivisionError`.
- `tests/test_web_app.py::test_stats_summary_renders` (AC4.1): seed the DB with a
  `"pushed"` row and a `"reverted"` row for the same article plus approved/rejected
  rows, GET `/stats`, assert 200 and the body contains the pushed count and a
  revert percentage.
- `test_stats_summary_empty_db` (AC4.2): fresh empty DB → GET `/stats` returns
  200 with the "No pushes recorded yet" state, never a 500.

## Done when

- `uv run pytest tests/test_stats.py tests/test_web_app.py` passes.
- `/stats` shows pushed count, revert rate, and approve/reject rate above the
  dimension tables; an empty DB shows the no-data state, not a traceback.
- `uv run ruff check .` clean.
