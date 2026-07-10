# Issue #7 — Add Wikipedia source-reliability policy to the agent's system prompt

**Status:** In Progress
**Complexity:** Standard
**GitHub:** https://github.com/jackal1991/wiki-cite/issues/7

## Worktree

- branch: feat/7-wikipedia-sourcing-policy-prompt
- path: .worktrees/7-wikipedia-sourcing-policy-prompt
- created: 2026-07-09

## Summary
`SEARCH_SYSTEM_PROMPT` in `wiki_cite/agent.py` (lines ~25-78) instructs Claude
to "find reliable sources that verify EXISTING claims in the article" but
never defines what makes a source *reliable*. The prompt has:

- no guidance distinguishing **secondary from primary sources** (WP:PSTS),
- no criteria for **source reliability** (WP:RS — editorial oversight,
  fact-checking reputation, independence from the subject),
- no explicit exclusion of **self-published / user-generated sources**
  (personal blogs, forums, wikis, social media — WP:SPS).

Today reliability is only enforced implicitly, downstream, by
`source_finder.py`'s domain-based `check_reliability`. We want the system
prompt itself to carry Wikipedia's sourcing policy so Claude's own judgment
*during* the search loop — which queries to run, which candidate source to
cite via `fetch_page` — is grounded in the actual standards, not left to a
downstream filter that only sees domains.

## Motivation
The agentic search loop (added in #4) gives Claude real discretion over which
sources to pursue and cite. If the prompt doesn't encode WP:RS / WP:PSTS /
WP:SPS, Claude may spend search budget on, and ultimately propose, citations
to primary or self-published sources that the domain-level reliability filter
can't reliably catch (the filter keys on known domains; the long tail of
blogs/forums/UGC won't all be enumerated). Grounding the prompt in policy
improves the quality of proposed edits before guardrails/human review ever
see them.

## Scope / touch points
- **`wiki_cite/agent.py`** — `SEARCH_SYSTEM_PROMPT`, specifically the
  "Citation Addition" section: add concise WP:RS / WP:PSTS / WP:SPS guidance
  (prefer secondary sources with editorial oversight and independence;
  exclude self-published/UGC; how to weigh a candidate during `fetch_page`
  verification).
- **`wiki_cite/source_finder.py`** — existing `check_reliability` /
  `ReliabilityRating` logic. Reconcile, don't duplicate: decide the division
  of labor between prompt-level judgment and the code-level domain filter,
  keep them consistent (prompt guidance shouldn't contradict what the filter
  deprecates).
- **`wiki_cite/guardrails.py`** — out of scope for this pass (see Decision
  below) — noted here only because the issue considered and rejected it.
- **`config.yaml`** — `sources.reliability_check` already gates the domain
  filter; note how prompt-level guidance interacts with it (prompt guidance
  is unconditional; the filter is toggleable).
- **Tests** — `tests/test_agent.py` asserts on prompt content; update those
  assertions.

## Decision (scope-narrowing, resolved before impl-plan)
Reliability stays **prompt guidance + the existing downstream domain filter
only** — not a new enforced guardrail in `guardrails.py`. This keeps the
issue at Standard complexity (matches the issue's own stated default). If a
guardrail is wanted later, that's a separate, Complex-rated follow-up issue
with its own design doc.

## Notes
- Filed by supervisor agent (`supervisor-source-policy`); GitHub label
  application likely hit the same repo-wide `AddLabelsToLabelable`
  permissions gap seen on #6/#8/#9 — no labels currently on this issue.
- No local issue doc was created by the filing agent; authored manually here
  before starting the impl-plan phase, same pattern as #6/#8/#9.
