# wiki-cite

Last verified: 2026-07-08

Wikipedia citation & cleanup tool: a Flask review dashboard plus a Claude agent that
proposes **minimal, sourced** edits to stub articles. The agent finds reliable sources for
claims *already present* (targeting `{{Citation needed}}`), fixes grammar/style/policy
issues, and adds wikilinks. It never adds new facts, and every edit is human-reviewed
before it can be pushed to Wikipedia.

## Tech Stack
- Language: Python >=3.11, managed with `uv` (see `pyproject.toml`, `uv.lock`)
- Agent: Anthropic `anthropic` SDK; Claude Sonnet with adaptive thinking (model in `config.yaml`)
- Models/validation: Pydantic v2 (+ pydantic-settings)
- Web: Flask (+ flask-cors), server-sent events for the over-the-shoulder activity view
- MediaWiki: mwclient, mwparserfromhell
- Sourcing: requests, beautifulsoup4, nltk (Semantic Scholar, CrossRef, optional Brave web search)

## Commands
- `uv sync` - Install runtime + dev deps into `.venv`
- `uv run wiki-cite web` - Launch the review dashboard (default http://localhost:5000)
- `uv run wiki-cite fetch --limit N` - List stub articles needing citations
- `uv run wiki-cite analyze "Article Title"` - Analyze one article, print proposed edits
- `uv run wiki-cite config` - Print current configuration
- `uv run pytest` - Run tests (coverage + branch coverage are on by default)
- `uv run ruff check .` - Lint
- `uv run bandit -r wiki_cite` - Security scan
- `uv run pip-audit` - Dependency vulnerability audit
- `uv run detect-secrets scan` - Secret scan

## Project Structure
- `wiki_cite/` - The package
  - `agent.py` - Claude agent: agentic tool-use loop that sources claims and proposes edits
  - `source_finder.py` - Source search across Semantic Scholar / CrossRef / Brave; reliability checks
  - `article_picker.py` - Selects candidate stub articles lacking sources
  - `guardrails.py` - Enforces minimal-edit / policy constraints on proposed edits
  - `models.py` - Pydantic v2 domain models (`ProposedEdit`, etc.)
  - `config.py` - Loads `config.yaml` + env (pydantic-settings)
  - `seen_store.py` - Persisted store of already-seen articles (idempotent fetch)
  - `web_app.py` + `templates/` - Flask review dashboard and SSE activity stream
  - `wikipedia_push.py` - Pushes approved, human-reviewed edits to Wikipedia
  - `cli.py` - Entry point (`wiki-cite` script)
- `tests/` - pytest suite (mirrors module names: `test_<module>.py`)
- `docs/design-plans/` - Design docs written before implementation (`<date>-<issue#>-<slug>.md`)
- `docs/issues/` - Jackal issue docs (backlog work units)
- `docs/impl-plans/` - Jackal implementation plans
- `examples/` - Standalone usage examples
- `config.yaml` - Runtime behavior (model, guardrail thresholds, source APIs, article selection)
- `.env` - Secrets (API keys); copy from `.env.example`. Never commit.

## Conventions
- Modern type hints, Pydantic v2 models for structured data; keep pure logic separable from I/O.
- Tests live in `tests/` as `test_<module>.py`; markers `slow` and `integration` exist.
- `ruff` is the only style gate (line-length 300, E/F/W). No black, no mypy, no `setup.py` —
  these were intentionally dropped; don't reintroduce them.
- Design doc before implementation for non-trivial work.

## Boundaries
- Safe to edit: `wiki_cite/`, `tests/`, `examples/`, `docs/`.
- Never edit: `uv.lock` by hand (use `uv`), `.env` / any secrets, `wiki_cite_seen.db`,
  `.coverage`, `htmlcov/`, `wiki_cite.egg-info/`.
- All Wikipedia edits require human review in the dashboard before push — the agent must
  never bypass the review step or the guardrails.

## Workflow
- **Commits are LOCAL ONLY.** Never push to `origin` unless the user explicitly asks. `main`
  is not kept in sync with the remote by default.
- Write a design doc under `docs/design-plans/` before implementing non-trivial changes.
- Ideas / backlog items are tracked as GitHub issues on `jackal1991/wiki-cite`.

## Issue discipline (labels)
The Jackal workflow (`label_style: slash`) reads/writes these status labels on GitHub issues.
They now exist on `jackal1991/wiki-cite`:
- `status/ready` - Scoped and ready to pick up
- `status/in-progress` - Actively being planned/implemented (worktree assigned)
- `status/paused` - Checkpointed mid-flight (see `/jackal-supervisor:jackal-pause-session`)
- `status/blocked` - Waiting on something external

## Jackal Config

- repo_root: .
- gh_repo: jackal1991/wiki-cite
- issue_docs: docs/issues
- design_plans: docs/design-plans
- impl_plans: docs/impl-plans
- modules: wiki_cite
- test_cmd: uv run pytest
- label_style: slash
- git_remote: origin
- push_cmd: (manual only — this project is local-commits-only; do not auto-push. See Workflow above.)
