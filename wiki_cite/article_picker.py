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

# Prefixes for wikitext blocks that are not prose (templates, tables, media,
# categories, headings) — skipped when locating the lead paragraph.
_NON_PROSE_PREFIXES = ("{{", "{|", "|", "!", "==", "[[category", "[[file", "[[image", "#redirect", "<!--")


def _lead_block(blocks: list[str]) -> str:
    """Return the first prose block (the lead paragraph), or "" if none."""
    for block in blocks:
        if not block.lstrip().lower().startswith(_NON_PROSE_PREFIXES):
            return block
    return ""


def _preceding_heading(blocks: list[str], index: int) -> str:
    """Return the nearest section heading (== … ==) block above ``index``, or ""."""
    for block in reversed(blocks[:index]):
        if block.lstrip().startswith("=="):
            return block
    return ""


def build_focused_excerpt(wikitext: str, max_chars: int = 6000) -> str:
    """Reduce an article to just what Claude needs to source flagged claims:
    the lead paragraph plus each flagged section (its heading and the paragraph
    containing the {{Citation needed}} tag).

    Sending this excerpt instead of the whole article keeps the prompt small and
    focused. Edits still apply to the full article because they are matched by
    verbatim ``original_text`` (see ``ClaudeAgent.apply_edits``). Falls back to the
    (truncated) full text when no structure can be found.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", wikitext) if b.strip()]
    if not blocks:
        return wikitext.strip()[:max_chars]

    selected: list[str] = []
    lead = _lead_block(blocks)
    if lead:
        selected.append(lead)
    for index, block in enumerate(blocks):
        if not CITATION_NEEDED_RE.search(block):
            continue
        heading = _preceding_heading(blocks, index)
        for piece in ([heading] if heading else []) + [block]:
            if piece and piece not in selected:
                selected.append(piece)

    if not selected:
        return wikitext.strip()[:max_chars]

    return "\n\n[…]\n\n".join(selected)[:max_chars]


class ArticlePicker:
    """Picks Wikipedia articles that need citation cleanup."""

    def __init__(self, site: mwclient.Site | None = None, seen_store=None):
        """Initialize the article picker.

        Args:
            site: mwclient Site object. If None, creates a new connection to en.wikipedia.org
            seen_store: optional SeenStore; already-processed titles are skipped so
                fetching progresses through the category instead of restarting at the top.
        """
        self.config = get_config()
        self.site = site or mwclient.Site("en.wikipedia.org")
        self.seen_store = seen_store

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

    @staticmethod
    def _normalize_category(name: str) -> str:
        """Normalize a category name for comparison: drop the ``Category:`` prefix,
        convert underscores to spaces, and casefold."""
        return name.split(":", 1)[-1].replace("_", " ").strip().casefold()

    @staticmethod
    def category_filter(
        categories: list[str],
        include: list[str],
        exclude: list[str],
    ) -> tuple[bool, str]:
        """Decide whether an article's categories pass the include/exclude filter.

        Exclude takes precedence: any overlap with ``exclude`` rejects regardless of
        ``include``. A non-empty ``include`` then requires overlap to pass. Empty
        lists are no-ops (matches today's behavior).
        """
        article = {ArticlePicker._normalize_category(c) for c in categories}
        excluded = {ArticlePicker._normalize_category(c) for c in exclude}
        hit = article & excluded
        if hit:
            return False, f"excluded category: {sorted(hit)[0]}"
        if include:
            included = {ArticlePicker._normalize_category(c) for c in include}
            if not (article & included):
                return False, "not in included categories"
        return True, ""

    def is_candidate(
        self,
        page,
        include_categories: list[str] | None = None,
        exclude_categories: list[str] | None = None,
    ) -> tuple[bool, str]:
        """Check if a page is a candidate for cleanup.

        Args:
            page: mwclient Page object
            include_categories: optional override for the configured include list
                (``None`` means "use config"; pass ``[]`` to explicitly disable).
            exclude_categories: optional override for the configured exclude list.

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

        # Cost guard: don't spend a Claude call on a very long article.
        max_chars = self.config.article_selection.max_wikitext_chars
        if max_chars and len(page_text) > max_chars:
            return False, f"too long to analyze ({len(page_text)} chars)"

        categories = self.get_categories(page)

        include = include_categories if include_categories is not None else self.config.article_selection.include_categories
        exclude = exclude_categories if exclude_categories is not None else self.config.article_selection.exclude_categories
        ok, reason = self.category_filter(categories, include, exclude)
        if not ok:
            return False, reason

        # Check if BLP
        if self.config.article_selection.exclude_blp and self.is_blp(page_text, categories):
            return False, "BLP article"

        # Require at least one inline {{Citation needed}} claim to source.
        if not self.extract_citation_needed_claims(page_text):
            return False, "no citation-needed tag"

        return True, ""

    def _build_candidate(self, page) -> CandidateArticle:
        """Build a CandidateArticle from an mwclient page already known to be a candidate."""
        page_text = page.text()
        categories = self.get_categories(page)
        body_lines = self.count_body_lines(page_text)

        # Check for infobox
        wikicode = mwparserfromhell.parse(page_text)
        has_infobox = any("infobox" in str(t.name).lower() for t in wikicode.filter_templates())

        return CandidateArticle(
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

    def fetch_candidates(
        self,
        limit: int = 100,
        include_categories: list[str] | None = None,
        exclude_categories: list[str] | None = None,
    ) -> Iterator[CandidateArticle]:
        """Fetch candidate articles from Wikipedia.

        Reads a look-ahead pool of candidates (size >= limit, cheap title/text
        checks only — no Claude calls) so a future ranking pass can reorder the
        pool by learned success rate before truncating to `limit`. With no active
        scorer, pool order is identical to today's plain category order.

        Args:
            limit: Maximum number of candidates to fetch
            include_categories: optional override for the configured include list
                (``None`` means "use config"; pass ``[]`` to explicitly disable).
            exclude_categories: optional override for the configured exclude list.

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

        pool_size = max(self.config.article_selection.candidate_pool_size, limit)
        pool: list[CandidateArticle] = []
        for page in cat_page:
            if len(pool) >= pool_size:
                break

            # Skip already-processed articles first — a cheap title lookup, no
            # page fetch — so fetching progresses instead of restarting at the top.
            if self.seen_store is not None and self.seen_store.is_seen(page.name):
                continue

            # Check if this is a candidate
            is_candidate, _ = self.is_candidate(
                page,
                include_categories=include_categories,
                exclude_categories=exclude_categories,
            )
            if not is_candidate:
                continue

            try:
                pool.append(self._build_candidate(page))
            except Exception as e:
                print(f"Error processing page {page.name}: {e}")
                continue

        # Phase 4: no scorer yet -> identity order (Phase 5 sorts this pool).
        yield from pool[:limit]
