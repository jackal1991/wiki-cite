"""
Article Picker component for selecting Wikipedia articles to clean up.
"""

import logging
import random
import re
import sqlite3
from collections import deque
from datetime import datetime
from collections.abc import Iterator
from functools import lru_cache

import mwclient
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
import mwparserfromhell

from wiki_cite.category_discovery import load_expansion
from wiki_cite.config import get_config
from wiki_cite.models import CandidateArticle

logger = logging.getLogger(__name__)


def _build_session(user_agent: str) -> requests.Session:
    """A requests.Session for mwclient that backs off and retries on 429/5xx,
    honoring Retry-After when Wikipedia sends one (see mediawiki.org/wiki/API:Etiquette
    — "Making your requests in series rather than in parallel... should result in a
    safe request rate"; this is the reactive complement to staying sequential)."""
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        respect_retry_after_header=True,
        allowed_methods=("GET", "HEAD", "POST"),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def crawl_subcategories(
    site,
    root: str,
    max_depth: int | None = None,
) -> list[str]:
    """Breadth-first walk of the subcategory tree under ``root``.

    Sequential — one Wikipedia request in flight at a time, per API:Etiquette — and
    cycle-safe: MediaWiki categories form a graph (a subcategory can loop back to an
    ancestor), so a ``visited`` set guarantees termination and that each category is
    fetched at most once.

    Args:
        site: an mwclient Site (``ArticlePicker.site``); ``site.pages[...]`` yields a
            Category with ``.members(namespace=14)``.
        root: root category name, with or without the ``Category:`` prefix.
        max_depth: optional BFS depth cap (root is depth 0). ``None`` = unbounded
            (still terminates via the visited set).

    Returns:
        A sorted, de-duplicated list of discovered category names, WITHOUT the
        ``Category:`` prefix, INCLUDING the root itself. No relevance judgment is
        applied here — that is the classification stage's job.
    """
    root_name = root.split(":", 1)[-1] if root.lower().startswith("category:") else root

    results: list[str] = []
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(root_name, 0)])

    while queue:
        name, depth = queue.popleft()
        key = name.replace("_", " ").strip().casefold()
        if key in visited:
            continue
        visited.add(key)
        results.append(name)

        if max_depth is not None and depth >= max_depth:
            continue

        try:
            cat_page = site.pages[f"Category:{name}"]
            members = list(cat_page.members(namespace=14))
        except Exception as e:
            logger.warning("Skipping subcategory branch %r: %s", name, e)
            continue

        for member in members:
            child_name = member.name.split(":", 1)[-1] if member.name.lower().startswith("category:") else member.name
            child_key = child_name.replace("_", " ").strip().casefold()
            if child_key not in visited:
                queue.append((child_name, depth + 1))

    return sorted(results)


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


