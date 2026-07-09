# Phase 7: Provenance columns wired for #4 (DORMANT until #4 lands)

**Goal:** Once #4's agentic search loop populates `ProposedEdit.source` (source type,
reliability) and exposes which tool/API found a source, fill the `source_api`/`source_type`/
`reliability` columns in the `record_outcome` calls instead of leaving them NULL.

**Status:** **Do not implement now.** Phases 1–6 do not depend on this. This phase is a no-op
until #4 has shipped `ProposedEdit.source` population from the agentic loop. It is documented
here so the wiring is not forgotten and the schema's nullable columns have a clear owner.

**ACs covered:** none in this issue's core set (AC6.2 explicitly requires the loop to work
*without* this phase). When #4 lands, this phase makes `dimension_rates("source_api")` return
real data.

## Preconditions (verify before starting)

- #4 is merged and `ProposedEdit.source` is routinely populated (today it is `None`;
  models.py line 143 `source: Source | None = None`).
- #4 exposes which search API found a source. Per the #4 design referenced in this design doc,
  that provenance lives in the agentic loop's tool dispatch
  (`wiki_cite/agent.py` `_dispatch_search_tool` per #4's design). Confirm the actual field
  name/shape #4 shipped before wiring — do not assume `source_api` exists; grep for it.

## Changes (when unblocked)

- `wiki_cite/web_app.py`:
  - In the **propose** recording (Phase 1, `scan_events`): `source_type`/`reliability` already
    read from `edit.source` (they become non-NULL automatically once #4 populates `source`).
    Add `source_api=<provenance from #4>` to those `record_outcome` calls.
  - In **approve/reject** (Phase 2): same — add `source_api=...`.
  - In **push** (Phase 1): same.
- Wherever #4 records tool-call provenance (`wiki_cite/agent.py`): ensure the API name is
  carried onto the `Source`/`ProposedEdit` so `web_app` can read it at record time. If #4
  attaches it to `Source`, add a field there; otherwise thread it through the event payload
  `analyze_article_events` already yields.
- Add `source_api` to `STATS_DIMENSIONS` consumers if not already present (Phase 3 already lists
  it; it simply shows "no data" until now).

## Tests (when unblocked)

- With #4 implemented, a full scan → approve cycle produces outcomes rows with non-null
  `source_api`; `dimension_rates("source_api")` returns real values.
- Until then, the existing Phase 1–6 tests already assert these columns stay NULL and
  `dimension_rates("source_api")` returns `{}` (AC6.2) — no new test needed while dormant.

## Done when (deferred)

- #4 is merged, and a full scan→approve cycle yields outcomes rows with non-null `source_api`.
- **If #4 has not shipped, this phase stays closed and untouched.**
