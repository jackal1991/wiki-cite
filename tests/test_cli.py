"""Tests for the wiki-cite CLI commands."""

import argparse
import json
from unittest.mock import Mock, patch

from wiki_cite.cli import cmd_discover_categories, cmd_stats
from wiki_cite.config import Config, set_config
from wiki_cite.seen_store import SeenStore


def test_cmd_stats_prints_rates(tmp_path, capsys):
    db_path = str(tmp_path / "seen.db")
    store = SeenStore(db_path)
    store.record_outcome("A", "1", "approved", source_type="news")
    store.record_outcome("B", "2", "pushed", source_type="news")
    store.record_outcome("C", "3", "rejected", source_type="news")
    set_config(Config(SEEN_DB_PATH=db_path))

    cmd_stats(argparse.Namespace())

    out = capsys.readouterr().out
    assert "source_type:" in out
    assert "news" in out
    assert "2/3" in out


def test_cmd_stats_omits_zero_sample(tmp_path, capsys):
    db_path = str(tmp_path / "seen.db")
    SeenStore(db_path)  # fresh, empty DB
    set_config(Config(SEEN_DB_PATH=db_path))

    cmd_stats(argparse.Namespace())  # must not raise ZeroDivisionError

    out = capsys.readouterr().out
    assert "0%" not in out
    assert "(no data)" in out


def test_cmd_discover_categories_wires_crawl_classify_write(tmp_path, capsys):
    """The command crawls, classifies, and writes the expansion file, printing
    progress and the final written count (root included)."""
    fake_path = tmp_path / "topic.json"
    fake_path.write_text(json.dumps({"categories": ["Alpha", "Topic"]}), encoding="utf-8")

    with (
        patch("wiki_cite.cli.ArticlePicker") as mock_picker_cls,
        patch("wiki_cite.cli.crawl_subcategories") as mock_crawl,
        patch("wiki_cite.cli.classify_categories") as mock_classify,
        patch("wiki_cite.cli.write_expansion_file") as mock_write,
    ):
        mock_picker_cls.return_value = Mock(site="fake-site")
        mock_crawl.return_value = ["Topic", "Topic Task Force", "Alpha"]
        mock_classify.return_value = ["Alpha", "Topic"]
        mock_write.return_value = fake_path

        args = argparse.Namespace(root="Category:Topic", max_depth=3, batch_size=20)
        cmd_discover_categories(args)

        mock_crawl.assert_called_once_with("fake-site", "Category:Topic", max_depth=3)
        mock_classify.assert_called_once_with(["Topic", "Topic Task Force", "Alpha"], batch_size=20)
        mock_write.assert_called_once_with("Category:Topic", ["Alpha", "Topic"], max_depth=3)

    out = capsys.readouterr().out
    assert "Crawling subcategories under 'Category:Topic'" in out
    assert "Discovered 3 categories" in out
    assert f"Wrote 2 accepted categories to {fake_path}" in out
