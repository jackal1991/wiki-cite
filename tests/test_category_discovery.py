"""Tests for Anthropic-facing category relevance classification."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wiki_cite.category_discovery as category_discovery
from wiki_cite.category_discovery import (
    _classify_batch,
    _parse_keep_map,
    classify_categories,
    expansion_file_path,
    load_expansion,
    slugify_root,
    write_expansion_file,
)


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
    """AC2.1: maintenance-style names (task force, quality/importance assessment,
    WikiProject participants) are dropped; topical and `...stubs` names are kept."""
    names = [
        "20th-century American politicians",
        "American politician stubs",
        "American politics task force",
        "American politics articles by quality",
        "WikiProject Biography participants",
    ]
    payload = json.dumps(
        {
            "20th-century American politicians": True,
            "American politician stubs": True,
            "American politics task force": False,
            "American politics articles by quality": False,
            "WikiProject Biography participants": False,
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


def test_slugify_root_strips_prefix_and_normalizes():
    assert slugify_root("Category:American Politicians") == "american-politicians"
    assert slugify_root("American Politicians") == "american-politicians"
    assert slugify_root("American_Politicians") == "american-politicians"


def test_slugify_root_drops_non_alnum_hyphen_chars():
    assert slugify_root("Category:20th-century (U.S.) politicians!") == "20th-century-us-politicians"


def test_expansion_file_path_uses_slug(tmp_path, monkeypatch):
    monkeypatch.setattr(category_discovery, "EXPANSIONS_DIR", tmp_path)
    assert expansion_file_path("Category:Foo Bar") == tmp_path / "foo-bar.json"


def test_write_expansion_file_includes_root_sorted_deduplicated(tmp_path, monkeypatch):
    """AC3.1: root is always included even if not in the classified list; output is
    sorted and deduplicated."""
    monkeypatch.setattr(category_discovery, "EXPANSIONS_DIR", tmp_path)

    path = write_expansion_file("Category:Root Topic", ["Zeta", "Alpha", "Alpha"], max_depth=2)

    assert path == tmp_path / "root-topic.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["root"] == "Root Topic"
    assert data["max_depth"] == 2
    assert data["categories"] == ["Alpha", "Root Topic", "Zeta"]
    assert "generated_at" in data


def test_write_expansion_file_deterministic_except_timestamp(tmp_path, monkeypatch):
    """AC3.2: given fixed inputs, two writes produce identical content except generated_at."""
    monkeypatch.setattr(category_discovery, "EXPANSIONS_DIR", tmp_path)

    path1 = write_expansion_file("Root", ["B", "A"], max_depth=None)
    data1 = json.loads(path1.read_text(encoding="utf-8"))

    path2 = write_expansion_file("Root", ["B", "A"], max_depth=None)
    data2 = json.loads(path2.read_text(encoding="utf-8"))

    assert path1 == path2
    assert {k: v for k, v in data1.items() if k != "generated_at"} == {k: v for k, v in data2.items() if k != "generated_at"}


def test_write_expansion_file_overwrites_wholesale(tmp_path, monkeypatch):
    """AC3.2: a second write with different categories replaces the file content
    entirely rather than merging with the first write's categories."""
    monkeypatch.setattr(category_discovery, "EXPANSIONS_DIR", tmp_path)

    write_expansion_file("Root", ["Old One", "Old Two"], max_depth=None)
    path = write_expansion_file("Root", ["New One"], max_depth=None)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["categories"] == ["New One", "Root"]
    assert "Old One" not in data["categories"]


def test_write_expansion_file_creates_directory(tmp_path, monkeypatch):
    nested = tmp_path / "nested" / "expansions"
    monkeypatch.setattr(category_discovery, "EXPANSIONS_DIR", nested)

    path = write_expansion_file("Root", [], max_depth=None)

    assert path.exists()


def test_load_expansion_returns_categories_when_file_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(category_discovery, "EXPANSIONS_DIR", tmp_path)
    write_expansion_file("Root Topic", ["Sub A", "Sub B"], max_depth=1)

    result = load_expansion("Root Topic")

    assert result == ["Root Topic", "Sub A", "Sub B"]


def test_load_expansion_returns_none_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(category_discovery, "EXPANSIONS_DIR", tmp_path)

    assert load_expansion("Nonexistent Root") is None


def test_load_expansion_returns_none_and_warns_on_malformed_json(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(category_discovery, "EXPANSIONS_DIR", tmp_path)
    path = expansion_file_path("Broken Root")
    path.write_text("not valid json{{{", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = load_expansion("Broken Root")

    assert result is None
    assert any("Broken Root" in record.message or str(path) in record.message for record in caplog.records)


def test_load_expansion_returns_none_when_categories_key_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(category_discovery, "EXPANSIONS_DIR", tmp_path)
    path = expansion_file_path("Incomplete Root")
    path.write_text(json.dumps({"root": "Incomplete Root"}), encoding="utf-8")

    assert load_expansion("Incomplete Root") is None


def test_load_expansion_returns_none_when_categories_is_not_a_list_of_strings(tmp_path, monkeypatch, caplog):
    """A hand-edited/corrupt file with e.g. 'categories': 'Foo' (a string, not a list)
    must fail closed to None rather than let a caller iterate its characters."""
    monkeypatch.setattr(category_discovery, "EXPANSIONS_DIR", tmp_path)
    path = expansion_file_path("Bad Shape Root")
    path.write_text(json.dumps({"root": "Bad Shape Root", "categories": "Foo"}), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        result = load_expansion("Bad Shape Root")

    assert result is None
    assert any("Bad Shape Root" in record.message or str(path) in record.message for record in caplog.records)


def test_load_expansion_returns_none_when_categories_contains_non_strings(tmp_path, monkeypatch):
    monkeypatch.setattr(category_discovery, "EXPANSIONS_DIR", tmp_path)
    path = expansion_file_path("Mixed Types Root")
    path.write_text(json.dumps({"root": "Mixed Types Root", "categories": ["Alpha", 123]}), encoding="utf-8")

    assert load_expansion("Mixed Types Root") is None
