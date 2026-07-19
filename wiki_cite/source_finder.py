"""
Source Finding service for discovering citations for existing claims.
"""

import logging
import re
import time
from collections.abc import Callable
from urllib.parse import urlparse

import mwclient
import mwparserfromhell
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from wiki_cite.article_picker import _build_session, fetch_backlink_pages
from wiki_cite.config import get_config
from wiki_cite.models import ReliabilityRating, Source, SourceType

logger = logging.getLogger(__name__)

# Identical (api, query, max_results) searches within this window are served from
# an in-memory cache instead of re-hitting the network — cuts both latency and
# quota pressure on rate-limited search APIs (e.g. Semantic Scholar).
_SEARCH_CACHE_TTL_SECONDS = 3600

# Wikipedia's Reliable Sources Perennial (simplified version)
# In production, this should be fetched from WP:RSP
RELIABLE_SOURCES = {
    # Generally reliable
    "nytimes.com": ReliabilityRating.GENERALLY_RELIABLE,
    "theguardian.com": ReliabilityRating.GENERALLY_RELIABLE,
    "bbc.com": ReliabilityRating.GENERALLY_RELIABLE,
    "bbc.co.uk": ReliabilityRating.GENERALLY_RELIABLE,
    "washingtonpost.com": ReliabilityRating.GENERALLY_RELIABLE,
    "reuters.com": ReliabilityRating.GENERALLY_RELIABLE,
    "apnews.com": ReliabilityRating.GENERALLY_RELIABLE,
    "nature.com": ReliabilityRating.GENERALLY_RELIABLE,
    "science.org": ReliabilityRating.GENERALLY_RELIABLE,
    "doi.org": ReliabilityRating.GENERALLY_RELIABLE,
    "gov": ReliabilityRating.GENERALLY_RELIABLE,  # Government domains
    "edu": ReliabilityRating.GENERALLY_RELIABLE,  # Academic domains
    # Potentially unreliable
    "dailymail.co.uk": ReliabilityRating.POTENTIALLY_UNRELIABLE,
    "forbes.com": ReliabilityRating.SITUATIONALLY_RELIABLE,
    "medium.com": ReliabilityRating.POTENTIALLY_UNRELIABLE,
    "wordpress.com": ReliabilityRating.POTENTIALLY_UNRELIABLE,
    "blogspot.com": ReliabilityRating.POTENTIALLY_UNRELIABLE,
}


def extract_citation_url(text: str) -> str | None:
    """Extract the source URL from a wikitext snippet containing a citation.

    Looks for a |url= parameter inside a {{cite ...}} template first, then
    falls back to the first bare URL found in the text.

    Args:
        text: Wikitext, typically a proposed edit's inserted <ref>/{{cite}} markup

    Returns:
        The extracted URL, or None if no URL was found
    """
    wikicode = mwparserfromhell.parse(text)
    for template in wikicode.filter_templates():
        if template.name.strip().lower().startswith("cite"):
            for param_name in ("url", "URL"):
                if template.has(param_name):
                    value = str(template.get(param_name).value).strip()
                    if value:
                        return value

    bare_url_match = re.search(r"https?://[^\s|}\]<>\"']+", text)
    if bare_url_match:
        return bare_url_match.group(0)

    return None


def extract_all_citation_urls(text: str) -> list[str]:
    """Extract every distinct external citation URL from a wikitext blob.

    Scans all {{cite ...}} templates' |url=/|URL= parameters first, then every bare
    https?:// URL in the text. Deduplicates while preserving first-seen order — the
    citation most relevant to a specific claim is not guaranteed to be the first one
    on the page, so all distinct URLs are surfaced (design decision: all-URLs, not
    first-only).

    Args:
        text: Wikitext (typically a whole backlinking article's source).

    Returns:
        A list of distinct URLs in first-seen order. Empty list if none are found
        (never raises for a citation-free page).
    """
    urls: list[str] = []
    seen: set[str] = set()

    wikicode = mwparserfromhell.parse(text)
    for template in wikicode.filter_templates():
        if template.name.strip().lower().startswith("cite"):
            for param_name in ("url", "URL"):
                if template.has(param_name):
                    value = str(template.get(param_name).value).strip()
                    if value:
                        if value not in seen:
                            seen.add(value)
                            urls.append(value)
                        break

    for bare_url_match in re.finditer(r"https?://[^\s|}\]<>\"']+", text):
        value = bare_url_match.group(0)
        if value not in seen:
            seen.add(value)
            urls.append(value)

    return urls


