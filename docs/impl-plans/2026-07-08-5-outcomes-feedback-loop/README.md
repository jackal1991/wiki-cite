# Implementation Plan: Outcomes Feedback Loop (#5)

Design: `docs/design-plans/2026-07-08-5-outcomes-feedback-loop.md`

## Goal

Widen `SeenStore` into an append-only `outcomes` log, persist review-UI
approve/reject decisions (currently lost on restart), surface aggregate
approval rates via `wiki-cite stats` / `/stats`, and feed those learned rates
back into `ArticlePicker.fetch_candidates` as a ranking signal with an
exploration epsilon — all degrading gracefully when the DB is missing/corrupt
or before #4 populates source provenance.

## Phase sequence

| Phase | File | Scope | ACs |
|---|---|---|---|
| 1 | `phase-1-outcomes-schema.md` | `outcomes` table + `record_outcome`/`dimension_rates`; wire skip/propose/push | AC1, AC6.1 (partial) |
| 2 | `phase-2-persist-review-decisions.md` | Persist approve/reject from the review UI | AC2 |
| 3 | `phase-3-stats-surface.md` | `wiki-cite stats` CLI + `/stats` route + `stats.html` | AC3 |
| 4 | `phase-4-feedback-config-pooling.md` | `FeedbackConfig` + candidate pooling (inert reorder) | (pooling plumbing) |
| 5 | `phase-5-candidate-scorer.md` | `CandidateScorer` + wire into `fetch_candidates` | AC4, AC5 |
| 6 | `phase-6-degrade-hardening.md` | Explicit degrade paths + tests | AC6 |
| 7 | `phase-7-provenance-for-4.md` | Populate `source_api`/`source_type`/`reliability` (dormant until #4) | (deferred) |

Phases 1–6 are independent of #4. Phase 7 stays a no-op until #4 lands.

## Conventions to honor

- `uv run pytest` is the test command; coverage + branch coverage are on by default.
- `uv run ruff check .` is the only style gate (line-length 300, E/F/W). No black, no mypy.
- Tests live in `tests/` as `test_<module>.py`, `tmp_path` fixtures, no mocking of sqlite
  (per existing `test_seen_store.py` style); `test_article_picker.py` builds fake `mwclient`
  pages with `Mock`.
- Commits are LOCAL ONLY — never push.
