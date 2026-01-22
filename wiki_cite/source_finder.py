"""
Source Finding service for discovering citations for existing claims.
"""

import re
from urllib.parse import urlparse

import requests

from wiki_cite.config import get_config
from wiki_cite.models import ReliabilityRating, Source, SourceType


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


class SourceFinder:
    """Finds reliable sources for verifying existing claims."""

    def __init__(self):
        """Initialize the source finder."""
        self.config = get_config()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.config.wikipedia.user_agent})

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

    def search_google_scholar(
        self, query: str, max_results: int = 5
    ) -> list[Source]:  # pylint: disable=unused-argument
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
        sources = []

        api_key = self.config.semantic_scholar_api_key
        if not api_key:
            return sources

        try:
            url = "https://api.semanticscholar.org/graph/v1/paper/search"
            params = {
                "query": query,
                "limit": max_results,
                "fields": "title,authors,year,doi,url,venue",
            }
            headers = {"x-api-key": api_key} if api_key else {}

            response = self.session.get(url, params=params, headers=headers, timeout=10)
            if response.status_code == 200:
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
            print(f"Error searching Semantic Scholar: {e}")

        return sources

    def search_crossref(self, query: str, max_results: int = 5) -> list[Source]:
        """Search CrossRef for published sources.

        Args:
            query: The search query
            max_results: Maximum number of results to return

        Returns:
            List of Source objects
        """
        sources = []

        email = self.config.crossref_email
        if not email:
            return sources

        try:
            url = "https://api.crossref.org/works"
            params = {
                "query": query,
                "rows": max_results,
                "mailto": email,
            }

            response = self.session.get(url, params=params, timeout=10)
            if response.status_code == 200:
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
            print(f"Error searching CrossRef: {e}")

        return sources

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
