# Phase 3: Agent tool wiring + anti-circularity guardrail

**Goal:** Expose `search_backlinks` as a fifth search tool — schema, `SEARCH_TOOLS`/`ALL_TOOLS`
membership, `_dispatch_search_tool` routing, `_SEARCH_TOOL_API_NAMES` label — and add explicit
WP:CIRCULAR / WP:WPNOTRS language to the system prompt in the same structural style as the
existing WP:RS/WP:PSTS/WP:SPS block, so the model never treats "found via another Wikipedia
article" as itself sufficient and never cites Wikipedia as a source.
**AC Coverage:** 16-backlink-citation-discovery.AC1 (AC1.1, AC1.2), AC5 (AC5.1)

---

## Context

`wiki_cite/agent.py` dispatches every search tool through one table
(`_dispatch_search_tool`, `agent.py:239`), which routes by name to a `SourceFinder` method and
returns `(ok, payload)` without ever raising (`except Exception -> (False, msg)` at
`agent.py:265`). `search_backlinks` is a fifth `if name == ...` branch — nothing structurally
new. Verified anchors:

- Tool schemas are module constants (`agent.py:114-199`); `_QUERY_INPUT_SCHEMA` (`agent.py:107`)
  is the shared `{query: string}` schema. `search_backlinks` needs a **different** input
  (`article_title`), so it gets its own inline schema.
- `SEARCH_TOOLS` (`agent.py:201`) is the list offered every turn under `tool_choice=auto`;
  `ALL_TOOLS` (`agent.py:202`) is `[*SEARCH_TOOLS, PROPOSE_EDITS_TOOL]`. Adding
  `SEARCH_BACKLINKS_TOOL` to `SEARCH_TOOLS` **automatically** gives AC1.2 for free: at the turn
  cap the loop offers `[PROPOSE_EDITS_TOOL]` only (`agent.py:444`), so `search_backlinks`
  disappears exactly like the other search tools.
- `_SEARCH_TOOL_API_NAMES` (`agent.py:205`) maps tool name → activity-log label; used by
  `_tool_call_event` (`agent.py:268`) to build the `"searching"` progress event.
- The WP:RS/WP:PSTS/WP:SPS block lives inside `SEARCH_SYSTEM_PROMPT` under
  "### Citation Addition" (`agent.py:44-53`); it already names "wikis (including Wikipedia
  itself)" as WP:SPS-disallowed. The new block sharpens this specifically for `search_backlinks`.
- `SEARCH_SYSTEM_PROMPT` is wrapped once into `_CACHED_SEARCH_SYSTEM_PROMPT` (`agent.py:100`)
  with a cache breakpoint; editing the prompt string is all that's needed — the cache wrapper
  re-renders automatically.

Phase 2 provides `SourceFinder.find_backlink_sources(article_title, *, site=None)`.

## Implementation

### `SEARCH_BACKLINKS_TOOL` schema + tool lists (`agent.py`)

**Files:**
- Modify: `wiki_cite/agent.py`

- Add a schema constant beside the other `SEARCH_*_TOOL` constants:
  ```python
  SEARCH_BACKLINKS_TOOL: dict[str, Any] = {
      "name": "search_backlinks",
      "description": (
          "Discover candidate citation URLs by scanning other Wikipedia articles that link to "
          "this article ('what links here'). Related articles often already cite an external "
          "source that also supports the flagged claim. This only SURFACES external URLs found "
          "on those pages as candidates — a Wikipedia article is NEVER itself a usable source "
          "(WP:CIRCULAR). Every returned URL must still be verified with fetch_page and judged "
          "against the reliability criteria before you cite it."
      ),
      "input_schema": {
          "type": "object",
          "properties": {
              "article_title": {
                  "type": "string",
                  "description": "The title of the article currently being edited.",
              }
          },
          "required": ["article_title"],
          "additionalProperties": False,
      },
      "strict": True,
  }
  ```
- Add it to `SEARCH_TOOLS`:
  `SEARCH_TOOLS = [SEARCH_SCHOLAR_TOOL, SEARCH_CROSSREF_TOOL, SEARCH_WEB_TOOL, SEARCH_BACKLINKS_TOOL, FETCH_PAGE_TOOL]`
  (`ALL_TOOLS` needs no change — it splats `SEARCH_TOOLS`). This membership alone satisfies
  AC1.2 (unavailable at the turn cap) with no extra code.
