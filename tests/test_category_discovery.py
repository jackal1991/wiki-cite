"""Tests for Anthropic-facing category relevance classification."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from wiki_cite.category_discovery import _classify_batch, _parse_keep_map, classify_categories


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _response(text: str):
    return SimpleNamespace(content=[_text_block(text)])


def _client_returning(text: str):
    client = SimpleNamespace(messages=SimpleNamespace(create=Mock(return_value=_response(text))))
    return client


def test_parse_keep_map_plain_json():
    text = '{"20th-century American politicians": true, "American politics task force": false}'
    result = _parse_keep_map(text, ["20th-century American politicians", "American politics task force"])
    assert result == {"20th-century American politicians": True, "American politics task force": False}


def test_parse_keep_map_code_fence():
    text = '```json\n{"American politician stubs": true}\n```'
    result = _parse_keep_map(text, ["American politician stubs"])
    assert result == {"American politician stubs": True}


def test_parse_keep_map_surrounding_prose():
    text = 'Here is my classification:\n{"Foo": true}\nHope that helps!'
    result = _parse_keep_map(text, ["Foo"])
    assert result == {"Foo": True}


def test_parse_keep_map_missing_name_absent_from_map():
    """A name absent from the parsed JSON is simply absent — callers treat that as excluded."""
    text = '{"Foo": true}'
    result = _parse_keep_map(text, ["Foo", "Bar"])
    assert result == {"Foo": True}
    assert "Bar" not in result


def test_parse_keep_map_malformed_json_returns_empty():
    result = _parse_keep_map("not json at all", ["Foo"])
    assert result == {}


def test_classify_batch_keeps_content_and_drops_maintenance():
    """AC2.1: maintenance-style names are dropped, topical/stub names are kept."""
    names = ["20th-century American politicians", "American politician stubs", "American politics task force"]
    payload = json.dumps(
        {
            "20th-century American politicians": True,
            "American politician stubs": True,
            "American politics task force": False,
        }
    )
    client = _client_returning(payload)

    result = _classify_batch(client, "claude-sonnet-5", names)

    assert sorted(result) == sorted(["20th-century American politicians", "American politician stubs"])


def test_classify_batch_raises_excludes_whole_batch():
    """AC2.2: a batch whose messages.create raises results in every name excluded."""
    client = SimpleNamespace(messages=SimpleNamespace(create=Mock(side_effect=RuntimeError("API error"))))

    result = _classify_batch(client, "claude-sonnet-5", ["Foo", "Bar"])

    assert result == []


def test_classify_batch_malformed_response_excludes_whole_batch():
    """AC2.2: non-JSON response text results in every name in the batch excluded."""
    client = _client_returning("I cannot help with that.")

    result = _classify_batch(client, "claude-sonnet-5", ["Foo", "Bar"])

    assert result == []


def test_classify_categories_unions_batches_and_fails_closed_per_batch():
    """AC2.2: one batch's call raises, but the other batch still classifies and
    contributes its accepted names."""
    good_names = [f"Good {i}" for i in range(20)]
    bad_names = [f"Bad {i}" for i in range(5)]

    good_payload = json.dumps({name: True for name in good_names})

    call_count = {"n": 0}

    def create(**kwargs):
        call_count["n"] += 1
        content = kwargs["messages"][0]["content"]
        if "Bad" in content:
            raise RuntimeError("API error")
        return _response(good_payload)

    client = SimpleNamespace(messages=SimpleNamespace(create=Mock(side_effect=create)))

    result = classify_categories(good_names + bad_names, client=client, batch_size=5, max_workers=2)

    assert result == sorted(good_names)
    for name in bad_names:
        assert name not in result


def test_classify_categories_dedupes_and_sorts():
    payload = json.dumps({"Alpha": True, "Beta": True})
    client = _client_returning(payload)

    result = classify_categories(["Beta", "Alpha", "Alpha"], client=client)

    assert result == ["Alpha", "Beta"]


def test_classify_categories_builds_client_from_config_when_none_given():
    payload = json.dumps({"Foo": True})
    with patch("wiki_cite.category_discovery.Anthropic") as mock_anthropic:
        mock_anthropic.return_value = _client_returning(payload)

        result = classify_categories(["Foo"])

        mock_anthropic.assert_called_once()
        assert result == ["Foo"]
