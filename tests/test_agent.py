"""Tests for the Claude agent's agentic search loop."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wiki_cite.agent import ALL_TOOLS, PROPOSE_EDITS_TOOL, SEARCH_BACKLINKS_TOOL, SEARCH_SYSTEM_PROMPT, SEARCH_TOOLS, ClaudeAgent
from wiki_cite.models import Article, EditType, ProposedEdit, ReliabilityRating, Source, SourceType


def _thinking_block(text: str = "Considering how to search..."):
    return SimpleNamespace(type="thinking", thinking=text)


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(tool_id: str, name: str, tool_input: dict):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


def _response(stop_reason: str, content: list):
    usage = SimpleNamespace(input_tokens=10, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=10)
    return SimpleNamespace(stop_reason=stop_reason, content=content, usage=usage)


@pytest.fixture
def agent():
    """Create agent instance with a mocked Anthropic client."""
    with patch("wiki_cite.agent.Anthropic"):
        instance = ClaudeAgent()
        instance.client = SimpleNamespace(messages=SimpleNamespace(create=None))
        return instance


@pytest.fixture
def sample_article():
    """Create a sample article with a flagged claim for testing."""
    return Article(
        title="Test Article",
        url="https://en.wikipedia.org/wiki/Test_Article",
        wikitext="Test Article was founded in 1990. It was created in 2020.",
        revision_id="12345",
        citation_needed_claims=["Test Article was founded in 1990"],
    )


def _run_events(agent, article):
    """Run analyze_article_events to completion and return (events, proposal)."""
    events = []
    proposal = None
    for event in agent.analyze_article_events(article):
        events.append(event)
        if event["type"] == "analyzed":
            proposal = event["proposal"]
    return events, proposal


def test_search_system_prompt_carries_sourcing_policy():
    """The Citation Addition guidance must encode WP:RS / WP:PSTS / WP:SPS so the agent's
    in-loop source choices are grounded in policy (issue #7)."""
    prompt = SEARCH_SYSTEM_PROMPT
    # Policy shorthands present (match the WP:MOS/WP:BLP style already in the prompt).
    for token in ("WP:RS", "WP:PSTS", "WP:SPS"):
        assert token in prompt, f"missing policy reference {token}"

    lowered = prompt.lower()
    # WP:RS reliability criteria.
    assert "editorial oversight" in lowered
    assert "independent" in lowered
    # WP:PSTS: prefer secondary over primary.
    assert "secondary" in lowered and "primary" in lowered
    # WP:SPS: exclude self-published / UGC.
    assert "self-published" in lowered
    for banned in ("blog", "forum", "social media"):
        assert banned in lowered, f"expected {banned} in self-published exclusions"


def test_search_system_prompt_forbids_citing_wikipedia():
    """AC5.1/AC5.2: the prompt must explicitly forbid citing Wikipedia itself — including the
    backlinking article a search_backlinks URL was found on — with WP:CIRCULAR/WP:WPNOTRS
    language, so a future edit can't silently drop this guardrail."""
    prompt = SEARCH_SYSTEM_PROMPT

    for token in ("WP:CIRCULAR", "WP:WPNOTRS"):
        assert token in prompt, f"missing policy reference {token}"

    lowered = prompt.lower()
    assert "search_backlinks" in lowered
    # Collapse line-wrap whitespace so a phrase split across source lines still matches.
    normalized = " ".join(lowered.split())
    assert "do not cite wikipedia" in normalized
    assert "backlinking article" in normalized


# --- Extraction / apply_edits (unchanged behavior) --------------------------


def test_extract_json_from_response_with_code_block(agent):
    """Test extracting JSON from markdown code block."""
    response = """Here are the edits:
```json
[
  {"edit_type": "grammar", "original_text": "test", "proposed_text": "test2"}
]
```
"""
    result = agent._extract_json_from_response(response)
    assert len(result) == 1
    assert result[0]["edit_type"] == "grammar"


def test_extract_json_from_response_with_raw_json(agent):
    """Test extracting raw JSON from response."""
    response = '[{"edit_type": "citation", "original_text": "test"}]'
    result = agent._extract_json_from_response(response)
    assert len(result) == 1
    assert result[0]["edit_type"] == "citation"


def test_extract_json_from_invalid_response(agent):
    """Test extracting JSON from invalid response."""
    response = "This is not JSON"
    result = agent._extract_json_from_response(response)
    assert result == []


def test_apply_edits_single_edit(agent, sample_article):
    """Test applying a single edit to article."""
    edit = ProposedEdit(
        edit_type=EditType.GRAMMAR_FIX,
        original_text="founded in 1990",
        proposed_text="founded in the year 1990",
        rationale="Enhancement",
        confidence="high",
    )

    result = agent.apply_edits(sample_article, [edit])

    assert "founded in the year 1990" in result


def test_apply_edits_multiple_edits(agent, sample_article):
    """Test applying multiple edits to article."""
    edit1 = ProposedEdit(
        edit_type=EditType.GRAMMAR_FIX,
        original_text="was founded",
        proposed_text="had been founded",
        rationale="Tense correction",
        confidence="high",
    )

    edit2 = ProposedEdit(
        edit_type=EditType.WIKILINK_ADDED,
        original_text="2020",
        proposed_text="[[2020]]",
        rationale="Add wikilink",
        confidence="high",
    )

    result = agent.apply_edits(sample_article, [edit1, edit2])

    assert "had been founded" in result
    assert "[[2020]]" in result


def test_apply_edits_no_edits(agent, sample_article):
    """Test applying empty edit list."""
    result = agent.apply_edits(sample_article, [])
    assert result == sample_article.wikitext


# --- Tool dispatcher (Phase 1: pure, no network) -----------------------------


def test_dispatch_search_scholar_success(agent):
    """search_scholar wraps SourceFinder.search_semantic_scholar and returns JSON."""
    sources = [
        Source(title="A Paper", authors=["A. Author"], url="https://doi.org/10.1/x", source_type=SourceType.JOURNAL, reliability=ReliabilityRating.GENERALLY_RELIABLE)
    ]
    with patch.object(agent.source_finder, "search_semantic_scholar", return_value=sources) as mock_search:
        ok, payload = agent._dispatch_search_tool("search_scholar", {"query": "test query"})

    assert ok is True
    assert "A Paper" in payload
    mock_search.assert_called_once_with("test query", max_results=agent.config.agent.search_results_per_query)


def test_dispatch_search_crossref_success(agent):
    """search_crossref wraps SourceFinder.search_crossref."""
    with patch.object(agent.source_finder, "search_crossref", return_value=[]) as mock_search:
        ok, payload = agent._dispatch_search_tool("search_crossref", {"query": "q"})

    assert ok is True
    assert payload == "[]"
    mock_search.assert_called_once()


def test_dispatch_search_web_success(agent):
    """search_web wraps SourceFinder.search_web."""
    sources = [Source(title="News item", url="https://example.com/a", source_type=SourceType.NEWS)]
    with patch.object(agent.source_finder, "search_web", return_value=sources):
        ok, payload = agent._dispatch_search_tool("search_web", {"query": "q"})

    assert ok is True
    assert "News item" in payload


def test_search_backlinks_in_tool_lists():
    """AC1.1: search_backlinks is a search tool, available in both SEARCH_TOOLS and ALL_TOOLS."""
    assert SEARCH_BACKLINKS_TOOL in SEARCH_TOOLS
    assert SEARCH_BACKLINKS_TOOL in ALL_TOOLS


def test_dispatch_search_backlinks_success(agent):
    """search_backlinks wraps SourceFinder.find_backlink_sources and returns the same
    _sources_to_dicts JSON shape as the other search tools (AC1.1, AC4.1)."""
    sources = [Source(title="Related coverage", url="https://example.com/a", source_type=SourceType.WEB, reliability=ReliabilityRating.GENERALLY_RELIABLE)]
    with patch.object(agent.source_finder, "find_backlink_sources", return_value=sources) as mock_find:
        ok, payload = agent._dispatch_search_tool("search_backlinks", {"article_title": "Test Article"})

    assert ok is True
    assert "Related coverage" in payload
    mock_find.assert_called_once_with("Test Article")


def test_dispatch_fetch_page_success(agent):
    """fetch_page wraps SourceFinder.fetch_page_preview."""
    preview = {"url": "https://example.com", "ok": True, "title": "Example", "description": None, "site_name": "example.com", "image": None, "error": None}
    with patch.object(agent.source_finder, "fetch_page_preview", return_value=preview):
        ok, payload = agent._dispatch_search_tool("fetch_page", {"url": "https://example.com"})

    assert ok is True
    assert "Example" in payload


def test_dispatch_unknown_tool(agent):
    """An unrecognized tool name is an error, not a crash."""
    ok, payload = agent._dispatch_search_tool("not_a_real_tool", {})
    assert ok is False
    assert "Unknown tool" in payload


def test_dispatch_tool_execution_failure_is_error(agent):
    """A raised exception in the wrapped SourceFinder call surfaces as is_error, not a crash (AC5)."""
    with patch.object(agent.source_finder, "search_semantic_scholar", side_effect=RuntimeError("network down")):
        ok, payload = agent._dispatch_search_tool("search_scholar", {"query": "q"})

    assert ok is False
    assert "network down" in payload


# --- The agentic loop (Phase 2 + AC1/AC2/AC3/AC5) ---------------------------


def test_no_claims_skips_model_entirely(agent):
    """AC1.2: an article with no findable claims terminates in <=1 model turn, no API call."""
    article = Article(
        title="Empty",
        url="https://en.wikipedia.org/wiki/Empty",
        wikitext="",
        revision_id="1",
        citation_needed_claims=[],
    )
    with patch.object(agent.source_finder, "extract_claims", return_value=[]):
        events, proposal = _run_events(agent, article)

    assert proposal.edits == []
    assert {"type": "model_done", "edit_count": 0} in events
    assert not any(e["type"] == "model_call" for e in events)


def test_happy_path_tool_use_then_propose_edits(agent, sample_article):
    """AC1.1/AC2.1: search_web -> fetch_page -> propose_edits produces a valid proposal
    and the exact messages contract: verbatim content echo, single tool_result user
    message per turn, matching tool_use_id."""
    thinking = _thinking_block("I should search the web for this claim.")
    search_call = _tool_use_block("toolu_1", "search_web", {"query": "Test Article founded 1990"})
    response1 = _response("tool_use", [thinking, search_call])

    fetch_call = _tool_use_block("toolu_2", "fetch_page", {"url": "https://example.com/a"})
    response2 = _response("tool_use", [fetch_call])

    edits_payload = {
        "edits": [
            {
                "edit_type": "citation",
                "original_text": "Test Article was founded in 1990",
                "proposed_text": "Test Article was founded in 1990<ref>{{cite web|url=https://example.com/a|title=Example}}</ref>",
                "rationale": "Adding citation for existing claim",
                "policy_reference": "WP:CITE",
                "confidence": "high",
            }
        ]
    }
    propose_call = _tool_use_block("toolu_3", "propose_edits", edits_payload)
    response3 = _response("tool_use", [propose_call])

    sources = [Source(title="Example Source", url="https://example.com/a", source_type=SourceType.WEB)]
    preview = {"url": "https://example.com/a", "ok": True, "title": "Example", "description": None, "site_name": "example.com", "image": None, "error": None}

    agent.client.messages.create = None
    with (
        patch.object(agent.client.messages, "create", side_effect=[response1, response2, response3]) as mock_create,
        patch.object(agent.source_finder, "search_web", return_value=sources),
        patch.object(agent.source_finder, "fetch_page_preview", return_value=preview),
    ):
        events, proposal = _run_events(agent, sample_article)

    # Proposal is valid and the citation edit survived guardrails.
    assert proposal is not None
    assert len(proposal.edits) == 1
    assert proposal.edits[0].edit_type == EditType.CITATION_ADDED
    assert proposal.edits[0].original_text in sample_article.wikitext

    # Progress events surfaced for streaming (AC4).
    assert {"type": "thinking", "text": "I should search the web for this claim."} in events
    assert any(e["type"] == "searching" and e["api"] == "web_search" for e in events)
    assert any(e["type"] == "source_found" for e in events)
    assert any(e["type"] == "fetching" for e in events)
    assert {"type": "model_done", "edit_count": 1} in events

    assert mock_create.call_count == 3

    # --- AC2: exact messages contract ---
    _, kwargs1 = mock_create.call_args_list[0]
    _, kwargs2 = mock_create.call_args_list[1]
    _, kwargs3 = mock_create.call_args_list[2]

    messages_before_call2 = kwargs2["messages"]
    messages_before_call3 = kwargs3["messages"]

    # messages[0] is the original user prompt, untouched throughout.
    assert messages_before_call2[0] == kwargs1["messages"][0]

    # The assistant turn after response1 is echoed back VERBATIM (same object/content,
    # including the thinking block) - never stripped or reconstructed.
    assert messages_before_call2[1] == {"role": "assistant", "content": response1.content}

    # All tool_result blocks for response1's single tool_use go in ONE user message,
    # and the tool_use_id matches the originating tool_use block's id.
    assert messages_before_call2[2]["role"] == "user"
    assert len(messages_before_call2[2]["content"]) == 1
    assert messages_before_call2[2]["content"][0]["tool_use_id"] == "toolu_1"
    assert messages_before_call2[2]["content"][0]["is_error"] is False

    # Same verbatim-echo contract after response2 (fetch_page turn).
    assert messages_before_call3[3] == {"role": "assistant", "content": response2.content}
    assert messages_before_call3[4]["role"] == "user"
    assert len(messages_before_call3[4]["content"]) == 1
    assert messages_before_call3[4]["content"][0]["tool_use_id"] == "toolu_2"

    # Terminal call used the default (auto) tool_choice, not the forced cap.
    assert kwargs3["tool_choice"] == {"type": "auto"}

    # thinking/adaptive contract is present on every call (non-negotiable API facts).
    for _, kwargs in mock_create.call_args_list:
        assert kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert "budget_tokens" not in kwargs["thinking"]
        assert "temperature" not in kwargs
        assert "top_p" not in kwargs
        assert "top_k" not in kwargs


def test_parallel_tool_calls_in_one_turn_share_a_single_user_message(agent, sample_article):
    """AC2.1: two tool_use blocks in the same assistant turn produce ONE user message
    with two tool_result blocks, not two separate user messages."""
    scholar_call = _tool_use_block("toolu_a", "search_scholar", {"query": "q1"})
    web_call = _tool_use_block("toolu_b", "search_web", {"query": "q2"})
    response1 = _response("tool_use", [scholar_call, web_call])

    propose_call = _tool_use_block("toolu_c", "propose_edits", {"edits": []})
    response2 = _response("tool_use", [propose_call])

    with (
        patch.object(agent.client.messages, "create", side_effect=[response1, response2]) as mock_create,
        patch.object(agent.source_finder, "search_semantic_scholar", return_value=[]),
        patch.object(agent.source_finder, "search_web", return_value=[]),
    ):
        _run_events(agent, sample_article)

    _, kwargs2 = mock_create.call_args_list[1]
    messages = kwargs2["messages"]

    # Exactly one user message follows response1's assistant turn (messages[0]=prompt,
    # messages[1]=assistant echo, messages[2]=the single tool_result batch for both calls).
    tool_result_batch = messages[2]
    assert tool_result_batch["role"] == "user"
    assert len(tool_result_batch["content"]) == 2
    ids = {block["tool_use_id"] for block in tool_result_batch["content"]}
    assert ids == {"toolu_a", "toolu_b"}


def test_turn_cap_forces_terminal_tool(agent, sample_article):
    """AC3.1: the loop makes at most max_search_turns tool-executing calls, then one
    forced final decision call with only propose_edits available."""
    agent.config.agent.max_search_turns = 2

    search1 = _response("tool_use", [_tool_use_block("t1", "search_web", {"query": "a"})])
    search2 = _response("tool_use", [_tool_use_block("t2", "search_web", {"query": "b"})])
    forced_final = _response("tool_use", [_tool_use_block("t3", "propose_edits", {"edits": []})])

    with (
        patch.object(agent.client.messages, "create", side_effect=[search1, search2, forced_final]) as mock_create,
        patch.object(agent.source_finder, "search_web", return_value=[]),
    ):
        events, proposal = _run_events(agent, sample_article)

    assert mock_create.call_count == 3  # max_search_turns (2) + 1 forced decision call
    assert proposal is not None
    assert proposal.edits == []

    # The final (cap) call forced the terminal tool and disabled the search tools.
    _, kwargs_final = mock_create.call_args_list[2]
    assert kwargs_final["tool_choice"] == {"type": "tool", "name": "propose_edits"}
    assert kwargs_final["tools"] == [PROPOSE_EDITS_TOOL]

    # The earlier calls were not capped.
    _, kwargs_first = mock_create.call_args_list[0]
    assert kwargs_first["tool_choice"] == {"type": "auto"}


def test_search_backlinks_unavailable_at_turn_cap(agent, sample_article):
    """AC1.2: at the turn cap, search_backlinks is unavailable exactly like the other
    search tools — only propose_edits is offered."""
    agent.config.agent.max_search_turns = 1

    search1 = _response("tool_use", [_tool_use_block("t1", "search_backlinks", {"article_title": "Test Article"})])
    forced_final = _response("tool_use", [_tool_use_block("t2", "propose_edits", {"edits": []})])

    with (
        patch.object(agent.client.messages, "create", side_effect=[search1, forced_final]) as mock_create,
        patch.object(agent.source_finder, "find_backlink_sources", return_value=[]),
    ):
        _run_events(agent, sample_article)

    _, kwargs_final = mock_create.call_args_list[1]
    assert SEARCH_BACKLINKS_TOOL not in kwargs_final["tools"]
    assert kwargs_final["tools"] == [PROPOSE_EDITS_TOOL]


def test_refusal_stop_reason_ends_loop_gracefully(agent, sample_article):
    """AC5.1: stop_reason == 'refusal' ends the loop with whatever edits exist (none)."""
    refusal_response = _response("refusal", [])

    with patch.object(agent.client.messages, "create", return_value=refusal_response) as mock_create:
        events, proposal = _run_events(agent, sample_article)

    assert mock_create.call_count == 1
    assert proposal is not None
    assert proposal.edits == []
    assert not any(e["type"] == "model_error" for e in events)


def test_max_tokens_stop_reason_falls_back_to_text_extraction(agent, sample_article):
    """AC5.1: stop_reason == 'max_tokens' ends the loop; any text content is used as a
    best-effort fallback rather than losing the turn entirely."""
    text = '```json\n[{"edit_type": "citation", "original_text": "Test Article was founded in 1990", "proposed_text": "Test Article was founded in 1990<ref>x</ref>", "rationale": "r", "policy_reference": null, "confidence": "medium"}]\n```'
    max_tokens_response = _response("max_tokens", [_text_block(text)])

    with patch.object(agent.client.messages, "create", return_value=max_tokens_response) as mock_create:
        events, proposal = _run_events(agent, sample_article)

    assert mock_create.call_count == 1
    assert proposal is not None
    assert len(proposal.edits) == 1
    assert proposal.edits[0].edit_type == EditType.CITATION_ADDED
    assert not any(e["type"] == "model_error" for e in events)


def test_system_prompt_is_cached(agent, sample_article):
    """The system prompt (and tools, which render before it) must carry a
    cache_control breakpoint - it's a frozen constant on every call across every
    article, so caching it is a pure win. Verified on both the first call and a
    later turn, since the cached constant is reused unchanged across the loop."""
    text = '```json\n[]\n```'
    response1 = _response("tool_use", [_tool_use_block("t1", "search_scholar", {"query": "q"})])
    response2 = _response("end_turn", [_text_block(text)])

    with patch.object(agent.client.messages, "create", side_effect=[response1, response2]) as mock_create:
        _run_events(agent, sample_article)

    assert mock_create.call_count == 2
    for _, kwargs in mock_create.call_args_list:
        system = kwargs["system"]
        assert isinstance(system, list)
        assert len(system) == 1
        assert system[0]["type"] == "text"
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert system[0]["text"] == SEARCH_SYSTEM_PROMPT


def test_tool_execution_failure_reports_is_error_and_continues(agent, sample_article):
    """AC5.1: a tool execution error returns is_error: true and the loop continues
    to a normal terminal propose_edits call (does not abort the whole fetch)."""
    failing_search = _response("tool_use", [_tool_use_block("t1", "search_scholar", {"query": "q"})])
    propose_call = _response("tool_use", [_tool_use_block("t2", "propose_edits", {"edits": []})])

    with (
        patch.object(agent.client.messages, "create", side_effect=[failing_search, propose_call]) as mock_create,
        patch.object(agent.source_finder, "search_semantic_scholar", side_effect=RuntimeError("boom")),
    ):
        events, proposal = _run_events(agent, sample_article)

    assert mock_create.call_count == 2
    assert proposal is not None
    assert not any(e["type"] == "model_error" for e in events)

    _, kwargs2 = mock_create.call_args_list[1]
    tool_result = kwargs2["messages"][2]["content"][0]
    assert tool_result["tool_use_id"] == "t1"
    assert tool_result["is_error"] is True
    assert "boom" in tool_result["content"]


def test_unhandled_api_exception_does_not_crash_the_generator(agent, sample_article):
    """An unexpected exception from the client itself is caught and surfaced as a
    model_error event, ending with an analyzed (rejected) proposal rather than raising."""
    with patch.object(agent.client.messages, "create", side_effect=RuntimeError("connection reset")):
        events, proposal = _run_events(agent, sample_article)

    assert proposal is not None
    assert proposal.status == "rejected"
    assert proposal.edits == []
    assert any(e["type"] == "model_error" for e in events)
