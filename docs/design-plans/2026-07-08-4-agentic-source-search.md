# Agentic Source Search Design

GitHub issue: #4

## Summary

Replace the one-shot keyword source search with a **bounded agentic tool-use loop** on `claude-sonnet-5` with **adaptive thinking enabled**. Instead of firing a single query per claim and taking the top hits, the model reads the flagged passage in context, writes its own queries against our existing source APIs, optionally fetches a page to verify a candidate, iterates, and proposes citations that genuinely support existing claims. The loop is **per fetched article** (sources all of the article's `{{Citation needed}}` claims together) and is capped by a new config value `max_search_turns`.

## Definition of Done

- The agent's sourcing path is an agentic call-and-response loop, not a single keyword query.
- Model: `claude-sonnet-5` (project default) with `thinking: {type: "adaptive"}`; the raw reasoning summary is surfaced into the over-the-shoulder activity log.
- Claude drives its own search via four custom tools wrapping `SourceFinder`, plus a terminal tool that returns the proposed edits.
- The loop is bounded by `article_selection`/`agent` config `max_search_turns` (max tool-use iterations per article) and degrades gracefully at the cap.
- Each tool call streams into the existing SSE "Working" view.
- Existing guardrails, `has_confident_citation`, the review UI, and the `ProposedEdit` shape are unchanged.

**Out of scope:** article selection, the guardrail rules, review-UI changes beyond the activity log, and the Anthropic server-side `web_search` tool (we keep our own API keys/infra).

## Acceptance Criteria

### 4-agentic-source-search.AC1: Agentic loop drives real call-and-response
- **AC1.1 Success:** given an article whose excerpt contains a flagged claim with a findable source, the loop issues ≥1 `search_*` tool call, receives results, and terminates by calling `propose_edits` with at least one citation edit whose `original_text` is a verbatim substring of the article.
- **AC1.2 Failure:** if no tool call would help (empty excerpt / no claims), the loop terminates in ≤1 model turn with an empty edit set and the article is skipped — no infinite loop.

### 4-agentic-source-search.AC2: Correct Anthropic API call/response contract
- **AC2.1 Success:** on each iteration where `stop_reason == "tool_use"`, the assistant turn is appended to `messages` **verbatim** (`response.content`, including `thinking` and `tool_use` blocks), every `tool_result` is returned in a **single** user message with a matching `tool_use_id`, and failed tool calls return `is_error: true` rather than being dropped.
- **AC2.2 Failure:** a request that strips or mutates thinking blocks, splits tool_results across multiple user messages, or omits a `tool_use_id` is rejected in review — these break the API contract with thinking enabled.

### 4-agentic-source-search.AC3: Bounded cost
- **AC3.1 Success:** the loop makes at most `max_search_turns` tool-executing model calls per article; when the budget is exhausted it makes one final decision call (tools disabled) and returns whatever citations it has.
- **AC3.2 Failure:** a loop that can exceed `max_search_turns` model calls, or that never forces a terminal decision, is rejected.

### 4-agentic-source-search.AC4: Streaming visibility
- **AC4.1 Success:** each `search_*` / `fetch_page` tool call and the thinking summary emit SSE events that render in the activity log (query text, source found/none, page fetched, reasoning).
- **AC4.2 Failure:** a tool call that produces no corresponding activity-log line is rejected.

### 4-agentic-source-search.AC5: Graceful degradation
- **AC5.1 Success:** a tool execution error (network failure, no key) returns a `tool_result` with `is_error: true` and the loop continues; a `stop_reason` of `refusal` or `max_tokens` ends the loop with whatever edits exist.
- **AC5.2 Failure:** an unhandled tool exception that aborts the whole fetch, or unhandled `refusal`/`max_tokens`, is rejected.

## Architecture

### Chosen approach: per-article agentic loop with a terminal tool

`ClaudeAgent.analyze_article_events(article)` is reworked from "search each claim once, then one message to propose edits" into a single agentic loop that both searches and proposes. The loop's tools are search-focused; its terminal `propose_edits` tool returns the full `ProposedEdit` list (citation edits backed by the searches, plus any obvious grammar/wikilink/formatting edits the model reads off the excerpt). This keeps one model conversation per article and preserves the existing edit variety and downstream shape.

#### The exact call/response loop

```python
SEARCH_MODEL = self.config.agent.model            # "claude-sonnet-5"
max_turns   = self.config.agent.max_search_turns  # new; default 5

messages = [{"role": "user", "content": user_prompt}]   # excerpt + flagged claims + instructions
proposal_edits = None
turns = 0

while True:
    tools = TOOLS if turns < max_turns else [PROPOSE_EDITS_TOOL]
    tool_choice = {"type": "tool", "name": "propose_edits"} if turns >= max_turns else {"type": "auto"}

    response = self.client.messages.create(
        model=SEARCH_MODEL,
        max_tokens=8000,
        system=SEARCH_SYSTEM_PROMPT,                     # stable → prompt-cacheable prefix
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": "high"},
        tools=tools,
        tool_choice=tool_choice,
        messages=messages,
    )

    # --- stop_reason handling (guard BEFORE reading content) ---
    if response.stop_reason == "refusal":
        break                                            # decline → skip; content may be empty
    if response.stop_reason in ("end_turn", "max_tokens"):
        break                                            # no tool call; take what we have

    # stop_reason == "tool_use":
    # 1) append the assistant turn VERBATIM — thinking + tool_use blocks unchanged.
    messages.append({"role": "assistant", "content": response.content})

    # 2) execute every tool_use block; collect ALL results into ONE user message.
    tool_results = []
    terminal = False
    for block in response.content:
        if block.type != "tool_use":
            continue                                     # thinking/text blocks: leave in place
        if block.name == "propose_edits":
            proposal_edits = block.input["edits"]        # terminal tool → capture + stop
            terminal = True
            tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                 "content": "recorded"})
        else:
            ok, payload = self._dispatch_search_tool(block.name, block.input)
            tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                 "content": payload, "is_error": not ok})

    messages.append({"role": "user", "content": tool_results})   # single user message
    if terminal:
        break
    turns += 1
```

**Non-negotiable API facts (Sonnet 5 + adaptive thinking):**

- `thinking: {type: "adaptive"}` only. `budget_tokens` and `temperature`/`top_p`/`top_k` **400** on Sonnet 5. Effort depth via `output_config: {effort: "high"}`.
- `display: "summarized"` returns a readable reasoning summary (default is `"omitted"` → empty thinking text). We surface the summary in the activity log.
- With thinking enabled, **thinking blocks must be echoed back unchanged** on the next turn of the same model. Appending the full `response.content` satisfies this — never strip or reconstruct blocks.
- **All `tool_result` blocks for one assistant turn go in a single user message.** Splitting them across messages trains the model to stop issuing parallel tool calls.
- Each `tool_result.tool_use_id` must equal the originating `tool_use.id`. Failed tools return `is_error: true` with an error string — return the result, don't drop it.
- The turn cap is our own counter, independent of `stop_reason`. At the cap we swap `tools` to only `propose_edits` and force `tool_choice` to it, so the model returns a structured final answer instead of searching forever.

#### Tools (custom, `strict: true`)

Each search tool wraps an existing `SourceFinder` method and returns a compact JSON string of results. Descriptions are prescriptive about **when** to call (recent Sonnet models reach for tools more precisely when the trigger condition is in the description).

| Tool | Wraps | Input | Returns |
|---|---|---|---|
| `search_scholar` | `SourceFinder.search_semantic_scholar` | `{query}` | top-N papers: title, authors, year, doi, url |
| `search_crossref` | `SourceFinder.search_crossref` | `{query}` | top-N works: title, authors, year, doi, url |
| `search_web` | Brave (existing web search) | `{query}` | top-N pages: title, url, description |
| `fetch_page` | `SourceFinder.fetch_page_preview` | `{url}` | title, description, site — to verify a candidate actually backs the claim |
| `propose_edits` (terminal) | — | `{edits: [ProposedEdit-shaped]}` | ends the loop; edits go through existing guardrails |

`propose_edits` input schema mirrors `ProposedEdit`: `edit_type` (enum), `original_text`, `proposed_text`, `rationale`, `policy_reference`, `confidence`. `strict: true` + `additionalProperties: false` + `required` guarantees valid input.

#### Config

Add to `AgentConfig`:
- `max_search_turns: int = 5` — max tool-executing model calls per article (the cost guard the loop is bounded by).
- `search_results_per_query: int = 3` — cap results returned per tool call (keeps tool_result payloads small).

Cost envelope per fetch: `max_candidates_per_fetch` (articles, default 8) × up to `max_search_turns + 1` model calls × per-call tokens (excerpt + accumulating tool results + thinking). Bounded and observable; pairs with the existing `max_wikitext_chars` guard.

#### Streaming integration

`analyze_article_events` still yields the same event vocabulary the SSE loop already re-emits, extended with the loop's activity:
- `search_*` tool call → `{type: "searching", api, query}` then `{type: "source_found"|"source_none", ...}` from the tool result.
- `fetch_page` → new `{type: "fetching", url, title?}`.
- thinking summary block → new `{type: "thinking", text}` (rendered dim in the log).
- `propose_edits` → existing `{type: "model_done", edit_count}` then `{type: "analyzed", proposal}`.

The frontend activity-log already handles `searching`/`source_found`/`source_none`/`model_call`/`model_done`; add `fetching` and `thinking` line renderers.

## Existing Patterns

- `ClaudeAgent` already constructs messages by hand and calls `self.client.messages.create(...)`; `analyze_article_events` is already a generator that yields progress events — the loop slots into the same generator.
- `SourceFinder` already exposes `search_semantic_scholar`, `search_crossref`, Brave web search, and `fetch_page_preview` — the tools are thin wrappers, no new source integrations.
- `EditGuardrails.validate_edit` and `EditProposal.has_confident_citation` already gate the resulting edits — reuse unchanged.
- Config is Pydantic `BaseSettings` with `extra="ignore"`; new fields follow the existing `AgentConfig` pattern and `config.yaml` mirror.
- SSE `scan_events` in `web_app.py` re-emits agent events verbatim (tagged with title) — new event types flow through without backend changes beyond emitting them.

## Implementation Phases

### Phase 1: Tool layer
**Goal:** define the tool schemas and a pure dispatcher that wraps `SourceFinder`.
**Components:** `wiki_cite/agent.py` (tool constants + `_dispatch_search_tool`), possibly a small `wiki_cite/search_tools.py`.
**Done when:** AC1 tool plumbing exists; unit tests exercise the dispatcher against a mocked `SourceFinder` (success + `is_error` paths) with no network/API.

### Phase 2: Agentic loop
**Goal:** rework `analyze_article_events` into the bounded call/response loop with correct API contract and terminal handling.
**Components:** `wiki_cite/agent.py`.
**Done when:** AC1, AC2, AC3, AC5 — a mocked Anthropic client simulating `tool_use → tool_use → propose_edits` (and cap-exhaustion, `refusal`, `max_tokens`) produces the right `messages` sequence and a validated `EditProposal`.

### Phase 3: Config + streaming
**Goal:** add `max_search_turns` / `search_results_per_query`; emit and render the new `fetching`/`thinking` events.
**Components:** `wiki_cite/config.py`, `config.yaml`, `wiki_cite/web_app.py`, `wiki_cite/templates/base.html` + `index.html`.
**Done when:** AC3, AC4 — config loads the new values; the over-the-shoulder view shows queries, fetches, and reasoning streaming live.

### Phase 4: Tests + verification
**Goal:** lock behavior and verify end-to-end.
**Components:** `tests/test_agent.py`, `tests/test_config.py`, seeded Playwright spin.
**Done when:** ruff + bandit clean, pytest green (mocked-client loop tests), and a seeded fetch shows the agentic search driving the activity log through to a review page.

## Glossary

- **Agentic loop / call-and-response:** the manual Anthropic tool-use loop — `messages.create` → `stop_reason: "tool_use"` → execute tools → append results → repeat — until a terminal tool or a terminal `stop_reason`.
- **Terminal tool (`propose_edits`):** the tool whose invocation ends the loop and carries the final `ProposedEdit` list.
- **Turn budget (`max_search_turns`):** the cap on tool-executing model calls per article; at the cap the loop forces the terminal tool.
- **Thinking echo:** the requirement to append assistant `thinking` blocks back unchanged on the next same-model turn when adaptive thinking is enabled.
