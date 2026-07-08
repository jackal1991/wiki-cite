"""Tests for configuration management."""

from pathlib import Path
import tempfile

import pytest
from pydantic import ValidationError

from wiki_cite.config import (
    Config,
    AgentConfig,
    GuardrailsConfig,
    WikipediaConfig,
    ArticleSelectionConfig,
    FeedbackConfig,
)


def test_agent_config_defaults():
    """Test agent configuration defaults."""
    config = AgentConfig()
    assert config.model == "claude-sonnet-5"
    assert config.max_edits_per_article == 15
    assert config.max_search_turns == 5
    assert config.search_results_per_query == 3


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
    assert "unsourced_statements" in config.category
    assert config.max_body_lines == 4
    assert config.exclude_blp is True
    assert config.exclude_protected is True


def test_article_selection_category_lists_default_empty():
    """Test include/exclude category lists default to empty (no-op filtering)."""
    config = ArticleSelectionConfig()
    assert config.include_categories == []
    assert config.exclude_categories == []


def test_candidate_pool_size_default():
    assert ArticleSelectionConfig().candidate_pool_size == 30


def test_feedback_config_defaults():
    config = FeedbackConfig()
    assert config.enabled is True
    assert config.epsilon == 0.15
    assert config.min_samples == 5


def test_config_load_from_yaml():
    """Test loading configuration from YAML file."""
    yaml_content = """
agent:
  model: "test-model"
  max_edits_per_article: 20
  max_search_turns: 7
  search_results_per_query: 4

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
            assert config.agent.max_search_turns == 7
            assert config.agent.search_results_per_query == 4
            assert config.guardrails.max_new_words == 100
            assert config.guardrails.min_similarity_ratio == 0.9
        finally:
            Path(f.name).unlink()


def test_config_load_feedback_block():
    """Test loading a `feedback:` block from YAML."""
    yaml_content = """
feedback:
  enabled: false
  epsilon: 0.3
  min_samples: 10
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()

        try:
            config = Config.load(f.name)

            assert config.feedback.enabled is False
            assert config.feedback.epsilon == 0.3
            assert config.feedback.min_samples == 10
        finally:
            Path(f.name).unlink()


def test_config_load_nonexistent_file():
    """Test loading configuration when file doesn't exist."""
    config = Config.load("nonexistent.yaml")

    # Should use defaults
    assert config.agent.model == "claude-sonnet-5"
    assert config.guardrails.max_new_words == 50


def test_config_with_environment_variables(monkeypatch):
    """Test configuration with environment variables."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key-123")
    monkeypatch.setenv("WIKIPEDIA_USERNAME", "testuser")

    config = Config.load("nonexistent.yaml")

    assert config.anthropic_api_key == "test-api-key-123"
    assert config.wikipedia_username == "testuser"


def test_config_load_category_lists_from_yaml():
    """Test include/exclude category lists load from YAML into ArticleSelectionConfig."""
    yaml_content = """
article_selection:
  include_categories: [History]
  exclude_categories: [Sports]
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()

        try:
            config = Config.load(f.name)

            assert config.article_selection.include_categories == ["History"]
            assert config.article_selection.exclude_categories == ["Sports"]
        finally:
            Path(f.name).unlink()


def test_article_selection_non_list_categories_rejected():
    """Test a scalar value for include_categories raises a ValidationError."""
    yaml_content = """
article_selection:
  include_categories: "History"
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()

        try:
            with pytest.raises(ValidationError):
                Config.load(f.name)
        finally:
            Path(f.name).unlink()
