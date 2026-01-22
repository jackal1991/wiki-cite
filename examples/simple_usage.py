"""
Simple usage example for the Wikipedia Citation & Cleanup Tool.

This script demonstrates basic usage of the tool's components.
"""

import os
from pathlib import Path

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from wiki_cite.agent import ClaudeAgent
from wiki_cite.article_picker import ArticlePicker
from wiki_cite.config import get_config, set_config, Config
from wiki_cite.models import Article


def example_fetch_articles():
    """Example: Fetch candidate articles."""
    print("=" * 60)
    print("Example 1: Fetching Candidate Articles")
    print("=" * 60)

    picker = ArticlePicker()

    print("\nFetching 3 candidate articles...")
    for i, candidate in enumerate(picker.fetch_candidates(limit=3), 1):
        print(f"\n{i}. {candidate.title}")
        print(f"   URL: {candidate.url}")
        print(f"   Body lines: {candidate.body_line_count}")
        print(f"   Categories: {', '.join(candidate.categories[:3])}")


def example_analyze_article():
    """Example: Analyze a specific article."""
    print("\n" + "=" * 60)
    print("Example 2: Analyzing an Article")
    print("=" * 60)

    # Create a simple test article
    article = Article(
        title="Test Article",
        url="https://en.wikipedia.org/wiki/Test_Article",
        wikitext="""
The Test Article is a example article. It was created in 2020.
The article contain several grammar errors and needs citations.
""",
        revision_id="123456",
    )

    print(f"\nAnalyzing: {article.title}")
    print("This requires an Anthropic API key...")

    # Check if API key is set
    config = get_config()
    if not config.anthropic_api_key:
        print("\nNote: Set ANTHROPIC_API_KEY environment variable to run this example.")
        return

    agent = ClaudeAgent()
    proposal = agent.analyze_article(article)

    print(f"\nFound {len(proposal.edits)} proposed edits:\n")

    for i, edit in enumerate(proposal.edits, 1):
        print(f"{i}. [{edit.edit_type.value.upper()}]")
        print(f"   Confidence: {edit.confidence}")
        print(f"   Original: {edit.original_text[:60]}...")
        print(f"   Proposed: {edit.proposed_text[:60]}...")
        print(f"   Rationale: {edit.rationale}")
        print()


def example_configuration():
    """Example: Working with configuration."""
    print("\n" + "=" * 60)
    print("Example 3: Configuration")
    print("=" * 60)

    config = get_config()

    print("\nCurrent configuration:")
    print(f"  Model: {config.agent.model}")
    print(f"  Max edits: {config.agent.max_edits_per_article}")
    print(f"  Skip BLP: {config.guardrails.skip_blp_articles}")
    print(f"  Rate limit: {config.wikipedia.rate_limit_edits_per_hour}/hour")

    print("\nYou can modify config.yaml to change these settings.")


def main():
    """Run all examples."""
    print("\n")
    print("╔" + "═" * 58 + "╗")
    print("║" + " " * 58 + "║")
    print("║" + "  Wikipedia Citation & Cleanup Tool - Examples".center(58) + "║")
    print("║" + " " * 58 + "║")
    print("╚" + "═" * 58 + "╝")

    # Run examples
    try:
        example_configuration()
        # example_fetch_articles()  # Commented out to avoid hitting Wikipedia
        # example_analyze_article()  # Commented out to avoid API usage

        print("\n" + "=" * 60)
        print("Examples complete!")
        print("=" * 60)
        print("\nNote: Some examples are commented out to avoid API usage.")
        print("Uncomment them in the script to try them out.")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