- Add the activity-log label to `_SEARCH_TOOL_API_NAMES`:
  `"search_backlinks": "wikipedia_backlinks"`.

### Dispatch routing (`_dispatch_search_tool`, `agent.py:239`)

**Files:**
- Modify: `wiki_cite/agent.py`

- Add a branch alongside the other `if name == ...` cases:
  ```python
  if name == "search_backlinks":
      sources = self.source_finder.find_backlink_sources(tool_input["article_title"])
      return True, json.dumps(_sources_to_dicts(sources))
  ```
  Return shape is `_sources_to_dicts(...)` exactly like `search_web`/`search_scholar`/
  `search_crossref` (AC1.1, AC4.1) — the model sees backlink candidates in the same JSON shape
  as any other search result. The outer `try/except` (`agent.py:265`) already makes any failure
  a non-fatal `(False, msg)`, so a backlink fetch error can't abort the loop.

### Activity-log event handling (`_tool_call_event`, `agent.py:268`)

**Files:**
- Modify: `wiki_cite/agent.py`

- `_tool_call_event` currently emits `{"type": "searching", "api": ..., "query":
  tool_input.get("query", "")}`. `search_backlinks` has no `query`, so surface the title
  instead — change the query lookup to fall back:
  `"query": tool_input.get("query") or tool_input.get("article_title", "")`.
  (`_tool_result_event` needs no change — backlink results are a JSON list of sources, handled
  by the existing `results = data if isinstance(data, list)` path.)

### Anti-circularity guardrail in `SEARCH_SYSTEM_PROMPT` (`agent.py:28`)

**Files:**
- Modify: `wiki_cite/agent.py`

- Add an explicit block to `SEARCH_SYSTEM_PROMPT`, structurally matching the existing
  WP:RS/WP:PSTS/WP:SPS paragraph (`agent.py:44-53`). Place it inside "### Citation Addition"
  right after that paragraph, or as a short dedicated subsection. Required content (AC5.1):
  - `search_backlinks` returns **candidate external URLs discovered via another Wikipedia
    article** — the other article is a lead, never itself a source.
  - Citing Wikipedia itself — including the backlinking article the URL was found on, or any
    `wikipedia.org` link — is **never** permitted (WP:CIRCULAR / WP:WPNOTRS).
  - A backlink-discovered URL earns citation **only** by independently passing the same
    reliability judgment as any other source (verify with `fetch_page`, weigh independence /
    editorial oversight / secondary-vs-primary) and genuinely supporting the flagged claim.
  - Keep phrasing strong and unambiguous ("never", "only") mirroring the surrounding
    ABSOLUTE-CONSTRAINTS / WP:SPS tone.
- Optionally add one line under "### Citation Addition"'s tool-selection guidance
  (`agent.py:55-56`) mentioning `search_backlinks` as a discovery avenue for claims where a
  closely-related article likely already carries a usable source — kept minimal.

**Notes:**
- Do not alter `check_reliability`, `guardrails.py`, or the reliability whitelist — the guardrail
  is prompt + parity only. WP:CIRCULAR is enforced by (a) this prompt language and (b) the fact
  that a `wikipedia.org` URL, if ever extracted, still flows through `check_reliability` with no
  exemption (Phase 2, AC4.2) — never by special-casing.

## Verification

Run: `uv run pytest tests/test_agent.py -q`
Also: `uv run ruff check wiki_cite/agent.py`
Also: `uv run wiki-cite config` (unaffected) and a quick import
`uv run python -c "from wiki_cite.agent import SEARCH_BACKLINKS_TOOL, SEARCH_TOOLS, ALL_TOOLS; assert SEARCH_BACKLINKS_TOOL in SEARCH_TOOLS and SEARCH_BACKLINKS_TOOL in ALL_TOOLS"`.
Expected: existing agent tests still pass; the new tool is in both tool lists and routed by
`_dispatch_search_tool`. Full AC test coverage (including the AC5.2 prompt-text assertion) lands
in Phase 4.

## Commit

`feat: wire search_backlinks agent tool with anti-circularity guardrail`
