# Phase 6: Test coverage sweep

**Goal:** Ensure every AC has direct test coverage and the suite passes green
with coverage/branch-coverage on. Most tests are written inline with phases 1–5;
this phase is a consolidation + gap-fill pass, not a from-scratch test build.

**ACs covered:** all of AC1–AC5 have at least one direct test.

## Test-to-AC map (confirm each exists after phases 1–5)

| AC | Test | File |
|---|---|---|
| AC1.1 capture revid | `test_push_edits_returns_new_revid_from_save`; `test_push_persists_new_revid` | `test_wikipedia_push.py`, `test_web_app.py` |
| AC1.2 no fabricated revid | `test_push_edits_null_edit_returns_none_revid`; `test_push_edits_save_failure_returns_none_revid` | `test_wikipedia_push.py` |
| AC2.1 revert detected | `test_check_article_for_revert_detects_newer_revert`; `test_check_pending_reverts_writes_reverted_row` | `test_revert_checker.py` |
| AC2.2 no false revert | `test_is_revert_revision_ignores_normal_edit`; `test_check_article_for_revert_skips_own_revision`; `test_pending_revert_candidates_excludes_reverted` | `test_revert_checker.py`, `test_seen_store.py` |
| AC2.3 horizon expiry | `test_pending_revert_candidates_excludes_expired` | `test_seen_store.py` |
| AC3.1 CLI summary | `test_cmd_check_reverts_prints_summary` | `test_cli.py` |
| AC3.2 batch failure isolation | `test_cmd_check_reverts_reports_failures`; `test_check_pending_reverts_isolates_failures` | `test_cli.py`, `test_revert_checker.py` |
| AC4.1 summary view | `test_stats_summary_renders`; `test_compute_summary_rates` | `test_web_app.py`, `test_stats.py` |
| AC4.2 empty/broken store | `test_stats_summary_empty_db`; `test_compute_summary_zero_denominators` | `test_web_app.py`, `test_stats.py` |
| AC5.1 config override | `test_revert_tracking_override` | `test_config.py` |
| AC5.2 config default | `test_revert_tracking_default_is_seven` | `test_config.py` |

## Gap-fill tests to add in this phase

These exercise the phase-2 batch entry point end-to-end (the per-phase files
sketch the pure predicate and the candidate query; add the integration-level
`check_pending_reverts` tests here against a real `tmp_path` `SeenStore` + a
`Mock` site):

- `tests/test_revert_checker.py::test_check_pending_reverts_writes_reverted_row`
  (AC2.1 end-to-end): seed a `"pushed"` row (revid `"100"`, recent) in a real
  `SeenStore`; build a `Mock` site whose `pages["Foo"].revisions(...)` yields
  `[{"revid": 100, "tags": [], "comment": "push"}, {"revid": 101, "tags": ["mw-undo"], "comment": "rv"}]`;
  call `check_pending_reverts(site, store, horizon_days=7)`; assert the returned
  summary has `reverts_found == 1` and that a `"reverted"` row now exists for
  `("Foo", "100")` (read it back). Then assert `pending_revert_candidates` no
  longer returns `("Foo", "100")` — the loop is closed (AC2.2).
- `test_check_pending_reverts_no_match_leaves_pending` (AC2.2): revisions yield
  only non-revert edits → `reverts_found == 0`, no `"reverted"` row, candidate
  still pending on a subsequent `pending_revert_candidates` call.
- `test_check_pending_reverts_isolates_failures` (AC3.2): two pending candidates;
  the `Mock` site raises for the first article's `revisions(...)` and succeeds for
  the second. Assert the summary reports one `failure` and still checks/records
  the second (batch not aborted).

## Full-suite verification

- `uv run pytest` passes (coverage + branch coverage are on by default — do not
  lower thresholds; add tests to cover new branches, e.g. the `None`-revid and
  `store is None` degrade paths).
- `uv run ruff check .` clean (line-length 300, E/F/W).
- New modules (`revert_checker.py`) and new methods have branch coverage on their
  degrade/guard paths (`_conn is None`, non-numeric revid, per-article exception).

## Done when

- Every row in the test-to-AC map has a passing test.
- `uv run pytest` and `uv run ruff check .` are both green.
- No AC relies solely on a manual check.
