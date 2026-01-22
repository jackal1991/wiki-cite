"""
Article Picker component for selecting Wikipedia articles to clean up.
"""

import re
from datetime import datetime
from typing import Iterator

import mwclient
import mwparserfromhell

from wiki_cite.config import get_config
from wiki_cite.models import CandidateArticle


class ArticlePicker:
    """Picks Wikipedia articles that need citation cleanup."""

    def __init__(self, site: mwclient.Site | None = None):
        """Initialize the article picker.

        Args:
            site: mwclient Site object. If None, creates a new connection to en.wikipedia.org
        """
        self.config = get_config()
        self.site = site or mwclient.Site("en.wikipedia.org")

    def is_blp(self, page_text: str, categories: list[str]) -> bool:
        """Check if an article is a Biography of Living Person.

        Args:
            page_text: The wikitext of the article
            categories: List of categories the article belongs to

        Returns:
            True if this appears to be a BLP article
        """
        # Check categories
        blp_categories = [
            "living people",
            "year of birth missing (living people)",
            "possibly living people",
        ]

        for cat in categories:
            cat_lower = cat.lower()
            if any(blp_cat in cat_lower for blp_cat in blp_categories):
                return True

        # Check for BLP-related templates
        blp_templates = ["blp", "living", "bio-living"]
        wikicode = mwparserfromhell.parse(page_text)
        for template in wikicode.filter_templates():
            template_name = template.name.strip().lower()
            if any(blp_temp in template_name for blp_temp in blp_templates):
                return True

        return False

    def count_body_lines(self, page_text: str) -> int:
        """Count the number of lines of body text in an article.

        Excludes infoboxes, categories, templates, references sections, etc.

        Args:
            page_text: The wikitext of the article

        Returns:
            Number of lines of actual body text
        """
        wikicode = mwparserfromhell.parse(page_text)

        # Remove templates (including infoboxes)
        for template in wikicode.filter_templates():
            try:
                wikicode.remove(template)
            except ValueError:
                pass

        # Remove references section
        text = str(wikicode)
        text = re.sub(r"==\s*References\s*==.*", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"==\s*External links\s*==.*", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"==\s*See also\s*==.*", "", text, flags=re.DOTALL | re.IGNORECASE)

        # Remove categories
        text = re.sub(r"\[\[Category:.*?\]\]", "", text, flags=re.IGNORECASE)

        # Remove reference tags
        text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<ref[^>]*/>", "", text, flags=re.IGNORECASE)

        # Remove empty lines and count
        lines = [
            line.strip()
            for line in text.split("\n")
            if line.strip() and not line.strip().startswith("==")
        ]

        return len(lines)

    def is_protected(self, page) -> bool:
        """Check if a page is protected from editing.

        Args:
            page: mwclient Page object

        Returns:
            True if page is protected
        """
        try:
            protection = page.protection
            # Check for any form of protection
            return bool(protection.get("edit") or protection.get("move"))
        except Exception:
            # If we can't determine protection status, assume it's protected to be safe
            return True

    def get_categories(self, page) -> list[str]:
        """Get the categories an article belongs to.

        Args:
            page: mwclient Page object

        Returns:
            List of category names (without "Category:" prefix)
        """
        try:
            return [cat.name.replace("Category:", "") for cat in page.categories()]
        except Exception:
            return []

    def is_candidate(self, page) -> tuple[bool, str]:
        """Check if a page is a candidate for cleanup.

        Args:
            page: mwclient Page object

        Returns:
            Tuple of (is_candidate, reason_if_not)
        """
        # Skip redirects
        if page.redirect:
            return False, "redirect"

        # Check protection
        if self.config.article_selection.exclude_protected and self.is_protected(page):
            return False, "protected"

        # Get page text and categories
        try:
            page_text = page.text()
        except Exception as e:
            return False, f"error reading page: {e}"

        if not page_text:
            return False, "empty page"

        categories = self.get_categories(page)

        # Check if BLP
        if self.config.article_selection.exclude_blp and self.is_blp(page_text, categories):
            return False, "BLP article"

        # Count body lines
        body_lines = self.count_body_lines(page_text)
        if body_lines > self.config.article_selection.max_body_lines:
            return False, f"too long ({body_lines} lines)"

        if body_lines == 0:
            return False, "no body text"

        return True, ""

    def fetch_candidates(self, limit: int = 100) -> Iterator[CandidateArticle]:
        """Fetch candidate articles from Wikipedia.

        Args:
            limit: Maximum number of candidates to fetch

        Yields:
            CandidateArticle objects
        """
        # Get articles from the category
        category = self.config.article_selection.category
        category = category.replace("Category:", "")

        try:
            cat_page = self.site.pages[f"Category:{category}"]
        except Exception as e:
            print(f"Error accessing category {category}: {e}")
            return

        count = 0
        for page in cat_page:
            if count >= limit:
                break

            # Check if this is a candidate
            is_candidate, _ = self.is_candidate(page)
            if not is_candidate:
                continue

            # Get article data
            try:
                page_text = page.text()
                categories = self.get_categories(page)
                body_lines = self.count_body_lines(page_text)

                # Check for infobox
                wikicode = mwparserfromhell.parse(page_text)
                has_infobox = any(
                    "infobox" in str(t.name).lower() for t in wikicode.filter_templates()
                )

                candidate = CandidateArticle(
                    title=page.name,
                    url=f"https://en.wikipedia.org/wiki/{page.name.replace(' ', '_')}",
                    wikitext=page_text,
                    body_line_count=body_lines,
                    revision_id=str(page.revision),
                    is_blp=self.is_blp(page_text, categories),
                    categories=categories,
                    has_infobox=has_infobox,
                    fetched_at=datetime.now(),
                )

                yield candidate
                count += 1

            except Exception as e:
                print(f"Error processing page {page.name}: {e}")
                continue
