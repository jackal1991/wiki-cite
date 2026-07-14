# Issue #16 — Agent tool: discover citations via backlinking Wikipedia articles

**Status:** Ready
**Complexity:** Complex
**GitHub:** https://github.com/jackal1991/wiki-cite/issues/16

## Summary
Give the agent's search loop a new tool that checks *other* Wikipedia articles —
specifically ones that link to the article being edited ("what links here") —
for citations that might support the same flagged claim. Higher-quality
related articles, especially ones that already discuss/reference the subject,
often already cite a source that would also support the claim in the article
under edit.

## Critical constraint: this must never become circular sourcing
WP:CIRCULAR / WP:WPNOTRS: Wikipedia is not a reliable source for itself. The
agent must **never** cite another Wikipedia article as the source for a claim.
This tool can only be used to *discover* a candidate external citation (a
`<ref>`/`{{cite ...}}` URL already present on the other page) — that candidate
then has to go through the exact same verification every other found source
goes through: `SourceFinder.check_reliability()`, and the model's own
judgment that the source's content actually supports the specific claim in
*this* article. The system prompt must make this explicit and hard to
misread, not just imply it.

## Why this needs a design doc, not just an issue → impl-plan
- **New MediaWiki API usage.** "What links here" is `list=backlinks`
  (`bl` prefix) — a pattern not used anywhere in this codebase today. Needs
  the same sequential/etiquette treatment as every other Wikipedia-facing call
  (see `_build_session`, the `bae9507` revert, `crawl_subcategories`'s
  cycle-safety precedent).
- **Cost/turn budgeting.** This adds a new tool call type inside the existing
  bounded agentic loop (`agent.max_search_turns`). Does checking backlinks
  count as one of the existing 5 turns, or does it need its own separate
  budget? How many backlinking articles get checked per call (all of them —
  could be hundreds for a well-linked topic — or a capped sample)?
- **Extraction logic.** Parsing candidate citation URLs out of another page's
  wikitext reuses `extract_citation_url`'s pattern (`source_finder.py`) but
  needs to scan a whole article's `<ref>`/`{{cite}}` blocks, not a single
  proposed-edit snippet — a new parsing surface.
- **Prompt-guardrail design.** This is the highest-risk part: how the system
  prompt frames this tool so the model never treats "found via another
  Wikipedia article" as itself sufficient justification. Needs explicit
  language and probably a worked example, similar to how #7 added WP:RS/
  WP:PSTS/WP:SPS guidance.
- **Reliability-pipeline integration.** Discovered candidate sources must flow
  through the same `check_reliability`/policy-reference machinery as
  `search_scholar`/`search_crossref`/`search_web` results — not a parallel,
  looser path.

## Scope / touch points (indicative, not final — design doc decides)
- `wiki_cite/source_finder.py` — new method(s) for backlink discovery + candidate-citation extraction from a fetched page's wikitext.
- `wiki_cite/agent.py` — new tool definition, `SEARCH_TOOLS`/`ALL_TOOLS`, `_dispatch_search_tool`, and system-prompt guardrail language preventing circular sourcing.
- `wiki_cite/article_picker.py` or `source_finder.py` — the backlinks API call itself, following the existing sequential/etiquette pattern.
- `config.yaml` — likely a new cost-guard (how many backlinking pages to check, turn-budget interaction).
- `tests/` — coverage for extraction, the circular-sourcing guardrail (a source found this way still has to independently pass reliability checks), and turn-budget interaction.

## Complexity
Complex — real architectural decisions (turn budgeting, extraction scope) and
a policy-compliance design surface (circular sourcing) that needs explicit
sign-off before implementation, not just an issue doc.