class CandidateScorer:
    """Turns learned per-dimension outcome rates into a candidate score.

    Pure function of (candidate, rates) — no I/O, so it's unit-testable without
    sqlite or Wikipedia.
    """

    def __init__(self, rates: dict[str, dict[str, tuple[int, int]]], epsilon: float, min_samples: int):
        self._rates = rates  # {dimension: {value: (successes, total)}}
        self._epsilon = epsilon
        self._min_samples = min_samples

    def score(self, candidate: CandidateArticle) -> float:
        """Blend the candidate's known article-level dimensions' success rates.

        A dimension/value with fewer than min_samples observations scores at the
        neutral 0.5 prior (unknown, not bad) so under-sampled candidates aren't
        starved. Independent epsilon-random jitter is added so even well-observed,
        low-rate dimensions occasionally surface ahead of a high-rate one.
        """
        scores = []
        for category in candidate.categories:
            successes, total = self._rates.get("categories", {}).get(category, (0, 0))
            scores.append(successes / total if total >= self._min_samples else 0.5)

        # "True"/"False" — matches dimension_rates' 1->"True"/0->"False" mapping.
        has_infobox_key = str(candidate.has_infobox)
        successes, total = self._rates.get("has_infobox", {}).get(has_infobox_key, (0, 0))
        scores.append(successes / total if total >= self._min_samples else 0.5)

        base = sum(scores) / len(scores) if scores else 0.5
        return base + random.random() * self._epsilon


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
        self.site = site or mwclient.Site(
            "en.wikipedia.org",
            pool=_build_session(self.config.wikipedia.user_agent),
        )
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
    @lru_cache(maxsize=32)
    def _normalized_set(names: tuple[str, ...]) -> frozenset[str]:
        """Cached normalization for a category name tuple. include/exclude lists are
        the same object across every candidate evaluated in one fetch_candidates()
        call (potentially thousands of names, from a discovery-file expansion) — this
        avoids re-normalizing them from scratch on every single page checked."""
        return frozenset(ArticlePicker._normalize_category(c) for c in names)

    @staticmethod
    def _expand_categories(names: list[str]) -> list[str]:
        """For each configured category name, if a discovery file exists for it, replace it
        with that file's discovered set (root + accepted subcats); otherwise keep the name
        as-is (AC4.2 fallback). Returns a deduplicated, order-stable list."""
        expanded: list[str] = []
        seen: set[str] = set()
        for name in names:
            discovered = load_expansion(name)
            for candidate in discovered if discovered is not None else [name]:
                if candidate not in seen:
                    seen.add(candidate)
                    expanded.append(candidate)
        return expanded

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
        article = ArticlePicker._normalized_set(tuple(categories))
        excluded = ArticlePicker._normalized_set(tuple(exclude))
        hit = article & excluded
        if hit:
            return False, f"excluded category: {sorted(hit)[0]}"
        if include:
            included = ArticlePicker._normalized_set(tuple(include))
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
        ok, reason, _, _ = self._evaluate_candidate(page, include_categories, exclude_categories)
        return ok, reason

    def _evaluate_candidate(
        self,
        page,
        include_categories: list[str] | None,
        exclude_categories: list[str] | None,
    ) -> tuple[bool, str, str | None, list[str] | None]:
        """Core is_candidate logic. Also returns the fetched page text/categories
        (or None if rejected before fetching them) so fetch_candidates can reuse
        them in _build_candidate instead of re-fetching from Wikipedia."""
        # Skip redirects
        if page.redirect:
            return False, "redirect", None, None

        # Only main-namespace articles (namespace 0) — skip Category:/Template:/etc.
        if getattr(page, "namespace", 0) != 0:
            return False, "not an article namespace", None, None

        # Check protection
        if self.config.article_selection.exclude_protected and self.is_protected(page):
            return False, "protected", None, None

        # Get page text and categories
        try:
            page_text = page.text()
        except Exception as e:
            return False, f"error reading page: {e}", None, None

        if not page_text:
            return False, "empty page", None, None

        # Cost guard: don't spend a Claude call on a very long article.
        max_chars = self.config.article_selection.max_wikitext_chars
        if max_chars and len(page_text) > max_chars:
            return False, f"too long to analyze ({len(page_text)} chars)", page_text, None

        categories = self.get_categories(page)

        include = include_categories if include_categories is not None else self.config.article_selection.include_categories
        exclude = exclude_categories if exclude_categories is not None else self.config.article_selection.exclude_categories
        ok, reason = self.category_filter(categories, include, exclude)
        if not ok:
            return False, reason, page_text, categories

        # BLP is excluded by default. A deliberately-scoped topic filter may opt out via
        # guardrails.relax_blp_when_topic_filtered — but ONLY when an include filter is
        # actually active, so the flag can never silently disable BLP exclusion repo-wide.
        include_filter_active = bool(include)
        blp_relaxed = self.config.guardrails.relax_blp_when_topic_filtered and include_filter_active
        if self.config.article_selection.exclude_blp and not blp_relaxed and self.is_blp(page_text, categories):
            return False, "BLP article", page_text, categories

        # Require at least one inline {{Citation needed}} claim to source.
        if not self.extract_citation_needed_claims(page_text):
            return False, "no citation-needed tag", page_text, categories

        return True, "", page_text, categories

    def _build_candidate(self, page, page_text: str, categories: list[str]) -> CandidateArticle:
        """Build a CandidateArticle from an mwclient page already known to be a candidate.

        Takes the page text/categories already fetched by _evaluate_candidate
        instead of re-fetching them, so each pooled candidate costs one round
        of Wikipedia API calls instead of two.
        """
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

    def _build_scorer(self) -> CandidateScorer | None:
        """Build a CandidateScorer from learned outcome rates, or None to degrade
        to plain category order (no history, disabled, or a broken/missing DB)."""
        if self.seen_store is None or not self.config.feedback.enabled:
            return None

        try:
            rates = {
                "categories": self.seen_store.dimension_rates("categories"),
                "has_infobox": self.seen_store.dimension_rates("has_infobox"),
            }
        except sqlite3.Error:
            return None

        return CandidateScorer(rates, self.config.feedback.epsilon, self.config.feedback.min_samples)

    def fetch_candidates(
        self,
        limit: int = 100,
        include_categories: list[str] | None = None,
        exclude_categories: list[str] | None = None,
    ) -> Iterator[CandidateArticle]:
        """Fetch candidate articles from Wikipedia.

        Reads a look-ahead pool of candidates (size >= limit, cheap title/text
        checks only — no Claude calls), then ranks the pool by learned success
        rate (via CandidateScorer) before truncating to `limit`. Falls back to
        plain category order when there's no seen_store, feedback is disabled,
        or the outcomes DB is missing/broken.

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
            logger.warning("Error accessing category %r: %s", category, e)
            return

        start_prefix = self.config.article_selection.category_start_prefix
        if hasattr(cat_page, "args"):
            if start_prefix:
                cat_page.args["gcmstartsortkeyprefix"] = start_prefix
            # Piggyback each candidate's category membership onto the batch
            # generator=categorymembers query so the topic filter can run before any
            # per-page fetch (see issue #18). prop is a single pipe-delimited value, so
            # overwriting the default 'info|imageinfo' with the superset is correct and
            # preserves the info|imageinfo|protection data the rest of the flow relies on.
            cat_page.args["prop"] = "info|imageinfo|categories"
            cat_page.args["cllimit"] = "max"

        # Resolve and expand the include/exclude lists once per fetch (not once per page):
        # each configured name that has a static discovery file is widened to its
        # discovered subcategory set (no live Wikipedia subcategory call here — Phase 4).
        include = include_categories if include_categories is not None else self.config.article_selection.include_categories
        exclude = exclude_categories if exclude_categories is not None else self.config.article_selection.exclude_categories
        include = self._expand_categories(include)
        exclude = self._expand_categories(exclude)

        pool_size = max(self.config.article_selection.candidate_pool_size, limit)
        pool: list[CandidateArticle] = []
        # Sequential, one request in flight at a time — per mediawiki.org/wiki/API:Etiquette,
        # "making your requests in series rather than in parallel... should result in a
        # safe request rate."
        for page in cat_page:
            if len(pool) >= pool_size:
                break

            # Skip already-processed articles first — a cheap title lookup, no
            # page fetch — so fetching progresses instead of restarting at the top.
            if self.seen_store is not None and self.seen_store.is_seen(page.name):
                continue

            is_ok, _, page_text, categories = self._evaluate_candidate(page, include, exclude)
            if not is_ok:
                continue

            try:
                pool.append(self._build_candidate(page, page_text, categories))
            except Exception as e:
                logger.warning("Error processing page %r: %s", page.name, e)
                continue

        scorer = self._build_scorer()
        ranked = sorted(pool, key=scorer.score, reverse=True) if scorer else pool
        yield from ranked[:limit]
