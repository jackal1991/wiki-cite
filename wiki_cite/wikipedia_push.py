"""
Wikipedia Push Service for submitting approved edits to Wikipedia.
"""

from datetime import datetime, timedelta

import mwclient

from wiki_cite.config import get_config
from wiki_cite.models import EditProposal


class RateLimiter:
    """Simple rate limiter for Wikipedia edits."""

    def __init__(self, max_edits_per_hour: int):
        """Initialize the rate limiter.

        Args:
            max_edits_per_hour: Maximum number of edits allowed per hour
        """
        self.max_edits_per_hour = max_edits_per_hour
        self.edit_times: list[datetime] = []

    def can_edit(self) -> bool:
        """Check if we can make another edit within rate limits.

        Returns:
            True if we can edit, False if rate limited
        """
        now = datetime.now()
        cutoff = now - timedelta(hours=1)

        # Remove old edits
        self.edit_times = [t for t in self.edit_times if t > cutoff]

        return len(self.edit_times) < self.max_edits_per_hour

    def record_edit(self) -> None:
        """Record that an edit was made."""
        self.edit_times.append(datetime.now())


class WikipediaPushService:
    """Service for pushing approved edits to Wikipedia."""

    def __init__(self, site: mwclient.Site | None = None):
        """Initialize the push service.

        Args:
            site: mwclient Site object. If None, creates new connection
        """
        self.config = get_config()
        self.site = site

        if self.site is None:
            self.site = mwclient.Site("en.wikipedia.org")

            # Login if credentials provided
            if self.config.wikipedia_username and self.config.wikipedia_password:
                try:
                    self.site.login(self.config.wikipedia_username, self.config.wikipedia_password)
                except Exception as e:
                    print(f"Failed to login to Wikipedia: {e}")

        self.rate_limiter = RateLimiter(self.config.wikipedia.rate_limit_edits_per_hour)

    def check_for_conflicts(self, article_title: str, base_revision: str) -> bool:
        """Check if article has been edited since we fetched it.

        Args:
            article_title: Title of the article
            base_revision: Revision ID we based our edits on

        Returns:
            True if there's a conflict (article was edited)
        """
        try:
            page = self.site.pages[article_title]
            current_revision = str(page.revision)
            return current_revision != base_revision
        except Exception as e:
            print(f"Error checking for conflicts: {e}")
            return True  # Assume conflict on error to be safe

    def push_edits(self, proposal: EditProposal, modified_text: str) -> tuple[bool, str]:
        """Push approved edits to Wikipedia.

        Args:
            proposal: The edit proposal with approved edits
            modified_text: The modified article text to push

        Returns:
            Tuple of (success, message)
        """
        # Check rate limiting
        if not self.rate_limiter.can_edit():
            return False, "Rate limit exceeded. Please wait before making more edits."

        # Check for edit conflicts
        if self.check_for_conflicts(proposal.article.title, proposal.article.revision_id):
            return (
                False,
                "Edit conflict: article has been modified since analysis. Please re-analyze.",
            )

        # Generate edit summary
        edit_summary = proposal.get_edit_summary()
        if not edit_summary:
            return False, "No approved edits to push"

        # Get the page
        try:
            page = self.site.pages[proposal.article.title]
        except Exception as e:
            return False, f"Failed to access page: {e}"

        # Push the edit
        try:
            page.save(
                modified_text,
                summary=edit_summary,
                minor=True,  # Mark as minor edit
                bot=True,  # Mark as bot edit if logged in as bot
            )

            # Record the edit for rate limiting
            self.rate_limiter.record_edit()

            return True, f"Successfully pushed edits. Edit summary: {edit_summary}"

        except Exception as e:
            return False, f"Failed to push edits: {e}"

    def preview_diff(self, proposal: EditProposal, modified_text: str) -> str:
        """Generate a preview diff of changes.

        Args:
            proposal: The edit proposal
            modified_text: The modified article text

        Returns:
            Diff string showing changes
        """
        import difflib

        original_lines = proposal.article.wikitext.splitlines()
        modified_lines = modified_text.splitlines()

        diff = difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=f"{proposal.article.title} (original)",
            tofile=f"{proposal.article.title} (modified)",
            lineterm="",
        )

        return "\n".join(diff)