class SourceFinder:
    """Finds reliable sources for verifying existing claims."""

    def __init__(self):
        """Initialize the source finder."""
        self.config = get_config()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.config.wikipedia.user_agent})

        # Respect 429/5xx responses: back off and retry, honoring Retry-After
        # when the API sends one, instead of hammering an API that just rate-limited us.
        retry = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            respect_retry_after_header=True,
            allowed_methods=("GET", "HEAD"),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self._search_cache: dict[tuple, tuple[float, list[Source]]] = {}

    def _cached_search(self, cache_key: tuple, fetch: Callable[[], list[Source]]) -> list[Source]:
        """Serve a repeated identical search from the in-memory TTL cache, or run
        ``fetch`` and cache its result."""
        now = time.monotonic()
        cached = self._search_cache.get(cache_key)
        if cached is not None and now < cached[0]:
            return cached[1]

        sources = fetch()
        self._search_cache[cache_key] = (now + _SEARCH_CACHE_TTL_SECONDS, sources)
        return sources

    def check_reliability(self, url: str) -> ReliabilityRating:
        """Check the reliability of a source based on its domain.

        Args:
            url: The URL to check

        Returns:
            ReliabilityRating for the source
        """
        if not url:
            return ReliabilityRating.POTENTIALLY_UNRELIABLE

        try:
            domain = urlparse(url).netloc.lower()
            domain = domain.replace("www.", "")

            # Check exact domain matches
            if domain in RELIABLE_SOURCES:
                return RELIABLE_SOURCES[domain]

            # Check for government domains
            if domain.endswith(".gov"):
                return ReliabilityRating.GENERALLY_RELIABLE

            # Check for academic domains
            if domain.endswith(".edu"):
                return ReliabilityRating.GENERALLY_RELIABLE

            # Check parent domain
            parts = domain.split(".")
            if len(parts) >= 2:
                parent = ".".join(parts[-2:])
                if parent in RELIABLE_SOURCES:
                    return RELIABLE_SOURCES[parent]

            # Default to situationally reliable
            return ReliabilityRating.SITUATIONALLY_RELIABLE

        except Exception:
            return ReliabilityRating.POTENTIALLY_UNRELIABLE

    def find_backlink_sources(self, article_title: str, *, site=None) -> list[Source]:
        """Discover candidate citation URLs from articles that link to ``article_title``.

        Fetches up to ``config.agent.max_backlink_pages_to_check`` backlinking pages
        (fetch_backlink_pages), extracts ALL citation URLs from each
        (extract_all_citation_urls), deduplicates across every scanned page, and runs
        each distinct URL through the SAME check_reliability() the other search tools
        use — returning Source objects identical in shape to search_web results. A
        discovered URL is only ever a *candidate*: it is never accepted as a source
        here, and never exempted from reliability checking (including any
        wikipedia.org URL that slips through — WP:CIRCULAR is enforced by
        check_reliability parity + the system prompt, not by a carve-out).

        Args:
            article_title: the article currently being edited.
            site: injected mwclient Site (tests pass a mock); when None a real
                en.wikipedia.org Site is built lazily with the pooled retry session.

        Returns:
            Candidate Source objects, one per distinct discovered URL, in first-seen order.
        """
        if site is None:
            site = mwclient.Site("en.wikipedia.org", pool=_build_session(self.config.wikipedia.user_agent))

        max_pages = self.config.agent.max_backlink_pages_to_check
        pages = fetch_backlink_pages(site, article_title, max_pages)

        seen: set[str] = set()
        sources: list[Source] = []
        for _page_title, wikitext in pages:
            for url in extract_all_citation_urls(wikitext):
                if url in seen:
                    continue
                seen.add(url)
                sources.append(
                    Source(
                        title=urlparse(url).netloc or url,
                        url=url,
                        source_type=SourceType.WEB,
                        reliability=self.check_reliability(url),
                    )
                )

        return sources

    def verify_url_exists(self, url: str) -> bool:
        """Verify that a URL exists and is accessible.

        Args:
            url: The URL to verify

        Returns:
            True if URL is accessible
        """
        try:
            response = self.session.head(url, timeout=10, allow_redirects=True)
            return response.status_code == 200
        except Exception:
            # Try GET if HEAD fails
            try:
                response = self.session.get(url, timeout=10, allow_redirects=True)
                return response.status_code == 200
            except Exception:
                return False

    def search_google_scholar(self, query: str, max_results: int = 5) -> list[Source]:  # pylint: disable=unused-argument
        """Search Google Scholar for academic sources.

        Note: This is a simplified placeholder implementation.
        In production, use the scholarly library or an official API.

        Args:
            query: The search query (currently unused)
            max_results: Maximum number of results to return (currently unused)

        Returns:
            Empty list (placeholder implementation)
        """
        # This is a placeholder - actual implementation would use
        # the scholarly library or Semantic Scholar API
        return []

    def search_semantic_scholar(self, query: str, max_results: int = 5) -> list[Source]:
        """Search Semantic Scholar for academic sources.

        Args:
            query: The search query
            max_results: Maximum number of results to return

        Returns:
            List of Source objects
        """
        api_key = self.config.semantic_scholar_api_key
        if not api_key:
            return []

        def fetch() -> list[Source]:
            sources = []
            try:
                url = "https://api.semanticscholar.org/graph/v1/paper/search"
                params = {
                    "query": query,
                    "limit": max_results,
                    "fields": "title,authors,year,doi,url,venue",
                }
                headers = {"x-api-key": api_key} if api_key else {}

                response = self.session.get(url, params=params, headers=headers, timeout=10)
                if response.status_code != 200:
                    logger.warning(
                        "Semantic Scholar search failed: status=%s retry_after=%s query=%r",
                        response.status_code,
                        response.headers.get("Retry-After"),
                        query,
                    )
                    return sources

                data = response.json()
                for paper in data.get("data", []):
                    authors = [a.get("name", "") for a in paper.get("authors", [])]
                    source = Source(
                        title=paper.get("title", ""),
                        authors=authors,
                        publication_date=str(paper.get("year", "")),
                        doi=paper.get("doi"),
                        url=paper.get("url"),
                        publisher=paper.get("venue", ""),
                        source_type=SourceType.JOURNAL,
                        reliability=ReliabilityRating.GENERALLY_RELIABLE,
                    )
                    sources.append(source)

            except Exception as e:
                logger.warning("Error searching Semantic Scholar: %s", e)

            return sources

        return self._cached_search(("semantic_scholar", query, max_results), fetch)

    def search_crossref(self, query: str, max_results: int = 5) -> list[Source]:
        """Search CrossRef for published sources.

        Args:
            query: The search query
            max_results: Maximum number of results to return

        Returns:
            List of Source objects
        """
        email = self.config.crossref_email
        if not email:
            return []

        def fetch() -> list[Source]:
            sources = []
            try:
                url = "https://api.crossref.org/works"
                params = {
                    "query": query,
                    "rows": max_results,
                    "mailto": email,
                }

                response = self.session.get(url, params=params, timeout=10)
                if response.status_code != 200:
                    logger.warning(
                        "CrossRef search failed: status=%s retry_after=%s query=%r",
                        response.status_code,
                        response.headers.get("Retry-After"),
                        query,
                    )
                    return sources

                data = response.json()
                for item in data.get("message", {}).get("items", []):
                    # Extract authors
                    authors = []
                    for author in item.get("author", []):
                        given = author.get("given", "")
                        family = author.get("family", "")
                        if given and family:
                            authors.append(f"{given} {family}")
                        elif family:
                            authors.append(family)

                    # Determine source type
                    item_type = item.get("type", "").lower()
                    if "journal" in item_type:
                        source_type = SourceType.JOURNAL
                    elif "book" in item_type:
                        source_type = SourceType.BOOK
                    else:
                        source_type = SourceType.WEB

                    # Get publication date
                    pub_date = ""
                    if "published" in item:
                        date_parts = item["published"].get("date-parts", [[]])[0]
                        if date_parts:
                            pub_date = str(date_parts[0])  # Year

                    # Build URL from DOI
                    doi = item.get("DOI", "")
                    url = f"https://doi.org/{doi}" if doi else item.get("URL", "")

                    source = Source(
                        title=item.get("title", [""])[0] if item.get("title") else "",
                        authors=authors,
                        publication_date=pub_date,
                        doi=doi,
                        url=url,
                        publisher=item.get("publisher", ""),
                        source_type=source_type,
                        reliability=ReliabilityRating.GENERALLY_RELIABLE,
                    )
                    sources.append(source)

            except Exception as e:
                logger.warning("Error searching CrossRef: %s", e)

            return sources

        return self._cached_search(("crossref", query, max_results), fetch)

    def search_web(self, query: str, max_results: int = 5) -> list[Source]:
        """Search the general web (via Brave Search) for news/reference sources.

        Unlike the academic APIs, this can find sources for everyday claims
        (biographical facts, events, places) that never appear in scholarly databases.

        Args:
            query: The search query
            max_results: Maximum number of results to return

        Returns:
            List of Source objects
        """
        api_key = self.config.brave_api_key
        if not api_key:
            return []

        def fetch() -> list[Source]:
            sources = []
            try:
                url = "https://api.search.brave.com/res/v1/web/search"
                params = {"q": query, "count": max_results}
                headers = {"Accept": "application/json", "X-Subscription-Token": api_key}

                response = self.session.get(url, params=params, headers=headers, timeout=10)
                if response.status_code != 200:
                    logger.warning(
                        "Brave web search failed: status=%s retry_after=%s query=%r",
                        response.status_code,
                        response.headers.get("Retry-After"),
                        query,
                    )
                    return sources

                data = response.json()
                for result in data.get("web", {}).get("results", [])[:max_results]:
                    result_url = result.get("url", "")
                    source = Source(
                        title=result.get("title", ""),
                        url=result_url,
                        publication_date=result.get("page_age") or result.get("age"),
                        publisher=result.get("profile", {}).get("name") or urlparse(result_url).netloc,
                        source_type=SourceType.NEWS if "news" in query.lower() else SourceType.WEB,
                        reliability=self.check_reliability(result_url),
                    )
                    sources.append(source)

            except Exception as e:
                logger.warning("Error searching web: %s", e)

            return sources

        return self._cached_search(("web_search", query, max_results), fetch)

    def fetch_page_preview(self, url: str) -> dict:
        """Fetch a lightweight preview of a source page for reviewer verification.

        Args:
            url: The source URL to preview

        Returns:
            Dict with title, description, site_name, image, url, and ok status
        """
        preview = {
            "url": url,
            "ok": False,
            "title": None,
            "description": None,
            "site_name": urlparse(url).netloc.replace("www.", "") if url else None,
            "image": None,
            "error": None,
        }

        if not url:
            preview["error"] = "No URL provided"
            return preview

        try:
            response = self.session.get(url, timeout=10, allow_redirects=True, stream=True)
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                preview["error"] = f"Not an HTML page ({content_type or 'unknown type'})"
                return preview

            # Only read the first portion of the page; head metadata lives early in the document
            html = response.raw.read(200_000, decode_content=True)
            soup = BeautifulSoup(html, "html.parser")

            def meta(*names: str) -> str | None:
                for name in names:
                    tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
                    if tag and tag.get("content"):
                        return tag["content"].strip()
                return None

            title_tag = soup.find("title")
            preview["title"] = meta("og:title") or (title_tag.text.strip() if title_tag else None)
            preview["description"] = meta("og:description", "description")
            preview["site_name"] = meta("og:site_name") or preview["site_name"]
            preview["image"] = meta("og:image")
            preview["ok"] = bool(preview["title"] or preview["description"])
            if not preview["ok"]:
                preview["error"] = "Could not extract page metadata"

        except Exception as e:
            preview["error"] = str(e)

        return preview

    def find_sources_for_claim(self, claim: str, max_results: int = 5) -> list[Source]:
        """Find sources that might verify a claim.

        Args:
            claim: The claim to find sources for
            max_results: Maximum number of sources to return

        Returns:
            List of Source objects, sorted by reliability
        """
        all_sources = []

        # Search different APIs based on configuration
        if "semantic_scholar" in self.config.sources.search_apis:
            all_sources.extend(self.search_semantic_scholar(claim, max_results))

        if "crossref" in self.config.sources.search_apis:
            all_sources.extend(self.search_crossref(claim, max_results))

        if "web_search" in self.config.sources.search_apis:
            all_sources.extend(self.search_web(claim, max_results))

        if "google_scholar" in self.config.sources.search_apis:
            all_sources.extend(self.search_google_scholar(claim, max_results))

        # Filter by reliability if configured
        if self.config.sources.reliability_check:
            all_sources = [s for s in all_sources if s.reliability != ReliabilityRating.DEPRECATED]

        # Sort by reliability (generally reliable first)
        reliability_order = {
            ReliabilityRating.GENERALLY_RELIABLE: 0,
            ReliabilityRating.SITUATIONALLY_RELIABLE: 1,
            ReliabilityRating.POTENTIALLY_UNRELIABLE: 2,
        }
        all_sources.sort(key=lambda s: reliability_order.get(s.reliability, 3))

        return all_sources[:max_results]

    def extract_claims(self, wikitext: str) -> list[str]:
        """Extract factual claims from wikitext.

        Args:
            wikitext: Wikipedia article text

        Returns:
            List of claims (sentences)
        """
        # Remove templates, categories, references
        text = re.sub(r"\{\{[^}]+\}\}", "", wikitext)
        text = re.sub(r"\[\[Category:[^\]]+\]\]", "", text)
        text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
        text = re.sub(r"<ref[^>]*/>", "", text)

        # Remove wikilinks but keep the text
        text = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", text)

        # Remove section headers
        text = re.sub(r"==+[^=]+=+", "", text)

        # Split into sentences (simple version)
        sentences = re.split(r"[.!?]+", text)

        # Clean and filter
        claims = []
        for sentence in sentences:
            sentence = sentence.strip()
            # Skip very short sentences and those that look like headers
            if len(sentence) > 20 and not sentence.isupper():
                claims.append(sentence)

        return claims
