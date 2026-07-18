# Phase 4: Revert-tracking config

**Goal:** Make the revert horizon a config value (default 7 days) rather than a
literal, following the existing per-section `BaseSettings` pattern. Consumed by
the phase-2 detector and the phase-3 CLI command.

**ACs covered:** AC5.1 (`revert_tracking` section with `check_horizon_days: 7`,
overridable), AC5.2 (missing section falls back to the 7-day default, no crash).

**Ordering:** although numbered 4, this is a dependency of phases 2 and 3 (both
read `config.revert_tracking.check_horizon_days`). Land it together with — or
before — phase 2 so the horizon is never a magic number. See README ordering note.

## Files

- `wiki_cite/config.py` — new `RevertTrackingConfig` + wiring into `Config` / `Config.load`.
- `config.yaml` — new `revert_tracking` section.
- `tests/test_config.py` — load + default tests (create if it does not exist).

## Changes

### `wiki_cite/config.py`

Mirror `FeedbackConfig` (config.py:69-74) exactly — a `BaseSettings` subclass
with a typed default, composed onto `Config`, gated in `Config.load`:

1. New class (place next to `FeedbackConfig`):
   ```python
   class RevertTrackingConfig(BaseSettings):
       """Configuration for the post-push revert checker."""

       check_horizon_days: int = 7
   ```
2. Field on `Config` (after `feedback`, config.py:87):
   ```python
   revert_tracking: RevertTrackingConfig = Field(default_factory=RevertTrackingConfig)
   ```
   The `default_factory` is what satisfies AC5.2: with no `revert_tracking` key in
   YAML, `Config` still constructs a `RevertTrackingConfig()` with `check_horizon_days=7`.
3. `Config.load` gate (after the `feedback` block, config.py:123-124):
   ```python
   if "revert_tracking" in yaml_config:
       config_data["revert_tracking"] = RevertTrackingConfig(**yaml_config["revert_tracking"])
   ```
   Omit the block-add nothing else — the `if "..." in yaml_config` guard is the
   established pattern; a missing section simply skips this line and the default
   factory applies (AC5.2).

### `config.yaml`

Add a `revert_tracking` section with the documented default so it is discoverable
and overridable (AC5.1). Match the commenting style of the existing sections:

```yaml
revert_tracking:
  # Days after a push during which `wiki-cite check-reverts` keeps re-checking an
  # article for a revert. After this window elapses with no revert found, the
  # article drops out of the check set (bounds the work per run).
  check_horizon_days: 7
```

(Confirm the exact key nesting matches how other sections appear in the real
`config.yaml`; the loader keys off the top-level section name `revert_tracking`.)

### Optionally surface in `cmd_config`

`cmd_config` (cli.py:84-110) prints each config section. Adding a small block is
consistent and aids debugging, though no AC requires it:

```python
print("\nRevert Tracking:")
print(f"  Check horizon (days): {config.revert_tracking.check_horizon_days}")
```

## Tests (`tests/test_config.py`)

- `test_revert_tracking_default_is_seven` (AC5.2): `Config.load` on a config path
  with **no** `revert_tracking` section (e.g. a `tmp_path` YAML omitting it)
  yields `config.revert_tracking.check_horizon_days == 7`. Also assert a
  fully-empty/absent config file still constructs (default factory path).
- `test_revert_tracking_override` (AC5.1): a `tmp_path` YAML with
  `revert_tracking: {check_horizon_days: 3}` loads as `3`.

If `tests/test_config.py` does not exist yet, create it following the `tmp_path`
+ `Config.load(path)` style; write the YAML fixture with `yaml.safe_dump` or a
plain string.

## Done when

- `uv run pytest tests/test_config.py` passes.
- `uv run wiki-cite config` (if the optional block is added) prints the horizon.
- A config file without `revert_tracking` loads with `check_horizon_days == 7`.
- `uv run ruff check .` clean.
