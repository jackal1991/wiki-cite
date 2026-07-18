# Phase 3: `wiki-cite check-reverts` CLI command

**Goal:** Wire the phase-2 detector into a runnable batch command the user
schedules from their own cron/launchd (there is no in-repo scheduler by design).
Per-article failures are isolated; the command prints a summary.

**ACs covered:** AC3.1 (collect in-horizon un-reverted pushes, check each, print
checked count + reverts found), AC3.2 (one article's network/API error does not
abort the batch; failures are reported).

**Depends on:** Phase 2 (`revert_checker.check_pending_reverts`,
`SeenStore.pending_revert_candidates`), Phase 4 (`config.revert_tracking`).

## Files

- `wiki_cite/cli.py` — new `cmd_check_reverts` + subparser.
- `tests/test_cli.py` — command tests.

## Changes

### `wiki_cite/cli.py` — `cmd_check_reverts`

Follow the flat `cmd_*` pattern (see `cmd_stats`, cli.py:113-127). The command
builds the same mwclient site the push service uses and delegates the walk to
`revert_checker`:

```python
def cmd_check_reverts(args):
    """Check pushed articles for reverts within the configured horizon."""
    import mwclient

    from wiki_cite.revert_checker import check_pending_reverts

    config = get_config()
    store = SeenStore(config.seen_db_path)
    site = mwclient.Site("en.wikipedia.org")

    horizon = config.revert_tracking.check_horizon_days
    summary = check_pending_reverts(site, store, horizon)

    print(f"Checked {summary.checked} pushed article(s) within the {horizon}-day horizon.")
    print(f"Reverts found: {summary.reverts_found}")
    if summary.failures:
        print(f"\n{len(summary.failures)} article(s) could not be checked:")
        for title, error in summary.failures:
            print(f"  {title}: {error}")
```

- No login is required for reads (`page.revisions()` is a public read), so the
  command does not need Wikipedia credentials — keep it that way so it runs
  unattended from cron. (Contrast with the push service, which logs in for writes.)
- Register the subparser in `main()` after the `stats` subparser (cli.py:167-168):
  ```python
  check_reverts_parser = subparsers.add_parser(
      "check-reverts", help="Check pushed articles for reverts within the horizon"
  )
  check_reverts_parser.set_defaults(func=cmd_check_reverts)
  ```
  No arguments — the horizon comes from config (AC5). (Optionally accept
  `--horizon-days` to override, but that is not required by any AC; if added, it
  falls back to the config value when omitted.)

## Tests (`tests/test_cli.py`)

The batch-failure isolation lives in `check_pending_reverts` (phase 2), so
`cmd_check_reverts` tests focus on wiring + printout. Monkeypatch
`check_pending_reverts` (imported inside the function — patch
`wiki_cite.revert_checker.check_pending_reverts`) and `mwclient.Site` so no
network happens, and point config at a `tmp_path` DB via `set_config`.

- `test_cmd_check_reverts_prints_summary` (AC3.1): patch `check_pending_reverts`
  to return a summary with `checked=3, reverts_found=1, failures=[]`; capture
  stdout (`capsys`); assert it contains `Checked 3` and `Reverts found: 1`.
- `test_cmd_check_reverts_reports_failures` (AC3.2): return a summary with
  `failures=[("Foo", "HTTPError")]`; assert the output lists `Foo` and the error,
  and the command exits normally (no raise, no `sys.exit(1)`).
- `test_cmd_check_reverts_uses_configured_horizon` (AC5.1 followthrough): set a
  non-default `check_horizon_days` in config, assert `check_pending_reverts` is
  called with that value (capture the call args on the patch).

## Done when

- `uv run pytest tests/test_cli.py` passes.
- `uv run wiki-cite check-reverts` runs, prints checked/reverts-found, and lists
  any per-article failures without aborting.
- The command reads the horizon from config, not a literal.
- `uv run ruff check .` clean.
