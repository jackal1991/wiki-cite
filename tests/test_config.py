"""Tests for configuration management."""

import pytest
from pathlib import Path
import tempfile

from wiki_cite.config import (
    Config,
    AgentConfig,
    GuardrailsConfig,
    WikipediaConfig,
    ArticleSelectionConfig,
)


def test_agent_config_defaults():
    """Test agent configuration defaults."""
    config = AgentConfig()
    assert config.model == "claude-sonnet-4-20250514"
    assert config.max_edits_per_article == 15


def test_guardrails_config_defaults():
    """Test guardrails configuration defaults."""
    config = GuardrailsConfig()
    assert config.max_new_words == 50
    assert config.max_content_removal_pct == 20
    assert config.min_similarity_ratio == 0.85
    assert config.skip_blp_articles is True


def test_wikipedia_config_defaults():
    """Test Wikipedia configuration defaults."""
    config = WikipediaConfig()
    assert "AI-assisted" in config.edit_summary_suffix
    assert config.rate_limit_edits_per_hour == 10


def test_article_selection_config_defaults():
    """Test article selection configuration defaults."""
    config = ArticleSelectionConfig()
    assert "Articles_lacking_sources" in config.category
    assert config.max_body_lines == 4
    assert config.exclude_blp is True
    assert config.exclude_protected is True


def test_config_load_from_yaml():
    """Test loading configuration from YAML file."""
    yaml_content = """
agent:
  model: "test-model"
  max_edits_per_article: 20

guardrails:
  max_new_words: 100
  min_similarity_ratio: 0.9
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()

        try:
            config = Config.load(f.name)

            assert config.agent.model == "test-model"
            assert config.agent.max_edits_per_article == 20
            assert config.guardrails.max_new_words == 100
            assert config.guardrails.min_similarity_ratio == 0.9
        finally:
            Path(f.name).unlink()


def test_config_load_nonexistent_file():
    """Test loading configuration when file doesn't exist."""
    config = Config.load("nonexistent.yaml")

    # Should use defaults
    assert config.agent.model == "claude-sonnet-4-20250514"
    assert config.guardrails.max_new_words == 50


def test_config_with_environment_variables(monkeypatch):
    """Test configuration with environment variables."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key-123")
    monkeypatch.setenv("WIKIPEDIA_USERNAME", "testuser")

    config = Config.load("nonexistent.yaml")

    assert config.anthropic_api_key == "test-api-key-123"
    assert config.wikipedia_username == "testuser"
