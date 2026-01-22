"""
Configuration management for the Wikipedia Citation & Cleanup Tool.
"""

from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings


class AgentConfig(BaseSettings):
    """Configuration for the Claude agent."""

    model: str = "claude-sonnet-4-20250514"
    max_edits_per_article: int = 15


class GuardrailsConfig(BaseSettings):
    """Configuration for edit guardrails."""

    max_new_words: int = 50
    max_content_removal_pct: int = 20
    min_similarity_ratio: float = 0.85
    skip_blp_articles: bool = True


class SourcesConfig(BaseSettings):
    """Configuration for source finding."""

    search_apis: list[str] = Field(
        default_factory=lambda: ["semantic_scholar", "crossref", "google_scholar"]
    )
    reliability_check: bool = True


class WikipediaConfig(BaseSettings):
    """Configuration for Wikipedia API interactions."""

    edit_summary_suffix: str = "(AI-assisted citation/cleanup, human-reviewed)"
    rate_limit_edits_per_hour: int = 10
    user_agent: str = (
        "WikiCiteBot/1.0 (https://github.com/yourorg/wiki-cite; citation-cleanup-assistant)"
    )


class ArticleSelectionConfig(BaseSettings):
    """Configuration for article selection criteria."""

    category: str = "Category:Articles_lacking_sources"
    max_body_lines: int = 4
    exclude_blp: bool = True
    exclude_protected: bool = True


class Config(BaseSettings):
    """Main configuration class."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    agent: AgentConfig = Field(default_factory=AgentConfig)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    wikipedia: WikipediaConfig = Field(default_factory=WikipediaConfig)
    article_selection: ArticleSelectionConfig = Field(default_factory=ArticleSelectionConfig)

    # API Keys from environment
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    wikipedia_username: str = Field(default="", alias="WIKIPEDIA_USERNAME")
    wikipedia_password: str = Field(default="", alias="WIKIPEDIA_PASSWORD")
    semantic_scholar_api_key: str = Field(default="", alias="SEMANTIC_SCHOLAR_API_KEY")
    crossref_email: str = Field(default="", alias="CROSSREF_EMAIL")
    flask_secret_key: str = Field(default="dev-secret-key", alias="FLASK_SECRET_KEY")

    @classmethod
    def load(cls, config_path: str | Path = "config.yaml") -> "Config":
        """Load configuration from a YAML file and environment variables."""
        config_path = Path(config_path)

        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                yaml_config = yaml.safe_load(f)
        else:
            yaml_config = {}

        # Create nested config objects
        config_data = {}
        if "agent" in yaml_config:
            config_data["agent"] = AgentConfig(**yaml_config["agent"])
        if "guardrails" in yaml_config:
            config_data["guardrails"] = GuardrailsConfig(**yaml_config["guardrails"])
        if "sources" in yaml_config:
            config_data["sources"] = SourcesConfig(**yaml_config["sources"])
        if "wikipedia" in yaml_config:
            config_data["wikipedia"] = WikipediaConfig(**yaml_config["wikipedia"])
        if "article_selection" in yaml_config:
            config_data["article_selection"] = ArticleSelectionConfig(
                **yaml_config["article_selection"]
            )

        return cls(**config_data)


# Global config instance
_config: Config | None = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def set_config(config: Config) -> None:
    """Set the global configuration instance."""
    global _config
    _config = config
