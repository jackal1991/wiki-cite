"""
Command-line interface for the Wikipedia Citation & Cleanup Tool.
"""

import argparse
import sys

from wiki_cite.agent import ClaudeAgent
from wiki_cite.article_picker import ArticlePicker
from wiki_cite.config import get_config
from wiki_cite.models import Article
from wiki_cite.web_app import create_app


def cmd_fetch_articles(args):
    """Fetch candidate articles."""
    picker = ArticlePicker()

    print(f"Fetching up to {args.limit} candidate articles...")

    count = 0
    for candidate in picker.fetch_candidates(limit=args.limit):
        count += 1
        print(f"\n{count}. {candidate.title}")
        print(f"   URL: {candidate.url}")
        print(f"   Body lines: {candidate.body_line_count}")
        print(f"   Categories: {', '.join(candidate.categories[:3])}")

    print(f"\nFound {count} candidate articles.")


def cmd_analyze_article(args):
    """Analyze a specific article."""
    import mwclient

    site = mwclient.Site("en.wikipedia.org")

    try:
        page = site.pages[args.title]
        page_text = page.text()

        article = Article(
            title=page.name,
            url=f"https://en.wikipedia.org/wiki/{page.name.replace(' ', '_')}",
            wikitext=page_text,
            revision_id=str(page.revision),
        )

        print(f"Analyzing article: {article.title}")
        print("This may take a moment...\n")

        agent = ClaudeAgent()
        proposal = agent.analyze_article(article)

        print(f"Analysis complete! Found {len(proposal.edits)} proposed edits:\n")

        for i, edit in enumerate(proposal.edits, 1):
            print(f"{i}. [{edit.edit_type.value.upper()}] ({edit.confidence} confidence)")
            print(f"   Original: {edit.original_text[:80]}...")
            print(f"   Proposed: {edit.proposed_text[:80]}...")
            print(f"   Rationale: {edit.rationale}")
            if edit.policy_reference:
                print(f"   Policy: {edit.policy_reference}")
            print()

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_web(args):
    """Start the web interface."""
    print("Starting web interface...")
    print(f"Open your browser to: http://localhost:{args.port}")
    print("Press Ctrl+C to stop.")

    app = create_app()
    app.run(debug=args.debug, host=args.host, port=args.port)


def cmd_config(args):
    """Show current configuration."""
    config = get_config()

    print("Current Configuration:")
    print("\nAgent:")
    print(f"  Model: {config.agent.model}")
    print(f"  Max edits per article: {config.agent.max_edits_per_article}")

    print("\nGuardrails:")
    print(f"  Max new words: {config.guardrails.max_new_words}")
    print(f"  Max content removal: {config.guardrails.max_content_removal_pct}%")
    print(f"  Min similarity ratio: {config.guardrails.min_similarity_ratio}")
    print(f"  Skip BLP articles: {config.guardrails.skip_blp_articles}")

    print("\nSources:")
    print(f"  Search APIs: {', '.join(config.sources.search_apis)}")
    print(f"  Reliability check: {config.sources.reliability_check}")

    print("\nWikipedia:")
    print(f"  Rate limit: {config.wikipedia.rate_limit_edits_per_hour} edits/hour")

    print("\nArticle Selection:")
    print(f"  Category: {config.article_selection.category}")
    print(f"  Max body lines: {config.article_selection.max_body_lines}")
    print(f"  Exclude BLP: {config.article_selection.exclude_blp}")
    print(f"  Exclude protected: {config.article_selection.exclude_protected}")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Wikipedia Citation & Cleanup Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Fetch articles command
    fetch_parser = subparsers.add_parser(
        "fetch",
        help="Fetch candidate articles for cleanup"
    )
    fetch_parser.add_argument(
        "-l", "--limit",
        type=int,
        default=10,
        help="Maximum number of articles to fetch (default: 10)"
    )
    fetch_parser.set_defaults(func=cmd_fetch_articles)

    # Analyze article command
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Analyze a specific article"
    )
    analyze_parser.add_argument(
        "title",
        help="Title of the Wikipedia article to analyze"
    )
    analyze_parser.set_defaults(func=cmd_analyze_article)

    # Web interface command
    web_parser = subparsers.add_parser(
        "web",
        help="Start the web interface"
    )
    web_parser.add_argument(
        "-p", "--port",
        type=int,
        default=5000,
        help="Port to run on (default: 5000)"
    )
    web_parser.add_argument(
        "-H", "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)"
    )
    web_parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Enable debug mode"
    )
    web_parser.set_defaults(func=cmd_web)

    # Config command
    config_parser = subparsers.add_parser(
        "config",
        help="Show current configuration"
    )
    config_parser.set_defaults(func=cmd_config)

    # Parse arguments
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Run command
    args.func(args)


if __name__ == "__main__":
    main()
