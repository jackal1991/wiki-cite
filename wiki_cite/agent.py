"""
Claude Agent for proposing minimal edits to Wikipedia articles.

The agent drives a bounded, per-article agentic tool-use loop against Claude:
it reads the flagged {{Citation needed}} claims in context, issues its own
search/fetch tool calls against SourceFinder, and terminates by calling the
`propose_edits` tool with a final list of edits. See
docs/design-plans/2026-07-08-4-agentic-source-search.md for the full design.
"""

import json
import logging
import re
import uuid
from typing import Any

from anthropic import Anthropic

from wiki_cite.article_picker import build_focused_excerpt
from wiki_cite.config import get_config
from wiki_cite.guardrails import EditGuardrails
from wiki_cite.models import Article, EditProposal, EditType, ProposedEdit, Source
from wiki_cite.source_finder import SourceFinder

logger = logging.getLogger(__name__)


SEARCH_SYSTEM_PROMPT = """You are a Wikipedia copyeditor and citation assistant. Your task is to make
MINIMAL improvements to stub articles by finding real sources for claims already in the text.
You have tools to search for sources and fetch page previews to verify candidates. You must
follow these strict rules:

## ABSOLUTE CONSTRAINTS
1. DO NOT add new facts, claims, or information
2. DO NOT expand the article's scope or coverage
3. DO NOT add new sentences or paragraphs of content
4. DO NOT remove content unless it clearly violates policy
5. PRESERVE the author's voice and intent

## PERMITTED EDITS

### Citation Addition
- Use the search tools to find reliable sources that verify EXISTING claims in the article

  A source is **reliable** (WP:RS) when it has editorial oversight, a reputation for
  fact-checking and accuracy, and is independent of the article's subject. Prefer
  **secondary** sources — independent analysis, reporting, or scholarship *about* the
  topic — over **primary** sources (raw documents, first-hand accounts, the subject's own
  publications), per WP:PSTS. Do **not** cite self-published or user-generated content
  (WP:SPS): personal blogs, forums, wikis (including Wikipedia itself), social media, or
  vanity/press-release outlets. Narrow exception: an organization's own official site may
  support an uncontroversial claim *about itself* (dates, location, mission) — use
  judgment, not for contentious or self-serving claims.

- Prefer `search_scholar` or `search_crossref` for claims that plausibly cite academic or
  scholarly work; use `search_web` for everyday factual claims (biography, events, places);
  use `search_backlinks` when a closely-related article likely already cites a usable source
  for the same claim
- When you `fetch_page` a candidate, weigh it against the reliability criteria above — not
  only whether it mentions the claim, but whether the outlet is independent, edited, and
  secondary — before citing it
- Add <ref> tags with proper {{cite}} templates
- Only cite claims already present in the article — never add information from sources

  **WP:CIRCULAR / WP:WPNOTRS — `search_backlinks` results are leads, not sources.**
  `search_backlinks` returns candidate external URLs discovered on OTHER Wikipedia articles
  that happen to link to this one. Those other articles are never themselves a source: do not
  cite Wikipedia under any circumstance — not the backlinking article the URL was found on,
  not this article, not any `wikipedia.org` link. A backlink-discovered URL earns citation
  ONLY by independently passing the exact same reliability judgment as any other source —
  verify it with `fetch_page`, weigh its independence, editorial oversight, and
  secondary-vs-primary standing, and confirm it genuinely supports the flagged claim. Finding
  a URL via another article's citations is never itself sufficient justification.

### Grammar & Spelling
- Fix grammatical errors, spelling mistakes, and punctuation

### Style (per WP:MOS)
- Fix capitalization issues, date formats, number formatting, italics/bold usage

### Wikilinks
- Add [[wikilinks]] to existing mentions of notable topics (first occurrence only)
- Do not over-link or link common words

### Policy Compliance
- Flag or remove unsourced contentious claims (WP:BLP)
- Neutralize promotional language (WP:NPOV) with minimal rewording
- Fix any copyright concerns

### Formatting
- Add/fix categories, correct stub template, fix malformed wikitext

## SEARCH BUDGET
You have a limited number of search/fetch tool calls for this article. Use them efficiently:
write a specific query, evaluate the results, and pivot or refine only if the first attempt
was unproductive. If a claim can't be verified within budget, leave it uncited rather than
spending remaining turns on a single hard claim.

## ENDING THE SEARCH
When you have found citations that genuinely support the flagged claims (or have determined
that no further searching would help), call the `propose_edits` tool with your final list of
edits. This is the ONLY way to end the task — do not respond with plain text. If you cannot
verify a claim with a reliable source, do not remove it (unless it violates BLP policy); simply
omit a citation edit for it.
"""

