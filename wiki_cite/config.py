"""
Configuration management for the Wikipedia Citation & Cleanup Tool.
"""

from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings


class AgentConfig(BaseSettings):
    """Configuration for the Claude agent."""

    model: str = "claude-sonnet-5"
    max_edits_per_article: int = 15
    # Cost guard: each candidate scanned costs one Claude analysis call, so this
    # caps the worst-case number of model calls per "Fetch new article" click.
    max_candidates_per_fetch: int = 8
    # Cost guard: max tool-executing model calls per article in the agentic
    # search loop. Once hit, the loop forces a final decision call with only
    # the terminal `propose_edits` tool available.
    max_search_turns: int = 5
    # Cap on results returned per search tool call, to keep tool_result
    # payloads (and therefore token spend) small.
    search_results_per_query: int = 3


class GuardrailsConfig(BaseSettings):
    """Configuration for edit guardrails."""

    max_new_words: int = 50
    max_content_removal_pct: int = 20
    min_similarity_ratio: float = 0.85
    skip_blp_articles: bool = True


class SourcesConfig(BaseSettings):
    """Configuration for source finding."""

    search_apis: list[str] = Field(default_factory=lambda: ["semantic_scholar", "crossref", "web_search"])
    reliability_check: bool = True


class WikipediaConfig(BaseSettings):
    """Configuration for Wikipedia API interactions."""

    edit_summary_suffix: str = "(AI-assisted citation/cleanup, human-reviewed)"
    rate_limit_edits_per_hour: int = 10
    user_agent: str = "WikiCiteBot/1.0 (https://github.com/yourorg/wiki-cite; citation-cleanup-assistant)"


class ArticleSelectionConfig(BaseSettings):
    """Configuration for article selection criteria."""

    category: str = "Category:All_articles_with_unsourced_statements"
    max_body_lines: int = 4
    exclude_blp: bool = True
    exclude_protected: bool = True
    # Cost guard: skip articles whose wikitext exceeds this many characters, so a
    # single analysis never sends a huge (expensive) prompt to Claude. 0 disables.
    max_wikitext_chars: int = 12000
    include_categories: list[str] = Field(default_factory=list)
    exclude_categories: list[str] = Field(default_factory=list)
    # How many candidates to look ahead & rank before yielding `limit` (must be >= limit).
    candidate_pool_size: int = 30


class FeedbackConfig(BaseSettings):
    """Configuration for the outcomes-feedback loop that re-ranks candidates."""

    enabled: bool = True
    epsilon: float = 0.15
    min_samples: int = 5


class Config(BaseSettings):
    """Main configuration class."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    agent: AgentConfig = Field(default_factory=AgentConfig)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    wikipedia: WikipediaConfig = Field(default_factory=WikipediaConfig)
    article_selection: ArticleSelectionConfig = Field(default_factory=ArticleSelectionConfig)
    feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)

    # API Keys from environment
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    wikipedia_username: str = Field(default="", alias="WIKIPEDIA_USERNAME")
    wikipedia_password: str = Field(default="", alias="WIKIPEDIA_PASSWORD")
    semantic_scholar_api_key: str = Field(default="", alias="SEMANTIC_SCHOLAR_API_KEY")
    crossref_email: str = Field(default="", alias="CROSSREF_EMAIL")
    brave_api_key: str = Field(default="", alias="BRAVE_API_KEY")
    flask_secret_key: str = Field(default="dev-secret-key", alias="FLASK_SECRET_KEY")
    # SQLite file tracking already-processed articles (keeps fetch idempotent).
    seen_db_path: str = Field(default="wiki_cite_seen.db", alias="SEEN_DB_PATH")

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
            config_data["article_selection"] = ArticleSelectionConfig(**yaml_config["article_selection"])
        if "feedback" in yaml_config:
            config_data["feedback"] = FeedbackConfig(**yaml_config["feedback"])

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
