"""
Article Picker component for selecting Wikipedia articles to clean up.
"""

import re
from datetime import datetime
from collections.abc import Iterator

import mwclient
import mwparserfromhell

from wiki_cite.config import get_config
from wiki_cite.models import CandidateArticle

# Matches inline {{Citation needed}} tags and its common redirects ({{cn}}, {{fact}}).
# These are the signal Citation Hunt surfaces: a specific claim needing a source.
CITATION_NEEDED_RE = re.compile(
    r"\{\{\s*(?:citation[ _-]needed|cn|fact|cite[ _-]needed)\b[^{}]*\}\}",
    re.IGNORECASE,
)


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
        lines = [line.strip() for line in text.split("\n") if line.strip() and not line.strip().startswith("==")]

        return len(lines)

    def extract_citation_needed_claims(self, wikitext: str) -> list[str]:
        """Extract the claims tagged with {{Citation needed}} (or {{cn}}/{{fact}}).

        For each inline citation-needed tag, returns the sentence immediately
        preceding it — the specific unsourced claim a reviewer would source.
        This mirrors what the Citation Hunt tool surfaces.
        """
        claims: list[str] = []
        seen: set[str] = set()
        for match in CITATION_NEEDED_RE.finditer(wikitext):
            claim = self._trailing_sentence(wikitext[: match.start()])
            if claim and claim not in seen:
                seen.add(claim)
                claims.append(claim)
        return claims

    @staticmethod
    def _trailing_sentence(text: str) -> str:
        """Return the last clean sentence of a wikitext fragment (or "")."""
        text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<ref[^>]*/>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)
        text = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", text)
        text = re.sub(r"'{2,}", "", text)
        text = re.sub(r"==+[^=]+=+", "", text)

        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]
        if not parts:
            return ""
        claim = re.sub(r"\s+", " ", parts[-1].strip().strip("*#: ")).strip()
        return claim if len(claim) > 20 else ""

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

        # Only main-namespace articles (namespace 0) — skip Category:/Template:/etc.
        if getattr(page, "namespace", 0) != 0:
            return False, "not an article namespace"

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

        # Require at least one inline {{Citation needed}} claim to source.
        if not self.extract_citation_needed_claims(page_text):
            return False, "no citation-needed tag"

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
                has_infobox = any("infobox" in str(t.name).lower() for t in wikicode.filter_templates())

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
                    citation_needed_claims=self.extract_citation_needed_claims(page_text),
                )

                yield candidate
                count += 1

            except Exception as e:
                print(f"Error processing page {page.name}: {e}")
                continue
