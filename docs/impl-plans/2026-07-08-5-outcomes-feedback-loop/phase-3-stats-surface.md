# Phase 3: Stats CLI + web route

**Goal:** `wiki-cite stats` and `/stats` render the same aggregation over
`SeenStore.dimension_rates`, showing `successes/total` per dimension value with the
sample count `N` always shown, and zero-sample values omitted (never a bare `0%`).

**ACs covered:** AC3 (aggregates correct; never divides by zero).

**Depends on:** Phase 1 (`dimension_rates`).

## Files

- `wiki_cite/cli.py` — new `cmd_stats` + subparser.
- `wiki_cite/web_app.py` — new `/stats` route.
- `wiki_cite/templates/stats.html` — new template.
- `tests/test_cli.py` — new (no cli tests today) or add to an existing test module.
- `tests/test_web_app.py` — `/stats` render test (extends Phase 2's file).

## Shared: `STATS_DIMENSIONS`

Define a single module-level list of the dimensions both renderers walk (design §"Stats
surface"): `source_type`, `source_api`, `edit_type`, `confidence`, `has_infobox`, and
`categories`. (`body_line_count` is continuous — the design mentions a *bucketed* version;
for this phase either omit `body_line_count` or add a small helper that buckets it before
aggregation. Recommended: omit raw `body_line_count` from `STATS_DIMENSIONS` to avoid a
high-cardinality unusable table, and note the bucket as a follow-up. Keep the list in one
place — e.g. `wiki_cite/stats.py` or a constant importable by both `cli.py` and `web_app.py`
— so the two surfaces cannot drift.)

## Changes

### `wiki_cite/cli.py` — `cmd_stats`

Follow the flat `cmd_*` pattern (`cmd_config`, lines 82–108). Per design §"Stats surface":

```python
def cmd_stats(args):
    store = SeenStore(get_config().seen_db_path)
    for dimension in STATS_DIMENSIONS:
        print(f"\n{dimension}:")
        rates = store.dimension_rates(dimension)
        shown = False
        for value, (successes, total) in sorted(rates.items()):
            if total == 0:
                continue  # AC3.1: never a rate with n=0
            print(f"  {value:<30} {successes}/{total} ({successes / total:.0%})")
            shown = True
        if not shown:
            print("  (no data)")
```

Import `SeenStore` and `get_config`. Register a `stats` subparser in `main()` (after the
`config` subparser, lines 143–145) with `set_defaults(func=cmd_stats)`. No arguments needed.

### `wiki_cite/web_app.py` — `/stats`

Per design §"Stats surface", wrap the aggregation in `try/except sqlite3.Error` and pass a
`store_ok` flag so a broken DB renders a "no data" state, not a 500 (feeds AC6):

```python
@app.route("/stats")
def stats_page():
    store_ok, dimensions = True, {}
    try:
        for dimension in STATS_DIMENSIONS:
            dimensions[dimension] = {
                v: (s, t) for v, (s, t) in seen_store.dimension_rates(dimension).items() if t > 0
            }
    except sqlite3.Error:
        store_ok = False
    return render_template("stats.html", dimensions=dimensions, store_ok=store_ok)
```

Add `import sqlite3` to `web_app.py`.

### `wiki_cite/templates/stats.html`

Extend `base.html` (`{% extends "base.html" %}`), which exposes `block title`, `block content`,
`block extra_styles`, `block scripts` (base.html lines 294–312). Mirror `index.html`'s panel
structure (eyebrow / page-title / lead, then sections). For each dimension render a small
table of `value — successes/total (rate%)`; compute the percentage in the template as
`(successes / total)` only inside the `if total > 0` guard (the route already filtered to
`t > 0`, so the template loop is safe). When `dimensions` for a key is empty, or `store_ok`
is false, show a "no data yet" line rather than an empty table (AC3.1 / AC6). Keep styling to
existing base.html classes (`.panel`, `.section-head`, `.muted`) — no new external assets
(the CSP/self-contained rule does not apply to server templates, but staying within base.html
keeps the look consistent).

Optionally add a nav link to `/stats` from `index.html` (small, in the header area) — nice
for discoverability but not required by any AC.

## Tests

- `tests/test_cli.py::test_cmd_stats_prints_rates` (AC3.1): build a `SeenStore` on `tmp_path`,
  seed N rows for `source_type=news` with K successes, point config at that DB (via
  `set_config` or `SEEN_DB_PATH`), call `cmd_stats` capturing stdout (`capsys`), assert the
  output contains `news` and `K/N`.
- `test_cmd_stats_omits_zero_sample` (AC3.2): assert a dimension value with N=0 is not printed
  as `0%` — it is absent or under a "(no data)" line. Also assert no `ZeroDivisionError` is
  raised on an empty DB.
- `tests/test_web_app.py::test_stats_route_renders_rates`: seed the DB, GET `/stats`, assert
  200 and the response body contains `news` and `K/N`.
- `test_stats_route_empty_db_no_error`: fresh empty DB → GET `/stats` returns 200 with a "no
  data" state, not a 500 (feeds AC6.3).

## Done when

- `uv run pytest tests/test_cli.py tests/test_web_app.py` passes.
- `uv run wiki-cite stats` prints per-dimension `K/N (rate%)` with zero-sample values omitted.
- `/stats` renders the same numbers; empty DB shows "no data", never a traceback.
- `uv run ruff check .` clean.