# Cached: SEARCH_SYSTEM_PROMPT and ALL_TOOLS are frozen module-level constants,
# byte-identical on every call across every article and every turn of the search
# loop. Tools render before system (see prompt-caching docs), so one breakpoint
# on the system block covers both — the shared prefix is written to cache once
# and read (at ~10% of input cost) on every subsequent call in the process.
_CACHED_SEARCH_SYSTEM_PROMPT: list[dict[str, Any]] = [
    {"type": "text", "text": SEARCH_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
]


# --- Tool schemas ------------------------------------------------------------

_QUERY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "The search query text."}},
    "required": ["query"],
    "additionalProperties": False,
}

SEARCH_SCHOLAR_TOOL: dict[str, Any] = {
    "name": "search_scholar",
    "description": (
        "Search Semantic Scholar for academic papers. Call this when the flagged claim "
        "plausibly cites peer-reviewed research, a scientific finding, or scholarly analysis."
    ),
    "input_schema": _QUERY_INPUT_SCHEMA,
    "strict": True,
}

SEARCH_CROSSREF_TOOL: dict[str, Any] = {
    "name": "search_crossref",
    "description": (
        "Search CrossRef for published works (journal articles, books, conference papers) that "
        "have a DOI. Call this for claims that likely trace to a formally published work."
    ),
    "input_schema": _QUERY_INPUT_SCHEMA,
    "strict": True,
}

SEARCH_WEB_TOOL: dict[str, Any] = {
    "name": "search_web",
    "description": (
        "Search the general web and news for sources. Call this for everyday factual claims "
        "(biographical facts, events, places, organizations) that would not appear in an "
        "academic database."
    ),
    "input_schema": _QUERY_INPUT_SCHEMA,
    "strict": True,
}

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

FETCH_PAGE_TOOL: dict[str, Any] = {
    "name": "fetch_page",
    "description": (
        "Fetch a lightweight preview (title, description, site) of a candidate source page. "
        "Call this before citing a search result to verify it actually appears to support the "
        "claim it would be attached to."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "The candidate source URL to preview."}},
        "required": ["url"],
        "additionalProperties": False,
    },
    "strict": True,
}

_EDIT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "edit_type": {
            "type": "string",
            "enum": [t.value for t in EditType],
            "description": "The kind of edit being proposed.",
        },
        "original_text": {
            "type": "string",
            "description": "The exact existing wikitext being changed (must be a verbatim substring of the article excerpt).",
        },
        "proposed_text": {"type": "string", "description": "The replacement wikitext."},
        "rationale": {"type": "string", "description": "Why this edit is being made."},
        "policy_reference": {
            "type": ["string", "null"],
            "description": "Relevant Wikipedia policy (e.g. WP:CITE), or null if not applicable.",
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["edit_type", "original_text", "proposed_text", "rationale", "policy_reference", "confidence"],
    "additionalProperties": False,
}

PROPOSE_EDITS_TOOL: dict[str, Any] = {
    "name": "propose_edits",
    "description": (
        "Submit the final list of proposed edits and end the task. Call this once you have "
        "either found citations that genuinely support the flagged claims, or determined that "
        "no further searching would help. This is the terminal step — nothing else follows it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"edits": {"type": "array", "items": _EDIT_INPUT_SCHEMA}},
        "required": ["edits"],
        "additionalProperties": False,
    },
    "strict": True,
}

SEARCH_TOOLS: list[dict[str, Any]] = [SEARCH_SCHOLAR_TOOL, SEARCH_CROSSREF_TOOL, SEARCH_WEB_TOOL, SEARCH_BACKLINKS_TOOL, FETCH_PAGE_TOOL]
ALL_TOOLS: list[dict[str, Any]] = [*SEARCH_TOOLS, PROPOSE_EDITS_TOOL]

