"""
Core data models for the Wikipedia Citation & Cleanup Tool.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal


class EditType(Enum):
    """Types of edits that can be made to an article."""

    CITATION_ADDED = "citation"
    GRAMMAR_FIX = "grammar"
    STYLE_FIX = "style"
    WIKILINK_ADDED = "wikilink"
    POLICY_FIX = "policy"
    FORMAT_FIX = "formatting"


class SourceType(Enum):
    """Types of sources that can be cited."""

    JOURNAL = "journal"
    NEWS = "news"
    BOOK = "book"
    WEB = "web"
    GOVERNMENT = "government"


class ReliabilityRating(Enum):
    """Source reliability ratings per WP:RS."""

    GENERALLY_RELIABLE = "generally_reliable"
    SITUATIONALLY_RELIABLE = "situationally_reliable"
    POTENTIALLY_UNRELIABLE = "potentially_unreliable"
    DEPRECATED = "deprecated"


@dataclass
class Source:
    """A source that can be cited in Wikipedia."""

    title: str
    url: str | None = None
    authors: list[str] = field(default_factory=list)
    publication_date: str | None = None
    doi: str | None = None
    isbn: str | None = None
    publisher: str | None = None
    source_type: SourceType = SourceType.WEB
    citation_template: str = ""
    reliability: ReliabilityRating | None = None

    def to_citation_template(self) -> str:
        """Generate a Wikipedia citation template for this source."""
        if self.citation_template:
            return self.citation_template

        # Generate based on source type
        if self.source_type == SourceType.BOOK:
            parts = ["{{cite book"]
            if self.authors:
                if len(self.authors) == 1:
                    parts.append(f"|last={self.authors[0].split()[-1]}")
                    if len(self.authors[0].split()) > 1:
                        parts.append(f"|first={' '.join(self.authors[0].split()[:-1])}")
                else:
                    for i, author in enumerate(self.authors, 1):
                        parts.append(f"|author{i}={author}")
            parts.append(f"|title={self.title}")
            if self.publication_date:
                parts.append(f"|year={self.publication_date}")
            if self.publisher:
                parts.append(f"|publisher={self.publisher}")
            if self.isbn:
                parts.append(f"|isbn={self.isbn}")
            parts.append("}}")
            return " ".join(parts)

        elif self.source_type == SourceType.NEWS:
            parts = ["{{cite news"]
            if self.authors and len(self.authors) == 1:
                parts.append(f"|last={self.authors[0].split()[-1]}")
                if len(self.authors[0].split()) > 1:
                    parts.append(f"|first={' '.join(self.authors[0].split()[:-1])}")
            parts.append(f"|title={self.title}")
            if self.publisher:
                parts.append(f"|work={self.publisher}")
            if self.publication_date:
                parts.append(f"|date={self.publication_date}")
            if self.url:
                parts.append(f"|url={self.url}")
            parts.append("}}")
            return " ".join(parts)

        elif self.source_type == SourceType.JOURNAL:
            parts = ["{{cite journal"]
            if self.authors and len(self.authors) == 1:
                parts.append(f"|last={self.authors[0].split()[-1]}")
                if len(self.authors[0].split()) > 1:
                    parts.append(f"|first={' '.join(self.authors[0].split()[:-1])}")
            parts.append(f"|title={self.title}")
            if self.publisher:
                parts.append(f"|journal={self.publisher}")
            if self.publication_date:
                parts.append(f"|date={self.publication_date}")
            if self.doi:
                parts.append(f"|doi={self.doi}")
            if self.url:
                parts.append(f"|url={self.url}")
            parts.append("}}")
            return " ".join(parts)

        else:  # WEB
            parts = ["{{cite web"]
            if self.authors and len(self.authors) == 1:
                parts.append(f"|last={self.authors[0].split()[-1]}")
                if len(self.authors[0].split()) > 1:
                    parts.append(f"|first={' '.join(self.authors[0].split()[:-1])}")
            parts.append(f"|title={self.title}")
            if self.url:
                parts.append(f"|url={self.url}")
            if self.publisher:
                parts.append(f"|website={self.publisher}")
            if self.publication_date:
                parts.append(f"|date={self.publication_date}")
            parts.append("}}")
            return " ".join(parts)


@dataclass
class ProposedEdit:
    """A single proposed edit to an article."""

    edit_type: EditType
    original_text: str
    proposed_text: str
    rationale: str
    policy_reference: str | None = None
    confidence: Literal["high", "medium", "low"] = "medium"
    source: Source | None = None
    approved: bool | None = None  # None = pending, True = approved, False = rejected
    reviewer_notes: str | None = None


@dataclass
class CandidateArticle:
    """A Wikipedia article that is a candidate for cleanup."""

    title: str
    url: str
    wikitext: str
    body_line_count: int
    revision_id: str
    is_blp: bool = False
    categories: list[str] = field(default_factory=list)
    has_infobox: bool = False
    fetched_at: datetime = field(default_factory=datetime.now)


@dataclass
class Article:
    """Full representation of a Wikipedia article."""

    title: str
    url: str
    wikitext: str
    revision_id: str
    fetched_at: datetime = field(default_factory=datetime.now)


@dataclass
class EditProposal:
    """A complete proposal for editing an article."""

    id: str
    article: Article
    edits: list[ProposedEdit]
    status: Literal["pending", "approved", "rejected", "pushed"] = "pending"
    created_at: datetime = field(default_factory=datetime.now)
    reviewed_at: datetime | None = None
    reviewer_notes: str | None = None

    def get_approved_edits(self) -> list[ProposedEdit]:
        """Get only the edits that have been approved."""
        return [edit for edit in self.edits if edit.approved is True]

    def get_edit_summary(self) -> str:
        """Generate a summary of approved edits for Wikipedia."""
        approved = self.get_approved_edits()
        if not approved:
            return ""

        counts = {}
        for edit in approved:
            edit_type = edit.edit_type.value
            counts[edit_type] = counts.get(edit_type, 0) + 1

        parts = []
        if counts.get("citation"):
            parts.append(
                f"added {counts['citation']} citation{'s' if counts['citation'] > 1 else ''}"
            )
        if counts.get("wikilink"):
            parts.append(f"{counts['wikilink']} wikilink{'s' if counts['wikilink'] > 1 else ''}")
        if counts.get("grammar"):
            parts.append("fixed grammar")
        if counts.get("style"):
            parts.append("style fixes")
        if counts.get("policy"):
            parts.append("policy compliance")
        if counts.get("formatting"):
            parts.append("formatting")

        summary = "Copyedit: " + ", ".join(parts)
        return summary + " (AI-assisted citation/cleanup, human-reviewed)"
