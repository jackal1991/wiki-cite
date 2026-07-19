"""
Command-line interface for the Wikipedia Citation & Cleanup Tool.
"""

import argparse
import json
import logging
import sys
from logging.handlers import RotatingFileHandler

from wiki_cite.agent import ClaudeAgent
from wiki_cite.article_picker import ArticlePicker, crawl_subcategories
from wiki_cite.category_discovery import classify_categories, write_expansion_file
from wiki_cite.config import get_config
from wiki_cite.models import Article
from wiki_cite.seen_store import SeenStore
from wiki_cite.stats import STATS_DIMENSIONS
from wiki_cite.web_app import create_app


def _configure_logging(log_file: str) -> None:
    """Route wiki_cite's warnings/errors (rate-limit responses, store failures,
    etc.) to both the console and a rotating file, so they're visible no matter
    how the process was launched."""
    package_logger = logging.getLogger("wiki_cite")
    package_logger.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3)
    file_handler.setFormatter(formatter)
    package_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    package_logger.addHandler(console_handler)


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
            citation_needed_claims=ArticlePicker(site=site).extract_citation_needed_claims(page_text),
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
    # threaded=True: without it, Werkzeug's dev server handles one request at a
    # time, so a single slow request (e.g. one stuck in a Claude/search-API call
    # inside the SSE fetch stream) blocks every other request, including new ones.
    app.run(debug=args.debug, host=args.host, port=args.port, threaded=True)  # pylint: disable=no-member


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

    print("\nRevert Tracking:")
    print(f"  Check horizon (days): {config.revert_tracking.check_horizon_days}")


def cmd_discover_categories(args):
    """Crawl a category's subcategory tree and write a static expansion file."""
    picker = ArticlePicker()

    print(f"Crawling subcategories under {args.root!r}...")
    raw = crawl_subcategories(picker.site, args.root, max_depth=args.max_depth)

    print(f"Discovered {len(raw)} categories; classifying...")
    accepted = classify_categories(raw, batch_size=args.batch_size)

    path = write_expansion_file(args.root, accepted, max_depth=args.max_depth)
    written = json.loads(path.read_text(encoding="utf-8"))["categories"]
    print(f"Wrote {len(written)} accepted categories to {path}")


def cmd_stats(args):
    """Show approval/success rates by dimension, from the recorded outcomes."""
    store = SeenStore(get_config().seen_db_path)

    for dimension in STATS_DIMENSIONS:
        print(f"\n{dimension}:")
        rates = store.dimension_rates(dimension)
        shown = False
        for value, (successes, total) in sorted(rates.items()):
            if total == 0:
                continue  # never show a rate with n=0
            print(f"  {value:<30} {successes}/{total} ({successes / total:.0%})")
            shown = True
        if not shown:
            print("  (no data)")


def cmd_check_reverts(args):
    """Check pushed articles for reverts within the configured horizon."""
    import mwclient

    from wiki_cite.revert_checker import check_pending_reverts

    config = get_config()
    store = SeenStore(config.seen_db_path)
    site = mwclient.Site("en.wikipedia.org")

    horizon = config.revert_tracking.check_horizon_days
    summary = check_pending_reverts(site, store, horizon)

    print(f"Checked {summary.checked} pushed article(s) within the {horizon}-day horizon.")
    print(f"Reverts found: {summary.reverts_found}")
    if summary.failures:
        print(f"\n{len(summary.failures)} article(s) could not be checked:")
        for title, error in summary.failures:
            print(f"  {title}: {error}")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Wikipedia Citation & Cleanup Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Fetch articles command
    fetch_parser = subparsers.add_parser("fetch", help="Fetch candidate articles for cleanup")
    fetch_parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=10,
        help="Maximum number of articles to fetch (default: 10)",
    )
    fetch_parser.set_defaults(func=cmd_fetch_articles)

    # Analyze article command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze a specific article")
    analyze_parser.add_argument("title", help="Title of the Wikipedia article to analyze")
    analyze_parser.set_defaults(func=cmd_analyze_article)

    # Web interface command
    web_parser = subparsers.add_parser("web", help="Start the web interface")
    web_parser.add_argument("-p", "--port", type=int, default=5000, help="Port to run on (default: 5000)")
    web_parser.add_argument("-H", "--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    web_parser.add_argument("-d", "--debug", action="store_true", help="Enable debug mode")
    web_parser.set_defaults(func=cmd_web)

    # Config command
    config_parser = subparsers.add_parser("config", help="Show current configuration")
    config_parser.set_defaults(func=cmd_config)

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show approval/success rates by dimension")
    stats_parser.set_defaults(func=cmd_stats)

    # Check reverts command
    check_reverts_parser = subparsers.add_parser("check-reverts", help="Check pushed articles for reverts within the horizon")
    check_reverts_parser.set_defaults(func=cmd_check_reverts)

    # Discover categories command
    discover_parser = subparsers.add_parser(
        "discover-categories",
        help="Crawl a category's subcategory tree and write a static expansion file",
    )
    discover_parser.add_argument("root", help="Root category name (with or without Category: prefix)")
    discover_parser.add_argument("--max-depth", type=int, default=None, help="BFS depth cap (default: unbounded)")
    discover_parser.add_argument("--batch-size", type=int, default=20, help="Category names per Anthropic classification call")
    discover_parser.set_defaults(func=cmd_discover_categories)

    # Parse arguments
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    _configure_logging(get_config().log_file)

    # Run command
    args.func(args)


if __name__ == "__main__":
    main()