# Maps a search tool name to the config.sources.search_apis / activity-log API label.
_SEARCH_TOOL_API_NAMES: dict[str, str] = {
    "search_scholar": "semantic_scholar",
    "search_crossref": "crossref",
    "search_web": "web_search",
    "search_backlinks": "wikipedia_backlinks",
}


def _sources_to_dicts(sources: list[Source]) -> list[dict[str, Any]]:
    """Convert Source objects into a compact JSON-serializable shape for tool results."""
    return [
        {
            "title": s.title,
            "authors": s.authors,
            "year": s.publication_date,
            "doi": s.doi,
            "url": s.url,
            "citation_template": s.to_citation_template(),
        }
        for s in sources
    ]


class ClaudeAgent:
    """Claude-powered agent that searches for sources and proposes article edits."""

    def __init__(self):
        """Initialize the agent."""
        self.config = get_config()
        self.client = Anthropic(api_key=self.config.anthropic_api_key)
        self.source_finder = SourceFinder()
        self.guardrails = EditGuardrails()

    # --- Tool dispatch ---------------------------------------------------

    def _dispatch_search_tool(self, name: str, tool_input: dict[str, Any]) -> tuple[bool, str]:
        """Execute one search/fetch tool call. Never raises.

        Args:
            name: The tool name (search_scholar, search_crossref, search_web, search_backlinks, fetch_page).
            tool_input: The tool's parsed input dict.

        Returns:
            (ok, payload) — payload is a JSON string of results (or a page-preview
            dict) on success, or a human-readable error string when ok is False.
        """
        try:
            per_query = self.config.agent.search_results_per_query
            if name == "search_scholar":
                sources = self.source_finder.search_semantic_scholar(tool_input["query"], max_results=per_query)
                return True, json.dumps(_sources_to_dicts(sources))
            if name == "search_crossref":
                sources = self.source_finder.search_crossref(tool_input["query"], max_results=per_query)
                return True, json.dumps(_sources_to_dicts(sources))
            if name == "search_web":
                sources = self.source_finder.search_web(tool_input["query"], max_results=per_query)
                return True, json.dumps(_sources_to_dicts(sources))
            if name == "search_backlinks":
                sources = self.source_finder.find_backlink_sources(tool_input["article_title"])
                return True, json.dumps(_sources_to_dicts(sources))
            if name == "fetch_page":
                preview = self.source_finder.fetch_page_preview(tool_input["url"])
                return True, json.dumps(preview)
            return False, f"Unknown tool: {name}"
        except Exception as e:  # a failed tool call must never abort the loop
            return False, f"Tool execution error: {e}"

    def _tool_call_event(self, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Build the pre-execution progress event for a search/fetch tool call."""
        if name == "fetch_page":
            return {"type": "fetching", "url": tool_input.get("url", "")}
        return {
            "type": "searching",
            "api": _SEARCH_TOOL_API_NAMES.get(name, name),
            "query": tool_input.get("query") or tool_input.get("article_title", ""),
        }

    def _tool_result_event(self, name: str, ok: bool, payload: str) -> dict[str, Any]:
        """Build the post-execution progress event for a search/fetch tool call."""
        if not ok:
            return {"type": "source_none", "error": payload}

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = None

        if name == "fetch_page":
            preview = data if isinstance(data, dict) else {}
            return {"type": "fetching", "url": preview.get("url", ""), "title": preview.get("title")}

        results = data if isinstance(data, list) else []
        if results:
            top = results[0]
            return {
                "type": "source_found",
                "count": len(results),
                "source_title": top.get("title", ""),
                "citation": top.get("citation_template", ""),
            }
        return {"type": "source_none"}

    # --- Prompt & response parsing ----------------------------------------

    def _extract_json_from_response(self, text: str) -> list[dict[str, Any]]:
        """Extract a JSON array of edits from free-form response text (fallback path).

        Args:
            text: The response text

        Returns:
            List of edit dictionaries
        """
        json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if json_match:
            json_text = json_match.group(1)
        else:
            json_match = re.search(r"\[\s*\{.*?\}\s*\]", text, re.DOTALL)
            json_text = json_match.group(0) if json_match else text

        try:
            return json.loads(json_text)
        except json.JSONDecodeError as e:
            print(f"Failed to parse JSON: {e}")
            print(f"Response text: {text[:500]}")
            return []

    def _build_agentic_prompt(self, article: Article) -> str:
        """Assemble the user prompt from the focused excerpt, flagged claims, and instructions."""
        flagged_section = ""
        if article.citation_needed_claims:
            flagged = "\n".join(f'- "{claim}"' for claim in article.citation_needed_claims)
            flagged_section = (
                "## Claims tagged {{Citation needed}}\n"
                "These statements are already in the article and have been flagged as needing a "
                "source. Search for a source for each one; do not add new information.\n"
                f"{flagged}\n"
            )

        # Send only the lead paragraph plus the paragraphs containing flagged
        # claims, rather than the whole article — keeps the prompt small and focused.
        excerpt = build_focused_excerpt(article.wikitext)

        return f"""Please find citations for this Wikipedia article's flagged claims, then propose minimal edits.

## Article Title
{article.title}

## Article Excerpt
This is an excerpt: the lead paragraph and the paragraphs containing flagged claims. "[…]" marks omitted content. Only propose edits to text shown here.

{excerpt}

{flagged_section}
Use the search tools to find sources for the flagged claims above. When you have what you need
(or have determined further searching won't help), call `propose_edits` with your final edits.

Remember:
1. Only cite EXISTING claims, never add new information
2. Keep edits minimal - grammar, style, wikilinks, citations only
3. Do not change the article's scope or add new content
4. You must end by calling `propose_edits` - do not respond with plain text
"""

    @staticmethod
    def _edit_type_from_value(value: str) -> EditType | None:
        """Map an edit_type string (tool value, e.g. 'citation') to an EditType."""
        try:
            return EditType(value)
        except ValueError:
            pass
        try:
            return EditType[value.upper().replace(" ", "_")]
        except (KeyError, AttributeError):
            return None

    def _build_edits_from_data(self, article: Article, edits_data: list[dict[str, Any]]) -> list[ProposedEdit]:
        """Validate and guardrail-filter a list of edit dicts into ProposedEdit objects."""
        proposed_edits: list[ProposedEdit] = []
        for edit_data in edits_data[: self.config.agent.max_edits_per_article]:
            edit_type = self._edit_type_from_value(edit_data.get("edit_type", ""))
            if edit_type is None:
                continue

            edit = ProposedEdit(
                edit_type=edit_type,
                original_text=edit_data.get("original_text", ""),
                proposed_text=edit_data.get("proposed_text", ""),
                rationale=edit_data.get("rationale", ""),
                policy_reference=edit_data.get("policy_reference"),
                confidence=edit_data.get("confidence", "medium"),
                source=None,
            )

            is_valid, reason = self.guardrails.validate_edit(edit, article.wikitext, article.wikitext)
            if is_valid:
                proposed_edits.append(edit)
            else:
                print(f"Rejected edit: {reason}")
        return proposed_edits

    # --- The agentic loop --------------------------------------------------

    def analyze_article_events(self, article: Article):
        """Analyze an article via a bounded agentic search loop, yielding progress events.

        Drives its own call-and-response loop against Claude: reads the flagged
        {{Citation needed}} claims in context, issues search/fetch tool calls
        against SourceFinder, and terminates by calling the `propose_edits` tool
        with a final list of edits. Bounded by ``agent.max_search_turns``
        tool-executing model calls — once the budget is exhausted, the loop
        forces one final decision call with only the terminal tool available.

        Yields intermediate progress events — searches, fetches, thinking
        summaries, and edits proposed — and finally
        ``{"type": "analyzed", "proposal": ...}``. ``analyze_article`` wraps
        this and returns just the proposal, so callers that don't care about
        progress are unaffected.
        """
        claims = article.citation_needed_claims or self.source_finder.extract_claims(article.wikitext)
        if not claims:
            proposal = EditProposal(
                id=str(uuid.uuid4()),
                article=article,
                edits=[],
                status="rejected",
                reviewer_notes="No clear factual claims found to cite.",
            )
            yield {"type": "model_done", "edit_count": 0}
            yield {"type": "analyzed", "proposal": proposal}
            return

        messages: list[dict[str, Any]] = [{"role": "user", "content": self._build_agentic_prompt(article)}]
        max_turns = self.config.agent.max_search_turns
        turns = 0
        proposed_edits_data: list[dict[str, Any]] | None = None
        fallback_text = ""

        yield {"type": "model_call", "model": self.config.agent.model}

        try:
            while True:
                at_cap = turns >= max_turns
                tools = [PROPOSE_EDITS_TOOL] if at_cap else ALL_TOOLS
                tool_choice = {"type": "tool", "name": "propose_edits"} if at_cap else {"type": "auto"}

                response = self.client.messages.create(
                    model=self.config.agent.model,
                    max_tokens=8000,
                    system=_CACHED_SEARCH_SYSTEM_PROMPT,
                    thinking={"type": "adaptive", "display": "summarized"},
                    output_config={"effort": "high"},
                    tools=tools,
                    tool_choice=tool_choice,
                    messages=messages,
                )
                logger.debug(
                    "messages.create usage: input=%s cache_read=%s cache_creation=%s output=%s",
                    response.usage.input_tokens,
                    response.usage.cache_read_input_tokens,
                    response.usage.cache_creation_input_tokens,
                    response.usage.output_tokens,
                )

                # --- stop_reason handling (guard BEFORE reading content) ---
                if response.stop_reason == "refusal":
                    break  # decline -> skip; content may be empty
                if response.stop_reason in ("end_turn", "max_tokens"):
                    fallback_text = "".join(
                        block.text for block in response.content if getattr(block, "type", None) == "text"
                    )
                    break  # no tool call; take what we have

                # stop_reason == "tool_use":
                # 1) append the assistant turn VERBATIM - thinking + tool_use blocks unchanged.
                messages.append({"role": "assistant", "content": response.content})

                # 2) execute every tool_use block; collect ALL results into ONE user message.
                tool_results: list[dict[str, Any]] = []
                terminal = False
                for block in response.content:
                    if block.type == "thinking":
                        text = getattr(block, "thinking", "") or ""
                        if text:
                            yield {"type": "thinking", "text": text}
                        continue
                    if block.type != "tool_use":
                        continue
                    if block.name == "propose_edits":
                        proposed_edits_data = block.input.get("edits", [])
                        terminal = True
                        tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": "recorded"})
                        continue

                    yield self._tool_call_event(block.name, block.input)
                    ok, payload = self._dispatch_search_tool(block.name, block.input)
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": payload, "is_error": not ok}
                    )
                    yield self._tool_result_event(block.name, ok, payload)

                messages.append({"role": "user", "content": tool_results})  # single user message
                if terminal:
                    break
                turns += 1
        except Exception as e:
            logger.warning("Error calling Claude API: %s", e)
            yield {"type": "model_error", "error": str(e)}
            yield {
                "type": "analyzed",
                "proposal": EditProposal(
                    id=str(uuid.uuid4()), article=article, edits=[], status="rejected", reviewer_notes=f"Error: {e}"
                ),
            }
            return

        if proposed_edits_data is None and fallback_text:
            proposed_edits_data = self._extract_json_from_response(fallback_text)

        proposed_edits = self._build_edits_from_data(article, proposed_edits_data or [])
        proposal = EditProposal(id=str(uuid.uuid4()), article=article, edits=proposed_edits, status="pending")
        yield {"type": "model_done", "edit_count": len(proposed_edits)}
        yield {"type": "analyzed", "proposal": proposal}

    def analyze_article(self, article: Article) -> EditProposal:
        """Analyze an article and propose minimal edits.

        Args:
            article: The article to analyze

        Returns:
            EditProposal with suggested edits
        """
        proposal = None
        for event in self.analyze_article_events(article):
            if event["type"] == "analyzed":
                proposal = event["proposal"]
        return proposal

    def apply_edits(self, article: Article, edits: list[ProposedEdit]) -> str:
        """Apply approved edits to an article.

        Args:
            article: The original article
            edits: List of approved edits to apply

        Returns:
            Modified article wikitext
        """
        modified_text = article.wikitext

        # Sort edits by position in text (to apply from end to start)
        # This prevents position shifts from affecting later edits
        sorted_edits = sorted(edits, key=lambda e: modified_text.find(e.original_text), reverse=True)

        for edit in sorted_edits:
            # Find and replace the original text
            if edit.original_text in modified_text:
                modified_text = modified_text.replace(
                    edit.original_text,
                    edit.proposed_text,
                    1,  # Replace only first occurrence
                )

        return modified_text
