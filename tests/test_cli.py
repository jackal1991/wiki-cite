"""Tests for the `wiki-cite stats` command."""

import argparse

from wiki_cite.cli import cmd_stats
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
